# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Enumerate deterministic minimum-release windows for one ordered chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ScopeKind = Literal["column", "band"]
ReleaseStage = Literal[
    "current",
    "neighbors",
    "radius",
    "whole-column",
    "whole-band",
]


class WindowSearchError(ValueError):
    """Raised when a release-window request is malformed."""


@dataclass(frozen=True, slots=True)
class ReleaseWindow:
    """One half-open, contiguous window in deterministic attempt order."""

    stage: ReleaseStage
    start_index: int
    end_index: int
    radius: int | None
    covers_whole_scope: bool


class WindowSearchComplexityError(WindowSearchError):
    """Raised rather than jumping across unattempted local radii."""

    code = "LAYOUT_COMPLEXITY_LIMIT"

    def __init__(
        self,
        *,
        observed: int,
        maximum: int,
        partial_windows: tuple[ReleaseWindow, ...],
    ) -> None:
        self.observed = observed
        self.maximum = maximum
        self.partial_windows = partial_windows
        super().__init__(
            f"release radius {observed} exceeds configured maximum {maximum}"
        )


def _integer(value: object) -> bool:
    return type(value) is int


def enumerate_release_windows(
    *,
    pivot_index: int,
    item_count: int,
    scope_kind: ScopeKind,
    maximum_local_radius: int | None = None,
) -> tuple[ReleaseWindow, ...]:
    """Return current, neighboring, local-radius, then whole-scope windows.

    Identical half-open ranges are attempted once.  If an earlier stage already
    spans the entire chain, it retains its stage name and records that fact.
    """

    if not _integer(item_count) or item_count < 1:
        raise WindowSearchError("item_count must be a positive integer")
    if not _integer(pivot_index) or not 0 <= pivot_index < item_count:
        raise WindowSearchError("pivot_index must identify an item in the scope")
    if scope_kind not in ("column", "band"):
        raise WindowSearchError("scope_kind must be 'column' or 'band'")
    if maximum_local_radius is not None and (
        not _integer(maximum_local_radius) or maximum_local_radius < 1
    ):
        raise WindowSearchError(
            "maximum_local_radius must be a positive integer or None"
        )

    windows: list[ReleaseWindow] = []
    seen: set[tuple[int, int]] = set()

    def add(
        stage: ReleaseStage,
        start_index: int,
        end_index: int,
        radius: int | None,
    ) -> bool:
        key = (start_index, end_index)
        if key in seen:
            return start_index == 0 and end_index == item_count
        seen.add(key)
        covers_whole_scope = start_index == 0 and end_index == item_count
        windows.append(
            ReleaseWindow(
                stage=stage,
                start_index=start_index,
                end_index=end_index,
                radius=radius,
                covers_whole_scope=covers_whole_scope,
            )
        )
        return covers_whole_scope

    if add("current", pivot_index, pivot_index + 1, 0):
        return tuple(windows)
    if add(
        "neighbors",
        max(0, pivot_index - 1),
        min(item_count, pivot_index + 2),
        1,
    ):
        return tuple(windows)

    whole_stage: ReleaseStage = (
        "whole-column" if scope_kind == "column" else "whole-band"
    )
    radius = 2
    while True:
        if maximum_local_radius is not None and radius > maximum_local_radius:
            raise WindowSearchComplexityError(
                observed=radius,
                maximum=maximum_local_radius,
                partial_windows=tuple(windows),
            )
        start_index = max(0, pivot_index - radius)
        end_index = min(item_count, pivot_index + radius + 1)
        if start_index == 0 and end_index == item_count:
            add("radius", start_index, end_index, radius)
            # The conceptual whole-scope stage has the same released set.  The
            # de-duplication rule intentionally retains the earlier radius.
            add(whole_stage, 0, item_count, None)
            return tuple(windows)
        add("radius", start_index, end_index, radius)
        radius += 1
