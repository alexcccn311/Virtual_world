from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from world_generation.generators.family_relationship_generator import (
    FamilyAssignmentReport,
    assign_family_relationships,
    assign_family_relationships_in_database,
    extract_surname,
)
from world_generation.generation_pipeline import generate_city_with_relationships


def _character(
    character_id: str,
    name: str,
    sex: str,
    age: int,
    organization_id: str,
    *,
    relation: dict[str, object] | None = None,
    net_assets: int = 0,
) -> dict[str, object]:
    return {
        "id": character_id,
        "name": name,
        "sex": sex,
        "age": age,
        "organization_id": organization_id,
        "relation": {} if relation is None else relation,
        "net_assets": net_assets,
    }


def _world_map() -> dict[str, object]:
    return {
        "city_id": "CITY-TEST",
        "city_template": "测试城市",
        "districts": [],
        "district_map": {"测试区": 3},
        "streets": [
            {
                "name": "旧巷",
                "district": "测试区",
                "prosperity": 15,
                "cells": ((-2, 0), (-1, 0)),
            },
            {
                "name": "中街",
                "district": "测试区",
                "prosperity": 50,
                "cells": ((0, 0), (1, 0)),
            },
            {
                "name": "金街",
                "district": "测试区",
                "prosperity": 90,
                "cells": ((2, 0), (3, 0)),
            },
        ],
    }


def _two_families() -> list[dict[str, object]]:
    return [
        _character(
            "CUS-00000001",
            "李国强",
            "male",
            50,
            "ORG-A",
            relation={"superior_id": "CUS-BOSS"},
        ),
        _character("CUS-00000002", "张雅琴", "female", 52, "ORG-B"),
        _character("CUS-00000003", "李晨", "male", 25, "ORG-C"),
        _character("CUS-00000004", "李雪", "female", 20, "ORG-D"),
        _character("CUS-00000005", "欧阳建军", "male", 55, "ORG-E"),
        _character("CUS-00000006", "周芳", "female", 54, "ORG-F"),
        _character("CUS-00000007", "欧阳明", "male", 30, "ORG-G"),
        _character("CUS-00000008", "欧阳雨", "female", 25, "ORG-H"),
    ]


class FamilyRelationshipGeneratorTests(unittest.TestCase):
    def test_assigns_reciprocal_three_to_five_person_families(self) -> None:
        characters = _two_families()
        report = assign_family_relationships(
            characters,
            _world_map(),
            seed=907,
        )

        self.assertGreaterEqual(report.family_count, 1)
        self.assertEqual(
            sum(report.family_size_counts.values()), report.family_count
        )
        self.assertEqual(
            report.cross_organization_family_count,
            sum(report.family_size_counts[size] for size in (3, 4, 5)),
        )
        self.assertEqual(report.addressed_character_count, len(characters))
        by_id = {character["id"]: character for character in characters}
        families: dict[str, list[dict[str, object]]] = {}
        for character in characters:
            relation = character["relation"]
            if "family_id" in relation:
                families.setdefault(relation["family_id"], []).append(character)
        self.assertTrue(
            all(len(members) in {1, 3, 4, 5} for members in families.values())
        )

        for members in families.values():
            if len(members) == 1:
                self.assertEqual(members[0]["relation"]["family_role"], "single")
                self.assertEqual(members[0]["relation"]["family_size"], 1)
                continue
            father = next(
                (
                    member
                    for member in members
                    if member["relation"]["family_role"] == "father"
                ),
                None,
            )
            mother = next(
                (
                    member
                    for member in members
                    if member["relation"]["family_role"] == "mother"
                ),
                None,
            )
            children = [
                member
                for member in members
                if member["relation"]["family_role"] not in {"father", "mother"}
            ]
            parents = [parent for parent in (father, mother) if parent is not None]
            self.assertIn((len(parents), len(children)), {(2, 1), (1, 2), (2, 2), (2, 3)})
            self.assertEqual(
                {member["relation"]["family_size"] for member in members},
                {len(members)},
            )
            self.assertEqual(len({extract_surname(child["name"]) for child in children}), 1)
            if father is not None:
                self.assertTrue(
                    all(
                        extract_surname(father["name"]) == extract_surname(child["name"])
                        for child in children
                    )
                )
            for parent in parents:
                for child in children:
                    self.assertLessEqual(20, parent["age"] - child["age"])
                    self.assertLessEqual(parent["age"] - child["age"], 35)
                self.assertEqual(
                    set(parent["relation"]["children_ids"]),
                    {child["id"] for child in children},
                )
            if father is not None and mother is not None:
                self.assertEqual(father["relation"]["spouse_id"], mother["id"])
                self.assertEqual(mother["relation"]["spouse_id"], father["id"])
            for child in children:
                self.assertEqual(
                    set(child["relation"]["sibling_ids"]),
                    {other["id"] for other in children if other is not child},
                )
                if father is not None:
                    self.assertEqual(child["relation"]["father_id"], father["id"])
                else:
                    self.assertNotIn("father_id", child["relation"])
                if mother is not None:
                    self.assertEqual(child["relation"]["mother_id"], mother["id"])
                else:
                    self.assertNotIn("mother_id", child["relation"])
            for member in members:
                self.assertIn(member["id"], by_id)
            self.assertEqual({member["address"] for member in members}, {members[0]["address"]})

        self.assertEqual(
            by_id["CUS-00000001"]["relation"]["superior_id"],
            "CUS-BOSS",
        )

    def test_result_is_independent_of_generation_order_and_idempotent(self) -> None:
        forward = _two_families()
        reversed_input = list(reversed(copy.deepcopy(forward)))
        first = assign_family_relationships(forward, _world_map(), seed=41)
        second = assign_family_relationships(reversed_input, _world_map(), seed=41)

        forward_relations = {
            character["id"]: character["relation"] for character in forward
        }
        reverse_relations = {
            character["id"]: character["relation"]
            for character in reversed_input
        }
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(forward_relations, reverse_relations)
        self.assertEqual(
            {character["id"]: character["address"] for character in forward},
            {
                character["id"]: character["address"]
                for character in reversed_input
            },
        )

        before = copy.deepcopy(forward_relations)
        existing_ids = {
            character_id
            for character_id, relation in before.items()
            if relation.get("family_id")
        }
        repeated = assign_family_relationships(forward, _world_map(), seed=41)
        self.assertEqual(repeated.family_count, 0)
        self.assertEqual(
            repeated.skipped_existing_family_character_count, len(existing_ids)
        )
        after = {character["id"]: character["relation"] for character in forward}
        for character_id in existing_ids:
            self.assertEqual(after[character_id], before[character_id])

    def test_fewer_than_three_compatible_people_become_single_families(self) -> None:
        characters = _two_families()[:2]
        report = assign_family_relationships(characters, _world_map())
        self.assertEqual(report.family_count, 2)
        self.assertEqual(report.family_size_counts, {1: 2, 3: 0, 4: 0, 5: 0})
        self.assertTrue(
            all(
                character["relation"]["family_role"] == "single"
                and character["relation"]["family_size"] == 1
                and character["relation"].get("family_id")
                for character in characters
            )
        )

    def test_supports_both_three_person_family_compositions(self) -> None:
        parents_and_only_child = [
            _character("CUS-A1", "李强", "male", 50, "ORG-A1"),
            _character("CUS-A2", "周兰", "female", 48, "ORG-A2"),
            _character("CUS-A3", "李明", "male", 20, "ORG-A3"),
        ]
        report = assign_family_relationships(
            parents_and_only_child, _world_map(), seed=1
        )
        self.assertEqual(report.family_size_counts, {1: 0, 3: 1, 4: 0, 5: 0})
        roles = {
            character["relation"]["family_role"]
            for character in parents_and_only_child
        }
        self.assertIn("father", roles)
        self.assertIn("mother", roles)
        self.assertTrue(roles & {"only_son", "only_daughter"})

        single_parent_and_siblings = [
            _character("CUS-B1", "王芳", "female", 50, "ORG-B1"),
            _character("CUS-B2", "李晨", "male", 25, "ORG-B2"),
            _character("CUS-B3", "李雪", "female", 20, "ORG-B3"),
        ]
        report = assign_family_relationships(
            single_parent_and_siblings, _world_map(), seed=2
        )
        self.assertEqual(report.single_parent_family_count, 1)
        roles = {
            character["relation"]["family_role"]
            for character in single_parent_and_siblings
        }
        self.assertEqual(roles & {"father", "mother"}, {"mother"})
        self.assertTrue(any(role.startswith("older_") for role in roles))
        self.assertTrue(any(role.startswith("younger_") for role in roles))

    def test_random_plans_can_produce_every_supported_size(self) -> None:
        source = [
            _character("CUS-S1", "李强", "male", 55, "ORG-S1"),
            _character("CUS-S2", "周兰", "female", 53, "ORG-S2"),
            _character("CUS-S3", "李甲", "male", 30, "ORG-S3"),
            _character("CUS-S4", "李乙", "female", 25, "ORG-S4"),
            _character("CUS-S5", "李丙", "male", 20, "ORG-S5"),
        ]
        observed_sizes: set[int] = set()
        for seed in range(100):
            characters = copy.deepcopy(source)
            report = assign_family_relationships(
                characters, _world_map(), seed=seed
            )
            observed_sizes.update(
                size
                for size, count in report.family_size_counts.items()
                if count and size in {3, 4, 5}
            )
            if observed_sizes == {3, 4, 5}:
                break
        self.assertEqual(observed_sizes, {3, 4, 5})

    def test_age_constrained_children_use_older_parents_before_young_children(self) -> None:
        characters = [
            _character("CUS-P1", "赵芳", "female", 60, "ORG-P1"),
            _character("CUS-P2", "钱芳", "female", 60, "ORG-P2"),
            _character("CUS-P3", "孙芳", "female", 45, "ORG-P3"),
            _character("CUS-P4", "周芳", "female", 45, "ORG-P4"),
            _character("CUS-O1", "李甲", "male", 35, "ORG-O1"),
            _character("CUS-O2", "李乙", "male", 34, "ORG-O2"),
            _character("CUS-O3", "王甲", "male", 35, "ORG-O3"),
            _character("CUS-O4", "王乙", "male", 34, "ORG-O4"),
            _character("CUS-Y1", "张甲", "male", 20, "ORG-Y1"),
            _character("CUS-Y2", "张乙", "male", 19, "ORG-Y2"),
            _character("CUS-Y3", "刘甲", "male", 20, "ORG-Y3"),
            _character("CUS-Y4", "刘乙", "male", 19, "ORG-Y4"),
        ]

        report = assign_family_relationships(characters, _world_map(), seed=37)

        self.assertEqual(report.family_size_counts[1], 0)
        self.assertEqual(report.family_size_counts[3], 4)
        self.assertEqual(report.single_parent_family_count, 4)

    def test_database_adapter_merges_relation_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "family.sqlite3"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE cities (
                    city_id TEXT PRIMARY KEY,
                    data_json TEXT
                );
                CREATE TABLE characters (
                    character_id TEXT PRIMARY KEY,
                    city_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO cities(city_id, data_json) VALUES ('CITY-1', '{}')"
            )
            source = _two_families()[:4]
            for character in source:
                connection.execute(
                    """
                    INSERT INTO characters(
                        character_id, city_id, organization_id, name, data_json
                    ) VALUES (?, 'CITY-1', ?, ?, ?)
                    """,
                    (
                        character["id"],
                        character["organization_id"],
                        character["name"],
                        json.dumps(character, ensure_ascii=False),
                    ),
                )
            connection.commit()
            connection.close()

            report = assign_family_relationships_in_database(
                database_path,
                _world_map(),
                city_id="CITY-1",
                seed=17,
            )
            self.assertEqual(sum(report.family_size_counts.values()), report.family_count)

            connection = sqlite3.connect(database_path)
            saved = {
                row[0]: json.loads(row[1])
                for row in connection.execute(
                    "SELECT character_id, data_json FROM characters"
                )
            }
            city_data = json.loads(
                connection.execute(
                    "SELECT data_json FROM cities WHERE city_id = 'CITY-1'"
                ).fetchone()[0]
            )
            connection.close()
            self.assertEqual(
                saved["CUS-00000001"]["relation"]["superior_id"],
                "CUS-BOSS",
            )
            assigned = [
                data for data in saved.values() if data["relation"].get("family_id")
            ]
            self.assertEqual(len(assigned), report.assigned_character_count)
            self.assertEqual(report.assigned_character_count, 4)
            self.assertEqual(report.addressed_character_count, 4)
            self.assertTrue(
                all(
                    isinstance(data.get("address"), list)
                    and len(data["address"]) == 2
                    for data in saved.values()
                )
            )
            family_addresses: dict[str, set[tuple[int, int]]] = {}
            for data in assigned:
                family_id = data["relation"]["family_id"]
                family_addresses.setdefault(family_id, set()).add(
                    tuple(data["address"])
                )
            self.assertTrue(
                all(len(addresses) == 1 for addresses in family_addresses.values())
            )
            self.assertEqual(
                city_data["family_relationships"]["family_count"],
                report.family_count,
            )

    def test_household_wealth_biases_addresses_toward_prosperous_streets(self) -> None:
        world_map = {
            "streets": [
                {
                    "name": f"街道-{index}",
                    "prosperity": index * 10,
                    "cells": ((index, 0),),
                }
                for index in range(10)
            ]
        }
        characters = [
            _character(
                f"CUS-W{index:03d}",
                f"李住户{index}",
                "male" if index % 2 else "female",
                20,
                f"ORG-W{index % 7}",
                net_assets=index * 10_000,
            )
            for index in range(200)
        ]
        report = assign_family_relationships(characters, world_map, seed=816)
        self.assertEqual(report.family_count, 200)
        self.assertEqual(report.family_size_counts[1], 200)
        self.assertEqual(report.household_count, 200)
        self.assertEqual(report.single_person_household_count, 200)
        self.assertEqual(report.addressed_character_count, 200)
        low_average = sum(character["address"][0] for character in characters[:50]) / 50
        high_average = sum(character["address"][0] for character in characters[-50:]) / 50
        self.assertGreater(high_average, low_average + 3)

    def test_requires_map_for_future_address_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "world_map"):
            assign_family_relationships(_two_families(), None)

    def test_pipeline_generates_before_linking_and_passes_full_map(self) -> None:
        events: list[str] = []
        character = _character("CUS-1", "李明", "male", 20, "ORG-1")
        generated = {
            "city_id": None,
            "city_template": "测试城市",
            "districts": ["district-object"],
            "streets": ["street-object"],
            "district_map": {"一区": 3},
            "organizations": [{"characters": [character]}],
        }
        empty_report = FamilyAssignmentReport(0, 0, 1, 0, 0, 0)

        def fake_generate(*args: object, **kwargs: object) -> dict[str, object]:
            events.append("generate")
            return generated

        def fake_assign(
            characters: object,
            world_map: object,
            *,
            seed: int,
        ) -> FamilyAssignmentReport:
            events.append("assign")
            self.assertEqual(list(characters), [character])
            self.assertEqual(
                world_map,
                {
                    "city_id": None,
                    "city_template": "测试城市",
                    "districts": ["district-object"],
                    "streets": ["street-object"],
                    "district_map": {"一区": 3},
                },
            )
            self.assertEqual(seed, 88)
            return empty_report

        with patch(
            "world_generation.generation_pipeline.generate_city",
            side_effect=fake_generate,
        ), patch(
            "world_generation.generation_pipeline.assign_family_relationships",
            side_effect=fake_assign,
        ):
            result = generate_city_with_relationships(100, seed=88)

        self.assertEqual(events, ["generate", "assign"])
        self.assertEqual(result["family_relationships"], empty_report.as_dict())


if __name__ == "__main__":
    unittest.main()
