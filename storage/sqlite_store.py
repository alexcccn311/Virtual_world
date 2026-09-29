"""Buffered SQLite persistence for streamed world generation."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .global_id_registry import (
    GLOBAL_ID_REGISTRY_FILENAME,
    GlobalCharacterIdRegistry,
)


_ID_FORMATS = {
    "city": ("CITY", 8),
    "organization": ("ORG", 8),
    "character": ("CUS", 8),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _format_id(kind: str, number: int) -> str:
    prefix, width = _ID_FORMATS[kind]
    return f"{prefix}-{number:0{width}d}"


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS id_sequences (
    kind TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL CHECK (next_value >= 1)
);

CREATE TABLE IF NOT EXISTS reserved_ids (
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    owner_city_id TEXT,
    reserved_at TEXT NOT NULL,
    PRIMARY KEY (kind, entity_id)
);

CREATE TABLE IF NOT EXISTS cities (
    city_id TEXT PRIMARY KEY,
    city_template TEXT NOT NULL,
    seed INTEGER NOT NULL,
    target_population INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('generating', 'complete', 'failed')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    data_json TEXT
);

CREATE TABLE IF NOT EXISTS districts (
    city_id TEXT NOT NULL,
    name TEXT NOT NULL,
    template_name TEXT NOT NULL,
    level TEXT NOT NULL,
    population INTEGER NOT NULL,
    prosperity INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (city_id, name),
    FOREIGN KEY (city_id) REFERENCES cities(city_id)
);

CREATE TABLE IF NOT EXISTS streets (
    city_id TEXT NOT NULL,
    name TEXT NOT NULL,
    district_name TEXT NOT NULL,
    prosperity INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (city_id, name),
    FOREIGN KEY (city_id, district_name)
        REFERENCES districts(city_id, name)
);

CREATE TABLE IF NOT EXISTS organizations (
    organization_id TEXT PRIMARY KEY,
    city_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    parent_organization_id TEXT,
    manager_id TEXT,
    root_organization_id TEXT NOT NULL,
    population_usage INTEGER NOT NULL,
    special_role_character_id TEXT,
    data_json TEXT NOT NULL,
    FOREIGN KEY (city_id) REFERENCES cities(city_id),
    FOREIGN KEY (parent_organization_id)
        REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_organizations_city
    ON organizations(city_id);
CREATE INDEX IF NOT EXISTS idx_organizations_parent
    ON organizations(parent_organization_id);

CREATE TABLE IF NOT EXISTS roads (
    city_id TEXT NOT NULL,
    road_id TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    level TEXT NOT NULL CHECK (
        level IN ('district_boundary', 'street_boundary', 'local')
    ),
    width_m REAL NOT NULL CHECK (width_m > 0),
    data_json TEXT NOT NULL,
    PRIMARY KEY (city_id, road_id),
    FOREIGN KEY (city_id) REFERENCES cities(city_id)
);

CREATE INDEX IF NOT EXISTS idx_roads_street
    ON roads(city_id, street_name, level);

CREATE TABLE IF NOT EXISTS bus_systems (
    city_id TEXT PRIMARY KEY,
    service_start_minute INTEGER NOT NULL CHECK (
        service_start_minute >= 0 AND service_start_minute < 1440
    ),
    service_end_minute INTEGER NOT NULL CHECK (
        service_end_minute > service_start_minute AND service_end_minute < 1440
    ),
    frequency_minutes INTEGER NOT NULL CHECK (frequency_minutes > 0),
    speed_kmh REAL NOT NULL CHECK (speed_kmh > 0),
    stop_min_spacing_m REAL NOT NULL CHECK (stop_min_spacing_m > 0),
    data_json TEXT NOT NULL,
    FOREIGN KEY (city_id) REFERENCES cities(city_id)
);

CREATE TABLE IF NOT EXISTS bus_stops (
    city_id TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    name TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    road_id TEXT NOT NULL,
    position_x REAL NOT NULL,
    position_y REAL NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (city_id, stop_id),
    FOREIGN KEY (city_id) REFERENCES cities(city_id),
    FOREIGN KEY (city_id, road_id) REFERENCES roads(city_id, road_id)
);

CREATE INDEX IF NOT EXISTS idx_bus_stops_street
    ON bus_stops(city_id, street_name);

CREATE TABLE IF NOT EXISTS parcels (
    city_id TEXT NOT NULL,
    parcel_id TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    cell_q INTEGER NOT NULL,
    cell_r INTEGER NOT NULL,
    area_m2 REAL NOT NULL CHECK (area_m2 > 0),
    organization_id TEXT,
    data_json TEXT NOT NULL,
    PRIMARY KEY (city_id, parcel_id),
    UNIQUE (organization_id),
    FOREIGN KEY (city_id) REFERENCES cities(city_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_parcels_cell
    ON parcels(city_id, cell_q, cell_r);

CREATE TABLE IF NOT EXISTS buildings (
    city_id TEXT NOT NULL,
    building_id TEXT NOT NULL,
    parcel_id TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    organization_id TEXT,
    footprint_area_m2 REAL NOT NULL CHECK (footprint_area_m2 > 0),
    data_json TEXT NOT NULL,
    PRIMARY KEY (city_id, building_id),
    UNIQUE (city_id, parcel_id),
    UNIQUE (organization_id),
    FOREIGN KEY (city_id, parcel_id) REFERENCES parcels(city_id, parcel_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_buildings_street
    ON buildings(city_id, street_name);

CREATE TABLE IF NOT EXISTS characters (
    character_id TEXT PRIMARY KEY,
    city_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    organization_name TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    name TEXT NOT NULL,
    occupation TEXT NOT NULL,
    title TEXT NOT NULL,
    superior_id TEXT,
    service_to TEXT,
    data_json TEXT NOT NULL,
    FOREIGN KEY (city_id) REFERENCES cities(city_id),
    FOREIGN KEY (organization_id)
        REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_characters_city
    ON characters(city_id);
CREATE INDEX IF NOT EXISTS idx_characters_organization
    ON characters(organization_id);
CREATE INDEX IF NOT EXISTS idx_characters_occupation
    ON characters(occupation);

CREATE TABLE IF NOT EXISTS player_loan_contracts (
    loan_id TEXT PRIMARY KEY,
    borrower_character_id TEXT NOT NULL,
    lender_organization_id TEXT NOT NULL,
    lender_organization_name TEXT NOT NULL,
    borrowed_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    principal INTEGER NOT NULL CHECK (principal > 0),
    cash_disbursed INTEGER NOT NULL CHECK (cash_disbursed >= 0),
    monthly_rate_bps INTEGER NOT NULL CHECK (monthly_rate_bps >= 0),
    interest INTEGER NOT NULL CHECK (interest >= 0),
    fee_bps INTEGER NOT NULL CHECK (fee_bps >= 0),
    fee INTEGER NOT NULL CHECK (fee >= 0),
    total_due INTEGER NOT NULL CHECK (total_due > 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'paid', 'overdue')),
    repaid_at TEXT,
    request_id TEXT UNIQUE,
    FOREIGN KEY (borrower_character_id) REFERENCES characters(character_id),
    FOREIGN KEY (lender_organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_player_loan_contracts_borrower
    ON player_loan_contracts(borrower_character_id, borrowed_at);
CREATE INDEX IF NOT EXISTS idx_player_loan_contracts_due
    ON player_loan_contracts(status, due_at);

CREATE TABLE IF NOT EXISTS player_debt_initializations (
    borrower_character_id TEXT PRIMARY KEY,
    original_debt INTEGER NOT NULL CHECK (original_debt >= 0),
    initialized_debt INTEGER NOT NULL CHECK (initialized_debt >= 0),
    initialized_at TEXT NOT NULL,
    FOREIGN KEY (borrower_character_id) REFERENCES characters(character_id)
);

CREATE TABLE IF NOT EXISTS organization_assignments (
    parent_organization_id TEXT NOT NULL,
    child_organization_id TEXT PRIMARY KEY,
    declaration_key TEXT NOT NULL,
    instance_index INTEGER NOT NULL,
    manager_id TEXT NOT NULL,
    district_name TEXT NOT NULL,
    street_name TEXT NOT NULL,
    FOREIGN KEY (parent_organization_id)
        REFERENCES organizations(organization_id),
    FOREIGN KEY (child_organization_id)
        REFERENCES organizations(organization_id)
);

CREATE TABLE IF NOT EXISTS population_usage (
    city_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('city', 'district', 'street')),
    district_name TEXT NOT NULL DEFAULT '',
    street_name TEXT NOT NULL DEFAULT '',
    capacity INTEGER NOT NULL,
    used INTEGER NOT NULL,
    remaining INTEGER NOT NULL,
    PRIMARY KEY (city_id, scope, district_name, street_name),
    FOREIGN KEY (city_id) REFERENCES cities(city_id)
);

CREATE TABLE IF NOT EXISTS world_time_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    last_update_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sex_service_schedule_days (
    city_id TEXT NOT NULL,
    visit_date TEXT NOT NULL,
    planned_at TEXT NOT NULL,
    initial_cutoff_at TEXT,
    PRIMARY KEY (city_id, visit_date),
    FOREIGN KEY (city_id) REFERENCES cities(city_id)
);

CREATE TABLE IF NOT EXISTS sex_service_visit_schedule (
    visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL,
    city_id TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    visit_date TEXT,
    arrival_at TEXT,
    service_started_at TEXT,
    service_ended_at TEXT,
    waiting_minutes INTEGER,
    intention_score REAL,
    venue_commission_bps INTEGER,
    worker_earnings INTEGER,
    worker_paid_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('planned', 'elapsed')),
    free_access INTEGER NOT NULL CHECK (free_access IN (0, 1)),
    privilege_scope TEXT CHECK (
        privilege_scope IS NULL
        OR privilege_scope IN ('street', 'district', 'city')
    ),
    privilege_district_name TEXT,
    privilege_street_name TEXT,
    organization_id TEXT,
    worker_character_id TEXT,
    service_name TEXT,
    quoted_price INTEGER,
    customer_charge INTEGER,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE (character_id, scheduled_at),
    FOREIGN KEY (character_id) REFERENCES characters(character_id),
    FOREIGN KEY (city_id) REFERENCES cities(city_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id),
    FOREIGN KEY (worker_character_id) REFERENCES characters(character_id)
);

CREATE INDEX IF NOT EXISTS idx_sex_service_visit_schedule_due
    ON sex_service_visit_schedule(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sex_service_visit_schedule_character
    ON sex_service_visit_schedule(character_id, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sex_service_visit_schedule_venue_arrival
    ON sex_service_visit_schedule(organization_id, arrival_at);
CREATE INDEX IF NOT EXISTS idx_sex_service_visit_schedule_daily_capacity
    ON sex_service_visit_schedule(city_id, visit_date, organization_id);
"""


class _PersistentIdAllocator:
    def __init__(
        self,
        session: "CityWriteSession",
        kind: str,
        block_size: int,
    ) -> None:
        self.session = session
        self.kind = kind
        self.block_size = block_size
        self.available: list[str] = []

    def __call__(self) -> str:
        if not self.available:
            self.available = self.session._reserve_id_block(
                self.kind,
                self.block_size,
            )
        return self.available.pop(0)

    def claim(self, entity_id: object) -> str:
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("special_role.id 必须是非空字符串")
        self.session._claim_id(self.kind, entity_id)
        if entity_id in self.available:
            self.available.remove(entity_id)
        return entity_id


class SQLiteWorldStore:
    """Create SQLite-backed city generation sessions."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        character_flush_limit: int = 2_000,
        organization_flush_limit: int = 20,
        character_id_block_size: int = 1_000,
        organization_id_block_size: int = 100,
        global_id_registry_path: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.character_flush_limit = character_flush_limit
        self.organization_flush_limit = organization_flush_limit
        self.character_id_block_size = character_id_block_size
        self.organization_id_block_size = organization_id_block_size
        for field_name, value in (
            ("character_flush_limit", character_flush_limit),
            ("organization_flush_limit", organization_flush_limit),
            ("character_id_block_size", character_id_block_size),
            ("organization_id_block_size", organization_id_block_size),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} 必须是正整数")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(_SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO id_sequences(kind, next_value) VALUES (?, 1)",
                ((kind,) for kind in _ID_FORMATS),
            )
            connection.commit()
        finally:
            connection.close()
        registry_path = Path(global_id_registry_path or (
            self.database_path.parent / GLOBAL_ID_REGISTRY_FILENAME
        ))
        self.global_character_ids = GlobalCharacterIdRegistry(
            registry_path,
            world_database_directory=self.database_path.parent,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def begin_city_generation(
        self,
        *,
        city_template: str,
        seed: int,
        target_population: int,
    ) -> "CityWriteSession":
        connection = self._connect()
        try:
            city_ids = CityWriteSession._reserve_block_on_connection(
                connection,
                "city",
                1,
                owner_city_id=None,
            )
            city_id = city_ids[0]
            connection.execute(
                """
                INSERT INTO cities(
                    city_id, city_template, seed, target_population,
                    status, created_at
                ) VALUES (?, ?, ?, ?, 'generating', ?)
                """,
                (city_id, city_template, seed, target_population, _utc_now()),
            )
            connection.commit()
            return CityWriteSession(self, connection, city_id)
        except BaseException:
            connection.close()
            raise


class CityWriteSession:
    """Persist one city incrementally while keeping ID reservations durable."""

    def __init__(
        self,
        store: SQLiteWorldStore,
        connection: sqlite3.Connection,
        city_id: str,
    ) -> None:
        self.store = store
        self.connection = connection
        self.city_id = city_id
        self.character_ids = _PersistentIdAllocator(
            self,
            "character",
            store.character_id_block_size,
        )
        self.organization_ids = _PersistentIdAllocator(
            self,
            "organization",
            store.organization_id_block_size,
        )
        self._organizations: list[tuple[Any, ...]] = []
        self._characters: list[tuple[Any, ...]] = []
        self._assignments: list[tuple[Any, ...]] = []
        self._closed = False

    @staticmethod
    def _reserve_block_on_connection(
        connection: sqlite3.Connection,
        kind: str,
        count: int,
        *,
        owner_city_id: str | None,
    ) -> list[str]:
        if kind not in _ID_FORMATS:
            raise ValueError(f"未知 ID sequence kind“{kind}”")
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT next_value FROM id_sequences WHERE kind = ?",
                (kind,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"ID sequence“{kind}”不存在")
            start = row[0]
            connection.execute(
                "UPDATE id_sequences SET next_value = ? WHERE kind = ?",
                (start + count, kind),
            )
            ids = [_format_id(kind, number) for number in range(start, start + count)]
            reserved_at = _utc_now()
            connection.executemany(
                """
                INSERT INTO reserved_ids(kind, entity_id, owner_city_id, reserved_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (kind, entity_id, owner_city_id, reserved_at)
                    for entity_id in ids
                ),
            )
            connection.commit()
            return ids
        except Exception:
            connection.rollback()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("CityWriteSession 已关闭")

    def _reserve_id_block(self, kind: str, count: int) -> list[str]:
        self._ensure_open()
        if kind == "character":
            ids = self.store.global_character_ids.reserve(
                count,
                owner_database=self.store.database_path.name,
            )
            next_value = int(ids[-1].split("-", 1)[1]) + 1
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.executemany(
                    """
                    INSERT INTO reserved_ids(
                        kind, entity_id, owner_city_id, reserved_at
                    ) VALUES ('character', ?, ?, ?)
                    """,
                    (
                        (entity_id, self.city_id, _utc_now())
                        for entity_id in ids
                    ),
                )
                self.connection.execute(
                    """
                    UPDATE id_sequences
                    SET next_value = MAX(next_value, ?)
                    WHERE kind = 'character'
                    """,
                    (next_value,),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            return ids
        return self._reserve_block_on_connection(
            self.connection,
            kind,
            count,
            owner_city_id=self.city_id,
        )

    def _claim_id(self, kind: str, entity_id: str) -> None:
        self._ensure_open()
        if kind == "character":
            self.store.global_character_ids.claim(
                entity_id,
                owner_database=self.store.database_path.name,
            )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            table = "characters" if kind == "character" else "organizations"
            id_column = "character_id" if kind == "character" else "organization_id"
            if self.connection.execute(
                f"SELECT 1 FROM {table} WHERE {id_column} = ?",
                (entity_id,),
            ).fetchone():
                raise ValueError(f"ID“{entity_id}”已经存在")
            self.connection.execute(
                """
                INSERT INTO reserved_ids(kind, entity_id, owner_city_id, reserved_at)
                VALUES (?, ?, ?, ?)
                """,
                (kind, entity_id, self.city_id, _utc_now()),
            )
            prefix, _ = _ID_FORMATS[kind]
            prefix_text = f"{prefix}-"
            suffix = entity_id[len(prefix_text):] if entity_id.startswith(prefix_text) else ""
            if suffix.isdigit():
                number = int(suffix)
                self.connection.execute(
                    """
                    UPDATE id_sequences
                    SET next_value = MAX(next_value, ?)
                    WHERE kind = ?
                    """,
                    (number + 1, kind),
                )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            self.connection.rollback()
            raise ValueError(f"ID“{entity_id}”已经被永久占用") from error
        except Exception:
            self.connection.rollback()
            raise

    def write_map(self, districts: list[object], streets: list[object]) -> None:
        self._ensure_open()
        district_rows = []
        for district in districts:
            data = district.as_dict() if hasattr(district, "as_dict") else district
            district_rows.append((
                self.city_id,
                data["name"],
                data["template"],
                data["level"],
                data["population"],
                data["prosperity"],
                _json(data),
            ))
        street_rows = []
        for street in streets:
            data = street.as_dict() if hasattr(street, "as_dict") else street
            street_rows.append((
                self.city_id,
                data["name"],
                data["district"],
                data["prosperity"],
                _json(data),
            ))
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO districts(
                    city_id, name, template_name, level,
                    population, prosperity, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                district_rows,
            )
            self.connection.executemany(
                """
                INSERT INTO streets(
                    city_id, name, district_name, prosperity, data_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                street_rows,
            )

    def write_organization(
        self,
        organization: dict[str, object],
        *,
        declaration_key: str | None = None,
        instance_index: int | None = None,
    ) -> None:
        self._ensure_open()
        organization_data = {
            key: value
            for key, value in organization.items()
            if key not in {
                "characters",
                "sub_organization_assignments",
                "sub_organization_ids",
            }
        }
        self._organizations.append((
            organization["organization_id"],
            self.city_id,
            organization["template_name"],
            organization["scope"],
            organization["name"],
            organization["district"],
            organization["street"],
            organization["parent_organization_id"],
            organization["manager_id"],
            organization["root_organization_id"],
            organization["population_usage"],
            organization["special_role_character_id"],
            _json(organization_data),
        ))
        for character in organization["characters"]:
            relation = character.get("relation", {})
            self._characters.append((
                character["id"],
                self.city_id,
                organization["organization_id"],
                organization["name"],
                character["district"],
                character["street"],
                character["name"],
                character["occupation"],
                character["title"],
                relation.get("superior_id"),
                relation.get("service_to"),
                _json(character),
            ))
        parent_id = organization["parent_organization_id"]
        if parent_id is not None:
            if declaration_key is None or instance_index is None:
                raise ValueError("sub-organization 写入时缺少声明或实例索引")
            self._assignments.append((
                parent_id,
                organization["organization_id"],
                declaration_key,
                instance_index,
                organization["manager_id"],
                organization["district"],
                organization["street"],
            ))
        if (
            len(self._characters) >= self.store.character_flush_limit
            or len(self._organizations) >= self.store.organization_flush_limit
        ):
            self.flush()

    def flush(self) -> None:
        self._ensure_open()
        if not self._organizations and not self._characters and not self._assignments:
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.executemany(
                """
                INSERT INTO organizations(
                    organization_id, city_id, template_name, scope, name,
                    district_name, street_name, parent_organization_id,
                    manager_id, root_organization_id, population_usage,
                    special_role_character_id, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._organizations,
            )
            self.connection.executemany(
                """
                INSERT INTO characters(
                    character_id, city_id, organization_id, organization_name,
                    district_name, street_name, name, occupation, title,
                    superior_id, service_to, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._characters,
            )
            self.connection.executemany(
                """
                INSERT INTO organization_assignments(
                    parent_organization_id, child_organization_id,
                    declaration_key, instance_index, manager_id,
                    district_name, street_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                self._assignments,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self._organizations.clear()
        self._characters.clear()
        self._assignments.clear()

    def write_urban_layout(
        self,
        layout: object,
        organizations: list[dict[str, object]],
    ) -> None:
        """Persist generated roads, parcels, buildings, and Organization sites."""

        self._ensure_open()
        self.flush()
        roads = getattr(layout, "roads")
        parcels = getattr(layout, "parcels")
        buildings = getattr(layout, "buildings")
        spatial_fields = (
            "parcel_id",
            "building_id",
            "position",
            "parcel_area_m2",
            "building_footprint",
            "building_area_m2",
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.executemany(
                """
                INSERT INTO roads(
                    city_id, road_id, district_name, street_name,
                    level, width_m, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        self.city_id,
                        road["road_id"],
                        road["district"],
                        road["street"],
                        road["level"],
                        road["width_m"],
                        _json(road),
                    )
                    for road in roads
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO parcels(
                    city_id, parcel_id, district_name, street_name,
                    cell_q, cell_r, area_m2, organization_id, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        self.city_id,
                        parcel["parcel_id"],
                        parcel["district"],
                        parcel["street"],
                        parcel["cell"][0],
                        parcel["cell"][1],
                        parcel["area_m2"],
                        parcel["organization_id"],
                        _json(parcel),
                    )
                    for parcel in parcels
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO buildings(
                    city_id, building_id, parcel_id, district_name,
                    street_name, organization_id, footprint_area_m2,
                    data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        self.city_id,
                        building["building_id"],
                        building["parcel_id"],
                        building["district"],
                        building["street"],
                        building["organization_id"],
                        building["footprint_area_m2"],
                        _json(building),
                    )
                    for building in buildings
                ),
            )
            for organization in organizations:
                organization_id = str(organization["organization_id"])
                row = self.connection.execute(
                    "SELECT data_json FROM organizations WHERE organization_id = ?",
                    (organization_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        f"无法为不存在的 Organization“{organization_id}”写入建筑"
                    )
                payload = json.loads(row[0])
                payload.update({
                    field: organization[field]
                    for field in spatial_fields
                    if field in organization
                })
                self.connection.execute(
                    "UPDATE organizations SET data_json = ? WHERE organization_id = ?",
                    (_json(payload), organization_id),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def write_bus_system(self, bus_system: object) -> None:
        """Persist fixed stops and the route-free system-wide timetable."""

        self._ensure_open()
        stops = getattr(bus_system, "stops")
        report = getattr(bus_system, "report")
        start_minute = int(getattr(bus_system, "service_start_minute"))
        end_minute = int(getattr(bus_system, "service_end_minute"))
        frequency_minutes = int(getattr(bus_system, "frequency_minutes"))
        speed_kmh = float(getattr(bus_system, "speed_kmh"))
        stop_min_spacing_m = float(getattr(bus_system, "stop_min_spacing_m"))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                INSERT INTO bus_systems(
                    city_id, service_start_minute, service_end_minute,
                    frequency_minutes, speed_kmh, stop_min_spacing_m, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.city_id,
                    start_minute,
                    end_minute,
                    frequency_minutes,
                    speed_kmh,
                    stop_min_spacing_m,
                    _json({"report": report}),
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO bus_stops(
                    city_id, stop_id, name, district_name, street_name,
                    road_id, position_x, position_y, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        self.city_id,
                        stop["stop_id"],
                        stop["name"],
                        stop["district"],
                        stop["street"],
                        stop["road_id"],
                        stop["position"][0],
                        stop["position"][1],
                        _json(stop),
                    )
                    for stop in stops
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def prepare_completion(
        self,
        population_usage: dict[str, object],
        *,
        additional_data: dict[str, object] | None = None,
    ) -> None:
        """Flush generated data while leaving the city open for post-processing."""

        self._ensure_open()
        self.flush()
        rows = [(
            self.city_id,
            "city",
            "",
            "",
            population_usage["capacity"],
            population_usage["used"],
            population_usage["remaining"],
        )]
        rows.extend(
            (
                self.city_id,
                "district",
                district_name,
                "",
                values["capacity"],
                values["used"],
                values["remaining"],
            )
            for district_name, values in population_usage["districts"].items()
        )
        rows.extend(
            (
                self.city_id,
                "street",
                values["district"],
                street_name,
                values["capacity"],
                values["used"],
                values["remaining"],
            )
            for street_name, values in population_usage["streets"].items()
        )
        city_data = {"organization_population_usage": population_usage}
        if additional_data:
            city_data.update(additional_data)
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO population_usage(
                    city_id, scope, district_name, street_name,
                    capacity, used, remaining
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self.connection.execute(
                """
                UPDATE cities
                SET data_json = ?
                WHERE city_id = ?
                """,
                (_json(city_data), self.city_id),
            )

    def mark_complete(self) -> None:
        """Publish a fully generated and post-processed city."""

        self._ensure_open()
        with self.connection:
            self.connection.execute(
                """
                UPDATE cities
                SET status = 'complete', completed_at = ?, error = NULL
                WHERE city_id = ?
                """,
                (_utc_now(), self.city_id),
            )
        self._closed = True
        self.connection.close()

    def complete(
        self,
        population_usage: dict[str, object],
        *,
        additional_data: dict[str, object] | None = None,
    ) -> None:
        self.prepare_completion(
            population_usage,
            additional_data=additional_data,
        )
        self.mark_complete()

    def fail(self, error: BaseException) -> None:
        if self._closed:
            return
        self.connection.rollback()
        self._organizations.clear()
        self._characters.clear()
        self._assignments.clear()
        with self.connection:
            self.connection.execute(
                """
                UPDATE cities
                SET status = 'failed', completed_at = ?, error = ?
                WHERE city_id = ?
                """,
                (_utc_now(), str(error), self.city_id),
            )
        self._closed = True
        self.connection.close()
