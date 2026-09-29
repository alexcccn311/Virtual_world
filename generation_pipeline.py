"""Generator orchestration followed by generator-independent world linking."""
from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from .generators.city_generator import generate_city
from .generators.family_relationship_generator import (
    FamilyAssignmentReport,
    assign_family_relationships,
    assign_family_relationships_in_database,
)
from .generators.statistics_report_generator import (
    write_world_statistics_report,
)
from .generators.sex_worker_level_generator import (
    assign_sex_worker_levels,
    assign_sex_worker_levels_in_database,
)
from .storage.sqlite_store import SQLiteWorldStore


def _world_map_from_generation(result: dict[str, object]) -> dict[str, object]:
    """Keep the full map surface available to later household-address logic."""

    return {
        "city_id": result["city_id"],
        "city_template": result["city_template"],
        "districts": result["districts"],
        "streets": result["streets"],
        "district_map": result["district_map"],
    }


def _retained_characters(
    result: dict[str, object],
) -> list[MutableMapping[str, object]]:
    characters: list[MutableMapping[str, object]] = []
    organizations = result.get("organizations", [])
    if not isinstance(organizations, list):
        return characters
    for organization in organizations:
        if not isinstance(organization, dict):
            continue
        members = organization.get("characters", [])
        if not isinstance(members, list):
            continue
        characters.extend(
            member for member in members if isinstance(member, MutableMapping)
        )
    return characters


def _organization_types(result: dict[str, object]) -> dict[str, str]:
    organization_types: dict[str, str] = {}
    organizations = result.get("organizations", [])
    if not isinstance(organizations, list):
        return organization_types
    for organization in organizations:
        if not isinstance(organization, dict):
            continue
        organization_id = organization.get("organization_id")
        template_name = organization.get("template_name")
        if isinstance(organization_id, str) and isinstance(template_name, str):
            organization_types[organization_id] = template_name
    return organization_types


def generate_city_with_relationships(
    target_city_population: int,
    seed: int = 12345,
    *,
    city_template: str = "罪恶都市",
    special_role: dict | None = None,
    store: SQLiteWorldStore | None = None,
    retain_organization_characters: bool | None = None,
    statistics_report_path: str | Path | None = None,
) -> dict[str, object]:
    """Generate a city, link its families, then report the final database.

    The relationship step is intentionally outside ``city_generator``.  Future
    country/world generators can call the same matcher with their own character
    iterable and map without importing city-specific orchestration.
    """

    def postprocess_persisted_city(result: dict[str, object]) -> None:
        world_map = _world_map_from_generation(result)
        retained = _retained_characters(result)
        city_id = result.get("city_id")
        if not isinstance(city_id, str) or not city_id:
            raise RuntimeError("SQLite 城市生成完成后未返回有效 city_id")
        sex_worker_report = assign_sex_worker_levels_in_database(
            store.database_path,  # type: ignore[union-attr]
            city_id=city_id,
            seed=seed,
        )
        family_report = assign_family_relationships_in_database(
            store.database_path,  # type: ignore[union-attr]
            world_map,
            city_id=city_id,
            seed=seed,
        )
        if retained:
            # Keep an explicitly retained in-memory result consistent with the
            # database.  Stable-ID matching makes both passes identical.
            retained_level_report = assign_sex_worker_levels(
                retained,
                _organization_types(result),
                seed=seed,
            )
            if retained_level_report.as_dict() != sex_worker_report.as_dict():
                raise RuntimeError("内存与数据库性工作者等级分配结果不一致")
            assign_family_relationships(retained, world_map, seed=seed)
        result["sex_worker_levels"] = sex_worker_report.as_dict()
        result["family_relationships"] = family_report.as_dict()

    result = generate_city(
        target_city_population,
        seed,
        city_template=city_template,
        special_role=special_role,
        store=store,
        retain_organization_characters=retain_organization_characters,
        before_store_complete=(
            postprocess_persisted_city if store is not None else None
        ),
    )
    if store is None:
        world_map = _world_map_from_generation(result)
        retained = _retained_characters(result)
        sex_worker_report = assign_sex_worker_levels(
            retained,
            _organization_types(result),
            seed=seed,
        )
        report = assign_family_relationships(
            retained,
            world_map,
            seed=seed,
        )
        result["sex_worker_levels"] = sex_worker_report.as_dict()
        result["family_relationships"] = report.as_dict()
    if store is not None:
        output_path = write_world_statistics_report(
            store.database_path,
            statistics_report_path,
        )
        result["statistics_report"] = str(output_path.resolve())
    return result


__all__ = ["generate_city_with_relationships", "FamilyAssignmentReport"]
