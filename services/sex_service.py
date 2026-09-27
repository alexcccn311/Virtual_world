"""Commercial-sex pricing, demand, and worker-level services."""
from __future__ import annotations

import bisect
import calendar
import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..common.distributions import weighted_choice
from ..generators import character_generator
from ..models.entities import Character, CityPopulation, Organization


BREAST_PREFERENCE_ADJUSTMENTS = {
    "偏爱大胸": {"A": -5.0, "B": -5.0, "D": 5.0, "E": 5.0},
    "偏爱小胸": {"A": 5.0, "B": 5.0, "D": -5.0, "E": -5.0},
}

HYGIENE_SELECTION_ADJUSTMENTS = {
    "卫生良好": 5.0,
    "卫生较差": 0.0,
    "肮脏恶臭": -5.0,
    "污秽不堪": -10.0,
}

SERVICE_SKILL_SELECTION_BONUSES = {
    "熟练": 0.0,
    "专业": 3.0,
    "精通": 5.0,
    "大师": 7.0,
    "宗师": 9.0,
}

STYLE_MATCH_BONUS = 10.0
SEX_SERVICE_WAITING_PENALTY_PER_MINUTE = 0.4
SEX_SERVICE_PRICE_BURDEN_MAX_PENALTY = 80.0
SEX_SERVICE_PRICE_BURDEN_EXPONENT = 1.25
SEX_SERVICE_DURATIONS_MINUTES = {
    "手冲服务": 20,
    "口交服务": 30,
    "普通性交": 45,
    "肛交服务": 45,
    "包夜性交": 360,
    "色情角色扮演": 90,
    "轻度调教": 90,
    "重度危险调教": 120,
    "陪吸陪睡": 90,
}
SEX_WORKER_SERVICES_BY_OCCUPATION = {
    "站街女": ("手冲服务", "口交服务", "普通性交", "肛交服务"),
    "发廊妹": ("手冲服务", "口交服务", "普通性交"),
    "妓女": (
        "手冲服务", "口交服务", "普通性交", "肛交服务",
        "包夜性交", "色情角色扮演", "轻度调教",
    ),
    "SM妓女": (
        "手冲服务", "口交服务", "普通性交", "肛交服务",
        "包夜性交", "色情角色扮演", "轻度调教", "重度危险调教",
    ),
    "冰妹": (
        "手冲服务", "口交服务", "普通性交", "肛交服务",
        "包夜性交", "色情角色扮演", "陪吸陪睡",
    ),
    "性奴": tuple(config.SEX_SERVICE_BASE_PRICES),
}
_SKILL_LEVEL_INDEX = {
    level: index
    for index, level in enumerate(config.SKILL_LEVELS)
}


def _service_preference_weights(
    service_weights: object,
) -> tuple[tuple[str, float], ...]:
    if not isinstance(service_weights, Mapping):
        raise ValueError("客户的 sexual_service_weights 必须为服务权重字典")
    known_services = set(config.SEX_SERVICE_REQUIRED_SKILLS)
    if set(service_weights) != known_services:
        raise ValueError("客户的性服务偏好必须覆盖全部服务类型")

    weighted_services: list[tuple[str, float]] = []
    for service, weight in service_weights.items():
        if (
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError(f"性服务偏好 {service} 的权重必须为正数")
        weighted_services.append((service, float(weight)))
    if not math.isclose(
        sum(weight for _, weight in weighted_services),
        100.0,
        abs_tol=0.01,
    ):
        raise ValueError("客户的性服务偏好权重总和必须为 100")
    return tuple(weighted_services)


def _service_skill_level(
    skills: Mapping[str, object],
    service: str,
) -> str | None:
    required_skills = config.SEX_SERVICE_REQUIRED_SKILLS[service]
    levels: list[str] = []
    for skill in required_skills:
        level = skills.get(skill)
        if not isinstance(level, str) or level not in _SKILL_LEVEL_INDEX:
            return None
        levels.append(level)

    service_level = min(levels, key=_SKILL_LEVEL_INDEX.__getitem__)
    return service_level if service_level in SERVICE_SKILL_SELECTION_BONUSES else None


def choose_sex_worker(
    customer: Mapping[str, object],
    sex_workers: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], str]:
    """Choose one worker and the service the customer will request.

    ``customer["attraction_preferences"]`` must contain:

    - ``breasts``: one key from :data:`BREAST_PREFERENCE_ADJUSTMENTS`;
    - ``sexual_service_weights``: a positive 100-point weight distribution
      covering every configured service;
    - ``style``: one of the female temperament values generated for workers.

    Candidates are assumed to be the sex workers currently available in one
    venue. Age, occupation, availability, and price are intentionally not
    filtered or scored here.
    """
    preferences = customer.get("attraction_preferences")
    if not isinstance(preferences, Mapping):
        raise ValueError("客户缺少 attraction_preferences")

    breast_preference = preferences.get("breasts")
    if breast_preference not in BREAST_PREFERENCE_ADJUSTMENTS:
        raise ValueError(f"未知胸部偏好：{breast_preference}")

    style_preference = preferences.get("style")
    if style_preference not in character_generator.FEMALE_TEMPERAMENT_OPTIONS:
        raise ValueError(f"未知性工作者风格偏好：{style_preference}")

    service_preferences = _service_preference_weights(
        preferences.get("sexual_service_weights")
    )
    if not isinstance(sex_workers, Sequence) or isinstance(
        sex_workers,
        (str, bytes, bytearray),
    ):
        raise ValueError("候选性工作者必须为序列")
    if not sex_workers:
        raise ValueError("没有可供选择的性工作者")

    selected_worker: Mapping[str, object] | None = None
    selected_service: str | None = None
    selected_score = float("-inf")

    for worker in sex_workers:
        if not isinstance(worker, Mapping):
            raise ValueError("性工作者数据必须为字典")

        for service, preference_weight in service_preferences:
            try:
                score = sex_service_intention_score(
                    customer,
                    worker,
                    service,
                    waiting_minutes=0,
                )
            except ValueError as error:
                if str(error).startswith("性工作者无法提供服务"):
                    continue
                raise
            if preference_weight <= 0:
                continue
            if score > selected_score:
                selected_worker = worker
                selected_service = service
                selected_score = score

    if selected_worker is None or selected_service is None:
        raise ValueError("没有性工作者熟练掌握任何可选服务")
    return selected_worker, selected_service


def sex_service_intention_score(
    customer: Mapping[str, object],
    worker: Mapping[str, object],
    service: str,
    *,
    waiting_minutes: int,
    service_price: int = 0,
    free_access: bool = False,
) -> float:
    """Score one customer/worker/service choice, including queue waiting.

    Waiting is deliberately a linear penalty: every required minute subtracts
    exactly 0.4 points from the same preference score used by
    :func:`choose_sex_worker`.
    """

    if (
        isinstance(waiting_minutes, bool)
        or not isinstance(waiting_minutes, int)
        or waiting_minutes < 0
    ):
        raise ValueError("waiting_minutes 必须是非负整数")
    preferences = customer.get("attraction_preferences")
    if not isinstance(preferences, Mapping):
        raise ValueError("客户缺少 attraction_preferences")
    breast_preference = preferences.get("breasts")
    if breast_preference not in BREAST_PREFERENCE_ADJUSTMENTS:
        raise ValueError(f"未知胸部偏好：{breast_preference}")
    style_preference = preferences.get("style")
    if style_preference not in character_generator.FEMALE_TEMPERAMENT_OPTIONS:
        raise ValueError(f"未知性工作者风格偏好：{style_preference}")
    service_preferences = dict(_service_preference_weights(
        preferences.get("sexual_service_weights")
    ))
    if service not in service_preferences:
        raise ValueError(f"未知性服务项目：{service}")

    appearance_score = worker.get("appearance_score")
    if (
        not isinstance(appearance_score, (int, float))
        or isinstance(appearance_score, bool)
        or not 0 <= appearance_score <= 100
    ):
        raise ValueError("性工作者缺少有效的 appearance_score")
    skills = worker.get("skills")
    if not isinstance(skills, Mapping):
        raise ValueError("性工作者缺少技能字典")
    service_level = _service_skill_level(skills, service)
    if service_level is None:
        raise ValueError(f"性工作者无法提供服务：{service}")
    hygiene = worker.get("hygiene")
    if hygiene not in HYGIENE_SELECTION_ADJUSTMENTS:
        raise ValueError(f"性工作者使用未知清洁程度：{hygiene}")

    breast_adjustment = BREAST_PREFERENCE_ADJUSTMENTS[breast_preference].get(
        worker.get("cup_size"),
        0.0,
    )
    style_bonus = (
        STYLE_MATCH_BONUS
        if worker.get("temperament") == style_preference
        else 0.0
    )
    price_penalty = sex_service_price_burden_penalty(
        customer.get("cash"),
        service_price,
        free_access=free_access,
    )
    return round(
        float(appearance_score)
        + breast_adjustment
        + HYGIENE_SELECTION_ADJUSTMENTS[hygiene]
        + style_bonus
        + service_preferences[service]
        + SERVICE_SKILL_SELECTION_BONUSES[service_level]
        - SEX_SERVICE_WAITING_PENALTY_PER_MINUTE * waiting_minutes
        - price_penalty,
        4,
    )


def sex_service_price_burden_penalty(
    cash: object,
    service_price: int,
    *,
    free_access: bool = False,
) -> float:
    """Return a nonlinear price penalty or reject an unaffordable service."""

    if (
        isinstance(service_price, bool)
        or not isinstance(service_price, int)
        or service_price < 0
    ):
        raise ValueError("service_price 必须是非负整数")
    if not isinstance(free_access, bool):
        raise ValueError("free_access 必须是 bool")
    if free_access or service_price == 0:
        return 0.0
    if (
        not isinstance(cash, (int, float))
        or isinstance(cash, bool)
        or not math.isfinite(cash)
        or cash < 0
    ):
        raise ValueError("客户缺少有效 cash")
    if service_price > cash:
        raise ValueError("客户无法支付服务价格")
    if cash == 0:
        raise ValueError("客户无法支付服务价格")
    burden = service_price / float(cash)
    return round(
        SEX_SERVICE_PRICE_BURDEN_MAX_PENALTY
        * burden ** SEX_SERVICE_PRICE_BURDEN_EXPONENT,
        4,
    )


def _character_value(character: Character | Mapping[str, object], field: str) -> object:
    if isinstance(character, Mapping):
        if field == "id":
            return character.get("id", character.get("character_id"))
        return character.get(field)
    return getattr(character, field)


def _stable_seed(seed: int, *parts: object) -> int:
    payload = ":".join((str(seed), *(str(part) for part in parts)))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")


def _cash_multiplier(cash: float) -> float:
    multiplier = config.SEX_SERVICE_VISIT_CASH_MULTIPLIERS[0][1]
    for minimum_cash, candidate in config.SEX_SERVICE_VISIT_CASH_MULTIPLIERS:
        if cash < minimum_cash:
            break
        multiplier = candidate
    return float(multiplier)


def monthly_sex_service_visit_frequency(
    character: Character | Mapping[str, object],
    *,
    privilege_scope: str | None = None,
    seed: int = 12345,
) -> float:
    """Return this character's stable expected visits per calendar month.

    Age is only an adult eligibility gate. Occupation affects visit time, not
    frequency. Cash is the sole wealth input; income, debt, and other assets
    are intentionally ignored. A crime-syndicate privilege only softens the
    cash constraint and adds a modest scope-dependent uplift. Whether one
    particular visit is free is decided later, against the selected venue.
    """

    sex = _character_value(character, "sex")
    age = _character_value(character, "age")
    if sex != "male" or not isinstance(age, int) or age < 18:
        return 0.0

    character_id = _character_value(character, "id")
    if not isinstance(character_id, str) or not character_id:
        raise ValueError("成年男性缺少角色 ID")
    preferences = _character_value(character, "sexual_preferences")
    if not isinstance(preferences, Mapping):
        raise ValueError(f"成年男性 {character_id} 缺少性欲偏好")
    desire = preferences.get("desire")
    if desire not in config.SEX_SERVICE_MONTHLY_BASE_FREQUENCIES:
        raise ValueError(f"成年男性 {character_id} 使用未知性欲等级 {desire}")

    cash = _character_value(character, "cash")
    if (
        not isinstance(cash, (int, float))
        or isinstance(cash, bool)
        or not math.isfinite(cash)
    ):
        raise ValueError(f"成年男性 {character_id} 缺少有效 cash")
    hobbies = _character_value(character, "hobbies")
    if not isinstance(hobbies, Sequence) or isinstance(hobbies, (str, bytes)):
        raise ValueError(f"成年男性 {character_id} 缺少爱好列表")

    low, high = config.SEX_SERVICE_VISIT_INDIVIDUAL_MULTIPLIER_RANGE
    stable_rng = random.Random(_stable_seed(seed, character_id, "visit-frequency"))
    individual_multiplier = stable_rng.uniform(low, high)
    hobby_multiplier = (
        config.SEX_SERVICE_VISIT_HOBBY_MULTIPLIER
        if "嫖妓" in hobbies
        else 1.0
    )
    affordability_multiplier = _cash_multiplier(float(cash))
    privilege_multiplier = 1.0
    if privilege_scope is not None:
        if privilege_scope not in config.SEX_SERVICE_CRIME_PRIVILEGE_FREQUENCY_MULTIPLIERS:
            raise ValueError(f"未知犯罪集团性服务权限级别：{privilege_scope}")
        relief = config.SEX_SERVICE_CRIME_PRIVILEGE_AFFORDABILITY_RELIEF[
            privilege_scope
        ]
        if affordability_multiplier < 1.0:
            affordability_multiplier += (
                1.0 - affordability_multiplier
            ) * relief
        privilege_multiplier = (
            config.SEX_SERVICE_CRIME_PRIVILEGE_FREQUENCY_MULTIPLIERS[
                privilege_scope
            ]
        )

    frequency = (
        config.SEX_SERVICE_MONTHLY_BASE_FREQUENCIES[desire]
        * individual_multiplier
        * hobby_multiplier
        * affordability_multiplier
        * privilege_multiplier
    )
    return max(0.0, frequency)


@dataclass(frozen=True, slots=True)
class SexServicePrivilege:
    """A member's free-service influence territory."""

    scope: str
    city_id: str | None
    district: str | None
    street: str | None

    def __post_init__(self) -> None:
        if self.scope not in {"street", "district", "city"}:
            raise ValueError(f"未知性服务权限级别：{self.scope}")
        if self.scope in {"street", "district"} and not self.district:
            raise ValueError(f"{self.scope} 权限缺少 district 锚点")
        if self.scope == "street" and not self.street:
            raise ValueError("street 权限缺少 street 锚点")


def _organization_value(
    organization: Organization | Mapping[str, object],
    *fields: str,
) -> object:
    if isinstance(organization, Mapping):
        for field in fields:
            if field in organization:
                return organization[field]
        return None
    for field in fields:
        if hasattr(organization, field):
            return getattr(organization, field)
    return None


def sex_service_crime_organization_depths(
    organizations: Sequence[Organization | Mapping[str, object]],
) -> dict[str, int]:
    """Return hierarchy depth for organizations below configured crime roots."""

    organization_data: dict[str, tuple[str, str | None]] = {}
    for organization in organizations:
        organization_id = _organization_value(
            organization, "id", "organization_id"
        )
        template_name = _organization_value(
            organization, "type", "template_name"
        )
        parent_id = _organization_value(organization, "parent_organization_id")
        if not isinstance(organization_id, str) or not organization_id:
            raise ValueError("组织缺少有效 ID")
        if not isinstance(template_name, str) or not template_name:
            raise ValueError(f"组织 {organization_id} 缺少模板类型")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"组织 {organization_id} 的父组织 ID 无效")
        organization_data[organization_id] = (template_name, parent_id)

    result: dict[str, int] = {}
    for organization_id in organization_data:
        current_id: str | None = organization_id
        visited: set[str] = set()
        depth = 0
        while current_id is not None:
            if current_id in visited:
                raise ValueError(f"组织层级存在循环：{organization_id}")
            visited.add(current_id)
            current = organization_data.get(current_id)
            if current is None:
                break
            template_name, parent_id = current
            if template_name in config.SEX_SERVICE_CRIME_ROOT_TEMPLATES:
                result[organization_id] = depth
                break
            current_id = parent_id
            depth += 1
    return result


def _organization_template(template_name: str) -> Mapping[str, object]:
    matches = [
        template
        for templates in config.ORGANIZATION_TEMPLATES.values()
        if (template := templates.get(template_name)) is not None
    ]
    if len(matches) != 1:
        raise ValueError(f"无法唯一定位组织模板：{template_name}")
    return matches[0]


def organization_role_depth(template_name: str, role_key: str) -> int:
    """Return a configured role's reporting depth inside its organization."""

    roles = _organization_template(template_name).get("roles")
    if not isinstance(roles, Mapping) or role_key not in roles:
        raise ValueError(f"组织模板 {template_name} 不包含岗位 {role_key}")
    depth = 0
    current = role_key
    visited: set[str] = set()
    while True:
        if current in visited:
            raise ValueError(f"组织模板 {template_name} 的岗位汇报关系存在循环")
        visited.add(current)
        role = roles[current]
        if not isinstance(role, Mapping):
            raise ValueError(f"组织模板 {template_name} 的岗位 {current} 无效")
        parent = role.get("reports_to")
        if parent is None:
            return depth
        if not isinstance(parent, str) or parent not in roles:
            raise ValueError(f"组织模板 {template_name} 的岗位 {current} 汇报对象无效")
        current = parent
        depth += 1


def organization_role_has_crime_privilege(
    template_name: str,
    role_key: str,
) -> bool:
    """Return whether this is a core authority/field role, not support staff."""

    template = _organization_template(template_name)
    roles = template.get("roles")
    if not isinstance(roles, Mapping) or role_key not in roles:
        raise ValueError(f"组织模板 {template_name} 不包含岗位 {role_key}")
    role = roles[role_key]
    if not isinstance(role, Mapping):
        raise ValueError(f"组织模板 {template_name} 的岗位 {role_key} 无效")
    explicit = role.get("sex_service_crime_privilege_eligible")
    if explicit is not None:
        if not isinstance(explicit, bool):
            raise ValueError(
                f"组织模板 {template_name} 的岗位 {role_key} 使用无效权限资格"
            )
        return explicit
    if (
        template_name,
        role_key,
    ) in config.SEX_SERVICE_CRIME_PRIVILEGE_EXCLUDED_ROLES:
        return False
    return (
        role.get("occupation")
        in config.SEX_SERVICE_CRIME_PRIVILEGE_ELIGIBLE_OCCUPATIONS
    )


def sex_service_crime_privilege_scope(
    template_name: str,
    organization_depth: int,
    role_key: str,
) -> str | None:
    """Resolve influence from both organization layer and member position."""

    if (
        isinstance(organization_depth, bool)
        or not isinstance(organization_depth, int)
        or organization_depth < 0
    ):
        raise ValueError("organization_depth 必须是非负整数")
    if not organization_role_has_crime_privilege(template_name, role_key):
        return None
    role_depth = organization_role_depth(template_name, role_key)
    return config.SEX_SERVICE_CRIME_PRIVILEGE_SCOPE_RULES.get(
        (organization_depth, role_depth),
        "street",
    )


def sex_service_privilege_covers_venue(
    privilege: SexServicePrivilege | None,
    venue: Organization | Mapping[str, object],
) -> bool:
    """Return whether a selected venue lies inside a member's territory."""

    if privilege is None:
        return False
    venue_city_id = _organization_value(venue, "city_id")
    if (
        privilege.city_id is not None
        and venue_city_id is not None
        and venue_city_id != privilege.city_id
    ):
        return False
    if privilege.scope == "city":
        return True
    venue_district = _organization_value(venue, "district", "district_name")
    if venue_district != privilege.district:
        return False
    if privilege.scope == "district":
        return True
    venue_street = _organization_value(venue, "street", "street_name")
    return venue_street == privilege.street


@dataclass(frozen=True, slots=True)
class SexServiceVenueSelection:
    organization_id: str
    venue_type: str
    free_access: bool
    quoted_price: int
    customer_charge: int
    distance_cells: int


@dataclass(frozen=True, slots=True)
class SexServiceAppointment:
    worker_character_id: str
    service_name: str
    arrival_at: datetime
    service_start_at: datetime
    service_end_at: datetime
    waiting_minutes: int
    intention_score: float
    quoted_price: int
    customer_charge: int
    venue_commission_bps: int
    worker_earnings: int


def choose_player_organization_appointment(
    customer: Mapping[str, object],
    sex_workers: Sequence[Mapping[str, object]],
    arrival_at: datetime,
    worker_available_at: Mapping[str, datetime],
    *,
    venue_type: str,
    free_access: bool = False,
    seed: int = 12345,
) -> SexServiceAppointment:
    """Append one arriving customer to the best worker's continuous queue.

    ``worker_available_at`` is the end of every reservation already accepted
    for each worker.  A busy worker's new service starts exactly at that chain
    end; an idle worker starts at the customer's arrival.  Consequently an
    appointment can extend an existing queue but can never create a gap inside
    it.
    """

    if arrival_at.tzinfo is None or arrival_at.utcoffset() is None:
        raise ValueError("arrival_at 必须是带时区的 datetime")
    if not isinstance(sex_workers, Sequence) or isinstance(
        sex_workers,
        (str, bytes, bytearray),
    ):
        raise ValueError("候选性工作者必须为序列")
    if not sex_workers:
        raise ValueError("没有可供选择的性工作者")
    customer_id = _character_value(customer, "id")
    if not isinstance(customer_id, str) or not customer_id:
        raise ValueError("客户缺少有效 ID")

    best: tuple[tuple[float, int, int], SexServiceAppointment] | None = None
    for worker in sex_workers:
        if not isinstance(worker, Mapping):
            raise ValueError("性工作者数据必须为字典")
        worker_id = _character_value(worker, "id")
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("性工作者缺少有效 ID")
        available_at = worker_available_at.get(worker_id, arrival_at)
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError(f"性工作者 {worker_id} 的可用时间缺少时区")
        available_at = available_at.astimezone(arrival_at.tzinfo)
        service_start = max(arrival_at, available_at)
        waiting_minutes = max(
            0,
            math.ceil((service_start - arrival_at).total_seconds() / 60.0),
        )
        for service in sex_worker_available_services(worker):
            occupation = _character_value(worker, "occupation")
            worker_level = _character_value(worker, "sex_worker_level")
            if not isinstance(occupation, str):
                raise ValueError(f"性工作者 {worker_id} 缺少职业")
            if isinstance(worker_level, bool) or not isinstance(worker_level, int):
                raise ValueError(f"性工作者 {worker_id} 缺少有效等级")
            quoted_price = quote_sex_service_price(
                service,
                venue_type,
                worker_level,
                occupation,
            )
            try:
                score = sex_service_intention_score(
                    customer,
                    worker,
                    service,
                    waiting_minutes=waiting_minutes,
                    service_price=quoted_price,
                    free_access=free_access,
                )
            except ValueError as error:
                if str(error) == "客户无法支付服务价格":
                    continue
                raise
            customer_charge = 0 if free_access else quoted_price
            commission_bps = sex_service_commission_bps(
                venue_type,
                occupation,
            )
            worker_earnings = sex_service_worker_earnings(
                customer_charge,
                venue_type,
                occupation,
            )
            service_end = service_start + timedelta(
                minutes=sex_service_duration_minutes(service)
            )
            appointment = SexServiceAppointment(
                worker_character_id=worker_id,
                service_name=service,
                arrival_at=arrival_at,
                service_start_at=service_start,
                service_end_at=service_end,
                waiting_minutes=waiting_minutes,
                intention_score=score,
                quoted_price=quoted_price,
                customer_charge=customer_charge,
                venue_commission_bps=commission_bps,
                worker_earnings=worker_earnings,
            )
            tie_breaker = _stable_seed(
                seed,
                customer_id,
                worker_id,
                service,
                arrival_at.isoformat(),
                "player-appointment-choice",
            )
            key = (score, -waiting_minutes, tie_breaker)
            if best is None or key > best[0]:
                best = key, appointment

    if best is None:
        raise ValueError("玩家组织没有可支付且可提供的性工作者服务组合")
    return best[1]


class SexServiceVenueIndex:
    """Cache venue groups and home-to-venue distances for repeated choices.

    A city can contain hundreds of thousands of customers, while many family
    members share the same home cell.  Sorting once per ``(home, venue type)``
    prevents every visit from rescanning and resorting every venue in the city.
    Dynamic capacity remains on each mutable venue summary and is checked when
    the six candidates are collected.
    """

    def __init__(self, venues: Sequence[Mapping[str, object]]) -> None:
        if not isinstance(venues, Sequence) or isinstance(
            venues,
            (str, bytes, bytearray),
        ):
            raise ValueError("场所摘要必须为序列")
        self._by_type: dict[str, tuple[Mapping[str, object], ...]] = {}
        grouped: dict[str, list[Mapping[str, object]]] = {}
        minimum_prices: dict[str, int] = {}
        seen_ids: set[str] = set()
        for venue in venues:
            if not isinstance(venue, Mapping):
                raise ValueError("场所摘要必须为字典")
            venue_id, venue_type = _venue_identity(venue)
            if venue_id in seen_ids:
                raise ValueError(f"场所摘要包含重复 organization_id：{venue_id}")
            seen_ids.add(venue_id)
            _cell_coordinate(venue.get("address"), f"venue {venue_id}.address")
            min_price, _ = _venue_price_range(venue, venue_id)
            grouped.setdefault(venue_type, []).append(venue)
            minimum_prices[venue_type] = min(
                min_price,
                minimum_prices.get(venue_type, min_price),
            )
        self._by_type = {
            venue_type: tuple(group)
            for venue_type, group in grouped.items()
        }
        self._minimum_prices = minimum_prices
        self._nearest_cache: dict[
            tuple[str, tuple[int, int]],
            tuple[Mapping[str, object], ...],
        ] = {}

    def affordable_types(
        self,
        cash: float,
        privilege: SexServicePrivilege | None,
    ) -> set[str]:
        cash_budget = max(
            0,
            math.floor(float(cash) * config.SEX_SERVICE_SIMPLIFIED_CASH_RATIO),
        )
        result = {
            venue_type
            for venue_type, minimum_price in self._minimum_prices.items()
            if cash_budget >= minimum_price
        }
        if privilege is not None:
            result.update(
                venue_type
                for venue_type, venues in self._by_type.items()
                if any(
                    sex_service_privilege_covers_venue(privilege, venue)
                    for venue in venues
                )
            )
        return result

    def nearest(
        self,
        venue_type: str,
        home_address: tuple[int, int],
    ) -> tuple[Mapping[str, object], ...]:
        key = venue_type, home_address
        cached = self._nearest_cache.get(key)
        if cached is not None:
            return cached
        venues = self._by_type.get(venue_type, ())
        ordered = tuple(sorted(
            venues,
            key=lambda venue: (
                sex_service_cell_distance(
                    home_address,
                    _cell_coordinate(
                        venue.get("address"),
                        f"venue {_venue_identity(venue)[0]}.address",
                    ),
                ),
                _venue_identity(venue)[0],
            ),
        ))
        self._nearest_cache[key] = ordered
        return ordered


def sex_service_duration_minutes(service: str) -> int:
    try:
        return SEX_SERVICE_DURATIONS_MINUTES[service]
    except KeyError as error:
        raise ValueError(f"未知性服务项目：{service}") from error


def sex_service_daily_capacity(sex_worker_count: int) -> int:
    if (
        isinstance(sex_worker_count, bool)
        or not isinstance(sex_worker_count, int)
        or sex_worker_count < 0
    ):
        raise ValueError("sex_worker_count 必须是非负整数")
    return sex_worker_count * config.SEX_SERVICE_DAILY_CLIENTS_PER_WORKER


def _venue_identity(venue: Mapping[str, object]) -> tuple[str, str]:
    venue_id = venue.get("organization_id", venue.get("id"))
    venue_type = venue.get("template_name", venue.get("type"))
    if not isinstance(venue_id, str) or not venue_id:
        raise ValueError("场所摘要缺少 organization_id")
    if not isinstance(venue_type, str) or not venue_type:
        raise ValueError(f"场所 {venue_id} 缺少 template_name")
    if venue_type not in config.SEX_SERVICE_VENUE_OPEN_PERIODS:
        raise ValueError(f"组织 {venue_id} 不是性服务场所")
    return venue_id, venue_type


def _venue_price_range(
    venue: Mapping[str, object],
    venue_id: str,
) -> tuple[int, int]:
    min_price = venue.get("min_price")
    max_price = venue.get("max_price")
    if (
        isinstance(min_price, bool)
        or not isinstance(min_price, int)
        or min_price < 0
        or isinstance(max_price, bool)
        or not isinstance(max_price, int)
        or max_price < min_price
    ):
        raise ValueError(f"场所 {venue_id} 使用无效价格区间")
    return min_price, max_price


def _venue_has_capacity(
    venue: Mapping[str, object],
    venue_id: str,
    *,
    player_organization_id: str | None,
    daily_visit_counts: Mapping[str, int] | None = None,
) -> bool:
    daily_visits = (
        daily_visit_counts.get(venue_id, 0)
        if daily_visit_counts is not None
        else venue.get("daily_visit_count")
    )
    if (
        isinstance(daily_visits, bool)
        or not isinstance(daily_visits, int)
        or daily_visits < 0
    ):
        raise ValueError(f"场所 {venue_id} 使用无效 daily_visit_count")
    return daily_visits < sex_service_daily_capacity(venue.get("sex_worker_count"))


def simplified_sex_service_charge(cash: float, venue_max_price: int) -> int:
    """Charge 10% of cash, capped by this venue's priciest service."""

    if (
        not isinstance(cash, (int, float))
        or isinstance(cash, bool)
        or not math.isfinite(cash)
    ):
        raise ValueError("cash 必须是有限数值")
    if (
        isinstance(venue_max_price, bool)
        or not isinstance(venue_max_price, int)
        or venue_max_price < 0
    ):
        raise ValueError("venue_max_price 必须是非负整数")
    return min(venue_max_price, max(0, math.floor(cash * config.SEX_SERVICE_SIMPLIFIED_CASH_RATIO)))


def sex_service_venue_type_weights(
    cash: float,
    available_types: Sequence[str] | None = None,
) -> dict[str, float]:
    """Return cash-tier weights without treating a higher price as better."""

    if (
        not isinstance(cash, (int, float))
        or isinstance(cash, bool)
        or not math.isfinite(cash)
    ):
        raise ValueError("cash 必须是有限数值")
    tier_index = bisect.bisect_right(
        config.SEX_SERVICE_CASH_TIER_THRESHOLDS,
        max(0.0, float(cash)),
    )
    configured = config.SEX_SERVICE_VENUE_TYPE_WEIGHTS_BY_CASH_TIER[tier_index]
    selected_types = set(configured) if available_types is None else set(available_types)
    unknown = selected_types - set(configured)
    if unknown:
        raise ValueError(f"未知性服务场所类型：{'、'.join(sorted(unknown))}")
    return {
        venue_type: float(weight)
        for venue_type, weight in configured.items()
        if venue_type in selected_types
    }


def _cell_coordinate(value: object, field_name: str) -> tuple[int, int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError(f"{field_name} 必须是二元整数 cell 坐标")
    return int(value[0]), int(value[1])


def sex_service_cell_distance(
    left: Sequence[int],
    right: Sequence[int],
) -> int:
    left_q, left_r = _cell_coordinate(left, "left")
    right_q, right_r = _cell_coordinate(right, "right")
    dq, dr = left_q - right_q, left_r - right_r
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def sex_service_venue_selection_weight(
    *,
    customer_id: str,
    venue_id: str,
    distance_cells: int,
    familiarity_visits: int,
    appeal_score: float,
    free_access: bool,
    seed: int = 12345,
) -> float:
    """Combine soft distance decay with capped familiarity and appeal."""

    if not isinstance(customer_id, str) or not customer_id:
        raise ValueError("customer_id 必须是非空字符串")
    if not isinstance(venue_id, str) or not venue_id:
        raise ValueError("venue_id 必须是非空字符串")
    if isinstance(distance_cells, bool) or not isinstance(distance_cells, int) or distance_cells < 0:
        raise ValueError("distance_cells 必须是非负整数")
    if (
        isinstance(familiarity_visits, bool)
        or not isinstance(familiarity_visits, int)
        or familiarity_visits < 0
    ):
        raise ValueError("familiarity_visits 必须是非负整数")
    if (
        not isinstance(appeal_score, (int, float))
        or isinstance(appeal_score, bool)
        or not math.isfinite(appeal_score)
        or not 0 <= appeal_score <= 100
    ):
        raise ValueError("appeal_score 必须介于 0 至 100")

    distance_weight = (
        1.0 + distance_cells / config.SEX_SERVICE_VENUE_DISTANCE_SCALE_CELLS
    ) ** -config.SEX_SERVICE_VENUE_DISTANCE_EXPONENT
    familiarity_weight = 1.0 + min(
        config.SEX_SERVICE_VENUE_FAMILIARITY_MAX_BONUS,
        config.SEX_SERVICE_VENUE_FAMILIARITY_LOG_FACTOR
        * math.log1p(familiarity_visits),
    )
    appeal_weight = (
        config.SEX_SERVICE_VENUE_APPEAL_BASE
        + config.SEX_SERVICE_VENUE_APPEAL_RANGE * float(appeal_score) / 100.0
    )
    affinity_rng = random.Random(
        _stable_seed(seed, customer_id, venue_id, "venue-affinity")
    )
    personal_affinity = affinity_rng.uniform(
        *config.SEX_SERVICE_VENUE_PERSONAL_AFFINITY_RANGE
    )
    free_weight = (
        config.SEX_SERVICE_VENUE_FREE_ACCESS_MULTIPLIER
        if free_access
        else 1.0
    )
    return (
        distance_weight
        * familiarity_weight
        * appeal_weight
        * personal_affinity
        * free_weight
    )


def select_sex_service_venue(
    customer: Character | Mapping[str, object],
    venues: Sequence[Mapping[str, object]] | SexServiceVenueIndex,
    *,
    privilege: SexServicePrivilege | None = None,
    familiarity: Mapping[str, int] | None = None,
    player_organization_id: str | None = None,
    daily_visit_counts: Mapping[str, int] | None = None,
    seed: int = 12345,
    visit_key: object = "visit",
) -> SexServiceVenueSelection | None:
    """Select one venue without scanning its individual workers at visit time.

    Venue summaries must provide organization_id, template_name, address,
    min_price, max_price, appeal_score, sex_worker_count and daily_visit_count.
    All venues, including the player organization, use the same coarse daily
    capacity here.  Only visits that select the player organization are later
    expanded into arrival times, workers, services, and continuous queues.
    """

    customer_id = _character_value(customer, "id")
    if not isinstance(customer_id, str) or not customer_id:
        raise ValueError("客户缺少有效 ID")
    cash = _character_value(customer, "cash")
    if (
        not isinstance(cash, (int, float))
        or isinstance(cash, bool)
        or not math.isfinite(cash)
    ):
        raise ValueError(f"客户 {customer_id} 缺少有效 cash")
    home_address = _cell_coordinate(
        _character_value(customer, "address"),
        "customer.address",
    )
    familiarity = familiarity or {}
    venue_index = (
        venues
        if isinstance(venues, SexServiceVenueIndex)
        else SexServiceVenueIndex(venues)
    )
    types = venue_index.affordable_types(float(cash), privilege)
    if not types:
        return None
    rng = random.Random(_stable_seed(seed, customer_id, visit_key, "venue-choice"))
    nearest: list[
        tuple[Mapping[str, object], str, str, int, bool, int]
    ] = []
    while types and not nearest:
        type_weights = sex_service_venue_type_weights(float(cash), tuple(types))
        chosen_type = weighted_choice(rng, type_weights)
        for venue in venue_index.nearest(chosen_type, home_address):
            venue_id, venue_type = _venue_identity(venue)
            min_price, max_price = _venue_price_range(venue, venue_id)
            if not _venue_has_capacity(
                venue,
                venue_id,
                player_organization_id=player_organization_id,
                daily_visit_counts=daily_visit_counts,
            ):
                continue
            free_access = sex_service_privilege_covers_venue(privilege, venue)
            quoted_price = simplified_sex_service_charge(float(cash), max_price)
            if not free_access and quoted_price < min_price:
                continue
            distance = sex_service_cell_distance(
                home_address,
                _cell_coordinate(
                    venue.get("address"),
                    f"venue {venue_id}.address",
                ),
            )
            nearest.append((
                venue,
                venue_id,
                venue_type,
                distance,
                free_access,
                quoted_price,
            ))
            if len(nearest) >= config.SEX_SERVICE_VENUE_CANDIDATE_LIMIT:
                break
        if not nearest:
            types.remove(chosen_type)
    if not nearest:
        return None

    candidate_weights: dict[int, float] = {}
    for index, (venue, venue_id, _, distance, free_access, _) in enumerate(nearest):
        visits = familiarity.get(venue_id, 0)
        appeal = venue.get("appeal_score")
        candidate_weights[index] = sex_service_venue_selection_weight(
            customer_id=customer_id,
            venue_id=venue_id,
            distance_cells=distance,
            familiarity_visits=visits,
            appeal_score=appeal,
            free_access=free_access,
            seed=seed,
        )
    selected = nearest[weighted_choice(rng, candidate_weights)]
    _, venue_id, venue_type, distance, free_access, quoted_price = selected
    return SexServiceVenueSelection(
        organization_id=venue_id,
        venue_type=venue_type,
        free_access=free_access,
        quoted_price=quoted_price,
        customer_charge=0 if free_access else quoted_price,
        distance_cells=distance,
    )


def sex_service_visit_time_weights(
    character: Character | Mapping[str, object],
) -> dict[str, float]:
    """Return occupation-aware weights restricted to venue opening periods."""

    sex = _character_value(character, "sex")
    age = _character_value(character, "age")
    if sex != "male" or not isinstance(age, int) or age < 18:
        return {period: 0.0 for period in config.SEX_SERVICE_VISIT_TIME_PERIODS}
    character_id = _character_value(character, "id")
    occupation = _character_value(character, "occupation")
    profile_name = config.SEX_SERVICE_VISIT_OCCUPATION_TIME_PROFILE.get(occupation)
    if profile_name is None:
        raise ValueError(f"角色 {character_id} 使用未配置到访时段的职业 {occupation}")
    profile_weights = config.SEX_SERVICE_VISIT_TIME_PROFILES[profile_name]
    open_venue_counts = {
        period: sum(
            period in open_periods
            for open_periods in config.SEX_SERVICE_VENUE_OPEN_PERIODS.values()
        )
        for period in config.SEX_SERVICE_VISIT_TIME_PERIODS
    }
    weighted = {
        period: float(profile_weight) * open_venue_counts[period]
        for period, profile_weight in zip(
            config.SEX_SERVICE_VISIT_TIME_PERIODS,
            profile_weights,
        )
    }
    total = sum(weighted.values())
    if total <= 0:
        raise ValueError("没有同时符合职业作息和场所营业时间的到访时段")
    return {period: weight / total for period, weight in weighted.items()}


def monthly_sex_service_visit_count(
    character: Character | Mapping[str, object],
    year: int,
    month: int,
    *,
    privilege_scope: str | None = None,
    seed: int = 12345,
) -> int:
    """Stochastically round the stable frequency once for a specific month."""

    if not 1 <= month <= 12:
        raise ValueError("month 必须介于 1 至 12")
    frequency = monthly_sex_service_visit_frequency(
        character,
        privilege_scope=privilege_scope,
        seed=seed,
    )
    base_count = math.floor(frequency)
    character_id = _character_value(character, "id")
    rng = random.Random(_stable_seed(seed, character_id, year, month, "visit-count"))
    return base_count + (1 if rng.random() < frequency - base_count else 0)


def _period_minute_bounds(period: str) -> tuple[int, int]:
    start_text, end_text = period.split("-", 1)
    start_hour, start_minute = (int(value) for value in start_text.split(":"))
    end_hour, end_minute = (int(value) for value in end_text.split(":"))
    return start_hour * 60 + start_minute, end_hour * 60 + end_minute


def plan_monthly_sex_service_visits(
    character: Character | Mapping[str, object],
    year: int,
    month: int,
    *,
    privilege_scope: str | None = None,
    seed: int = 12345,
    timezone_name: str = "Asia/Shanghai",
    holiday_dates: Sequence[date] = (),
) -> tuple[datetime, ...]:
    """Generate all exact visit timestamps for one character and month."""

    selected_days = plan_monthly_sex_service_visit_dates(
        character,
        year,
        month,
        privilege_scope=privilege_scope,
        seed=seed,
        holiday_dates=holiday_dates,
    )
    if not selected_days:
        return ()

    timezone = ZoneInfo(timezone_name)
    visits = {
        sex_service_visit_arrival_time(
            character,
            selected_day,
            seed=seed,
            timezone_name=timezone.key,
            occurrence=index,
        )
        for index, selected_day in enumerate(selected_days)
    }
    return tuple(sorted(visits))


def plan_monthly_sex_service_visit_dates(
    character: Character | Mapping[str, object],
    year: int,
    month: int,
    *,
    privilege_scope: str | None = None,
    seed: int = 12345,
    holiday_dates: Sequence[date] = (),
) -> tuple[date, ...]:
    """Generate only visit dates; ordinary venues need no arrival minute."""

    count = monthly_sex_service_visit_count(
        character,
        year,
        month,
        privilege_scope=privilege_scope,
        seed=seed,
    )
    if count == 0:
        return ()

    character_id = _character_value(character, "id")
    rng = random.Random(_stable_seed(seed, character_id, year, month, "visit-dates"))
    days_in_month = calendar.monthrange(year, month)[1]
    all_days = [date(year, month, day) for day in range(1, days_in_month + 1)]
    holidays = set(holiday_dates)
    holidays.update(
        date(year, holiday_month, holiday_day)
        for holiday_month, holiday_day in config.SEX_SERVICE_VISIT_FIXED_HOLIDAYS
    )

    selected_days: list[date] = []
    while len(selected_days) < count:
        day_pool = list(all_days)
        take = min(count - len(selected_days), len(day_pool))
        for _ in range(take):
            weights = []
            for candidate in day_pool:
                weight = config.SEX_SERVICE_VISIT_WEEKDAY_WEIGHTS[candidate.weekday()]
                if candidate + timedelta(days=1) in holidays:
                    weight *= config.SEX_SERVICE_VISIT_HOLIDAY_EVE_MULTIPLIER
                weights.append(weight)
            chosen_index = rng.choices(range(len(day_pool)), weights=weights, k=1)[0]
            selected_days.append(day_pool.pop(chosen_index))

    return tuple(sorted(selected_days))


def sex_service_visit_arrival_time(
    character: Character | Mapping[str, object],
    visit_date: date,
    *,
    seed: int = 12345,
    timezone_name: str = "Asia/Shanghai",
    occurrence: int = 0,
) -> datetime:
    """Generate an exact arrival only after a visit selects the player venue."""

    if not isinstance(visit_date, date) or isinstance(visit_date, datetime):
        raise ValueError("visit_date 必须是 date")
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 0:
        raise ValueError("occurrence 必须是非负整数")
    timezone = ZoneInfo(timezone_name)
    character_id = _character_value(character, "id")
    rng = random.Random(_stable_seed(
        seed,
        character_id,
        visit_date.isoformat(),
        occurrence,
        "player-venue-arrival",
    ))
    period = weighted_choice(rng, sex_service_visit_time_weights(character))
    start_minute, end_minute = _period_minute_bounds(period)
    minute_of_day = rng.randrange(start_minute, end_minute)
    return datetime.combine(visit_date, time.min, timezone) + timedelta(
        minutes=minute_of_day
    )


def daily_sex_service_visit_probability(
    character: Character | Mapping[str, object],
    *,
    privilege_scope: str | None = None,
    seed: int = 12345,
) -> float:
    """Compatibility conversion; runtime scheduling uses monthly plans."""

    monthly_frequency = monthly_sex_service_visit_frequency(
        character,
        privilege_scope=privilege_scope,
        seed=seed,
    )
    return 1.0 - math.exp(-monthly_frequency / (365.2425 / 12.0))


def simulate_sex_service_demand(city: CityPopulation, days: int = 365, seed: int = 12345) -> dict:
    """Simulate independent daily visits and report demand per sex worker."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("days 必须是大于 0 的整数")
    workers = [
        character for character in city.characters
        if character.occupation in character_generator.SEX_SERVICE_PROFESSIONS
    ]
    if not workers:
        raise ValueError("城市中没有性工作者，无法计算人均接客量")
    customers = [character for character in city.characters if character.sex == "male" and character.age >= 18]
    crime_depths = sex_service_crime_organization_depths(city.organizations)
    organizations_by_id = {organization.id: organization for organization in city.organizations}
    probabilities = [
        daily_sex_service_visit_probability(
            character,
            privilege_scope=(
                sex_service_crime_privilege_scope(
                    organizations_by_id[character.organization_id].type,
                    crime_depths[character.organization_id],
                    character.organization_role,
                )
                if character.organization_id in crime_depths
                and character.organization_role is not None
                else None
            ),
            seed=seed,
        )
        for character in customers
    ]
    time_weights = [sex_service_visit_time_weights(character) for character in customers]
    expected_by_period = {
        period: sum(probability * weights[period] for probability, weights in zip(probabilities, time_weights))
        for period in config.SEX_SERVICE_VISIT_TIME_PERIODS
    }
    rng = random.Random(seed)
    time_rng = random.Random(seed ^ 0x5E71C3)
    simulated_by_period = {period: 0 for period in config.SEX_SERVICE_VISIT_TIME_PERIODS}
    total_visits = 0
    for _ in range(days):
        for probability, weights in zip(probabilities, time_weights):
            if rng.random() < probability:
                total_visits += 1
                simulated_by_period[weighted_choice(time_rng, weights)] += 1
    expected_daily_visits = sum(probabilities)
    return {
        "days": days,
        "adult_men": len(probabilities),
        "sex_workers": len(workers),
        "total_visits": total_visits,
        "simulated_daily_visits": total_visits / days,
        "expected_daily_visits": expected_daily_visits,
        "simulated_clients_per_worker_per_day": total_visits / days / len(workers),
        "expected_clients_per_worker_per_day": expected_daily_visits / len(workers),
        "mean_daily_probability": expected_daily_visits / len(probabilities) if probabilities else 0.0,
        "max_daily_probability": max(probabilities, default=0.0),
        "expected_daily_visits_by_period": expected_by_period,
        "simulated_visits_by_period": simulated_by_period,
        "simulated_daily_visits_by_period": {
            period: count / days for period, count in simulated_by_period.items()
        },
    }


def sex_worker_appeal_score(attractiveness: float, body: float) -> float:
    return round(.55 * attractiveness + .30 * body + .15 * min(attractiveness, body), 4)


def _venue_service_rules(venue_type: str) -> dict | None:
    template = config.ORGANIZATION_TEMPLATES["street"].get(venue_type)
    if not template:
        return None
    rules = template.get("sex_service")
    return rules if rules and rules.get("enabled") else None


def sex_service_commission_bps(venue_type: str, occupation: str) -> int:
    venue_rules = _venue_service_rules(venue_type)
    if venue_rules is None:
        raise ValueError(f"组织类型 {venue_type} 不是性服务场所")
    if occupation not in config.SEX_WORKER_COMMISSION_MULTIPLIER_MILLI:
        raise ValueError(f"职业 {occupation} 不是接客性工作职业")
    raw_bps = (
        venue_rules["commission_bps"]
        * config.SEX_WORKER_COMMISSION_MULTIPLIER_MILLI[occupation]
        // 1000
    )
    return min(10_000 if occupation == "性奴" else 9_000, raw_bps)


def sex_service_worker_earnings(
    customer_charge: int,
    venue_type: str,
    occupation: str,
) -> int:
    """Return the worker's retained cash after the venue takes commission."""

    if (
        isinstance(customer_charge, bool)
        or not isinstance(customer_charge, int)
        or customer_charge < 0
    ):
        raise ValueError("customer_charge 必须是非负整数")
    commission_bps = sex_service_commission_bps(venue_type, occupation)
    venue_commission = customer_charge * commission_bps // 10_000
    return customer_charge - venue_commission


def quote_sex_service_price(
    service: str,
    venue_type: str,
    worker_level: int,
    occupation: str,
) -> int:
    """Return a deterministic price floored to its two highest significant digits."""
    if service not in config.SEX_SERVICE_BASE_PRICES:
        raise ValueError(f"未知性服务项目：{service}")
    venue_rules = _venue_service_rules(venue_type)
    if venue_rules is None:
        raise ValueError(f"组织类型 {venue_type} 不是性服务场所")
    if worker_level not in config.SEX_WORKER_LEVEL_PRICE_MULTIPLIERS:
        raise ValueError("性工作者等级必须为 1 至 9")
    if occupation not in config.SEX_WORKER_OCCUPATION_PRICE_MULTIPLIERS:
        raise ValueError(f"职业 {occupation} 不是接客性工作职业")
    if service not in SEX_WORKER_SERVICES_BY_OCCUPATION[occupation]:
        raise ValueError(f"职业 {occupation} 不提供服务：{service}")

    lower, upper = venue_rules["price_multiplier_bounds"]
    raw_multiplier = (
        config.SEX_WORKER_LEVEL_PRICE_MULTIPLIERS[worker_level]
        * config.SEX_WORKER_OCCUPATION_PRICE_MULTIPLIERS[occupation]
    )
    bounded_multiplier = min(upper, max(lower, raw_multiplier))
    raw_price = config.SEX_SERVICE_BASE_PRICES[service] * bounded_multiplier
    place = 10 ** max(0, int(math.floor(math.log10(raw_price))) - 1)
    return int(math.floor(raw_price / place) * place)


def sex_worker_available_services(
    worker: Character | Mapping[str, object],
) -> tuple[str, ...]:
    """Return services supported by both the occupation and actual skills."""

    occupation = _character_value(worker, "occupation")
    if occupation not in SEX_WORKER_SERVICES_BY_OCCUPATION:
        return ()
    skills = _character_value(worker, "skills")
    if not isinstance(skills, Mapping):
        raise ValueError(f"性工作者 {_character_value(worker, 'id')} 缺少技能字典")
    return tuple(
        service
        for service in SEX_WORKER_SERVICES_BY_OCCUPATION[occupation]
        if _service_skill_level(skills, service) is not None
    )


def sex_service_venue_price_range(
    venue_type: str,
    sex_workers: Sequence[Character | Mapping[str, object]],
) -> tuple[int, int]:
    """Calculate the cheapest and priciest real offerings in one venue."""

    if _venue_service_rules(venue_type) is None:
        raise ValueError(f"组织类型 {venue_type} 不是性服务场所")
    prices: list[int] = []
    for worker in sex_workers:
        occupation = _character_value(worker, "occupation")
        if occupation not in SEX_WORKER_SERVICES_BY_OCCUPATION:
            continue
        worker_level = _character_value(worker, "sex_worker_level")
        if (
            isinstance(worker_level, bool)
            or not isinstance(worker_level, int)
            or worker_level not in config.SEX_WORKER_LEVEL_PRICE_MULTIPLIERS
        ):
            raise ValueError(
                f"性工作者 {_character_value(worker, 'id')} 缺少有效等级"
            )
        prices.extend(
            quote_sex_service_price(
                service,
                venue_type,
                worker_level,
                str(occupation),
            )
            for service in sex_worker_available_services(worker)
        )
    if not prices:
        raise ValueError(f"{venue_type} 没有任何可报价的性服务")
    return min(prices), max(prices)


def _worker_appeal(worker: Character | Mapping[str, object]) -> float:
    score = _character_value(worker, "sex_worker_score")
    if score is None:
        attractiveness = _character_value(worker, "appearance_score")
        if attractiveness is None:
            # Compatibility with the retired dataclass-based generator.
            attractiveness = _character_value(worker, "attractiveness_score")
        body = _character_value(worker, "body_score")
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0 <= value <= 100
            for value in (attractiveness, body)
        ):
            raise ValueError(
                f"性工作者 {_character_value(worker, 'id')} 缺少有效吸引力评分"
            )
        score = sex_worker_appeal_score(float(attractiveness), float(body))
    if (
        not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
        or not 0 <= score <= 100
    ):
        raise ValueError(
            f"性工作者 {_character_value(worker, 'id')} 使用无效吸引力评分"
        )
    return float(score)


def sex_service_venue_appeal_score(
    sex_workers: Sequence[Character | Mapping[str, object]],
) -> float:
    """Blend overall roster quality with the venue's five best workers."""

    eligible = [
        worker
        for worker in sex_workers
        if _character_value(worker, "occupation")
        in SEX_WORKER_SERVICES_BY_OCCUPATION
        and sex_worker_available_services(worker)
    ]
    if not eligible:
        raise ValueError("场所没有能够提供性服务的性工作者")
    scores = sorted((_worker_appeal(worker) for worker in eligible), reverse=True)
    overall_mean = sum(scores) / len(scores)
    marquee = scores[:min(5, len(scores))]
    marquee_mean = sum(marquee) / len(marquee)
    return round(0.45 * overall_mean + 0.55 * marquee_mean, 4)


def build_sex_service_venue_summary(
    venue: Organization | Mapping[str, object],
    sex_workers: Sequence[Character | Mapping[str, object]],
    *,
    daily_visit_count: int = 0,
    exact_available: bool | None = None,
) -> dict[str, object]:
    """Build the compact immutable-data summary used by venue selection.

    Worker skills are scanned once here.  Ordinary NPC visits subsequently use
    only this summary, while a player organization may additionally provide an
    exact availability result for its detailed scheduler.
    """

    venue_id = _organization_value(venue, "organization_id", "id")
    venue_type = _organization_value(venue, "template_name", "type")
    if not isinstance(venue_id, str) or not venue_id:
        raise ValueError("性服务场所缺少有效 organization_id")
    if not isinstance(venue_type, str) or not venue_type:
        raise ValueError(f"场所 {venue_id} 缺少 template_name")
    address = _cell_coordinate(
        _organization_value(venue, "address"),
        f"venue {venue_id}.address",
    )
    if (
        isinstance(daily_visit_count, bool)
        or not isinstance(daily_visit_count, int)
        or daily_visit_count < 0
    ):
        raise ValueError("daily_visit_count 必须是非负整数")
    available_workers = tuple(
        worker
        for worker in sex_workers
        if _character_value(worker, "occupation")
        in SEX_WORKER_SERVICES_BY_OCCUPATION
        and sex_worker_available_services(worker)
    )
    if not available_workers:
        raise ValueError(f"场所 {venue_id} 没有有效性工作者")
    min_price, max_price = sex_service_venue_price_range(
        venue_type,
        available_workers,
    )
    summary: dict[str, object] = {
        "organization_id": venue_id,
        "city_id": _organization_value(venue, "city_id"),
        "template_name": venue_type,
        "district": _organization_value(venue, "district", "district_name"),
        "street": _organization_value(venue, "street", "street_name"),
        "address": address,
        "min_price": min_price,
        "max_price": max_price,
        "appeal_score": sex_service_venue_appeal_score(available_workers),
        "sex_worker_count": len(available_workers),
        "daily_visit_count": daily_visit_count,
    }
    if exact_available is not None:
        if not isinstance(exact_available, bool):
            raise ValueError("exact_available 必须是 bool 或 None")
        summary["exact_available"] = exact_available
    return summary
