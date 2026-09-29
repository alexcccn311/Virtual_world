from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from world_generation.services.city_map_service import (
    load_city_map_buildings,
    load_city_map_cell_roads,
    load_city_map_snapshot,
)


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
                    CREATE TABLE roads(
                        city_id TEXT NOT NULL,
                        road_id TEXT NOT NULL,
                        street_name TEXT NOT NULL,
                        level TEXT NOT NULL,
                        data_json TEXT NOT NULL
                    );
                    CREATE TABLE bus_stops(
                        city_id TEXT NOT NULL,
                        stop_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        position_x REAL NOT NULL,
                        position_y REAL NOT NULL
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
                connection.execute(
                    "INSERT INTO roads VALUES (?, ?, ?, ?, ?)",
                    (
                        "CITY-1",
                        "ROAD-1",
                        "中街",
                        "district_boundary",
                        json.dumps(
                            {
                                "segment_type": "boundary",
                                "centerline": [[0.0, 0.0], [1.0, 1.0]],
                            }
                        ),
                    ),
                )
                connection.execute(
                    "INSERT INTO roads VALUES (?, ?, ?, ?, ?)",
                    (
                        "CITY-1",
                        "ROAD-CELL",
                        "中街",
                        "local",
                        json.dumps(
                            {
                                "segment_type": "subdivision",
                                "cell": [0, 0],
                                "centerline": [[0.1, 0.2], [0.8, 0.9]],
                            }
                        ),
                    ),
                )
                connection.execute(
                    "INSERT INTO bus_stops VALUES (?, ?, ?, ?, ?)",
                    ("CITY-1", "STOP-1", "中央区中街站", 0.5, 0.5),
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
            self.assertEqual(snapshot.roads[0].level, "district_boundary")
            self.assertEqual(snapshot.roads[0].centerline[1], (1.0, 1.0))
            self.assertEqual(snapshot.bus_stops[0].name, "中央区中街站")
            workplace_map = load_city_map_snapshot(database, "CUS-1", "ORG-SHOP")
            workplace = next(item for item in workplace_map.landmarks if item.organization_id == "ORG-SHOP")
            self.assertEqual(workplace.min_level, "city")
            self.assertEqual(workplace.kind, "workplace")
            self.assertEqual(workplace.name, "便利店")
            self.assertEqual(len(workplace_map.landmarks), len(snapshot.landmarks) + 1)
            self.assertNotIn("ORG-SHOP", {item.organization_id for item in load_city_map_snapshot(database, "CUS-1", "ORG-CASINO").landmarks})
            # A venue gets a distinct workplace marker only for its employee.
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("INSERT INTO organizations VALUES (?, 'CITY-1', ?, ?, ?)",
                                   ("ORG-VENUE", "ordinary_brothel", "测试会馆", json.dumps({"address": [0, 0]})))
            for employer, expected, level in (("ORG-VENUE", "workplace", "city"), ("ORG-CASINO", "sex_service", "cell")):
                view = load_city_map_snapshot(database, "CUS-1", employer)
                venue = next(item for item in view.landmarks if item.organization_id == "ORG-VENUE")
                self.assertEqual((venue.kind, venue.min_level), (expected, level))
                self.assertEqual(sum(item.kind == "workplace" for item in view.landmarks), 1)
            cell_roads = load_city_map_cell_roads(database, "CITY-1", ((0, 0),))
            self.assertEqual(len(cell_roads), 1)
            self.assertEqual(cell_roads[0].kind, "subdivision")
            self.assertEqual(load_city_map_buildings(database, "CITY-1", ((0, 0),)), [])
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    """CREATE TABLE parcels(city_id TEXT, parcel_id TEXT, cell_q INTEGER, cell_r INTEGER);
                       CREATE TABLE buildings(city_id TEXT, parcel_id TEXT, data_json TEXT);"""
                )
                for index in range(2):
                    connection.execute("INSERT INTO parcels VALUES (?, ?, ?, 0)", ("CITY-1", str(index), index))
                    connection.execute("INSERT INTO buildings VALUES (?, ?, ?)", (
                        "CITY-1", str(index), json.dumps({"footprint": [[index, 0], [index+1, 0], [index, 1]]}),
                    ))
                connection.commit()
            footprints = load_city_map_buildings(database, "CITY-1", ((0, 0),))
            self.assertEqual(footprints, [[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]])
            with self.assertRaisesRegex(ValueError, "最多读取 5 个 cell"):
                load_city_map_cell_roads(
                    database,
                    "CITY-1",
                    tuple((index, 0) for index in range(6)),
                )


if __name__ == "__main__":
    unittest.main()
