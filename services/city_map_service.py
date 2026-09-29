"""Read-only map snapshots for the player-facing city map."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from .. import config


CITY_LANDMARK_TEMPLATES = {
    "casino_1": "casino",
    "crime_syndicate_city_level_1": "crime_headquarters",
}
ORGANIZATION_MARKERS = {
    **{key: (kind, "city") for key, kind in CITY_LANDMARK_TEMPLATES.items()},
    **{key: ("sex_service", "city" if key in {"luxury_business_ktv", "adult_nightclub", "adult_club"} else "cell") for key in config.SEX_SERVICE_VENUE_OPEN_PERIODS},
    "loan_shark_chain_1": ("lender", "city"),
    "loan_shark_chain_store_1": ("lender", "district"),
    "underground_lender": ("lender", "cell"),
    "neighborhood_lender": ("lender", "cell"),
}
MAX_CELL_DETAIL_REQUEST = 5


@dataclass(slots=True, frozen=True)
class MapRegion:
    name: str
    district_name: str | None
    cells: tuple[tuple[int, int], ...]


@dataclass(slots=True, frozen=True)
class MapLandmark:
    organization_id: str
    name: str
    kind: str
    address: tuple[int, int]
    position: tuple[float, float]
    min_level: str = "city"
    can_enter: bool = False


@dataclass(slots=True, frozen=True)
class MapRoad:
    level: str
    kind: str
    centerline: tuple[tuple[float, float], tuple[float, float]]


@dataclass(slots=True, frozen=True)
class MapBusStop:
    stop_id: str
    name: str
    position: tuple[float, float]


@dataclass(slots=True, frozen=True)
class CityMapSnapshot:
    city_id: str
    city_name: str
    player_address: tuple[int, int]
    player_district: str
    player_street: str
    districts: tuple[MapRegion, ...]
    streets: tuple[MapRegion, ...]
    landmarks: tuple[MapLandmark, ...]
    roads: tuple[MapRoad, ...]
    bus_stops: tuple[MapBusStop, ...]
    player_position: tuple[float, float] | None = None
    player_stop_id: str | None = None


def _payload(raw_value: object, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw_value) if isinstance(raw_value, str) else None
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} 的 data_json 无效") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} 的 data_json 必须是对象")
    return value


def _cell(value: object, *, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{label} 必须是二元整数 cell 坐标")
    return int(value[0]), int(value[1])


def _position(value: object, *, label: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        )
    ):
        raise ValueError(f"{label} 必须是二元数值坐标")
    return float(value[0]), float(value[1])


def _cell_center(cell: tuple[int, int]) -> tuple[float, float]:
    q, r = cell
    return 3 ** 0.5 * (q + r / 2), 1.5 * r


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _road(level: object, raw_payload: object, *, label: str) -> MapRoad:
    payload = _payload(raw_payload, label=label)
    centerline = payload.get("centerline")
    if not isinstance(centerline, list) or len(centerline) != 2:
        raise ValueError(f"{label}.centerline 必须包含两个端点")
    return MapRoad(
        level=str(level),
        kind=str(payload.get("segment_type") or "boundary"),
        centerline=(
            _position(centerline[0], label=f"{label}.centerline[0]"),
            _position(centerline[1], label=f"{label}.centerline[1]"),
        ),
    )


def _region(
    name: str,
    district_name: str | None,
    raw_payload: object,
    *,
    label: str,
) -> MapRegion:
    payload = _payload(raw_payload, label=label)
    raw_cells = payload.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise ValueError(f"{label} 缺少地图 cells")
    return MapRegion(
        name=name,
        district_name=district_name,
        cells=tuple(
            _cell(value, label=f"{label}.cells")
            for value in raw_cells
        ),
    )


def load_city_map_snapshot(
    database_path: str | Path,
    player_character_id: str,
    player_organization_id: str | None = None,
) -> CityMapSnapshot:
    """Load geometry, current player position, and city-level landmarks."""
    database = Path(database_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"世界数据库不存在：{database}")
    if not str(player_character_id).strip():
        raise ValueError("player_character_id 不能为空")

    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        player_row = connection.execute(
            """
            SELECT city_id, district_name, street_name, data_json
            FROM characters
            WHERE character_id = ?
            """,
            (player_character_id,),
        ).fetchone()
        if player_row is None:
            raise LookupError(f"世界中不存在玩家角色：{player_character_id}")
        city_id = str(player_row[0])
        player_payload = _payload(
            player_row[3], label=f"玩家角色 {player_character_id}"
        )
        player_address = _cell(
            player_payload.get("address"),
            label=f"玩家角色 {player_character_id}.address",
        )

        city_row = connection.execute(
            "SELECT city_template FROM cities WHERE city_id = ?",
            (city_id,),
        ).fetchone()
        if city_row is None:
            raise LookupError(f"世界中不存在角色所属城市：{city_id}")

        districts = tuple(
            _region(
                str(name),
                None,
                raw_payload,
                label=f"区 {name}",
            )
            for name, raw_payload in connection.execute(
                """
                SELECT name, data_json FROM districts
                WHERE city_id = ? ORDER BY name
                """,
                (city_id,),
            )
        )
        streets = tuple(
            _region(
                str(name),
                str(district_name),
                raw_payload,
                label=f"街道 {name}",
            )
            for name, district_name, raw_payload in connection.execute(
                """
                SELECT name, district_name, data_json FROM streets
                WHERE city_id = ? ORDER BY district_name, name
                """,
                (city_id,),
            )
        )

        landmark_rows = connection.execute(
            f"""
            SELECT organization_id, template_name, name, data_json
            FROM organizations
            WHERE city_id = ? AND (template_name IN ({",".join("?" for _ in ORGANIZATION_MARKERS)}) OR organization_id = ?)
            ORDER BY organization_id
            """,
            (city_id, *ORGANIZATION_MARKERS, player_organization_id),
        )
        landmarks: list[MapLandmark] = []
        for organization_id, template_name, name, raw_payload in landmark_rows:
            payload = _payload(raw_payload, label=f"组织 {organization_id}")
            address = _cell(
                payload.get("address"),
                label=f"组织 {organization_id}.address",
            )
            landmarks.append(
                MapLandmark(
                    organization_id=str(organization_id),
                    name=str(name),
                    can_enter=ORGANIZATION_MARKERS.get(str(template_name), ("",))[0] == "lender" and payload.get("offers_loans") is True,
                    kind="workplace" if str(organization_id) == player_organization_id else ORGANIZATION_MARKERS[str(template_name)][0],
                    min_level="city" if str(organization_id) == player_organization_id else ORGANIZATION_MARKERS[str(template_name)][1],
                    address=address,
                    position=(
                        _position(
                            payload.get("position"),
                            label=f"组织 {organization_id}.position",
                        )
                        if payload.get("position") is not None
                        else _cell_center(address)
                    ),
                )
            )

        roads: list[MapRoad] = []
        if _table_exists(connection, "roads"):
            # Local roads and their connectors are loaded together by visible cell.
            road_rows = connection.execute(
                """
                SELECT level, data_json FROM roads
                WHERE city_id = ? AND level != 'local'
                ORDER BY road_id
                """,
                (city_id,),
            )
            for level, raw_payload in road_rows:
                roads.append(_road(level, raw_payload, label="道路"))

        bus_stops: tuple[MapBusStop, ...] = ()
        if _table_exists(connection, "bus_stops"):
            bus_stops = tuple(
                MapBusStop(
                    stop_id=str(stop_id),
                    name=str(name),
                    position=(float(position_x), float(position_y)),
                )
                for stop_id, name, position_x, position_y in connection.execute(
                    """
                    SELECT stop_id, name, position_x, position_y FROM bus_stops
                    WHERE city_id = ? ORDER BY stop_id
                    """,
                    (city_id,),
                )
            )

        if not districts or not streets:
            raise ValueError(f"城市 {city_id} 缺少区或街道地图数据")
        return CityMapSnapshot(
            player_stop_id=(player_payload.get("current_location") or {}).get("stop_id"),
            player_position=_position(player_payload["current_location"]["position"], label="当前位置") if player_payload.get("current_location") else _cell_center(player_address),
            city_id=city_id,
            city_name=str(city_row[0]),
            player_address=player_address,
            player_district=str((player_payload.get("current_location") or {}).get("district") or next((street.district_name for street in streets if player_address in street.cells), player_row[1])),
            player_street=str((player_payload.get("current_location") or {}).get("street") or next((street.name for street in streets if player_address in street.cells), player_row[2])),
            districts=districts,
            streets=streets,
            landmarks=tuple(landmarks),
            roads=tuple(roads),
            bus_stops=bus_stops,
        )
    finally:
        connection.close()


def load_city_map_cell_roads(
    database_path: str | Path,
    city_id: str,
    cells: object,
) -> tuple[MapRoad, ...]:
    """Load local roads and connectors only for the requested visible cells."""
    database = Path(database_path).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"世界数据库不存在：{database}")
    if not str(city_id).strip():
        raise ValueError("city_id 不能为空")
    if not isinstance(cells, (list, tuple)):
        raise ValueError("cells 必须是坐标列表")
    requested = {
        _cell(value, label="cells")
        for value in cells
    }
    if len(requested) > MAX_CELL_DETAIL_REQUEST:
        raise ValueError(f"单次最多读取 {MAX_CELL_DETAIL_REQUEST} 个 cell")
    if not requested:
        return ()

    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        if not _table_exists(connection, "roads"):
            return ()
        street_names: list[str] = []
        for name, raw_payload in connection.execute(
            "SELECT name, data_json FROM streets WHERE city_id = ?",
            (city_id,),
        ):
            payload = _payload(raw_payload, label=f"街道 {name}")
            if any(
                _cell(value, label=f"街道 {name}.cells") in requested
                for value in payload.get("cells", ())
            ):
                street_names.append(str(name))
        if not street_names:
            return ()

        placeholders = ",".join("?" for _ in street_names)
        roads: list[MapRoad] = []
        for road_id, raw_payload in connection.execute(
            f"""
            SELECT road_id, data_json FROM roads
            WHERE city_id = ? AND level = 'local'
              AND street_name IN ({placeholders})
            ORDER BY road_id
            """,
            (city_id, *street_names),
        ):
            payload = _payload(raw_payload, label=f"道路 {road_id}")
            if (
                _cell(payload.get("cell"), label=f"道路 {road_id}.cell")
                in requested
            ):
                roads.append(_road("cell", raw_payload, label=f"道路 {road_id}"))
        return tuple(roads)
    finally:
        connection.close()


def load_city_map_buildings(database_path: str | Path, city_id: str, cells: object) -> list:
    """Read existing building footprints for at most five cells, without organization data."""
    if not isinstance(cells, (list, tuple)) or len(cells) > MAX_CELL_DETAIL_REQUEST:
        raise ValueError("建筑请求最多允许 5 个 cell")
    requested = sorted({_cell(cell, label="cells") for cell in cells})
    if not requested:
        return []
    database = Path(database_path).resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        if not _table_exists(connection, "buildings") or not _table_exists(connection, "parcels"):
            return []
        footprints = []
        for q, r in requested:
            for (raw,) in connection.execute(
                """SELECT b.data_json FROM parcels p JOIN buildings b
                   ON b.city_id=p.city_id AND b.parcel_id=p.parcel_id
                   WHERE p.city_id=? AND p.cell_q=? AND p.cell_r=?""",
                (city_id, q, r),
            ):
                polygon = _payload(raw, label="建筑").get("footprint")
                if not isinstance(polygon, list) or len(polygon) < 3:
                    raise ValueError("建筑缺少有效轮廓")
                footprints.append([_position(point, label="建筑轮廓") for point in polygon])
        return footprints
    finally:
        connection.close()


__all__ = (
    "CITY_LANDMARK_TEMPLATES",
    "MAX_CELL_DETAIL_REQUEST",
    "CityMapSnapshot",
    "MapLandmark",
    "MapRoad",
    "MapBusStop",
    "MapRegion",
    "load_city_map_snapshot",
    "load_city_map_cell_roads",
    "load_city_map_buildings",
)
