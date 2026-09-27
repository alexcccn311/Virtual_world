from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from world_generation.storage.global_id_registry import (
    GLOBAL_ID_REGISTRY_FILENAME,
    GlobalCharacterIdRegistry,
)
from world_generation.storage.sqlite_store import SQLiteWorldStore


class GlobalIdRegistryTests(unittest.TestCase):
    def test_sibling_world_databases_receive_disjoint_character_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first_store = SQLiteWorldStore(
                root / "first_world.sqlite3",
                character_id_block_size=3,
            )
            first_session = first_store.begin_city_generation(
                city_template="测试世界",
                seed=1,
                target_population=1,
            )
            first_ids = [first_session.character_ids() for _ in range(3)]
            first_session.fail(RuntimeError("test close"))

            second_store = SQLiteWorldStore(
                root / "second_world.sqlite3",
                character_id_block_size=3,
            )
            second_session = second_store.begin_city_generation(
                city_template="测试世界",
                seed=2,
                target_population=1,
            )
            second_ids = [second_session.character_ids() for _ in range(3)]
            second_session.fail(RuntimeError("test close"))

            self.assertEqual(first_ids, [
                "CUS-00000001",
                "CUS-00000002",
                "CUS-00000003",
            ])
            self.assertEqual(second_ids, [
                "CUS-00000004",
                "CUS-00000005",
                "CUS-00000006",
            ])
            self.assertTrue(set(first_ids).isdisjoint(second_ids))
            registry = GlobalCharacterIdRegistry(
                root / GLOBAL_ID_REGISTRY_FILENAME
            )
            self.assertEqual(registry.next_value(), 7)

    def test_registry_bootstraps_after_existing_reserved_range(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / "legacy_world.sqlite3"
            connection = sqlite3.connect(legacy)
            connection.executescript(
                """
                CREATE TABLE characters(character_id TEXT PRIMARY KEY);
                CREATE TABLE id_sequences(
                    kind TEXT PRIMARY KEY,
                    next_value INTEGER NOT NULL
                );
                INSERT INTO id_sequences VALUES ('character', 534001);
                INSERT INTO characters VALUES ('CUS-00533263');
                """
            )
            connection.commit()
            connection.close()

            registry = GlobalCharacterIdRegistry(
                root / GLOBAL_ID_REGISTRY_FILENAME
            )
            self.assertEqual(registry.next_value(), 534001)
            self.assertEqual(
                registry.reserve(2, owner_database="next_world.sqlite3"),
                ["CUS-00534001", "CUS-00534002"],
            )
            self.assertEqual(registry.next_value(), 534003)


if __name__ == "__main__":
    unittest.main()
