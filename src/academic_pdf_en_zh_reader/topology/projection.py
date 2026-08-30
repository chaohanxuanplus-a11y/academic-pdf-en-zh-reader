# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic integer occupancy projections for page topology."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

type BoxMpt = tuple[int, int, int, int]
type IntervalMpt = tuple[int, int]


@dataclass(frozen=True, order=True)
class OccupiedBox:
    """One visible line or graphic region used by the geometry detector."""

    id: str
    kind: str
    bbox_mpt: BoxMpt
    font_size_mpt: int = 0


def _integer_box(value: object) -> BoxMpt:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or not all(type(coordinate) is int for coordinate in value)
    ):
        raise ValueError("occupancy box must contain four integer milli-points")
    x0, y0, x1, y1 = value
    if x0 >= x1 or y0 >= y1:
        raise ValueError("occupancy box must have positive area")
    return x0, y0, x1, y1


def _integer_coordinates(value: object) -> BoxMpt:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or not all(type(coordinate) is int for coordinate in value)
    ):
        raise ValueError("vector box must contain four integer milli-points")
    x0, y0, x1, y1 = value
    if x0 > x1 or y0 > y1:
        raise ValueError("vector box coordinates are reversed")
    return x0, y0, x1, y1


def _contains(outer: BoxMpt, inner: BoxMpt) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def occupied_boxes(page: Mapping[str, object]) -> tuple[OccupiedBox, ...]:
    """Select geometry facts from an already validated extraction page.

    Repeated marginals and low-confidence text inside graphics are excluded from
    the text projection. Their containing graphic region remains represented.
    No PDF parser, document metadata, or synthetic fixture truth is consulted.
    """

    selected: list[OccupiedBox] = []
    seen: set[str] = set()
    lines = page.get("lines", ())
    graphics = page.get("graphic_regions", ())
    curves = page.get("curves", ())
    if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
        raise ValueError("page lines must be a sequence")
    if not isinstance(graphics, Sequence) or isinstance(graphics, (str, bytes)):
        raise ValueError("page graphic regions must be a sequence")
    if not isinstance(curves, Sequence) or isinstance(curves, (str, bytes)):
        raise ValueError("page curves must be a sequence")

    for raw in lines:
        if not isinstance(raw, Mapping):
            raise ValueError("page line must be an object")
        if raw.get("coverage_eligible", True) is False:
            continue
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("page line must have an identifier")
        if identifier in seen:
            raise ValueError("occupancy identifiers must be unique")
        seen.add(identifier)
        font_size = raw.get("max_font_size_mpt", 0)
        if type(font_size) is not int or font_size < 0:
            raise ValueError("line font size must be a non-negative integer")
        selected.append(
            OccupiedBox(
                id=identifier,
                kind="line",
                bbox_mpt=_integer_box(raw.get("bbox_mpt")),
                font_size_mpt=font_size,
            )
        )

    graphic_boxes: list[BoxMpt] = []
    for raw in graphics:
        if not isinstance(raw, Mapping):
            raise ValueError("graphic region must be an object")
        identifier = raw.get("id")
        kind = raw.get("kind")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("graphic region must have an identifier")
        if not isinstance(kind, str) or not kind:
            raise ValueError("graphic region must have a kind")
        if identifier in seen:
            raise ValueError("occupancy identifiers must be unique")
        seen.add(identifier)
        box = _integer_box(raw.get("bbox_mpt"))
        graphic_boxes.append(box)
        selected.append(
            OccupiedBox(
                id=identifier,
                kind=f"graphic:{kind}",
                bbox_mpt=box,
            )
        )

    crop = _integer_box(page.get("crop_box_mpt"))
    minimum_rule_width = (crop[2] - crop[0]) // 2
    for raw in curves:
        if not isinstance(raw, Mapping):
            raise ValueError("page curve must be an object")
        if raw.get("source_kind") != "line":
            continue
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("page curve must have an identifier")
        raw_box = _integer_coordinates(raw.get("bbox_mpt"))
        if raw_box[2] - raw_box[0] < minimum_rule_width:
            continue
        if any(_contains(graphic, raw_box) for graphic in graphic_boxes):
            continue
        line_width = raw.get("line_width_mpt", 0)
        if type(line_width) is not int or line_width < 0:
            raise ValueError("curve line width must be a non-negative integer")
        half_height = max(500, (line_width + 1) // 2)
        bottom = max(crop[1], raw_box[1] - half_height)
        top = min(crop[3], raw_box[3] + half_height)
        if bottom >= top:
            continue
        if identifier in seen:
            raise ValueError("occupancy identifiers must be unique")
        seen.add(identifier)
        selected.append(
            OccupiedBox(
                id=identifier,
                kind="horizontal-rule",
                bbox_mpt=(raw_box[0], bottom, raw_box[2], top),
            )
        )

    return tuple(
        sorted(
            selected,
            key=lambda item: (
                -item.bbox_mpt[3],
                item.bbox_mpt[0],
                item.bbox_mpt[1],
                item.bbox_mpt[2],
                item.kind,
                item.id,
            ),
        )
    )


def merge_intervals(intervals: Sequence[IntervalMpt]) -> tuple[IntervalMpt, ...]:
    """Return the stable union of positive one-dimensional intervals."""

    normalized: list[IntervalMpt] = []
    for start, end in intervals:
        if type(start) is not int or type(end) is not int or start >= end:
            raise ValueError("projection intervals must be positive integers")
        normalized.append((start, end))
    if not normalized:
        return ()
    normalized.sort()
    merged: list[IntervalMpt] = [normalized[0]]
    for start, end in normalized[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def axis_projection(
    boxes: Sequence[OccupiedBox],
    *,
    axis: Literal["x", "y"],
) -> tuple[IntervalMpt, ...]:
    """Project occupied boxes onto one page axis and union their intervals."""

    indexes = (0, 2) if axis == "x" else (1, 3)
    return merge_intervals(
        tuple((box.bbox_mpt[indexes[0]], box.bbox_mpt[indexes[1]]) for box in boxes)
    )


def axis_gaps(
    boxes: Sequence[OccupiedBox],
    *,
    axis: Literal["x", "y"],
    minimum_mpt: int,
) -> tuple[IntervalMpt, ...]:
    """Find inner empty projection gaps, excluding unpainted outer margins."""

    if type(minimum_mpt) is not int or minimum_mpt < 0:
        raise ValueError("minimum gap must be a non-negative integer")
    occupied = axis_projection(boxes, axis=axis)
    return tuple(
        (left[1], right[0])
        for left, right in zip(occupied, occupied[1:], strict=False)
        if right[0] - left[1] >= minimum_mpt
    )


def gutter_persistence_ppm(
    boxes: Sequence[OccupiedBox],
    *,
    gutter: IntervalMpt,
    y_bottom_mpt: int,
    y_top_mpt: int,
) -> int:
    """Measure how much occupied vertical support leaves a gutter unobstructed."""

    gutter_left, gutter_right = gutter
    if gutter_left >= gutter_right or y_bottom_mpt >= y_top_mpt:
        raise ValueError("gutter and vertical range must have positive extent")

    def clipped_y(box: OccupiedBox) -> IntervalMpt | None:
        bottom = max(y_bottom_mpt, box.bbox_mpt[1])
        top = min(y_top_mpt, box.bbox_mpt[3])
        return None if bottom >= top else (bottom, top)

    all_vertical = [interval for box in boxes if (interval := clipped_y(box))]
    crossing_vertical = [
        interval
        for box in boxes
        if box.bbox_mpt[0] < gutter_right
        and box.bbox_mpt[2] > gutter_left
        and (interval := clipped_y(box))
    ]
    occupied = sum(end - start for start, end in merge_intervals(all_vertical))
    if occupied == 0:
        return 0
    blocked = sum(end - start for start, end in merge_intervals(crossing_vertical))
    return max(0, min(1_000_000, (occupied - blocked) * 1_000_000 // occupied))


def union_box(boxes: Sequence[OccupiedBox]) -> BoxMpt:
    """Return the smallest positive integer box containing every object."""

    if not boxes:
        raise ValueError("cannot union an empty occupancy collection")
    return (
        min(item.bbox_mpt[0] for item in boxes),
        min(item.bbox_mpt[1] for item in boxes),
        max(item.bbox_mpt[2] for item in boxes),
        max(item.bbox_mpt[3] for item in boxes),
    )
