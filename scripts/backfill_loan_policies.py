"""Backfill fixed lender policies into worlds generated before loan persistence."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from world_generation import config


def _templates() -> dict[str, Mapping[str, object]]:
    return {
        name: template
        for templates in config.ORGANIZATION_TEMPLATES.values()
        for name, template in templates.items()
        if isinstance(template, Mapping) and template.get("offers_loans") is True
    }


def _fixed_limit(
    city_seed: int,
    organization_id: str,
    configured: object,
) -> int | None:
    if configured is None:
        return None
    low, high = configured
    digest = hashlib.blake2b(
        f"{city_seed}:{organization_id}:loan-policy".encode("utf-8"),
        digest_size=16,
    ).digest()
    return random.Random(int.from_bytes(digest, "big")).randint(int(low), int(high))


def backfill_loan_policies(database: str | Path) -> int:
    database = Path(database)
    templates = _templates()
    connection = sqlite3.connect(database, timeout=60.0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT organization.organization_id,
                   organization.template_name,
                   organization.data_json,
                   city.seed
            FROM organizations AS organization
            JOIN cities AS city ON city.city_id = organization.city_id
            """
        ).fetchall()
        updated = 0
        for organization_id, template_name, data_json, city_seed in rows:
            template = templates.get(str(template_name))
            if template is None:
                continue
            payload = json.loads(data_json)
            if not isinstance(payload, dict):
                raise ValueError(f"组织 {organization_id} 的 data_json 不是对象")
            if "offers_loans" in payload:
                continue
            limit = _fixed_limit(
                int(city_seed),
                str(organization_id),
                template.get("max_total_debt"),
            )
            payload["offers_loans"] = True
            payload["max_total_debt"] = limit
            payload["loan_blackness"] = (
                1.0
                if limit is None
                else limit / (limit + config.LOAN_DEBT_SCALE)
            )
            connection.execute(
                "UPDATE organizations SET data_json = ? WHERE organization_id = ?",
                (
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    str(organization_id),
                ),
            )
            updated += 1
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    updated = backfill_loan_policies(args.database)
    print(f"已补齐 {updated} 家贷款机构的固定放贷参数：{args.database}")


if __name__ == "__main__":
    main()
