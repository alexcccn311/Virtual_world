"""Core data models shared by orchestration and generators."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class District:
    template: str
    name: str
    level: str
    population: int
    prosperity: int
    area: float
    unorganized_population_ratio: float
    sex_index: float
    required_organizations: dict[str, int]
    seed: tuple[float, float] | None = None
    cells: tuple[tuple[int, int], ...] = ()
    neighbors: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Street:
    name: str
    district: str | None
    seed: tuple[int, int]
    cells: tuple[tuple[int, int], ...]
    area: float
    centroid: tuple[float, float]
    neighbors: tuple[str, ...]
    prosperity: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Organization:
    id: str
    name: str
    type: str
    industry: str
    district: str | None
    street: str | None
    economy: str
    planned_size: int
    business_scope: tuple[str, ...] = ()
    market_position: str | None = None
    parent_organization_id: str | None = None
    address: tuple[int, int] | None = None
    offers_loans: bool = False
    max_total_debt: int | None = None
    loan_blackness: float | None = None
    sex_service_enabled: bool = False
    sex_service_min_level: int | None = None
    sex_service_commission_bps: int | None = None
    sex_service_price_multiplier_bounds: tuple[float, float] | None = None
    sexual_services: tuple[str, ...] = ()
    controlling_division: str | None = None
    controlling_subdivision: str | None = None

    def lending_description(self) -> str | None:
        if not self.offers_loans:
            return None
        limit = "无负债上限" if self.max_total_debt is None else f"贷款后总负债不得超过 ¥{self.max_total_debt:,}"
        return f"{limit}；一个月到期一次还清；利率与手续费按负债和机构黑心程度报价。"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Character:
    id: str
    sex: str
    age: int
    height: int
    bmi: float
    weight: float
    bust: float | None
    waist: float | None
    hips: float | None
    district: str | None
    street: str | None
    organization: str | None
    occupation: str
    rank: str | None
    superior_id: str | None
    name: str
    nickname: str | None
    net_worth: int
    debt: int
    cash: int
    other_assets: int
    income: int
    hygiene: str
    sexual_preferences: dict[str, str] | None
    attraction_preferences: dict[str, object] | None
    appearance_tags: tuple[str, ...]
    body_score: float
    body_grade: str
    attractiveness_score: float
    attractiveness_grade: str
    style_vibe: str | None
    personality_tags: tuple[str, ...]
    hobbies: tuple[str, ...]
    employment_status: str
    social_class: str
    industry: str
    organization_id: str | None
    division: str | None
    subdivision: str | None
    position: str | None
    sex_worker_score: float | None
    sex_worker_level: int | None
    sex_worker_percentile: float | None
    description: str | None
    service_to: str | None = None
    organization_role: str | None = None

    def as_dict(self) -> dict:
        result = asdict(self)
        result["appearance_tags"] = list(self.appearance_tags)
        result["personality_tags"] = list(self.personality_tags)
        result["hobbies"] = list(self.hobbies)
        return result


@dataclass(slots=True)
class CityPopulation:
    seed: int
    characters: list[Character]
    organizations: list[Organization]
    config_version: str
    districts: list[District] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class LoanQuote:
    organization_id: str
    current_debt: int
    principal: int
    blackness: float
    debt_pressure: float
    monthly_rate_bps: int
    fee_bps: int
    interest: int
    fee: int
    cash_disbursed: int
    total_due: int
    term_months: int = 1

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class PlayerLoanContract:
    loan_id: str
    borrower_character_id: str
    lender_organization_id: str
    lender_organization_name: str
    borrowed_at: str
    due_at: str
    principal: int
    cash_disbursed: int
    monthly_rate_bps: int
    interest: int
    fee_bps: int
    fee: int
    total_due: int
    status: str
    repaid_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

# ---- distributions.py ----
"""Reusable deterministic distribution helpers."""


import math
import random
from collections.abc import Mapping, Sequence
