"""Generate District values and their required and ordinary Organizations."""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from .. import config
from ..character_config import SEX_SERVICE_PROFESSIONS
from ..models.entities import District, Street
from .organization_generator import OrganizationGenerator


LEVEL_ORGANIZATION_MULTIPLIERS = {"low": 1, "middle": 2, "high": 3}


def required_organization_counts(
    template: Mapping[str, Any],
    level: str,
) -> dict[str, int]:
    if level not in LEVEL_ORGANIZATION_MULTIPLIERS:
        raise ValueError(f"未知 District level“{level}”")
    required = template.get("required_organizations")
    if not isinstance(required, Mapping):
        raise ValueError("DISTRICT_TEMPLATES.required_organizations 必须是 mapping")
    multiplier = LEVEL_ORGANIZATION_MULTIPLIERS[level]
    if any(
        not isinstance(template_name, str)
        or not template_name
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
        for template_name, count in required.items()
    ):
        raise ValueError(
            "DISTRICT_TEMPLATES.required_organizations 必须由非空模板名和正整数数量组成"
        )
    return {
        template_name: count * multiplier
        for template_name, count in required.items()
    }


def generate_district(
    rng: random.Random,
    template_name: str,
    level: str,
    *,
    templates: Mapping[str, dict] | None = None,
) -> District:
    """Generate one District; level multipliers affect organizations only."""
    district_templates = config.DISTRICT_TEMPLATES if templates is None else templates
    template = district_templates[template_name]
    level_config = template["levels"][level]
    population_min, population_max = level_config["population"]
    prosperity_min, prosperity_max = level_config["prosperity"]
    area_min, area_max = level_config["area"]

    return District(
        template=template_name,
        name=rng.choice(template["district_name_pool"]),
        level=level,
        population=rng.randint(population_min, population_max),
        prosperity=rng.randint(prosperity_min, prosperity_max),
        area=rng.uniform(area_min, area_max),
        unorganized_population_ratio=template["unorganized_population_ratio"],
        sex_index=template["sex_index"],
        required_organizations=required_organization_counts(template, level),
    )


class _DistrictStreetPlanner:
    """Choose a Street from the currently eligible streets in one District."""

    def __init__(
        self,
        rng: random.Random,
        streets: list[Street],
        population_weights: Mapping[str, int],
    ) -> None:
        if not streets:
            raise ValueError("District 至少需要一条 Street")
        self.rng = rng
        self.streets = list(streets)
        self.population_weights = population_weights
        self.weighted_loads: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def _weighted_pick(self, items: list[Street], weights: list[float]) -> Street:
        normalized = [max(0.0, float(weight)) for weight in weights]
        if not any(normalized):
            return self.rng.choice(items)
        return self.rng.choices(items, weights=normalized, k=1)[0]

    def choose(
        self,
        template_name: str,
        template: Mapping[str, Any],
        *,
        candidates: list[Street] | None = None,
    ) -> Street:
        placement = template.get("placement")
        if not isinstance(placement, Mapping):
            raise ValueError(f"Organization 模板“{template_name}”缺少 placement")
        rule = placement.get("street")
        if not isinstance(rule, Mapping):
            raise ValueError(
                f"Organization 模板“{template_name}”缺少 street placement"
            )
        mode = rule.get("mode")
        candidates = list(self.streets if candidates is None else candidates)
        known_street_names = {street.name for street in self.streets}
        if not candidates:
            raise RuntimeError(
                f"Organization 模板“{template_name}”没有可用 Street"
            )
        if any(street.name not in known_street_names for street in candidates):
            raise ValueError("Street 候选必须属于当前 District")
        if mode in {"prosperity_weighted", "population_weighted"}:
            loads = self.weighted_loads[template_name]
            minimum = min(loads.get(street.name, 0) for street in candidates)
            candidates = [
                street for street in candidates
                if loads.get(street.name, 0) == minimum
            ]

        if mode == "highest_prosperity":
            highest = max(street.prosperity for street in candidates)
            chosen = self.rng.choice([
                street for street in candidates
                if street.prosperity == highest
            ])
        elif mode == "prosperity_weighted":
            chosen = self._weighted_pick(
                candidates,
                [street.prosperity + 1 for street in candidates],
            )
        elif mode == "population_weighted":
            try:
                weights = [
                    self.population_weights[street.name]
                    for street in candidates
                ]
            except KeyError as error:
                raise ValueError(
                    f"Street“{error.args[0]}”缺少规划人口权重"
                ) from error
            chosen = self._weighted_pick(candidates, weights)
        elif mode == "random":
            chosen = self.rng.choice(candidates)
        else:
            raise ValueError(f"未知 street placement.mode“{mode}”")

        if mode in {"prosperity_weighted", "population_weighted"}:
            self.weighted_loads[template_name][chosen.name] += 1
        return chosen


def _random_template(
    rng: random.Random,
    district: District,
    templates: Mapping[str, Mapping[str, Any]],
) -> str:
    names = list(templates)
    if not names:
        raise ValueError("没有可随机生成的 street Organization 模板")
    weights = []
    for name in names:
        prosperity = templates[name].get("prosperity")
        if (
            isinstance(prosperity, bool)
            or not isinstance(prosperity, (int, float))
            or not 0 <= prosperity <= 100
        ):
            raise ValueError(f"Organization 模板“{name}”的 prosperity 无效")
        compatibility = max(1.0, 101.0 - abs(district.prosperity - prosperity))
        weights.append(compatibility)
    return rng.choices(names, weights=weights, k=1)[0]


def generate_district_organizations(
    district: District,
    streets: list[Street],
    district_map: Mapping[str, int],
    rng: random.Random,
    organization_generator: OrganizationGenerator,
    population_ledger: Any,
    *,
    write_session: Any = None,
    retain_organization_characters: bool = True,
    sex_worker_population_minimum: int = 0,
    initial_sex_worker_population: int = 0,
    sex_worker_random_templates: frozenset[str] = frozenset(),
    generation_report: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Generate required then prosperity-matched Organizations for a District."""
    if not streets or any(street.district != district.name for street in streets):
        raise ValueError(f"District“{district.name}”的 Street 集合无效")
    street_templates = config.ORGANIZATION_TEMPLATES["street"]
    if (
        isinstance(sex_worker_population_minimum, bool)
        or not isinstance(sex_worker_population_minimum, int)
        or sex_worker_population_minimum < 0
    ):
        raise ValueError("sex_worker_population_minimum 必须是非负整数")
    if (
        isinstance(initial_sex_worker_population, bool)
        or not isinstance(initial_sex_worker_population, int)
        or initial_sex_worker_population < 0
    ):
        raise ValueError("initial_sex_worker_population 必须是非负整数")

    def sex_worker_count(template_name: str) -> int:
        return sum(
            role["count"]
            for role in street_templates[template_name]["roles"].values()
            if role["occupation"] in SEX_SERVICE_PROFESSIONS
        )

    ordinary_random_templates = {
        template_name: template
        for template_name, template in street_templates.items()
        if sex_worker_count(template_name) == 0
    }
    allowed_sex_worker_templates = {
        template_name: street_templates[template_name]
        for template_name in sorted(sex_worker_random_templates)
        if (
            template_name in street_templates
            and sex_worker_count(template_name) > 0
        )
    }
    if not ordinary_random_templates:
        raise ValueError("没有非性服务类 street Organization 可供随机生成")
    if (
        initial_sex_worker_population < sex_worker_population_minimum
        and not allowed_sex_worker_templates
    ):
        raise ValueError("该 District 没有可用于补足下限的性服务组织模板")
    for template_name in district.required_organizations:
        if template_name not in street_templates:
            raise KeyError(
                f"District“{district.name}”引用未知 street Organization "
                f"模板“{template_name}”"
            )
        if street_templates[template_name].get("sub_organizations"):
            raise ValueError(
                f"street Organization 模板“{template_name}”不能包含下级组织"
            )

    planner = _DistrictStreetPlanner(
        rng,
        streets,
        population_ledger.street_population_min,
    )
    generated: list[dict[str, object]] = []
    current_sex_worker_population = initial_sex_worker_population
    generated_sex_worker_population = 0
    generated_sex_worker_organizations: Counter[str] = Counter()

    def unfilled_streets() -> list[Street]:
        try:
            return [
                street for street in streets
                if population_ledger.street_used[street.name]
                < population_ledger.street_population_min[street.name]
            ]
        except KeyError as error:
            raise ValueError(
                f"Street“{error.args[0]}”缺少人口占用或规划人口下限"
            ) from error

    def create(
        template_name: str,
        *,
        required: bool,
        candidates: list[Street] | None = None,
        enforce_capacity: bool = False,
    ) -> None:
        nonlocal current_sex_worker_population
        nonlocal generated_sex_worker_population
        template = street_templates[template_name]
        street = planner.choose(
            template_name,
            template,
            candidates=candidates,
        )
        organization = organization_generator.generate(
            district.name,
            street.name,
            template_name,
            district_map,
        )
        if required:
            population_ledger.reserve_required(organization)
        else:
            population_ledger.reserve(
                organization,
                enforce_capacity=enforce_capacity,
            )
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
        workers = sex_worker_count(template_name)
        if workers:
            current_sex_worker_population += workers
            generated_sex_worker_population += workers
            generated_sex_worker_organizations[template_name] += 1

    def eligible_sex_worker_templates(
        *,
        require_capacity: bool,
    ) -> dict[str, Mapping[str, Any]]:
        remaining = (
            sex_worker_population_minimum - current_sex_worker_population
        )
        source = dict(allowed_sex_worker_templates)
        if require_capacity:
            source = {
                template_name: template
                for template_name, template in source.items()
                if any(
                    population_ledger.street_remaining(street.name)
                    >= sum(role["count"] for role in template["roles"].values())
                    and population_ledger.district_remaining(district.name)
                    >= sum(role["count"] for role in template["roles"].values())
                    for street in streets
                )
            }
        if not source:
            return source
        fitting = {
            template_name: template
            for template_name, template in source.items()
            if sex_worker_count(template_name) <= remaining
        }
        if fitting:
            return fitting
        smallest = min(sex_worker_count(name) for name in source)
        return {
            template_name: template
            for template_name, template in source.items()
            if sex_worker_count(template_name) == smallest
        }

    for template_name, count in district.required_organizations.items():
        for _ in range(count):
            create(template_name, required=True)

    while candidates := unfilled_streets():
        random_templates = dict(ordinary_random_templates)
        if current_sex_worker_population < sex_worker_population_minimum:
            random_templates.update(eligible_sex_worker_templates(
                require_capacity=False,
            ))
        create(
            _random_template(rng, district, random_templates),
            required=False,
            candidates=candidates,
        )

    while current_sex_worker_population < sex_worker_population_minimum:
        eligible_templates = eligible_sex_worker_templates(
            require_capacity=True,
        )
        if not eligible_templates:
            raise RuntimeError(
                f"District“{district.name}”人口容量不足，无法把性工作者"
                f"数量补足到 {sex_worker_population_minimum}；"
                f"当前为 {current_sex_worker_population}"
            )
        template_name = _random_template(rng, district, eligible_templates)
        population = sum(
            role["count"]
            for role in street_templates[template_name]["roles"].values()
        )
        capacity_candidates = [
            street for street in streets
            if population_ledger.street_remaining(street.name) >= population
            and population_ledger.district_remaining(district.name) >= population
        ]
        create(
            template_name,
            required=False,
            candidates=capacity_candidates,
            enforce_capacity=True,
        )

    if generation_report is not None:
        generation_report.update({
            "district": district.name,
            "district_template": district.template,
            "sex_index": district.sex_index,
            "target_minimum": sex_worker_population_minimum,
            "initial_count": initial_sex_worker_population,
            "generated_worker_count": generated_sex_worker_population,
            "generated_organization_count": sum(
                generated_sex_worker_organizations.values()
            ),
            "generated_organizations_by_template": dict(sorted(
                generated_sex_worker_organizations.items()
            )),
            "actual_count": current_sex_worker_population,
            "overshoot": (
                current_sex_worker_population
                - sex_worker_population_minimum
            ),
            "lower_bound_met": (
                current_sex_worker_population
                >= sex_worker_population_minimum
            ),
        })
    return generated
