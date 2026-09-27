"""Small deterministic sampling utilities shared by generation layers."""
from __future__ import annotations
import math
import random
from collections.abc import Mapping, Sequence
from typing import TypeVar

T = TypeVar("T")


def weighted_choice(rng: random.Random, weights: Mapping[T, float]) -> T:
    choices = [(item, float(weight)) for item, weight in weights.items()]
    if any(not math.isfinite(weight) or weight < 0 for _, weight in choices):
        raise ValueError("权重必须是有限非负数")
    total = sum(weight for _, weight in choices)
    if total <= 0:
        raise ValueError("权重总和必须大于 0")
    point = rng.random() * total
    cumulative = 0.0
    for item, weight in choices:
        cumulative += weight
        if point < cumulative:
            return item
    return choices[-1][0]


def largest_remainder(total: int, weights: Mapping[T, float]) -> dict[T, int]:
    numeric_weights = {key: float(value) for key, value in weights.items()}
    if any(not math.isfinite(value) or value < 0 for value in numeric_weights.values()):
        raise ValueError("权重必须是有限非负数")
    weight_sum = sum(numeric_weights.values())
    if isinstance(total, bool) or not isinstance(total, int) or total < 0 or weight_sum <= 0:
        raise ValueError("总数和权重必须有效")
    exact = {key: total * value / weight_sum for key, value in numeric_weights.items()}
    result = {key: math.floor(value) for key, value in exact.items()}
    remainder = total - sum(result.values())
    order = sorted(exact, key=lambda key: (exact[key] - result[key]), reverse=True)
    for key in order[:remainder]:
        result[key] += 1
    return result


def bounded_lognormal(rng: random.Random, median: float, sigma: float, minimum: int, maximum: int) -> int:
    if not all(math.isfinite(float(value)) for value in (median, sigma, minimum, maximum)) or median <= 0 or sigma < 0 or minimum > maximum:
        raise ValueError("对数正态分布参数无效")
    value = rng.lognormvariate(math.log(max(median, .01)), sigma)
    return max(minimum, min(maximum, int(round(value))))


def sample_distinct(
    rng: random.Random,
    weights: Mapping[T, float],
    count: int,
    conflicts: Mapping[T, set[T]] | None = None,
) -> tuple[T, ...]:
    available = dict(weights)
    selected: list[T] = []
    while available and len(selected) < count:
        item = weighted_choice(rng, available)
        selected.append(item)
        available.pop(item, None)
        if conflicts:
            for conflict in conflicts.get(item, set()):
                available.pop(conflict, None)
    return tuple(selected)


def normalized_rank_counts(size: int, ranks: Sequence[tuple[str, float]]) -> list[tuple[str, int]]:
    if size < 1 or not ranks:
        raise ValueError("组织规模和职位配置必须非空")
    counts = largest_remainder(size, dict(ranks))
    # Every organization has exactly one top leader; very small organizations may skip middle layers.
    top_name = ranks[0][0]
    counts[top_name] = 1
    difference = sum(counts.values()) - size
    if difference > 0:
        for name, _ in reversed(ranks[1:]):
            reduction = min(difference, counts[name])
            counts[name] -= reduction
            difference -= reduction
            if difference == 0:
                break
    elif difference < 0:
        counts[ranks[-1][0]] += -difference
    return [(name, counts[name]) for name, _ in ranks if counts[name] > 0]
