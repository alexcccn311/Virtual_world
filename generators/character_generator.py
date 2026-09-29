"""Single-character generation for a city generation run.

Tunable tables live in :mod:`world_generation.character_config`; this module
contains scoring primitives, orchestration, and domain-specific generators.
"""
from __future__ import annotations

import random
from copy import deepcopy
from collections.abc import Mapping, Sequence
from math import exp, log10, sqrt
from typing import Callable

from ..character_config import (
    EDUCATION_LEVELS,
    NAME_SURNAME_POOL,
    GIVEN_NAME_POOLS,
    ENGLISH_NICKNAME_POOLS as _ENGLISH_NICKNAME_POOLS,
    STAGE_NICKNAME_POOLS as _STAGE_NICKNAME_POOLS,
    COVERT_NICKNAME_POOLS as _COVERT_NICKNAME_POOLS,
    PROFESSIONAL_NICKNAME_RULES as _PROFESSIONAL_NICKNAME_RULES,
    HAIRDRESSING_OCCUPATIONS as _HAIRDRESSING_OCCUPATIONS,
    COVERT_ROLE_KEYWORDS as _COVERT_ROLE_KEYWORDS,
    COVERT_SKILL_MARKERS as _COVERT_SKILL_MARKERS,
    ROUGH_OCCUPATION_KEYWORDS as _ROUGH_OCCUPATION_KEYWORDS,
    PERFORMER_NICKNAME_KEYWORDS as _PERFORMER_NICKNAME_KEYWORDS,
    ASSET_LEVEL_CURVE,
    PERSONALITY_ASSET_MODIFIERS,
    SEXUAL_PREFERENCE_WEIGHTS,
    SEXUAL_PREFERENCE_FORCED_TAGS,
    SEXUAL_PREFERENCE_TAG_MULTIPLIERS,
    ATTRACTION_BREAST_PREFERENCE_WEIGHTS,
    ATTRACTION_SERVICE_PREFERENCE_WEIGHTS,
    ATTRACTION_SERVICE_PREFERENCE_MULTIPLIERS,
    HOBBY_BASE_WEIGHTS,
    HYGIENE_BASE_WEIGHTS,
    HYGIENE_SEX_MULTIPLIERS,
    FEMALE_HAIR_STYLE_WEIGHTS,
    MALE_HAIR_STYLE_WEIGHTS,
    FEMALE_DYED_HAIR_COLOR_WEIGHTS,
    FEMALE_SKIN_QUALITY_OPTIONS,
    FEMALE_SKIN_QUALITY_WEIGHTS,
    MALE_SKIN_QUALITY_OPTIONS,
    MALE_SKIN_QUALITY_WEIGHTS,
    FEMALE_PRESENTATION_OPTIONS,
    FEMALE_PRESENTATION_WEIGHTS,
    MALE_PRESENTATION_OPTIONS,
    MALE_PRESENTATION_WEIGHTS,
    FEMALE_PRESENTATION_HYGIENE_MULTIPLIERS,
    MALE_PRESENTATION_HYGIENE_MULTIPLIERS,
    FEMALE_FEATURE_OPTIONS,
    FEMALE_FEATURE_WEIGHTS,
    MALE_FEATURE_OPTIONS,
    MALE_FEATURE_WEIGHTS,
    FEMALE_FACE_SKIN_ADJUSTMENTS,
    MALE_FACE_SKIN_ADJUSTMENTS,
    FEMALE_FACE_FEATURE_ADJUSTMENTS,
    MALE_FACE_FEATURE_ADJUSTMENTS,
    GRAY_OCCUPATION_FEATURE_MULTIPLIERS,
    ILLEGAL_OCCUPATION_FEATURE_MULTIPLIERS,
    FEMALE_TEMPERAMENT_OPTIONS,
    FEMALE_TEMPERAMENT_BASE_WEIGHTS,
    MALE_TEMPERAMENT_OPTIONS,
    MALE_TEMPERAMENT_BASE_WEIGHTS,
    MALE_TEMPERAMENT_OCCUPATION_MULTIPLIERS,
    FEMALE_FACE_SHAPE_WEIGHTS,
    MALE_FACE_SHAPE_WEIGHTS,
    FEMALE_JAWLINE_WEIGHTS,
    MALE_JAWLINE_WEIGHTS,
    FEMALE_CHIN_WEIGHTS,
    MALE_CHIN_WEIGHTS,
    EYE_SIZE_WEIGHTS,
    FEMALE_EYE_SHAPE_WEIGHTS,
    MALE_EYE_SHAPE_WEIGHTS,
    FEMALE_EYELID_WEIGHTS,
    MALE_EYELID_WEIGHTS,
    FEMALE_EYE_TAIL_WEIGHTS,
    MALE_EYE_TAIL_WEIGHTS,
    FEMALE_EYEBROW_THICKNESS_WEIGHTS,
    MALE_EYEBROW_THICKNESS_WEIGHTS,
    FEMALE_EYEBROW_SHAPE_WEIGHTS,
    MALE_EYEBROW_SHAPE_WEIGHTS,
    EYEBROW_PEAK_WEIGHTS,
    FEMALE_NOSE_HEIGHT_WEIGHTS,
    MALE_NOSE_HEIGHT_WEIGHTS,
    FEMALE_NOSE_WIDTH_WEIGHTS,
    MALE_NOSE_WIDTH_WEIGHTS,
    FEMALE_NOSE_TIP_WEIGHTS,
    MALE_NOSE_TIP_WEIGHTS,
    FEMALE_LIP_THICKNESS_WEIGHTS,
    MALE_LIP_THICKNESS_WEIGHTS,
    LIP_RELATION_WEIGHTS,
    FEMALE_LIP_CONTOUR_WEIGHTS,
    MALE_LIP_CONTOUR_WEIGHTS,
    CHILD_HEIGHT_MEAN,
    CHILD_BMI_MEAN,
    CHILD_BODY_RATIO_ANCHORS,
    SEX_SERVICE_PROFESSIONS,
    FEMALE_BODY_PROFILE,
    BODY_SCORE_WEIGHTS,
    BODY_SCORE_NORMAL_RANGES,
    BODY_SCORE_FORMULA_VERSION,
    BODY_SCORE_MODEL_VERSION,
    FACE_SCORE_WEIGHT,
    BODY_SCORE_WEIGHT,
    FACE_SCORE_NORMAL,
    FACE_SCORE_PERFECT,
)
from ..storage.global_id_registry import format_character_id


# =============================================================================
# Body and appearance scoring primitives
# =============================================================================


# Each facial area contributes only a small correction to the existing
# harmony-based score. Entries match the text emitted by
# ``generate_facial_features``; compound descriptions therefore retain the
# contribution of each of their constituent traits.
_FACIAL_FEATURE_SCORE_ADJUSTMENTS = {
    "female": {
        "face_shape": {
            "鹅蛋脸": 0.7, "瓜子脸": 0.6, "方圆脸": 0.2,
            "圆脸": 0.0, "长脸": -0.3, "菱形脸": -0.3,
            "下颌线柔和": 0.4, "下颌线自然": 0.2, "下颌线清晰": 0.2,
            "下颌略宽": -0.5,
            "下巴自然": 0.3, "下巴偏尖": 0.2, "下巴圆润": 0.1,
            "下巴略短": -0.5,
        },
        "eyes": {
            "偏小的": -0.3, "中等大小的": 0.2, "偏大的": 0.3,
            "杏眼": 0.4, "桃花眼": 0.4, "圆眼": 0.1, "细长眼": 0.2,
            "丹凤眼": 0.3, "下垂眼": -0.3, "狭长眼": -0.2,
            "双眼皮": 0.2, "内双": 0.1, "单眼皮": 0.0,
            "眼尾平直": 0.1, "眼尾轻微上扬": 0.3, "眼尾轻微下垂": -0.2,
        },
        "eyebrows": {
            "纤细的": 0.1, "粗细适中的": 0.3, "偏浓的": 0.0,
            "自然弯眉": 0.3, "平眉": 0.1, "柳叶眉": 0.2,
            "微挑眉": 0.2, "弧形眉": 0.1,
            "眉峰平缓": 0.0, "眉峰柔和": 0.2, "眉峰明显": 0.1,
        },
        "nose": {
            "鼻梁偏低": -0.4, "鼻梁自然": 0.2, "鼻梁高度自然": 0.2,
            "鼻梁较高": 0.4, "鼻梁高而立体": 0.5,
            "纤细": 0.2, "宽度适中": 0.3, "且偏宽": -0.3,
            "鼻头小巧、鼻翼偏窄": 0.4, "鼻头自然、鼻翼适中": 0.3,
            "鼻头圆润、鼻翼自然": 0.0, "鼻头较宽、鼻翼略宽": -0.4,
        },
        "lips": {
            "嘴唇偏薄": -0.1, "双唇厚度适中": 0.3,
            "双唇较丰满": 0.2, "双唇丰满": 0.1,
            "上下唇厚度接近": 0.2, "上唇略薄、下唇较饱满": 0.2,
            "上唇较饱满、下唇适中": 0.0,
            "唇形柔和": 0.2, "唇形清晰": 0.3, "唇峰明显": 0.2,
        },
    },
    "male": {
        "face_shape": {
            "方脸": 0.4, "长脸": 0.1, "方圆脸": 0.3,
            "鹅蛋脸": 0.2, "圆脸": -0.2, "菱形脸": 0.0,
            "下颌线清晰": 0.5, "下颌略宽": 0.2, "下颌线自然": 0.2,
            "下颌线柔和": -0.2,
            "下巴自然": 0.3, "下巴方正": 0.4, "下巴圆润": 0.0,
            "下巴略短": -0.4,
        },
        "eyes": {
            "偏小的": -0.2, "中等大小的": 0.3, "偏大的": 0.1,
            "细长眼": 0.3, "狭长眼": 0.1, "杏眼": 0.2, "丹凤眼": 0.3,
            "圆眼": 0.0, "下垂眼": -0.3, "深邃眼型": 0.4,
            "双眼皮": 0.1, "内双": 0.2, "单眼皮": 0.1,
            "眼尾平直": 0.2, "眼尾轻微上扬": 0.2, "眼尾轻微下垂": -0.2,
        },
        "eyebrows": {
            "偏浓的": 0.3, "粗细适中的": 0.2, "偏细的": -0.2,
            "平眉": 0.2, "自然眉": 0.3, "微挑眉": 0.1,
            "剑眉": 0.3, "弧形眉": 0.0,
            "眉峰平缓": 0.1, "眉峰柔和": 0.1, "眉峰明显": 0.2,
        },
        "nose": {
            "鼻梁偏低": -0.4, "鼻梁自然": 0.2, "鼻梁高度自然": 0.2,
            "鼻梁较高": 0.4, "鼻梁高而立体": 0.5,
            "纤细": 0.0, "宽度适中": 0.3, "且偏宽": -0.1,
            "鼻头较小、鼻翼偏窄": 0.1, "鼻头自然、鼻翼适中": 0.3,
            "鼻头圆润、鼻翼自然": 0.1, "鼻头较宽、鼻翼略宽": -0.3,
        },
        "lips": {
            "嘴唇偏薄": 0.0, "双唇厚度适中": 0.3,
            "双唇较丰满": 0.1, "双唇丰满": -0.1,
            "上下唇厚度接近": 0.2, "上唇略薄、下唇较饱满": 0.1,
            "上唇较饱满、下唇适中": 0.0,
            "唇形自然": 0.2, "唇形清晰": 0.3, "唇峰明显": 0.1,
        },
    },
}

# Controls the thin upper tail around each female Appearance Level. The same
# sampled factor is shared by face and body, preventing implausible cases where
# one is near-perfect while the other remains at the level baseline.
_FEMALE_APPEARANCE_TAIL_POWER = {
    0: 525.0,
    1: 270.0,
    2: 200.0,
    3: 90.0,
    4: 55.0,
    5: 31.0,
    6: 22.0,
    7: 14.0,
    8: 4.0,
}


def _perfect_cup_index_distance(value: float) -> float:
    """Return distance to the closest perfect cup-index route."""
    targets = FEMALE_BODY_PROFILE["perfect"]["cup_index"]["values"]
    return min(abs(value - target) for target in targets)


def _generate_female_appearance_factor(
    rng: random.Random,
    appearance_level: int,
) -> float:
    """Sample a coordinated face/body factor with a thin exceptional tail."""
    base_factor = FEMALE_BODY_PROFILE["level_factor"].get(appearance_level)
    if base_factor is None:
        raise ValueError(f"appearance_level 必须是 0–9，收到 {appearance_level!r}")
    if appearance_level == 9:
        return 1.0
    tail = rng.random() ** _FEMALE_APPEARANCE_TAIL_POWER[appearance_level]
    return base_factor + (1.0 - base_factor) * tail


def _female_body_score(
    height: float,
    bmi: float,
    waist_height: float,
    waist_hip: float,
    cup_index: float,
) -> float:
    """Score an adult female body by distance to the perfect body targets."""
    perfect = FEMALE_BODY_PROFILE["perfect"]
    distances = {
        "height": abs(height - perfect["height"]),
        "bmi": abs(bmi - perfect["bmi"]),
        "waist_height": abs(waist_height - perfect["waist_height"]),
        "waist_hip": abs(waist_hip - perfect["waist_hip"]),
        "cup_index": _perfect_cup_index_distance(cup_index),
    }
    normalized = {
        name: distance / (maximum - minimum)
        for name, distance in distances.items()
        for minimum, maximum in (BODY_SCORE_NORMAL_RANGES[name],)
    }
    weighted_distance = sum(
        BODY_SCORE_WEIGHTS[name] * normalized[name]
        for name in BODY_SCORE_WEIGHTS
    )
    return round(min(100.0, max(0.0, 100.0 * (1.0 - weighted_distance))), 2)


def _male_body_score(
    height: float,
    bmi: float,
    waist_height: float,
    waist_hip: float,
    chest_waist: float,
) -> float:
    """Score an adult male body against a broad natural male ideal."""
    targets = (178.0, 22.5, 0.445, 0.86, 1.20)
    ranges = ((150.0, 200.0), (16.0, 38.0), (0.38, 0.66), (0.74, 1.02), (0.92, 1.34))
    weights = (0.15, 0.25, 0.20, 0.20, 0.20)
    values = (height, bmi, waist_height, waist_hip, chest_waist)
    weighted_distance = sum(
        weight * abs(value - target) / (maximum - minimum)
        for value, target, (minimum, maximum), weight in zip(values, targets, ranges, weights)
    )
    return round(min(100.0, max(0.0, 100.0 * (1.0 - weighted_distance))), 2)


def _minor_body_score(
    sex: str,
    age: int,
    bmi: float,
    waist_height: float,
    waist_hip: float,
) -> float:
    """Give minors a simple same-age, same-sex coordination score."""
    sex_index = 0 if sex == "male" else 1
    bmi_target = CHILD_BMI_MEAN[age][sex_index]
    anchors = CHILD_BODY_RATIO_ANCHORS[sex]
    upper_age = next(anchor_age for anchor_age in anchors if anchor_age >= age)
    if upper_age == age or upper_age == 0:
        target_waist_height, target_waist_hip, _ = anchors[upper_age]
    else:
        lower_age = max(anchor_age for anchor_age in anchors if anchor_age < age)
        progress = (age - lower_age) / (upper_age - lower_age)
        lower = anchors[lower_age]
        upper = anchors[upper_age]
        target_waist_height = lower[0] + (upper[0] - lower[0]) * progress
        target_waist_hip = lower[1] + (upper[1] - lower[1]) * progress
    weighted_distance = (
        0.40 * abs(bmi - bmi_target) / 3.0
        + 0.30 * abs(waist_height - target_waist_height) / 0.12
        + 0.30 * abs(waist_hip - target_waist_hip) / 0.14
    )
    return round(min(100.0, max(0.0, 100.0 * (1.0 - weighted_distance))), 2)


def calculate_raw_body_score(
    sex: str,
    height: float,
    bmi: float,
    cup_index: float,
    waist_height: float,
    waist_hip: float,
) -> float:
    """Calculate the raw score from the five female core body parameters."""
    if sex == "female":
        return _female_body_score(height, bmi, waist_height, waist_hip, cup_index)
    return 50.0


def _bust_from_cup_index(
    height: float,
    bmi: float,
    waist: float,
    cup_index: float,
) -> float:
    """Derive bust circumference from frame, BMI, waist, and cup index."""
    # The cup contribution is intentionally convex: neighboring cup levels
    # separate progressively while the frame and BMI keep the result bounded.
    frame = 8.0 + 0.02 * (height - 160.0) + 0.35 * (bmi - 18.5)
    cup_difference = 6.0 + 3.2 * cup_index + 0.6 * cup_index ** 2
    return waist + frame + cup_difference


def cup_index_to_text(cup_index: float) -> str:
    """Map a continuous cup index to the nearest displayed cup label."""
    labels = ("A", "B", "C", "D", "E", "F")
    index = min(len(labels) - 1, max(0, round(cup_index)))
    return labels[index]


def _cup_index_from_text(cup_size: object) -> float | None:
    if cup_size is None:
        return None
    labels = ("A", "B", "C", "D", "E", "F")
    if cup_size not in labels:
        raise ValueError("special_role.cup_size 必须是 A–F 或 None")
    return float(labels.index(cup_size))


def _cup_index_from_measurements(
    height: float,
    bmi: float,
    bust: float,
    waist: float,
) -> float:
    frame = 8.0 + 0.02 * (height - 160.0) + 0.35 * (bmi - 18.5)
    target = bust - waist - frame
    discriminant = 3.2 ** 2 - 4 * 0.6 * (6.0 - target)
    value = (-3.2 + sqrt(max(0.0, discriminant))) / 1.2
    return min(5.0, max(0.0, value))


# =============================================================================
# Identity and asset helpers
# =============================================================================

_next_character_number = 1
_issued_character_ids: set[str] = set()

_POSSIBLE_SKILL_PROBABILITY = 0.35

def _generate_name_parts(rng: random.Random, sex: str) -> tuple[str, str]:
    try:
        given_name_pool = GIVEN_NAME_POOLS[sex]
    except KeyError as exc:
        raise ValueError(f"unsupported sex for name generation: {sex!r}") from exc
    if not NAME_SURNAME_POOL or not given_name_pool:
        raise ValueError("character name pools must not be empty")
    return rng.choice(NAME_SURNAME_POOL), rng.choice(given_name_pool)


def generate_name(rng: random.Random, sex: str) -> str:
    """Generate a Chinese full name from the shared surname and sexed given-name pools."""
    surname, given_name = _generate_name_parts(rng, sex)
    return f"{surname}{given_name}"


def generate_nickname(
    rng: random.Random,
    sex: str,
    occupation: str,
    *,
    title: str = "",
    surname: str = "",
    given_name: str = "",
    age: int = 30,
    hair: str = "",
    feature: str = "",
    height: float | None = None,
    bmi: float | None = None,
    personality_tags: Sequence[str] = (),
    occupation_type: str | None = None,
    skills: Mapping[str, str] | None = None,
) -> str:
    """Generate one nickname using hard role rules, weighted traits, then fallback."""
    if sex not in _ENGLISH_NICKNAME_POOLS:
        raise ValueError(f"unsupported sex for nickname generation: {sex!r}")

    family = surname or "阿"
    given = given_name or "安"
    given_tail = given[-1]
    gendered_address = "哥" if sex == "male" else "姐"
    role_text = f"{occupation}|{title}"
    skill_names = set(skills or ())
    management_keywords = (
        "老板", "店主", "经理", "主管", "总监", "负责人", "领班", "店长",
        "组长", "妈咪",
    )
    is_management = any(keyword in role_text for keyword in management_keywords)

    # Hard rule 1: people actually doing hairdressing work use an English work
    # name. Sex workers and performers are excluded even inside a hair salon.
    is_hairdressing_staff = occupation in _HAIRDRESSING_OCCUPATIONS or (
        bool(skill_names.intersection({"发型设计", "洗发护理"}))
        and occupation not in SEX_SERVICE_PROFESSIONS
        and not any(keyword in role_text for keyword in _PERFORMER_NICKNAME_KEYWORDS)
    )
    if is_hairdressing_staff:
        nickname_pool = _ENGLISH_NICKNAME_POOLS[sex]
        if not nickname_pool:
            raise ValueError(f"English nickname pool for {sex!r} must not be empty")
        return rng.choice(nickname_pool)

    # Hard rule 2: sex workers and actual performers use a stage name which is
    # unrelated to their legal name. Management roles do not enter this rule.
    is_performer = (
        any(keyword in role_text for keyword in _PERFORMER_NICKNAME_KEYWORDS)
        and not is_management
    )
    if occupation in SEX_SERVICE_PROFESSIONS or is_performer:
        return rng.choice(_STAGE_NICKNAME_POOLS[sex])

    is_covert = (
        occupation_type == "ILLEGAL_OCCUPATIONS"
        or any(keyword in role_text for keyword in _COVERT_ROLE_KEYWORDS)
        or bool(skill_names.intersection(_COVERT_SKILL_MARKERS))
    )
    is_rough_occupation = (
        is_covert
        or any(keyword in role_text for keyword in _ROUGH_OCCUPATION_KEYWORDS)
        or bool(
            skill_names.intersection(
                {
                    "徒手格斗", "近距离战斗", "肢体制伏",
                    "手枪射击", "步枪射击", "匕首格斗",
                }
            )
        )
    )
    candidates: dict[str, float] = {}

    def add(nickname: str, weight: float) -> None:
        if nickname and weight > 0:
            candidates[nickname] = candidates.get(nickname, 0.0) + weight

    professional_prefix = "" if is_covert else family
    for keywords, suffixes in _PROFESSIONAL_NICKNAME_RULES:
        if any(keyword in role_text for keyword in keywords):
            for suffix in suffixes:
                add(f"{professional_prefix}{suffix}", 8.0)
            break

    leadership_rules = (
        (("会首", "首脑", "帮主"), ("爷" if sex == "male" else "姐")),
        (("董事长", "总裁", "总经理", "总监", "总管", "负责人"), "总"),
        (("老板", "店主"), "老板"),
        (("经理",), "经理"),
        (("主任",), "主任"),
        (("主管",), "主管"),
        (("队长",), "队"),
        (("组长",), "组长"),
        (("领班",), "领班"),
    )
    for keywords, suffix in leadership_rules:
        if any(keyword in role_text for keyword in keywords):
            if is_covert:
                if any(keyword in role_text for keyword in ("会首", "首脑", "帮主")):
                    add("老大" if sex == "male" else "大姐", 8.0)
                elif suffix in {"总", "老板"}:
                    add("掌柜" if suffix == "总" else "老板", 7.0)
                else:
                    add("管事" if suffix in {"经理", "主任", "主管", "领班"} else "头儿", 7.0)
            else:
                add(f"{family}{suffix}", 7.0)
            break

    adult_venue_context = (
        any(keyword in role_text for keyword in ("成人", "色情", "夜场", "KTV", "会所", "发廊"))
        or bool(skill_names.intersection({"色情业务运营", "性工作者管理"}))
    )
    basic_management = any(
        keyword in role_text for keyword in ("妈咪", "领班", "主管", "店长", "组长")
    )
    if sex == "female" and adult_venue_context and basic_management:
        add(f"{family}姐", 9.0)

    # Visible-feature aliases are deliberately narrow: only rough male jobs
    # use scar, dark-skin, or metal-tooth descriptions as nicknames.
    if sex == "male" and is_rough_occupation:
        if feature == "面部有浅淡疤痕":
            add("刀疤", 8.0)
        elif feature == "金属牙":
            add("金牙", 8.0)
        elif feature == "肤色偏深":
            add("黑皮", 6.0)

    if "光头" in hair:
        add("光头", 6.0)

    if sex == "male" and bmi is not None:
        if bmi >= 30:
            add("大块头", 6.0)
            if height is not None and height >= 180:
                add("铁塔", 6.0)
            if feature == "肤色偏深" and is_rough_occupation:
                # In this specific combination 黑熊 is about twice as likely
                # as either of the other build-based aliases.
                add("黑熊", 12.0)
        elif bmi <= 18:
            add("竹竿", 6.0)

    personality_set = set(personality_tags)
    if personality_set.intersection({"暴戾", "残忍", "虐待狂"}):
        add("疯狗" if is_covert else "狠角色", 4.0)
    if personality_set.intersection({"阴险", "恶毒腹黑", "虚伪"}):
        add("笑面虎", 4.0)
    if personality_set.intersection({"贪得无厌", "势利", "虚荣拜金"}):
        add("铁算盘", 3.0)

    if is_covert:
        # Covert and illegal workers receive a non-identifying fallback rather
        # than leaking any part of their legal name.
        add(rng.choice(_COVERT_NICKNAME_POOLS[sex]), 6.0)
    else:
        # Ordinary social forms are the universal legal-role fallback.
        if age <= 25:
            add(f"小{family}", 6.0)
            add(f"阿{given_tail}", 2.0)
        elif age >= 50:
            add(f"老{family}", 6.0)
            add(f"{family}{gendered_address}", 3.0)
        elif sex == "female" and age >= 35:
            add(f"{family}姐", 6.0)
            add(f"阿{given_tail}", 2.0)
        elif sex == "female":
            add(f"小{family}", 4.0)
            add(f"阿{given_tail}", 3.0)
        else:
            add(f"{family}哥", 5.0)
            add(f"阿{given_tail}", 2.0)

    nicknames = tuple(candidates)
    return rng.choices(
        nicknames,
        weights=tuple(candidates[nickname] for nickname in nicknames),
        k=1,
    )[0]


def generate_education(
    rng: random.Random,
    education_weights: object,
) -> str | None:
    """Choose a Chinese education label from an ordered role weight sequence."""
    if (
        not isinstance(education_weights, Sequence)
        or isinstance(education_weights, (str, bytes, bytearray))
        or len(education_weights) != len(EDUCATION_LEVELS)
    ):
        return None
    if any(
        not isinstance(weight, (int, float))
        or isinstance(weight, bool)
        or weight < 0
        for weight in education_weights
    ):
        return None
    if sum(education_weights) <= 0:
        return None
    return rng.choices(
        EDUCATION_LEVELS,
        weights=education_weights,
        k=1,
    )[0]


def generate_skills(
    rng: random.Random,
    skill_config: object,
) -> dict[str, str]:
    """Materialize required skills and independently sample possible skills."""
    if not isinstance(skill_config, Mapping):
        return {}

    required = skill_config.get("required", {})
    generated = dict(required) if isinstance(required, Mapping) else {}
    possible = skill_config.get("possible", {})
    if not isinstance(possible, Mapping):
        return generated

    for skill, configured_levels in possible.items():
        if skill in generated:
            continue
        valid_levels = (
            tuple(configured_levels)
            if isinstance(configured_levels, Sequence)
            and not isinstance(configured_levels, (str, bytes, bytearray))
            else ()
        )
        if valid_levels and rng.random() < _POSSIBLE_SKILL_PROBABILITY:
            generated[skill] = rng.choice(valid_levels)
    return generated


def set_next_character_id(next_number: int) -> None:
    """Advance the process-wide allocator after restoring persisted state."""
    global _next_character_number
    if isinstance(next_number, bool) or not isinstance(next_number, int):
        raise ValueError("next character id number must be an integer")
    if next_number < 1:
        raise ValueError("next character id number must be at least 1")
    if next_number < _next_character_number:
        raise ValueError("next character id number cannot move backwards")
    _next_character_number = next_number


def _allocate_character_id() -> str:
    global _next_character_number
    while True:
        character_id = format_character_id(_next_character_number)
        _next_character_number += 1
        if character_id not in _issued_character_ids:
            _issued_character_ids.add(character_id)
            return character_id


def _claim_character_id(character_id: object) -> str:
    if not isinstance(character_id, str) or not character_id:
        raise ValueError("special_role.id 必须是非空字符串")
    if character_id in _issued_character_ids:
        raise ValueError(f"Character ID“{character_id}”已经被使用")
    _issued_character_ids.add(character_id)
    return character_id


def calculate_asset_level(net_assets: float) -> float:
    if net_assets == 0:
        return 0.0

    curve = ASSET_LEVEL_CURVE
    cap = (
        curve["positive_cap"]
        if net_assets > 0
        else curve["negative_cap"]
    )
    assets = min(abs(net_assets), cap)

    level = (
        log10(
            1
            + (assets / curve["soft_scale"]) ** curve["curve_power"]
        )
        / log10(
            1
            + (cap / curve["soft_scale"]) ** curve["curve_power"]
        )
    )
    return level if net_assets > 0 else -level


# =============================================================================
# Public generation orchestrator
# =============================================================================

class CharacterGenerator:
    """Generate one character step by step."""

    def __init__(
        self,
        rng: random.Random,
        *,
        id_allocator: Callable[[], str] | None = None,
        id_claimer: Callable[[object], str] | None = None,
    ) -> None:
        self.rng = rng
        self.id_allocator = id_allocator or _allocate_character_id
        self.id_claimer = id_claimer or _claim_character_id

    def generate(
        self,
        character_config: Mapping[str, object],
        special_role: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if special_role is not None and not isinstance(special_role, Mapping):
            raise TypeError("special_role 必须是 mapping 或 None")
        special = {} if special_role is None else special_role

        def supplied(field_name: str) -> bool:
            return field_name in special

        def supplied_value(field_name: str):
            return deepcopy(special[field_name])

        # ID is overridable but always claimed globally, so it cannot be
        # reused by a later generated or special Character.
        character_id = (
            self.id_claimer(supplied_value("id"))
            if supplied("id")
            else self.id_allocator()
        )

        # District and Street are placement-owned and cannot be overridden by
        # a special role. Occupation and title remain player-overridable after
        # the role template has selected the slot this Character occupies.
        district = character_config["district"]
        street = character_config["street"]
        occupation = (
            supplied_value("occupation")
            if supplied("occupation")
            else character_config["occupation"]
        )
        title = (
            supplied_value("title")
            if supplied("title")
            else character_config["title"]
        )
        occupation_type = character_config.get("occupation_type")

        if supplied("sex"):
            sex = supplied_value("sex")
        else:
            sex_weights = character_config["sex"]
            sex = self.rng.choices(
                tuple(sex_weights.keys()),
                weights=tuple(sex_weights.values()),
                k=1,
            )[0]
        if sex not in {"male", "female"}:
            raise ValueError("special_role.sex 必须是 'male' 或 'female'")

        if supplied("name"):
            name = supplied_value("name")
            if not isinstance(name, str) or not name:
                raise ValueError("special_role.name 必须是非空字符串")
            surname = name[0]
            given_name = name[1:] or name[0]
        else:
            surname, given_name = _generate_name_parts(self.rng, sex)
            name = f"{surname}{given_name}"

        if supplied("age"):
            age = supplied_value("age")
        else:
            minimum_age, maximum_age = character_config["age"]
            age = self.rng.randint(minimum_age, maximum_age)
        if isinstance(age, bool) or not isinstance(age, int) or age < 0:
            raise ValueError("special_role.age 必须是非负整数")

        hair = (
            supplied_value("hair")
            if supplied("hair")
            else generate_hair(self.rng, sex, age)
        )

        finance = character_config["finance"]
        income = (
            supplied_value("income")
            if supplied("income")
            else self.rng.randint(*finance["income"])
        )
        cash = (
            supplied_value("cash")
            if supplied("cash")
            else self.rng.randint(*finance["cash"])
        )
        other_assets = (
            supplied_value("other_assets")
            if supplied("other_assets")
            else self.rng.randint(*finance["other_assets"])
        )
        debt = (
            supplied_value("debt")
            if supplied("debt")
            else self.rng.randint(*finance["debt"])
        )
        net_assets = (
            supplied_value("net_assets")
            if supplied("net_assets")
            else cash + other_assets - debt
        )
        asset_level = calculate_asset_level(net_assets)

        appearance_level = (
            9 if asset_level == 1.0
            else 7 if occupation in SEX_SERVICE_PROFESSIONS
            else 5 if any(x in occupation for x in ("前台", "秘书", "迎宾"))
            else min(9, int(max(0.0, asset_level) * 10 + 0.5))
        )
        female_appearance_factor = (
            _generate_female_appearance_factor(self.rng, appearance_level)
            if sex == "female"
            else None
        )

        skin_quality = (
            supplied_value("skin_quality")
            if supplied("skin_quality")
            else generate_skin_quality(
                self.rng,
                sex,
                age,
                occupation,
                asset_level,
            )
        )

        body_fields = ("height", "bmi", "bust", "waist", "hips")
        need_base_body = any(not supplied(field) for field in body_fields)
        need_base_body = need_base_body or (
            sex == "female" and not supplied("cup_size")
        )
        if need_base_body:
            (
                base_height,
                base_bmi,
                base_weight,
                base_bust,
                base_waist,
                base_hips,
                base_cup_index,
            ) = generate_body_measurements(
                self.rng,
                sex,
                age,
                occupation,
                appearance_level,
                asset_level=asset_level,
                include_cup_index=True,
                female_appearance_factor=female_appearance_factor,
            )
        else:
            base_height = base_bmi = base_weight = None
            base_bust = base_waist = base_hips = None
            base_cup_index = None

        height = supplied_value("height") if supplied("height") else base_height
        if supplied("bmi"):
            bmi = supplied_value("bmi")
        elif supplied("weight"):
            bmi = supplied_value("weight") / (height / 100) ** 2
        else:
            bmi = base_bmi
        scale = height / base_height if base_height else 1.0
        base_waist_scaled = base_waist * scale if base_waist is not None else None
        base_hips_scaled = base_hips * scale if base_hips is not None else None
        base_bust_scaled = base_bust * scale if base_bust is not None else None
        base_waist_hip = (
            base_waist / base_hips
            if base_waist is not None and base_hips
            else None
        )

        if supplied("waist"):
            waist = supplied_value("waist")
        elif supplied("hips") and base_waist_hip is not None:
            waist = supplied_value("hips") * base_waist_hip
        else:
            waist = base_waist_scaled
        if supplied("hips"):
            hips = supplied_value("hips")
        elif supplied("waist") and base_waist_hip is not None:
            hips = waist / base_waist_hip
        else:
            hips = base_hips_scaled

        if sex == "female" and supplied("cup_size"):
            cup_index = _cup_index_from_text(supplied_value("cup_size"))
        else:
            cup_index = base_cup_index
        if supplied("bust"):
            bust = supplied_value("bust")
            if sex == "female" and not supplied("cup_size"):
                cup_index = _cup_index_from_measurements(
                    height, bmi, bust, waist,
                )
        elif sex == "female" and cup_index is not None:
            if any(
                supplied(field)
                for field in (
                    "height", "bmi", "weight", "waist", "hips", "cup_size",
                )
            ):
                bust = _bust_from_cup_index(height, bmi, waist, cup_index)
            else:
                bust = base_bust_scaled
        elif sex == "male" and base_waist and base_bust is not None:
            bust = waist * (base_bust / base_waist)
        else:
            bust = base_bust_scaled

        weight = (
            supplied_value("weight")
            if supplied("weight")
            else round(bmi * (height / 100) ** 2, 1)
        )

        personality_tags = (
            supplied_value("personality_tags")
            if supplied("personality_tags")
            else generate_personality_tags(
                self.rng,
                sex,
                asset_level,
                character_config["personality_weights"],
            )
        )
        sexual_preferences = (
            supplied_value("sexual_preferences")
            if supplied("sexual_preferences")
            else generate_sexual_preferences(
                self.rng,
                sex,
                age,
                personality_tags,
            )
        )
        attraction_preferences = (
            supplied_value("attraction_preferences")
            if supplied("attraction_preferences")
            else generate_attraction_preferences(
                self.rng,
                sex,
                age,
                sexual_preferences,
            )
        )

        hobbies = (
            supplied_value("hobbies")
            if supplied("hobbies")
            else generate_hobbies(
                self.rng,
                sex,
                age,
                net_assets,
                personality_tags,
            )
        )
        hygiene = (
            supplied_value("hygiene")
            if supplied("hygiene")
            else generate_hygiene(self.rng, sex, asset_level)
        )
        presentation = (
            supplied_value("presentation")
            if supplied("presentation")
            else generate_presentation(
                self.rng,
                sex,
                age,
                occupation,
                asset_level,
                hygiene,
            )
        )
        feature = (
            supplied_value("feature")
            if supplied("feature")
            else generate_feature(self.rng, sex, occupation_type)
        )

        facial_fields = ("face_shape", "eyes", "eyebrows", "nose", "lips")
        if any(not supplied(field) for field in facial_fields):
            generated_face = generate_facial_features(self.rng, sex)
        else:
            generated_face = (None, None, None, None, None)
        face_shape, eyes, eyebrows, nose, lips = (
            supplied_value(field) if supplied(field) else generated
            for field, generated in zip(facial_fields, generated_face)
        )
        face_score = (
            supplied_value("face_score")
            if supplied("face_score")
            else generate_face_score(
                self.rng,
                sex,
                age,
                asset_level,
                skin_quality,
                feature,
                appearance_level,
                face_shape=face_shape,
                eyes=eyes,
                eyebrows=eyebrows,
                nose=nose,
                lips=lips,
                female_appearance_factor=female_appearance_factor,
            )
        )
        waist_height = waist / height
        waist_hip = waist / hips
        if supplied("body_score"):
            body_score = supplied_value("body_score")
        else:
            if sex == "female" and age >= 18:
                body_score = calculate_raw_body_score(
                    sex,
                    height,
                    bmi,
                    cup_index,
                    waist_height,
                    waist_hip,
                )
            elif sex == "male" and age >= 18:
                body_score = _male_body_score(
                    height,
                    bmi,
                    waist_height,
                    waist_hip,
                    bust / waist,
                )
            else:
                body_score = _minor_body_score(
                    sex,
                    age,
                    bmi,
                    waist_height,
                    waist_hip,
                )
            body_score = round(min(100.0, max(0.0, body_score)), 2)
        appearance_score = (
            supplied_value("appearance_score")
            if supplied("appearance_score")
            else round(
                min(
                    100.0,
                    max(
                        0.0,
                        face_score * FACE_SCORE_WEIGHT
                        + body_score * BODY_SCORE_WEIGHT,
                    ),
                ),
                2,
            )
        )

        cup_size = (
            supplied_value("cup_size")
            if supplied("cup_size")
            else (
                cup_index_to_text(cup_index)
                if sex == "female" and cup_index is not None
                else None
            )
        )
        temperament = (
            supplied_value("temperament")
            if supplied("temperament")
            else generate_temperament(
                self.rng,
                sex,
                age,
                height,
                bmi,
                bust,
                waist,
                hips,
                presentation,
                hair,
                feature,
                occupation_type,
            )
        )

        background_rng = random.Random()
        background_rng.setstate(self.rng.getstate())
        education = (
            supplied_value("education")
            if supplied("education")
            else generate_education(
                background_rng,
                character_config.get("education"),
            )
        )
        skills = (
            supplied_value("skills")
            if supplied("skills")
            else generate_skills(
                background_rng,
                character_config.get("skills"),
            )
        )
        if supplied("nickname"):
            nickname = supplied_value("nickname")
        else:
            nickname_rng = random.Random()
            nickname_rng.setstate(self.rng.getstate())
            nickname = generate_nickname(
                nickname_rng,
                sex,
                occupation,
                title=title,
                surname=surname,
                given_name=given_name,
                age=age,
                hair=hair,
                feature=feature,
                height=height,
                bmi=bmi,
                personality_tags=personality_tags,
                occupation_type=occupation_type,
                skills=skills,
            )
        relation = (
            supplied_value("relation") if supplied("relation") else {}
        )

        return {
            "id": character_id,
            "name": name,
            "nickname": nickname,
            "district": district,
            "street": street,
            "occupation": occupation,
            "title": title,
            "education": education,
            "skills": skills,
            "sex": sex,
            "age": age,
            "hair": hair,
            "skin_quality": skin_quality,
            "income": income,
            "cash": cash,
            "other_assets": other_assets,
            "debt": debt,
            "net_assets": net_assets,
            "height": height,
            "bmi": bmi,
            "weight": weight,
            "bust": bust,
            "waist": waist,
            "hips": hips,
            "personality_tags": personality_tags,
            "sexual_preferences": sexual_preferences,
            "attraction_preferences": attraction_preferences,
            "hobbies": hobbies,
            "hygiene": hygiene,
            "presentation": presentation,
            "feature": feature,
            "face_shape": face_shape,
            "eyes": eyes,
            "eyebrows": eyebrows,
            "nose": nose,
            "lips": lips,
            "face_score": face_score,
            "body_score": body_score,
            "appearance_score": appearance_score,
            "cup_size": cup_size,
            "temperament": temperament,
            "relation": relation,
        }


# =============================================================================
# Shared sampling helpers
# =============================================================================

def _truncated_normal(
    rng: random.Random,
    mean: float,
    standard_deviation: float,
    minimum: float,
    maximum: float,
) -> float:
    while True:
        value = rng.normalvariate(mean, standard_deviation)
        if minimum <= value <= maximum:
            return value


def generate_natural_adult_bmi(sex: str, rng: random.Random) -> float:
    if sex == "male":
        return round(_truncated_normal(rng, 23.6, 3.2, 16.0, 38.0), 1)
    return round(_truncated_normal(rng, 21.5, 3.0, 15.0, 35.0), 1)


# =============================================================================
# Appearance generation: hair and skin
# =============================================================================

def generate_hair(
    rng: random.Random,
    sex: str,
    age: int,
) -> str:
    def _gray_hair_probability(age: int) -> float:
        if age < 50:
            return 0.0
        if age <= 52:
            return 0.15
        if age <= 55:
            return 0.35
        if age <= 59:
            return 0.70
        return 1.0

    if sex == "male":
        if age >= 45 and rng.random() < 0.08:
            return "光头"

        gray_probability = _gray_hair_probability(age)
        is_gray = gray_probability > 0 and rng.random() < gray_probability
        style = rng.choices(
            tuple(MALE_HAIR_STYLE_WEIGHTS.keys()),
            weights=tuple(MALE_HAIR_STYLE_WEIGHTS.values()),
            k=1,
        )[0]
        return f"花白{style}" if is_gray else style

    gray_probability = _gray_hair_probability(age)
    is_gray = gray_probability > 0 and rng.random() < gray_probability
    if is_gray:
        style_weights = dict(FEMALE_HAIR_STYLE_WEIGHTS)
        style_weights.pop("盘发")
        style = rng.choices(
            tuple(style_weights.keys()),
            weights=tuple(style_weights.values()),
            k=1,
        )[0]
        return f"花白{style}"

    style = rng.choices(
        tuple(FEMALE_HAIR_STYLE_WEIGHTS.keys()),
        weights=tuple(FEMALE_HAIR_STYLE_WEIGHTS.values()),
        k=1,
    )[0]
    if age < 40 and rng.random() < 0.60:
        color = rng.choices(
            tuple(FEMALE_DYED_HAIR_COLOR_WEIGHTS.keys()),
            weights=tuple(FEMALE_DYED_HAIR_COLOR_WEIGHTS.values()),
            k=1,
        )[0]
        return f"{color}{style}"
    if age < 50:
        return f"乌黑亮丽的{style}"
    return f"黑色{style}"


def generate_skin_quality(
    rng: random.Random,
    sex: str,
    age: int,
    occupation: str,
    asset_level: float,
) -> str:
    def _skin_quality_age_multipliers(sex: str, age: int) -> tuple[float, ...]:
        if sex == "female":
            if age < 25:
                return (0.55, 0.80, 1.10, 1.30, 1.45)
            if age < 40:
                return (0.80, 0.95, 1.10, 1.10, 1.00)
            if age < 50:
                return (1.00, 1.15, 1.15, 0.90, 0.55)
            if age < 60:
                return (1.30, 1.30, 1.05, 0.65, 0.30)
            return (1.70, 1.45, 0.90, 0.40, 0.15)

        if age < 25:
            return (0.55, 0.75, 1.00, 1.30, 1.45)
        if age < 40:
            return (0.85, 0.95, 1.00, 1.10, 1.00)
        if age < 50:
            return (1.10, 1.20, 1.05, 0.85, 0.60)
        if age < 60:
            return (1.35, 1.35, 1.00, 0.65, 0.35)
        return (1.70, 1.50, 0.90, 0.45, 0.18)

    if sex == "female":
        options = FEMALE_SKIN_QUALITY_OPTIONS
        base_weights = FEMALE_SKIN_QUALITY_WEIGHTS
    else:
        options = MALE_SKIN_QUALITY_OPTIONS
        base_weights = MALE_SKIN_QUALITY_WEIGHTS

    age_multipliers = _skin_quality_age_multipliers(sex, age)
    weights = tuple(
        weight * multiplier
        for weight, multiplier in zip(base_weights, age_multipliers)
    )

    draw_count = 1
    if sex == "female" and age >= 18 and occupation in SEX_SERVICE_PROFESSIONS:
        draw_count = 2
    if asset_level > 0.5:
        draw_count = 2
    if asset_level == 1.0:
        draw_count = 5

    best_index = max(
        rng.choices(range(len(options)), weights=weights, k=1)[0]
        for _ in range(draw_count)
    )
    return options[best_index]


# =============================================================================
# Appearance generation: body
# =============================================================================

def generate_body_measurements(
    rng: random.Random,
    sex: str,
    age: int,
    occupation: str,
    female_body_level: int,
    *,
    asset_level: float = 0.0,
    include_cup_index: bool = False,
    female_appearance_factor: float | None = None,
) -> (
    tuple[int, float, float, float, float, float]
    | tuple[int, float, float, float, float, float, float | None]
):
    def _interpolate_child_body_ratios(
        sex: str,
        age: int,
    ) -> tuple[float, float, float]:
        anchors = CHILD_BODY_RATIO_ANCHORS[sex]
        upper_age = next(anchor_age for anchor_age in anchors if anchor_age >= age)
        if upper_age == age or upper_age == 0:
            return anchors[upper_age]
        lower_age = max(anchor_age for anchor_age in anchors if anchor_age < age)
        progress = (age - lower_age) / (upper_age - lower_age)
        return tuple(
            lower + (upper - lower) * progress
            for lower, upper in zip(anchors[lower_age], anchors[upper_age])
        )

    def _generate_body_laziness(
        rng: random.Random,
        asset_level: float,
        occupation: str,
    ) -> float:
        if asset_level == 1.0:
            return _truncated_normal(rng, 0.08, 0.06, 0.0, 0.22)

        mean = (
            0.55
            - 0.24 * max(0.0, asset_level)
            + 0.10 * max(0.0, -asset_level)
        )
        special_body_occupation = (
            occupation in SEX_SERVICE_PROFESSIONS
            or "前台" in occupation
            or "秘书" in occupation
            or "迎宾" in occupation
        )
        if special_body_occupation:
            return _truncated_normal(rng, min(mean, 0.30), 0.12, 0.0, 0.60)
        return _truncated_normal(rng, mean, 0.16, 0.0, 1.0)

    def _body_age_progress(sex: str, age: int) -> float:
        if sex == "female":
            reference_age, midpoint_age, steepness = 22.0, 35.0, 0.10
        else:
            reference_age, midpoint_age, steepness = 22.0, 23.0, 0.11
        if age <= reference_age:
            return 0.0
        reference_value = 1.0 / (
            1.0 + exp(-steepness * (reference_age - midpoint_age))
        )
        age_value = 1.0 / (
            1.0 + exp(-steepness * (age - midpoint_age))
        )
        return min(1.0, max(0.0, (age_value - reference_value) / (1.0 - reference_value)))

    def _male_body_quality(
        sex: str,
        bmi: float,
        waist_height_ratio: float,
        waist_hip_ratio: float,
        bust_waist_ratio: float,
    ) -> float:
        targets = (
            (bmi, 23.0, 3.0),
            (waist_height_ratio, 0.445, 0.040),
            (waist_hip_ratio, 0.860, 0.060),
            (bust_waist_ratio, 1.24, 0.12),
        )
        return sum(
            exp(-0.5 * ((value - center) / width) ** 2)
            for value, center, width in targets
        ) / len(targets)

    def _apply_adult_body_aging(
        sex: str,
        age: int,
        laziness: float,
        bmi: float,
        waist_height_ratio: float,
        waist_hip_ratio: float,
        cup_index: float | None,
        male_bust_waist_ratio: float | None,
        waist_height_bmi_coefficient: float,
    ) -> tuple[float, float, float, float]:
        if sex == "female":
            body_quality = _female_body_score(
                height, bmi, waist_height_ratio, waist_hip_ratio, cup_index,
            ) / 100.0
        else:
            body_quality = _male_body_quality(
                sex,
                bmi,
                waist_height_ratio,
                waist_hip_ratio,
                male_bust_waist_ratio,
            )
        age_progress = _body_age_progress(sex, age)
        quality_reference = 0.50 if sex == "female" else 0.80
        laziness_sensitivity = 0.80 + 0.50 * age_progress
        quality_sensitivity = 0.45 + 0.30 * age_progress
        individual_multiplier = (
            1.0
            + laziness_sensitivity * (laziness - 0.55)
            + quality_sensitivity * max(0.0, body_quality - quality_reference)
        )
        individual_multiplier = min(1.75, max(0.25, individual_multiplier))
        effective_age_progress = min(
            1.0,
            max(0.0, age_progress * individual_multiplier),
        )

        if sex == "female":
            p = effective_age_progress
            bmi_adjustment = 3.55 * p ** 0.78
            total_waist_height_adjustment = 0.079 * p ** 1.07
            waist_hip_adjustment = 0.100 * p ** 1.30
            bmi_range = (14.0, 40.0)
        else:
            p = effective_age_progress
            population_bmi_adjustment = max(
                0.0,
                2.64 * age_progress ** 0.72
                - 0.043 * max(0.0, age - 35.0),
            )
            bmi_adjustment = max(
                0.0,
                population_bmi_adjustment
                + 0.75 * age_progress * (individual_multiplier - 1.0),
            )
            total_waist_height_adjustment = 0.0415 * p ** 0.44
            waist_hip_adjustment = 0.067 * p ** 0.77
            bust_waist_adjustment = -0.039 * p ** 0.32
            bmi_range = (15.0, 42.0)

        final_bmi = min(bmi_range[1], max(bmi_range[0], bmi + bmi_adjustment))
        bmi_delta = final_bmi - bmi
        bmi_related_waist_change = waist_height_bmi_coefficient * bmi_delta
        extra_waist_height_adjustment = (
            total_waist_height_adjustment - bmi_related_waist_change
        )
        final_waist_height_ratio = (
            waist_height_ratio
            + bmi_related_waist_change
            + extra_waist_height_adjustment
        )
        final_waist_height_ratio = min(0.66, max(0.34, final_waist_height_ratio))
        final_waist_hip_ratio = min(
            1.05,
            max(0.62, waist_hip_ratio + waist_hip_adjustment),
        )
        final_male_bust_waist_ratio = male_bust_waist_ratio
        if sex == "male":
            final_male_bust_waist_ratio = min(
                1.34,
                max(0.92, male_bust_waist_ratio + bust_waist_adjustment),
            )
        return (
            final_bmi,
            final_waist_height_ratio,
            final_waist_hip_ratio,
            final_male_bust_waist_ratio,
        )

    if age < 18:
        sex_index = 0 if sex == "male" else 1
        height_mean = CHILD_HEIGHT_MEAN[age][sex_index]
        bmi_mean = CHILD_BMI_MEAN[age][sex_index]
        height = round(
            _truncated_normal(
                rng,
                height_mean,
                2.0,
                height_mean - 4,
                height_mean + 4,
            )
        )
        bmi = round(
            _truncated_normal(
                rng,
                bmi_mean,
                0.6,
                bmi_mean - 1.0,
                bmi_mean + 1.0,
            ),
            1,
        )
        ratio_means = _interpolate_child_body_ratios(sex, age)
        waist_height_ratio = _truncated_normal(
            rng,
            ratio_means[0],
            0.006,
            ratio_means[0] - 0.012,
            ratio_means[0] + 0.012,
        )
        waist_hip_ratio = _truncated_normal(
            rng,
            ratio_means[1],
            0.010,
            ratio_means[1] - 0.020,
            ratio_means[1] + 0.020,
        )
        bust_waist_ratio = _truncated_normal(
            rng,
            ratio_means[2],
            0.012,
            ratio_means[2] - 0.024,
            ratio_means[2] + 0.024,
        )
        weight = round(bmi * (height / 100) ** 2, 1)
        waist = height * waist_height_ratio
        hips = waist / waist_hip_ratio
        bust = waist * bust_waist_ratio
        result = (height, bmi, weight, round(bust, 1), round(waist, 1), round(hips, 1))
        return (*result, None) if include_cup_index else result

    male_bust_waist_ratio = None
    if sex == "male":
        height = round(_truncated_normal(rng, 172, 7, 150, 200))
        if asset_level == 1.0:
            bmi = _truncated_normal(rng, 23.0, 0.8, 21.0, 25.0)
            waist_height_bmi_coefficient = 0.010
            waist_height_ratio = _truncated_normal(
                rng,
                0.445 + waist_height_bmi_coefficient * (bmi - 23.0),
                0.012,
                0.41,
                0.48,
            )
            waist_hip_ratio = _truncated_normal(rng, 0.86, 0.025, 0.80, 0.93)
            male_bust_waist_ratio = _truncated_normal(rng, 1.24, 0.040, 1.14, 1.34)
        else:
            bmi = generate_natural_adult_bmi(sex, rng)
            waist_height_bmi_coefficient = 0.012
            waist_height_ratio = _truncated_normal(
                rng,
                0.478 + waist_height_bmi_coefficient * (bmi - 23.6),
                0.035,
                0.38,
                0.66,
            )
            waist_hip_ratio = _truncated_normal(rng, 0.860, 0.050, 0.74, 1.02)
            male_bust_waist_ratio = _truncated_normal(rng, 1.11, 0.080, 0.92, 1.34)
        cup_index = None
    else:
        normal = FEMALE_BODY_PROFILE["normal"]
        perfect = FEMALE_BODY_PROFILE["perfect"]
        base_factor = FEMALE_BODY_PROFILE["level_factor"].get(female_body_level)
        if base_factor is None:
            raise ValueError(f"female_body_level 必须是 0–9，收到 {female_body_level!r}")
        factor = (
            base_factor
            if female_appearance_factor is None
            else female_appearance_factor
        )
        if not 0.0 <= factor <= 1.0:
            raise ValueError(
                "female_appearance_factor 必须在 0–1 范围内，"
                f"收到 {factor!r}"
            )

        # Generate an ordinary Level 0 body first.  All interpolation stays
        # in floating point until the final public measurements are formed.
        normal_height = _truncated_normal(
            rng, normal["height"]["mean"], normal["height"]["std"],
            normal["height"]["min"], normal["height"]["max"],
        )
        normal_bmi = _truncated_normal(
            rng, normal["bmi"]["mean"], normal["bmi"]["std"],
            normal["bmi"]["min"], normal["bmi"]["max"],
        )
        normal_waist_height = _truncated_normal(
            rng, normal["waist_height"]["mean"], normal["waist_height"]["std"],
            normal["waist_height"]["min"], normal["waist_height"]["max"],
        )
        normal_waist_hip = _truncated_normal(
            rng, normal["waist_hip"]["mean"], normal["waist_hip"]["std"],
            normal["waist_hip"]["min"], normal["waist_hip"]["max"],
        )
        normal_cup_index = _truncated_normal(
            rng, normal["cup_index"]["mean"], normal["cup_index"]["std"],
            normal["cup_index"]["min"], normal["cup_index"]["max"],
        )

        perfect_cup_index = min(
            perfect["cup_index"]["values"],
            key=lambda target: abs(target - normal_cup_index),
        )

        def contract(normal_value: float, perfect_value: float) -> float:
            return normal_value + (perfect_value - normal_value) * factor

        height = contract(normal_height, perfect["height"])
        bmi = contract(normal_bmi, perfect["bmi"])
        waist_height_ratio = contract(normal_waist_height, perfect["waist_height"])
        waist_hip_ratio = contract(normal_waist_hip, perfect["waist_hip"])
        cup_index = contract(normal_cup_index, perfect_cup_index)
        waist_height_bmi_coefficient = 0.0

    laziness = _generate_body_laziness(rng, asset_level, occupation)
    bmi, waist_height_ratio, waist_hip_ratio, male_bust_waist_ratio = (
        _apply_adult_body_aging(
            sex,
            age,
            laziness,
            bmi,
            waist_height_ratio,
            waist_hip_ratio,
            cup_index,
            male_bust_waist_ratio,
            waist_height_bmi_coefficient,
        )
    )
    final_height = round(height)
    waist = final_height * waist_height_ratio
    hips = waist / waist_hip_ratio
    bust = (
        _bust_from_cup_index(final_height, bmi, waist, cup_index)
        if sex == "female"
        else waist * male_bust_waist_ratio
    )

    bmi = round(bmi, 1)
    weight = round(bmi * (final_height / 100) ** 2, 1)
    result = (
        final_height,
        bmi,
        weight,
        round(bust, 1),
        round(waist, 1),
        round(hips, 1),
    )
    return (*result, cup_index) if include_cup_index else result


# =============================================================================
# Personality and lifestyle generation
# =============================================================================

def generate_personality_tags(
    rng: random.Random,
    sex: str,
    asset_level: float,
    personality_weights: Mapping[str, Mapping[str, float]],
) -> list[str]:
    def asset_personality_multiplier(
        tag: str,
        asset_level: float,
    ) -> float:
        modifiers = PERSONALITY_ASSET_MODIFIERS[sex][tag]
        t = min(1.0, abs(asset_level))
        smooth_t = t * t * (3.0 - 2.0 * t)
        target_multiplier = (
            modifiers["poor"]
            if asset_level < 0
            else modifiers["rich"]
        )
        return 1.0 + (target_multiplier - 1.0) * smooth_t

    def calculate_final_weights() -> dict[str, float]:
        base_weights = personality_weights[sex]
        raw_weights = {
            tag: base_weight * asset_personality_multiplier(tag, asset_level)
            for tag, base_weight in base_weights.items()
        }
        total = sum(raw_weights.values())
        if total <= 0:
            raise ValueError("最终人格权重总和必须大于 0")
        return {
            tag: weight / total
            for tag, weight in raw_weights.items()
        }

    def weighted_sample_without_replacement(
        weights: Mapping[str, float],
        count: int,
    ) -> list[str]:
        pool = dict(weights)
        selected: list[str] = []
        for _ in range(count):
            total = sum(pool.values())
            threshold = rng.random() * total
            cumulative = 0.0
            chosen = next(reversed(pool))
            for tag, weight in pool.items():
                cumulative += weight
                if threshold < cumulative:
                    chosen = tag
                    break
            selected.append(chosen)
            del pool[chosen]
        return selected

    weights = calculate_final_weights()
    count = rng.randint(2, 3)
    return weighted_sample_without_replacement(weights, count)


def generate_sexual_preferences(
    rng: random.Random,
    sex: str,
    age: int,
    personality_tags: Sequence[str],
) -> dict[str, str] | None:
    """Generate the three adult-male sexual disposition axes.

    Explicit personality tags have priority over random sampling.  Related
    traits only reshape an axis's weights: violence or arrogance alone does
    not guarantee the most extreme sexual outcome.
    """
    if sex not in {"male", "female"}:
        raise ValueError(f"不支持的性别：{sex}")
    if sex != "male" or age < 18:
        return None

    tag_set = set(personality_tags)
    forced: dict[str, str] = {}
    for tag in tag_set:
        forced.update(SEXUAL_PREFERENCE_FORCED_TAGS.get(tag, {}))

    generated: dict[str, str] = {}
    for axis, base_weights in SEXUAL_PREFERENCE_WEIGHTS.items():
        if axis in forced:
            generated[axis] = forced[axis]
            continue

        options = tuple(base_weights)
        weights = [float(base_weights[option]) for option in options]
        for tag in tag_set:
            multipliers = SEXUAL_PREFERENCE_TAG_MULTIPLIERS.get(tag, {}).get(axis)
            if multipliers is None:
                continue
            if len(multipliers) != len(options):
                raise ValueError(f"人格标签 {tag} 的 {axis} 偏好修正数量不匹配")
            weights = [
                weight * multiplier
                for weight, multiplier in zip(weights, multipliers)
            ]
        generated[axis] = rng.choices(options, weights=weights, k=1)[0]

    return generated


def generate_attraction_preferences(
    rng: random.Random,
    sex: str,
    age: int,
    sexual_preferences: Mapping[str, str] | None,
) -> dict[str, object] | None:
    """Generate adult-male preferences consumed by ``choose_sex_worker``."""
    if sex not in {"male", "female"}:
        raise ValueError(f"不支持的性别：{sex}")
    if sex != "male" or age < 18:
        return None
    if (
        not isinstance(sexual_preferences, Mapping)
        or set(sexual_preferences) != {"desire", "violence", "control"}
    ):
        raise ValueError("成年男性缺少完整的 sexual_preferences")

    service_weights = {
        service: float(weight)
        for service, weight in ATTRACTION_SERVICE_PREFERENCE_WEIGHTS.items()
    }
    for axis, value in sexual_preferences.items():
        axis_modifiers = ATTRACTION_SERVICE_PREFERENCE_MULTIPLIERS.get(axis, {})
        if value not in axis_modifiers:
            raise ValueError(f"未知的 {axis} 偏好等级：{value}")
        for service, multiplier in axis_modifiers[value].items():
            service_weights[service] *= multiplier

    service_weight_total = sum(service_weights.values())
    if service_weight_total <= 0:
        raise ValueError("性服务偏好权重总和必须大于 0")
    normalized_service_weights = {
        service: round(weight * 100.0 / service_weight_total, 4)
        for service, weight in service_weights.items()
    }
    # Keep the stored distribution exactly at 100 despite decimal rounding.
    final_service = next(reversed(normalized_service_weights))
    normalized_service_weights[final_service] = round(
        normalized_service_weights[final_service]
        + 100.0 - sum(normalized_service_weights.values()),
        4,
    )

    breast_options = tuple(ATTRACTION_BREAST_PREFERENCE_WEIGHTS)
    style_options = tuple(FEMALE_TEMPERAMENT_BASE_WEIGHTS)
    return {
        "breasts": rng.choices(
            breast_options,
            weights=tuple(
                ATTRACTION_BREAST_PREFERENCE_WEIGHTS[preference]
                for preference in breast_options
            ),
            k=1,
        )[0],
        "sexual_service_weights": normalized_service_weights,
        "style": rng.choices(
            style_options,
            weights=tuple(
                FEMALE_TEMPERAMENT_BASE_WEIGHTS[style]
                for style in style_options
            ),
            k=1,
        )[0],
    }


def generate_hobbies(
    rng: random.Random,
    sex: str,
    age: int,
    net_assets: float,
    personality_tags: list[str],
) -> list[str]:
    weights = dict(HOBBY_BASE_WEIGHTS)

    if net_assets < 10_000_000:
        weights.pop("茶道", None)
        weights.pop("性奴收藏", None)

    if age < 18:
        for hobby in (
            "嫖妓",
            "百家乐",
            "牌九",
            "麻将",
            "线上博彩",
            "吸毒",
            "饮酒",
        ):
            weights.pop(hobby, None)

    if not 16 <= age <= 50:
        weights.pop("拳击", None)

    if sex == "female":
        weights.pop("嫖妓", None)

    if age < 30:
        for hobby, multiplier in {
            "短视频": 1.4,
            "电子游戏": 2.0,
            "嘻哈": 1.8,
            "线上博彩": 1.5,
            "球类运动": 1.4,
        }.items():
            if hobby in weights:
                weights[hobby] *= multiplier
    elif age >= 50:
        for hobby, multiplier in {
            "麻将": 1.8,
            "牌九": 1.5,
            "电子游戏": 0.4,
            "嘻哈": 0.4,
            "线上博彩": 0.6,
            "茶道": 1.3,
        }.items():
            if hobby in weights:
                weights[hobby] *= multiplier

    if net_assets < 0:
        if "线上博彩" in weights:
            weights["线上博彩"] *= 1.3
        if "百家乐" in weights:
            weights["百家乐"] *= 0.8
    elif 1_000_000 <= net_assets < 10_000_000:
        if "百家乐" in weights:
            weights["百家乐"] *= 1.25
    elif net_assets >= 10_000_000:
        if "百家乐" in weights:
            weights["百家乐"] *= 1.5

    for tag in personality_tags:
        if tag in {"色情狂", "性饥渴"} and "嫖妓" in weights:
            weights["嫖妓"] *= 2.5
        elif tag == "贪得无厌":
            for hobby in ("百家乐", "牌九", "麻将", "线上博彩"):
                if hobby in weights:
                    weights[hobby] *= 1.8
        elif tag == "暴戾" and "拳击" in weights:
            weights["拳击"] *= 1.8
        elif tag == "残忍" and "拳击" in weights:
            weights["拳击"] *= 1.4

        if "性奴收藏" in weights:
            if tag == "控制欲强":
                weights["性奴收藏"] *= 1.8
            elif tag == "虐待狂":
                weights["性奴收藏"] *= 2.2
            elif tag == "傲慢":
                weights["性奴收藏"] *= 1.3
                if "茶道" in weights:
                    weights["茶道"] *= 1.5

    total = sum(weights.values())
    if total <= 0:
        raise ValueError("最终爱好权重总和必须大于 0")

    count = 2 if rng.random() < 0.68 else 3
    count = min(count, len(weights))

    pool = dict(weights)
    selected: list[str] = []
    for _ in range(count):
        total = sum(pool.values())
        if total <= 0:
            raise ValueError("剩余爱好权重总和必须大于 0")
        threshold = rng.random() * total
        cumulative = 0.0
        chosen = next(reversed(pool))
        for hobby, weight in pool.items():
            cumulative += weight
            if threshold < cumulative:
                chosen = hobby
                break
        selected.append(chosen)
        del pool[chosen]

    return selected


def generate_hygiene(
    rng: random.Random,
    sex: str,
    asset_level: float,
) -> str:
    weights = {
        label: weight * HYGIENE_SEX_MULTIPLIERS[sex][label]
        for label, weight in HYGIENE_BASE_WEIGHTS.items()
    }

    if asset_level >= 0.6971:
        weights["卫生良好"] *= 5.0
        weights["卫生较差"] *= 2.0
        weights["肮脏恶臭"] *= 0.35
        weights["污秽不堪"] *= 0.15
    elif asset_level >= 0.3943:
        weights["卫生良好"] *= 3.2
        weights["卫生较差"] *= 1.7
        weights["肮脏恶臭"] *= 0.55
        weights["污秽不堪"] *= 0.30
    elif asset_level >= 0:
        weights["卫生良好"] *= 1.6
        weights["卫生较差"] *= 1.25
        weights["肮脏恶臭"] *= 0.85
        weights["污秽不堪"] *= 0.70
    elif asset_level < -0.2181:
        weights["卫生良好"] *= 0.55
        weights["肮脏恶臭"] *= 1.20
        weights["污秽不堪"] *= 1.35

    return rng.choices(
        tuple(weights.keys()),
        weights=tuple(weights.values()),
        k=1,
    )[0]


def generate_presentation(
    rng: random.Random,
    sex: str,
    age: int,
    occupation: str,
    asset_level: float,
    hygiene: str,
) -> str:
    def _female_presentation_age_multipliers(age: int) -> tuple[float, ...]:
        if age < 25:
            return (0.85, 0.95, 1.00, 1.15, 1.20, 1.15)
        if age < 40:
            return (0.95, 1.00, 1.00, 1.10, 1.10, 1.00)
        if age < 50:
            return (1.00, 1.00, 1.05, 1.10, 0.95, 0.80)
        if age < 60:
            return (1.10, 1.05, 1.15, 1.00, 0.80, 0.55)
        return (1.25, 1.10, 1.20, 0.90, 0.65, 0.35)

    def _male_presentation_age_multipliers(age: int) -> tuple[float, ...]:
        if age < 25:
            return (0.90, 1.10, 0.90, 1.15, 0.95, 0.85, 0.70)
        if age < 40:
            return (1.00, 1.05, 1.00, 1.05, 1.00, 0.95, 0.85)
        if age < 50:
            return (1.10, 1.10, 1.10, 1.00, 1.00, 0.90, 0.75)
        if age < 60:
            return (1.15, 1.10, 1.15, 0.95, 1.05, 0.85, 0.70)
        return (1.20, 1.15, 1.20, 0.90, 1.10, 0.75, 0.60)

    def _presentation_asset_multipliers(
        sex: str,
        asset_level: float,
    ) -> tuple[float, ...]:
        if sex == "female":
            rich_targets = (0.45, 0.75, 1.00, 1.15, 1.45, 1.80)
            poor_targets = (1.35, 1.20, 1.05, 0.90, 0.75, 0.60)
        else:
            rich_targets = (0.45, 0.55, 0.80, 0.90, 1.20, 1.45, 1.60)
            poor_targets = (1.35, 1.35, 1.15, 1.05, 0.90, 0.75, 0.65)

        t = min(1.0, abs(asset_level))
        smooth_t = t * t * (3.0 - 2.0 * t)
        targets = poor_targets if asset_level < 0 else rich_targets
        return tuple(1.0 + (target - 1.0) * smooth_t for target in targets)

    if occupation in {"性奴", "SM妓女", "冰妹"}:
        return rng.choices(
            FEMALE_PRESENTATION_OPTIONS[:3],
            weights=(55, 30, 15),
            k=1,
        )[0]

    positive_occupation = (
        occupation == "妓女"
        or "前台" in occupation
        or "秘书" in occupation
        or "迎宾" in occupation
    )
    if positive_occupation:
        if sex == "female":
            return rng.choices(
                FEMALE_PRESENTATION_OPTIONS[3:],
                weights=(25, 40, 35),
                k=1,
            )[0]
        return rng.choices(
            MALE_PRESENTATION_OPTIONS[4:],
            weights=(25, 45, 30),
            k=1,
        )[0]

    if sex == "female":
        options = FEMALE_PRESENTATION_OPTIONS
        base_weights = FEMALE_PRESENTATION_WEIGHTS
        age_multipliers = _female_presentation_age_multipliers(age)
        hygiene_multipliers = FEMALE_PRESENTATION_HYGIENE_MULTIPLIERS[hygiene]
    else:
        options = MALE_PRESENTATION_OPTIONS
        base_weights = MALE_PRESENTATION_WEIGHTS
        age_multipliers = _male_presentation_age_multipliers(age)
        hygiene_multipliers = MALE_PRESENTATION_HYGIENE_MULTIPLIERS[hygiene]

    asset_multipliers = _presentation_asset_multipliers(sex, asset_level)
    weights = tuple(
        base * age_multiplier * hygiene_multiplier * asset_multiplier
        for base, age_multiplier, hygiene_multiplier, asset_multiplier in zip(
            base_weights,
            age_multipliers,
            hygiene_multipliers,
            asset_multipliers,
        )
    )
    return rng.choices(options, weights=weights, k=1)[0]


# =============================================================================
# Appearance generation: face
# =============================================================================

def generate_feature(
    rng: random.Random,
    sex: str,
    occupation_type: str | None,
) -> str:
    if sex == "female":
        return rng.choices(
            FEMALE_FEATURE_OPTIONS,
            weights=FEMALE_FEATURE_WEIGHTS,
            k=1,
        )[0]

    weights = list(MALE_FEATURE_WEIGHTS)
    if occupation_type == "GREY_OCCUPATIONS":
        weights = [
            weight * multiplier
            for weight, multiplier in zip(
                weights,
                GRAY_OCCUPATION_FEATURE_MULTIPLIERS,
            )
        ]
    elif occupation_type == "ILLEGAL_OCCUPATIONS":
        weights = [
            weight * multiplier
            for weight, multiplier in zip(
                weights,
                ILLEGAL_OCCUPATION_FEATURE_MULTIPLIERS,
            )
        ]

    return rng.choices(
        MALE_FEATURE_OPTIONS,
        weights=weights,
        k=1,
    )[0]


def generate_facial_features(
    rng: random.Random,
    sex: str,
) -> tuple[str, str, str, str, str]:
    def choose(weights: Mapping[str, float]) -> str:
        return rng.choices(
            tuple(weights.keys()),
            weights=tuple(weights.values()),
            k=1,
        )[0]

    if sex == "female":
        face_weights = FEMALE_FACE_SHAPE_WEIGHTS
        jawline_weights = FEMALE_JAWLINE_WEIGHTS
        chin_weights = FEMALE_CHIN_WEIGHTS
        eye_shape_weights = FEMALE_EYE_SHAPE_WEIGHTS
        eyelid_weights = FEMALE_EYELID_WEIGHTS
        eye_tail_weights = FEMALE_EYE_TAIL_WEIGHTS
        eyebrow_thickness_weights = FEMALE_EYEBROW_THICKNESS_WEIGHTS
        eyebrow_shape_weights = FEMALE_EYEBROW_SHAPE_WEIGHTS
        nose_height_weights = FEMALE_NOSE_HEIGHT_WEIGHTS
        nose_width_weights = FEMALE_NOSE_WIDTH_WEIGHTS
        nose_tip_weights = FEMALE_NOSE_TIP_WEIGHTS
        lip_thickness_weights = FEMALE_LIP_THICKNESS_WEIGHTS
        lip_contour_weights = FEMALE_LIP_CONTOUR_WEIGHTS
    else:
        face_weights = MALE_FACE_SHAPE_WEIGHTS
        jawline_weights = MALE_JAWLINE_WEIGHTS
        chin_weights = MALE_CHIN_WEIGHTS
        eye_shape_weights = MALE_EYE_SHAPE_WEIGHTS
        eyelid_weights = MALE_EYELID_WEIGHTS
        eye_tail_weights = MALE_EYE_TAIL_WEIGHTS
        eyebrow_thickness_weights = MALE_EYEBROW_THICKNESS_WEIGHTS
        eyebrow_shape_weights = MALE_EYEBROW_SHAPE_WEIGHTS
        nose_height_weights = MALE_NOSE_HEIGHT_WEIGHTS
        nose_width_weights = MALE_NOSE_WIDTH_WEIGHTS
        nose_tip_weights = MALE_NOSE_TIP_WEIGHTS
        lip_thickness_weights = MALE_LIP_THICKNESS_WEIGHTS
        lip_contour_weights = MALE_LIP_CONTOUR_WEIGHTS

    face_shape = f"{choose(face_weights)}，{choose(jawline_weights)}，{choose(chin_weights)}"

    eye_size = choose(EYE_SIZE_WEIGHTS)
    eye_shape = choose(eye_shape_weights)
    eye_size_text = "中等大小的" if eye_size == "中等大小" else f"{eye_size}的"
    eyes = f"{eye_size_text}{eye_shape}，{choose(eyelid_weights)}，{choose(eye_tail_weights)}"

    eyebrow_thickness = choose(eyebrow_thickness_weights)
    eyebrows = (
        f"{eyebrow_thickness}的{choose(eyebrow_shape_weights)}，"
        f"{choose(EYEBROW_PEAK_WEIGHTS)}"
    )

    nose_height = choose(nose_height_weights)
    nose_width = choose(nose_width_weights)
    if nose_width == "鼻梁纤细":
        nose_bridge = f"{nose_height}而纤细"
    elif nose_width == "鼻梁宽度适中":
        nose_bridge = (
            "鼻梁高度自然、宽度适中"
            if nose_height == "鼻梁自然"
            else f"{nose_height}、宽度适中"
        )
    else:
        nose_bridge = f"{nose_height}且偏宽"
    nose = f"{nose_bridge}，{choose(nose_tip_weights)}"

    lips = (
        f"{choose(lip_thickness_weights)}，{choose(LIP_RELATION_WEIGHTS)}，"
        f"{choose(lip_contour_weights)}"
    )
    return face_shape, eyes, eyebrows, nose, lips


def _facial_feature_score_adjustment(
    sex: str,
    face_shape: str,
    eyes: str,
    eyebrows: str,
    nose: str,
    lips: str,
) -> float:
    descriptions = {
        "face_shape": face_shape,
        "eyes": eyes,
        "eyebrows": eyebrows,
        "nose": nose,
        "lips": lips,
    }
    total = 0.0
    for area, description in descriptions.items():
        area_adjustments = _FACIAL_FEATURE_SCORE_ADJUSTMENTS[sex][area]
        if area == "face_shape":
            # Face-shape labels such as ``方圆脸`` contain shorter labels
            # such as ``圆脸``; exact component matching avoids double count.
            components = description.split("，")
            total += sum(
                adjustment
                for text, adjustment in area_adjustments.items()
                if text in components
            )
        else:
            total += sum(
                adjustment
                for text, adjustment in area_adjustments.items()
                if text in description
            )
    return total


def generate_face_score(
    rng: random.Random,
    sex: str,
    age: int,
    asset_level: float,
    skin_quality: str,
    feature: str,
    appearance_level: int = 0,
    *,
    face_shape: str = "",
    eyes: str = "",
    eyebrows: str = "",
    nose: str = "",
    lips: str = "",
    female_appearance_factor: float | None = None,
) -> float:
    level_factor = FEMALE_BODY_PROFILE["level_factor"].get(appearance_level)
    if level_factor is None:
        raise ValueError(f"appearance_level 必须是 0–9，收到 {appearance_level!r}")
    # Appearance Level is a female-only model input. Male generation keeps
    # its natural distribution regardless of the supplied level.
    if sex == "female":
        factor = (
            level_factor
            if female_appearance_factor is None
            else female_appearance_factor
        )
        if not 0.0 <= factor <= 1.0:
            raise ValueError(
                "female_appearance_factor 必须在 0–1 范围内，"
                f"收到 {factor!r}"
            )
    else:
        factor = 0.0
    facial_harmony = _truncated_normal(
        rng,
        FACE_SCORE_NORMAL["mean"],
        FACE_SCORE_NORMAL["std"],
        FACE_SCORE_NORMAL["min"],
        FACE_SCORE_NORMAL["max"],
    )

    if sex == "female":
        base_age_penalty = 30.0 / (
            1.0 + exp(-0.4 * (age - 28.0))
        )
        skin_adjustment = FEMALE_FACE_SKIN_ADJUSTMENTS[skin_quality]
        feature_adjustment = FEMALE_FACE_FEATURE_ADJUSTMENTS[feature]
    else:
        base_age_penalty = (
            0.0
            if age <= 25
            else 23.0 * (1.0 - exp(-0.08 * (age - 25)))
        )
        skin_adjustment = MALE_FACE_SKIN_ADJUSTMENTS[skin_quality]
        feature_adjustment = MALE_FACE_FEATURE_ADJUSTMENTS[feature]

    positive_asset_level = max(0.0, asset_level)
    protection = (positive_asset_level ** 0.8) * 0.30
    maintenance_factor = 1.0 - protection

    effective_age_penalty = base_age_penalty * maintenance_factor
    facial_feature_adjustment = _facial_feature_score_adjustment(
        sex,
        face_shape,
        eyes,
        eyebrows,
        nose,
        lips,
    )
    level_zero_score = (
        facial_harmony
        - effective_age_penalty
        + skin_adjustment
        + feature_adjustment
        + facial_feature_adjustment
    )
    level_zero_score = min(100.0, max(0.0, level_zero_score))
    face_score = level_zero_score + (FACE_SCORE_PERFECT - level_zero_score) * factor
    return round(min(100.0, max(0.0, face_score)), 2)


# =============================================================================
# Temperament generation
# =============================================================================

def generate_temperament(
    rng: random.Random,
    sex: str,
    age: int,
    height: float,
    bmi: float,
    bust: float | None,
    waist: float | None,
    hips: float | None,
    presentation: str,
    hair: str,
    feature: str,
    occupation_type: str | None,
) -> str:
    if sex == "male":
        weights = dict(MALE_TEMPERAMENT_BASE_WEIGHTS)
        multipliers = MALE_TEMPERAMENT_OCCUPATION_MULTIPLIERS.get(occupation_type)
        if multipliers is not None:
            for label, multiplier in multipliers.items():
                weights[label] *= multiplier
        return rng.choices(
            MALE_TEMPERAMENT_OPTIONS,
            weights=tuple(weights[label] for label in MALE_TEMPERAMENT_OPTIONS),
            k=1,
        )[0]

    weights = dict(FEMALE_TEMPERAMENT_BASE_WEIGHTS)

    def apply(multipliers: Mapping[str, float]) -> None:
        for label, multiplier in multipliers.items():
            weights[label] *= multiplier

    if age < 18:
        weights["御姐感"] = 0
        weights["娇媚感"] = 0
        weights["成熟风韵"] = 0
        apply({"甜妹感": 1.35, "软萌感": 1.45, "清冷感": 1.05, "温婉感": 1.05})
    elif age <= 22:
        weights["成熟风韵"] = 0
        apply({"甜妹感": 1.30, "软萌感": 1.35, "御姐感": 0.80, "娇媚感": 0.95})
    elif age <= 29:
        weights["成熟风韵"] = 0
        apply({"御姐感": 1.10, "甜妹感": 1.00, "清冷感": 1.05, "娇媚感": 1.05, "冷锐感": 1.05})
    elif age <= 34:
        apply({"御姐感": 1.15, "成熟风韵": 1.20, "温婉感": 1.10, "软萌感": 0.80})
    elif age <= 49:
        apply({"御姐感": 1.20, "成熟风韵": 1.45, "温婉感": 1.15, "甜妹感": 0.70, "软萌感": 0.60})
    else:
        apply({"成熟风韵": 1.65, "温婉感": 1.20, "御姐感": 1.10, "甜妹感": 0.55, "软萌感": 0.45})

    if height < 155:
        apply({"软萌感": 1.8, "甜妹感": 1.35, "御姐感": 0.55, "冷锐感": 0.75})
    elif height < 165:
        apply({"软萌感": 1.25, "甜妹感": 1.15})
    elif height >= 170:
        apply({"御姐感": 1.65, "清冷感": 1.25, "冷锐感": 1.45, "软萌感": 0.55})

    if bmi < 18.5:
        apply({"清冷感": 1.35, "软萌感": 1.15, "娇媚感": 0.80, "成熟风韵": 0.80})
    elif bmi <= 21.5:
        apply({"甜妹感": 1.10, "清冷感": 1.10, "冷锐感": 1.10})
    elif bmi < 24:
        apply({"御姐感": 1.10, "娇媚感": 1.15, "温婉感": 1.10, "成熟风韵": 1.10})
    else:
        apply({"成熟风韵": 1.30, "温婉感": 1.15, "娇媚感": 1.10, "清冷感": 0.75, "软萌感": 0.70})

    if bust is not None and waist is not None and hips is not None:
        waist_hip_ratio = waist / hips
        bust_waist_ratio = bust / waist
        if waist_hip_ratio <= 0.72:
            apply({"娇媚感": 1.55, "御姐感": 1.30, "成熟风韵": 1.20, "软萌感": 0.80})
        elif waist_hip_ratio <= 0.78:
            apply({"御姐感": 1.15, "娇媚感": 1.20, "温婉感": 1.10})
        elif waist_hip_ratio > 0.82:
            apply({"清冷感": 1.20, "冷锐感": 1.20, "娇媚感": 0.70, "御姐感": 0.85})

        if bust_waist_ratio >= 1.35:
            apply({"娇媚感": 1.40, "御姐感": 1.20, "成熟风韵": 1.15})
        elif bust_waist_ratio >= 1.22:
            apply({"御姐感": 1.10, "娇媚感": 1.10, "温婉感": 1.05})
        elif bust_waist_ratio < 1.15:
            apply({"清冷感": 1.20, "冷锐感": 1.15, "娇媚感": 0.70})

    presentation_multipliers = {
        "疲惫凌乱": {"清冷感": 1.10, "娇媚感": 0.75, "甜妹感": 0.80},
        "随意自然": {"甜妹感": 1.10, "软萌感": 1.10},
        "朴素整洁": {"温婉感": 1.25, "甜妹感": 1.05},
        "干净利落": {"冷锐感": 1.45, "清冷感": 1.20, "御姐感": 1.15},
        "精心打理": {"御姐感": 1.30, "娇媚感": 1.35},
        "精致讲究": {"御姐感": 1.40, "娇媚感": 1.45, "成熟风韵": 1.20},
    }
    apply(presentation_multipliers.get(presentation, {}))

    if "短发" in hair:
        apply({"冷锐感": 1.35, "甜妹感": 0.90})
    if "盘发" in hair:
        apply({"御姐感": 1.20, "成熟风韵": 1.20, "温婉感": 1.15})
    if "卷发" in hair:
        apply({"娇媚感": 1.25, "御姐感": 1.10})
    if "长发" in hair or "中长发" in hair:
        apply({"温婉感": 1.10, "甜妹感": 1.10, "御姐感": 1.05})

    feature_multipliers = {
        "笑起来有酒窝": {"甜妹感": 1.30, "软萌感": 1.25},
        "眼角有泪痣": {"娇媚感": 1.20, "清冷感": 1.10},
        "脸上有浅淡雀斑": {"甜妹感": 1.15, "软萌感": 1.20},
        "眉眼清秀": {"清冷感": 1.10, "温婉感": 1.10},
        "五官秀气": {"温婉感": 1.15, "甜妹感": 1.10},
        "唇形精致": {"御姐感": 1.15, "娇媚感": 1.20},
    }
    apply(feature_multipliers.get(feature, {}))

    occupation_multipliers = {
        "SEX_SERVICE_OCCUPATIONS": {
            "御姐感": 1.15, "甜妹感": 0.95, "清冷感": 0.95, "娇媚感": 1.65,
            "成熟风韵": 1.10, "冷锐感": 0.95, "软萌感": 0.75, "温婉感": 0.85,
        },
        "GREY_OCCUPATIONS": {
            "御姐感": 1.05, "甜妹感": 0.90, "清冷感": 1.10, "娇媚感": 0.95,
            "成熟风韵": 1.00, "冷锐感": 1.35, "软萌感": 0.75, "温婉感": 0.85,
        },
        "ILLEGAL_OCCUPATIONS": {
            "御姐感": 1.10, "甜妹感": 0.80, "清冷感": 1.15, "娇媚感": 0.95,
            "成熟风韵": 1.00, "冷锐感": 1.65, "软萌感": 0.60, "温婉感": 0.75,
        },
    }
    apply(occupation_multipliers.get(occupation_type, {}))

    return rng.choices(
        FEMALE_TEMPERAMENT_OPTIONS,
        weights=tuple(weights[label] for label in FEMALE_TEMPERAMENT_OPTIONS),
        k=1,
    )[0]
