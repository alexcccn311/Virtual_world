"""Build a categorized statistics report from a completed world database."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .character_generator import (
    SEX_SERVICE_PROFESSIONS,
    calculate_asset_level,
)


REPORT_VERSION = 1

OCCUPATION_CATEGORY_LABELS = {
    "DAILY_OCCUPATIONS": "日常职业",
    "PROFESSIONAL_OCCUPATIONS": "专业职业",
    "FREELANCER": "自由职业",
    "GREY_OCCUPATIONS": "灰色职业",
    "SEX_SERVICE_OCCUPATIONS": "性服务职业",
    "ILLEGAL_OCCUPATIONS": "非法职业",
}

ORGANIZATION_SCOPE_LABELS = {
    "city": "城市级组织",
    "district": "城区级组织",
    "street": "街道级组织",
    "sub_organization": "下属组织",
}

INDUSTRY_LABELS = {
    "adult_entertainment": "成人娱乐",
    "corporate": "公司企业",
    "education": "教育",
    "entertainment": "娱乐",
    "finance": "金融",
    "gambling": "博彩",
    "government": "政府公共部门",
    "healthcare": "医疗",
    "hospitality": "酒店餐饮",
    "illegal": "非法产业",
    "logistics": "物流仓储",
    "manufacturing": "工业制造",
    "public": "公共服务",
    "retail": "零售",
    "services": "生活服务",
    "underground": "地下产业",
    "underground_finance": "地下金融",
    "unknown": "未分类",
}


def default_statistics_report_path(database_path: str | Path) -> Path:
    """Return the report path paired with a world SQLite database."""

    database = Path(database_path)
    return database.with_name(f"{database.stem}_statistics.json")


def _percentage(count: int, total: int) -> float:
    return round(count * 100.0 / total, 4) if total else 0.0


def _distribution(
    counts: Mapping[str, int] | Counter[str],
    *,
    total: int | None = None,
    preferred_order: Iterable[str] = (),
) -> dict[str, object]:
    normalized = {str(key): int(value) for key, value in counts.items()}
    denominator = sum(normalized.values()) if total is None else int(total)
    ordered_keys: list[str] = []
    for key in preferred_order:
        if key in normalized and key not in ordered_keys:
            ordered_keys.append(key)
    ordered_keys.extend(
        key
        for key, _ in sorted(
            normalized.items(), key=lambda item: (-item[1], item[0])
        )
        if key not in ordered_keys
    )
    return {
        "total": denominator,
        "counts": {key: normalized[key] for key in ordered_keys},
        "percentages": {
            key: _percentage(normalized[key], denominator) for key in ordered_keys
        },
    }


def _number_band(value: float, bands: tuple[tuple[str, float | None], ...]) -> str:
    for label, upper_bound in bands:
        if upper_bound is None or value < upper_bound:
            return label
    raise AssertionError("number bands must end with an open upper bound")


AGE_BANDS = (
    ("未满20岁", 20),
    ("20-29岁", 30),
    ("30-39岁", 40),
    ("40-49岁", 50),
    ("50-59岁", 60),
    ("60-69岁", 70),
    ("70岁及以上", None),
)

INCOME_BANDS = (
    ("低于3000", 3_000),
    ("3000-4999", 5_000),
    ("5000-7999", 8_000),
    ("8000-11999", 12_000),
    ("12000-24999", 25_000),
    ("25000-99999", 100_000),
    ("100000及以上", None),
)

NET_ASSET_BANDS = (
    ("低于-1000000", -1_000_000),
    ("-1000000至-100001", -100_000),
    ("-100000至-10001", -10_000),
    ("-10000至-1", 0),
    ("0至9999", 10_000),
    ("10000至99999", 100_000),
    ("100000至999999", 1_000_000),
    ("1000000至9999999", 10_000_000),
    ("10000000至99999999", 100_000_000),
    ("100000000及以上", None),
)

DEBT_BANDS = (
    ("无负债", 1),
    ("1至9999", 10_000),
    ("10000至99999", 100_000),
    ("100000至999999", 1_000_000),
    ("1000000及以上", None),
)

SCORE_BANDS = (
    ("0-39", 40),
    ("40-49", 50),
    ("50-59", 60),
    ("60-69", 70),
    ("70-79", 80),
    ("80-89", 90),
    ("90-100", None),
)

ASSET_LEVEL_BANDS = (
    ("-1.00至-0.70（重度负资产）", -0.70),
    ("-0.70至-0.40（中度负资产）", -0.40),
    ("-0.40至0（轻度负资产）", 0.0),
    ("0（资产负债相抵）", 1e-15),
    ("0至0.40（低资产）", 0.40),
    ("0.40至0.70（中资产）", 0.70),
    ("0.70至1.00（高资产）", None),
)

HOUSEHOLDS_PER_CELL_BANDS = (
    ("1户", 2),
    ("2-5户", 6),
    ("6-10户", 11),
    ("11-25户", 26),
    ("26-50户", 51),
    ("51户及以上", None),
)

ORGANIZATION_SIZE_BANDS = (
    ("0人", 1),
    ("1-9人", 10),
    ("10-29人", 30),
    ("30-99人", 100),
    ("100-299人", 300),
    ("300-999人", 1_000),
    ("1000人及以上", None),
)


def _occupation_catalog() -> tuple[
    dict[str, tuple[str, str]],
    list[tuple[str, str, list[tuple[str, list[str]]]]],
]:
    paths: dict[str, tuple[str, str]] = {}
    categories: list[tuple[str, str, list[tuple[str, list[str]]]]] = []
    for category_key, configured in config.OCCUPATION_TEMPLATES.items():
        label = OCCUPATION_CATEGORY_LABELS.get(category_key, category_key)
        groups: list[tuple[str, list[str]]] = []
        if isinstance(configured, Mapping):
            source_groups = configured.items()
        else:
            source_groups = (("未细分", configured),)
        for subgroup, occupations in source_groups:
            values = list(occupations)
            groups.append((str(subgroup), values))
            for occupation in values:
                if occupation in paths:
                    previous = paths[occupation]
                    raise ValueError(
                        f"职业“{occupation}”同时属于 {previous} 和 "
                        f"({category_key}, {subgroup})"
                    )
                paths[occupation] = (category_key, str(subgroup))
        categories.append((category_key, label, groups))
    return paths, categories


def _organization_catalog() -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for scope, templates in config.ORGANIZATION_TEMPLATES.items():
        for template_name, template in templates.items():
            result[(scope, template_name)] = str(template.get("industry", "unknown"))
    return result


def _occupation_report(
    counts: Counter[str], population: int
) -> dict[str, object]:
    paths, catalog = _occupation_catalog()
    categories: list[dict[str, object]] = []
    for category_key, category_label, groups in catalog:
        category_total = sum(
            counts[occupation]
            for _, occupations in groups
            for occupation in occupations
        )
        subgroup_rows: list[dict[str, object]] = []
        for subgroup, occupations in groups:
            subgroup_total = sum(counts[occupation] for occupation in occupations)
            occupation_rows = [
                {
                    "occupation": occupation,
                    "count": counts[occupation],
                    "percentage_of_category": _percentage(
                        counts[occupation], category_total
                    ),
                    "percentage_of_population": _percentage(
                        counts[occupation], population
                    ),
                }
                for occupation in sorted(
                    occupations, key=lambda item: (-counts[item], item)
                )
            ]
            subgroup_rows.append({
                "subcategory": subgroup,
                "count": subgroup_total,
                "occupations": occupation_rows,
            })
        categories.append({
            "category": category_key,
            "label": category_label,
            "count": category_total,
            "percentage": _percentage(category_total, population),
            "subcategories": subgroup_rows,
        })

    unknown = sorted(
        (
            {
                "occupation": occupation,
                "count": count,
                "percentage_of_population": _percentage(count, population),
            }
            for occupation, count in counts.items()
            if occupation not in paths
        ),
        key=lambda row: (-int(row["count"]), str(row["occupation"])),
    )
    return {
        "total_characters": population,
        "distinct_occupations_present": len(counts),
        "configured_occupations": len(paths),
        "categories": categories,
        "unclassified_occupations": unknown,
    }


def _organization_report(
    organization_rows: list[sqlite3.Row],
    member_counts: Counter[str],
) -> dict[str, object]:
    catalog = _organization_catalog()
    scope_counts: Counter[str] = Counter()
    industry_counts: Counter[str] = Counter()
    industry_members: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    template_stats: dict[
        tuple[str, str, str], dict[str, int]
    ] = defaultdict(lambda: {"organizations": 0, "characters": 0})

    for row in organization_rows:
        scope = str(row["scope"])
        template = str(row["template_name"])
        organization_id = str(row["organization_id"])
        industry = catalog.get((scope, template), "unknown")
        members = member_counts[organization_id]
        scope_counts[scope] += 1
        industry_counts[industry] += 1
        industry_members[industry] += members
        size_counts[_number_band(members, ORGANIZATION_SIZE_BANDS)] += 1
        stats = template_stats[(industry, scope, template)]
        stats["organizations"] += 1
        stats["characters"] += members

    industries: list[dict[str, object]] = []
    for industry in sorted(
        industry_counts, key=lambda key: (-industry_counts[key], key)
    ):
        templates = []
        for (row_industry, scope, template), values in sorted(
            template_stats.items(),
            key=lambda item: (
                -item[1]["organizations"], item[0][1], item[0][2]
            ),
        ):
            if row_industry != industry:
                continue
            templates.append({
                "template_name": template,
                "scope": scope,
                "scope_label": ORGANIZATION_SCOPE_LABELS.get(scope, scope),
                **values,
            })
        industries.append({
            "industry": industry,
            "label": INDUSTRY_LABELS.get(industry, industry),
            "organizations": industry_counts[industry],
            "characters": industry_members[industry],
            "templates": templates,
        })

    return {
        "total_organizations": len(organization_rows),
        "characters_in_organizations": sum(member_counts.values()),
        "by_scope": _distribution(scope_counts),
        "by_size": _distribution(
            size_counts,
            preferred_order=(label for label, _ in ORGANIZATION_SIZE_BANDS),
        ),
        "industries": industries,
    }


def _family_report(
    *,
    population: int,
    family_sizes: Mapping[tuple[str, str], int],
    family_addresses: Mapping[tuple[str, str], str],
    family_roles: Counter[str],
    single_person_genders: Counter[str],
    single_person_ages: Counter[str],
    unassigned_characters: int,
    addressed_characters: int,
    inconsistent_family_addresses: int,
) -> dict[str, object]:
    size_counts = Counter(str(size) for size in family_sizes.values())
    household_counts = Counter(family_addresses.values())
    density_counts = Counter(
        _number_band(count, HOUSEHOLDS_PER_CELL_BANDS)
        for count in household_counts.values()
    )
    return {
        "family_count": len(family_sizes),
        "family_members": population - unassigned_characters,
        "unassigned_characters": unassigned_characters,
        "single_person_families": size_counts["1"],
        "single_person_by_gender": _distribution(
            single_person_genders,
            total=size_counts["1"],
        ),
        "single_person_by_age": _distribution(
            single_person_ages,
            total=size_counts["1"],
            preferred_order=(label for label, _ in AGE_BANDS),
        ),
        "by_family_size": _distribution(
            size_counts,
            total=len(family_sizes),
            preferred_order=("1", "2", "3", "4", "5"),
        ),
        "by_family_role": _distribution(family_roles),
        "addressed_characters": addressed_characters,
        "families_with_address": len(family_addresses),
        "families_without_address": len(family_sizes) - len(family_addresses),
        "families_with_inconsistent_member_addresses": inconsistent_family_addresses,
        "occupied_map_cells": len(household_counts),
        "households_per_occupied_cell": {
            "minimum": min(household_counts.values(), default=0),
            "maximum": max(household_counts.values(), default=0),
            "mean": round(
                sum(household_counts.values()) / len(household_counts), 4
            ) if household_counts else 0.0,
            "distribution": _distribution(
                density_counts,
                total=len(household_counts),
                preferred_order=(
                    label for label, _ in HOUSEHOLDS_PER_CELL_BANDS
                ),
            ),
        },
    }


def build_world_statistics_report(database_path: str | Path) -> dict[str, object]:
    """Aggregate all completed cities in one world database.

    Character rows are streamed once.  This keeps reporting memory bounded by
    the family and organization indexes instead of the total serialized data.
    """

    database = Path(database_path)
    if not database.is_file():
        raise FileNotFoundError(f"世界数据库不存在：{database}")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        city_rows = connection.execute(
            """
            SELECT city_id, city_template, seed, target_population,
                   created_at, completed_at, data_json
            FROM cities
            WHERE status = 'complete'
            ORDER BY city_id
            """
        ).fetchall()
        if not city_rows:
            raise ValueError(f"世界数据库中没有生成完成的城市：{database}")

        organization_rows = connection.execute(
            """
            SELECT o.organization_id, o.city_id, o.template_name, o.scope,
                   o.district_name, o.street_name
            FROM organizations AS o
            JOIN cities AS c ON c.city_id = o.city_id
            WHERE c.status = 'complete'
            ORDER BY o.organization_id
            """
        ).fetchall()
        organization_type_by_id = {
            str(row["organization_id"]): str(row["template_name"])
            for row in organization_rows
        }
        district_rows = connection.execute(
            """
            SELECT d.city_id, d.name, d.template_name, d.level,
                   d.population, d.prosperity
            FROM districts AS d
            JOIN cities AS c ON c.city_id = d.city_id
            WHERE c.status = 'complete'
            ORDER BY d.city_id, d.name
            """
        ).fetchall()
        street_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM streets AS s
            JOIN cities AS c ON c.city_id = s.city_id
            WHERE c.status = 'complete'
            """
        ).fetchone()[0]

        gender_counts: Counter[str] = Counter()
        age_counts: Counter[str] = Counter()
        occupation_counts: Counter[str] = Counter()
        title_counts: Counter[str] = Counter()
        education_counts: Counter[str] = Counter()
        hygiene_counts: Counter[str] = Counter()
        temperament_counts: Counter[str] = Counter()
        asset_level_counts: Counter[str] = Counter()
        income_counts: Counter[str] = Counter()
        net_asset_counts: Counter[str] = Counter()
        debt_counts: Counter[str] = Counter()
        face_score_counts: Counter[str] = Counter()
        body_score_counts: Counter[str] = Counter()
        appearance_score_counts: Counter[str] = Counter()
        city_counts: Counter[str] = Counter()
        district_counts: Counter[str] = Counter()
        street_counts: Counter[str] = Counter()
        sex_worker_city_counts: Counter[str] = Counter()
        sex_worker_level_counts: Counter[str] = Counter()
        sex_worker_minimum_level_counts: Counter[str] = Counter()
        sex_worker_levels_by_occupation: dict[str, Counter[str]] = defaultdict(Counter)
        sex_worker_levels_by_venue_type: dict[str, Counter[str]] = defaultdict(Counter)
        sex_worker_missing_levels = 0
        sex_worker_minimum_level_violations = 0
        member_counts: Counter[str] = Counter()
        family_roles: Counter[str] = Counter()
        single_person_genders: Counter[str] = Counter()
        single_person_ages: Counter[str] = Counter()
        family_sizes: dict[tuple[str, str], int] = defaultdict(int)
        family_addresses: dict[tuple[str, str], str] = {}
        inconsistent_family_keys: set[tuple[str, str]] = set()
        unassigned_characters = 0
        addressed_characters = 0
        population = 0

        query = """
            SELECT ch.city_id, ch.organization_id, ch.district_name,
                   ch.street_name, ch.occupation, ch.title,
                   json_extract(ch.data_json, '$.sex') AS gender,
                   json_extract(ch.data_json, '$.age') AS age,
                   json_extract(ch.data_json, '$.education') AS education,
                   json_extract(ch.data_json, '$.hygiene') AS hygiene,
                   json_extract(ch.data_json, '$.temperament') AS temperament,
                   json_extract(ch.data_json, '$.income') AS income,
                   json_extract(ch.data_json, '$.net_assets') AS net_assets,
                   json_extract(ch.data_json, '$.debt') AS debt,
                   json_extract(ch.data_json, '$.face_score') AS face_score,
                   json_extract(ch.data_json, '$.body_score') AS body_score,
                   json_extract(ch.data_json, '$.appearance_score') AS appearance_score,
                   json_extract(ch.data_json, '$.sex_worker_level') AS sex_worker_level,
                   json_extract(ch.data_json, '$.sex_worker_minimum_level') AS sex_worker_minimum_level,
                   json_extract(ch.data_json, '$.relation.family_id') AS family_id,
                   json_extract(ch.data_json, '$.relation.family_role') AS family_role,
                   json_extract(ch.data_json, '$.relation.family_size') AS family_size,
                   json_extract(ch.data_json, '$.address') AS address
            FROM characters AS ch
            JOIN cities AS c ON c.city_id = ch.city_id
            WHERE c.status = 'complete'
        """
        for row in connection.execute(query):
            population += 1
            city_id = str(row["city_id"])
            organization_id = str(row["organization_id"])
            occupation = str(row["occupation"])
            title = str(row["title"])
            district = str(row["district_name"])
            street = str(row["street_name"])
            gender = str(row["gender"] or "未填写")
            age = float(row["age"] or 0)
            income = float(row["income"] or 0)
            net_assets = float(row["net_assets"] or 0)
            debt = float(row["debt"] or 0)

            city_counts[city_id] += 1
            member_counts[organization_id] += 1
            occupation_counts[occupation] += 1
            if occupation in SEX_SERVICE_PROFESSIONS:
                sex_worker_city_counts[city_id] += 1
                level = row["sex_worker_level"]
                minimum_level = row["sex_worker_minimum_level"]
                if level is None or minimum_level is None:
                    sex_worker_missing_levels += 1
                else:
                    level_key = str(int(level))
                    minimum_key = str(int(minimum_level))
                    sex_worker_level_counts[level_key] += 1
                    sex_worker_minimum_level_counts[minimum_key] += 1
                    sex_worker_levels_by_occupation[occupation][level_key] += 1
                    venue_type = organization_type_by_id.get(
                        organization_id,
                        "未知场所",
                    )
                    sex_worker_levels_by_venue_type[venue_type][level_key] += 1
                    if int(level) < int(minimum_level):
                        sex_worker_minimum_level_violations += 1
            title_counts[title] += 1
            gender_counts[gender] += 1
            age_counts[_number_band(age, AGE_BANDS)] += 1
            income_counts[_number_band(income, INCOME_BANDS)] += 1
            net_asset_counts[_number_band(net_assets, NET_ASSET_BANDS)] += 1
            debt_counts[_number_band(debt, DEBT_BANDS)] += 1
            asset_level_counts[
                _number_band(calculate_asset_level(net_assets), ASSET_LEVEL_BANDS)
            ] += 1
            district_counts[f"{city_id}/{district}"] += 1
            street_counts[f"{city_id}/{district}/{street}"] += 1

            for field, counter in (
                (row["education"], education_counts),
                (row["hygiene"], hygiene_counts),
                (row["temperament"], temperament_counts),
            ):
                counter[str(field or "未填写")] += 1
            for field, counter in (
                (row["face_score"], face_score_counts),
                (row["body_score"], body_score_counts),
                (row["appearance_score"], appearance_score_counts),
            ):
                if field is not None:
                    counter[_number_band(float(field), SCORE_BANDS)] += 1

            family_id = row["family_id"]
            if not family_id:
                unassigned_characters += 1
                continue
            family_key = (city_id, str(family_id))
            family_sizes[family_key] += 1
            family_role = str(row["family_role"] or "未填写")
            family_roles[family_role] += 1
            if row["family_size"] == 1 or family_role == "single":
                single_person_genders[gender] += 1
                single_person_ages[_number_band(age, AGE_BANDS)] += 1
            address = row["address"]
            if address is not None:
                addressed_characters += 1
                address_text = str(address)
                previous = family_addresses.setdefault(family_key, address_text)
                if previous != address_text:
                    inconsistent_family_keys.add(family_key)

        city_names = {str(row["city_id"]): str(row["city_template"]) for row in city_rows}
        stored_sex_worker_controls: dict[str, dict[str, object]] = {}
        for row in city_rows:
            try:
                city_data = json.loads(row["data_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                city_data = {}
            control = city_data.get("sex_worker_population")
            if isinstance(control, dict):
                stored_sex_worker_controls[str(row["city_id"])] = control
        sex_worker_target = sum(
            math.ceil(
                int(row["target_population"])
                * config.SEX_WORKER_POPULATION_RATIO
            )
            for row in city_rows
        )
        sex_worker_actual = sum(sex_worker_city_counts.values())
        sex_worker_by_city = [
            {
                "city_id": str(row["city_id"]),
                "city_template": str(row["city_template"]),
                "configured_ratio": config.SEX_WORKER_POPULATION_RATIO,
                "target_minimum": math.ceil(
                    int(row["target_population"])
                    * config.SEX_WORKER_POPULATION_RATIO
                ),
                "actual_count": sex_worker_city_counts[str(row["city_id"])],
                "generation_control": stored_sex_worker_controls.get(
                    str(row["city_id"])
                ),
            }
            for row in city_rows
        ]
        geography = {
            "by_city": _distribution({
                f"{city_id}/{city_names[city_id]}": count
                for city_id, count in city_counts.items()
            }, total=population),
            "by_district": _distribution(district_counts, total=population),
            "by_street": _distribution(street_counts, total=population),
            "district_templates": _distribution(
                Counter(str(row["template_name"]) for row in district_rows),
                total=len(district_rows),
            ),
            "district_levels": _distribution(
                Counter(str(row["level"]) for row in district_rows),
                total=len(district_rows),
            ),
        }
        report = {
            "report_version": REPORT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": str(database.resolve()),
            "scope": {
                "type": "world_database",
                "completed_cities": [
                    {
                        key: row[key]
                        for key in (
                            "city_id", "city_template", "seed",
                            "target_population", "created_at", "completed_at",
                        )
                    }
                    for row in city_rows
                ],
            },
            "totals": {
                "cities": len(city_rows),
                "districts": len(district_rows),
                "streets": int(street_count),
                "organizations": len(organization_rows),
                "characters": population,
                "families": len(family_sizes),
            },
            "population_distributions": {
                "gender": _distribution(gender_counts, total=population),
                "age": _distribution(
                    age_counts,
                    total=population,
                    preferred_order=(label for label, _ in AGE_BANDS),
                ),
                "education": _distribution(education_counts, total=population),
                "title": _distribution(title_counts, total=population),
                "hygiene": _distribution(hygiene_counts, total=population),
                "temperament": _distribution(
                    temperament_counts, total=population
                ),
            },
            "asset_distributions": {
                "asset_level": {
                    "definition": (
                        "由 net_assets 使用角色生成器的对数曲线换算到 "
                        "[-1, 1]；负数表示净负债，正数表示净资产"
                    ),
                    **_distribution(
                        asset_level_counts,
                        total=population,
                        preferred_order=(
                            label for label, _ in ASSET_LEVEL_BANDS
                        ),
                    ),
                },
                "income": _distribution(
                    income_counts,
                    total=population,
                    preferred_order=(label for label, _ in INCOME_BANDS),
                ),
                "net_assets": _distribution(
                    net_asset_counts,
                    total=population,
                    preferred_order=(label for label, _ in NET_ASSET_BANDS),
                ),
                "debt": _distribution(
                    debt_counts,
                    total=population,
                    preferred_order=(label for label, _ in DEBT_BANDS),
                ),
            },
            "appearance_distributions": {
                "face_score": _distribution(
                    face_score_counts,
                    preferred_order=(label for label, _ in SCORE_BANDS),
                ),
                "body_score": _distribution(
                    body_score_counts,
                    preferred_order=(label for label, _ in SCORE_BANDS),
                ),
                "appearance_score": _distribution(
                    appearance_score_counts,
                    preferred_order=(label for label, _ in SCORE_BANDS),
                ),
            },
            "family": _family_report(
                population=population,
                family_sizes=family_sizes,
                family_addresses=family_addresses,
                family_roles=family_roles,
                single_person_genders=single_person_genders,
                single_person_ages=single_person_ages,
                unassigned_characters=unassigned_characters,
                addressed_characters=addressed_characters,
                inconsistent_family_addresses=len(inconsistent_family_keys),
            ),
            "organization": _organization_report(
                organization_rows, member_counts
            ),
            "occupation": _occupation_report(occupation_counts, population),
            "sex_worker_population_control": {
                "configured_ratio": config.SEX_WORKER_POPULATION_RATIO,
                "ratio_denominator": sum(
                    int(row["target_population"]) for row in city_rows
                ),
                "target_minimum": sex_worker_target,
                "actual_count": sex_worker_actual,
                "overshoot": sex_worker_actual - sex_worker_target,
                "actual_ratio": (
                    sex_worker_actual
                    / sum(int(row["target_population"]) for row in city_rows)
                ),
                "lower_bound_met": sex_worker_actual >= sex_worker_target,
                "cities": sex_worker_by_city,
            },
            "sex_worker_levels": {
                "assigned_count": sum(sex_worker_level_counts.values()),
                "missing_count": sex_worker_missing_levels,
                "minimum_level_violations": sex_worker_minimum_level_violations,
                "actual_level_distribution": _distribution(
                    sex_worker_level_counts,
                    total=sex_worker_actual,
                    preferred_order=(str(level) for level in range(1, 10)),
                ),
                "minimum_level_distribution": _distribution(
                    sex_worker_minimum_level_counts,
                    total=sex_worker_actual,
                    preferred_order=(str(level) for level in range(1, 10)),
                ),
                "by_occupation": {
                    occupation: _distribution(
                        counts,
                        preferred_order=(str(level) for level in range(1, 10)),
                    )
                    for occupation, counts in sorted(
                        sex_worker_levels_by_occupation.items()
                    )
                },
                "by_venue_type": {
                    venue_type: _distribution(
                        counts,
                        preferred_order=(str(level) for level in range(1, 10)),
                    )
                    for venue_type, counts in sorted(
                        sex_worker_levels_by_venue_type.items()
                    )
                },
            },
            "geography": geography,
        }
        return report
    finally:
        connection.close()


def write_world_statistics_report(
    database_path: str | Path,
    output_path: str | Path | None = None,
) -> Path:
    """Build and atomically write the report as indented UTF-8 JSON."""

    database = Path(database_path)
    output = Path(output_path) if output_path else default_statistics_report_path(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_world_statistics_report(database)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


__all__ = [
    "REPORT_VERSION",
    "build_world_statistics_report",
    "default_statistics_report_path",
    "write_world_statistics_report",
]
