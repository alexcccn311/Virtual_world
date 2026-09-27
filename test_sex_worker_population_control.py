from __future__ import annotations

import json
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from world_generation import config
from world_generation.generators.city_generator import (
    _allocate_district_sex_worker_minimums,
    _independent_sex_worker_template_names,
)
from world_generation.generators.district_generator import (
    generate_district_organizations,
)
from world_generation.generators.organization_generator import (
    OrganizationGenerator,
)
from world_generation.models.entities import District, Street
from world_generation.storage.sqlite_store import SQLiteWorldStore


def _district(name: str, sex_index: float) -> District:
    return District(
        template="test",
        name=name,
        level="low",
        population=1_000,
        prosperity=50,
        area=1.0,
        unorganized_population_ratio=0.2,
        sex_index=sex_index,
        required_organizations={},
    )


class _FakeOrganizationGenerator:
    def __init__(self, templates: dict[str, dict]) -> None:
        self.templates = templates
        self.serial = 0

    def generate(
        self,
        district: str,
        street: str,
        template_name: str,
        district_map: object,
    ) -> dict[str, object]:
        self.serial += 1
        template = self.templates[template_name]
        population = sum(role["count"] for role in template["roles"].values())
        return {
            "organization_id": f"ORG-{self.serial}",
            "template_name": template_name,
            "district": district,
            "street": street,
            "population_usage": population,
            "characters": [],
        }


class _FakeLedger:
    def __init__(self) -> None:
        self.street_used = {"测试街": 0}
        self.street_population_min = {"测试街": 100}
        self.street_capacity = {"测试街": 1_000}
        self.district_used = {"测试区": 0}
        self.district_capacity = {"测试区": 1_000}

    def street_remaining(self, name: str) -> int:
        return self.street_capacity[name] - self.street_used[name]

    def district_remaining(self, name: str) -> int:
        return self.district_capacity[name] - self.district_used[name]

    def reserve_required(self, organization: dict[str, object]) -> None:
        self.reserve(organization, enforce_capacity=False)

    def reserve(
        self,
        organization: dict[str, object],
        *,
        enforce_capacity: bool = True,
    ) -> None:
        population = int(organization["population_usage"])
        self.street_used[str(organization["street"])] += population
        self.district_used[str(organization["district"])] += population


class SexWorkerPopulationControlTests(unittest.TestCase):
    def test_city_floor_uses_largest_remainder_sex_index_apportionment(self) -> None:
        districts = [
            _district("低权重区", 1.0),
            _district("中权重区", 2.0),
            _district("高权重区", 3.0),
        ]
        with patch.object(config, "SEX_WORKER_POPULATION_RATIO", 0.1):
            total, allocated = _allocate_district_sex_worker_minimums(
                100,
                districts,
            )

        self.assertEqual(total, 10)
        self.assertEqual(sum(allocated.values()), total)
        self.assertEqual(allocated, {
            "低权重区": 2,
            "中权重区": 3,
            "高权重区": 5,
        })

    def test_district_removes_sex_worker_templates_after_floor(self) -> None:
        templates = {
            "ordinary": {
                "prosperity": 50,
                "placement": {"street": {"mode": "random"}},
                "roles": {
                    "worker": {"count": 1, "occupation": "厨师"},
                },
                "sub_organizations": {},
            },
            "small_sex_venue": {
                "prosperity": 50,
                "placement": {"street": {"mode": "random"}},
                "roles": {
                    "worker": {"count": 3, "occupation": "妓女"},
                    "owner": {"count": 1, "occupation": "场子经纪"},
                },
                "sub_organizations": {},
            },
            "large_sex_venue": {
                "prosperity": 50,
                "placement": {"street": {"mode": "random"}},
                "roles": {
                    "worker": {"count": 10, "occupation": "妓女"},
                    "owner": {"count": 1, "occupation": "场子经纪"},
                },
                "sub_organizations": {},
            },
        }
        district = _district("测试区", 1.0)
        street = Street(
            name="测试街",
            district="测试区",
            seed=(0, 0),
            cells=((0, 0),),
            area=1.0,
            centroid=(0.0, 0.0),
            neighbors=(),
            prosperity=50,
        )
        ledger = _FakeLedger()
        report: dict[str, object] = {}

        with patch.object(
            config,
            "ORGANIZATION_TEMPLATES",
            {"street": templates},
        ):
            generated = generate_district_organizations(
                district,
                [street],
                {"测试区": 1},
                random.Random(17),
                _FakeOrganizationGenerator(templates),  # type: ignore[arg-type]
                ledger,
                sex_worker_population_minimum=5,
                sex_worker_random_templates=frozenset({
                    "small_sex_venue", "large_sex_venue",
                }),
                generation_report=report,
            )

        self.assertGreater(len(generated), 2)
        self.assertEqual(report["actual_count"], 6)
        self.assertEqual(report["overshoot"], 1)
        self.assertEqual(report["generated_worker_count"], 6)
        self.assertTrue(report["lower_bound_met"])
        self.assertEqual(
            report["generated_organizations_by_template"],
            {"small_sex_venue": 2},
        )

    def test_only_standalone_venues_enter_district_random_pool(self) -> None:
        names = set(_independent_sex_worker_template_names())
        self.assertIn("ordinary_brothel", names)
        self.assertIn("adult_hair_salon", names)
        self.assertNotIn("luxury_brothel", names)
        self.assertNotIn("adult_club", names)

    def test_real_templates_generate_near_the_district_floor(self) -> None:
        district = _district("测试区", 1.0)
        street = Street(
            name="测试街",
            district="测试区",
            seed=(0, 0),
            cells=((0, 0),),
            area=1.0,
            centroid=(0.0, 0.0),
            neighbors=(),
            prosperity=50,
        )
        ledger = _FakeLedger()
        ledger.street_population_min["测试街"] = 0
        report: dict[str, object] = {}

        generated = generate_district_organizations(
            district,
            [street],
            {"测试区": 1},
            random.Random(29),
            OrganizationGenerator(random.Random(29)),
            ledger,
            sex_worker_population_minimum=25,
            sex_worker_random_templates=frozenset(
                _independent_sex_worker_template_names()
            ),
            generation_report=report,
        )

        self.assertTrue(generated)
        self.assertGreaterEqual(report["actual_count"], 25)
        self.assertLessEqual(report["overshoot"], 7)
        self.assertTrue(report["lower_bound_met"])

    def test_generation_control_is_persisted_in_city_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "world.sqlite3"
            store = SQLiteWorldStore(database)
            session = store.begin_city_generation(
                city_template="测试城市",
                seed=1,
                target_population=100,
            )
            control = {
                "target_minimum": 10,
                "actual_count": 11,
                "districts": [{"district": "测试区", "target_minimum": 10}],
            }
            session.complete(
                {
                    "capacity": 0,
                    "used": 0,
                    "remaining": 0,
                    "districts": {},
                    "streets": {},
                },
                additional_data={"sex_worker_population": control},
            )
            connection = sqlite3.connect(database)
            payload = json.loads(
                connection.execute("SELECT data_json FROM cities").fetchone()[0]
            )
            connection.close()

            self.assertEqual(payload["sex_worker_population"], control)


if __name__ == "__main__":
    unittest.main()
