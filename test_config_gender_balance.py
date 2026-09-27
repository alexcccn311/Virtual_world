from __future__ import annotations

import unittest

from world_generation import config


class OrganizationRoleGenderBalanceTests(unittest.TestCase):
    def test_only_balanced_roles_receive_the_male_weight_multiplier(self) -> None:
        roles = config.ORGANIZATION_TEMPLATES["city"][
            "crime_syndicate_city_level_1"
        ]["roles"]

        self.assertGreater(config.BALANCED_ROLE_SEX_ADJUSTED_COUNT, 400)
        self.assertEqual(
            roles["administration_director"]["sex"],
            {"male": 4.4, "female": 0.9},
        )
        self.assertEqual(
            roles["leader"]["sex"],
            {"male": 0.1, "female": 1.9},
        )
        self.assertEqual(
            roles["personal_secretary"]["sex"],
            {"male": 0.0, "female": 1.0},
        )

    def test_adjustment_never_removes_a_roles_female_weight(self) -> None:
        for templates in config.ORGANIZATION_TEMPLATES.values():
            for template in templates.values():
                for role in template.get("roles", {}).values():
                    weights = role.get("sex")
                    if isinstance(weights, dict):
                        self.assertGreater(weights["female"], 0)


if __name__ == "__main__":
    unittest.main()
