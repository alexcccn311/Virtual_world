"""Verify family and address invariants in a generated city database."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from world_generation.generators.character_generator import SEX_SERVICE_PROFESSIONS
from world_generation.generators.sex_worker_level_generator import (
    sex_worker_minimum_level,
)
from world_generation.services.sex_service import sex_worker_appeal_score


def verify(database: str | Path) -> dict[str, object]:
    path = Path(database)
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        result: dict[str, object] = {
            "database": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "integrity": connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0],
            "cities": connection.execute(
                """
                SELECT city_id, status, target_population
                FROM cities ORDER BY city_id
                """
            ).fetchall(),
            "character_count": connection.execute(
                "SELECT count(*) FROM characters"
            ).fetchone()[0],
            "organization_count": connection.execute(
                "SELECT count(*) FROM organizations"
            ).fetchone()[0],
            "missing_or_invalid_organization_address_count": connection.execute(
                """
                SELECT count(*) FROM organizations
                WHERE json_extract(data_json, '$.address') IS NULL
                   OR json_array_length(
                       json_extract(data_json, '$.address')
                   ) != 2
                """
            ).fetchone()[0],
            "missing_family_count": connection.execute(
                """
                SELECT count(*) FROM characters
                WHERE json_extract(data_json, '$.relation.family_id') IS NULL
                """
            ).fetchone()[0],
            "missing_or_invalid_address_count": connection.execute(
                """
                SELECT count(*) FROM characters
                WHERE json_extract(data_json, '$.address') IS NULL
                   OR json_array_length(
                       json_extract(data_json, '$.address')
                   ) != 2
                """
            ).fetchone()[0],
            "invalid_family_size_group_count": connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT
                        json_extract(
                            data_json, '$.relation.family_id'
                        ) AS family_id,
                        count(*) AS actual_size,
                        min(json_extract(
                            data_json, '$.relation.family_size'
                        )) AS declared_min,
                        max(json_extract(
                            data_json, '$.relation.family_size'
                        )) AS declared_max
                    FROM characters
                    GROUP BY family_id
                    HAVING actual_size != declared_min
                        OR declared_min != declared_max
                        OR actual_size NOT IN (1, 3, 4, 5)
                )
                """
            ).fetchone()[0],
            "split_family_address_count": connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT json_extract(
                        data_json, '$.relation.family_id'
                    ) AS family_id
                    FROM characters
                    GROUP BY family_id
                    HAVING count(DISTINCT json_extract(
                        data_json, '$.address'
                    )) != 1
                )
                """
            ).fetchone()[0],
        }
        connection.execute(
            """
            CREATE TEMP TABLE valid_cells(
                q INTEGER,
                r INTEGER,
                PRIMARY KEY(q, r)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO valid_cells(q, r)
            SELECT
                json_extract(cell.value, '$[0]'),
                json_extract(cell.value, '$[1]')
            FROM streets, json_each(streets.data_json, '$.cells') AS cell
            """
        )
        result["valid_map_cell_count"] = connection.execute(
            "SELECT count(*) FROM valid_cells"
        ).fetchone()[0]
        result["off_map_address_count"] = connection.execute(
            """
            SELECT count(*)
            FROM characters AS character
            LEFT JOIN valid_cells AS cell
              ON cell.q = json_extract(character.data_json, '$.address[0]')
             AND cell.r = json_extract(character.data_json, '$.address[1]')
            WHERE cell.q IS NULL
            """
        ).fetchone()[0]
        result["off_map_organization_address_count"] = connection.execute(
            """
            SELECT count(*)
            FROM organizations AS organization
            LEFT JOIN valid_cells AS cell
              ON cell.q = json_extract(organization.data_json, '$.address[0]')
             AND cell.r = json_extract(organization.data_json, '$.address[1]')
            WHERE cell.q IS NULL
            """
        ).fetchone()[0]
        result["organization_address_outside_street_count"] = connection.execute(
            """
            SELECT count(*)
            FROM organizations AS organization
            JOIN streets AS street
              ON street.city_id = organization.city_id
             AND street.name = organization.street_name
            WHERE NOT EXISTS (
                SELECT 1
                FROM json_each(street.data_json, '$.cells') AS cell
                WHERE json_extract(cell.value, '$[0]')
                      = json_extract(organization.data_json, '$.address[0]')
                  AND json_extract(cell.value, '$[1]')
                      = json_extract(organization.data_json, '$.address[1]')
            )
            """
        ).fetchone()[0]
        placeholders = ",".join("?" for _ in SEX_SERVICE_PROFESSIONS)
        sex_worker_rows = connection.execute(
            f"""
            SELECT
                character.character_id,
                character.occupation,
                organization.template_name,
                json_extract(character.data_json, '$.appearance_score'),
                json_extract(character.data_json, '$.body_score'),
                json_extract(character.data_json, '$.sex_worker_score'),
                json_extract(character.data_json, '$.sex_worker_level'),
                json_extract(character.data_json, '$.sex_worker_minimum_level')
            FROM characters AS character
            JOIN organizations AS organization
              ON organization.organization_id = character.organization_id
            WHERE character.occupation IN ({placeholders})
            """,
            tuple(sorted(SEX_SERVICE_PROFESSIONS)),
        ).fetchall()
        missing_levels = 0
        minimum_violations = 0
        configured_minimum_mismatches = 0
        score_mismatches = 0
        scores_by_level: dict[int, list[float]] = {}
        for (
            _, occupation, venue_type, appearance_score, body_score,
            stored_score, level, minimum_level,
        ) in sex_worker_rows:
            if stored_score is None or level is None or minimum_level is None:
                missing_levels += 1
                continue
            level = int(level)
            minimum_level = int(minimum_level)
            expected_minimum = sex_worker_minimum_level(
                str(venue_type),
                str(occupation),
            )
            if minimum_level != expected_minimum:
                configured_minimum_mismatches += 1
            if level < minimum_level:
                minimum_violations += 1
            expected_score = sex_worker_appeal_score(
                float(appearance_score),
                float(body_score),
            )
            if not abs(float(stored_score) - expected_score) <= 0.0001:
                score_mismatches += 1
            scores_by_level.setdefault(level, []).append(float(stored_score))
        score_order_violations = 0
        ordered_levels = sorted(scores_by_level)
        for lower, higher in zip(ordered_levels, ordered_levels[1:]):
            if max(scores_by_level[lower]) > min(scores_by_level[higher]):
                score_order_violations += 1
        result["sex_worker_levels"] = {
            "count": len(sex_worker_rows),
            "missing_count": missing_levels,
            "minimum_level_violation_count": minimum_violations,
            "configured_minimum_mismatch_count": configured_minimum_mismatches,
            "score_mismatch_count": score_mismatches,
            "score_order_violation_count": score_order_violations,
            "levels": {
                str(level): {
                    "count": len(scores),
                    "minimum_score": min(scores),
                    "maximum_score": max(scores),
                }
                for level, scores in sorted(scores_by_level.items())
            },
        }
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    result = verify(args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failures = (
        result["integrity"] != "ok"
        or any(
            result[field] != 0
            for field in (
                "missing_family_count",
                "missing_or_invalid_organization_address_count",
                "missing_or_invalid_address_count",
                "invalid_family_size_group_count",
                "split_family_address_count",
                "off_map_address_count",
                "off_map_organization_address_count",
                "organization_address_outside_street_count",
            )
        )
        or any(
            result["sex_worker_levels"][field] != 0
            for field in (
                "missing_count",
                "minimum_level_violation_count",
                "configured_minimum_mismatch_count",
                "score_mismatch_count",
                "score_order_violation_count",
            )
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
