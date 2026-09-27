"""Regression tests for the interactive portrait review loop."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from world_generation.scripts import interactive_character_portrait as portrait_script  # noqa: E402


class InteractivePortraitReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = SimpleNamespace(database=Path("fixture.sqlite3"))

    def test_r_input_maps_to_reroll(self) -> None:
        with patch("builtins.input", return_value="r"):
            self.assertEqual(portrait_script._review_choice(), "reroll")

    def test_reroll_draws_another_character_instead_of_returning(self) -> None:
        first = {"source_character_id": "CUS-1"}
        second = {"source_character_id": "CUS-2"}
        draw = Mock(side_effect=[first, second])
        with (
            patch.object(portrait_script, "draw_reference_character", draw),
            patch.object(
                portrait_script,
                "build_character_portrait_prompt",
                side_effect=["prompt-1", "prompt-2"],
            ),
            patch.object(portrait_script, "_print_candidate"),
            patch.object(
                portrait_script,
                "_review_choice",
                side_effect=["reroll", "generate"],
            ),
        ):
            reviewed = portrait_script.review_until_confirmed(
                self.world,
                "随机",
                rng=random.Random(7),
            )

        self.assertEqual(reviewed, (second, "prompt-2"))
        self.assertEqual(draw.call_count, 2)

    def test_exhausted_reroll_pool_resets_instead_of_exiting(self) -> None:
        only_character = {"source_character_id": "CUS-1"}
        draw = Mock(side_effect=[only_character, LookupError("没有更多角色"), only_character])
        with (
            patch.object(portrait_script, "draw_reference_character", draw),
            patch.object(
                portrait_script,
                "build_character_portrait_prompt",
                return_value="prompt",
            ),
            patch.object(portrait_script, "_print_candidate"),
            patch.object(
                portrait_script,
                "_review_choice",
                side_effect=["reroll", "quit"],
            ),
        ):
            reviewed = portrait_script.review_until_confirmed(
                self.world,
                "随机",
            )

        self.assertIsNone(reviewed)
        self.assertEqual(draw.call_count, 3)


if __name__ == "__main__":
    unittest.main()
