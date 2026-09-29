from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from world_generation.generation_pipeline import generate_city_with_relationships
from world_generation.generators.family_relationship_generator import (
    FamilyAssignmentReport,
)
from world_generation.generators.statistics_report_generator import (
    build_world_statistics_report,
    default_statistics_report_path,
    write_world_statistics_report,
)
from world_generation.generators.sex_worker_level_generator import (
    SexWorkerLevelAssignmentReport,
)


class StatisticsReportGeneratorTests(unittest.TestCase):
    def _database(self, folder: str) -> Path:
        path = Path(folder) / "test_world.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE cities(
                city_id TEXT PRIMARY KEY, city_template TEXT, seed INTEGER,
                target_population INTEGER, status TEXT, created_at TEXT,
                completed_at TEXT, data_json TEXT
            );
            CREATE TABLE districts(
                city_id TEXT, name TEXT, template_name TEXT, level TEXT,
                population INTEGER, prosperity INTEGER
            );
            CREATE TABLE streets(city_id TEXT, name TEXT);
            CREATE TABLE organizations(
                organization_id TEXT PRIMARY KEY, city_id TEXT,
                template_name TEXT, scope TEXT, district_name TEXT,
                street_name TEXT
            );
            CREATE TABLE characters(
                character_id TEXT PRIMARY KEY, city_id TEXT,
                organization_id TEXT, district_name TEXT, street_name TEXT,
                occupation TEXT, title TEXT, data_json TEXT
            );

            INSERT INTO cities VALUES(
                'CITY-1', '测试世界', 7, 2, 'complete',
                '2026-01-01', '2026-01-02',
                '{"sex_worker_population":{"target_minimum":1,"districts":[]}}'
            );
            INSERT INTO cities VALUES(
                'CITY-X', '未完成世界', 8, 99, 'failed',
                '2026-01-01', NULL, '{}'
            );
            INSERT INTO districts VALUES(
                'CITY-1', '一区', 'commercial_core', '一级', 2, 80
            );
            INSERT INTO streets VALUES('CITY-1', '一街');
            INSERT INTO organizations VALUES(
                'ORG-1', 'CITY-1', 'small_restaurant', 'street', '一区', '一街'
            );
            """
        )
        characters = (
            (
                "CUS-1", "CITY-1", "ORG-1", "一区", "一街", "厨师", "大厨",
                {
                    "sex": "female", "age": 28, "education": "高中",
                    "hygiene": "整洁", "temperament": "明艳",
                    "income": 7000, "net_assets": 60000, "debt": 10000,
                    "face_score": 72, "body_score": 68,
                    "appearance_score": 70,
                    "relation": {
                        "family_id": "FAM-1", "family_role": "single",
                    },
                    "address": [3, 4],
                },
            ),
            (
                "CUS-2", "CITY-1", "ORG-1", "一区", "一街", "性奴", "性奴",
                {
                    "sex": "male", "age": 35, "education": "初中",
                    "hygiene": "普通", "temperament": "顺从",
                    "income": 4000, "net_assets": -200000, "debt": 220000,
                    "face_score": 55, "body_score": 61,
                    "appearance_score": 58,
                    "relation": {
                        "family_id": "FAM-2", "family_role": "single",
                    },
                    "address": [3, 4],
                },
            ),
        )
        connection.executemany(
            """
            INSERT INTO characters VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (*row[:-1], json.dumps(row[-1], ensure_ascii=False))
                for row in characters
            ),
        )
        connection.commit()
        connection.close()
        return path

    def test_report_contains_final_family_assets_and_categorized_occupations(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = self._database(folder)
            report = build_world_statistics_report(database)

            self.assertEqual(report["totals"]["characters"], 2)
            self.assertEqual(
                report["population_distributions"]["gender"]["counts"],
                {"female": 1, "male": 1},
            )
            self.assertEqual(report["family"]["family_count"], 2)
            self.assertEqual(report["family"]["occupied_map_cells"], 1)
            self.assertEqual(
                report["family"]["single_person_by_gender"]["counts"],
                {"female": 1, "male": 1},
            )
            self.assertEqual(
                report["family"]["households_per_occupied_cell"]["maximum"], 2
            )
            self.assertEqual(
                sum(report["asset_distributions"]["asset_level"]["counts"].values()),
                2,
            )

            categories = {
                row["category"]: row for row in report["occupation"]["categories"]
            }
            daily = categories["DAILY_OCCUPATIONS"]
            sex_service = categories["SEX_SERVICE_OCCUPATIONS"]
            self.assertEqual(daily["count"], 1)
            self.assertEqual(sex_service["count"], 1)
            self.assertEqual(
                report["sex_worker_population_control"]["target_minimum"],
                1,
            )
            self.assertEqual(
                report["sex_worker_population_control"]["actual_count"],
                1,
            )
            self.assertEqual(report["sex_worker_levels"]["assigned_count"], 0)
            self.assertEqual(report["sex_worker_levels"]["missing_count"], 1)
            self.assertEqual(
                report["sex_worker_population_control"]["cities"][0]
                ["generation_control"]["target_minimum"],
                1,
            )
            all_rows = [
                occupation
                for category in report["occupation"]["categories"]
                for subgroup in category["subcategories"]
                for occupation in subgroup["occupations"]
            ]
            by_name = {row["occupation"]: row["count"] for row in all_rows}
            self.assertEqual(by_name["厨师"], 1)
            self.assertEqual(by_name["性奴"], 1)
            self.assertEqual(report["occupation"]["unclassified_occupations"], [])

    def test_writer_uses_database_derived_name_and_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = self._database(folder)
            output = write_world_statistics_report(database)
            self.assertEqual(output, default_statistics_report_path(database))
            self.assertTrue(output.is_file())
            loaded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(loaded["totals"]["characters"], 2)

    def test_pipeline_writes_statistics_only_after_family_assignment(self) -> None:
        events: list[str] = []
        generated = {
            "city_id": "CITY-1",
            "city_template": "测试世界",
            "districts": [],
            "streets": [],
            "district_map": {"一区": 1},
            "organizations": [],
        }
        family_report = FamilyAssignmentReport(1, 1, 1, 0, 0, 0)
        level_report = SexWorkerLevelAssignmentReport(
            0, {}, {}, {}, {}, {}, 0, 0
        )
        store = SimpleNamespace(database_path=Path("world.sqlite3"))

        def fake_generate(*args: object, **kwargs: object) -> dict[str, object]:
            events.append("generate")
            before_complete = kwargs.get("before_store_complete")
            self.assertIsNotNone(before_complete)
            before_complete(generated)  # type: ignore[operator]
            events.append("complete")
            return generated

        def fake_family(*args: object, **kwargs: object) -> FamilyAssignmentReport:
            events.append("family")
            return family_report

        def fake_levels(
            *args: object,
            **kwargs: object,
        ) -> SexWorkerLevelAssignmentReport:
            events.append("levels")
            return level_report

        def fake_statistics(*args: object, **kwargs: object) -> Path:
            events.append("statistics")
            return Path("world_statistics.json")

        with patch(
            "world_generation.generation_pipeline.generate_city",
            side_effect=fake_generate,
        ), patch(
            "world_generation.generation_pipeline.assign_sex_worker_levels_in_database",
            side_effect=fake_levels,
        ), patch(
            "world_generation.generation_pipeline.assign_family_relationships_in_database",
            side_effect=fake_family,
        ), patch(
            "world_generation.generation_pipeline.write_world_statistics_report",
            side_effect=fake_statistics,
        ):
            result = generate_city_with_relationships(1, store=store)  # type: ignore[arg-type]

        self.assertEqual(
            events,
            ["generate", "levels", "family", "complete", "statistics"],
        )
        self.assertTrue(str(result["statistics_report"]).endswith(
            "world_statistics.json"
        ))


if __name__ == "__main__":
    unittest.main()
