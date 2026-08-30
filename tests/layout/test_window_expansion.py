# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.layout.window_search import (
    WindowSearchComplexityError,
    WindowSearchError,
    enumerate_release_windows,
)


def _summary(windows: tuple[object, ...]) -> list[tuple[object, ...]]:
    return [
        (
            window.stage,
            window.start_index,
            window.end_index,
            window.radius,
            window.covers_whole_scope,
        )
        for window in windows
    ]


def test_release_windows_expand_every_radius_until_the_whole_column() -> None:
    windows = enumerate_release_windows(
        pivot_index=5,
        item_count=11,
        scope_kind="column",
    )

    assert _summary(windows) == [
        ("current", 5, 6, 0, False),
        ("neighbors", 4, 7, 1, False),
        ("radius", 3, 8, 2, False),
        ("radius", 2, 9, 3, False),
        ("radius", 1, 10, 4, False),
        ("radius", 0, 11, 5, True),
    ]


def test_small_scope_keeps_the_earliest_stage_that_already_covers_it() -> None:
    windows = enumerate_release_windows(
        pivot_index=1,
        item_count=3,
        scope_kind="column",
    )

    assert _summary(windows) == [
        ("current", 1, 2, 0, False),
        ("neighbors", 0, 3, 1, True),
    ]


def test_the_first_radius_covering_a_band_retains_its_earlier_stage() -> None:
    windows = enumerate_release_windows(
        pivot_index=2,
        item_count=5,
        scope_kind="band",
    )

    assert _summary(windows) == [
        ("current", 2, 3, 0, False),
        ("neighbors", 1, 4, 1, False),
        ("radius", 0, 5, 2, True),
    ]


def test_edge_pivot_grows_only_the_available_side_without_duplicates() -> None:
    windows = enumerate_release_windows(
        pivot_index=0,
        item_count=7,
        scope_kind="column",
    )

    assert _summary(windows) == [
        ("current", 0, 1, 0, False),
        ("neighbors", 0, 2, 1, False),
        ("radius", 0, 3, 2, False),
        ("radius", 0, 4, 3, False),
        ("radius", 0, 5, 4, False),
        ("radius", 0, 6, 5, False),
        ("radius", 0, 7, 6, True),
    ]


def test_explicit_radius_cap_cannot_skip_unattempted_local_windows() -> None:
    with pytest.raises(WindowSearchComplexityError) as raised:
        enumerate_release_windows(
            pivot_index=5,
            item_count=11,
            scope_kind="column",
            maximum_local_radius=3,
        )

    assert raised.value.code == "LAYOUT_COMPLEXITY_LIMIT"
    assert raised.value.observed == 4
    assert raised.value.maximum == 3
    assert _summary(raised.value.partial_windows) == [
        ("current", 5, 6, 0, False),
        ("neighbors", 4, 7, 1, False),
        ("radius", 3, 8, 2, False),
        ("radius", 2, 9, 3, False),
    ]


@pytest.mark.parametrize(
    ("pivot_index", "item_count", "scope_kind", "maximum_local_radius"),
    [
        (-1, 3, "column", 2),
        (3, 3, "column", 2),
        (0, 0, "column", 2),
        (0, 3, "page", 2),
        (0, 3, [], 2),
        (0, 3, "column", 0),
        (True, 3, "column", 2),
    ],
)
def test_invalid_window_requests_fail_explicitly(
    pivot_index: object,
    item_count: object,
    scope_kind: object,
    maximum_local_radius: object,
) -> None:
    with pytest.raises(WindowSearchError):
        enumerate_release_windows(
            pivot_index=pivot_index,  # type: ignore[arg-type]
            item_count=item_count,  # type: ignore[arg-type]
            scope_kind=scope_kind,  # type: ignore[arg-type]
            maximum_local_radius=maximum_local_radius,  # type: ignore[arg-type]
        )
