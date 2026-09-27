"""Advance persistent world time and run time-based maintenance tasks."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import config
from ..generators.character_generator import SEX_SERVICE_PROFESSIONS
from .loan_service import ensure_loan_schema, settle_due_player_loans
from .sex_service import (
    SexServiceVenueIndex,
    SexServicePrivilege,
    build_sex_service_venue_summary,
    choose_player_organization_appointment,
    plan_monthly_sex_service_visit_dates,
    select_sex_service_venue,
    sex_service_crime_organization_depths,
    sex_service_crime_privilege_scope,
    sex_service_visit_arrival_time,
)


DEFAULT_TIMEZONE = "Asia/Shanghai"
_INSERT_BATCH_SIZE = 5_000

_TIME_SCHEMA = """
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
"""


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _month_range(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> Iterable[tuple[int, int]]:
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        year, month = _next_month(year, month)


def _salary_boundaries(
    previous: datetime,
    current: datetime,
    timezone: ZoneInfo,
) -> tuple[datetime, ...]:
    previous_local = previous.astimezone(timezone)
    current_local = current.astimezone(timezone)
    year, month = previous_local.year, previous_local.month
    boundaries: list[datetime] = []
    while (year, month) <= (current_local.year, current_local.month):
        boundary = datetime(year, month, 1, tzinfo=timezone)
        if previous_local < boundary <= current_local:
            boundaries.append(boundary)
        year, month = _next_month(year, month)
    return tuple(boundaries)


def _pay_monthly_salaries(connection: sqlite3.Connection, payment_count: int) -> None:
    if payment_count <= 0:
        return
    excluded = tuple(sorted(SEX_SERVICE_PROFESSIONS))
    placeholders = ",".join("?" for _ in excluded)
    connection.execute(
        f"""
        UPDATE characters
        SET data_json = json_set(
            data_json,
            '$.cash',
            CAST(COALESCE(json_extract(data_json, '$.cash'), 0) AS INTEGER)
            + CAST(COALESCE(json_extract(data_json, '$.income'), 0) AS INTEGER) * ?
        )
        WHERE occupation NOT IN ({placeholders})
        """,
        (payment_count, *excluded),
    )


def _settle_sex_worker_earnings(
    connection: sqlite3.Connection,
    boundary: datetime,
) -> None:
    """Pay completed, unsettled player-venue commissions on a month first."""

    boundary_iso = boundary.astimezone(ZoneInfo("UTC")).isoformat(
        timespec="seconds"
    )
    earnings = connection.execute(
        """
        SELECT worker_character_id, SUM(worker_earnings)
        FROM sex_service_visit_schedule
        WHERE worker_character_id IS NOT NULL
          AND worker_earnings IS NOT NULL
          AND worker_paid_at IS NULL
          AND service_ended_at IS NOT NULL
          AND service_ended_at <= ?
        GROUP BY worker_character_id
        """,
        (boundary_iso,),
    ).fetchall()
    for worker_character_id, total_earnings in earnings:
        connection.execute(
            """
            UPDATE characters
            SET data_json = json_set(
                data_json,
                '$.cash',
                CAST(COALESCE(json_extract(data_json, '$.cash'), 0) AS INTEGER)
                    + ?,
                '$.sex_service_earnings_total',
                CAST(COALESCE(
                    json_extract(data_json, '$.sex_service_earnings_total'),
                    0
                ) AS INTEGER) + ?
            )
            WHERE character_id = ?
            """,
            (
                int(total_earnings or 0),
                int(total_earnings or 0),
                str(worker_character_id),
            ),
        )
    connection.execute(
        """
        UPDATE sex_service_visit_schedule
        SET worker_paid_at = ?
        WHERE worker_character_id IS NOT NULL
          AND worker_earnings IS NOT NULL
          AND worker_paid_at IS NULL
          AND service_ended_at IS NOT NULL
          AND service_ended_at <= ?
        """,
        (boundary_iso, boundary_iso),
    )


def _ensure_time_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_TIME_SCHEMA)
    ensure_loan_schema(connection)
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(sex_service_visit_schedule)")
    }
    additions = {
        "privilege_scope": "TEXT",
        "privilege_district_name": "TEXT",
        "privilege_street_name": "TEXT",
        "visit_date": "TEXT",
        "arrival_at": "TEXT",
        "service_started_at": "TEXT",
        "service_ended_at": "TEXT",
        "waiting_minutes": "INTEGER",
        "intention_score": "REAL",
        "venue_commission_bps": "INTEGER",
        "worker_earnings": "INTEGER",
        "worker_paid_at": "TEXT",
    }
    for column, definition in additions.items():
        if column not in columns:
            connection.execute(
                f"ALTER TABLE sex_service_visit_schedule "
                f"ADD COLUMN {column} {definition}"
            )
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_sex_service_visit_schedule_venue_arrival
            ON sex_service_visit_schedule(organization_id, arrival_at);
        CREATE INDEX IF NOT EXISTS idx_sex_service_visit_schedule_daily_capacity
            ON sex_service_visit_schedule(city_id, visit_date, organization_id);
        """
    )


def _crime_organization_contexts(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            organization_id,
            city_id,
            template_name,
            parent_organization_id,
            district_name,
            street_name
        FROM organizations
        """
    ).fetchall()
    organizations = [
        {
            "organization_id": str(organization_id),
            "city_id": str(city_id),
            "template_name": str(template_name),
            "parent_organization_id": (
                str(parent_id) if parent_id is not None else None
            ),
            "district_name": str(district_name),
            "street_name": str(street_name),
        }
        for (
            organization_id,
            city_id,
            template_name,
            parent_id,
            district_name,
            street_name,
        ) in rows
    ]
    depths = sex_service_crime_organization_depths(organizations)
    return {
        str(organization["organization_id"]): {
            **organization,
            "organization_depth": depths[str(organization["organization_id"])],
        }
        for organization in organizations
        if str(organization["organization_id"]) in depths
    }


def _customer_privilege(
    customer: dict[str, object],
    contexts: dict[str, dict[str, object]],
) -> SexServicePrivilege | None:
    context = contexts.get(str(customer["organization_id"]))
    role_key = customer.get("organization_role")
    if context is None or not isinstance(role_key, str) or not role_key:
        return None
    scope = sex_service_crime_privilege_scope(
        str(context["template_name"]),
        int(context["organization_depth"]),
        role_key,
    )
    if scope is None:
        return None
    return SexServicePrivilege(
        scope=scope,
        city_id=str(context["city_id"]),
        district=str(context["district_name"]),
        street=str(context["street_name"]),
    )


def _eligible_customers(
    connection: sqlite3.Connection,
) -> Iterable[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            character.character_id,
            character.city_id,
            character.organization_id,
            character.occupation,
            city.seed,
            json_extract(character.data_json, '$.age'),
            json_extract(character.data_json, '$.cash'),
            json_extract(character.data_json, '$.sexual_preferences.desire'),
            json_extract(character.data_json, '$.hobbies'),
            json_extract(character.data_json, '$.attraction_preferences'),
            json_extract(character.data_json, '$.organization_role'),
            json_extract(character.data_json, '$.address')
        FROM characters AS character
        JOIN cities AS city ON city.city_id = character.city_id
        WHERE json_extract(character.data_json, '$.sex') = 'male'
          AND CAST(json_extract(character.data_json, '$.age') AS INTEGER) >= 18
        """
    ).fetchall()
    for (
        character_id,
        city_id,
        organization_id,
        occupation,
        city_seed,
        age,
        cash,
        desire,
        hobbies_json,
        attraction_preferences_json,
        organization_role,
        address_json,
    ) in rows:
        hobbies = json.loads(hobbies_json) if isinstance(hobbies_json, str) else []
        attraction_preferences = (
            json.loads(attraction_preferences_json)
            if isinstance(attraction_preferences_json, str)
            else None
        )
        address = json.loads(address_json) if isinstance(address_json, str) else None
        yield {
            "id": str(character_id),
            "city_id": str(city_id),
            "organization_id": str(organization_id),
            "occupation": str(occupation),
            "seed": int(city_seed),
            "sex": "male",
            "age": int(age),
            "cash": float(cash or 0),
            "sexual_preferences": {"desire": str(desire)},
            "hobbies": hobbies,
            "attraction_preferences": attraction_preferences,
            "address": address,
            "organization_role": (
                str(organization_role) if organization_role is not None else None
            ),
        }


def _sex_service_venue_indices(
    connection: sqlite3.Connection,
    *,
    player_organization_id: str | None,
) -> dict[str, SexServiceVenueIndex]:
    organization_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(organizations)")
    }
    if "data_json" not in organization_columns:
        return {}
    placeholders = ",".join("?" for _ in SEX_SERVICE_PROFESSIONS)
    worker_rows = connection.execute(
        f"""
        SELECT character_id, city_id, organization_id, occupation, data_json
        FROM characters
        WHERE occupation IN ({placeholders})
        """,
        tuple(sorted(SEX_SERVICE_PROFESSIONS)),
    ).fetchall()
    workers_by_organization: dict[str, list[dict[str, object]]] = {}
    for character_id, city_id, organization_id, occupation, data_json in worker_rows:
        payload = json.loads(data_json)
        payload["id"] = str(character_id)
        payload["occupation"] = str(occupation)
        if payload.get("sex_worker_level") is None:
            raise RuntimeError(
                f"性工作者 {character_id} 缺少生成期 sex_worker_level；"
                "该世界数据库需要用当前生成管线重新生成"
            )
        workers_by_organization.setdefault(str(organization_id), []).append(payload)

    venue_types = tuple(config.SEX_SERVICE_VENUE_OPEN_PERIODS)
    type_placeholders = ",".join("?" for _ in venue_types)
    organization_rows = connection.execute(
        f"""
        SELECT
            organization_id,
            city_id,
            template_name,
            district_name,
            street_name,
            data_json
        FROM organizations
        WHERE template_name IN ({type_placeholders})
        """,
        venue_types,
    ).fetchall()
    summaries_by_city: dict[str, list[dict[str, object]]] = {}
    for (
        organization_id,
        city_id,
        template_name,
        district_name,
        street_name,
        data_json,
    ) in organization_rows:
        organization_id = str(organization_id)
        workers = workers_by_organization.get(organization_id, [])
        if not workers:
            continue
        payload = json.loads(data_json)
        if payload.get("address") is None:
            raise RuntimeError(
                f"性服务场所 {organization_id} 缺少生成期固定 address；"
                "该世界数据库需要用当前生成器重新生成，运行期不会补写坐标"
            )
        venue = {
            "organization_id": organization_id,
            "city_id": str(city_id),
            "template_name": str(template_name),
            "district": str(district_name),
            "street": str(street_name),
            "address": payload.get("address"),
        }
        summary = build_sex_service_venue_summary(
            venue,
            workers,
        )
        summaries_by_city.setdefault(str(city_id), []).append(summary)
    return {
        city_id: SexServiceVenueIndex(summaries)
        for city_id, summaries in summaries_by_city.items()
        if summaries
    }


def _visit_familiarity(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for character_id, organization_id, visits in connection.execute(
        """
        SELECT character_id, organization_id, COUNT(*)
        FROM sex_service_visit_schedule
        WHERE status = 'elapsed' AND organization_id IS NOT NULL
        GROUP BY character_id, organization_id
        """
    ):
        result.setdefault(str(character_id), {})[str(organization_id)] = int(visits)
    return result


def _scheduled_daily_counts(
    connection: sqlite3.Connection,
    timezone: ZoneInfo,
) -> dict[tuple[str, str], dict[str, int]]:
    result: dict[tuple[str, str], dict[str, int]] = {}
    for city_id, organization_id, visit_date, scheduled_at in connection.execute(
        """
        SELECT city_id, organization_id, visit_date, scheduled_at
        FROM sex_service_visit_schedule
        WHERE organization_id IS NOT NULL
        """
    ):
        local_day = (
            str(visit_date)
            if visit_date is not None
            else datetime.fromisoformat(str(scheduled_at)).astimezone(
                timezone
            ).date().isoformat()
        )
        counts = result.setdefault((str(city_id), local_day), {})
        venue_id = str(organization_id)
        counts[venue_id] = counts.get(venue_id, 0) + 1
    return result


def _date_range(first: date, last: date) -> Iterable[date]:
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


def _missing_schedule_days(
    connection: sqlite3.Connection,
    city_ids: Iterable[str],
    previous: datetime | None,
    current: datetime,
    timezone: ZoneInfo,
) -> dict[str, tuple[date, ...]]:
    current_local = current.astimezone(timezone)
    first_date = (
        current_local.date() + timedelta(days=1)
        if previous is None
        else previous.astimezone(timezone).date() + timedelta(days=1)
    )
    last_date = current_local.date() + timedelta(
        days=config.SEX_SERVICE_PLANNING_HORIZON_DAYS
    )

    existing = {
        (str(city_id), date.fromisoformat(str(visit_date)))
        for city_id, visit_date in connection.execute(
            "SELECT city_id, visit_date FROM sex_service_schedule_days"
        )
    }
    result: dict[str, tuple[date, ...]] = {}
    for city_id in city_ids:
        result[city_id] = tuple(
            day
            for day in _date_range(first_date, last_date)
            if (city_id, day) not in existing
        )
    return result


def _insert_visit_batch(
    connection: sqlite3.Connection,
    rows: list[tuple[object, ...]],
) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT OR IGNORE INTO sex_service_visit_schedule(
            character_id,
            city_id,
            scheduled_at,
            visit_date,
            arrival_at,
            service_started_at,
            service_ended_at,
            waiting_minutes,
            intention_score,
            venue_commission_bps,
            worker_earnings,
            worker_paid_at,
            status,
            free_access,
            privilege_scope,
            privilege_district_name,
            privilege_street_name,
            organization_id,
            worker_character_id,
            service_name,
            quoted_price,
            customer_charge,
            created_at,
            processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    rows.clear()


def _schedule_player_organization_visits(
    connection: sqlite3.Connection,
    player_organization_id: str,
    timezone: ZoneInfo,
) -> None:
    """Expand coarse player-venue visits into continuous worker queues."""

    pending = connection.execute(
        """
        SELECT
            visit.visit_id,
            visit.character_id,
            visit.arrival_at,
            character.data_json,
            city.seed,
            organization.template_name,
            visit.free_access
        FROM sex_service_visit_schedule AS visit
        JOIN characters AS character
          ON character.character_id = visit.character_id
        JOIN cities AS city ON city.city_id = visit.city_id
        JOIN organizations AS organization
          ON organization.organization_id = visit.organization_id
        WHERE visit.organization_id = ?
          AND visit.arrival_at IS NOT NULL
          AND visit.worker_character_id IS NULL
        ORDER BY visit.arrival_at, visit.character_id, visit.visit_id
        """,
        (player_organization_id,),
    ).fetchall()
    if not pending:
        return

    placeholders = ",".join("?" for _ in SEX_SERVICE_PROFESSIONS)
    worker_rows = connection.execute(
        f"""
        SELECT character_id, occupation, data_json
        FROM characters
        WHERE organization_id = ?
          AND occupation IN ({placeholders})
        ORDER BY character_id
        """,
        (player_organization_id, *sorted(SEX_SERVICE_PROFESSIONS)),
    ).fetchall()
    workers: list[dict[str, object]] = []
    for character_id, occupation, data_json in worker_rows:
        worker = json.loads(data_json)
        worker["id"] = str(character_id)
        worker["occupation"] = str(occupation)
        workers.append(worker)
    if not workers:
        raise RuntimeError("玩家性服务组织没有可排班的性工作者")

    worker_available_at: dict[str, datetime] = {}
    for worker_id, service_ended_at in connection.execute(
        """
        SELECT worker_character_id, MAX(service_ended_at)
        FROM sex_service_visit_schedule
        WHERE organization_id = ?
          AND worker_character_id IS NOT NULL
          AND service_ended_at IS NOT NULL
        GROUP BY worker_character_id
        """,
        (player_organization_id,),
    ):
        worker_available_at[str(worker_id)] = datetime.fromisoformat(
            str(service_ended_at)
        ).astimezone(timezone)

    for (
        visit_id,
        character_id,
        arrival_text,
        customer_json,
        city_seed,
        venue_type,
        free_access,
    ) in pending:
        customer = json.loads(customer_json)
        customer["id"] = str(character_id)
        arrival_at = datetime.fromisoformat(str(arrival_text)).astimezone(timezone)
        appointment = choose_player_organization_appointment(
            customer,
            workers,
            arrival_at,
            worker_available_at,
            venue_type=str(venue_type),
            free_access=bool(free_access),
            seed=int(city_seed),
        )
        connection.execute(
            """
            UPDATE sex_service_visit_schedule
            SET worker_character_id = ?,
                service_name = ?,
                service_started_at = ?,
                service_ended_at = ?,
                waiting_minutes = ?,
                intention_score = ?,
                quoted_price = ?,
                customer_charge = ?,
                venue_commission_bps = ?,
                worker_earnings = ?
            WHERE visit_id = ?
            """,
            (
                appointment.worker_character_id,
                appointment.service_name,
                appointment.service_start_at.astimezone(
                    ZoneInfo("UTC")
                ).isoformat(timespec="seconds"),
                appointment.service_end_at.astimezone(
                    ZoneInfo("UTC")
                ).isoformat(timespec="seconds"),
                appointment.waiting_minutes,
                appointment.intention_score,
                appointment.quoted_price,
                appointment.customer_charge,
                appointment.venue_commission_bps,
                appointment.worker_earnings,
                int(visit_id),
            ),
        )
        worker_available_at[appointment.worker_character_id] = (
            appointment.service_end_at
        )


def _prepare_player_organization_arrivals(
    connection: sqlite3.Connection,
    player_organization_id: str,
    timezone: ZoneInfo,
) -> None:
    """Add exact arrivals to already-planned visits after player switching."""

    rows = connection.execute(
        """
        SELECT
            visit.visit_id,
            visit.visit_date,
            visit.character_id,
            character.occupation,
            character.data_json,
            city.seed
        FROM sex_service_visit_schedule AS visit
        JOIN characters AS character
          ON character.character_id = visit.character_id
        JOIN cities AS city ON city.city_id = visit.city_id
        WHERE visit.organization_id = ?
          AND visit.status = 'planned'
          AND visit.visit_date IS NOT NULL
          AND visit.arrival_at IS NULL
        ORDER BY visit.visit_date, visit.character_id, visit.visit_id
        """,
        (player_organization_id,),
    ).fetchall()
    for visit_id, visit_date, character_id, occupation, data_json, city_seed in rows:
        customer = json.loads(data_json)
        customer["id"] = str(character_id)
        customer["occupation"] = str(occupation)
        arrival_at = sex_service_visit_arrival_time(
            customer,
            date.fromisoformat(str(visit_date)),
            seed=int(city_seed),
            timezone_name=timezone.key,
            occurrence=int(visit_id),
        ).astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")
        connection.execute(
            """
            UPDATE sex_service_visit_schedule
            SET arrival_at = ?, scheduled_at = ?
            WHERE visit_id = ?
            """,
            (arrival_at, arrival_at, int(visit_id)),
        )


def _plan_missing_days(
    connection: sqlite3.Connection,
    previous: datetime | None,
    current: datetime,
    timezone: ZoneInfo,
    *,
    player_organization_id: str | None,
) -> None:
    city_ids = {
        str(row[0])
        for row in connection.execute("SELECT city_id FROM cities")
    }
    missing_by_city = _missing_schedule_days(
        connection,
        city_ids,
        previous,
        current,
        timezone,
    )
    if not any(missing_by_city.values()):
        return

    crime_contexts = _crime_organization_contexts(connection)
    venue_indices = _sex_service_venue_indices(
        connection,
        player_organization_id=player_organization_id,
    )
    familiarity_by_customer = _visit_familiarity(connection)
    daily_counts = _scheduled_daily_counts(connection, timezone)
    current_utc = current.astimezone(ZoneInfo("UTC"))
    current_iso = current_utc.isoformat(timespec="seconds")
    batch: list[tuple[object, ...]] = []

    for customer in _eligible_customers(connection):
        city_id = str(customer["city_id"])
        privilege = _customer_privilege(customer, crime_contexts)
        missing_dates = set(missing_by_city.get(city_id, ()))
        missing_months = sorted({
            (missing_date.year, missing_date.month)
            for missing_date in missing_dates
        })
        for year, month in missing_months:
            visit_dates = plan_monthly_sex_service_visit_dates(
                customer,
                year,
                month,
                privilege_scope=(privilege.scope if privilege is not None else None),
                seed=int(customer["seed"]),
            )
            for occurrence, visit_date in enumerate(visit_dates):
                if visit_date not in missing_dates:
                    continue
                local_day = visit_date.isoformat()
                day_end = datetime.combine(
                    visit_date,
                    time(23, 59, 59),
                    timezone,
                )
                selection = None
                venue_index = venue_indices.get(city_id)
                if venue_index is not None and customer.get("address") is not None:
                    counts = daily_counts.setdefault((city_id, local_day), {})
                    familiarity = familiarity_by_customer.setdefault(
                        str(customer["id"]),
                        {},
                    )
                    selection = select_sex_service_venue(
                        customer,
                        venue_index,
                        privilege=privilege,
                        familiarity=familiarity,
                        player_organization_id=player_organization_id,
                        daily_visit_counts=counts,
                        seed=int(customer["seed"]),
                        visit_key=f"{local_day}:{occurrence}",
                    )
                    if selection is not None:
                        counts[selection.organization_id] = (
                            counts.get(selection.organization_id, 0) + 1
                        )
                        familiarity[selection.organization_id] = (
                            familiarity.get(selection.organization_id, 0) + 1
                        )
                arrival_at = None
                due_at = day_end
                if (
                    selection is not None
                    and selection.organization_id == player_organization_id
                ):
                    arrival_at = sex_service_visit_arrival_time(
                        customer,
                        visit_date,
                        seed=int(customer["seed"]),
                        timezone_name=timezone.key,
                        occurrence=occurrence,
                    )
                    due_at = arrival_at
                due_utc = due_at.astimezone(ZoneInfo("UTC"))
                arrival_utc = (
                    arrival_at.astimezone(ZoneInfo("UTC")).isoformat(
                        timespec="seconds"
                    )
                    if arrival_at is not None
                    else None
                )
                batch.append((
                    customer["id"],
                    city_id,
                    due_utc.isoformat(timespec="seconds"),
                    local_day,
                    arrival_utc,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "planned",
                    int(selection.free_access) if selection is not None else 0,
                    privilege.scope if privilege is not None else None,
                    privilege.district if privilege is not None else None,
                    privilege.street if privilege is not None else None,
                    selection.organization_id if selection is not None else None,
                    None,
                    None,
                    selection.quoted_price if selection is not None else None,
                    selection.customer_charge if selection is not None else None,
                    current_iso,
                    None,
                ))
                if len(batch) >= _INSERT_BATCH_SIZE:
                    _insert_visit_batch(connection, batch)
    _insert_visit_batch(connection, batch)
    for city_id, missing_dates in missing_by_city.items():
        for missing_date in missing_dates:
            connection.execute(
                """
                INSERT INTO sex_service_schedule_days(
                    city_id, visit_date, planned_at, initial_cutoff_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    city_id,
                    missing_date.isoformat(),
                    current_iso,
                    current_iso if previous is None else None,
                ),
            )


def _process_due_visits(
    connection: sqlite3.Connection,
    invocation_iso: str,
) -> None:
    charges = connection.execute(
        """
        SELECT character_id, SUM(customer_charge)
        FROM sex_service_visit_schedule
        WHERE status = 'planned'
          AND scheduled_at <= ?
          AND organization_id IS NOT NULL
          AND customer_charge IS NOT NULL
        GROUP BY character_id
        """,
        (invocation_iso,),
    ).fetchall()
    for character_id, total_charge in charges:
        connection.execute(
            """
            UPDATE characters
            SET data_json = json_set(
                data_json,
                '$.cash',
                MAX(
                    0,
                    CAST(COALESCE(json_extract(data_json, '$.cash'), 0) AS INTEGER)
                    - ?
                )
            )
            WHERE character_id = ?
            """,
            (int(total_charge or 0), str(character_id)),
        )

    connection.execute(
        """
        UPDATE sex_service_visit_schedule
        SET status = 'elapsed', processed_at = ?
        WHERE status = 'planned' AND scheduled_at <= ?
        """,
        (invocation_iso, invocation_iso),
    )


def update_time(
    database_path: str | Path,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    player_organization_id: str | None = None,
) -> bool:
    """Advance one world database to the invocation-start timestamp.

    The first call initializes the timestamp without back-paying salaries.
    Later calls pay one salary for every local-calendar first day crossed,
    excluding sex workers, and settle their completed service earnings on the
    same boundaries. Past visit plans are marked elapsed and every missing
    date through the configured three-day future horizon is planned once.
    """

    timezone = ZoneInfo(timezone_name)
    invocation_time = now or datetime.now(timezone)
    if invocation_time.tzinfo is None or invocation_time.utcoffset() is None:
        raise ValueError("now 必须是带时区的 datetime")
    invocation_time = invocation_time.astimezone(timezone)
    invocation_utc = invocation_time.astimezone(ZoneInfo("UTC"))
    invocation_iso = invocation_utc.isoformat(timespec="seconds")

    connection = sqlite3.connect(Path(database_path), timeout=60.0)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _ensure_time_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT last_update_at
            FROM world_time_state
            WHERE singleton_id = 1
            """
        ).fetchone()
        previous = datetime.fromisoformat(row[0]) if row is not None else None
        if previous is not None and invocation_utc < previous:
            raise ValueError("本次时间戳早于上次更新时间，拒绝倒退世界时间")

        if previous is not None:
            payment_boundaries = _salary_boundaries(
                previous,
                invocation_utc,
                timezone,
            )
            for boundary in payment_boundaries:
                # A loan already due before this salary boundary must be judged
                # against the cash available on its due date, not money earned
                # later while the player was offline.
                settle_due_player_loans(
                    connection,
                    boundary - timedelta(microseconds=1),
                )
                _pay_monthly_salaries(connection, 1)
                _settle_sex_worker_earnings(connection, boundary)

        settle_due_player_loans(connection, invocation_utc)

        _plan_missing_days(
            connection,
            previous,
            invocation_utc,
            timezone,
            player_organization_id=player_organization_id,
        )
        if player_organization_id is not None:
            _prepare_player_organization_arrivals(
                connection,
                player_organization_id,
                timezone,
            )
            _schedule_player_organization_visits(
                connection,
                player_organization_id,
                timezone,
            )
        _process_due_visits(connection, invocation_iso)
        connection.execute(
            """
            INSERT INTO world_time_state(singleton_id, last_update_at)
            VALUES (1, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                last_update_at = excluded.last_update_at
            """,
            (invocation_iso,),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = ("DEFAULT_TIMEZONE", "update_time")
