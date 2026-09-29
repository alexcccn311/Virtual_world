"""Deterministic connected roads, parcels, buildings, and Organization placement."""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..models.entities import Street


HEX_CELL_AREA_KM2 = 0.05
HEX_SIDE_LENGTH_KM = math.sqrt(2 * HEX_CELL_AREA_KM2 / (3 * math.sqrt(3)))
HEX_SIDE_LENGTH_M = HEX_SIDE_LENGTH_KM * 1000
MIN_PARCELS_PER_CELL = 31
LOCAL_ROAD_WIDTH_M = 7.0
STREET_BOUNDARY_ROAD_WIDTH_M = 16.0
DISTRICT_BOUNDARY_ROAD_WIDTH_M = 24.0
MIN_PARCEL_AREA_M2 = 220.0
BUILDING_INSET_RATIO = 0.76
ENDPOINT_SNAP_DISTANCE_M = 4.0
GEOMETRY_EPSILON = 1e-9
HEX_DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))
_DIRECTION_EDGES = ((0, 1), (5, 0), (4, 5), (3, 4), (2, 3), (1, 2))
_SPLIT_NORMAL_ANGLES = (0.0, math.pi / 3, 2 * math.pi / 3)

Point = tuple[float, float]
Polygon = tuple[Point, ...]


@dataclass(slots=True)
class UrbanLayout:
    roads: list[dict[str, object]]
    parcels: list[dict[str, object]]
    buildings: list[dict[str, object]]
    report: dict[str, object]


@dataclass(slots=True)
class _RoadSegment:
    start: Point
    end: Point
    kind: str


def _stable_seed(seed: int, *parts: object) -> int:
    digest = hashlib.sha256(
        ":".join((str(seed), *(str(part) for part in parts))).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _cell_center(cell: tuple[int, int]) -> Point:
    q, r = cell
    return math.sqrt(3) * (q + r / 2), 1.5 * r


def _hexagon(cell: tuple[int, int]) -> Polygon:
    center_x, center_y = _cell_center(cell)
    return tuple(
        (
            center_x + math.cos(math.radians(60 * index - 30)),
            center_y + math.sin(math.radians(60 * index - 30)),
        )
        for index in range(6)
    )


def _signed_area(polygon: Sequence[Point]) -> float:
    return sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(polygon, (*polygon[1:], polygon[0]))
    ) / 2


def _area_map_units(polygon: Sequence[Point]) -> float:
    return abs(_signed_area(polygon))


def _area_m2(polygon: Sequence[Point]) -> float:
    return _area_map_units(polygon) * HEX_SIDE_LENGTH_M**2


def _centroid(polygon: Sequence[Point]) -> Point:
    signed_area = _signed_area(polygon)
    if abs(signed_area) < 1e-12:
        return (
            sum(point[0] for point in polygon) / len(polygon),
            sum(point[1] for point in polygon) / len(polygon),
        )
    factor = 1 / (6 * signed_area)
    return (
        factor * sum(
            (left[0] + right[0])
            * (left[0] * right[1] - right[0] * left[1])
            for left, right in zip(polygon, (*polygon[1:], polygon[0]))
        ),
        factor * sum(
            (left[1] + right[1])
            * (left[0] * right[1] - right[0] * left[1])
            for left, right in zip(polygon, (*polygon[1:], polygon[0]))
        ),
    )


def _perimeter(polygon: Sequence[Point]) -> float:
    return sum(
        math.dist(left, right)
        for left, right in zip(polygon, (*polygon[1:], polygon[0]))
    )


def _compactness(polygon: Sequence[Point]) -> float:
    perimeter = _perimeter(polygon)
    return 0.0 if perimeter <= 0 else 4 * math.pi * _area_map_units(polygon) / perimeter**2


def _clip_half_plane(
    polygon: Sequence[Point],
    normal: Point,
    limit: float,
    *,
    keep_lower: bool,
) -> Polygon:
    def projection(point: Point) -> float:
        return point[0] * normal[0] + point[1] * normal[1]

    def inside(point: Point) -> bool:
        value = projection(point)
        return value <= limit + 1e-10 if keep_lower else value >= limit - 1e-10

    result: list[Point] = []
    for start, end in zip(polygon, (*polygon[1:], polygon[0])):
        start_inside, end_inside = inside(start), inside(end)
        if start_inside:
            result.append(start)
        if start_inside == end_inside:
            continue
        start_value, end_value = projection(start), projection(end)
        fraction = (limit - start_value) / (end_value - start_value)
        result.append((
            start[0] + fraction * (end[0] - start[0]),
            start[1] + fraction * (end[1] - start[1]),
        ))
    return tuple(result)


def _line_segment_in_polygon(
    polygon: Sequence[Point], normal: Point, offset: float
) -> tuple[Point, Point] | None:
    points: list[Point] = []
    for start, end in zip(polygon, (*polygon[1:], polygon[0])):
        start_value = start[0] * normal[0] + start[1] * normal[1] - offset
        end_value = end[0] * normal[0] + end[1] * normal[1] - offset
        if abs(start_value) <= 1e-9:
            points.append(start)
        if start_value * end_value < -1e-12:
            fraction = start_value / (start_value - end_value)
            points.append((
                start[0] + fraction * (end[0] - start[0]),
                start[1] + fraction * (end[1] - start[1]),
            ))
    unique: list[Point] = []
    for point in points:
        if not any(math.dist(point, existing) < 1e-8 for existing in unique):
            unique.append(point)
    if len(unique) < 2:
        return None
    return max(
        ((left, right) for index, left in enumerate(unique) for right in unique[index + 1:]),
        key=lambda pair: math.dist(*pair),
    )


def _try_split(
    polygon: Polygon,
    rng: random.Random,
) -> tuple[Polygon, Polygon, tuple[Point, Point]] | None:
    road_half_width = LOCAL_ROAD_WIDTH_M / HEX_SIDE_LENGTH_M / 2
    best: tuple[float, Polygon, Polygon, tuple[Point, Point]] | None = None
    angles = list(_SPLIT_NORMAL_ANGLES)
    rng.shuffle(angles)
    for angle in angles:
        normal = math.cos(angle), math.sin(angle)
        projections = [point[0] * normal[0] + point[1] * normal[1] for point in polygon]
        lower, upper = min(projections), max(projections)
        span = upper - lower
        if span <= road_half_width * 4:
            continue
        for _ in range(5):
            ratio = rng.uniform(0.38, 0.62)
            offset = lower + span * ratio
            first = _clip_half_plane(
                polygon, normal, offset - road_half_width, keep_lower=True
            )
            second = _clip_half_plane(
                polygon, normal, offset + road_half_width, keep_lower=False
            )
            if len(first) < 3 or len(second) < 3:
                continue
            first_area, second_area = _area_m2(first), _area_m2(second)
            if min(first_area, second_area) < MIN_PARCEL_AREA_M2:
                continue
            segment = _line_segment_in_polygon(polygon, normal, offset)
            if segment is None:
                continue
            compactness = min(_compactness(first), _compactness(second))
            balance = min(first_area, second_area) / max(first_area, second_area)
            score = compactness * 2 + balance * 0.35 + rng.random() * 0.025
            if best is None or score > best[0]:
                best = score, first, second, segment
    return None if best is None else (best[1], best[2], best[3])


def _subdivide_cell(
    cell: tuple[int, int],
    target_count: int,
    *,
    seed: int,
) -> tuple[list[Polygon], list[tuple[Point, Point]]]:
    rng = random.Random(_stable_seed(seed, cell[0], cell[1], "recursive-blocks"))
    parcels = [_hexagon(cell)]
    road_segments: list[tuple[Point, Point]] = []
    while len(parcels) < target_count:
        split_completed = False
        candidates = sorted(
            range(len(parcels)),
            key=lambda index: (_area_map_units(parcels[index]), rng.random()),
            reverse=True,
        )
        for index in candidates:
            split = _try_split(parcels[index], rng)
            if split is None:
                continue
            first, second, road = split
            parcels[index] = first
            parcels.append(second)
            road_segments.append(road)
            split_completed = True
            break
        if not split_completed:
            raise RuntimeError(
                f"cell {cell} 无法生成 {target_count} 个满足最小面积的建筑地块"
            )
    return parcels, road_segments


def _canonical_point(point: Point) -> Point:
    """Collapse floating-point noise so shared junctions compare exactly."""

    return round(float(point[0]), 12), round(float(point[1]), 12)


def _valid_segment(start: Point, end: Point) -> bool:
    return math.dist(start, end) > GEOMETRY_EPSILON


def _project_point_to_segment(
    point: Point,
    segment: _RoadSegment,
) -> tuple[Point, float]:
    dx = segment.end[0] - segment.start[0]
    dy = segment.end[1] - segment.start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= GEOMETRY_EPSILON**2:
        return segment.start, math.dist(point, segment.start)
    ratio = (
        (point[0] - segment.start[0]) * dx
        + (point[1] - segment.start[1]) * dy
    ) / length_squared
    ratio = max(0.0, min(1.0, ratio))
    projected = _canonical_point((
        segment.start[0] + ratio * dx,
        segment.start[1] + ratio * dy,
    ))
    return projected, math.dist(point, projected)


def _segment_intersection(
    first: _RoadSegment,
    second: _RoadSegment,
) -> Point | None:
    ax, ay = first.start
    bx, by = first.end
    cx, cy = second.start
    dx, dy = second.end
    first_dx, first_dy = bx - ax, by - ay
    second_dx, second_dy = dx - cx, dy - cy
    denominator = first_dx * second_dy - first_dy * second_dx
    if abs(denominator) <= GEOMETRY_EPSILON:
        return None
    offset_x, offset_y = cx - ax, cy - ay
    first_ratio = (offset_x * second_dy - offset_y * second_dx) / denominator
    second_ratio = (offset_x * first_dy - offset_y * first_dx) / denominator
    if not (
        -GEOMETRY_EPSILON <= first_ratio <= 1 + GEOMETRY_EPSILON
        and -GEOMETRY_EPSILON <= second_ratio <= 1 + GEOMETRY_EPSILON
    ):
        return None
    return _canonical_point((
        ax + first_ratio * first_dx,
        ay + first_ratio * first_dy,
    ))


def _closest_segment_points(
    first: _RoadSegment,
    second: _RoadSegment,
) -> tuple[Point, Point, float]:
    intersection = _segment_intersection(first, second)
    if intersection is not None:
        return intersection, intersection, 0.0

    candidates: list[tuple[float, Point, Point]] = []
    for point in (first.start, first.end):
        projected, distance = _project_point_to_segment(point, second)
        candidates.append((distance, point, projected))
    for point in (second.start, second.end):
        projected, distance = _project_point_to_segment(point, first)
        candidates.append((distance, projected, point))
    distance, first_point, second_point = min(
        candidates,
        key=lambda item: (
            item[0],
            item[1][0],
            item[1][1],
            item[2][0],
            item[2][1],
        ),
    )
    return _canonical_point(first_point), _canonical_point(second_point), distance


def _segment_pieces(
    segment: _RoadSegment,
    point_on_segment: Point,
    junction: Point,
) -> list[_RoadSegment]:
    """Split a segment at a point, optionally bending it to a snapped junction."""

    pieces = []
    if math.dist(segment.start, point_on_segment) > GEOMETRY_EPSILON:
        pieces.append(_RoadSegment(segment.start, junction, segment.kind))
    if math.dist(segment.end, point_on_segment) > GEOMETRY_EPSILON:
        pieces.append(_RoadSegment(junction, segment.end, segment.kind))
    if not pieces and _valid_segment(segment.start, junction):
        pieces.append(_RoadSegment(segment.start, junction, segment.kind))
    return [piece for piece in pieces if _valid_segment(piece.start, piece.end)]


def _replace_with_pieces(
    segments: list[_RoadSegment],
    index: int,
    point_on_segment: Point,
    junction: Point,
) -> None:
    pieces = _segment_pieces(segments[index], point_on_segment, junction)
    segments[index:index + 1] = pieces


def _snap_network_point(
    segments: list[_RoadSegment],
    index: int,
    point_on_segment: Point,
    junction: Point,
) -> None:
    """Move an existing network node without separating its incident roads."""

    segment = segments[index]
    endpoint = next(
        (
            candidate
            for candidate in (segment.start, segment.end)
            if math.dist(candidate, point_on_segment) <= GEOMETRY_EPSILON
        ),
        None,
    )
    if endpoint is None:
        _replace_with_pieces(segments, index, point_on_segment, junction)
        return

    old_node = _canonical_point(endpoint)
    for existing in segments:
        if _canonical_point(existing.start) == old_node:
            existing.start = junction
        if _canonical_point(existing.end) == old_node:
            existing.end = junction
    segments[:] = [
        existing
        for existing in segments
        if _valid_segment(existing.start, existing.end)
    ]


def _connect_segment_to_network(
    network: list[_RoadSegment],
    raw_segment: tuple[Point, Point],
) -> int:
    """Add a subdivision road and guarantee a junction with the existing network."""

    incoming = _RoadSegment(
        _canonical_point(raw_segment[0]),
        _canonical_point(raw_segment[1]),
        "subdivision",
    )
    if not network:
        network.append(incoming)
        return 0

    closest = min(
        (
            (*_closest_segment_points(incoming, existing), index)
            for index, existing in enumerate(network)
        ),
        key=lambda item: (item[2], item[3]),
    )
    incoming_point, network_point, distance, network_index = closest
    snap_distance = ENDPOINT_SNAP_DISTANCE_M / HEX_SIDE_LENGTH_M
    if distance <= snap_distance:
        junction = _canonical_point((
            (incoming_point[0] + network_point[0]) / 2,
            (incoming_point[1] + network_point[1]) / 2,
        ))
        _snap_network_point(network, network_index, network_point, junction)
        network.extend(_segment_pieces(incoming, incoming_point, junction))
        return 1

    _replace_with_pieces(network, network_index, network_point, network_point)
    network.extend(_segment_pieces(incoming, incoming_point, incoming_point))
    network.append(_RoadSegment(
        _canonical_point(incoming_point),
        _canonical_point(network_point),
        "junction_connector",
    ))
    return 0


def _connect_point_to_network(
    network: list[_RoadSegment],
    point: Point,
) -> int:
    """Connect a canonical cell gateway to the cell's local road network."""

    gateway = _canonical_point(point)
    closest = min(
        (
            (*_project_point_to_segment(gateway, segment), index)
            for index, segment in enumerate(network)
        ),
        key=lambda item: (item[1], item[2]),
    )
    network_point, distance, network_index = closest
    snap_distance = ENDPOINT_SNAP_DISTANCE_M / HEX_SIDE_LENGTH_M
    if distance <= snap_distance:
        _snap_network_point(network, network_index, network_point, gateway)
        return 1

    _replace_with_pieces(network, network_index, network_point, network_point)
    network.append(_RoadSegment(gateway, network_point, "cell_connector"))
    return 0


def _deduplicate_segments(segments: Sequence[_RoadSegment]) -> list[_RoadSegment]:
    result: list[_RoadSegment] = []
    seen: set[tuple[Point, Point]] = set()
    for segment in segments:
        start = _canonical_point(segment.start)
        end = _canonical_point(segment.end)
        if not _valid_segment(start, end):
            continue
        key = tuple(sorted((start, end)))
        if key in seen:
            continue
        seen.add(key)
        result.append(_RoadSegment(start, end, segment.kind))
    return result


def _connected_local_roads(
    raw_segments: Sequence[tuple[Point, Point]],
    gateways: Sequence[Point],
) -> tuple[list[_RoadSegment], int]:
    network: list[_RoadSegment] = []
    snapped_count = 0
    for raw_segment in raw_segments:
        snapped_count += _connect_segment_to_network(network, raw_segment)
    if not network:
        raise RuntimeError("cell 没有生成可连接的内部道路")
    for gateway in gateways:
        snapped_count += _connect_point_to_network(network, gateway)
    return _deduplicate_segments(network), snapped_count


def _scaled_polygon(polygon: Sequence[Point], ratio: float) -> Polygon:
    center = _centroid(polygon)
    return tuple(
        (
            center[0] + (point[0] - center[0]) * ratio,
            center[1] + (point[1] - center[1]) * ratio,
        )
        for point in polygon
    )


def _organization_cell(organization: Mapping[str, object]) -> tuple[int, int]:
    address = organization.get("address")
    if (
        not isinstance(address, (list, tuple))
        or len(address) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in address)
    ):
        raise ValueError(
            f"Organization“{organization.get('organization_id')}”缺少有效 address"
        )
    return int(address[0]), int(address[1])


def _map_cell(value: object, *, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(item is True or item is False or not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{label} 必须是二元整数 cell")
    return int(value[0]), int(value[1])


def _cell_edge(
    cell: tuple[int, int],
    direction_index: int,
) -> tuple[Point, Point, Point]:
    polygon = _hexagon(cell)
    left_index, right_index = _DIRECTION_EDGES[direction_index]
    left = _canonical_point(polygon[left_index])
    right = _canonical_point(polygon[right_index])
    gateway = _canonical_point(((left[0] + right[0]) / 2, (left[1] + right[1]) / 2))
    return left, right, gateway


def _cell_gateways(cell: tuple[int, int]) -> tuple[Point, ...]:
    return tuple(_cell_edge(cell, index)[2] for index in range(len(HEX_DIRECTIONS)))


def _boundary_roads(
    streets: Sequence[Street],
) -> list[dict[str, object]]:
    owners: dict[tuple[int, int], Street] = {
        _map_cell(cell, label=f"Street {street.name}.cells"): street
        for street in streets
        for cell in street.cells
    }
    roads: list[dict[str, object]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for cell, street in sorted(owners.items()):
        for direction_index, (dq, dr) in enumerate(HEX_DIRECTIONS):
            neighbor = cell[0] + dq, cell[1] + dr
            neighbor_street = owners.get(neighbor)
            if neighbor_street is not None and neighbor_street.name == street.name:
                continue
            edge_key = tuple(sorted((cell, neighbor)))
            if edge_key in seen:
                continue
            seen.add(edge_key)
            left, right, gateway = _cell_edge(cell, direction_index)
            level = (
                "district_boundary"
                if neighbor_street is None or neighbor_street.district != street.district
                else "street_boundary"
            )
            width = (
                DISTRICT_BOUNDARY_ROAD_WIDTH_M
                if level == "district_boundary"
                else STREET_BOUNDARY_ROAD_WIDTH_M
            )
            edge_serial = len(roads) // 2 + 1
            for part, centerline in enumerate(((left, gateway), (gateway, right)), 1):
                roads.append({
                    "road_id": f"ROAD-B-{edge_serial:08d}-{part}",
                    "district": street.district,
                    "street": street.name,
                    "level": level,
                    "width_m": width,
                    "segment_type": "boundary",
                    "centerline": list(centerline),
                    "cell": cell,
                    "neighbor_cell": neighbor if neighbor_street is not None else None,
                    "neighbor_street": (
                        neighbor_street.name if neighbor_street is not None else None
                    ),
                    "gateway": gateway,
                })
    return roads


def _road_graph_report(roads: Sequence[Mapping[str, object]]) -> dict[str, int]:
    adjacency: dict[Point, set[Point]] = defaultdict(set)
    for road in roads:
        centerline = road.get("centerline")
        if not isinstance(centerline, (list, tuple)) or len(centerline) != 2:
            raise RuntimeError(f"道路 {road.get('road_id')} 缺少二点中心线")
        start = _canonical_point(tuple(centerline[0]))  # type: ignore[arg-type]
        end = _canonical_point(tuple(centerline[1]))  # type: ignore[arg-type]
        if not _valid_segment(start, end):
            raise RuntimeError(f"道路 {road.get('road_id')} 长度为零")
        adjacency[start].add(end)
        adjacency[end].add(start)

    component_count = 0
    visited: set[Point] = set()
    for start in adjacency:
        if start in visited:
            continue
        component_count += 1
        pending = [start]
        visited.add(start)
        while pending:
            node = pending.pop()
            for neighbor in adjacency[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
    return {
        "road_node_count": len(adjacency),
        "road_edge_count": len(roads),
        "road_component_count": component_count,
        "road_dead_end_count": sum(len(neighbors) == 1 for neighbors in adjacency.values()),
    }


def generate_urban_layout(
    streets: Sequence[Street],
    organizations: Sequence[dict[str, object]],
    *,
    seed: int,
) -> UrbanLayout:
    """Generate a connected road graph, parcels, and headcount-ranked buildings."""
    street_by_cell: dict[tuple[int, int], Street] = {}
    for street in streets:
        for raw_cell in street.cells:
            cell = _map_cell(raw_cell, label=f"Street {street.name}.cells")
            if cell in street_by_cell:
                raise ValueError(f"cell {cell} 同时属于多条 Street")
            street_by_cell[cell] = street

    organizations_by_cell: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for organization in organizations:
        cell = _organization_cell(organization)
        if cell not in street_by_cell:
            raise ValueError(
                f"Organization“{organization.get('organization_id')}”的 cell {cell} 不在地图中"
            )
        organizations_by_cell[cell].append(organization)

    roads = _boundary_roads(streets)
    parcels: list[dict[str, object]] = []
    buildings: list[dict[str, object]] = []
    parcel_counts_by_cell: list[int] = []
    assigned_count = 0
    snapped_connection_count = 0
    cells = sorted(street_by_cell, key=lambda item: (street_by_cell[item].name, item))
    for cell_index, cell in enumerate(cells, 1):
        street = street_by_cell[cell]
        cell_organizations = organizations_by_cell.get(cell, [])
        target_count = max(MIN_PARCELS_PER_CELL, len(cell_organizations) + 1)
        cell_parcels, local_roads = _subdivide_cell(cell, target_count, seed=seed)
        connected_local_roads, snapped_count = _connected_local_roads(
            local_roads,
            _cell_gateways(cell),
        )
        snapped_connection_count += snapped_count
        parcel_counts_by_cell.append(len(cell_parcels))
        for local_index, segment in enumerate(connected_local_roads, 1):
            roads.append({
                "road_id": f"ROAD-L-{cell_index:06d}-{local_index:03d}",
                "district": street.district,
                "street": street.name,
                "level": "local",
                "width_m": LOCAL_ROAD_WIDTH_M,
                "segment_type": segment.kind,
                "centerline": [segment.start, segment.end],
                "cell": cell,
                "neighbor_cell": None,
                "neighbor_street": None,
            })

        parcel_rows: list[dict[str, object]] = []
        for parcel_index, polygon in enumerate(cell_parcels, 1):
            parcel_id = f"PARCEL-{cell_index:06d}-{parcel_index:03d}"
            building_id = f"BUILDING-{cell_index:06d}-{parcel_index:03d}"
            footprint = _scaled_polygon(polygon, BUILDING_INSET_RATIO)
            position = _centroid(footprint)
            parcel = {
                "parcel_id": parcel_id,
                "district": street.district,
                "street": street.name,
                "cell": cell,
                "area_m2": _area_m2(polygon),
                "polygon": list(polygon),
                "organization_id": None,
                "building_id": building_id,
            }
            building = {
                "building_id": building_id,
                "parcel_id": parcel_id,
                "district": street.district,
                "street": street.name,
                "cell": cell,
                "footprint": list(footprint),
                "footprint_area_m2": _area_m2(footprint),
                "position": position,
                "organization_id": None,
            }
            parcel["_building"] = building
            parcel_rows.append(parcel)

        ranked_organizations = sorted(
            cell_organizations,
            key=lambda organization: (
                -int(organization.get("population_usage", 0)),
                str(organization.get("organization_id", "")),
            ),
        )
        ranked_parcels = sorted(
            parcel_rows,
            key=lambda parcel: (-float(parcel["area_m2"]), str(parcel["parcel_id"])),
        )
        for organization, parcel in zip(ranked_organizations, ranked_parcels):
            organization_id = str(organization["organization_id"])
            building = parcel["_building"]
            parcel["organization_id"] = organization_id
            building["organization_id"] = organization_id
            organization.update({
                "parcel_id": parcel["parcel_id"],
                "building_id": building["building_id"],
                "position": building["position"],
                "parcel_area_m2": parcel["area_m2"],
                "building_footprint": building["footprint"],
                "building_area_m2": building["footprint_area_m2"],
            })
            assigned_count += 1

        for parcel in parcel_rows:
            buildings.append(parcel.pop("_building"))
            parcels.append(parcel)

    graph_report = _road_graph_report(roads)
    report = {
        "cell_count": len(cells),
        "road_count": len(roads),
        "parcel_count": len(parcels),
        "building_count": len(buildings),
        "organization_count": len(organizations),
        "assigned_organization_count": assigned_count,
        "minimum_parcels_per_cell": min(parcel_counts_by_cell, default=0),
        "snapped_connection_count": snapped_connection_count,
        "junction_connector_count": sum(
            road.get("segment_type") == "junction_connector" for road in roads
        ),
        "cell_connector_count": sum(
            road.get("segment_type") == "cell_connector" for road in roads
        ),
        **graph_report,
    }
    if assigned_count != len(organizations):
        raise RuntimeError(
            f"仅为 {assigned_count}/{len(organizations)} 个 Organization 分配了建筑"
        )
    expected_component_count = 1 if roads else 0
    if graph_report["road_component_count"] != expected_component_count:
        raise RuntimeError(
            "城市道路拓扑未完全联通："
            f"检测到 {graph_report['road_component_count']} 个连通分量"
        )
    return UrbanLayout(roads, parcels, buildings, report)


__all__ = (
    "BUILDING_INSET_RATIO",
    "ENDPOINT_SNAP_DISTANCE_M",
    "MIN_PARCELS_PER_CELL",
    "UrbanLayout",
    "generate_urban_layout",
)
