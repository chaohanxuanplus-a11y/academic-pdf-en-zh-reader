# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Exact integer placement in a top-down flow coordinate system.

All y values in this module increase from page top toward page bottom, so a valid
frame has ``frame_top_mpt < frame_bottom_mpt``.  PDF bottom-left user-space y
coordinates must be converted before they reach this mathematical boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class IsotonicError(ValueError):
    """Base class for deterministic chain-placement failures."""


class IsotonicInputError(IsotonicError):
    """Raised when the mathematical input contract is malformed."""


class IsotonicInfeasibleError(IsotonicError):
    """Raised when fixed geometry cannot contain the released chain."""


class IsotonicComplexityError(IsotonicError):
    """Raised instead of silently simplifying an over-limit problem."""

    code = "LAYOUT_COMPLEXITY_LIMIT"

    def __init__(self, *, observed: int, maximum: int) -> None:
        self.observed = observed
        self.maximum = maximum
        super().__init__(
            f"isotonic item count {observed} exceeds configured maximum {maximum}"
        )


@dataclass(frozen=True, slots=True)
class IsotonicItem:
    """One fixed-size item whose top uses top-down flow milli-points."""

    preferred_top_mpt: int
    height_mpt: int
    gap_after_mpt: int = 0
    weight: int = 1


@dataclass(frozen=True, slots=True)
class FrozenBounds:
    """Top-down bounds from immediate neighbors around a released window."""

    previous_bottom_mpt: int | None = None
    gap_before_mpt: int = 0
    next_top_mpt: int | None = None
    gap_after_mpt: int = 0


@dataclass(frozen=True, slots=True)
class IsotonicSolution:
    """Canonical pointwise-lowest solution to the lexicographic objective."""

    tops_mpt: tuple[int, ...]
    transformed_levels_mpt: tuple[int, ...]
    minimum_max_displacement_mpt: int
    weighted_l1_cost: int
    lower_bound_mpt: int
    upper_bound_mpt: int


@dataclass(slots=True)
class _Block:
    start: int
    end: int
    observations: tuple[tuple[int, int], ...]
    lower: int
    upper: int
    level: int


def _is_integer(value: object) -> bool:
    return type(value) is int


def _validate_input(
    items: Sequence[IsotonicItem],
    *,
    frame_top_mpt: int,
    frame_bottom_mpt: int,
    frozen: FrozenBounds,
    max_isotonic_items: int,
) -> None:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)) or not items:
        raise IsotonicInputError("items must be a non-empty sequence")
    if not _is_integer(max_isotonic_items) or max_isotonic_items < 1:
        raise IsotonicInputError("max_isotonic_items must be a positive integer")
    if len(items) > max_isotonic_items:
        raise IsotonicComplexityError(observed=len(items), maximum=max_isotonic_items)
    if (
        not _is_integer(frame_top_mpt)
        or not _is_integer(frame_bottom_mpt)
        or frame_top_mpt >= frame_bottom_mpt
    ):
        raise IsotonicInputError("frame bounds must be increasing integers")
    if not isinstance(frozen, FrozenBounds):
        raise IsotonicInputError("frozen must be a FrozenBounds value")

    for item in items:
        if not isinstance(item, IsotonicItem):
            raise IsotonicInputError("every item must be an IsotonicItem")
        if not _is_integer(item.preferred_top_mpt):
            raise IsotonicInputError("preferred tops must be integers")
        if not _is_integer(item.height_mpt) or item.height_mpt <= 0:
            raise IsotonicInputError("item heights must be positive integers")
        if not _is_integer(item.gap_after_mpt) or item.gap_after_mpt < 0:
            raise IsotonicInputError("item gaps must be non-negative integers")
        if not _is_integer(item.weight) or item.weight <= 0:
            raise IsotonicInputError("item weights must be positive integers")

    if frozen.previous_bottom_mpt is None:
        if frozen.gap_before_mpt != 0:
            raise IsotonicInputError("gap_before_mpt requires a previous neighbor")
    elif not _is_integer(frozen.previous_bottom_mpt):
        raise IsotonicInputError("previous frozen bottom must be an integer")
    if not _is_integer(frozen.gap_before_mpt) or frozen.gap_before_mpt < 0:
        raise IsotonicInputError("frozen preceding gap must be non-negative")

    if frozen.next_top_mpt is None:
        if frozen.gap_after_mpt != 0:
            raise IsotonicInputError("gap_after_mpt requires a next neighbor")
    elif not _is_integer(frozen.next_top_mpt):
        raise IsotonicInputError("next frozen top must be an integer")
    if not _is_integer(frozen.gap_after_mpt) or frozen.gap_after_mpt < 0:
        raise IsotonicInputError("frozen following gap must be non-negative")


def _feasible_at_displacement(
    preferred: tuple[int, ...],
    *,
    lower: int,
    upper: int,
    displacement: int,
) -> bool:
    level = lower
    for observation in preferred:
        item_lower = max(lower, observation - displacement)
        item_upper = min(upper, observation + displacement)
        level = max(level, item_lower)
        if level > item_upper:
            return False
    return True


def _lower_weighted_median(observations: tuple[tuple[int, int], ...]) -> int:
    ordered = sorted(observations)
    total_weight = sum(weight for _value, weight in ordered)
    cumulative = 0
    for value, weight in ordered:
        cumulative += weight
        if 2 * cumulative >= total_weight:
            return value
    raise AssertionError("positive weighted observations must have a median")


def _block(
    index: int,
    *,
    observation: int,
    weight: int,
    lower: int,
    upper: int,
) -> _Block:
    median = observation
    return _Block(
        start=index,
        end=index + 1,
        observations=((observation, weight),),
        lower=lower,
        upper=upper,
        level=min(max(median, lower), upper),
    )


def _merge(left: _Block, right: _Block) -> _Block:
    lower = max(left.lower, right.lower)
    upper = min(left.upper, right.upper)
    if lower > upper:
        raise IsotonicInfeasibleError(
            "bounded isotonic domains have no common ordered value"
        )
    observations = left.observations + right.observations
    median = _lower_weighted_median(observations)
    return _Block(
        start=left.start,
        end=right.end,
        observations=observations,
        lower=lower,
        upper=upper,
        level=min(max(median, lower), upper),
    )


def _bounded_weighted_l1_pava(
    preferred: tuple[int, ...],
    weights: tuple[int, ...],
    lowers: tuple[int, ...],
    uppers: tuple[int, ...],
) -> tuple[int, ...]:
    """Solve separable convex losses plus interval indicators on a chain."""

    stack: list[_Block] = []
    for index, (observation, weight, lower, upper) in enumerate(
        zip(preferred, weights, lowers, uppers, strict=True)
    ):
        stack.append(
            _block(
                index,
                observation=observation,
                weight=weight,
                lower=lower,
                upper=upper,
            )
        )
        while len(stack) >= 2 and stack[-2].level > stack[-1].level:
            right = stack.pop()
            left = stack.pop()
            stack.append(_merge(left, right))

    levels = [0] * len(preferred)
    for block in stack:
        levels[block.start : block.end] = [block.level] * (block.end - block.start)
    return tuple(levels)


def solve_bounded_l1_isotonic(
    items: Sequence[IsotonicItem],
    *,
    frame_top_mpt: int,
    frame_bottom_mpt: int,
    frozen: FrozenBounds | None = None,
    max_isotonic_items: int = 512,
) -> IsotonicSolution:
    """Place a top-down chain with exact minimax then weighted-L1 objectives.

    With ``prefix[i]`` equal to preceding fixed heights and gaps, the affine
    substitution ``z[i] = y[i] - prefix[i]`` turns minimum-separation
    constraints into a non-decreasing integer chain.  Frozen neighbors become
    common lower and upper bounds.  Integer binary search finds the globally
    smallest maximum displacement; generalized weighted-median PAVA then finds
    the pointwise-lowest weighted-L1 minimizer inside that displacement bound.
    ``frame_top_mpt`` must be numerically smaller than ``frame_bottom_mpt``;
    callers must not pass unconverted PDF bottom-left coordinates.
    """

    frozen_bounds = FrozenBounds() if frozen is None else frozen
    _validate_input(
        items,
        frame_top_mpt=frame_top_mpt,
        frame_bottom_mpt=frame_bottom_mpt,
        frozen=frozen_bounds,
        max_isotonic_items=max_isotonic_items,
    )

    prefix = [0]
    for item in items[:-1]:
        prefix.append(prefix[-1] + item.height_mpt + item.gap_after_mpt)
    total_extent = prefix[-1] + items[-1].height_mpt

    lower = frame_top_mpt
    if frozen_bounds.previous_bottom_mpt is not None:
        lower = max(
            lower,
            frozen_bounds.previous_bottom_mpt + frozen_bounds.gap_before_mpt,
        )
    last_bottom_limit = frame_bottom_mpt
    if frozen_bounds.next_top_mpt is not None:
        last_bottom_limit = min(
            last_bottom_limit,
            frozen_bounds.next_top_mpt - frozen_bounds.gap_after_mpt,
        )
    upper = last_bottom_limit - total_extent
    if lower > upper:
        raise IsotonicInfeasibleError(
            "released chain does not fit between its fixed boundaries"
        )

    preferred = tuple(
        item.preferred_top_mpt - offset
        for item, offset in zip(items, prefix, strict=True)
    )
    weights = tuple(item.weight for item in items)

    low_displacement = -1
    high_displacement = max(abs(lower - value) for value in preferred)
    while high_displacement - low_displacement > 1:
        candidate = (low_displacement + high_displacement) // 2
        if _feasible_at_displacement(
            preferred,
            lower=lower,
            upper=upper,
            displacement=candidate,
        ):
            high_displacement = candidate
        else:
            low_displacement = candidate

    displacement = high_displacement
    lowers = tuple(max(lower, value - displacement) for value in preferred)
    uppers = tuple(min(upper, value + displacement) for value in preferred)
    levels = _bounded_weighted_l1_pava(preferred, weights, lowers, uppers)

    if any(
        level < item_lower or level > item_upper
        for level, item_lower, item_upper in zip(levels, lowers, uppers, strict=True)
    ) or any(left > right for left, right in zip(levels, levels[1:], strict=False)):
        raise AssertionError("bounded PAVA returned an invalid chain")

    tops = tuple(level + offset for level, offset in zip(levels, prefix, strict=True))
    actual_displacement = max(
        abs(top - item.preferred_top_mpt) for top, item in zip(tops, items, strict=True)
    )
    if actual_displacement != displacement:
        raise AssertionError("bounded PAVA did not retain the minimum displacement")
    weighted_l1_cost = sum(
        item.weight * abs(top - item.preferred_top_mpt)
        for top, item in zip(tops, items, strict=True)
    )
    return IsotonicSolution(
        tops_mpt=tops,
        transformed_levels_mpt=levels,
        minimum_max_displacement_mpt=displacement,
        weighted_l1_cost=weighted_l1_cost,
        lower_bound_mpt=lower,
        upper_bound_mpt=upper,
    )
