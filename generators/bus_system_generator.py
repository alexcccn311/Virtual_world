"""Deterministic route-free bus-stop placement over the generated road graph."""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .. import config
from ..models.entities import Street
from .urban_layout_generator import HEX_SIDE_LENGTH_M


Point = tuple[float, float]
STOP_LANDMARK_RADIUS_M = 400.0
STOP_LANDMARK_TEMPLATES = frozenset({"large_general_hospital", "private_hospital", "casino_1"})


def _name_stops(stops, roads, streets, organizations):
    """Assign stable names before persistence, never during map loading."""
    # Street centroids are in kilometres; roads use hex-side-length map units.
    centers = {(street.district, street.name): tuple(value * 1000 / HEX_SIDE_LENGTH_M for value in street.centroid) for street in streets}
    road_points = defaultdict(set)
    for road in roads:
        key = (str(road.get("district", "")), str(road.get("street", "未命名街道")))
        if key in centers:
            continue
        road_points[key].update(_point(p, label="道路坐标") for p in road["centerline"])
    for key, points in road_points.items():
        ordered = sorted(points)
        centers.setdefault(key, (sum(p[0] for p in ordered) / len(ordered), sum(p[1] for p in ordered) / len(ordered)))
    landmarks = defaultdict(list)
    for organization in organizations:
        if organization.get("template_name") not in STOP_LANDMARK_TEMPLATES:
            continue
        name = organization.get("name")
        if not isinstance(name, str) or not name.strip() or organization.get("position") is None:
            continue
        position = _point(organization["position"], label="公交地标位置")
        key = (str(organization.get("district", "")), str(organization.get("street", "")))
        landmarks[key].append((position, name.strip(), str(organization.get("organization_id", ""))))
    bases = []
    for stop in stops:
        key = (stop["district"], stop["street"])
        x, y = stop["position"]
        cx, cy = centers[key]
        dx, dy = x - cx, y - cy
        # SVG/map y increases southward.
        direction = ("中心" if math.hypot(dx, dy) * HEX_SIDE_LENGTH_M <= 100
                     else ("东" if dx > 0 else "西") if abs(dx) >= abs(dy)
                     else ("南" if dy > 0 else "北"))
        nearby = min(((math.dist(stop["position"], p) * HEX_SIDE_LENGTH_M, name, oid)
                      for p, name, oid in landmarks.get(key, ())), default=None)
        base = (nearby[1].removesuffix("站") if nearby and nearby[0] <= STOP_LANDMARK_RADIUS_M
                else str(stop["street"]) + direction)
        bases.append((key, base, direction))
    counts = Counter((key, base) for key, base, _ in bases)
    used = set()
    for stop, (key, base, direction) in zip(stops, bases):
        # Duplicate landmark names get a direction before falling back to numbers.
        if counts[key, base] > 1 and not base.endswith(("东", "西", "南", "北", "中心")):
            base += direction
        name, serial = base + "站", 2
        while (key, name) in used:
            name = f"{base}{serial}站"
            serial += 1
        used.add((key, name))
        stop["name"] = name


@dataclass(slots=True)
class BusSystem:
    stops: list[dict[str, object]]
    service_start_minute: int
    service_end_minute: int
    frequency_minutes: int
    speed_kmh: float
    stop_min_spacing_m: float
    report: dict[str, object]


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


def _stable_rank(seed: int, point: Point) -> int:
    digest = hashlib.sha256(
        f"{seed}:bus-stop:{point[0]:.12f}:{point[1]:.12f}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _validate_operating_parameters(
    start_minute: int,
    end_minute: int,
    frequency_minutes: int,
    speed_kmh: float,
    spacing_m: float,
) -> None:
    if not 0 <= start_minute < end_minute < 24 * 60:
        raise ValueError("公交运营时间必须位于同一天内，且开始时间早于结束时间")
    if frequency_minutes <= 0:
        raise ValueError("公交发车频率必须为正数")
    if speed_kmh <= 0:
        raise ValueError("公交速度必须为正数")
    if spacing_m <= 0:
        raise ValueError("公交站最小间距必须为正数")


def _candidate_nodes(
    roads: Sequence[Mapping[str, object]],
) -> tuple[
    list[Point],
    dict[Point, list[Mapping[str, object]]],
    dict[Point, int],
]:
    incident_roads: dict[Point, list[Mapping[str, object]]] = defaultdict(list)
    neighbors: dict[Point, set[Point]] = defaultdict(set)
    for road in roads:
        centerline = road.get("centerline")
        if not isinstance(centerline, (list, tuple)) or len(centerline) != 2:
            raise ValueError(f"道路 {road.get('road_id')} 缺少二点中心线")
        start = _point(centerline[0], label=f"道路 {road.get('road_id')}.centerline")
        end = _point(centerline[1], label=f"道路 {road.get('road_id')}.centerline")
        if start == end:
            raise ValueError(f"道路 {road.get('road_id')} 长度为零")
        incident_roads[start].append(road)
        incident_roads[end].append(road)
        neighbors[start].add(end)
        neighbors[end].add(start)

    degrees = {node: len(values) for node, values in neighbors.items()}
    candidates = [node for node, degree in degrees.items() if degree >= 2]
    if not candidates:
        candidates = list(degrees)
    return candidates, incident_roads, degrees


def _select_spaced_nodes(
    candidates: Sequence[Point],
    degrees: Mapping[Point, int],
    *,
    seed: int,
    spacing_m: float,
) -> list[Point]:
    ordered = sorted(
        candidates,
        key=lambda point: (-degrees[point], _stable_rank(seed, point), point),
    )
    buckets: dict[tuple[int, int], list[Point]] = defaultdict(list)
    selected: list[Point] = []
    for point in ordered:
        x_m = point[0] * HEX_SIDE_LENGTH_M
        y_m = point[1] * HEX_SIDE_LENGTH_M
        bucket = math.floor(x_m / spacing_m), math.floor(y_m / spacing_m)
        too_close = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for existing in buckets.get((bucket[0] + dx, bucket[1] + dy), ()):
                    if math.dist(point, existing) * HEX_SIDE_LENGTH_M < spacing_m:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if not too_close:
            selected.append(point)
            buckets[bucket].append(point)
    return sorted(selected)


def _preferred_road(
    roads: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    level_priority = {"local": 0, "street_boundary": 1, "district_boundary": 2}
    return min(
        roads,
        key=lambda road: (
            level_priority.get(str(road.get("level")), 9),
            str(road.get("road_id", "")),
        ),
    )


def generate_bus_system(
    roads: Sequence[Mapping[str, object]],
    *,
    seed: int,
    streets: Sequence[Street] = (),
    organizations: Sequence[Mapping[str, object]] = (),
    stop_min_spacing_m: float = config.BUS_STOP_MIN_SPACING_M,
    service_start_minute: int = config.BUS_OPERATION_START_MINUTE,
    service_end_minute: int = config.BUS_OPERATION_END_MINUTE,
    frequency_minutes: int = config.BUS_FREQUENCY_MINUTES,
    speed_kmh: float = config.BUS_SPEED_KMH,
) -> BusSystem:
    """Place a deterministic maximal set of stops on connected road nodes."""

    _validate_operating_parameters(
        service_start_minute,
        service_end_minute,
        frequency_minutes,
        speed_kmh,
        stop_min_spacing_m,
    )
    candidates, incident_roads, degrees = _candidate_nodes(roads)
    if not candidates:
        raise ValueError("没有可用于放置公交站的道路节点")
    selected = _select_spaced_nodes(
        candidates,
        degrees,
        seed=seed,
        spacing_m=stop_min_spacing_m,
    )
    stops: list[dict[str, object]] = []
    for index, position in enumerate(selected, 1):
        road = _preferred_road(incident_roads[position])
        street = str(road.get("street", "未命名街道"))
        stops.append({
            "stop_id": f"BUS-STOP-{index:06d}",
            "district": str(road.get("district", "")),
            "street": street,
            "road_id": str(road.get("road_id", "")),
            "position": position,
        })
    _name_stops(stops, roads, streets, organizations)

    departure_count = (
        (service_end_minute - service_start_minute) // frequency_minutes + 1
    )
    last_departure_minute = (
        service_start_minute + (departure_count - 1) * frequency_minutes
    )
    report: dict[str, object] = {
        "stop_count": len(stops),
        "candidate_node_count": len(candidates),
        "stop_min_spacing_m": stop_min_spacing_m,
        "service_start_minute": service_start_minute,
        "service_end_minute": service_end_minute,
        "frequency_minutes": frequency_minutes,
        "speed_kmh": speed_kmh,
        "departure_count_per_day": departure_count,
        "last_departure_minute": last_departure_minute,
    }
    return BusSystem(
        stops=stops,
        service_start_minute=service_start_minute,
        service_end_minute=service_end_minute,
        frequency_minutes=frequency_minutes,
        speed_kmh=speed_kmh,
        stop_min_spacing_m=stop_min_spacing_m,
        report=report,
    )


__all__ = ("BusSystem", "generate_bus_system")
