# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from itertools import combinations_with_replacement, product

import pytest

from academic_pdf_en_zh_reader.layout.isotonic import (
    FrozenBounds,
    IsotonicComplexityError,
    IsotonicInfeasibleError,
    IsotonicInputError,
    IsotonicItem,
    solve_bounded_l1_isotonic,
)


def _oracle(
    preferred_levels: tuple[int, ...],
    weights: tuple[int, ...],
    *,
    lower: int,
    upper: int,
) -> tuple[tuple[int, int, tuple[int, ...]], tuple[int, ...]]:
    candidates = combinations_with_replacement(range(lower, upper + 1), len(weights))
    return min(
        (
            (
                max(
                    abs(level - preferred)
                    for level, preferred in zip(levels, preferred_levels, strict=True)
                ),
                sum(
                    weight * abs(level - preferred)
                    for level, preferred, weight in zip(
                        levels, preferred_levels, weights, strict=True
                    )
                ),
                levels,
            ),
            levels,
        )
        for levels in candidates
    )


def _direct_coordinate_oracle(
    items: tuple[IsotonicItem, ...],
    *,
    frame_top_mpt: int,
    frame_bottom_mpt: int,
    frozen: FrozenBounds,
) -> tuple[int, int, tuple[int, ...]] | None:
    """Enumerate physical top coordinates without the solver's affine reduction."""

    objectives: list[tuple[int, int, tuple[int, ...]]] = []
    for tops in product(range(frame_top_mpt, frame_bottom_mpt + 1), repeat=len(items)):
        if any(
            top + item.height_mpt > frame_bottom_mpt
            for top, item in zip(tops, items, strict=True)
        ):
            continue
        if any(
            tops[index + 1] < tops[index] + item.height_mpt + item.gap_after_mpt
            for index, item in enumerate(items[:-1])
        ):
            continue
        if (
            frozen.previous_bottom_mpt is not None
            and tops[0] < frozen.previous_bottom_mpt + frozen.gap_before_mpt
        ):
            continue
        if (
            frozen.next_top_mpt is not None
            and tops[-1] + items[-1].height_mpt + frozen.gap_after_mpt
            > frozen.next_top_mpt
        ):
            continue
        objectives.append(
            (
                max(
                    abs(top - item.preferred_top_mpt)
                    for top, item in zip(tops, items, strict=True)
                ),
                sum(
                    item.weight * abs(top - item.preferred_top_mpt)
                    for top, item in zip(tops, items, strict=True)
                ),
                tops,
            )
        )
    return min(objectives) if objectives else None


def test_integer_solver_matches_exhaustive_lexicographic_oracle() -> None:
    lower = 0
    upper = 4
    for count in range(1, 5):
        prefixes = tuple(range(count))
        for preferred_levels in product((0, 2, 4), repeat=count):
            for weights in product((1, 2), repeat=count):
                expected_objective, expected_levels = _oracle(
                    preferred_levels,
                    weights,
                    lower=lower,
                    upper=upper,
                )
                items = tuple(
                    IsotonicItem(
                        preferred_top_mpt=preferred + prefix,
                        height_mpt=1,
                        gap_after_mpt=0,
                        weight=weight,
                    )
                    for preferred, prefix, weight in zip(
                        preferred_levels, prefixes, weights, strict=True
                    )
                )

                result = solve_bounded_l1_isotonic(
                    items,
                    frame_top_mpt=lower,
                    frame_bottom_mpt=upper + count,
                    max_isotonic_items=4,
                )

                assert (
                    result.minimum_max_displacement_mpt,
                    result.weighted_l1_cost,
                    result.transformed_levels_mpt,
                ) == expected_objective
                assert result.transformed_levels_mpt == expected_levels
                assert result.tops_mpt == tuple(
                    level + prefix
                    for level, prefix in zip(expected_levels, prefixes, strict=True)
                )


@pytest.mark.parametrize(
    ("heights", "gaps", "frozen"),
    [
        ((2,), (0,), FrozenBounds()),
        ((2, 1), (1, 0), FrozenBounds(previous_bottom_mpt=0, gap_before_mpt=1)),
        ((1, 2, 1), (1, 0, 0), FrozenBounds(next_top_mpt=7, gap_after_mpt=1)),
        (
            (2, 1, 2),
            (0, 1, 0),
            FrozenBounds(
                previous_bottom_mpt=-1,
                gap_before_mpt=1,
                next_top_mpt=8,
                gap_after_mpt=1,
            ),
        ),
    ],
)
def test_solver_matches_direct_coordinate_oracle_with_gaps_and_frozen_neighbors(
    heights: tuple[int, ...],
    gaps: tuple[int, ...],
    frozen: FrozenBounds,
) -> None:
    """Keep the oracle independent of the implementation's z-coordinate math."""

    for preferred_tops in product((-2, 2, 7), repeat=len(heights)):
        for weights in product((1, 3), repeat=len(heights)):
            items = tuple(
                IsotonicItem(preferred, height, gap, weight)
                for preferred, height, gap, weight in zip(
                    preferred_tops, heights, gaps, weights, strict=True
                )
            )
            expected = _direct_coordinate_oracle(
                items,
                frame_top_mpt=-1,
                frame_bottom_mpt=8,
                frozen=frozen,
            )

            if expected is None:
                with pytest.raises(IsotonicInfeasibleError):
                    solve_bounded_l1_isotonic(
                        items,
                        frame_top_mpt=-1,
                        frame_bottom_mpt=8,
                        frozen=frozen,
                        max_isotonic_items=3,
                    )
                continue

            result = solve_bounded_l1_isotonic(
                items,
                frame_top_mpt=-1,
                frame_bottom_mpt=8,
                frozen=frozen,
                max_isotonic_items=3,
            )
            assert (
                result.minimum_max_displacement_mpt,
                result.weighted_l1_cost,
                result.tops_mpt,
            ) == expected


def test_minimum_d_bounds_a_low_weight_outlier_before_l1_optimization() -> None:
    items = (
        IsotonicItem(0, 1, weight=1000),
        IsotonicItem(101, 1, weight=1000),
        IsotonicItem(2, 1, weight=1),
    )

    result = solve_bounded_l1_isotonic(
        items,
        frame_top_mpt=-100,
        frame_bottom_mpt=203,
    )

    assert result.minimum_max_displacement_mpt == 50
    assert result.transformed_levels_mpt == (0, 50, 50)
    assert result.tops_mpt == (0, 51, 52)
    assert result.weighted_l1_cost == 50_050


def test_integer_binary_search_uses_the_exact_odd_inversion_boundary() -> None:
    result = solve_bounded_l1_isotonic(
        (
            IsotonicItem(5, 1),
            IsotonicItem(1, 1),
        ),
        frame_top_mpt=0,
        frame_bottom_mpt=6,
    )

    assert result.minimum_max_displacement_mpt == 3
    assert result.transformed_levels_mpt == (2, 2)
    assert result.tops_mpt == (2, 3)


def test_heights_gaps_and_frozen_neighbors_become_hard_common_bounds() -> None:
    items = (
        IsotonicItem(0, 10, gap_after_mpt=2, weight=2),
        IsotonicItem(8, 12, gap_after_mpt=3, weight=1),
        IsotonicItem(20, 8, weight=1),
    )
    frozen = FrozenBounds(
        previous_bottom_mpt=9,
        gap_before_mpt=4,
        next_top_mpt=56,
        gap_after_mpt=5,
    )

    result = solve_bounded_l1_isotonic(
        items,
        frame_top_mpt=0,
        frame_bottom_mpt=100,
        frozen=frozen,
    )

    assert result.lower_bound_mpt == 13
    assert result.upper_bound_mpt == 16
    assert result.tops_mpt[0] >= 13
    assert result.tops_mpt[1] >= result.tops_mpt[0] + 12
    assert result.tops_mpt[2] >= result.tops_mpt[1] + 15
    assert result.tops_mpt[-1] + items[-1].height_mpt + 5 <= 56


def test_structurally_overfull_window_is_an_explicit_infeasible_result() -> None:
    with pytest.raises(IsotonicInfeasibleError, match="does not fit"):
        solve_bounded_l1_isotonic(
            (
                IsotonicItem(0, 10, gap_after_mpt=2),
                IsotonicItem(12, 10),
            ),
            frame_top_mpt=0,
            frame_bottom_mpt=20,
        )


def test_item_limit_is_an_explicit_layout_complexity_failure() -> None:
    with pytest.raises(IsotonicComplexityError) as raised:
        solve_bounded_l1_isotonic(
            (IsotonicItem(0, 1), IsotonicItem(1, 1)),
            frame_top_mpt=0,
            frame_bottom_mpt=2,
            max_isotonic_items=1,
        )

    assert raised.value.code == "LAYOUT_COMPLEXITY_LIMIT"
    assert raised.value.observed == 2
    assert raised.value.maximum == 1


@pytest.mark.parametrize(
    "items",
    [
        (),
        (IsotonicItem(0, 0),),
        (IsotonicItem(0, 1, gap_after_mpt=-1),),
        (IsotonicItem(0, 1, weight=0),),
        (IsotonicItem(True, 1),),
    ],
)
def test_invalid_items_fail_explicitly(items: tuple[IsotonicItem, ...]) -> None:
    with pytest.raises(IsotonicInputError):
        solve_bounded_l1_isotonic(
            items,
            frame_top_mpt=0,
            frame_bottom_mpt=10,
        )


@pytest.mark.parametrize(
    ("frame_top_mpt", "frame_bottom_mpt", "frozen", "maximum"),
    [
        (0, 0, FrozenBounds(), 10),
        (10, 0, FrozenBounds(), 10),
        (True, 10, FrozenBounds(), 10),
        (0, 10, FrozenBounds(previous_bottom_mpt=1, gap_before_mpt=-1), 10),
        (0, 10, FrozenBounds(gap_before_mpt=1), 10),
        (0, 10, FrozenBounds(gap_after_mpt=1), 10),
        (0, 10, FrozenBounds(next_top_mpt=9, gap_after_mpt=-1), 10),
        (0, 10, FrozenBounds(), 0),
    ],
)
def test_invalid_boundaries_fail_explicitly(
    frame_top_mpt: object,
    frame_bottom_mpt: object,
    frozen: FrozenBounds,
    maximum: object,
) -> None:
    with pytest.raises(IsotonicInputError):
        solve_bounded_l1_isotonic(
            (IsotonicItem(0, 1),),
            frame_top_mpt=frame_top_mpt,  # type: ignore[arg-type]
            frame_bottom_mpt=frame_bottom_mpt,  # type: ignore[arg-type]
            frozen=frozen,
            max_isotonic_items=maximum,  # type: ignore[arg-type]
        )
