"""Create complete four-person families after world character generation.

The matcher deliberately knows nothing about a city generator.  It receives a
flat iterable of characters plus the generated map, so the same post-processing
step can later be reused by country and world generators.  Family members share
one axial-coordinate address; unmatched characters become one-person families.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from bisect import bisect_left
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..character_config import NAME_SURNAME_POOL


_SURNAME_PREFIXES = tuple(
    sorted(set(NAME_SURNAME_POOL), key=lambda value: (-len(value), value))
)

# In the generated adult population, people up to this age are considered the
# primary child generation during matching.  Older people are still allowed
# to become children later; they are merely preserved as scarce parents until
# the more age-constrained younger generation has been processed.
PREFERRED_CHILD_MAX_AGE = 39


@dataclass(frozen=True, slots=True)
class FamilyAssignmentReport:
    """Summary of one family-assignment pass."""

    family_count: int
    assigned_character_count: int
    eligible_character_count: int
    ignored_character_count: int
    skipped_existing_family_character_count: int
    cross_organization_family_count: int
    family_size_counts: dict[int, int] = field(default_factory=dict)
    single_parent_family_count: int = 0
    household_count: int = 0
    single_person_household_count: int = 0
    addressed_character_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _CharacterRecord:
    character: MutableMapping[str, object]
    character_id: str
    surname: str
    sex: str
    age: int
    organization_id: str | None
    net_assets: float


@dataclass(slots=True)
class _TakenCandidate:
    record: _CharacterRecord
    source: deque[_CharacterRecord]

    def put_back(self) -> None:
        self.source.append(self.record)


@dataclass(frozen=True, slots=True)
class _FamilyPlan:
    parent_mode: str
    child_count: int

    @property
    def family_size(self) -> int:
        parent_count = 2 if self.parent_mode == "both" else 1
        return parent_count + self.child_count


@dataclass(frozen=True, slots=True)
class _ResidentialStreet:
    name: str
    prosperity: float
    cells: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _StreetTier:
    streets: tuple[_ResidentialStreet, ...]
    cumulative_cell_weights: tuple[int, ...]
    total_cell_weight: int

    def choose(self, rng: random.Random) -> _ResidentialStreet:
        draw = rng.randrange(self.total_cell_weight) + 1
        return self.streets[bisect_left(self.cumulative_cell_weights, draw)]


def extract_surname(name: object) -> str | None:
    """Return a configured Chinese surname, preferring compound surnames."""

    if not isinstance(name, str):
        return None
    normalized = name.strip()
    if not normalized:
        return None
    for surname in _SURNAME_PREFIXES:
        if normalized.startswith(surname):
            return surname
    return None


def _organization_id(character: MutableMapping[str, object]) -> str | None:
    value = character.get("organization_id")
    return value if isinstance(value, str) and value else None


def _relation(character: MutableMapping[str, object]) -> dict[str, object]:
    value = character.get("relation")
    if value is None:
        value = {}
        character["relation"] = value
    if not isinstance(value, dict):
        character_id = character.get("id", "<unknown>")
        raise ValueError(f"Character“{character_id}”的 relation 必须是字典")
    return value


def _record_from_character(
    character: MutableMapping[str, object],
) -> _CharacterRecord | None:
    character_id = character.get("id")
    sex = character.get("sex")
    age = character.get("age")
    surname = extract_surname(character.get("name"))
    if (
        not isinstance(character_id, str)
        or not character_id
        or sex not in {"male", "female"}
        or isinstance(age, bool)
        or not isinstance(age, int)
        or surname is None
    ):
        return None
    raw_net_assets = character.get("net_assets", 0)
    net_assets = (
        float(raw_net_assets)
        if not isinstance(raw_net_assets, bool)
        and isinstance(raw_net_assets, (int, float))
        else 0.0
    )
    return _CharacterRecord(
        character=character,
        character_id=character_id,
        surname=surname,
        sex=sex,
        age=age,
        organization_id=_organization_id(character),
        net_assets=net_assets,
    )


def _shuffle_queues(
    buckets: dict[Any, deque[_CharacterRecord]],
    rng: random.Random,
) -> None:
    for key in sorted(buckets, key=repr):
        values = list(buckets[key])
        rng.shuffle(values)
        buckets[key] = deque(values)


def _take_candidate(
    buckets: dict[Any, deque[_CharacterRecord]],
    keys: Sequence[object],
    *,
    assigned_ids: set[str],
    excluded_ids: set[str],
    avoided_organizations: set[str],
) -> _TakenCandidate | None:
    """Take one candidate, first trying to add a new organization to a family."""

    phases = (True, False) if avoided_organizations else (False,)
    for avoid_same_organization in phases:
        for key in keys:
            source = buckets.get(key)
            if not source:
                continue
            remaining = len(source)
            for _ in range(remaining):
                record = source.popleft()
                if (
                    record.character_id in assigned_ids
                    or record.character_id in excluded_ids
                ):
                    continue
                if (
                    avoid_same_organization
                    and record.organization_id is not None
                    and record.organization_id in avoided_organizations
                ):
                    source.append(record)
                    continue
                return _TakenCandidate(record, source)
    return None


def _randomized(values: Iterable[int], rng: random.Random) -> list[int]:
    result = list(values)
    rng.shuffle(result)
    return result


def _family_id(records: Sequence[_CharacterRecord]) -> str:
    identity = "\x1f".join(sorted(record.character_id for record in records))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16].upper()
    return f"FAM-{digest}"


def _family_plans(rng: random.Random) -> list[_FamilyPlan]:
    """Return weighted-random plans, keeping scarce parent generations useful.

    A single-parent household with two children connects three people per
    parent.  The two-parent alternatives remain in the draw so family sizes
    three, four and five all continue to occur, but no longer consume the
    limited parent-age population with equal priority.
    """

    weighted_plans = (
        (_FamilyPlan(parent_mode="father", child_count=2), 10.0),
        (_FamilyPlan(parent_mode="mother", child_count=2), 10.0),
        (_FamilyPlan(parent_mode="both", child_count=1), 0.7),
        (_FamilyPlan(parent_mode="both", child_count=2), 1.8),
        (_FamilyPlan(parent_mode="both", child_count=3), 1.4),
    )
    # Independent exponential clocks form a deterministic weighted random
    # permutation without repeating a plan.  Every plan remains a fallback.
    ranked = sorted(
        weighted_plans,
        key=lambda item: rng.expovariate(item[1]),
    )
    return [plan for plan, _ in ranked]


def _child_role(index: int, count: int, sex: str) -> str:
    gender = "brother" if sex == "male" else "sister"
    if count == 1:
        return "only_son" if sex == "male" else "only_daughter"
    if count == 2:
        return f"{'older' if index == 0 else 'younger'}_{gender}"
    rank = ("oldest", "middle", "youngest")[index]
    return f"{rank}_{gender}"


def _write_family_relations(
    father: _CharacterRecord | None,
    mother: _CharacterRecord | None,
    children: Sequence[_CharacterRecord],
) -> None:
    parents = [parent for parent in (father, mother) if parent is not None]
    members = [*parents, *children]
    family_id = _family_id(members)
    family_size = len(members)
    child_ids = [child.character_id for child in children]

    for parent, role, spouse in (
        (father, "father", mother),
        (mother, "mother", father),
    ):
        if parent is None:
            continue
        values: dict[str, object] = {
            "family_id": family_id,
            "family_size": family_size,
            "family_role": role,
            "children_ids": child_ids,
        }
        if spouse is not None:
            values["spouse_id"] = spouse.character_id
        _relation(parent.character).update(values)

    for index, child in enumerate(children):
        older_ids = [member.character_id for member in children[:index]]
        younger_ids = [member.character_id for member in children[index + 1 :]]
        values = {
            "family_id": family_id,
            "family_size": family_size,
            "family_role": _child_role(index, len(children), child.sex),
            "sibling_ids": [*older_ids, *younger_ids],
        }
        if father is not None:
            values["father_id"] = father.character_id
        if mother is not None:
            values["mother_id"] = mother.character_id
        if older_ids:
            values["older_sibling_ids"] = older_ids
            if len(older_ids) == 1:
                values["older_sibling_id"] = older_ids[0]
        if younger_ids:
            values["younger_sibling_ids"] = younger_ids
            if len(younger_ids) == 1:
                values["younger_sibling_id"] = younger_ids[0]
        _relation(child.character).update(values)


def _write_single_person_family(record: _CharacterRecord) -> None:
    _relation(record.character).update(
        {
            "family_id": _family_id((record,)),
            "family_size": 1,
            "family_role": "single",
        }
    )


def _is_valid_family(
    father: _CharacterRecord | None,
    mother: _CharacterRecord | None,
    children: Sequence[_CharacterRecord],
) -> bool:
    parents = [parent for parent in (father, mother) if parent is not None]
    members = [*parents, *children]
    composition = (len(parents), len(children))
    valid_compositions = {(2, 1), (1, 2), (2, 2), (2, 3)}
    return (
        3 <= len(members) <= 5
        and composition in valid_compositions
        and len({member.character_id for member in members}) == len(members)
        and (father is None or father.sex == "male")
        and (mother is None or mother.sex == "female")
        and len({child.surname for child in children}) == 1
        and (father is None or all(child.surname == father.surname for child in children))
        and all(
            older.age > younger.age
            for older, younger in zip(children, children[1:])
        )
        and all(
            20 <= parent.age - child.age <= 35
            for parent in parents
            for child in children
        )
    )


def _take_children(
    oldest_child: _CharacterRecord,
    child_count: int,
    children_by_surname_age: dict[tuple[str, int], deque[_CharacterRecord]],
    *,
    assigned_ids: set[str],
    rng: random.Random,
) -> tuple[list[_CharacterRecord], list[_TakenCandidate]] | None:
    children = [oldest_child]
    taken: list[_TakenCandidate] = []
    used_ids = {oldest_child.character_id}
    used_orgs = (
        {oldest_child.organization_id}
        if oldest_child.organization_id is not None
        else set()
    )
    for _ in range(child_count - 1):
        youngest_so_far = children[-1]
        minimum_age = max(0, oldest_child.age - 15)
        ages = _randomized(
            range(youngest_so_far.age - 1, minimum_age - 1, -1), rng
        )
        candidate = _take_candidate(
            children_by_surname_age,
            [(oldest_child.surname, age) for age in ages],
            assigned_ids=assigned_ids,
            excluded_ids=used_ids,
            avoided_organizations=used_orgs,
        )
        if candidate is None:
            for selected in taken:
                selected.put_back()
            return None
        taken.append(candidate)
        children.append(candidate.record)
        used_ids.add(candidate.record.character_id)
        if candidate.record.organization_id is not None:
            used_orgs.add(candidate.record.organization_id)
    return children, taken


def _take_parents(
    plan: _FamilyPlan,
    children: Sequence[_CharacterRecord],
    fathers_by_surname_age: dict[tuple[str, int], deque[_CharacterRecord]],
    mothers_by_age: dict[int, deque[_CharacterRecord]],
    *,
    assigned_ids: set[str],
    rng: random.Random,
) -> tuple[
    _CharacterRecord | None,
    _CharacterRecord | None,
    list[_TakenCandidate],
] | None:
    minimum_parent_age = children[0].age + 20
    maximum_parent_age = children[-1].age + 35
    if minimum_parent_age > maximum_parent_age:
        return None
    parent_ages = _randomized(
        range(minimum_parent_age, maximum_parent_age + 1), rng
    )
    used_ids = {child.character_id for child in children}
    used_orgs = {
        child.organization_id
        for child in children
        if child.organization_id is not None
    }
    selected: list[_TakenCandidate] = []
    father: _CharacterRecord | None = None
    mother: _CharacterRecord | None = None

    if plan.parent_mode in {"both", "father"}:
        father_taken = _take_candidate(
            fathers_by_surname_age,
            [(children[0].surname, age) for age in parent_ages],
            assigned_ids=assigned_ids,
            excluded_ids=used_ids,
            avoided_organizations=used_orgs,
        )
        if father_taken is None:
            return None
        selected.append(father_taken)
        father = father_taken.record
        used_ids.add(father.character_id)
        if father.organization_id is not None:
            used_orgs.add(father.organization_id)

    if plan.parent_mode in {"both", "mother"}:
        mother_ages = parent_ages.copy()
        rng.shuffle(mother_ages)
        mother_taken = _take_candidate(
            mothers_by_age,
            mother_ages,
            assigned_ids=assigned_ids,
            excluded_ids=used_ids,
            avoided_organizations=used_orgs,
        )
        if mother_taken is None:
            for parent in selected:
                parent.put_back()
            return None
        selected.append(mother_taken)
        mother = mother_taken.record

    return father, mother, selected


def _map_field(item: object, field_name: str) -> object:
    if isinstance(item, Mapping):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _residential_streets(world_map: object) -> list[_ResidentialStreet]:
    if not isinstance(world_map, Mapping):
        raise ValueError("world_map 必须是包含 streets 的 mapping")
    raw_streets = world_map.get("streets")
    if not isinstance(raw_streets, Sequence) or isinstance(
        raw_streets, (str, bytes)
    ):
        raise ValueError("world_map.streets 必须是 Street 序列")

    streets: list[_ResidentialStreet] = []
    occupied_cells: set[tuple[int, int]] = set()
    for raw_street in raw_streets:
        name = _map_field(raw_street, "name")
        prosperity = _map_field(raw_street, "prosperity")
        raw_cells = _map_field(raw_street, "cells")
        if (
            not isinstance(name, str)
            or not name
            or isinstance(prosperity, bool)
            or not isinstance(prosperity, (int, float))
            or not isinstance(raw_cells, Sequence)
            or isinstance(raw_cells, (str, bytes))
        ):
            raise ValueError("world_map 中存在无效 Street")
        cells: list[tuple[int, int]] = []
        for raw_cell in raw_cells:
            if (
                not isinstance(raw_cell, Sequence)
                or isinstance(raw_cell, (str, bytes))
                or len(raw_cell) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in raw_cell
                )
            ):
                raise ValueError(f"Street“{name}”包含无效六边形坐标")
            cell = (raw_cell[0], raw_cell[1])
            if cell in occupied_cells:
                raise ValueError(f"地图坐标{cell}同时属于多条 Street")
            occupied_cells.add(cell)
            cells.append(cell)
        if not cells:
            raise ValueError(f"Street“{name}”没有可用 cells")
        streets.append(
            _ResidentialStreet(
                name=name,
                prosperity=float(prosperity),
                cells=tuple(cells),
            )
        )
    if not streets:
        raise ValueError("world_map.streets 不能为空，无法分配 address")
    return streets


def _street_tiers(
    streets: Sequence[_ResidentialStreet],
) -> tuple[_StreetTier, ...]:
    ordered = sorted(streets, key=lambda street: (street.prosperity, street.name))
    tier_count = min(10, len(ordered))
    tier_members: list[list[_ResidentialStreet]] = [
        [] for _ in range(tier_count)
    ]
    for index, street in enumerate(ordered):
        tier_index = min(tier_count - 1, index * tier_count // len(ordered))
        tier_members[tier_index].append(street)

    tiers: list[_StreetTier] = []
    for members in tier_members:
        cumulative: list[int] = []
        total = 0
        for street in members:
            total += len(street.cells)
            cumulative.append(total)
        tiers.append(
            _StreetTier(
                streets=tuple(members),
                cumulative_cell_weights=tuple(cumulative),
                total_cell_weight=total,
            )
        )
    return tuple(tiers)


def _address_seed(seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:household-address".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _assign_household_addresses(
    records: Sequence[_CharacterRecord],
    world_map: object,
    *,
    seed: int,
) -> tuple[int, int, int]:
    """Assign one axial map coordinate to each family or single resident."""

    tiers = _street_tiers(_residential_streets(world_map))
    households: dict[str, list[_CharacterRecord]] = defaultdict(list)
    for record in records:
        family_id = _relation(record.character).get("family_id")
        household_key = (
            f"family:{family_id}"
            if isinstance(family_id, str) and family_id
            else f"single:{record.character_id}"
        )
        households[household_key].append(record)

    ranked_households = sorted(
        (
            (sum(member.net_assets for member in members), key, members)
            for key, members in households.items()
        ),
        key=lambda item: (item[0], item[1]),
    )
    rng = random.Random(_address_seed(seed))
    household_count = len(ranked_households)
    single_person_count = 0
    addressed_count = 0
    tier_offsets = (-2, -1, 0, 1, 2)
    tier_offset_weights = (1, 4, 10, 4, 1)
    for rank, (_, household_key, members) in enumerate(ranked_households):
        wealth_percentile = (rank + 0.5) / household_count
        base_tier = min(len(tiers) - 1, int(wealth_percentile * len(tiers)))
        tier_offset = rng.choices(
            tier_offsets,
            weights=tier_offset_weights,
            k=1,
        )[0]
        tier_index = max(0, min(len(tiers) - 1, base_tier + tier_offset))
        street = tiers[tier_index].choose(rng)
        address = rng.choice(street.cells)
        for member in members:
            member.character["address"] = address
            addressed_count += 1
        if len(members) == 1:
            single_person_count += 1
    return household_count, single_person_count, addressed_count


def assign_family_relationships(
    characters: Iterable[MutableMapping[str, object]],
    world_map: object,
    *,
    seed: int = 12345,
) -> FamilyAssignmentReport:
    """Assign family relations and one shared address per household.

    Input order never participates in matching.  Stable character IDs are
    sorted first and then shuffled with ``seed``.  Families contain three to
    five people; unmatched characters receive a stable one-person family before
    all household wealth and addresses are calculated.
    """

    if world_map is None:
        raise ValueError("world_map 不能为空；家庭住址分配将复用该参数")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed 必须是整数")

    materialized = list(characters)
    seen_ids: set[str] = set()
    all_records: list[_CharacterRecord] = []
    records: list[_CharacterRecord] = []
    ignored_count = 0
    existing_family_count = 0
    for character in materialized:
        if not isinstance(character, MutableMapping):
            ignored_count += 1
            continue
        relation = _relation(character)
        record = _record_from_character(character)
        if record is None:
            ignored_count += 1
            continue
        if record.character_id in seen_ids:
            raise ValueError(f"出现重复 Character ID“{record.character_id}”")
        seen_ids.add(record.character_id)
        all_records.append(record)
        if relation.get("family_id"):
            existing_family_count += 1
            continue
        records.append(record)

    records.sort(key=lambda record: record.character_id)
    rng = random.Random(seed)

    children_by_surname_age: dict[
        tuple[str, int], deque[_CharacterRecord]
    ] = defaultdict(deque)
    fathers_by_surname_age: dict[
        tuple[str, int], deque[_CharacterRecord]
    ] = defaultdict(deque)
    mothers_by_age: dict[int, deque[_CharacterRecord]] = defaultdict(deque)
    for record in records:
        children_by_surname_age[(record.surname, record.age)].append(record)
        if record.sex == "male":
            fathers_by_surname_age[(record.surname, record.age)].append(record)
        else:
            mothers_by_age[record.age].append(record)

    _shuffle_queues(children_by_surname_age, rng)
    _shuffle_queues(fathers_by_surname_age, rng)
    _shuffle_queues(mothers_by_age, rng)
    older_candidates = records.copy()
    rng.shuffle(older_candidates)
    # Match the oldest likely children first.  They have the narrowest pool of
    # parents; people over 39 are kept behind the main child generation so
    # they are not prematurely consumed as somebody else's child when they
    # can connect two younger siblings as a parent instead.
    older_candidates.sort(
        key=lambda record: (
            record.age <= PREFERRED_CHILD_MAX_AGE,
            record.age,
        ),
        reverse=True,
    )

    assigned_ids: set[str] = set()
    family_count = 0
    cross_organization_count = 0
    family_size_counts = {1: 0, 3: 0, 4: 0, 5: 0}
    single_parent_family_count = 0
    for oldest_child in older_candidates:
        if oldest_child.character_id in assigned_ids:
            continue
        for plan in _family_plans(rng):
            child_result = _take_children(
                oldest_child,
                plan.child_count,
                children_by_surname_age,
                assigned_ids=assigned_ids,
                rng=rng,
            )
            if child_result is None:
                continue
            children, child_candidates = child_result
            parent_result = _take_parents(
                plan,
                children,
                fathers_by_surname_age,
                mothers_by_age,
                assigned_ids=assigned_ids,
                rng=rng,
            )
            if parent_result is None:
                for candidate in child_candidates:
                    candidate.put_back()
                continue
            father, mother, parent_candidates = parent_result

            if not _is_valid_family(father, mother, children):
                for candidate in [*parent_candidates, *child_candidates]:
                    candidate.put_back()
                continue

            parents = [
                parent for parent in (father, mother) if parent is not None
            ]
            members = [*parents, *children]
            _write_family_relations(father, mother, children)
            assigned_ids.update(member.character_id for member in members)
            family_count += 1
            family_size_counts[len(members)] += 1
            if len(parents) == 1:
                single_parent_family_count += 1
            organizations = {
                member.organization_id
                for member in members
                if member.organization_id is not None
            }
            if len(organizations) > 1:
                cross_organization_count += 1
            break

    for record in records:
        if record.character_id in assigned_ids:
            continue
        _write_single_person_family(record)
        assigned_ids.add(record.character_id)
        family_count += 1
        family_size_counts[1] += 1

    all_records.sort(key=lambda record: record.character_id)
    household_count, single_person_count, addressed_count = (
        _assign_household_addresses(
            all_records,
            world_map,
            seed=seed,
        )
    )

    return FamilyAssignmentReport(
        family_count=family_count,
        assigned_character_count=len(assigned_ids),
        eligible_character_count=len(records),
        ignored_character_count=ignored_count,
        skipped_existing_family_character_count=existing_family_count,
        cross_organization_family_count=cross_organization_count,
        family_size_counts=family_size_counts,
        single_parent_family_count=single_parent_family_count,
        household_count=household_count,
        single_person_household_count=single_person_count,
        addressed_character_count=addressed_count,
    )


def assign_family_relationships_in_database(
    database_path: str | Path,
    world_map: object,
    *,
    city_id: str,
    seed: int = 12345,
) -> FamilyAssignmentReport:
    """Run the generic matcher against one generated city in SQLite."""

    if not isinstance(city_id, str) or not city_id:
        raise ValueError("city_id 必须是非空字符串")
    connection = sqlite3.connect(Path(database_path), timeout=30.0)
    try:
        city_exists = connection.execute(
            "SELECT 1 FROM cities WHERE city_id = ?", (city_id,)
        ).fetchone()
        if city_exists is None:
            raise ValueError(f"数据库中不存在城市“{city_id}”")
        rows = connection.execute(
            """
            SELECT
                character_id,
                organization_id,
                name,
                json_extract(data_json, '$.sex'),
                json_extract(data_json, '$.age'),
                json_extract(data_json, '$.relation'),
                json_extract(data_json, '$.net_assets')
            FROM characters
            WHERE city_id = ?
            ORDER BY character_id
            """,
            (city_id,),
        )
        characters: list[dict[str, object]] = []
        for (
            character_id,
            organization_id,
            name,
            sex,
            age,
            relation_json,
            net_assets,
        ) in rows:
            relation = json.loads(relation_json) if relation_json else {}
            if not isinstance(relation, dict):
                raise ValueError(
                    f"Character“{character_id}”的 relation JSON 必须是对象"
                )
            characters.append(
                {
                    "id": character_id,
                    "organization_id": organization_id,
                    "name": name,
                    "sex": sex,
                    "age": age,
                    "relation": relation,
                    "net_assets": net_assets,
                }
            )

        report = assign_family_relationships(
            characters,
            world_map,
            seed=seed,
        )
        def updated_character_rows():
            for character in characters:
                relation = character["relation"]
                if not isinstance(relation, dict) or "address" not in character:
                    continue
                yield (
                    json.dumps(
                        relation,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        character["address"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    character["id"],
                    city_id,
                )

        report_json = json.dumps(
            report.as_dict(), ensure_ascii=False, separators=(",", ":")
        )
        with connection:
            connection.executemany(
                """
                UPDATE characters
                SET data_json = json_set(
                    data_json,
                    '$.relation', json(?),
                    '$.address', json(?)
                )
                WHERE character_id = ? AND city_id = ?
                """,
                updated_character_rows(),
            )
            connection.execute(
                """
                UPDATE cities
                SET data_json = json_set(
                    COALESCE(data_json, '{}'),
                    '$.family_relationships',
                    json(?)
                )
                WHERE city_id = ?
                """,
                (report_json, city_id),
            )
        return report
    finally:
        connection.close()


__all__ = [
    "FamilyAssignmentReport",
    "assign_family_relationships",
    "assign_family_relationships_in_database",
    "extract_surname",
]
