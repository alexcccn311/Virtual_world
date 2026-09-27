"""Generate every Character declared by one Organization template."""
from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping
from string import Formatter
from typing import Any, Callable

from .. import config
from .character_generator import CharacterGenerator


_next_organization_number = 1
LARGE_ORGANIZATION_POPULATION = 100
SMALL_ORGANIZATION_NAME_REPEAT_LIMIT = 3


def _allocate_organization_id() -> str:
    global _next_organization_number
    organization_id = f"ORG-{_next_organization_number:08d}"
    _next_organization_number += 1
    return organization_id


def _template_errors(
    scope: str, template_name: str, template: Mapping[str, Any],
    template_locations: Mapping[str, list[str]],
) -> list[str]:
    path = f"ORGANIZATION_TEMPLATES.{scope}.{template_name}"
    errors: list[str] = []
    for field_name in ("industry", "economy"):
        value = template.get(field_name)
        if not isinstance(value, str) or not value:
            errors.append(f"{path}.{field_name} 必须是非空字符串")
    offers_loans = template.get("offers_loans", False)
    if not isinstance(offers_loans, bool):
        errors.append(f"{path}.offers_loans 必须是 bool")
    max_total_debt = template.get("max_total_debt")
    if offers_loans and "max_total_debt" not in template:
        errors.append(f"{path}.max_total_debt 必须明确配置范围或 None")
    if "max_total_debt" in template and max_total_debt is not None:
        valid_debt_range = (
            isinstance(max_total_debt, (tuple, list))
            and len(max_total_debt) == 2
            and all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in max_total_debt
            )
            and max_total_debt[0] > 0
            and max_total_debt[1] >= max_total_debt[0]
        )
        if not valid_debt_range:
            errors.append(
                f"{path}.max_total_debt must be a positive integer range"
            )
    if not offers_loans and "max_total_debt" in template:
        errors.append(f"{path}.max_total_debt 只能用于 offers_loans=True 的组织")
    if "loan_blackness" in template:
        errors.append(
            f"{path}.loan_blackness is derived and must not be configured"
        )

    sex_service = template.get("sex_service")
    if "sex_service" in template:
        if not isinstance(sex_service, Mapping):
            errors.append(f"{path}.sex_service must be a mapping")
        else:
            enabled = sex_service.get("enabled")
            if not isinstance(enabled, bool):
                errors.append(f"{path}.sex_service.enabled must be bool")
            min_level = sex_service.get("min_level")
            if (isinstance(min_level, bool) or not isinstance(min_level, int)
                    or min_level <= 0):
                errors.append(
                    f"{path}.sex_service.min_level must be a positive integer"
                )
            commission_bps = sex_service.get("commission_bps")
            if (isinstance(commission_bps, bool)
                    or not isinstance(commission_bps, int)
                    or not 0 <= commission_bps <= 10_000):
                errors.append(
                    f"{path}.sex_service.commission_bps must be an integer "
                    "between 0 and 10000"
                )
            bounds = sex_service.get("price_multiplier_bounds")
            valid_bounds = (
                isinstance(bounds, (tuple, list))
                and len(bounds) == 2
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    for value in bounds
                )
                and bounds[0] > 0
                and bounds[1] >= bounds[0]
            )
            if not valid_bounds:
                errors.append(
                    f"{path}.sex_service.price_multiplier_bounds must be a "
                    "positive numeric range"
                )

    name_pool = template.get("name_pool")
    if not isinstance(name_pool, (tuple, list)) or not name_pool:
        errors.append(f"{path}.name_pool 必须是非空序列")
    elif any(not isinstance(name, str) or not name for name in name_pool):
        errors.append(f"{path}.name_pool 必须只包含非空字符串")

    roles = template.get("roles")
    if not isinstance(roles, Mapping) or not roles:
        errors.append(f"{path}.roles 必须是非空 mapping")
        return errors
    role_keys = set(roles)
    valid_roles = {
        role_key: role for role_key, role in roles.items()
        if isinstance(role, Mapping)
    }
    secretary_requirements: dict[str, int] = defaultdict(int)
    for role_key, role in roles.items():
        role_path = f"{path}.roles.{role_key}"
        if not isinstance(role, Mapping):
            errors.append(f"{role_path} 必须是 mapping")
            continue
        count = role.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            errors.append(f"{role_path}.count 必须是正整数")
        reports_to = role.get("reports_to")
        if reports_to is not None and (
            not isinstance(reports_to, str) or reports_to not in role_keys
        ):
            errors.append(f"{role_path}.reports_to 引用未知 role_key“{reports_to}”")
        secretary_config = role.get("secretary_config", {})
        if not isinstance(secretary_config, Mapping):
            errors.append(f"{role_path}.secretary_config 必须是 mapping")
        else:
            for secretary_role, secretary_count in secretary_config.items():
                valid_secretary_role = (
                    isinstance(secretary_role, str) and secretary_role in role_keys
                )
                if not valid_secretary_role:
                    errors.append(f"{role_path}.secretary_config 引用未知 role_key“{secretary_role}”")
                if (isinstance(secretary_count, bool)
                        or not isinstance(secretary_count, int) or secretary_count < 1):
                    errors.append(f"{role_path}.secretary_config.{secretary_role} 必须是正整数")
                elif (valid_secretary_role and isinstance(count, int)
                      and not isinstance(count, bool) and count > 0):
                    secretary_requirements[secretary_role] += count * secretary_count

    for secretary_role, required in secretary_requirements.items():
        secretary = valid_roles.get(secretary_role)
        available = secretary.get("count") if secretary is not None else None
        if (isinstance(available, int) and not isinstance(available, bool)
                and available > 0 and required > available):
            errors.append(
                f"{path}.roles.{secretary_role} 需要至少 {required} 人，"
                f"但模板只生成 {available} 人"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(role_key: str, trail: tuple[str, ...]) -> None:
        if role_key in visited:
            return
        if role_key in visiting:
            errors.append(f"{path}.roles reports_to 形成循环：{' -> '.join((*trail, role_key))}")
            return
        visiting.add(role_key)
        parent = valid_roles[role_key].get("reports_to")
        if isinstance(parent, str) and parent in valid_roles:
            visit(parent, (*trail, role_key))
        visiting.remove(role_key)
        visited.add(role_key)

    for role_key in valid_roles:
        visit(role_key, ())

    sub_organizations = template.get("sub_organizations", {})
    if not isinstance(sub_organizations, Mapping):
        errors.append(f"{path}.sub_organizations 必须是 mapping")
    else:
        for declaration_key, declaration in sub_organizations.items():
            declaration_path = f"{path}.sub_organizations.{declaration_key}"
            if not isinstance(declaration, Mapping):
                errors.append(f"{declaration_path} 必须是 mapping")
                continue
            child_template = declaration.get("template")
            locations = (
                template_locations.get(child_template, [])
                if isinstance(child_template, str) else []
            )
            if not locations:
                errors.append(f"{declaration_path}.template 引用未知组织模板“{child_template}”")
            elif len(locations) > 1:
                errors.append(f"{declaration_path}.template 引用的组织模板“{child_template}”不唯一")
            leader_reports_to = declaration.get("leader_reports_to")
            if not isinstance(leader_reports_to, str) or leader_reports_to not in role_keys:
                errors.append(f"{declaration_path}.leader_reports_to 必须引用父组织真实 role_key")
            spawn = declaration.get("spawn")
            if not isinstance(spawn, Mapping):
                errors.append(f"{declaration_path}.spawn 必须是 mapping")
                continue
            spawn_mode = spawn.get("mode")
            if spawn_mode not in {
                "fixed", "each_district", "each_street", "highest_prosperity",
            }:
                errors.append(
                    f"{declaration_path}.spawn.mode 使用未知规则“{spawn_mode}”"
                )
            if spawn_mode == "fixed" and (
                isinstance(spawn.get("count"), bool)
                or not isinstance(spawn.get("count"), int)
                or spawn["count"] < 1
            ):
                errors.append(f"{declaration_path}.spawn.count 必须是正整数")
    return errors


def organization_template_errors(
    templates: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> list[str]:
    """Return declarative Organization errors without executing generation."""
    source = config.ORGANIZATION_TEMPLATES if templates is None else templates
    errors: list[str] = []
    if not isinstance(source, Mapping):
        return ["ORGANIZATION_TEMPLATES 必须是 mapping"]
    template_locations: dict[str, list[str]] = defaultdict(list)
    for scope, scope_templates in source.items():
        if not isinstance(scope_templates, Mapping):
            continue
        for template_name in scope_templates:
            template_locations[template_name].append(f"{scope}.{template_name}")
    for template_name, locations in template_locations.items():
        if len(locations) > 1:
            errors.append(
                f"Organization template key“{template_name}”重复定义：\n"
                + "\n".join(locations)
            )
    for scope, scope_templates in source.items():
        if not isinstance(scope_templates, Mapping):
            errors.append(f"ORGANIZATION_TEMPLATES.{scope} 必须是 mapping")
            continue
        for template_name, template in scope_templates.items():
            if not isinstance(template, Mapping):
                errors.append(f"ORGANIZATION_TEMPLATES.{scope}.{template_name} 必须是 mapping")
                continue
            errors.extend(_template_errors(
                scope, template_name, template, template_locations,
            ))
    return errors


def validate_config(
    templates: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> None:
    errors = organization_template_errors(templates)
    if errors:
        raise ValueError("Organization 配置无效：\n- " + "\n- ".join(errors))


class OrganizationGenerator:
    """Generate all Character dictionaries for one Organization template."""

    def __init__(
        self,
        rng: random.Random,
        *,
        templates: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
        character_generator: CharacterGenerator | None = None,
        organization_id_allocator: Callable[[], str] | None = None,
        character_id_allocator: Callable[[], str] | None = None,
        character_id_claimer: Callable[[object], str] | None = None,
        organization_cell_allocator: (
            Callable[[str, str], tuple[int, int]] | None
        ) = None,
    ) -> None:
        self.rng = rng
        self.templates = config.ORGANIZATION_TEMPLATES if templates is None else templates
        self.character_generator = (
            CharacterGenerator(
                rng,
                id_allocator=character_id_allocator,
                id_claimer=character_id_claimer,
            )
            if character_generator is None
            else character_generator
        )
        self.organization_id_allocator = (
            organization_id_allocator or _allocate_organization_id
        )
        self.organization_cell_allocator = organization_cell_allocator
        # One OrganizationGenerator serves one city. Large Organization names
        # are city-wide unique; small names may repeat only a few times.
        self._name_use_counts: dict[str, int] = defaultdict(int)
        self._large_names: set[str] = set()
        self._name_pools: dict[str, list[str]] = {}
        self._name_pool_cursors: dict[str, int] = defaultdict(int)
        self._fallback_name_serials: dict[tuple[str, str, str], int] = (
            defaultdict(int)
        )

    def generate(
        self,
        district: str,
        street: str,
        template_name: str,
        district_map: Mapping[str, int],
        special_role: dict | None = None,
        *,
        parent_organization_id: str | None = None,
        manager_id: str | None = None,
        parent_name: str | None = None,
        root_organization_id: str | None = None,
    ) -> dict[str, object]:
        """Generate one Organization's Characters and child assignments.

        ``district_map`` maps each District name to its number of Streets.
        Child assignments contain manager, template, instance, generated ID,
        and placement information. Multiple managers of the configured
        ``leader_reports_to`` role receive each child template by minimum
        current load.

        ``special_role`` targets one role slot in this Organization. It must
        identify this Organization template and one configured role key.
        """
        if not isinstance(district, str) or not district:
            raise ValueError("district 必须是非空字符串")
        if not isinstance(street, str) or not street:
            raise ValueError("street 必须是非空字符串")
        self._validate_district_map(district_map)
        if special_role is not None and not isinstance(special_role, dict):
            raise TypeError("special_role 必须是 dict 或 None")

        scope, template = self._template(template_name)
        locations = self._template_locations()
        errors = _template_errors(scope, template_name, template, locations)
        if errors:
            raise ValueError("Organization 模板无效：\n- " + "\n- ".join(errors))

        special_role_key: str | None = None
        requested_organization_name: str | None = None
        if special_role is not None:
            if special_role.get("organization_template") != template_name:
                raise ValueError(
                    "special_role.organization_template 与当前 Organization "
                    f"模板“{template_name}”不一致"
                )
            special_role_key = special_role.get("role_template")
            if (
                not isinstance(special_role_key, str)
                or special_role_key not in template["roles"]
            ):
                raise ValueError(
                    f"special_role.role_template 必须引用“{template_name}”中的"
                    "真实 role key"
                )
            if "organization_name" in special_role:
                requested_organization_name = special_role["organization_name"]
                if (
                    not isinstance(requested_organization_name, str)
                    or not requested_organization_name
                ):
                    raise ValueError(
                        "special_role.organization_name 必须是非空字符串"
                    )

        organization_id = self.organization_id_allocator()
        address = (
            self.organization_cell_allocator(street, organization_id)
            if self.organization_cell_allocator is not None
            else None
        )
        if address is not None and (
            not isinstance(address, tuple)
            or len(address) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in address
            )
        ):
            raise ValueError(
                f"Organization“{organization_id}”被分配了无效 cell：{address}"
            )
        name_context = {
            "district_name": district,
            "street_name": street,
        }
        if parent_name is not None:
            name_context["parent_name"] = parent_name
        resolved_root_id = root_organization_id or organization_id
        offers_loans = bool(template.get("offers_loans", False))
        configured_debt_limit = template.get("max_total_debt")
        if offers_loans and configured_debt_limit is None:
            max_total_debt = None
        elif offers_loans:
            max_total_debt = self.rng.randint(
                int(configured_debt_limit[0]),
                int(configured_debt_limit[1]),
            )
        else:
            max_total_debt = None
        loan_blackness = (
            None
            if not offers_loans
            else 1.0
            if max_total_debt is None
            else max_total_debt / (max_total_debt + config.LOAN_DEBT_SCALE)
        )
        organization_population = sum(
            role_config["count"] for role_config in template["roles"].values()
        )
        organization_name = self._organization_name(
            template_name,
            template,
            name_context,
            population=organization_population,
            requested_name=requested_organization_name,
        )
        resolved_parent_name = parent_name or organization_name

        roles = template["roles"]
        characters_by_role: dict[str, list[dict[str, object]]] = {}
        seen_character_ids: set[str] = set()
        special_role_character_id: str | None = None
        for role_key, role_config in roles.items():
            character_config = self._character_config(
                district, street, role_config,
            )
            members: list[dict[str, object]] = []
            for member_index in range(role_config["count"]):
                role_special = (
                    special_role
                    if role_key == special_role_key and member_index == 0
                    else None
                )
                character = self.character_generator.generate(
                    character_config,
                    special_role=role_special,
                )
                character_id = character.get("id")
                if not isinstance(character_id, str) or not character_id:
                    raise ValueError(f"role“{role_key}”生成了无效 Character ID")
                if character_id in seen_character_ids:
                    raise ValueError(
                        f"Organization 内出现重复 Character ID“{character_id}”"
                    )
                relation = character.get("relation")
                if not isinstance(relation, dict):
                    raise ValueError(
                        f"Character“{character_id}”缺少 relation 字典"
                    )
                seen_character_ids.add(character_id)
                if role_special is not None:
                    special_role_character_id = character_id
                character["organization_id"] = organization_id
                character["organization_name"] = organization_name
                # Persist the stable template key. Runtime authority rules
                # must not infer rank from translated titles or occupations.
                character["organization_role"] = role_key
                members.append(character)
            characters_by_role[role_key] = members

        self._resolve_reports_to(roles, characters_by_role)
        self._resolve_parent_manager(
            roles,
            characters_by_role,
            parent_organization_id,
            manager_id,
        )
        self._assign_secretaries(roles, characters_by_role)
        characters = [
            character
            for role_key in roles
            for character in characters_by_role[role_key]
        ]
        sub_organization_assignments = self._assign_sub_organizations(
            template.get("sub_organizations", {}),
            characters_by_role,
            district,
            district_map,
        )
        return {
            "organization_id": organization_id,
            "name": organization_name,
            "template_name": template_name,
            "scope": scope,
            "district": district,
            "street": street,
            "address": address,
            "offers_loans": offers_loans,
            "max_total_debt": max_total_debt,
            "loan_blackness": loan_blackness,
            "parent_organization_id": parent_organization_id,
            "manager_id": manager_id,
            "parent_name": resolved_parent_name,
            "root_organization_id": resolved_root_id,
            "population_usage": len(characters),
            "special_role_character_id": special_role_character_id,
            "characters": characters,
            "sub_organization_assignments": sub_organization_assignments,
            "sub_organization_ids": [],
        }

    @staticmethod
    def _validate_district_map(district_map: Mapping[str, int]) -> None:
        if not isinstance(district_map, Mapping) or not district_map:
            raise ValueError("district_map 必须是非空的 {区名: 街道数量} mapping")
        if any(
            not isinstance(district_name, str)
            or not district_name
            or isinstance(street_count, bool)
            or not isinstance(street_count, int)
            or street_count < 1
            for district_name, street_count in district_map.items()
        ):
            raise ValueError("district_map 必须由非空区名和正整数街道数量组成")

    def _template_locations(self) -> dict[str, list[str]]:
        locations: dict[str, list[str]] = defaultdict(list)
        for scope, scope_templates in self.templates.items():
            if not isinstance(scope_templates, Mapping):
                continue
            for template_name in scope_templates:
                locations[template_name].append(f"{scope}.{template_name}")
        return locations

    def _template(self, template_name: str) -> tuple[str, Mapping[str, Any]]:
        if not isinstance(template_name, str) or not template_name:
            raise ValueError("template_name 必须是非空字符串")
        matches = [
            (scope, scope_templates[template_name])
            for scope, scope_templates in self.templates.items()
            if isinstance(scope_templates, Mapping)
            and template_name in scope_templates
        ]
        if not matches:
            raise KeyError(f"未知 Organization template“{template_name}”")
        if len(matches) > 1:
            scopes = "、".join(scope for scope, _ in matches)
            raise ValueError(
                f"Organization template“{template_name}”在多个 scope 中重复：{scopes}"
            )
        return matches[0]

    def _organization_name(
        self,
        template_name: str,
        template: Mapping[str, Any],
        name_context: Mapping[str, str],
        *,
        population: int,
        requested_name: str | None = None,
    ) -> str:
        is_large = population >= LARGE_ORGANIZATION_POPULATION

        def reserve(name: str) -> bool:
            used = self._name_use_counts[name]
            if name in self._large_names:
                return False
            if is_large:
                if used:
                    return False
                self._large_names.add(name)
            elif used >= SMALL_ORGANIZATION_NAME_REPEAT_LIMIT:
                return False
            self._name_use_counts[name] += 1
            return True

        if requested_name is not None:
            if not reserve(requested_name):
                raise ValueError(
                    f"special_role 指定的 Organization 名称“{requested_name}”已被使用"
                )
            return requested_name
        if template_name not in self._name_pools:
            names = list(template["name_pool"])
            self.rng.shuffle(names)
            self._name_pools[template_name] = names

        pool = self._name_pools[template_name]
        start = self._name_pool_cursors[template_name] % len(pool)
        missing_fields: set[str] = set()
        formatted_names: list[tuple[int, str]] = []
        for offset in range(len(pool)):
            index = (start + offset) % len(pool)
            raw_name = pool[index]
            try:
                fields = {
                    field
                    for _, field, _, _ in Formatter().parse(raw_name)
                    if field
                }
            except ValueError as error:
                raise ValueError(
                    f"Organization 模板“{template_name}”包含无效名称“{raw_name}”"
                ) from error
            missing = fields - set(name_context)
            if missing:
                missing_fields.update(missing)
                continue
            formatted_names.append((index, raw_name.format_map(name_context)))

        if not formatted_names and missing_fields:
            raise ValueError(
                f"Organization 模板“{template_name}”的名称需要上下文字段："
                f"{'、'.join(sorted(missing_fields))}"
            )

        def try_names(candidates: list[tuple[int, str]]) -> str | None:
            seen: set[str] = set()
            for index, name in candidates:
                if name in seen:
                    continue
                seen.add(name)
                if reserve(name):
                    self._name_pool_cursors[template_name] = (index + 1) % len(pool)
                    return name
            return None

        if selected := try_names(formatted_names):
            return selected

        for field_name in ("street_name", "district_name"):
            qualifier = name_context.get(field_name)
            if not qualifier:
                continue
            qualified = [
                (
                    index,
                    name if name.startswith(qualifier) else f"{qualifier}{name}",
                )
                for index, name in formatted_names
            ]
            if selected := try_names(qualified):
                return selected

        district_name = name_context.get("district_name", "")
        street_name = name_context.get("street_name", "")
        location = f"{district_name}{street_name}"
        if location:
            qualified = [
                (
                    index,
                    name if name.startswith(location) else f"{location}{name}",
                )
                for index, name in formatted_names
            ]
            if selected := try_names(qualified):
                return selected

        # The configured pool and geographic combinations are finite, while
        # ordinary street businesses can number in the thousands. A stable
        # location-qualified serial is the final collision-safe fallback.
        base_index, base_name = formatted_names[0]
        serial_key = (template_name, street_name, base_name)
        while True:
            self._fallback_name_serials[serial_key] += 1
            serial = self._fallback_name_serials[serial_key]
            fallback = f"{street_name}{base_name}（{serial}号）"
            if reserve(fallback):
                self._name_pool_cursors[template_name] = (
                    base_index + 1
                ) % len(pool)
                return fallback

    @staticmethod
    def _occupation_type(occupation: str) -> str:
        matches: list[str] = []
        for occupation_type, groups in config.OCCUPATION_TEMPLATES.items():
            occupations = (
                tuple(item for values in groups.values() for item in values)
                if isinstance(groups, Mapping)
                else tuple(groups)
            )
            if occupation in occupations:
                matches.append(occupation_type)
        if not matches:
            raise ValueError(f"职业“{occupation}”未配置 occupation_type")
        if len(matches) > 1:
            raise ValueError(
                f"职业“{occupation}”重复归属于 occupation_type：{'、'.join(matches)}"
            )
        return matches[0]

    @classmethod
    def _character_config(
        cls,
        district: str,
        street: str,
        role_config: Mapping[str, Any],
    ) -> dict[str, object]:
        occupation = role_config["occupation"]
        occupation_type = cls._occupation_type(occupation)
        return {
            "district": district,
            "street": street,
            "occupation": occupation,
            "title": role_config["title"],
            "occupation_type": occupation_type,
            "sex": role_config["sex"],
            "age": role_config["age"],
            "finance": role_config["finance"],
            "personality_weights": config.CHARACTER_PERSONALITY_CIRCLE_WEIGHTS[
                occupation_type
            ],
            "education": role_config.get("education"),
            "skills": role_config.get("skills"),
        }

    def _resolve_reports_to(
        self,
        roles: Mapping[str, Mapping[str, Any]],
        characters_by_role: Mapping[str, list[dict[str, object]]],
    ) -> None:
        superior_loads: dict[str, int] = defaultdict(int)
        for role_key, role_config in roles.items():
            superior_role = role_config.get("reports_to")
            if superior_role is None:
                continue
            subordinates = list(characters_by_role[role_key])
            superiors = list(characters_by_role[superior_role])
            self.rng.shuffle(subordinates)
            superior_ids = {superior["id"] for superior in superiors}
            for subordinate in subordinates:
                relation = subordinate["relation"]
                if "superior_id" in relation:
                    configured_id = relation["superior_id"]
                    if configured_id in superior_ids:
                        superior_loads[configured_id] += 1
                    continue
                minimum = min(
                    superior_loads[superior["id"]]
                    for superior in superiors
                )
                candidates = [
                    superior for superior in superiors
                    if superior_loads[superior["id"]] == minimum
                ]
                selected = self.rng.choice(candidates)
                relation["superior_id"] = selected["id"]
                superior_loads[selected["id"]] += 1

    @staticmethod
    def _resolve_parent_manager(
        roles: Mapping[str, Mapping[str, Any]],
        characters_by_role: Mapping[str, list[dict[str, object]]],
        parent_organization_id: str | None,
        manager_id: str | None,
    ) -> None:
        if parent_organization_id is None:
            if manager_id is not None:
                raise ValueError("顶层 Organization 不能指定 manager_id")
            return
        if not isinstance(manager_id, str) or not manager_id:
            raise ValueError("sub-organization 必须指定有效的 manager_id")

        root_roles = [
            role_key for role_key, role in roles.items()
            if role.get("reports_to") is None
        ]
        if len(root_roles) != 1:
            raise ValueError(
                "sub-organization 必须且只能有一个 reports_to=None 的 leader role，"
                f"实际为：{root_roles}"
            )
        root_role = root_roles[0]
        leaders = characters_by_role[root_role]
        if len(leaders) != 1:
            raise ValueError(
                f"sub-organization 的 leader role“{root_role}”必须只生成 1 人，"
                f"实际为 {len(leaders)} 人"
            )
        leaders[0]["relation"].setdefault("superior_id", manager_id)

    def _assign_secretaries(
        self,
        roles: Mapping[str, Mapping[str, Any]],
        characters_by_role: Mapping[str, list[dict[str, object]]],
    ) -> None:
        available = {
            role_key: list(members)
            for role_key, members in characters_by_role.items()
        }
        for members in available.values():
            self.rng.shuffle(members)
        for leader_role, role_config in roles.items():
            secretary_config = role_config.get("secretary_config", {})
            if not secretary_config:
                continue
            leaders = list(characters_by_role[leader_role])
            self.rng.shuffle(leaders)
            for leader in leaders:
                for secretary_role, count in secretary_config.items():
                    pool = available[secretary_role]
                    if len(pool) < count:
                        raise ValueError(
                            f"role“{leader_role}”为 Character“{leader['id']}”分配"
                            f"“{secretary_role}”时需要 {count} 人，但只剩 {len(pool)} 人"
                        )
                    for _ in range(count):
                        secretary = pool.pop()
                        secretary["relation"].setdefault(
                            "service_to", leader["id"],
                        )

    @staticmethod
    def _sub_organization_count(
        spawn: Mapping[str, object],
        district: str,
        district_map: Mapping[str, int],
    ) -> int:
        mode = spawn["mode"]
        if mode == "fixed":
            return spawn["count"]
        if mode == "each_district":
            return len(district_map)
        if mode == "each_street":
            if district not in district_map:
                raise ValueError(
                    f"district_map 不包含当前 Organization 所在 District“{district}”"
                )
            return district_map[district]
        if mode == "highest_prosperity":
            return 1
        raise ValueError(f"未知的 sub_organization spawn.mode：{mode}")

    @classmethod
    def _assign_sub_organizations(
        cls,
        declarations: Mapping[str, Mapping[str, object]],
        characters_by_role: Mapping[str, list[dict[str, object]]],
        district: str,
        district_map: Mapping[str, int],
    ) -> list[dict[str, object]]:
        loads: dict[tuple[str, str], int] = defaultdict(int)
        assignments: dict[tuple[str, str, str], list[int]] = {}

        for declaration_key, declaration in declarations.items():
            template_name = declaration["template"]
            manager_role = declaration["leader_reports_to"]
            managers = characters_by_role[manager_role]
            child_count = cls._sub_organization_count(
                declaration["spawn"], district, district_map,
            )
            for instance_index in range(child_count):
                manager = min(
                    managers,
                    key=lambda character: loads[
                        (template_name, character["id"])
                    ],
                )
                load_key = (template_name, manager["id"])
                loads[load_key] += 1
                assignment_key = (
                    declaration_key,
                    template_name,
                    manager["id"],
                )
                assignments.setdefault(assignment_key, []).append(instance_index)

        return [
            {
                "declaration_key": declaration_key,
                "manager_id": manager_id,
                "template_name": template_name,
                "count": len(instance_indexes),
                "instance_indexes": instance_indexes,
                "organization_ids": [],
                "placements": [],
            }
            for (
                declaration_key,
                template_name,
                manager_id,
            ), instance_indexes in assignments.items()
        ]
