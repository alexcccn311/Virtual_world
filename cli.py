"""Command-line entry point for complete city population generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .generation_pipeline import generate_city_with_relationships
from .storage.sqlite_store import SQLiteWorldStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "生成城市角色，在全部角色完成后建立家庭关系与地址，"
            "最后生成世界统计报告。"
        )
    )
    parser.add_argument(
        "--target-population",
        type=int,
        required=True,
        help="目标城市人口。",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "reference_city.sqlite3",
        help="输出 SQLite 路径；已有数据库会新增一座城市，不删除旧数据。",
    )
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--city-template", default="罪恶都市")
    parser.add_argument(
        "--statistics-report",
        type=Path,
        help=(
            "统计报告 JSON 路径；默认写到数据库旁的 "
            "<数据库名>_statistics.json。"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.target_population < 1:
        raise SystemExit("--target-population 必须为正整数")
    store = SQLiteWorldStore(args.database)
    result = generate_city_with_relationships(
        args.target_population,
        args.seed,
        city_template=args.city_template,
        store=store,
        statistics_report_path=(
            str(args.statistics_report) if args.statistics_report else None
        ),
    )
    print(
        json.dumps(
            {
                "database": str(args.database.resolve()),
                "city_id": result["city_id"],
                "sex_worker_population": result["sex_worker_population"],
                "sex_worker_levels": result["sex_worker_levels"],
                "family_relationships": result["family_relationships"],
                "statistics_report": result["statistics_report"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


__all__ = ["main"]
