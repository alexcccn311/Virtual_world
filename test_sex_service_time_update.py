from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from world_generation import config
from world_generation.generators import character_generator
from world_generation.services.sex_service import (
    SEX_SERVICE_DURATIONS_MINUTES,
    SEX_SERVICE_WAITING_PENALTY_PER_MINUTE,
    SexServiceVenueIndex,
    SexServicePrivilege,
    build_sex_service_venue_summary,
    choose_player_organization_appointment,
    monthly_sex_service_visit_frequency,
    plan_monthly_sex_service_visits,
    plan_monthly_sex_service_visit_dates,
    quote_sex_service_price,
    select_sex_service_venue,
    sex_service_cell_distance,
    sex_service_crime_organization_depths,
    sex_service_crime_privilege_scope,
    sex_service_daily_capacity,
    sex_service_intention_score,
    sex_service_price_burden_penalty,
    sex_service_privilege_covers_venue,
    sex_service_venue_selection_weight,
    sex_service_venue_type_weights,
    sex_service_visit_time_weights,
    sex_service_worker_earnings,
    simplified_sex_service_charge,
)
from world_generation.services.time_update import update_time


def _customer(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "id": "CUS-TEST",
        "sex": "male",
        "age": 30,
        "occupation": "建筑工人",
        "cash": 2_000_000,
        "income": 1,
        "debt": 999_999_999,
        "other_assets": 0,
        "sexual_preferences": {"desire": "性欲平淡"},
        "hobbies": [],
        "address": [0, 0],
    }
    result.update(overrides)
    return result


def _venue(index: int, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "organization_id": f"VENUE-{index}",
        "city_id": "CITY-1",
        "template_name": "ordinary_brothel",
        "district": "北区",
        "street": "槐树街",
        "address": [index, 0],
        "min_price": 100,
        "max_price": 1_000,
        "appeal_score": 50.0,
        "sex_worker_count": 1,
        "daily_visit_count": 0,
    }
    result.update(overrides)
    return result


def _attraction_preferences(**service_overrides: float) -> dict[str, object]:
    services = {service: 1.0 for service in config.SEX_SERVICE_BASE_PRICES}
    first_service = next(iter(services))
    services[first_service] += 100.0 - sum(services.values())
    services.update(service_overrides)
    difference = 100.0 - sum(services.values())
    services[first_service] += difference
    return {
        "breasts": "偏爱大胸",
        "style": character_generator.FEMALE_TEMPERAMENT_OPTIONS[0],
        "sexual_service_weights": services,
    }


class PlayerOrganizationAppointmentTests(unittest.TestCase):
    def _worker(self, worker_id: str, appearance: float) -> dict[str, object]:
        return {
            "id": worker_id,
            "occupation": "发廊妹",
            "appearance_score": appearance,
            "sex_worker_level": 5,
            "skills": {"打飞机": "熟练"},
            "hygiene": "卫生良好",
            "cup_size": "C",
            "temperament": character_generator.FEMALE_TEMPERAMENT_OPTIONS[0],
        }

    def test_waiting_penalty_is_exactly_point_four_per_minute(self) -> None:
        customer = _customer(
            attraction_preferences=_attraction_preferences()
        )
        worker = self._worker("WORKER-1", 70.0)
        immediate = sex_service_intention_score(
            customer, worker, "手冲服务", waiting_minutes=0
        )
        delayed = sex_service_intention_score(
            customer, worker, "手冲服务", waiting_minutes=37
        )
        self.assertEqual(SEX_SERVICE_WAITING_PENALTY_PER_MINUTE, 0.4)
        self.assertAlmostEqual(immediate - delayed, 37 * 0.4)

    def test_price_penalty_uses_cash_and_unaffordable_services_are_rejected(self) -> None:
        self.assertAlmostEqual(
            sex_service_price_burden_penalty(10_000, 1_000),
            round(80 * 0.1 ** 1.25, 4),
        )
        self.assertLess(
            sex_service_price_burden_penalty(100_000, 1_000),
            sex_service_price_burden_penalty(10_000, 1_000),
        )
        self.assertEqual(
            sex_service_price_burden_penalty(
                0, 10_000, free_access=True
            ),
            0,
        )
        with self.assertRaisesRegex(ValueError, "无法支付"):
            sex_service_price_burden_penalty(999, 1_000)

    def test_actual_quote_and_worker_share_are_attached_to_appointment(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        arrival = datetime(2026, 10, 6, 20, 0, tzinfo=timezone)
        customer = _customer(
            cash=10_000,
            attraction_preferences=_attraction_preferences(),
        )
        worker = self._worker("WORKER-1", 70.0)
        appointment = choose_player_organization_appointment(
            customer,
            [worker],
            arrival,
            {},
            venue_type="ordinary_brothel",
        )
        expected_price = quote_sex_service_price(
            "手冲服务", "ordinary_brothel", 5, "发廊妹"
        )
        self.assertEqual(appointment.quoted_price, expected_price)
        self.assertEqual(appointment.customer_charge, expected_price)
        self.assertEqual(appointment.venue_commission_bps, 4_000)
        self.assertEqual(
            appointment.worker_earnings,
            sex_service_worker_earnings(
                expected_price, "ordinary_brothel", "发廊妹"
            ),
        )
        self.assertEqual(appointment.worker_earnings, 96)

    def test_existing_reservations_extend_one_continuous_queue(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        arrival = datetime(2026, 10, 6, 20, 0, tzinfo=timezone)
        customer = _customer(
            attraction_preferences=_attraction_preferences()
        )
        worker = self._worker("WORKER-1", 70.0)
        first = choose_player_organization_appointment(
            customer,
            [worker],
            arrival,
            {"WORKER-1": arrival + timedelta(minutes=25)},
            venue_type="ordinary_brothel",
        )
        second_arrival = arrival + timedelta(minutes=5)
        second = choose_player_organization_appointment(
            customer,
            [worker],
            second_arrival,
            {"WORKER-1": first.service_end_at},
            venue_type="ordinary_brothel",
        )
        self.assertEqual(first.service_start_at, arrival + timedelta(minutes=25))
        self.assertEqual(first.waiting_minutes, 25)
        self.assertEqual(second.service_start_at, first.service_end_at)
        self.assertEqual(
            second.waiting_minutes,
            int((first.service_end_at - second_arrival).total_seconds() // 60),
        )

    def test_wait_can_shift_choice_to_a_less_attractive_idle_worker(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        arrival = datetime(2026, 10, 6, 20, 0, tzinfo=timezone)
        customer = _customer(
            attraction_preferences=_attraction_preferences()
        )
        attractive = self._worker("WORKER-A", 90.0)
        idle = self._worker("WORKER-B", 75.0)
        appointment = choose_player_organization_appointment(
            customer,
            [attractive, idle],
            arrival,
            {"WORKER-A": arrival + timedelta(minutes=60)},
            venue_type="ordinary_brothel",
        )
        self.assertEqual(appointment.worker_character_id, "WORKER-B")
        self.assertEqual(appointment.waiting_minutes, 0)


class VenueSelectionTests(unittest.TestCase):
    def test_duration_table_covers_every_service(self) -> None:
        self.assertEqual(
            set(SEX_SERVICE_DURATIONS_MINUTES),
            set(config.SEX_SERVICE_BASE_PRICES),
        )
        self.assertEqual(SEX_SERVICE_DURATIONS_MINUTES["普通性交"], 45)
        self.assertEqual(SEX_SERVICE_DURATIONS_MINUTES["包夜性交"], 360)

    def test_cash_tiers_shift_weight_without_price_maximization(self) -> None:
        low = sex_service_venue_type_weights(1_000)
        middle = sex_service_venue_type_weights(7_500)
        high = sex_service_venue_type_weights(1_000_000)
        self.assertGreater(
            low["street_prostitution_ring"],
            low["adult_club"],
        )
        self.assertEqual(
            max(middle, key=middle.__getitem__),
            "ordinary_brothel",
        )
        self.assertGreater(high["adult_club"], high["street_prostitution_ring"])
        expected_types = set(config.SEX_SERVICE_VENUE_OPEN_PERIODS)
        self.assertTrue(all(
            set(tier) == expected_types
            for tier in config.SEX_SERVICE_VENUE_TYPE_WEIGHTS_BY_CASH_TIER
        ))
        for cash, tier_index in (
            (1_999, 0),
            (2_000, 1),
            (5_000, 2),
            (10_000, 3),
            (15_000, 4),
            (30_000, 5),
        ):
            self.assertEqual(
                sex_service_venue_type_weights(cash),
                {
                    venue_type: float(weight)
                    for venue_type, weight in (
                        config.SEX_SERVICE_VENUE_TYPE_WEIGHTS_BY_CASH_TIER[
                            tier_index
                        ].items()
                    )
                },
            )

    def test_simplified_charge_and_daily_capacity(self) -> None:
        self.assertEqual(simplified_sex_service_charge(10_000, 5_000), 1_000)
        self.assertEqual(simplified_sex_service_charge(1_000_000, 5_000), 5_000)
        self.assertEqual(sex_service_daily_capacity(3), 30)

    def test_hex_distance_and_selection_factors_are_monotonic(self) -> None:
        self.assertEqual(sex_service_cell_distance((0, 0), (3, -1)), 3)
        base = {
            "customer_id": "CUS-1",
            "venue_id": "VENUE-1",
            "seed": 9,
        }
        close = sex_service_venue_selection_weight(
            **base,
            distance_cells=1,
            familiarity_visits=0,
            appeal_score=50,
            free_access=False,
        )
        far = sex_service_venue_selection_weight(
            **base,
            distance_cells=20,
            familiarity_visits=0,
            appeal_score=50,
            free_access=False,
        )
        familiar = sex_service_venue_selection_weight(
            **base,
            distance_cells=1,
            familiarity_visits=8,
            appeal_score=50,
            free_access=False,
        )
        attractive = sex_service_venue_selection_weight(
            **base,
            distance_cells=1,
            familiarity_visits=0,
            appeal_score=90,
            free_access=False,
        )
        free = sex_service_venue_selection_weight(
            **base,
            distance_cells=1,
            familiarity_visits=0,
            appeal_score=50,
            free_access=True,
        )
        self.assertGreater(close, far)
        self.assertGreater(familiar, close)
        self.assertGreater(attractive, close)
        self.assertGreater(free, close)

    def test_only_nearest_six_eligible_venues_enter_final_draw(self) -> None:
        venue_index = SexServiceVenueIndex([_venue(index) for index in range(7)])
        for seed in range(30):
            selected = select_sex_service_venue(
                _customer(),
                venue_index,
                seed=seed,
                visit_key=seed,
            )
            self.assertIsNotNone(selected)
            self.assertNotEqual(selected.organization_id, "VENUE-6")

    def test_full_venue_is_excluded_and_player_venue_uses_same_daily_capacity(self) -> None:
        full = _venue(0, sex_worker_count=1, daily_visit_count=10)
        available = _venue(1)
        selected = select_sex_service_venue(_customer(), [full, available])
        self.assertEqual(selected.organization_id, "VENUE-1")

        player = _venue(2, exact_available=False)
        self.assertEqual(select_sex_service_venue(
            _customer(),
            [player],
            player_organization_id="VENUE-2",
        ).organization_id, "VENUE-2")
        player["daily_visit_count"] = 10
        self.assertIsNone(select_sex_service_venue(
            _customer(),
            [player],
            player_organization_id="VENUE-2",
        ))

    def test_local_crime_privilege_can_select_an_unaffordable_venue(self) -> None:
        privilege = SexServicePrivilege(
            scope="street",
            city_id="CITY-1",
            district="北区",
            street="槐树街",
        )
        expensive = _venue(0, min_price=1_000, max_price=5_000)
        self.assertIsNone(select_sex_service_venue(
            _customer(cash=0),
            [expensive],
        ))
        selected = select_sex_service_venue(
            _customer(cash=0),
            [expensive],
            privilege=privilege,
        )
        self.assertTrue(selected.free_access)
        self.assertEqual(selected.customer_charge, 0)

    def test_summary_uses_real_worker_skills_prices_and_appeal(self) -> None:
        worker = {
            "id": "WORKER-1",
            "occupation": "妓女",
            "sex_worker_level": 5,
            "skills": {"打飞机": "熟练", "阴道交": "专业"},
            "sex_worker_score": 82.0,
        }
        summary = build_sex_service_venue_summary(
            {
                "organization_id": "VENUE-1",
                "city_id": "CITY-1",
                "template_name": "ordinary_brothel",
                "district": "北区",
                "street": "槐树街",
                "address": [4, -2],
            },
            [worker],
        )
        self.assertEqual(summary["sex_worker_count"], 1)
        self.assertEqual(summary["min_price"], 160)
        self.assertEqual(summary["max_price"], 520)
        self.assertEqual(summary["appeal_score"], 82.0)


class VenueTimeUpdateIntegrationTests(unittest.TestCase):
    def test_planned_visits_choose_a_venue_and_elapsed_visits_charge_cash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "world.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE cities(city_id TEXT PRIMARY KEY, seed INTEGER NOT NULL);
                CREATE TABLE organizations(
                    organization_id TEXT PRIMARY KEY,
                    city_id TEXT NOT NULL,
                    template_name TEXT NOT NULL,
                    parent_organization_id TEXT,
                    root_organization_id TEXT NOT NULL,
                    district_name TEXT NOT NULL,
                    street_name TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE characters(
                    character_id TEXT PRIMARY KEY,
                    city_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    occupation TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                """
            )
            connection.execute("INSERT INTO cities VALUES ('CITY-1', 12345)")
            connection.executemany(
                """
                INSERT INTO organizations VALUES (
                    ?, 'CITY-1', ?, NULL, ?, '北区', '槐树街', ?
                )
                """,
                (
                    (
                        "ORG-WORK",
                        "ordinary_company",
                        "ORG-WORK",
                        json.dumps({"address": [0, 0]}),
                    ),
                    (
                        "VENUE-1",
                        "ordinary_brothel",
                        "VENUE-1",
                        json.dumps({"address": [2, 0]}),
                    ),
                ),
            )
            customer = _customer(
                id="CUS-1",
                cash=20_000,
                income=100,
                sexual_preferences={"desire": "欲望失控"},
                hobbies=["嫖妓"],
            )
            worker = {
                "sex": "female",
                "age": 24,
                "cash": 0,
                "income": 10_000,
                "address": [2, 0],
                "skills": {"打飞机": "熟练", "阴道交": "专业"},
                "sex_worker_level": 5,
                "sex_worker_score": 82.0,
            }
            connection.executemany(
                "INSERT INTO characters VALUES (?, 'CITY-1', ?, ?, ?)",
                (
                    (
                        "CUS-1",
                        "ORG-WORK",
                        "建筑工人",
                        json.dumps(customer, ensure_ascii=False),
                    ),
                    (
                        "WORKER-1",
                        "VENUE-1",
                        "妓女",
                        json.dumps(worker, ensure_ascii=False),
                    ),
                ),
            )
            connection.commit()
            connection.close()

            timezone = ZoneInfo("Asia/Shanghai")
            update_time(database, now=datetime(2026, 1, 15, 12, 0, tzinfo=timezone))
            update_time(database, now=datetime(2026, 3, 2, 9, 0, tzinfo=timezone))

            connection = sqlite3.connect(database)
            try:
                elapsed, total_charge = connection.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(customer_charge), 0)
                    FROM sex_service_visit_schedule
                    WHERE character_id = 'CUS-1'
                      AND status = 'elapsed'
                      AND organization_id = 'VENUE-1'
                    """
                ).fetchone()
                cash = connection.execute(
                    """
                    SELECT json_extract(data_json, '$.cash')
                    FROM characters WHERE character_id = 'CUS-1'
                    """
                ).fetchone()[0]
                self.assertGreater(elapsed, 0)
                self.assertEqual(cash, 20_200 - total_charge)
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM sex_service_visit_schedule
                        WHERE character_id = 'CUS-1'
                          AND organization_id = 'VENUE-1'
                          AND quoted_price = 520
                          AND customer_charge = 520
                        """
                    ).fetchone()[0],
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM sex_service_visit_schedule
                        WHERE character_id = 'CUS-1'
                        """
                    ).fetchone()[0],
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM sex_service_visit_schedule
                        WHERE organization_id = 'VENUE-1'
                          AND visit_date IS NOT NULL
                          AND arrival_at IS NULL
                          AND worker_character_id IS NULL
                          AND service_name IS NULL
                        """
                    ).fetchone()[0],
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM sex_service_visit_schedule
                        WHERE organization_id = 'VENUE-1'
                        """
                    ).fetchone()[0],
                )
            finally:
                connection.close()

    def test_player_venue_visits_receive_arrivals_and_continuous_appointments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "player-world.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE cities(city_id TEXT PRIMARY KEY, seed INTEGER NOT NULL);
                CREATE TABLE organizations(
                    organization_id TEXT PRIMARY KEY,
                    city_id TEXT NOT NULL,
                    template_name TEXT NOT NULL,
                    parent_organization_id TEXT,
                    root_organization_id TEXT NOT NULL,
                    district_name TEXT NOT NULL,
                    street_name TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE characters(
                    character_id TEXT PRIMARY KEY,
                    city_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    occupation TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                """
            )
            connection.execute("INSERT INTO cities VALUES ('CITY-1', 12345)")
            connection.executemany(
                "INSERT INTO organizations VALUES (?, 'CITY-1', ?, NULL, ?, '北区', '槐树街', ?)",
                (
                    (
                        "ORG-WORK", "ordinary_company", "ORG-WORK",
                        json.dumps({"address": [0, 0]}),
                    ),
                    (
                        "VENUE-PLAYER", "ordinary_brothel", "VENUE-PLAYER",
                        json.dumps({"address": [1, 0]}),
                    ),
                ),
            )
            customer = _customer(
                id="CUS-PLAYER-VISITOR",
                cash=20_000,
                sexual_preferences={"desire": "欲望失控"},
                hobbies=["嫖妓"],
                attraction_preferences=_attraction_preferences(),
            )
            worker = {
                "sex": "female",
                "age": 24,
                "cash": 0,
                "income": 10_000,
                "address": [1, 0],
                "appearance_score": 80.0,
                "hygiene": "卫生良好",
                "cup_size": "C",
                "temperament": character_generator.FEMALE_TEMPERAMENT_OPTIONS[0],
                "skills": {"打飞机": "熟练"},
                "sex_worker_level": 5,
                "sex_worker_score": 80.0,
            }
            connection.executemany(
                "INSERT INTO characters VALUES (?, 'CITY-1', ?, ?, ?)",
                (
                    (
                        "CUS-PLAYER-VISITOR", "ORG-WORK", "建筑工人",
                        json.dumps(customer, ensure_ascii=False),
                    ),
                    (
                        "WORKER-PLAYER", "VENUE-PLAYER", "发廊妹",
                        json.dumps(worker, ensure_ascii=False),
                    ),
                ),
            )
            connection.commit()
            connection.close()

            timezone = ZoneInfo("Asia/Shanghai")
            update_time(
                database,
                now=datetime(2026, 1, 1, 0, 0, tzinfo=timezone),
                player_organization_id="VENUE-PLAYER",
            )
            update_time(
                database,
                now=datetime(2026, 1, 31, 23, 0, tzinfo=timezone),
                player_organization_id="VENUE-PLAYER",
            )

            connection = sqlite3.connect(database)
            try:
                rows = connection.execute(
                    """
                    SELECT arrival_at, service_started_at, service_ended_at,
                           waiting_minutes, worker_character_id, service_name,
                           quoted_price, customer_charge,
                           venue_commission_bps, worker_earnings
                    FROM sex_service_visit_schedule
                    WHERE organization_id = 'VENUE-PLAYER'
                    ORDER BY arrival_at
                    """
                ).fetchall()
                self.assertTrue(rows)
                previous_end = None
                for (
                    arrival_text,
                    start_text,
                    end_text,
                    waiting_minutes,
                    worker_id,
                    service_name,
                    quoted_price,
                    customer_charge,
                    commission_bps,
                    worker_earnings,
                ) in rows:
                    arrival = datetime.fromisoformat(arrival_text)
                    start = datetime.fromisoformat(start_text)
                    end = datetime.fromisoformat(end_text)
                    expected_start = max(arrival, previous_end or arrival)
                    self.assertEqual(start, expected_start)
                    self.assertEqual(
                        waiting_minutes,
                        int((start - arrival).total_seconds() // 60),
                    )
                    self.assertEqual(worker_id, "WORKER-PLAYER")
                    self.assertEqual(service_name, "手冲服务")
                    self.assertEqual(quoted_price, 160)
                    self.assertEqual(customer_charge, 160)
                    self.assertEqual(commission_bps, 4_000)
                    self.assertEqual(worker_earnings, 96)
                    self.assertGreater(end, start)
                    previous_end = end
            finally:
                connection.close()

            connection = sqlite3.connect(database)
            try:
                worker_cash, paid_count, earned_count = connection.execute(
                    """
                    SELECT
                        json_extract(character.data_json, '$.cash'),
                        SUM(visit.worker_paid_at IS NOT NULL),
                        SUM(visit.worker_earnings > 0)
                    FROM characters AS character
                    JOIN sex_service_visit_schedule AS visit
                      ON visit.worker_character_id = character.character_id
                    WHERE character.character_id = 'WORKER-PLAYER'
                    """
                ).fetchone()
                self.assertEqual(worker_cash, 0)
                self.assertEqual(paid_count, 0)
                self.assertGreater(earned_count, 0)
            finally:
                connection.close()

            update_time(
                database,
                now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone),
                player_organization_id="VENUE-PLAYER",
            )
            connection = sqlite3.connect(database)
            try:
                paid_total = connection.execute(
                    """
                    SELECT COALESCE(SUM(worker_earnings), 0)
                    FROM sex_service_visit_schedule
                    WHERE organization_id = 'VENUE-PLAYER'
                      AND worker_paid_at IS NOT NULL
                    """
                ).fetchone()[0]
                worker_cash, lifetime_earnings = connection.execute(
                    """
                    SELECT json_extract(data_json, '$.cash'),
                           json_extract(data_json, '$.sex_service_earnings_total')
                    FROM characters WHERE character_id = 'WORKER-PLAYER'
                    """
                ).fetchone()
                self.assertGreater(paid_total, 0)
                self.assertEqual(worker_cash, paid_total)
                self.assertEqual(lifetime_earnings, paid_total)
            finally:
                connection.close()


class MonthlyVisitPlanningTests(unittest.TestCase):
    def test_date_plan_contains_no_arrival_time(self) -> None:
        dates = plan_monthly_sex_service_visit_dates(
            _customer(
                sexual_preferences={"desire": "欲望失控"},
                hobbies=["嫖妓"],
            ),
            2026,
            10,
            seed=99,
        )
        self.assertTrue(dates)
        self.assertTrue(all(type(value) is date for value in dates))

    def test_desire_range_is_at_most_threefold(self) -> None:
        low = monthly_sex_service_visit_frequency(_customer(), seed=7)
        high = monthly_sex_service_visit_frequency(
            _customer(sexual_preferences={"desire": "欲望失控"}),
            seed=7,
        )
        self.assertAlmostEqual(high / low, 3.0)

    def test_age_and_occupation_do_not_change_frequency(self) -> None:
        first = monthly_sex_service_visit_frequency(
            _customer(age=18, occupation="建筑工人"),
            seed=11,
        )
        second = monthly_sex_service_visit_frequency(
            _customer(age=79, occupation="夜场经理"),
            seed=11,
        )
        self.assertEqual(first, second)

    def test_only_cash_is_used_as_wealth_input(self) -> None:
        baseline = monthly_sex_service_visit_frequency(
            _customer(cash=20_000, income=0, debt=0, other_assets=0),
            seed=12,
        )
        financially_different = monthly_sex_service_visit_frequency(
            _customer(
                cash=20_000,
                income=100_000_000,
                debt=900_000_000,
                other_assets=4_000_000_000,
            ),
            seed=12,
        )
        richer_cash = monthly_sex_service_visit_frequency(
            _customer(cash=2_000_000),
            seed=12,
        )
        self.assertEqual(baseline, financially_different)
        self.assertGreater(richer_cash, baseline)

    def test_privilege_scope_gives_a_bounded_frequency_uplift(self) -> None:
        paid = monthly_sex_service_visit_frequency(_customer(), seed=13)
        street = monthly_sex_service_visit_frequency(
            _customer(), privilege_scope="street", seed=13
        )
        district = monthly_sex_service_visit_frequency(
            _customer(), privilege_scope="district", seed=13
        )
        city = monthly_sex_service_visit_frequency(
            _customer(), privilege_scope="city", seed=13
        )
        self.assertLess(paid, street)
        self.assertLess(street, district)
        self.assertLess(district, city)
        self.assertLessEqual(city / paid, 1.10)

    def test_crime_privilege_uses_rank_and_exact_territory(self) -> None:
        organizations = (
            {
                "organization_id": "ROOT",
                "template_name": "crime_syndicate_city_level_1",
                "parent_organization_id": None,
            },
            {
                "organization_id": "BRANCH",
                "template_name": "district_control_branch_1",
                "parent_organization_id": "ROOT",
            },
        )
        depths = sex_service_crime_organization_depths(organizations)
        self.assertEqual(depths, {"ROOT": 0, "BRANCH": 1})
        self.assertEqual(
            sex_service_crime_privilege_scope(
                "crime_syndicate_city_level_1", 0, "leader"
            ),
            "city",
        )
        self.assertEqual(
            sex_service_crime_privilege_scope(
                "district_control_branch_1", 1, "branch_leader"
            ),
            "district",
        )
        self.assertEqual(
            sex_service_crime_privilege_scope(
                "district_control_branch_1", 1, "operations_staff"
            ),
            "street",
        )
        for template_name, organization_depth, role_key in (
            ("crime_syndicate_city_level_1", 0, "administration_director"),
            ("crime_syndicate_city_level_1", 0, "personal_secretary"),
            ("district_control_branch_1", 1, "secretariat_manager"),
            ("casino_1", 1, "baccarat_dealer"),
            ("luxury_brothel", 2, "prostitute"),
        ):
            with self.subTest(template=template_name, role=role_key):
                self.assertIsNone(sex_service_crime_privilege_scope(
                    template_name,
                    organization_depth,
                    role_key,
                ))
        privilege = SexServicePrivilege(
            scope="street",
            city_id="CITY-1",
            district="北区",
            street="槐树街",
        )
        self.assertTrue(sex_service_privilege_covers_venue(
            privilege,
            {"city_id": "CITY-1", "district": "北区", "street": "槐树街"},
        ))
        self.assertFalse(sex_service_privilege_covers_venue(
            privilege,
            {"city_id": "CITY-1", "district": "北区", "street": "长乐街"},
        ))

    def test_visit_plan_is_deterministic_and_respects_opening_hours(self) -> None:
        character = _customer(
            sexual_preferences={"desire": "欲望失控"},
            hobbies=["嫖妓"],
        )
        first = plan_monthly_sex_service_visits(
            character,
            2026,
            10,
            seed=99,
        )
        second = plan_monthly_sex_service_visits(
            character,
            2026,
            10,
            seed=99,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertTrue(first)
        self.assertTrue(all(value.year == 2026 and value.month == 10 for value in first))
        self.assertTrue(all(value.hour >= 12 or value.hour < 4 for value in first))

    def test_occupation_changes_time_weights_not_frequency(self) -> None:
        day = sex_service_visit_time_weights(_customer(occupation="建筑工人"))
        night = sex_service_visit_time_weights(_customer(occupation="夜场经理"))
        self.assertNotEqual(day, night)

    def test_current_occupation_catalogue_has_time_profiles(self) -> None:
        occupations: set[str] = set()
        for category in config.OCCUPATION_TEMPLATES.values():
            if isinstance(category, dict):
                for group in category.values():
                    occupations.update(group)
            else:
                occupations.update(category)
        self.assertEqual(
            occupations - set(config.SEX_SERVICE_VISIT_OCCUPATION_TIME_PROFILE),
            set(),
        )

    def test_every_sex_service_venue_has_opening_periods(self) -> None:
        venue_templates = {
            template_name
            for templates in config.ORGANIZATION_TEMPLATES.values()
            for template_name, template in templates.items()
            if template.get("sex_service", {}).get("enabled")
        }
        self.assertEqual(
            venue_templates,
            set(config.SEX_SERVICE_VENUE_OPEN_PERIODS),
        )


class TimeUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "world.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE cities(
                city_id TEXT PRIMARY KEY,
                seed INTEGER NOT NULL
            );
            CREATE TABLE organizations(
                organization_id TEXT PRIMARY KEY,
                city_id TEXT NOT NULL,
                template_name TEXT NOT NULL,
                parent_organization_id TEXT,
                root_organization_id TEXT NOT NULL,
                district_name TEXT NOT NULL,
                street_name TEXT NOT NULL
            );
            CREATE TABLE characters(
                character_id TEXT PRIMARY KEY,
                city_id TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                occupation TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO cities VALUES ('CITY-1', 12345)")
        connection.executemany(
            "INSERT INTO organizations VALUES (?, 'CITY-1', ?, ?, ?, ?, ?)",
            (
                (
                    "ORG-NORMAL", "ordinary_company", None, "ORG-NORMAL",
                    "北区", "槐树街",
                ),
                (
                    "ORG-CRIME",
                    "crime_syndicate_city_level_1",
                    None,
                    "ORG-CRIME",
                    "中央区",
                    "金融街",
                ),
                (
                    "ORG-CRIME-CHILD",
                    "district_control_branch_1",
                    "ORG-CRIME",
                    "ORG-CRIME",
                    "北区",
                    "槐树街",
                ),
            ),
        )

        def add_character(
            character_id: str,
            organization_id: str,
            occupation: str,
            sex: str,
            cash: int,
            income: int,
            desire: str | None = None,
            hobbies: list[str] | None = None,
            organization_role: str | None = None,
        ) -> None:
            payload = {
                "sex": sex,
                "age": 30,
                "cash": cash,
                "income": income,
                "sexual_preferences": {"desire": desire} if desire else None,
                "hobbies": hobbies or [],
                "organization_role": organization_role,
            }
            connection.execute(
                "INSERT INTO characters VALUES (?, 'CITY-1', ?, ?, ?)",
                (
                    character_id,
                    organization_id,
                    occupation,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

        add_character(
            "CUS-NORMAL",
            "ORG-NORMAL",
            "建筑工人",
            "male",
            20_000,
            100,
            "色情成瘾",
        )
        add_character(
            "CUS-CRIME",
            "ORG-CRIME-CHILD",
            "行动队员",
            "male",
            20_000,
            200,
            "色情成瘾",
            ["嫖妓"],
            "operations_staff",
        )
        add_character("CUS-WOMAN", "ORG-NORMAL", "会计", "female", 30, 300)
        add_character("CUS-WORKER", "ORG-NORMAL", "妓女", "female", 40, 400)
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _cash(self, character_id: str) -> int:
        connection = sqlite3.connect(self.database)
        try:
            return int(connection.execute(
                "SELECT json_extract(data_json, '$.cash') FROM characters WHERE character_id = ?",
                (character_id,),
            ).fetchone()[0])
        finally:
            connection.close()

    def test_initializes_then_pays_each_crossed_first_and_keeps_three_day_horizon(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        first = datetime(2026, 1, 15, 12, 0, tzinfo=timezone)
        second = datetime(2026, 3, 2, 9, 0, tzinfo=timezone)

        self.assertTrue(update_time(self.database, now=first))
        self.assertEqual(self._cash("CUS-NORMAL"), 20_000)
        self.assertEqual(self._cash("CUS-WORKER"), 40)

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sex_service_schedule_days"
                ).fetchone()[0],
                3,
            )
        finally:
            connection.close()

        self.assertTrue(update_time(self.database, now=second))
        self.assertEqual(self._cash("CUS-NORMAL"), 20_200)
        self.assertEqual(self._cash("CUS-CRIME"), 20_400)
        self.assertEqual(self._cash("CUS-WOMAN"), 630)
        self.assertEqual(self._cash("CUS-WORKER"), 40)

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sex_service_schedule_days"
                ).fetchone()[0],
                (
                    second.date()
                    + timedelta(days=config.SEX_SERVICE_PLANNING_HORIZON_DAYS)
                    - (first.date() + timedelta(days=1))
                ).days + 1,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM sex_service_visit_schedule WHERE status = 'elapsed'"
                ).fetchone()[0],
                0,
            )
            privilege_row = connection.execute(
                """
                SELECT
                    MIN(free_access),
                    MIN(privilege_scope),
                    MIN(privilege_district_name),
                    MIN(privilege_street_name)
                FROM sex_service_visit_schedule
                WHERE character_id = 'CUS-CRIME'
                """
            ).fetchone()
            self.assertEqual(
                privilege_row,
                (0, "street", "北区", "槐树街"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(free_access) FROM sex_service_visit_schedule WHERE character_id = 'CUS-NORMAL'"
                ).fetchone()[0],
                0,
            )
            before_repeat = connection.execute(
                "SELECT COUNT(*) FROM sex_service_visit_schedule"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertTrue(update_time(self.database, now=second))
        self.assertEqual(self._cash("CUS-NORMAL"), 20_200)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sex_service_visit_schedule"
                ).fetchone()[0],
                before_repeat,
            )
            stored = connection.execute(
                "SELECT last_update_at FROM world_time_state WHERE singleton_id = 1"
            ).fetchone()[0]
            self.assertEqual(
                datetime.fromisoformat(stored),
                second.astimezone(ZoneInfo("UTC")),
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
