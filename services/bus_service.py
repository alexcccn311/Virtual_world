"""Road-graph routing and route-free bus travel-time calculations."""
from __future__ import annotations

import heapq
import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..generators.urban_layout_generator import HEX_SIDE_LENGTH_M


Point = tuple[float, float]


def _point(value: object, *, label: str) -> Point:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        )
    ):
        raise ValueError(f"{label} 必须是二元数值坐标")
    return round(float(value[0]), 12), round(float(value[1]), 12)


@dataclass(slots=True, frozen=True)
class BusOperatingSchedule:
    service_start_minute: int
    service_end_minute: int
    frequency_minutes: int
    speed_kmh: float

    def __post_init__(self) -> None:
        if not 0 <= self.service_start_minute < self.service_end_minute < 24 * 60:
            raise ValueError("公交运营时间必须位于同一天内")
        if self.frequency_minutes <= 0:
            raise ValueError("公交发车频率必须为正数")
        if self.speed_kmh <= 0:
            raise ValueError("公交速度必须为正数")

    def departure_minutes(self) -> tuple[int, ...]:
        """Return every daily boarding time as start + n * frequency."""

        return tuple(
            range(
                self.service_start_minute,
                self.service_end_minute + 1,
                self.frequency_minutes,
            )
        )

    def next_departure(self, ready_at: datetime) -> datetime:
        """Return the first system-wide departure not earlier than ready_at."""

        midnight = ready_at.replace(hour=0, minute=0, second=0, microsecond=0)
        first = midnight + timedelta(minutes=self.service_start_minute)
        last = midnight + timedelta(minutes=self.service_end_minute)
        if ready_at <= first:
            return first
        if ready_at > last:
            return first + timedelta(days=1)
        elapsed_seconds = (ready_at - first).total_seconds()
        frequency_seconds = self.frequency_minutes * 60
        step = math.ceil(elapsed_seconds / frequency_seconds)
        departure = first + timedelta(seconds=step * frequency_seconds)
        if departure <= last:
            return departure
        return first + timedelta(days=1)


@dataclass(slots=True, frozen=True)
class BusTripEstimate:
    origin: Point
    destination: Point
    ready_at: datetime
    departure_at: datetime
    arrival_at: datetime
    distance_m: float
    waiting_minutes: float
    riding_minutes: float
    total_minutes: float


@dataclass(slots=True, frozen=True)
class BusStop:
    stop_id: str
    name: str
    district: str
    street: str
    road_id: str
    position: Point


@dataclass(slots=True, frozen=True)
class LoadedBusSystem:
    city_id: str
    schedule: BusOperatingSchedule
    stops: tuple[BusStop, ...]
    road_network: "RoadNetwork"


@dataclass(slots=True, frozen=True)
class _RoadEdge:
    start: Point
    end: Point
    length_m: float


@dataclass(slots=True, frozen=True)
class _Projection:
    edge_index: int
    point: Point
    fraction: float
    perpendicular_map_distance: float


class RoadNetwork:
    """Weighted undirected graph built from persisted road centerlines."""

    def __init__(self, roads: Sequence[Mapping[str, object]]) -> None:
        self.edges: list[_RoadEdge] = []
        self.adjacency: dict[Point, list[tuple[Point, float]]] = defaultdict(list)
        for road in roads:
            centerline = road.get("centerline")
            if not isinstance(centerline, (list, tuple)) or len(centerline) != 2:
                raise ValueError(f"道路 {road.get('road_id')} 缺少二点中心线")
            start = _point(
                centerline[0],
                label=f"道路 {road.get('road_id')}.centerline",
            )
            end = _point(
                centerline[1],
                label=f"道路 {road.get('road_id')}.centerline",
            )
            length_m = math.dist(start, end) * HEX_SIDE_LENGTH_M
            if length_m <= 0:
                raise ValueError(f"道路 {road.get('road_id')} 长度为零")
            self.edges.append(_RoadEdge(start, end, length_m))
            self.adjacency[start].append((end, length_m))
            self.adjacency[end].append((start, length_m))
        if not self.edges:
            raise ValueError("道路网络不能为空")

    def _nearest_projection(self, value: object, *, label: str) -> _Projection:
        point = _point(value, label=label)
        best: _Projection | None = None
        for index, edge in enumerate(self.edges):
            dx = edge.end[0] - edge.start[0]
            dy = edge.end[1] - edge.start[1]
            length_squared = dx * dx + dy * dy
            fraction = (
                (point[0] - edge.start[0]) * dx
                + (point[1] - edge.start[1]) * dy
            ) / length_squared
            fraction = max(0.0, min(1.0, fraction))
            projected = (
                edge.start[0] + fraction * dx,
                edge.start[1] + fraction * dy,
            )
            distance = math.dist(point, projected)
            candidate = _Projection(
                edge_index=index,
                point=(round(projected[0], 12), round(projected[1], 12)),
                fraction=fraction,
                perpendicular_map_distance=distance,
            )
            if best is None or (
                candidate.perpendicular_map_distance,
                candidate.edge_index,
            ) < (
                best.perpendicular_map_distance,
                best.edge_index,
            ):
                best = candidate
        if best is None:
            raise RuntimeError("道路网络缺少可投影路段")
        return best

    def shortest_distance_m(self, origin: object, destination: object, *, include_access: bool = False) -> float:
        return self.shortest_route(origin, destination, include_access=include_access)[0]

    def shortest_route(self, origin, destination, *, include_access=False):
        """Return (distance in meters, ordered polyline), optionally including access legs."""

        source = self._nearest_projection(origin, label="origin")
        target = self._nearest_projection(destination, label="destination")
        source_edge = self.edges[source.edge_index]
        target_edge = self.edges[target.edge_index]

        parents = {}
        best_node = None
        distances: dict[Point, float] = {}
        pending: list[tuple[float, Point]] = []
        for node, distance in (
            (source_edge.start, source.fraction * source_edge.length_m),
            (source_edge.end, (1.0 - source.fraction) * source_edge.length_m),
        ):
            if distance < distances.get(node, math.inf):
                distances[node] = distance
                parents[node] = None
                heapq.heappush(pending, (distance, node))

        best = math.inf
        if source.edge_index == target.edge_index:
            best = (
                abs(source.fraction - target.fraction) * source_edge.length_m
            )
        target_costs = {
            target_edge.start: target.fraction * target_edge.length_m,
            target_edge.end: (1.0 - target.fraction) * target_edge.length_m,
        }
        while pending:
            distance, node = heapq.heappop(pending)
            if distance != distances.get(node) or distance >= best:
                continue
            if node in target_costs:
                if distance + target_costs[node] < best:
                    best = distance + target_costs[node]
                    best_node = node
            for neighbor, edge_length in self.adjacency[node]:
                candidate = distance + edge_length
                if candidate < distances.get(neighbor, math.inf) and candidate < best:
                    distances[neighbor] = candidate
                    parents[neighbor] = node
                    heapq.heappush(pending, (candidate, neighbor))
        if not math.isfinite(best):
            raise ValueError("起点与终点之间不存在连通道路")
        if include_access and _point(origin, label="origin") != _point(destination, label="destination"):
            best += (source.perpendicular_map_distance + target.perpendicular_map_distance) * HEX_SIDE_LENGTH_M
        middle = []
        while best_node is not None:
            middle.append(best_node)
            best_node = parents[best_node]
        route = [source.point, *reversed(middle), target.point]
        if include_access:
            route = [_point(origin, label="origin"), *route, _point(destination, label="destination")]
        route = [point for index, point in enumerate(route) if index == 0 or point != route[index-1]]
        if _point(origin, label="origin") == _point(destination, label="destination"):
            return 0.0, [_point(origin, label="origin")]
        return best, route


def shortest_road_distance_m(
    roads: Sequence[Mapping[str, object]],
    origin: object,
    destination: object,
) -> float:
    """Convenience wrapper for one-off shortest-road-distance queries."""

    return RoadNetwork(roads).shortest_distance_m(origin, destination)


def estimate_bus_trip(
    road_network: RoadNetwork,
    origin: object,
    destination: object,
    *,
    ready_at: datetime,
    schedule: BusOperatingSchedule,
) -> BusTripEstimate:
    """Estimate one direct route-free bus trip, including system-wide waiting."""

    origin_point = _point(origin, label="origin")
    destination_point = _point(destination, label="destination")
    distance_m = road_network.shortest_distance_m(origin_point, destination_point)
    departure_at = schedule.next_departure(ready_at)
    waiting_minutes = (departure_at - ready_at).total_seconds() / 60
    riding_minutes = distance_m / 1000 / schedule.speed_kmh * 60
    arrival_at = departure_at + timedelta(minutes=riding_minutes)
    return BusTripEstimate(
        origin=origin_point,
        destination=destination_point,
        ready_at=ready_at,
        departure_at=departure_at,
        arrival_at=arrival_at,
        distance_m=distance_m,
        waiting_minutes=waiting_minutes,
        riding_minutes=riding_minutes,
        total_minutes=waiting_minutes + riding_minutes,
    )


def load_bus_system(
    database_path: str | Path,
    city_id: str,
) -> LoadedBusSystem:
    """Load one generated city's stops, operating parameters, and road graph."""

    database = Path(database_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"世界数据库不存在：{database}")
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        system_row = connection.execute(
            """
            SELECT service_start_minute, service_end_minute,
                   frequency_minutes, speed_kmh
            FROM bus_systems WHERE city_id = ?
            """,
            (city_id,),
        ).fetchone()
        if system_row is None:
            raise LookupError(f"城市 {city_id} 不存在公交系统")
        schedule = BusOperatingSchedule(
            service_start_minute=int(system_row[0]),
            service_end_minute=int(system_row[1]),
            frequency_minutes=int(system_row[2]),
            speed_kmh=float(system_row[3]),
        )
        stops = tuple(
            BusStop(
                stop_id=str(row[0]),
                name=str(row[1]),
                district=str(row[2]),
                street=str(row[3]),
                road_id=str(row[4]),
                position=(float(row[5]), float(row[6])),
            )
            for row in connection.execute(
                """
                SELECT stop_id, name, district_name, street_name, road_id,
                       position_x, position_y
                FROM bus_stops WHERE city_id = ? ORDER BY stop_id
                """,
                (city_id,),
            )
        )
        roads = []
        for road_id, raw_payload in connection.execute(
            "SELECT road_id, data_json FROM roads WHERE city_id = ? ORDER BY road_id",
            (city_id,),
        ):
            try:
                payload = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError(f"道路 {road_id} 的 data_json 无效") from error
            if not isinstance(payload, dict):
                raise ValueError(f"道路 {road_id} 的 data_json 必须是对象")
            roads.append(payload)
        return LoadedBusSystem(
            city_id=city_id,
            schedule=schedule,
            stops=stops,
            road_network=RoadNetwork(roads),
        )
    finally:
        connection.close()


__all__ = (
    "BusOperatingSchedule",
    "BusStop",
    "BusTripEstimate",
    "LoadedBusSystem",
    "RoadNetwork",
    "estimate_bus_trip",
    "load_bus_system",
    "shortest_road_distance_m",
)
