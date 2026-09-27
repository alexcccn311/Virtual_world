"""Read-only map snapshots for the player-facing city map."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


CITY_LANDMARK_TEMPLATES = {
    "casino_1": "casino",
    "crime_syndicate_city_level_1": "crime_headquarters",
}


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
            """
            SELECT organization_id, template_name, name, data_json
            FROM organizations
            WHERE city_id = ? AND template_name IN (?, ?)
            ORDER BY organization_id
            """,
            (city_id, *CITY_LANDMARK_TEMPLATES),
        )
        landmarks: list[MapLandmark] = []
        for organization_id, template_name, name, raw_payload in landmark_rows:
            payload = _payload(raw_payload, label=f"组织 {organization_id}")
            landmarks.append(
                MapLandmark(
                    organization_id=str(organization_id),
                    name=str(name),
                    kind=CITY_LANDMARK_TEMPLATES[str(template_name)],
                    address=_cell(
                        payload.get("address"),
                        label=f"组织 {organization_id}.address",
                    ),
                )
            )

        if not districts or not streets:
            raise ValueError(f"城市 {city_id} 缺少区或街道地图数据")
        return CityMapSnapshot(
            city_id=city_id,
            city_name=str(city_row[0]),
            player_address=player_address,
            player_district=str(player_row[1]),
            player_street=str(player_row[2]),
            districts=districts,
            streets=streets,
            landmarks=tuple(landmarks),
        )
    finally:
        connection.close()


__all__ = (
    "CITY_LANDMARK_TEMPLATES",
    "CityMapSnapshot",
    "MapLandmark",
    "MapRegion",
    "load_city_map_snapshot",
)
