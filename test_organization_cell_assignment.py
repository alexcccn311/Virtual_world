from __future__ import annotations

import unittest
import random

from world_generation.generators.city_generator import _OrganizationCellAllocator
from world_generation.generators.organization_generator import OrganizationGenerator
from world_generation.models.entities import Street


def _street(name: str, cells: tuple[tuple[int, int], ...]) -> Street:
    return Street(
        name=name,
        district="测试区",
        seed=cells[0],
        cells=cells,
        area=1.0,
        centroid=(0.0, 0.0),
        neighbors=(),
        prosperity=50,
    )


class OrganizationCellAllocatorTests(unittest.TestCase):
    def test_assignments_are_stable_valid_and_balanced(self) -> None:
        streets = [
            _street("甲街", ((0, 0), (1, 0), (2, 0))),
            _street("乙街", ((5, 5), (5, 6))),
        ]
        first = _OrganizationCellAllocator(streets, seed=12345)
        second = _OrganizationCellAllocator(streets, seed=12345)
        first_cells = [first("甲街", f"ORG-{index:08d}") for index in range(10)]
        second_cells = [second("甲街", f"ORG-{index:08d}") for index in range(10)]

        self.assertEqual(first_cells, second_cells)
        self.assertTrue(set(first_cells) <= set(streets[0].cells))
        counts = {cell: first_cells.count(cell) for cell in streets[0].cells}
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        self.assertIn(first("乙街", "ORG-OTHER"), streets[1].cells)

    def test_unknown_street_is_rejected(self) -> None:
        allocator = _OrganizationCellAllocator(
            [_street("甲街", ((0, 0),))],
            seed=1,
        )
        with self.assertRaisesRegex(ValueError, "没有可用 cell"):
            allocator("不存在的街", "ORG-1")

    def test_organization_generator_persists_allocated_address(self) -> None:
        generator = OrganizationGenerator(
            random.Random(7),
            organization_cell_allocator=lambda street, organization_id: (4, -2),
        )
        organization = generator.generate(
            "测试区",
            "测试街",
            "adult_hair_salon",
            {"测试区": 1},
        )
        self.assertEqual(organization["address"], (4, -2))

    def test_lender_generation_persists_fixed_policy(self) -> None:
        generator = OrganizationGenerator(random.Random(17))
        organization = generator.generate(
            "测试区",
            "测试街",
            "neighborhood_lender",
            {"测试区": 1},
        )

        self.assertTrue(organization["offers_loans"])
        self.assertGreaterEqual(organization["max_total_debt"], 80_000)
        self.assertLessEqual(organization["max_total_debt"], 200_000)
        expected = organization["max_total_debt"] / (
            organization["max_total_debt"] + 200_000
        )
        self.assertAlmostEqual(organization["loan_blackness"], expected)

    def test_crime_lender_generation_has_no_debt_limit(self) -> None:
        generator = OrganizationGenerator(random.Random(19))
        organization = generator.generate(
            "测试区",
            "测试街",
            "loan_shark_chain_store_1",
            {"测试区": 1},
            parent_name="测试集团",
        )

        self.assertTrue(organization["offers_loans"])
        self.assertIsNone(organization["max_total_debt"])
        self.assertEqual(organization["loan_blackness"], 1.0)


if __name__ == "__main__":
    unittest.main()
