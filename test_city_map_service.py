from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from world_generation.services.city_map_service import load_city_map_snapshot


class CityMapServiceTests(unittest.TestCase):
    def test_snapshot_contains_geometry_player_and_only_city_landmarks(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "world.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE cities(
                        city_id TEXT PRIMARY KEY,
                        city_template TEXT NOT NULL
                    );
                    CREATE TABLE districts(
                        city_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        data_json TEXT NOT NULL
                    );
                    CREATE TABLE streets(
                        city_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        district_name TEXT NOT NULL,
                        data_json TEXT NOT NULL
                    );
                    CREATE TABLE organizations(
                        organization_id TEXT PRIMARY KEY,
                        city_id TEXT NOT NULL,
                        template_name TEXT NOT NULL,
                        name TEXT NOT NULL,
                        data_json TEXT NOT NULL
                    );
                    CREATE TABLE characters(
                        character_id TEXT PRIMARY KEY,
                        city_id TEXT NOT NULL,
                        district_name TEXT NOT NULL,
                        street_name TEXT NOT NULL,
                        data_json TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO cities VALUES ('CITY-1', '测试城市')"
                )
                connection.execute(
                    "INSERT INTO districts VALUES (?, ?, ?)",
                    ("CITY-1", "中央区", json.dumps({"cells": [[0, 0], [1, 0]]})),
                )
                connection.execute(
                    "INSERT INTO streets VALUES (?, ?, ?, ?)",
                    ("CITY-1", "中街", "中央区", json.dumps({"cells": [[0, 0], [1, 0]]})),
                )
                organizations = (
                    ("ORG-CASINO", "casino_1", "金殿", [1, 0]),
                    (
                        "ORG-CRIME",
                        "crime_syndicate_city_level_1",
                        "黑山会",
                        [0, 0],
                    ),
                    ("ORG-SHOP", "small_shop", "便利店", [1, 0]),
                )
                for organization_id, template, name, address in organizations:
                    connection.execute(
                        "INSERT INTO organizations VALUES (?, 'CITY-1', ?, ?, ?)",
                        (organization_id, template, name, json.dumps({"address": address})),
                    )
                connection.execute(
                    "INSERT INTO characters VALUES (?, ?, ?, ?, ?)",
                    (
                        "CUS-1",
                        "CITY-1",
                        "中央区",
                        "中街",
                        json.dumps({"address": [0, 0]}),
                    ),
                )
                connection.commit()

            snapshot = load_city_map_snapshot(database, "CUS-1")

            self.assertEqual(snapshot.player_address, (0, 0))
            self.assertEqual(snapshot.player_district, "中央区")
            self.assertEqual(snapshot.player_street, "中街")
            self.assertEqual(snapshot.districts[0].cells, ((0, 0), (1, 0)))
            self.assertEqual(
                {landmark.kind for landmark in snapshot.landmarks},
                {"casino", "crime_headquarters"},
            )
            self.assertNotIn(
                "ORG-SHOP",
                {landmark.organization_id for landmark in snapshot.landmarks},
            )


if __name__ == "__main__":
    unittest.main()
