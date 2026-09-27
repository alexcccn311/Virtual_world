"""Build the fixed Level 0 population reference used by body_score."""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = PROJECT_DIR.parent
if str(REPOSITORY_DIR) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_DIR))

from world_generation.generators.character_generator import (  # noqa: E402
    BODY_SCORE_FORMULA_VERSION,
    BODY_SCORE_MODEL_VERSION,
    calculate_raw_body_score,
    generate_body_measurements,
)


RANDOM_SEED = 20260923
REPETITIONS = 100
AGE_GROUPS = (
    ("18-19", 18, 19, 1_000),
    ("20-29", 20, 29, 5_000),
    ("30-39", 30, 39, 1_000),
    ("40-49", 40, 49, 1_000),
    ("50-59", 50, 59, 1_000),
    ("60-69", 60, 69, 1_000),
    ("70-79", 70, 79, 1_000),
)
PERCENTILES = (0.05, 0.25, 0.50, 0.75, 0.95)
REFERENCE_PATH = PROJECT_DIR / "data" / "body_score_reference.json"
STATISTICS_PATH = PROJECT_DIR / "reports" / "body_score_reference_statistics.csv"


def percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return (
        sorted_values[lower_index]
        + fraction * (sorted_values[upper_index] - sorted_values[lower_index])
    )


def summarize(values: list[float]) -> dict[str, float]:
    sorted_values = sorted(values)
    summary = {
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values),
    }
    summary.update(
        {
            f"p{round(probability * 100):02d}": percentile(
                sorted_values, probability
            )
            for probability in PERCENTILES
        }
    )
    return summary


def calibration_probabilities() -> list[float]:
    probabilities = {index / 5_000 for index in range(5_001)}
    probabilities.update(0.99 + index / 100_000 for index in range(1_001))
    return sorted(min(1.0, probability) for probability in probabilities)


def build_sex_reference(
    sex: str,
    seed: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    rng = random.Random(seed)
    overall_scores: list[float] = []
    rows: list[dict[str, object]] = []

    for label, minimum_age, maximum_age, count_per_repeat in AGE_GROUPS:
        group_scores: list[float] = []
        age_span = maximum_age - minimum_age + 1
        base_ages = [
            minimum_age + index % age_span
            for index in range(count_per_repeat)
        ]
        for _ in range(REPETITIONS):
            ages = base_ages.copy()
            rng.shuffle(ages)
            for age in ages:
                height, bmi, _, bust, waist, hips = generate_body_measurements(
                    rng,
                    sex,
                    age,
                    0.0,
                    "普通职员",
                )
                group_scores.append(
                    calculate_raw_body_score(
                        sex,
                        height,
                        bmi,
                        bust,
                        waist,
                        hips,
                    )
                )
        overall_scores.extend(group_scores)
        rows.append(
            {
                "sex": sex,
                "age_group": label,
                "sample_count": len(group_scores),
                **summarize(group_scores),
            }
        )

    overall_summary = summarize(overall_scores)
    rows.append(
        {
            "sex": sex,
            "age_group": "overall",
            "sample_count": len(overall_scores),
            **overall_summary,
        }
    )

    sorted_scores = sorted(overall_scores)
    probabilities = calibration_probabilities()
    raw_score_nodes = [
        percentile(sorted_scores, probability)
        for probability in probabilities
    ]
    return (
        {
            "sample_count": len(overall_scores),
            "raw_scores": [round(value, 8) for value in raw_score_nodes],
            "cumulative_probabilities": probabilities,
            "overall_statistics": {
                key: round(value, 8)
                for key, value in overall_summary.items()
            },
        },
        rows,
    )


def main() -> None:
    references: dict[str, object] = {}
    statistic_rows: list[dict[str, object]] = []
    for offset, sex in enumerate(("female", "male")):
        references[sex], rows = build_sex_reference(
            sex,
            RANDOM_SEED + offset,
        )
        statistic_rows.extend(rows)

    reference = {
        "formula_version": BODY_SCORE_FORMULA_VERSION,
        "body_model_version": BODY_SCORE_MODEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "random_seed": RANDOM_SEED,
        "repetitions": REPETITIONS,
        "sample_count_per_sex": 1_100_000,
        "age_groups": [
            {
                "label": label,
                "minimum_age": minimum_age,
                "maximum_age": maximum_age,
                "count_per_repeat": count,
            }
            for label, minimum_age, maximum_age, count in AGE_GROUPS
        ],
        "mapping": {
            "base_probability_step": 0.0002,
            "tail_start": 0.99,
            "tail_probability_step": 0.00001,
        },
        "sexes": references,
    }

    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATISTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REFERENCE_PATH.open("w", encoding="utf-8") as reference_file:
        json.dump(reference, reference_file, ensure_ascii=False, indent=2)
        reference_file.write("\n")

    fieldnames = (
        "sex", "age_group", "sample_count", "mean", "std",
        "p05", "p25", "p50", "p75", "p95",
    )
    with STATISTICS_PATH.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in statistic_rows:
            writer.writerow(
                {
                    key: round(value, 8) if isinstance(value, float) else value
                    for key, value in row.items()
                }
            )

    print(f"wrote {REFERENCE_PATH}")
    print(f"wrote {STATISTICS_PATH}")


if __name__ == "__main__":
    main()
