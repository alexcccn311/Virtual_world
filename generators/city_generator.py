"""District and Street map generation."""
from __future__ import annotations

import random
import math
import hashlib
from collections import Counter, defaultdict
from collections.abc import Mapping
from string import Formatter

from .. import config
from ..character_config import SEX_SERVICE_PROFESSIONS
from ..common.distributions import weighted_choice
from ..models.entities import District, Street
from ..storage.sqlite_store import CityWriteSession, SQLiteWorldStore
from .district_generator import (
    generate_district_organizations,
    required_organization_counts,
)
from .organization_generator import OrganizationGenerator


# District area and HEX_CELL_AREA are measured in km².
HEX_CELL_AREA = .05
HEX_SIDE_LENGTH = math.sqrt(2 * HEX_CELL_AREA / (3 * math.sqrt(3)))
HEX_NEIGHBOR_DISTANCE = math.sqrt(3) * HEX_SIDE_LENGTH
CENTER_GRAVITY = .20
HEX_DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))
DISTRICT_LEVELS = ("low", "middle", "high")


def axial_to_xy(cell: tuple[int, int]) -> tuple[float, float]:
    """Convert a pointy-top axial cell center to map coordinates."""
    q, r = cell
    return (
        HEX_SIDE_LENGTH * math.sqrt(3) * (q + r / 2),
        HEX_SIDE_LENGTH * 1.5 * r,
    )


def _hex_neighbors(cell: tuple[int, int]):
    q, r = cell
    return ((q + dq, r + dr) for dq, dr in HEX_DIRECTIONS)


def _hex_distance(left: tuple[int, int], right: tuple[int, int] = (0, 0)) -> int:
    dq, dr = left[0] - right[0], left[1] - right[1]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def _generate_district_instance(
    rng: random.Random,
    template_name: str,
    level: str,
    used_names: set[str],
) -> District:
    template = config.DISTRICT_TEMPLATES[template_name]
    available_names = [name for name in template["district_name_pool"] if name not in used_names]
    if not available_names:
        raise RuntimeError(f"District 名称池已耗尽：{template_name}")
    name = rng.choice(available_names)
    used_names.add(name)
    rules = template["levels"][level]
    population_min, population_max = rules["population"]
    prosperity_min, prosperity_max = rules["prosperity"]
    area_min, area_max = rules["area"]
    return District(
        template=template_name,
        name=name,
        level=level,
        population=rng.randint(population_min, population_max),
        prosperity=rng.randint(prosperity_min, prosperity_max),
        area=rng.uniform(area_min, area_max),
        unorganized_population_ratio=template["unorganized_population_ratio"],
        sex_index=template["sex_index"],
        required_organizations=required_organization_counts(template, level),
    )


def _create_districts(
    target_city_population: int,
    rng: random.Random,
    city_template: str,
) -> list[District]:
    if isinstance(target_city_population, bool) or not isinstance(target_city_population, int) or target_city_population <= 0:
        raise ValueError("target_city_population 必须是正整数")
    rules = config.CITY_TEMPLATES[city_template]
    used_names: set[str] = set()
    districts: list[District] = []
    for template_name, count in rules["required_districts"].items():
        for _ in range(count):
            districts.append(_generate_district_instance(
                rng, template_name, rng.choice(DISTRICT_LEVELS), used_names,
            ))
    random_weights = rules["random_district_weights"]
    while sum(district.population for district in districts) < target_city_population:
        template_name = weighted_choice(rng, random_weights)
        districts.append(_generate_district_instance(
            rng, template_name, rng.choice(DISTRICT_LEVELS), used_names,
        ))
    return districts


def _choose_city_boundary_seed(
    rng: random.Random,
    occupied: dict[tuple[int, int], str],
) -> tuple[int, int]:
    candidates = {
        neighbor
        for cell in occupied
        for neighbor in _hex_neighbors(cell)
        if neighbor not in occupied
    }
    if not candidates:
        raise RuntimeError("无法在城市外边界找到未占用的 District seed cell")
    # More occupied neighbors fill concave gaps; distance pressure keeps the
    # whole city compact. Noise only breaks otherwise similar alternatives.
    def score(cell: tuple[int, int]) -> float:
        city_neighbors = sum(neighbor in occupied for neighbor in _hex_neighbors(cell))
        return city_neighbors * 4.0 - _hex_distance(cell) * .18 + rng.random() * .35
    return max(sorted(candidates), key=score)


def _grow_district_cells(
    district: District,
    seed_cell: tuple[int, int],
    target_cell_count: int,
    occupied: dict[tuple[int, int], str],
    rng: random.Random,
) -> set[tuple[int, int]]:
    if seed_cell in occupied:
        raise RuntimeError(f"District {district.name} 的 seed cell 已被占用：{seed_cell}")
    cells = {seed_cell}
    occupied[seed_cell] = district.name
    frontier = {cell for cell in _hex_neighbors(seed_cell) if cell not in occupied}
    max_steps = max(32, target_cell_count * 4)
    steps = 0
    while len(cells) < target_cell_count:
        steps += 1
        if not frontier or steps > max_steps:
            raise RuntimeError(
                f"District {district.name} 生长失败：目标 {target_cell_count} cells，"
                f"已生成 {len(cells)} cells"
            )
        xy_cells = [axial_to_xy(cell) for cell in cells]
        centroid_x = sum(x for x, _ in xy_cells) / len(xy_cells)
        centroid_y = sum(y for _, y in xy_cells) / len(xy_cells)

        def score(cell: tuple[int, int]) -> float:
            same_neighbors = sum(neighbor in cells for neighbor in _hex_neighbors(cell))
            cell_x, cell_y = axial_to_xy(cell)
            raw_centroid_distance = math.hypot(
                cell_x - centroid_x,
                cell_y - centroid_y,
            )
            centroid_distance = raw_centroid_distance / HEX_NEIGHBOR_DISTANCE
            raw_origin_distance = math.hypot(cell_x, cell_y)
            origin_distance = raw_origin_distance / HEX_NEIGHBOR_DISTANCE
            seed_distance = _hex_distance(cell, seed_cell)
            return (
                same_neighbors * 3.5
                - centroid_distance * .30
                - seed_distance * .12
                - origin_distance * CENTER_GRAVITY
                + rng.random() * .40
            )

        chosen = max(sorted(frontier), key=score)
        frontier.remove(chosen)
        cells.add(chosen)
        occupied[chosen] = district.name
        frontier.update(
            neighbor for neighbor in _hex_neighbors(chosen)
            if neighbor not in occupied
        )
    return cells


def _layout_districts(districts: list[District], rng: random.Random) -> None:
    if not districts:
        return
    tie_breakers = {district.name: rng.random() for district in districts}
    districts_sorted = sorted(
        districts,
        key=lambda district: (-district.prosperity, tie_breakers[district.name]),
    )
    occupied: dict[tuple[int, int], str] = {}
    cells_by_name: dict[str, set[tuple[int, int]]] = {}
    for index, district in enumerate(districts_sorted):
        seed_cell = (0, 0) if index == 0 else _choose_city_boundary_seed(rng, occupied)
        target_cell_count = max(1, round(district.area / HEX_CELL_AREA))
        cells = _grow_district_cells(
            district, seed_cell, target_cell_count, occupied, rng,
        )
        cells_by_name[district.name] = cells
        district.seed = axial_to_xy(seed_cell)
        district.cells = tuple(sorted(cells))

    neighbors = {district.name: set() for district in districts}
    for cell, owner in occupied.items():
        for adjacent in _hex_neighbors(cell):
            other = occupied.get(adjacent)
            if other is not None and other != owner:
                neighbors[owner].add(other)
                neighbors[other].add(owner)
    for district in districts:
        district.neighbors = tuple(sorted(neighbors[district.name]))


def generate_district_map(
    target_city_population: int,
    seed: int = 12345,
    *,
    city_template: str = "罪恶都市",
) -> list[District]:
    """Create and lay out first-stage Districts without later city systems."""
    rng = random.Random(seed)
    districts = _create_districts(target_city_population, rng, city_template)
    _layout_districts(districts, rng)
    return districts


def _select_street_seeds(
    district: District,
    street_count: int,
    rng: random.Random,
) -> list[tuple[int, int]]:
    cells = list(district.cells)
    if street_count > len(cells):
        raise RuntimeError(
            f"District {district.name} 需要 {street_count} 个 Street seeds，"
            f"但只有 {len(cells)} 个 cells"
        )
    xy_cells = [axial_to_xy(cell) for cell in cells]
    centroid_x = sum(x for x, _ in xy_cells) / len(xy_cells)
    centroid_y = sum(y for _, y in xy_cells) / len(xy_cells)
    first_distances = {
        cell: math.hypot(x - centroid_x, y - centroid_y)
        for cell, (x, y) in zip(cells, xy_cells)
    }
    nearest_distance = min(first_distances.values())
    nearest = sorted(
        cell for cell, distance in first_distances.items()
        if math.isclose(distance, nearest_distance, rel_tol=1e-12, abs_tol=1e-12)
    )
    seeds = [rng.choice(nearest)]

    while len(seeds) < street_count:
        candidates = [cell for cell in cells if cell not in seeds]
        ranked = sorted(
            candidates,
            key=lambda cell: (
                min(_hex_distance(cell, seed) for seed in seeds),
                cell,
            ),
            reverse=True,
        )
        farthest_pool_size = max(1, math.ceil(len(ranked) * .10))
        seeds.append(rng.choice(ranked[:farthest_pool_size]))
    return seeds


def _partition_district_cells(
    district: District,
    street_names: list[str],
    seeds: list[tuple[int, int]],
    rng: random.Random,
) -> dict[str, set[tuple[int, int]]]:
    district_cells = set(district.cells)
    owners = {seed: name for name, seed in zip(street_names, seeds)}
    owned_cells = {name: {seed} for name, seed in zip(street_names, seeds)}
    frontier = {name: {seed} for name, seed in zip(street_names, seeds)}
    max_layers = len(district_cells)
    layers = 0

    while len(owners) < len(district_cells):
        layers += 1
        if layers > max_layers:
            raise RuntimeError(
                f"District {district.name} Street 扩散失败："
                f"已分配 {len(owners)}/{len(district_cells)} cells"
            )
        claims: dict[tuple[int, int], set[str]] = {}
        for name in street_names:
            for cell in frontier[name]:
                for neighbor in _hex_neighbors(cell):
                    if neighbor in district_cells and neighbor not in owners:
                        claims.setdefault(neighbor, set()).add(name)
        if not claims:
            raise RuntimeError(
                f"District {district.name} Street 扩散无可用 frontier："
                f"已分配 {len(owners)}/{len(district_cells)} cells"
            )

        next_frontier = {name: set() for name in street_names}
        for cell in sorted(claims):
            claimants = sorted(claims[cell])
            owner = claimants[0] if len(claimants) == 1 else rng.choice(claimants)
            owners[cell] = owner
            owned_cells[owner].add(cell)
            next_frontier[owner].add(cell)
        frontier = next_frontier
    return owned_cells


def _street_centroid(cells: set[tuple[int, int]]) -> tuple[float, float]:
    xy_cells = [axial_to_xy(cell) for cell in cells]
    return (
        sum(x for x, _ in xy_cells) / len(xy_cells),
        sum(y for _, y in xy_cells) / len(xy_cells),
    )


def _assign_street_prosperity(
    district: District,
    streets: list[Street],
    rng: random.Random,
) -> None:
    distances = [math.hypot(*street.centroid) for street in streets]
    nearest, farthest = min(distances), max(distances)
    if math.isclose(nearest, farthest):
        spatial_scores = [0.0] * len(streets)
    else:
        spatial_scores = [
            1.0 - 2.0 * (distance - nearest) / (farthest - nearest)
            for distance in distances
        ]
    raw_values = [
        district.prosperity + spatial_score * 6.0 + rng.uniform(-3.0, 3.0)
        for spatial_score in spatial_scores
    ]
    total_cells = sum(len(street.cells) for street in streets)
    weighted_raw = sum(
        raw * len(street.cells) for raw, street in zip(raw_values, streets)
    ) / total_cells
    correction = district.prosperity - weighted_raw
    values = [max(0, min(100, round(raw + correction))) for raw in raw_values]

    target_total = district.prosperity * total_cells
    current_total = sum(value * len(street.cells) for value, street in zip(values, streets))
    while current_total != target_total:
        direction = 1 if current_total < target_total else -1
        candidates = [
            index for index, value in enumerate(values)
            if 0 <= value + direction <= 100
        ]
        if not candidates:
            break
        best = min(
            candidates,
            key=lambda index: abs(
                current_total + direction * len(streets[index].cells) - target_total
            ),
        )
        updated_total = current_total + direction * len(streets[best].cells)
        if abs(updated_total - target_total) >= abs(current_total - target_total):
            break
        values[best] += direction
        current_total = updated_total

    for street, prosperity in zip(streets, values):
        street.prosperity = prosperity


def _generate_streets(
    districts: list[District],
    rng: random.Random,
) -> list[Street]:
    used_street_names: set[str] = set()
    all_streets: list[Street] = []
    for district in districts:
        template = config.DISTRICT_TEMPLATES[district.template]
        street_count = template["levels"][district.level]["street_count"]
        available_names = [
            name for name in template["street_name_pool"]
            if name not in used_street_names
        ]
        if len(available_names) < street_count:
            raise RuntimeError(
                f"District {district.name} 的 Street 名称池不足："
                f"需要 {street_count}，可用 {len(available_names)}"
            )
        street_names = rng.sample(available_names, street_count)
        used_street_names.update(street_names)
        seeds = _select_street_seeds(district, street_count, rng)
        partition = _partition_district_cells(
            district, street_names, seeds, rng,
        )
        cell_owner = {
            cell: name for name, cells in partition.items() for cell in cells
        }
        neighbors = {name: set() for name in street_names}
        for cell, owner in cell_owner.items():
            for adjacent in _hex_neighbors(cell):
                other = cell_owner.get(adjacent)
                if other is not None and other != owner:
                    neighbors[owner].add(other)
                    neighbors[other].add(owner)
        district_streets = []
        for name, seed in zip(street_names, seeds):
            cells = partition[name]
            district_streets.append(Street(
                name=name,
                district=district.name,
                seed=seed,
                cells=tuple(sorted(cells)),
                area=len(cells) * HEX_CELL_AREA,
                centroid=_street_centroid(cells),
                neighbors=tuple(sorted(neighbors[name])),
                prosperity=district.prosperity,
            ))
        _assign_street_prosperity(district, district_streets, rng)
        all_streets.extend(district_streets)
    return all_streets


def generate_city_map_with_streets(
    target_city_population: int,
    seed: int = 12345,
    *,
    city_template: str = "罪恶都市",
) -> tuple[list[District], list[Street]]:
    """Create Districts and partition each into second-stage Streets."""
    rng = random.Random(seed)
    districts = _create_districts(target_city_population, rng, city_template)
    _layout_districts(districts, rng)
    streets = _generate_streets(districts, rng)
    return districts, streets


def _district_map(
    districts: list[District],
    streets: list[Street],
) -> dict[str, int]:
    """Return the compact {District name: Street count} organization input."""
    street_counts = {district.name: 0 for district in districts}
    for street in streets:
        if street.district not in street_counts:
            raise RuntimeError(
                f"Street“{street.name}”引用未知 District“{street.district}”"
            )
        street_counts[street.district] += 1
    if any(count < 1 for count in street_counts.values()):
        raise RuntimeError("存在没有 Street 的 District")
    return street_counts


class _OrganizationCellAllocator:
    """Assign fixed, balanced street cells without consuming generation RNG."""

    def __init__(self, streets: list[Street], *, seed: int) -> None:
        self.seed = seed
        self.cells_by_street = {
            street.name: tuple(sorted(street.cells))
            for street in streets
        }
        self.loads = {
            street.name: {cell: 0 for cell in street.cells}
            for street in streets
        }

    def __call__(
        self,
        street_name: str,
        organization_id: str,
    ) -> tuple[int, int]:
        cells = self.cells_by_street.get(street_name)
        if not cells:
            raise ValueError(
                f"Organization“{organization_id}”引用没有可用 cell 的"
                f"Street“{street_name}”"
            )
        street_loads = self.loads[street_name]
        minimum_load = min(street_loads.values())
        candidates = tuple(
            cell for cell in cells if street_loads[cell] == minimum_load
        )
        digest = hashlib.sha256(
            f"{self.seed}:{organization_id}:{street_name}:organization-cell".encode(
                "utf-8"
            )
        ).digest()
        selected = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
        street_loads[selected] += 1
        return selected


def _highest_prosperity(items: list[District] | list[Street]):
    if not items:
        raise RuntimeError("无法从空候选集合选择最高繁荣度位置")
    highest = max(item.prosperity for item in items)
    return min(
        (item for item in items if item.prosperity == highest),
        key=lambda item: item.name,
    )


def _organization_template(template_name: str) -> tuple[str, Mapping[str, object]]:
    matches = [
        (scope, scoped_templates[template_name])
        for scope, scoped_templates in config.ORGANIZATION_TEMPLATES.items()
        if template_name in scoped_templates
    ]
    if not matches:
        raise KeyError(f"未知 Organization template“{template_name}”")
    if len(matches) > 1:
        scopes = "、".join(scope for scope, _ in matches)
        raise ValueError(
            f"Organization template“{template_name}”在多个 scope 中重复：{scopes}"
        )
    return matches[0]


def _organization_population(template: Mapping[str, object]) -> int:
    return sum(role["count"] for role in template["roles"].values())


def _sex_worker_population(template: Mapping[str, object]) -> int:
    return sum(
        role["count"]
        for role in template["roles"].values()
        if role["occupation"] in SEX_SERVICE_PROFESSIONS
    )


def _sex_worker_template_names() -> frozenset[str]:
    return frozenset(
        template_name
        for template_name, template in config.ORGANIZATION_TEMPLATES[
            "street"
        ].items()
        if _sex_worker_population(template) > 0
    )


def _independent_sex_worker_template_names() -> tuple[str, ...]:
    referenced_children = {
        declaration["template"]
        for templates in config.ORGANIZATION_TEMPLATES.values()
        for template in templates.values()
        for declaration in template.get("sub_organizations", {}).values()
    }
    return tuple(sorted(
        template_name
        for template_name in _sex_worker_template_names()
        if template_name not in referenced_children
    ))


def _count_generated_sex_workers_by_district(
    organizations: list[dict[str, object]],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for organization in organizations:
        template_name = organization.get("template_name")
        district_name = organization.get("district")
        if (
            not isinstance(template_name, str)
            or not isinstance(district_name, str)
        ):
            continue
        _, template = _organization_template(template_name)
        counts[district_name] += _sex_worker_population(template)
    return counts


def _allocate_district_sex_worker_minimums(
    target_city_population: int,
    districts: list[District],
) -> tuple[int, dict[str, int]]:
    """Apportion the exact city floor using District.sex_index weights."""

    ratio = config.SEX_WORKER_POPULATION_RATIO
    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not 0 <= ratio <= 1
    ):
        raise ValueError("SEX_WORKER_POPULATION_RATIO 必须在 0–1 之间")
    if not districts:
        raise ValueError("性工作者下限分配至少需要一个 District")
    if any(
        isinstance(district.sex_index, bool)
        or not isinstance(district.sex_index, (int, float))
        or district.sex_index < 0
        for district in districts
    ):
        raise ValueError("District.sex_index 必须是非负数")
    total_weight = sum(float(district.sex_index) for district in districts)
    if total_weight <= 0:
        raise ValueError("District.sex_index 权重总和必须大于 0")

    target_minimum = math.ceil(target_city_population * ratio)
    raw = {
        district.name: target_minimum * district.sex_index / total_weight
        for district in districts
    }
    allocated = {
        district.name: math.floor(raw[district.name])
        for district in districts
    }
    remainder = target_minimum - sum(allocated.values())
    ranked = sorted(
        districts,
        key=lambda district: (
            raw[district.name] - allocated[district.name],
            district.name,
        ),
        reverse=True,
    )
    for district in ranked[:remainder]:
        allocated[district.name] += 1
    return target_minimum, allocated


class _OrganizationPopulationLedger:
    """Track how many generated Organization members occupy each map level."""

    def __init__(self, districts: list[District], streets: list[Street]) -> None:
        self.district_capacity = {
            district.name: district.population for district in districts
        }
        self.district_population_min: dict[str, int] = {}
        for district in districts:
            try:
                population_range = config.DISTRICT_TEMPLATES[
                    district.template
                ]["levels"][district.level]["population"]
                population_min = population_range[0]
            except (KeyError, TypeError, IndexError) as error:
                raise ValueError(
                    f"District“{district.name}”无法取得规划最小人口"
                ) from error
            if (
                isinstance(population_min, bool)
                or not isinstance(population_min, int)
                or population_min < 1
            ):
                raise ValueError(
                    f"District“{district.name}”的规划最小人口必须是正整数"
                )
            self.district_population_min[district.name] = population_min
        self.district_used = {district.name: 0 for district in districts}
        self.street_capacity: dict[str, int] = {}
        self.street_population_min: dict[str, int] = {}
        self.street_used = {street.name: 0 for street in streets}
        self.street_district = {
            street.name: street.district for street in streets
        }

        streets_by_district: dict[str, list[Street]] = defaultdict(list)
        for street in streets:
            if street.district is None:
                raise ValueError(f"Street“{street.name}”没有所属 District")
            streets_by_district[street.district].append(street)
        for district in districts:
            district_streets = streets_by_district[district.name]
            total_cells = sum(len(street.cells) for street in district_streets)
            if total_cells < 1:
                raise ValueError(f"District“{district.name}”没有可分配人口的 Street")

            def allocate(total_population: int) -> dict[str, int]:
                raw = {
                    street.name: (
                        total_population * len(street.cells) / total_cells
                    )
                    for street in district_streets
                }
                allocated = {
                    name: math.floor(value) for name, value in raw.items()
                }
                remainder = total_population - sum(allocated.values())
                ranked = sorted(
                    district_streets,
                    key=lambda street: (
                        raw[street.name] - allocated[street.name],
                        street.name,
                    ),
                    reverse=True,
                )
                for street in ranked[:remainder]:
                    allocated[street.name] += 1
                return allocated

            self.street_capacity.update(allocate(district.population))
            self.street_population_min.update(allocate(
                self.district_population_min[district.name]
            ))

    def district_remaining(self, district_name: str) -> int:
        return (
            self.district_capacity[district_name]
            - self.district_used[district_name]
        )

    def street_remaining(self, street_name: str) -> int:
        return self.street_capacity[street_name] - self.street_used[street_name]

    def reserve(
        self,
        organization: Mapping[str, object],
        *,
        enforce_capacity: bool = True,
    ) -> None:
        district = organization["district"]
        street = organization["street"]
        population = organization["population_usage"]
        if not isinstance(district, str) or district not in self.district_capacity:
            raise ValueError(f"Organization 使用未知 District“{district}”")
        if not isinstance(street, str) or street not in self.street_capacity:
            raise ValueError(f"Organization 使用未知 Street“{street}”")
        if self.street_district[street] != district:
            raise ValueError(
                f"Street“{street}”不属于 Organization 指定的 District“{district}”"
            )
        if isinstance(population, bool) or not isinstance(population, int):
            raise ValueError("Organization.population_usage 必须是整数")
        if enforce_capacity:
            if population > self.district_remaining(district):
                raise RuntimeError(
                    f"District“{district}”人口额度不足：需要 {population}，"
                    f"剩余 {self.district_remaining(district)}"
                )
            if population > self.street_remaining(street):
                raise RuntimeError(
                    f"Street“{street}”人口额度不足：需要 {population}，"
                    f"剩余 {self.street_remaining(street)}"
                )
        self.district_used[district] += population
        self.street_used[street] += population

    def reserve_required(self, organization: Mapping[str, object]) -> None:
        """Record a mandatory Organization without enforcing capacity."""
        self.reserve(organization, enforce_capacity=False)

    def snapshot(self) -> dict[str, object]:
        total_capacity = sum(self.district_capacity.values())
        total_used = sum(self.district_used.values())
        return {
            "capacity": total_capacity,
            "used": total_used,
            "remaining": total_capacity - total_used,
            "districts": {
                name: {
                    "capacity": capacity,
                    "used": self.district_used[name],
                    "remaining": capacity - self.district_used[name],
                }
                for name, capacity in self.district_capacity.items()
            },
            "streets": {
                name: {
                    "district": self.street_district[name],
                    "capacity": capacity,
                    "used": self.street_used[name],
                    "remaining": capacity - self.street_used[name],
                }
                for name, capacity in self.street_capacity.items()
            },
        }


class _SubOrganizationLocationPlanner:
    """Resolve child placement rules against the generated city map."""

    def __init__(
        self,
        rng: random.Random,
        districts: list[District],
        streets: list[Street],
        ledger: _OrganizationPopulationLedger,
        *,
        enforce_capacity: bool = True,
    ) -> None:
        self.rng = rng
        self.districts = list(districts)
        self.streets = list(streets)
        self.ledger = ledger
        self.enforce_capacity = enforce_capacity
        self.district_by_name = {
            district.name: district for district in districts
        }
        self.street_by_name = {street.name: street for street in streets}
        self.streets_by_district: dict[str, list[Street]] = defaultdict(list)
        for street in streets:
            if street.district is not None:
                self.streets_by_district[street.district].append(street)
        self.used_districts: dict[str, set[str]] = defaultdict(set)
        self.used_streets: dict[str, set[str]] = defaultdict(set)
        self.weighted_district_loads: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self.weighted_street_loads: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    @staticmethod
    def _name_fields(template: Mapping[str, object]) -> set[str]:
        return {
            field
            for raw_name in template["name_pool"]
            for _, field, _, _ in Formatter().parse(raw_name)
            if field
        }

    def _weighted_pick(self, items, weights):
        if not items:
            raise RuntimeError("Organization placement 没有可用候选位置")
        normalized = [max(0.0, float(weight)) for weight in weights]
        if not any(normalized):
            return self.rng.choice(items)
        return self.rng.choices(items, weights=normalized, k=1)[0]

    @staticmethod
    def _is_weighted_rule(rule: Mapping[str, object] | None) -> bool:
        return (
            isinstance(rule, Mapping)
            and rule.get("mode") in {
                "prosperity_weighted",
                "population_weighted",
            }
        )

    @staticmethod
    def _least_loaded(items, loads: Mapping[str, int]):
        if not items:
            return items
        minimum = min(loads.get(item.name, 0) for item in items)
        return [item for item in items if loads.get(item.name, 0) == minimum]

    def _candidate_streets(
        self,
        district_name: str | None,
        population: int,
    ) -> list[Street]:
        source = (
            self.streets
            if district_name is None
            else self.streets_by_district[district_name]
        )
        return [
            street for street in source
            if (
                not self.enforce_capacity
                or (
                    street.district is not None
                    and self.ledger.street_remaining(street.name) >= population
                    and self.ledger.district_remaining(street.district)
                    >= population
                )
            )
        ]

    def _choose_assigned_district(
        self,
        template_name: str,
        template: Mapping[str, object],
        population: int,
    ) -> str:
        candidates = [
            district for district in self.districts
            if (
                not self.enforce_capacity
                or self.ledger.district_remaining(district.name) >= population
            )
            and self._candidate_streets(district.name, population)
        ]
        if "district_name" in self._name_fields(template):
            unused = [
                district for district in candidates
                if district.name not in self.used_districts[template_name]
            ]
            if unused:
                candidates = unused
        chosen = self._weighted_pick(
            candidates,
            [self.ledger.district_population_min[item.name] for item in candidates],
        )
        return chosen.name

    def _choose_street(
        self,
        template_name: str,
        template: Mapping[str, object],
        district_name: str | None,
        rule: Mapping[str, object],
        population: int,
    ) -> Street:
        candidates = self._candidate_streets(district_name, population)
        if "street_name" in self._name_fields(template):
            unused = [
                street for street in candidates
                if street.name not in self.used_streets[template_name]
            ]
            if unused:
                candidates = unused
        mode = rule.get("mode")
        if self._is_weighted_rule(rule):
            candidates = self._least_loaded(
                candidates,
                self.weighted_street_loads[template_name],
            )
        if mode == "highest_prosperity":
            highest = max(street.prosperity for street in candidates)
            return self.rng.choice([
                street for street in candidates
                if street.prosperity == highest
            ])
        if mode in {"prosperity_weighted"}:
            return self._weighted_pick(
                candidates,
                [street.prosperity + 1 for street in candidates],
            )
        if mode == "population_weighted":
            return self._weighted_pick(
                candidates,
                [
                    self.ledger.street_population_min[street.name]
                    for street in candidates
                ],
            )
        if mode == "random":
            return self.rng.choice(candidates)
        raise ValueError(f"未知 street placement.mode“{mode}”")

    def resolve(
        self,
        parent: Mapping[str, object],
        child_template_name: str,
        declaration: Mapping[str, object],
        instance_index: int,
    ) -> tuple[str, str]:
        _, child_template = _organization_template(child_template_name)
        population = _organization_population(child_template)
        spawn_mode = declaration["spawn"]["mode"]
        assigned_district: str | None = None
        assigned_street: str | None = None

        if spawn_mode == "each_district":
            assigned_district = self.districts[instance_index].name
        elif spawn_mode == "each_street":
            parent_district = parent["district"]
            district_streets = self.streets_by_district[parent_district]
            assigned_street = district_streets[instance_index].name
            assigned_district = parent_district
        elif spawn_mode == "highest_prosperity":
            candidates = self._candidate_streets(None, population)
            highest = max(street.prosperity for street in candidates)
            selected = self.rng.choice([
                street for street in candidates
                if street.prosperity == highest
            ])
            assigned_street = selected.name
            assigned_district = selected.district
        elif spawn_mode != "fixed":
            raise ValueError(f"未知 sub_organization spawn.mode“{spawn_mode}”")

        placement = child_template.get("placement", {})
        district_rule = placement.get("district")
        if assigned_district is not None:
            district_name = assigned_district
        elif district_rule is None:
            district_name = None
        elif district_rule.get("mode") == "assigned_district":
            district_name = self._choose_assigned_district(
                child_template_name,
                child_template,
                population,
            )
        elif district_rule.get("mode") == "inherit_parent":
            district_name = parent["district"]
        elif district_rule.get("mode") == "highest_prosperity":
            eligible = [
                district for district in self.districts
                if (
                    not self.enforce_capacity
                    or self.ledger.district_remaining(district.name) >= population
                )
                and self._candidate_streets(district.name, population)
            ]
            district_name = _highest_prosperity(eligible).name
        elif self._is_weighted_rule(district_rule):
            eligible = [
                district for district in self.districts
                if (
                    not self.enforce_capacity
                    or self.ledger.district_remaining(district.name) >= population
                )
                and self._candidate_streets(district.name, population)
            ]
            eligible = self._least_loaded(
                eligible,
                self.weighted_district_loads[child_template_name],
            )
            mode = district_rule.get("mode")
            if mode == "prosperity_weighted":
                weights = [district.prosperity + 1 for district in eligible]
            elif mode == "population_weighted":
                weights = [
                    self.ledger.district_population_min[district.name]
                    for district in eligible
                ]
            else:
                raise ValueError(
                    f"未知 district placement.mode“{mode}”"
                )
            district_name = self._weighted_pick(eligible, weights).name
        else:
            raise ValueError(
                f"未知 district placement.mode“{district_rule.get('mode')}”"
            )

        street_rule = placement.get("street")
        if assigned_street is not None:
            street = self.street_by_name[assigned_street]
            if (
                self.enforce_capacity
                and self.ledger.street_remaining(street.name) < population
            ):
                raise RuntimeError(
                    f"assigned Street“{street.name}”人口额度不足以生成"
                    f"“{child_template_name}”"
                )
        elif street_rule is None:
            raise ValueError(
                f"Organization 模板“{child_template_name}”缺少 street placement"
            )
        elif street_rule.get("mode") == "assigned_street":
            raise ValueError(
                f"Organization 模板“{child_template_name}”需要 assigned_street，"
                "但父组织分布规则没有指定 Street"
            )
        else:
            street = self._choose_street(
                child_template_name,
                child_template,
                district_name,
                street_rule,
                population,
            )

        if district_name is None:
            district_name = street.district
        if street.district != district_name:
            raise ValueError(
                f"Organization“{child_template_name}”的 District“{district_name}”"
                f"与 Street“{street.name}”不匹配"
            )
        if self._is_weighted_rule(district_rule):
            self.weighted_district_loads[child_template_name][district_name] += 1
        if self._is_weighted_rule(street_rule):
            self.weighted_street_loads[child_template_name][street.name] += 1
        self.used_districts[child_template_name].add(district_name)
        self.used_streets[child_template_name].add(street.name)
        return district_name, street.name


def _required_organization_location(
    template_name: str,
    template: Mapping[str, object],
    districts: list[District],
    streets: list[Street],
    rng: random.Random,
    ledger: _OrganizationPopulationLedger,
    planner: _SubOrganizationLocationPlanner,
) -> tuple[District, Street]:
    placement = template.get("placement")
    if not isinstance(placement, Mapping):
        raise ValueError(
            f"Organization 模板“{template_name}”缺少 placement"
        )

    district_rule = placement.get("district")
    if not isinstance(district_rule, Mapping):
        raise ValueError(
            f"required Organization“{template_name}”缺少 placement.district"
        )

    streets_by_district: dict[str, list[Street]] = defaultdict(list)
    for street in streets:
        if street.district is not None:
            streets_by_district[street.district].append(street)
    district_candidates = [
        district for district in districts
        if streets_by_district[district.name]
    ]
    if not district_candidates:
        raise RuntimeError(
            f"required Organization“{template_name}”没有包含 Street 的 District"
        )

    def weighted_pick(items, weights):
        normalized = [max(0.0, float(weight)) for weight in weights]
        if not any(normalized):
            return rng.choice(items)
        return rng.choices(items, weights=normalized, k=1)[0]

    def choose(items, rule: Mapping[str, object], planned_population):
        mode = rule.get("mode")
        if mode == "highest_prosperity":
            return _highest_prosperity(items)
        if mode == "prosperity_weighted":
            return weighted_pick(items, [item.prosperity + 1 for item in items])
        if mode == "population_weighted":
            return weighted_pick(
                items,
                [planned_population(item) for item in items],
            )
        if mode == "random":
            return rng.choice(items)
        raise ValueError(
            f"未知 required Organization placement.mode“{mode}”"
        )

    if planner._is_weighted_rule(district_rule):
        district_candidates = planner._least_loaded(
            district_candidates,
            planner.weighted_district_loads[template_name],
        )
    district = choose(
        district_candidates,
        district_rule,
        lambda item: ledger.district_population_min[item.name],
    )

    street_rule = placement.get("street")
    if not isinstance(street_rule, Mapping):
        raise ValueError(
            f"required Organization“{template_name}”缺少 placement.street"
        )
    district_streets = list(streets_by_district[district.name])
    if not district_streets:
        raise RuntimeError(
            f"required Organization“{template_name}”在 District“{district.name}”"
            "没有 Street"
        )
    if planner._is_weighted_rule(street_rule):
        district_streets = planner._least_loaded(
            district_streets,
            planner.weighted_street_loads[template_name],
        )
    street = choose(
        district_streets,
        street_rule,
        lambda item: ledger.street_population_min[item.name],
    )
    if planner._is_weighted_rule(district_rule):
        planner.weighted_district_loads[template_name][district.name] += 1
    if planner._is_weighted_rule(street_rule):
        planner.weighted_street_loads[template_name][street.name] += 1
    return district, street


class _SpecialRoleDispatcher:
    """Deliver one player-defined Character to the first matching Organization."""

    def __init__(
        self,
        city_template: Mapping[str, object],
        special_role: dict | None,
    ) -> None:
        self.payload = special_role
        self.consumed = False
        if special_role is None:
            self.target_template = None
            return
        if not isinstance(special_role, dict):
            raise TypeError("special_role 必须是 dict 或 None")
        for field_name in ("organization_template", "role_template"):
            value = special_role.get(field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"special_role.{field_name} 必须是非空字符串")
        if "district" in special_role or "street" in special_role:
            raise ValueError("special_role 不能指定 district 或 street")

        target_template = special_role["organization_template"]
        _, template = _organization_template(target_template)
        role_template = special_role["role_template"]
        if role_template not in template["roles"]:
            raise ValueError(
                f"special_role.role_template“{role_template}”不是 Organization "
                f"模板“{target_template}”中的 role key"
            )

        reachable: set[str] = set()
        pending = list(city_template.get("required_organizations", ()))
        while pending:
            template_name = pending.pop()
            if template_name in reachable:
                continue
            reachable.add(template_name)
            _, reachable_template = _organization_template(template_name)
            pending.extend(
                declaration["template"]
                for declaration in reachable_template.get(
                    "sub_organizations", {}
                ).values()
            )
        if target_template not in reachable:
            raise ValueError(
                f"special_role 指定的 Organization 模板“{target_template}”"
                "不会由当前 CITY_TEMPLATE 直接生成"
            )
        self.target_template = target_template

    def take(self, template_name: str) -> dict | None:
        if (
            self.payload is not None
            and not self.consumed
            and template_name == self.target_template
        ):
            self.consumed = True
            return self.payload
        return None

    def ensure_consumed(self) -> None:
        if self.payload is not None and not self.consumed:
            raise RuntimeError("special_role 未被任何 Organization 消费")


def _generate_sub_organization_tree(
    parent: dict[str, object],
    organization_generator: OrganizationGenerator,
    planner: _SubOrganizationLocationPlanner,
    ledger: _OrganizationPopulationLedger,
    district_map: dict[str, int],
    generated: list[dict[str, object]],
    special_role_dispatcher: _SpecialRoleDispatcher,
    write_session: CityWriteSession | None,
    retain_organization_characters: bool,
) -> None:
    _, parent_template = _organization_template(parent["template_name"])
    declarations = parent_template.get("sub_organizations", {})
    assignments = parent["sub_organization_assignments"]

    for declaration_key, declaration in declarations.items():
        declaration_assignments = [
            assignment for assignment in assignments
            if assignment["declaration_key"] == declaration_key
        ]
        child_count = sum(
            assignment["count"] for assignment in declaration_assignments
        )
        assignment_by_index: dict[int, dict[str, object]] = {}
        for assignment in declaration_assignments:
            for instance_index in assignment["instance_indexes"]:
                if instance_index in assignment_by_index:
                    raise RuntimeError(
                        f"Organization“{parent['organization_id']}”的子组织实例"
                        f"索引 {instance_index} 被重复分配"
                    )
                assignment_by_index[instance_index] = assignment
        if set(assignment_by_index) != set(range(child_count)):
            raise RuntimeError(
                f"Organization“{parent['organization_id']}”的子组织声明"
                f"“{declaration_key}”实例分配不完整"
            )

        child_template_name = declaration["template"]
        for instance_index in range(child_count):
            assignment = assignment_by_index[instance_index]
            district_name, street_name = planner.resolve(
                parent,
                child_template_name,
                declaration,
                instance_index,
            )
            child = organization_generator.generate(
                district_name,
                street_name,
                child_template_name,
                district_map,
                special_role=special_role_dispatcher.take(
                    child_template_name
                ),
                parent_organization_id=parent["organization_id"],
                manager_id=assignment["manager_id"],
                parent_name=parent["parent_name"],
                root_organization_id=parent["root_organization_id"],
            )
            ledger.reserve_required(child)
            parent["sub_organization_ids"].append(child["organization_id"])
            assignment["organization_ids"].append(child["organization_id"])
            assignment["placements"].append({
                "organization_id": child["organization_id"],
                "district": district_name,
                "street": street_name,
                "address": child["address"],
            })
            if write_session is not None:
                write_session.write_organization(
                    child,
                    declaration_key=declaration_key,
                    instance_index=instance_index,
                )
            generated.append(
                child
                if retain_organization_characters
                else {
                    key: value
                    for key, value in child.items()
                    if key != "characters"
                }
            )
            _generate_sub_organization_tree(
                child,
                organization_generator,
                planner,
                ledger,
                district_map,
                generated,
                special_role_dispatcher,
                write_session,
                retain_organization_characters,
            )


def _generate_required_organizations(
    city_template: dict,
    districts: list[District],
    streets: list[Street],
    district_map: dict[str, int],
    rng: random.Random,
    special_role: dict | None,
    write_session: CityWriteSession | None,
    retain_organization_characters: bool,
    organization_cell_seed: int,
) -> tuple[
    list[dict[str, object]],
    _OrganizationPopulationLedger,
    OrganizationGenerator,
]:
    required = city_template.get("required_organizations")
    if not isinstance(required, Mapping):
        raise ValueError("CITY_TEMPLATES.required_organizations 必须是 {模板名: 数量}")
    if any(
        not isinstance(template_name, str)
        or not template_name
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
        for template_name, count in required.items()
    ):
        raise ValueError(
            "CITY_TEMPLATES.required_organizations 必须由非空模板名和正整数数量组成"
        )

    organization_generator = OrganizationGenerator(
        rng,
        organization_id_allocator=(
            write_session.organization_ids if write_session is not None else None
        ),
        character_id_allocator=(
            write_session.character_ids if write_session is not None else None
        ),
        character_id_claimer=(
            write_session.character_ids.claim
            if write_session is not None else None
        ),
        organization_cell_allocator=_OrganizationCellAllocator(
            streets,
            seed=organization_cell_seed,
        ),
    )
    population_ledger = _OrganizationPopulationLedger(districts, streets)
    location_planner = _SubOrganizationLocationPlanner(
        rng,
        districts,
        streets,
        population_ledger,
        enforce_capacity=False,
    )
    special_role_dispatcher = _SpecialRoleDispatcher(
        city_template,
        special_role,
    )
    generated: list[dict[str, object]] = []
    city_organization_templates = config.ORGANIZATION_TEMPLATES["city"]
    for template_name, count in required.items():
        if template_name not in city_organization_templates:
            raise KeyError(
                f"required_organizations 引用未知 city Organization 模板“{template_name}”"
            )
        organization_template = city_organization_templates[template_name]
        for _ in range(count):
            district, street = _required_organization_location(
                template_name,
                organization_template,
                districts,
                streets,
                rng,
                population_ledger,
                location_planner,
            )
            organization = organization_generator.generate(
                district.name,
                street.name,
                template_name,
                district_map,
                special_role=special_role_dispatcher.take(template_name),
            )
            population_ledger.reserve_required(organization)
            if write_session is not None:
                write_session.write_organization(organization)
            generated.append(
                organization
                if retain_organization_characters
                else {
                    key: value
                    for key, value in organization.items()
                    if key != "characters"
                }
            )
            _generate_sub_organization_tree(
                organization,
                organization_generator,
                location_planner,
                population_ledger,
                district_map,
                generated,
                special_role_dispatcher,
                write_session,
                retain_organization_characters,
            )
    special_role_dispatcher.ensure_consumed()
    return generated, population_ledger, organization_generator


def generate_city(
    target_city_population: int,
    seed: int = 12345,
    *,
    city_template: str = "罪恶都市",
    special_role: dict | None = None,
    store: SQLiteWorldStore | None = None,
    retain_organization_characters: bool | None = None,
) -> dict[str, object]:
    """Generate the map, city Organizations, then every District."""
    if city_template not in config.CITY_TEMPLATES:
        raise KeyError(f"未知 CITY_TEMPLATE“{city_template}”")

    template = config.CITY_TEMPLATES[city_template]
    write_session = (
        store.begin_city_generation(
            city_template=city_template,
            seed=seed,
            target_population=target_city_population,
        )
        if store is not None else None
    )
    retain_characters = (
        store is None
        if retain_organization_characters is None
        else retain_organization_characters
    )
    try:
        rng = random.Random(seed)
        districts = _create_districts(target_city_population, rng, city_template)
        _layout_districts(districts, rng)
        streets = _generate_streets(districts, rng)
        district_map = _district_map(districts, streets)
        if write_session is not None:
            write_session.write_map(districts, streets)
        organizations, population_ledger, organization_generator = (
            _generate_required_organizations(
                template,
                districts,
                streets,
                district_map,
                rng,
                special_role,
                write_session,
                retain_characters,
                seed,
            )
        )
        streets_by_district: dict[str, list[Street]] = defaultdict(list)
        for street in streets:
            if street.district is not None:
                streets_by_district[street.district].append(street)
        sex_worker_target, district_sex_worker_minimums = (
            _allocate_district_sex_worker_minimums(
                target_city_population,
                districts,
            )
        )
        initial_sex_workers_by_district = (
            _count_generated_sex_workers_by_district(organizations)
        )
        district_sex_worker_reports: list[dict[str, object]] = []
        independent_sex_worker_templates = frozenset(
            _independent_sex_worker_template_names()
        )
        for district in districts:
            district_report: dict[str, object] = {}
            organizations.extend(generate_district_organizations(
                district,
                streets_by_district[district.name],
                district_map,
                rng,
                organization_generator,
                population_ledger,
                write_session=write_session,
                retain_organization_characters=retain_characters,
                sex_worker_population_minimum=(
                    district_sex_worker_minimums[district.name]
                ),
                initial_sex_worker_population=(
                    initial_sex_workers_by_district[district.name]
                ),
                sex_worker_random_templates=(
                    independent_sex_worker_templates
                ),
                generation_report=district_report,
            ))
            district_sex_worker_reports.append(district_report)
        actual_sex_worker_count = sum(
            int(report["actual_count"])
            for report in district_sex_worker_reports
        )
        generated_sex_worker_organizations: Counter[str] = Counter()
        for report in district_sex_worker_reports:
            generated_sex_worker_organizations.update(
                report["generated_organizations_by_template"]
            )
        sex_worker_population = {
            "configured_ratio": config.SEX_WORKER_POPULATION_RATIO,
            "ratio_denominator": target_city_population,
            "target_minimum": sex_worker_target,
            "initial_count": sum(initial_sex_workers_by_district.values()),
            "generated_worker_count": sum(
                int(report["generated_worker_count"])
                for report in district_sex_worker_reports
            ),
            "generated_organization_count": sum(
                generated_sex_worker_organizations.values()
            ),
            "generated_organizations_by_template": dict(sorted(
                generated_sex_worker_organizations.items()
            )),
            "actual_count": actual_sex_worker_count,
            "overshoot": actual_sex_worker_count - sex_worker_target,
            "actual_ratio": (
                actual_sex_worker_count / target_city_population
                if target_city_population
                else 0.0
            ),
            "lower_bound_met": actual_sex_worker_count >= sex_worker_target,
            "districts": district_sex_worker_reports,
        }
        organization_population_usage = population_ledger.snapshot()
        result = {
            "city_id": (
                write_session.city_id if write_session is not None else None
            ),
            "seed": seed,
            "city_template": city_template,
            "target_city_population": target_city_population,
            "districts": districts,
            "streets": streets,
            "district_map": district_map,
            "organizations": organizations,
            "organization_population_usage": organization_population_usage,
            "sex_worker_population": sex_worker_population,
        }
        if write_session is not None:
            write_session.complete(
                organization_population_usage,
                additional_data={
                    "sex_worker_population": sex_worker_population,
                },
            )
        return result
    except Exception as error:
        if write_session is not None:
            write_session.fail(error)
        raise
