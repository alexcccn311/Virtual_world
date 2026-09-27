"""Assign demand-driven market levels to generated sex-worker role slots."""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import config
from ..common.distributions import weighted_choice
from ..services.sex_service import sex_worker_appeal_score
from .character_generator import SEX_SERVICE_PROFESSIONS


# These fields form one exchangeable appearance/body package. Role-owned age,
# occupation, skills, finance, presentation, temperament, and relationships
# deliberately remain attached to the destination slot.
SEX_WORKER_APPEARANCE_PACKAGE_FIELDS = (
    "hair",
    "skin_quality",
    "height",
    "bmi",
    "weight",
    "bust",
    "waist",
    "hips",
    "feature",
    "face_shape",
    "eyes",
    "eyebrows",
    "nose",
    "lips",
    "face_score",
    "body_score",
    "appearance_score",
    "cup_size",
)


@dataclass(frozen=True, slots=True)
class SexWorkerLevelAssignmentReport:
    sex_worker_count: int
    level_counts: dict[int, int]
    minimum_level_counts: dict[int, int]
    venue_type_counts: dict[str, int]
    occupation_counts: dict[str, int]
    venue_occupation_level_counts: dict[str, dict[int, int]]
    appearance_packages_reassigned: int
    minimum_level_violations: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _Slot:
    character: MutableMapping[str, object]
    character_id: str
    venue_type: str
    occupation: str
    minimum_level: int
    target_level: int


@dataclass(frozen=True, slots=True)
class _AppearancePackage:
    source_character_id: str
    values: dict[str, object]
    score: float
    percentile: float


def _stable_seed(seed: int, *parts: object) -> int:
    payload = ":".join((str(seed), *(str(part) for part in parts)))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")


def _organization_template(template_name: str) -> Mapping[str, object]:
    matches = [
        template
        for templates in config.ORGANIZATION_TEMPLATES.values()
        if (template := templates.get(template_name)) is not None
    ]
    if len(matches) != 1:
        raise ValueError(f"无法唯一定位组织模板：{template_name}")
    return matches[0]


def sex_worker_minimum_level(venue_type: str, occupation: str) -> int:
    """Resolve the venue/occupation admission floor configured for one slot."""

    if occupation not in SEX_SERVICE_PROFESSIONS:
        raise ValueError(f"职业 {occupation} 不是性工作职业")
    if occupation in config.SEX_WORKER_FIXED_MIN_LEVEL:
        return int(config.SEX_WORKER_FIXED_MIN_LEVEL[occupation])
    modifier = config.SEX_WORKER_MIN_LEVEL_MODIFIER.get(occupation)
    if modifier is None:
        raise ValueError(f"性工作职业 {occupation} 缺少等级修正")
    service_rules = _organization_template(venue_type).get("sex_service")
    if not isinstance(service_rules, Mapping) or not service_rules.get("enabled"):
        raise ValueError(f"组织类型 {venue_type} 不是性服务场所")
    venue_minimum = service_rules.get("min_level")
    if (
        isinstance(venue_minimum, bool)
        or not isinstance(venue_minimum, int)
        or not 1 <= venue_minimum <= 9
    ):
        raise ValueError(f"性服务场所 {venue_type} 使用无效 min_level")
    return max(1, min(9, venue_minimum + int(modifier)))


def _target_level(character_id: str, minimum_level: int, seed: int) -> int:
    rng = random.Random(_stable_seed(seed, character_id, "sex-worker-target-level"))
    return int(weighted_choice(
        rng,
        {
            level: weight
            for level, weight in config.SEX_WORKER_LEVEL_WEIGHTS.items()
            if level >= minimum_level
        },
    ))


def _required_number(character: Mapping[str, object], field: str) -> float:
    value = character.get(field)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 100
    ):
        raise ValueError(f"性工作者 {character.get('id')} 缺少有效 {field}")
    return float(value)


def _score(character: Mapping[str, object]) -> float:
    return sex_worker_appeal_score(
        _required_number(character, "appearance_score"),
        _required_number(character, "body_score"),
    )


def _percentile(sorted_scores: Sequence[float], score: float) -> float:
    if len(sorted_scores) == 1:
        return 100.0
    lower = bisect.bisect_left(sorted_scores, score)
    upper = bisect.bisect_right(sorted_scores, score)
    return round(
        ((lower + upper - 1) / 2) / (len(sorted_scores) - 1) * 100.0,
        4,
    )


def _appearance_package(
    character: Mapping[str, object],
    sorted_scores: Sequence[float],
) -> _AppearancePackage:
    character_id = character.get("id")
    if not isinstance(character_id, str) or not character_id:
        raise ValueError("性工作者缺少有效 ID")
    missing = [
        field for field in SEX_WORKER_APPEARANCE_PACKAGE_FIELDS
        if field not in character
    ]
    if missing:
        raise ValueError(
            f"性工作者 {character_id} 缺少外貌字段：{'、'.join(missing)}"
        )
    score = _score(character)
    return _AppearancePackage(
        source_character_id=character_id,
        values={
            field: character[field]
            for field in SEX_WORKER_APPEARANCE_PACKAGE_FIELDS
        },
        score=score,
        percentile=_percentile(sorted_scores, score),
    )


def assign_sex_worker_levels(
    characters: Sequence[MutableMapping[str, object]],
    organization_types: Mapping[str, str],
    *,
    seed: int = 12345,
) -> SexWorkerLevelAssignmentReport:
    """Rank all generated appearance packages and fill exact role demand.

    Each existing character record is the destination role slot. Its physical
    package is first added to the city-wide candidate pool. Role slots draw an
    exact target level from their venue/occupation floor, then the best package
    is paired with the highest-level slot. Within one level, a stable hash
    shuffle prevents organization generation order from affecting allocation.
    """

    slots: list[_Slot] = []
    seen_ids: set[str] = set()
    for character in characters:
        occupation = character.get("occupation")
        if occupation not in SEX_SERVICE_PROFESSIONS:
            continue
        character_id = character.get("id")
        organization_id = character.get("organization_id")
        if not isinstance(character_id, str) or not character_id:
            raise ValueError("性工作者缺少有效 ID")
        if character_id in seen_ids:
            raise ValueError(f"性工作者池包含重复 ID：{character_id}")
        seen_ids.add(character_id)
        if not isinstance(organization_id, str) or not organization_id:
            raise ValueError(f"性工作者 {character_id} 缺少 organization_id")
        venue_type = organization_types.get(organization_id)
        if not isinstance(venue_type, str) or not venue_type:
            raise ValueError(f"性工作者 {character_id} 无法定位组织类型")
        minimum_level = sex_worker_minimum_level(venue_type, str(occupation))
        slots.append(_Slot(
            character=character,
            character_id=character_id,
            venue_type=venue_type,
            occupation=str(occupation),
            minimum_level=minimum_level,
            target_level=_target_level(character_id, minimum_level, seed),
        ))

    if not slots:
        return SexWorkerLevelAssignmentReport(
            sex_worker_count=0,
            level_counts={},
            minimum_level_counts={},
            venue_type_counts={},
            occupation_counts={},
            venue_occupation_level_counts={},
            appearance_packages_reassigned=0,
            minimum_level_violations=0,
        )

    scores = sorted(_score(slot.character) for slot in slots)
    packages = sorted(
        (_appearance_package(slot.character, scores) for slot in slots),
        key=lambda package: (-package.score, package.source_character_id),
    )
    ordered_slots = sorted(
        slots,
        key=lambda slot: (
            -slot.target_level,
            _stable_seed(seed, slot.character_id, "sex-worker-slot-order"),
        ),
    )

    reassigned = 0
    level_counts: Counter[int] = Counter()
    minimum_counts: Counter[int] = Counter()
    venue_counts: Counter[str] = Counter()
    occupation_counts: Counter[str] = Counter()
    combination_counts: dict[str, Counter[int]] = defaultdict(Counter)
    for slot, package in zip(ordered_slots, packages, strict=True):
        if package.source_character_id != slot.character_id:
            reassigned += 1
        slot.character.update(package.values)
        slot.character["sex_worker_score"] = round(package.score, 4)
        slot.character["sex_worker_percentile"] = package.percentile
        slot.character["sex_worker_level"] = slot.target_level
        slot.character["sex_worker_minimum_level"] = slot.minimum_level
        level_counts[slot.target_level] += 1
        minimum_counts[slot.minimum_level] += 1
        venue_counts[slot.venue_type] += 1
        occupation_counts[slot.occupation] += 1
        combination_counts[
            f"{slot.venue_type}/{slot.occupation}"
        ][slot.target_level] += 1

    violations = sum(
        int(slot.target_level < slot.minimum_level)
        for slot in slots
    )
    return SexWorkerLevelAssignmentReport(
        sex_worker_count=len(slots),
        level_counts=dict(sorted(level_counts.items())),
        minimum_level_counts=dict(sorted(minimum_counts.items())),
        venue_type_counts=dict(sorted(venue_counts.items())),
        occupation_counts=dict(sorted(occupation_counts.items())),
        venue_occupation_level_counts={
            key: dict(sorted(counts.items()))
            for key, counts in sorted(combination_counts.items())
        },
        appearance_packages_reassigned=reassigned,
        minimum_level_violations=violations,
    )


def assign_sex_worker_levels_in_database(
    database_path: str | Path,
    *,
    city_id: str,
    seed: int = 12345,
) -> SexWorkerLevelAssignmentReport:
    """Assign and persist sex-worker levels for one completed generated city."""

    database = Path(database_path)
    connection = sqlite3.connect(database, timeout=60.0)
    try:
        rows = connection.execute(
            """
            SELECT
                character.character_id,
                character.organization_id,
                character.occupation,
                character.data_json,
                organization.template_name
            FROM characters AS character
            JOIN organizations AS organization
              ON organization.organization_id = character.organization_id
            WHERE character.city_id = ?
            """,
            (city_id,),
        ).fetchall()
        characters: list[MutableMapping[str, object]] = []
        organization_types: dict[str, str] = {}
        for character_id, organization_id, occupation, data_json, venue_type in rows:
            if str(occupation) not in SEX_SERVICE_PROFESSIONS:
                continue
            payload = json.loads(data_json)
            payload["id"] = str(character_id)
            payload["organization_id"] = str(organization_id)
            payload["occupation"] = str(occupation)
            characters.append(payload)
            organization_types[str(organization_id)] = str(venue_type)

        report = assign_sex_worker_levels(
            characters,
            organization_types,
            seed=seed,
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            UPDATE characters
            SET data_json = ?
            WHERE city_id = ? AND character_id = ?
            """,
            (
                (
                    json.dumps(character, ensure_ascii=False, separators=(",", ":")),
                    city_id,
                    character["id"],
                )
                for character in characters
            ),
        )
        row = connection.execute(
            "SELECT data_json FROM cities WHERE city_id = ?",
            (city_id,),
        ).fetchone()
        city_data = json.loads(row[0] or "{}") if row is not None else {}
        city_data["sex_worker_levels"] = report.as_dict()
        connection.execute(
            "UPDATE cities SET data_json = ? WHERE city_id = ?",
            (
                json.dumps(city_data, ensure_ascii=False, separators=(",", ":")),
                city_id,
            ),
        )
        connection.commit()
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = (
    "SEX_WORKER_APPEARANCE_PACKAGE_FIELDS",
    "SexWorkerLevelAssignmentReport",
    "assign_sex_worker_levels",
    "assign_sex_worker_levels_in_database",
    "sex_worker_minimum_level",
)
