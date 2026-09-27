from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from world_generation.generators.sex_worker_level_generator import (
    SEX_WORKER_APPEARANCE_PACKAGE_FIELDS,
    assign_sex_worker_levels,
    assign_sex_worker_levels_in_database,
    sex_worker_minimum_level,
)


def _worker(
    index: int,
    organization_id: str,
    occupation: str,
    score: float,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": f"CUS-{index:04d}",
        "organization_id": organization_id,
        "organization_role": f"role-{index}",
        "occupation": occupation,
        "skills": {"岗位技能": f"技能-{index}"},
        "cash": index,
        "hair": f"发型-{index}",
        "skin_quality": f"肤质-{index}",
        "height": 155 + index % 20,
        "bmi": 18.0 + index / 100,
        "weight": 48.0 + index / 10,
        "bust": 80.0 + index / 10,
        "waist": 60.0 + index / 10,
        "hips": 88.0 + index / 10,
        "feature": f"特征-{index}",
        "face_shape": f"脸型-{index}",
        "eyes": f"眼睛-{index}",
        "eyebrows": f"眉毛-{index}",
        "nose": f"鼻子-{index}",
        "lips": f"嘴唇-{index}",
        "face_score": score,
        "body_score": score,
        "appearance_score": score,
        "cup_size": "C",
    }
    self_missing = set(SEX_WORKER_APPEARANCE_PACKAGE_FIELDS) - set(result)
    if self_missing:
        raise AssertionError(self_missing)
    return result


class SexWorkerLevelGeneratorTests(unittest.TestCase):
    def _population(self) -> tuple[list[dict[str, object]], dict[str, str]]:
        organization_types = {
            "ORG-LUXURY": "luxury_brothel",
            "ORG-ORDINARY": "ordinary_brothel",
            "ORG-HAIR": "adult_hair_salon",
            "ORG-STREET": "street_prostitution_ring",
        }
        workers: list[dict[str, object]] = []
        index = 0
        for organization_id, occupation, count in (
            ("ORG-LUXURY", "妓女", 8),
            ("ORG-ORDINARY", "冰妹", 8),
            ("ORG-HAIR", "发廊妹", 8),
            ("ORG-STREET", "站街女", 8),
        ):
            for _ in range(count):
                workers.append(_worker(
                    index,
                    organization_id,
                    occupation,
                    45.0 + index,
                ))
                index += 1
        return workers, organization_types

    def test_minimum_level_combines_venue_and_occupation(self) -> None:
        self.assertEqual(sex_worker_minimum_level("luxury_brothel", "妓女"), 7)
        self.assertEqual(sex_worker_minimum_level("luxury_brothel", "冰妹"), 5)
        self.assertEqual(sex_worker_minimum_level("ordinary_brothel", "性奴"), 2)
        self.assertEqual(sex_worker_minimum_level("adult_hair_salon", "发廊妹"), 3)
        self.assertEqual(sex_worker_minimum_level("street_prostitution_ring", "站街女"), 1)

    def test_exact_role_demand_is_filled_by_ranked_appearance_packages(self) -> None:
        workers, organization_types = self._population()
        original_scores = sorted(float(worker["appearance_score"]) for worker in workers)
        role_owned = {
            str(worker["id"]): (
                worker["organization_id"],
                worker["occupation"],
                copy.deepcopy(worker["skills"]),
                worker["cash"],
            )
            for worker in workers
        }
        report = assign_sex_worker_levels(
            workers,
            organization_types,
            seed=77,
        )

        self.assertEqual(report.sex_worker_count, len(workers))
        self.assertEqual(sum(report.level_counts.values()), len(workers))
        self.assertEqual(report.minimum_level_violations, 0)
        self.assertEqual(
            sorted(float(worker["appearance_score"]) for worker in workers),
            original_scores,
        )
        for worker in workers:
            self.assertGreaterEqual(
                int(worker["sex_worker_level"]),
                int(worker["sex_worker_minimum_level"]),
            )
            self.assertEqual(
                (
                    worker["organization_id"],
                    worker["occupation"],
                    worker["skills"],
                    worker["cash"],
                ),
                role_owned[str(worker["id"])],
            )

        by_level: dict[int, list[float]] = {}
        for worker in workers:
            by_level.setdefault(int(worker["sex_worker_level"]), []).append(
                float(worker["sex_worker_score"])
            )
        levels = sorted(by_level)
        for lower, higher in zip(levels, levels[1:]):
            self.assertLessEqual(max(by_level[lower]), min(by_level[higher]))

    def test_assignment_is_independent_of_input_order(self) -> None:
        workers, organization_types = self._population()
        forward = copy.deepcopy(workers)
        reverse = list(reversed(copy.deepcopy(workers)))
        first = assign_sex_worker_levels(forward, organization_types, seed=91)
        second = assign_sex_worker_levels(reverse, organization_types, seed=91)
        self.assertEqual(first.as_dict(), second.as_dict())
        first_by_id = {worker["id"]: worker for worker in forward}
        second_by_id = {worker["id"]: worker for worker in reverse}
        for character_id in first_by_id:
            self.assertEqual(first_by_id[character_id], second_by_id[character_id])

    def test_database_assignment_persists_levels_and_city_report(self) -> None:
        workers, organization_types = self._population()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "world.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE cities(city_id TEXT PRIMARY KEY, data_json TEXT);
                CREATE TABLE organizations(
                    organization_id TEXT PRIMARY KEY,
                    template_name TEXT NOT NULL
                );
                CREATE TABLE characters(
                    character_id TEXT PRIMARY KEY,
                    city_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    occupation TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                INSERT INTO cities VALUES ('CITY-1', '{}');
                """
            )
            connection.executemany(
                "INSERT INTO organizations VALUES (?, ?)",
                organization_types.items(),
            )
            connection.executemany(
                "INSERT INTO characters VALUES (?, 'CITY-1', ?, ?, ?)",
                (
                    (
                        worker["id"],
                        worker["organization_id"],
                        worker["occupation"],
                        json.dumps(worker, ensure_ascii=False),
                    )
                    for worker in workers
                ),
            )
            connection.commit()
            connection.close()

            report = assign_sex_worker_levels_in_database(
                database,
                city_id="CITY-1",
                seed=17,
            )
            self.assertEqual(report.sex_worker_count, len(workers))
            connection = sqlite3.connect(database)
            try:
                missing = connection.execute(
                    """
                    SELECT COUNT(*) FROM characters
                    WHERE json_extract(data_json, '$.sex_worker_level') IS NULL
                    """
                ).fetchone()[0]
                city_data = json.loads(connection.execute(
                    "SELECT data_json FROM cities WHERE city_id = 'CITY-1'"
                ).fetchone()[0])
                self.assertEqual(missing, 0)
                self.assertEqual(
                    city_data["sex_worker_levels"]["sex_worker_count"],
                    len(workers),
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
