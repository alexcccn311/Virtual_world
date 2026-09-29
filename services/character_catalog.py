"""Read generated characters through a stable frontend-facing contract."""
from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .character_description import (
    build_character_card_summary,
    build_character_descriptions,
)


WORLD_DATABASE_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
DAILY_CHARACTER_OCCUPATIONS = (
    "站街女",
    "妓女",
    "冰妹",
    "性奴",
    "SM妓女",
    "发廊妹",
)


@dataclass(frozen=True, slots=True)
class CharacterWorldDatabase:
    """One validated generated-world database available to the frontend."""

    world_id: str
    name: str
    database: Path
    city_templates: tuple[str, ...]
    city_count: int

    @property
    def display_name(self) -> str:
        templates = "、".join(self.city_templates)
        return f"{self.name} · {templates}"


_REQUIRED_CHARACTER_COLUMNS = {
    "character_id",
    "city_id",
    "organization_id",
    "organization_name",
    "district_name",
    "street_name",
    "name",
    "occupation",
    "title",
    "data_json",
}


def _readonly_connection(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )


def _inspect_world_database(database: Path) -> CharacterWorldDatabase | None:
    try:
        with closing(_readonly_connection(database)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not {"cities", "characters"}.issubset(tables):
                return None
            character_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(characters)")
            }
            if not _REQUIRED_CHARACTER_COLUMNS.issubset(character_columns):
                return None
            complete_cities = connection.execute(
                """
                SELECT city_template
                FROM cities
                WHERE status = 'complete'
                ORDER BY city_id
                """
            ).fetchall()
            if not complete_cities:
                return None
            placeholders = ",".join("?" for _ in DAILY_CHARACTER_OCCUPATIONS)
            eligible = connection.execute(
                f"""
                SELECT 1 FROM characters
                WHERE occupation IN ({placeholders})
                LIMIT 1
                """,
                DAILY_CHARACTER_OCCUPATIONS,
            ).fetchone()
            if eligible is None:
                return None
    except (OSError, sqlite3.Error):
        return None

    templates = tuple(sorted({str(row[0]) for row in complete_cities}))
    return CharacterWorldDatabase(
        world_id=database.name,
        name=database.stem,
        database=database.resolve(),
        city_templates=templates,
        city_count=len(complete_cities),
    )


def discover_character_worlds(
    data_directory: Path = WORLD_DATABASE_DIRECTORY,
) -> tuple[CharacterWorldDatabase, ...]:
    """Find generated SQLite worlds without relying on a fixed filename."""

    directory = Path(data_directory)
    if not directory.is_dir():
        return ()
    worlds = [
        world
        for database in sorted(
            directory.glob("*.sqlite3"),
            key=lambda path: path.name.casefold(),
        )
        if (world := _inspect_world_database(database)) is not None
    ]
    return tuple(worlds)


def character_world_by_id(
    world_id: str,
    *,
    data_directory: Path = WORLD_DATABASE_DIRECTORY,
) -> CharacterWorldDatabase:
    for world in discover_character_worlds(data_directory):
        if world.world_id == world_id:
            return world
    raise LookupError(f"角色世界不存在或数据库结构无效：{world_id}")


def _display_list(value: object, fallback: str = "暂无") -> str:
    if isinstance(value, Mapping):
        return "、".join(f"{key}：{item}" for key, item in value.items()) or fallback
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) or fallback
    if value is None or value == "":
        return fallback
    return str(value)


def _score_grade(
    score: float,
    labels: tuple[str, str, str, str],
) -> str:
    """Convert the generator's 0–100 score to a concise display band."""
    if score < 55:
        return labels[0]
    if score < 70:
        return labels[1]
    if score < 85:
        return labels[2]
    return labels[3]


def _required_number(character: Mapping[str, object], key: str) -> float:
    value = character.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"角色字段 {key} 不是有效数字")
    return float(value)


def _normalise_character(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["data_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"角色 {row['character_id']} 的 data_json 无效") from error
    if not isinstance(payload, dict):
        raise ValueError(f"角色 {row['character_id']} 的 data_json 必须是对象")

    # Indexed relational columns are authoritative for identity and location.
    character: dict[str, Any] = dict(payload)
    character.update(
        {
            "id": str(row["character_id"]),
            "name": str(row["name"]),
            "occupation": str(row["occupation"]),
            "title": str(row["title"]),
            "district": str(row["district_name"]),
            "street": str(row["street_name"]),
            "organization_id": str(row["organization_id"]),
            "organization_name": str(row["organization_name"]),
            "organization_template": (
                str(row["organization_template"])
                if "organization_template" in row.keys()
                and row["organization_template"]
                else str(character.get("organization_template") or "")
            ),
        }
    )
    if character.get("sex") != "female" or int(character.get("age", 0)) < 18:
        raise ValueError(f"角色 {row['character_id']} 不是成年女性")

    descriptions = build_character_descriptions(character)
    face_score = _required_number(character, "face_score")
    body_score = _required_number(character, "body_score")
    appearance_score = _required_number(character, "appearance_score")
    face_grade = _score_grade(
        appearance_score,
        ("相貌平庸", "相貌普通", "长相漂亮", "漂亮得十分惹眼"),
    )
    body_grade = _score_grade(
        body_score,
        ("身材较差", "身材普通", "身材出众", "身材极佳"),
    )
    age = int(character["age"])
    organization = character["organization_name"] or "无固定组织"
    title = character["title"] or "普通成员"
    summary = build_character_card_summary(character)

    profile = {
        "姓名": character["name"],
        "昵称": _display_list(character.get("nickname"), "无"),
        "年龄": f"{age} 岁",
        "职业": character["occupation"],
        "所在城区": character["district"],
        "所在街道": character["street"],
        "所属组织": organization,
        "组织身份": title,
        "学历": _display_list(character.get("education")),
        "发型": _display_list(character.get("hair")),
        "皮肤状态": _display_list(character.get("skin_quality")),
        "面部特征": descriptions["face_description"],
        "身材数据": descriptions["body_metrics_text"],
        "身材描述": descriptions["body_description"],
        "外在状态": _display_list(character.get("presentation")),
        "气质风格": _display_list(character.get("temperament")),
        "性格": _display_list(character.get("personality_tags")),
        "爱好": _display_list(character.get("hobbies")),
        "卫生状况": _display_list(character.get("hygiene")),
        "综合外貌": f"{face_grade}（{appearance_score:.2f}）",
        "面部评分": f"{face_score:.2f}",
        "身材评价": f"{body_grade}（{body_score:.2f}）",
        "技能": _display_list(character.get("skills")),
        "月收入": f"¥{int(character.get('income', 0)):,}",
        "现金": f"¥{int(character.get('cash', 0)):,}",
        "其他资产": f"¥{int(character.get('other_assets', 0)):,}",
        "债务": f"¥{int(character.get('debt', 0)):,}",
        "净资产": f"¥{int(character.get('net_assets', 0)):,}",
    }

    # Compatibility aliases keep saved records readable while the frontend
    # migrates from the old flat city_population schema.
    character.update(
        {
            "source_character_id": character["id"],
            "city_id": str(row["city_id"]),
            "organization": organization,
            "rank": title,
            "net_worth": int(character.get("net_assets", 0)),
            "appearance_tags": [
                value
                for value in (
                    character.get("hair"),
                    character.get("presentation"),
                    character.get("feature"),
                )
                if value
            ],
            "style_vibe": character.get("temperament"),
            "attractiveness_score": appearance_score,
            "attractiveness_grade": face_grade,
            "body_grade": body_grade,
            "character_descriptions": descriptions,
            "summary": summary,
            "profile": profile,
        }
    )
    return character


def draw_world_character(
    occupation: str,
    *,
    excluded_source_ids: set[str] | None = None,
    database: Path | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Draw one eligible generated-world character without loading the world."""
    if occupation != "随机" and occupation not in DAILY_CHARACTER_OCCUPATIONS:
        raise ValueError("未知的职业选项")
    if database is None:
        worlds = discover_character_worlds()
        if not worlds:
            raise FileNotFoundError("data 文件夹中没有可用的角色世界数据库")
        database = worlds[0].database
    database = Path(database)
    if not database.is_file():
        raise FileNotFoundError(f"世界角色数据库不存在：{database}")

    selected = (
        DAILY_CHARACTER_OCCUPATIONS if occupation == "随机" else (occupation,)
    )
    parameters: list[object] = list(selected)
    # These six generated occupations are adult-female roles by definition.
    # Keep the hot query on relational columns so SQLite can use the occupation
    # index instead of parsing more than half a million JSON documents.
    clauses = [f"occupation IN ({','.join('?' for _ in selected)})"]
    excluded = sorted(excluded_source_ids or ())
    if excluded:
        clauses.append(
            f"character_id NOT IN ({','.join('?' for _ in excluded)})"
        )
        parameters.extend(excluded)
    where_sql = " AND ".join(clauses)

    with closing(_readonly_connection(database)) as connection:
        connection.row_factory = sqlite3.Row
        organization_columns = {
            str(item[1])
            for item in connection.execute("PRAGMA table_info(organizations)")
        }
        organization_template_sql = (
            "(SELECT template_name FROM organizations "
            "WHERE organizations.organization_id = characters.organization_id)"
            if "template_name" in organization_columns
            else "NULL"
        )
        count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM characters WHERE {where_sql}", parameters
            ).fetchone()[0]
        )
        if count == 0:
            raise LookupError("没有找到符合条件且尚未创建的角色")
        offset = (rng or random.SystemRandom()).randrange(count)
        row = connection.execute(
            f"""
            SELECT character_id, city_id, organization_id, organization_name,
                   district_name, street_name, name, occupation, title, data_json,
                   {organization_template_sql} AS organization_template
            FROM characters
            WHERE {where_sql}
            LIMIT 1 OFFSET ?
            """,
            [*parameters, offset],
        ).fetchone()
    if row is None:
        raise LookupError("随机抽取角色失败，请重试")
    character = _normalise_character(row)
    world_id = database.name
    source_character_id = character["source_character_id"]
    character.update(
        {
            "source_world_id": world_id,
            "source_world_name": database.stem,
            "source_key": f"{world_id}:{source_character_id}",
        }
    )
    character["profile"] = {
        "世界": database.stem,
        **character["profile"],
    }
    return character
