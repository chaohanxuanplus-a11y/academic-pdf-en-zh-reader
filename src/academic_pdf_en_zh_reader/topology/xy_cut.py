# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Small deterministic XY-cut primitives over integer occupancy boxes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from academic_pdf_en_zh_reader.topology.projection import (
    BoxMpt,
    OccupiedBox,
    axis_gaps,
    gutter_persistence_ppm,
    union_box,
)


@dataclass(frozen=True)
class CutRegion:
    bbox_mpt: BoxMpt
    object_ids: tuple[str, ...]


@dataclass(frozen=True)
class CutGap:
    start_mpt: int
    end_mpt: int
    persistence_ppm: int
    evidence_ids: tuple[str, ...]

    @property
    def width_mpt(self) -> int:
        return self.end_mpt - self.start_mpt


@dataclass(frozen=True)
class VerticalCut:
    regions: tuple[CutRegion, ...]
    gutters: tuple[CutGap, ...]
    score_ppm: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class XYCutNode:
    bbox_mpt: BoxMpt
    object_ids: tuple[str, ...]
    cut_axis: Literal["x", "y"] | None
    cut_gap_mpt: tuple[int, int] | None
    children: tuple[XYCutNode, ...] = ()


def _ordered(boxes: Sequence[OccupiedBox]) -> tuple[OccupiedBox, ...]:
    return tuple(
        sorted(
            boxes,
            key=lambda item: (
                -item.bbox_mpt[3],
                item.bbox_mpt[0],
                item.bbox_mpt[1],
                item.bbox_mpt[2],
                item.id,
            ),
        )
    )


def _region(boxes: Sequence[OccupiedBox]) -> CutRegion:
    return CutRegion(
        bbox_mpt=union_box(boxes),
        object_ids=tuple(item.id for item in _ordered(boxes)),
    )


def vertical_xy_cut(
    boxes: Sequence[OccupiedBox],
    *,
    minimum_gutter_mpt: int,
) -> VerticalCut:
    """Partition objects at every sustained vertical whitespace gutter."""

    ordered = _ordered(boxes)
    if not ordered:
        raise ValueError("vertical XY-cut requires at least one occupied box")
    content = union_box(ordered)
    gaps = axis_gaps(ordered, axis="x", minimum_mpt=minimum_gutter_mpt)
    if not gaps:
        return VerticalCut(
            regions=(_region(ordered),),
            gutters=(),
            score_ppm=650_000,
            evidence=("no-sustained-vertical-gutter",),
        )

    boundaries = [content[0], *(value for gap in gaps for value in gap), content[2]]
    horizontal_ranges = [
        (boundaries[index], boundaries[index + 1])
        for index in range(0, len(boundaries), 2)
    ]
    regions: list[CutRegion] = []
    for left, right in horizontal_ranges:
        members = tuple(
            item
            for item in ordered
            if left <= (item.bbox_mpt[0] + item.bbox_mpt[2]) // 2 <= right
        )
        if members:
            regions.append(_region(members))

    gutters = tuple(
        CutGap(
            start_mpt=start,
            end_mpt=end,
            persistence_ppm=gutter_persistence_ppm(
                ordered,
                gutter=(start, end),
                y_bottom_mpt=content[1],
                y_top_mpt=content[3],
            ),
            evidence_ids=tuple(
                item.id
                for item in ordered
                if item.bbox_mpt[2] <= start or item.bbox_mpt[0] >= end
            ),
        )
        for start, end in gaps
    )
    score = min(gap.persistence_ppm for gap in gutters)
    return VerticalCut(
        regions=tuple(regions),
        gutters=gutters,
        score_ppm=score,
        evidence=tuple(
            f"vertical-gutter:{gap.start_mpt}-{gap.end_mpt}" for gap in gutters
        ),
    )


def _split_at_gap(
    boxes: tuple[OccupiedBox, ...],
    *,
    axis: Literal["x", "y"],
    gap: tuple[int, int],
) -> tuple[tuple[OccupiedBox, ...], tuple[OccupiedBox, ...]]:
    index = 0 if axis == "x" else 1
    far_index = 2 if axis == "x" else 3
    first = tuple(item for item in boxes if item.bbox_mpt[far_index] <= gap[0])
    second = tuple(item for item in boxes if item.bbox_mpt[index] >= gap[1])
    if axis == "y":
        first, second = second, first
    return _ordered(first), _ordered(second)


def recursive_xy_cut(
    boxes: Sequence[OccupiedBox],
    *,
    minimum_x_gap_mpt: int,
    minimum_y_gap_mpt: int,
) -> XYCutNode:
    """Build a stable region tree, preferring sustained column cuts."""

    ordered = _ordered(boxes)
    if not ordered:
        raise ValueError("recursive XY-cut requires at least one occupied box")
    candidates: list[tuple[Literal["x", "y"], tuple[int, int]]] = []
    for axis, minimum in (
        ("x", minimum_x_gap_mpt),
        ("y", minimum_y_gap_mpt),
    ):
        candidates.extend(
            (axis, gap)
            for gap in axis_gaps(
                ordered,
                axis=axis,
                minimum_mpt=minimum,
            )
        )
    if not candidates:
        return XYCutNode(
            bbox_mpt=union_box(ordered),
            object_ids=tuple(item.id for item in ordered),
            cut_axis=None,
            cut_gap_mpt=None,
        )
    axis, gap = min(
        candidates,
        key=lambda item: (
            0 if item[0] == "x" else 1,
            -(item[1][1] - item[1][0]),
            item[1][0],
        ),
    )
    first, second = _split_at_gap(ordered, axis=axis, gap=gap)
    if not first or not second:
        return XYCutNode(
            bbox_mpt=union_box(ordered),
            object_ids=tuple(item.id for item in ordered),
            cut_axis=None,
            cut_gap_mpt=None,
        )
    return XYCutNode(
        bbox_mpt=union_box(ordered),
        object_ids=tuple(item.id for item in ordered),
        cut_axis=axis,
        cut_gap_mpt=gap,
        children=(
            recursive_xy_cut(
                first,
                minimum_x_gap_mpt=minimum_x_gap_mpt,
                minimum_y_gap_mpt=minimum_y_gap_mpt,
            ),
            recursive_xy_cut(
                second,
                minimum_x_gap_mpt=minimum_x_gap_mpt,
                minimum_y_gap_mpt=minimum_y_gap_mpt,
            ),
        ),
    )
