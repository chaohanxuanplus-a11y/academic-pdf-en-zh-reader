# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Content-only lower geometry for mirrored right-panel bands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FlowItemMeasure:
    """One already approved item that contributes to right-side height."""

    id: str
    height_mpt: int
    gap_before_mpt: int

    def __post_init__(self) -> None:
        if not self.id or self.height_mpt <= 0 or self.gap_before_mpt < 0:
            raise ValueError("flow item measure is invalid")


def lines_height_mpt(lines: Sequence[Mapping[str, object]]) -> int:
    """Use the frozen leading of every line as its conservative flow height."""

    heights = [line.get("line_height_mpt") for line in lines]
    if not heights or any(type(height) is not int or height <= 0 for height in heights):
        raise ValueError("line flow needs positive integer heights")
    return sum(heights)  # type: ignore[arg-type]


def initial_unsplit_band_content_height(
    frame_ids: Sequence[str],
    items_by_frame: Mapping[str, Sequence[FlowItemMeasure]],
    *,
    vertical_padding_mpt: int,
) -> tuple[int, tuple[str, ...]]:
    """Measure the current unsplit home-frame assignment, not solved ``H_b``."""

    if not frame_ids or vertical_padding_mpt < 0:
        raise ValueError("band geometry inputs are invalid")
    heights: list[int] = []
    content_ids: list[str] = []
    for frame_id in frame_ids:
        items = tuple(items_by_frame.get(frame_id, ()))
        height = vertical_padding_mpt * 2
        for item in items:
            height += item.gap_before_mpt + item.height_mpt
            content_ids.append(item.id)
        heights.append(height)
    return max(heights), tuple(content_ids)
