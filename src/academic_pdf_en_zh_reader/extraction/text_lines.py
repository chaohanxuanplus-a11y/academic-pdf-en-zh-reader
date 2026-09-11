# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build deterministic text lines from the already extracted characters."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from academic_pdf_en_zh_reader.extraction.page_objects import (
    BoxMpt,
    CharacterObject,
    ColorValue,
    PageObjects,
)

_INLINE_SUPERSCRIPT_SUFFIXES = frozenset({"®", "+", "−"})
_INLINE_SUBSCRIPT_INFIXES = frozenset("0123456789")
_NUMERIC_SUPERSCRIPT = re.compile(r"[+−-]?\d{1,3}(?:/\d{1,2})?[+−-]?\Z")
_LOWERED_SUFFIX = re.compile(r"(?:\d{1,3}(?:/\d{1,2})?|[a-z]{1,3})[,;]?\Z")


@dataclass(frozen=True)
class TextLine:
    id: str
    page_number: int
    text: str
    bbox_mpt: BoxMpt
    character_ids: tuple[str, ...]
    font_names: tuple[str, ...]
    fill_colors: tuple[ColorValue, ...]
    max_font_size_mpt: int
    confidence_ppm: int = 1_000_000
    body_eligible: bool = True
    coverage_eligible: bool = True
    exclusion_kind: str | None = None
    container_kind: str | None = None
    container_id: str | None = None


@dataclass(frozen=True)
class PageTextLines:
    page_number: int
    lines: tuple[TextLine, ...]


def _vertical_cluster(
    characters: tuple[CharacterObject, ...],
) -> list[list[CharacterObject]]:
    clusters: list[list[CharacterObject]] = []
    for character in sorted(
        characters,
        key=lambda item: (-item.bbox_mpt[3], item.bbox_mpt[0], item.id),
    ):
        midpoint = (character.bbox_mpt[1] + character.bbox_mpt[3]) // 2
        best: list[CharacterObject] | None = None
        for cluster in clusters:
            anchor = cluster[0]
            anchor_midpoint = (anchor.bbox_mpt[1] + anchor.bbox_mpt[3]) // 2
            tolerance = max(
                2_000,
                min(character.font_size_mpt, anchor.font_size_mpt) // 4,
            )
            if abs(midpoint - anchor_midpoint) <= tolerance:
                best = cluster
                break
        if best is None:
            clusters.append([character])
        else:
            best.append(character)
    return clusters


def _horizontal_segments(cluster: list[CharacterObject]) -> list[list[CharacterObject]]:
    ordered = sorted(
        cluster, key=lambda item: (item.bbox_mpt[0], item.bbox_mpt[1], item.id)
    )
    segments: list[list[CharacterObject]] = []
    for character in ordered:
        if not segments:
            segments.append([character])
            continue
        previous = segments[-1][-1]
        gap = character.bbox_mpt[0] - previous.bbox_mpt[2]
        split_gap = max(
            6_000,
            max(previous.font_size_mpt, character.font_size_mpt),
        )
        if gap > split_gap:
            segments.append([character])
        else:
            segments[-1].append(character)
    return segments


def _split_math_prefixes(
    segments: list[list[CharacterObject]], chars: tuple[CharacterObject, ...]
) -> list[list[CharacterObject]]:
    """Separate prose from an adjacent display equation with its own font.

    Require a native font transition, a lexical prose prefix, a non-prose
    suffix, and a nearby visible equation operator. Inline prose variables
    without that evidence remain untouched.
    """
    operators = [c for c in chars if c.text in {"=", "→"}]
    result = []
    for segment in segments:
        split = None
        segment_ids = {c.id for c in segment}
        for index in range(1, len(segment)):
            char = segment[index]
            prefix_text = _text(segment[:index])
            if (
                char.font_name == segment[index - 1].font_name
                and not prefix_text.endswith(":")
            ) or char.text.isspace():
                continue
            if not re.search(r"[A-Za-z]{4,}", prefix_text) or re.search(
                r"[A-Za-z]{4,}", _text(segment[index:])
            ):
                continue
            if any(
                op.id not in segment_ids
                and abs(
                    (op.bbox_mpt[1] + op.bbox_mpt[3])
                    - (char.bbox_mpt[1] + char.bbox_mpt[3])
                )
                <= 30000
                and char.bbox_mpt[0]
                <= op.bbox_mpt[0]
                <= segment[-1].bbox_mpt[2] + 40000
                for op in operators
            ):
                split = index
                break
        if split is None:
            result.append(segment)
        else:
            result.extend((segment[:split], segment[split:]))
    return result


def _superscript_target(
    marker: CharacterObject,
    segments: list[list[CharacterObject]],
) -> int | None:
    best: tuple[int, int, int] | None = None
    target: int | None = None
    marker_height = marker.bbox_mpt[3] - marker.bbox_mpt[1]
    marker_center = (marker.bbox_mpt[1] + marker.bbox_mpt[3]) // 2
    for segment_index, segment in enumerate(segments):
        for character in segment:
            if marker.font_size_mpt * 10 > character.font_size_mpt * 9:
                continue
            character_height = character.bbox_mpt[3] - character.bbox_mpt[1]
            character_center = (character.bbox_mpt[1] + character.bbox_mpt[3]) // 2
            vertical_overlap = max(
                0,
                min(marker.bbox_mpt[3], character.bbox_mpt[3])
                - max(marker.bbox_mpt[1], character.bbox_mpt[1]),
            )
            if marker_center <= character_center + max(
                500, marker.font_size_mpt // 10
            ) or vertical_overlap * 2 < min(marker_height, character_height):
                continue
            gap = marker.bbox_mpt[0] - character.bbox_mpt[2]
            if not (
                -max(250, character.font_size_mpt // 20)
                <= gap
                <= max(500, character.font_size_mpt // 4)
            ):
                continue
            score = (abs(gap), -vertical_overlap, segment_index)
            if best is None or score < best:
                best = score
                target = segment_index
    return target


def _subscript_target(
    marker: CharacterObject,
    segments: list[list[CharacterObject]],
) -> int | None:
    best: tuple[int, int, int] | None = None
    target: int | None = None
    marker_height = marker.bbox_mpt[3] - marker.bbox_mpt[1]
    marker_center = (marker.bbox_mpt[1] + marker.bbox_mpt[3]) // 2
    for segment_index, segment in enumerate(segments):
        ordered = sorted(segment, key=lambda item: (item.bbox_mpt[0], item.id))
        for left, right in zip(ordered, ordered[1:], strict=False):
            if not (left.text[-1:].isalnum() and right.text[:1].isalnum()):
                continue
            baseline_size = min(left.font_size_mpt, right.font_size_mpt)
            if marker.font_size_mpt * 10 > baseline_size * 9:
                continue
            left_height = left.bbox_mpt[3] - left.bbox_mpt[1]
            right_height = right.bbox_mpt[3] - right.bbox_mpt[1]
            left_overlap = max(
                0,
                min(marker.bbox_mpt[3], left.bbox_mpt[3])
                - max(marker.bbox_mpt[1], left.bbox_mpt[1]),
            )
            right_overlap = max(
                0,
                min(marker.bbox_mpt[3], right.bbox_mpt[3])
                - max(marker.bbox_mpt[1], right.bbox_mpt[1]),
            )
            baseline_center = (
                left.bbox_mpt[1]
                + left.bbox_mpt[3]
                + right.bbox_mpt[1]
                + right.bbox_mpt[3]
            ) // 4
            if (
                marker_center >= baseline_center - max(500, marker.font_size_mpt // 10)
                or left_overlap * 2 < min(marker_height, left_height)
                or right_overlap * 2 < min(marker_height, right_height)
            ):
                continue
            left_gap = marker.bbox_mpt[0] - left.bbox_mpt[2]
            right_gap = right.bbox_mpt[0] - marker.bbox_mpt[2]
            minimum_gap = -max(250, baseline_size // 20)
            maximum_gap = max(500, baseline_size // 4)
            if not (
                minimum_gap <= left_gap <= maximum_gap
                and minimum_gap <= right_gap <= maximum_gap
            ):
                continue
            score = (abs(left_gap) + abs(right_gap), abs(left_gap), segment_index)
            if best is None or score < best:
                best = score
                target = segment_index
    return target


def _attach_inline_affixes(
    segments: list[list[CharacterObject]],
) -> list[list[CharacterObject]]:
    removed: set[int] = set()
    for marker_index, segment in enumerate(segments):
        if marker_index in removed:
            continue
        text = _text(segment)
        if len(segment) == 1:
            marker = segment[0]
        elif len(segment) <= 6 and (
            _NUMERIC_SUPERSCRIPT.fullmatch(text) or _LOWERED_SUFFIX.fullmatch(text)
        ):
            marker = replace(
                segment[0],
                text=text,
                bbox_mpt=(
                    min(c.bbox_mpt[0] for c in segment),
                    min(c.bbox_mpt[1] for c in segment),
                    max(c.bbox_mpt[2] for c in segment),
                    max(c.bbox_mpt[3] for c in segment),
                ),
                font_size_mpt=max(c.font_size_mpt for c in segment),
            )
        else:
            continue
        if (
            marker.text in _INLINE_SUPERSCRIPT_SUFFIXES
            or _NUMERIC_SUPERSCRIPT.fullmatch(text)
        ):
            target = _superscript_target(marker, segments)
            if target is None and marker.text in _INLINE_SUBSCRIPT_INFIXES:
                target = _subscript_target(marker, segments)
        elif marker.text in _INLINE_SUBSCRIPT_INFIXES:
            target = _subscript_target(marker, segments)
        else:
            target = None
        if target is None and _LOWERED_SUFFIX.fullmatch(text):
            target = _lowered_suffix_target(marker, segments)
        if target is None or target == marker_index:
            continue
        segments[target] = sorted(
            (*segments[target], *segment),
            key=lambda item: (item.bbox_mpt[0], item.bbox_mpt[1], item.id),
        )
        removed.add(marker_index)
        # A wide multi-glyph suffix can itself have caused a baseline split.
        # Rejoin only immediately adjacent segments at the same baseline.
        anchor = max(segments[target], key=lambda c: c.font_size_mpt)
        for other_index, other in enumerate(segments):
            if other_index in removed or other_index == target:
                continue
            first = other[0]
            gap = first.bbox_mpt[0] - max(c.bbox_mpt[2] for c in segments[target])
            if (
                0 <= gap <= max(500, anchor.font_size_mpt // 3)
                and abs(first.bbox_mpt[1] - anchor.bbox_mpt[1]) <= 500
                and first.font_size_mpt == anchor.font_size_mpt
            ):
                segments[target].extend(other)
                removed.add(other_index)
    return [segment for index, segment in enumerate(segments) if index not in removed]


def _lowered_suffix_target(
    marker: CharacterObject, segments: list[list[CharacterObject]]
) -> int | None:
    """Attach a bounded lowered suffix using a directly adjacent baseline glyph.

    Unlike raised footnote letters, lowered variable/chemical suffixes do not
    require another baseline letter on their right. All source glyphs remain.
    """
    choices: list[tuple[int, int]] = []
    for index, segment in enumerate(segments):
        for char in segment:
            if not (char.text[-1:].isalnum() or char.text == ")"):
                continue
            if marker.font_size_mpt * 10 > char.font_size_mpt * 8:
                continue
            gap = marker.bbox_mpt[0] - char.bbox_mpt[2]
            overlap = min(marker.bbox_mpt[3], char.bbox_mpt[3]) - max(
                marker.bbox_mpt[1], char.bbox_mpt[1]
            )
            if (
                -500 <= gap <= max(500, char.font_size_mpt // 4)
                and marker.bbox_mpt[1] + marker.bbox_mpt[3]
                < char.bbox_mpt[1] + char.bbox_mpt[3] - 1000
                and overlap * 2 >= marker.bbox_mpt[3] - marker.bbox_mpt[1]
            ):
                choices.append((abs(gap), index))
    return min(choices)[1] if choices else None


def _text(segment: list[CharacterObject]) -> str:
    pieces: list[str] = []
    previous: CharacterObject | None = None
    for character in segment:
        if previous is not None:
            gap = character.bbox_mpt[0] - previous.bbox_mpt[2]
            word_gap = max(
                800,
                3 * min(previous.font_size_mpt, character.font_size_mpt) // 20,
            )
            if gap > word_gap:
                pieces.append(" ")
        pieces.append(character.text)
        previous = character
    return "".join(pieces).strip()


def _line(page_number: int, ordinal: int, segment: list[CharacterObject]) -> TextLine:
    return TextLine(
        id=f"p{page_number:04d}-line-{ordinal:05d}",
        page_number=page_number,
        text=_text(segment),
        bbox_mpt=(
            min(item.bbox_mpt[0] for item in segment),
            min(item.bbox_mpt[1] for item in segment),
            max(item.bbox_mpt[2] for item in segment),
            max(item.bbox_mpt[3] for item in segment),
        ),
        character_ids=tuple(item.id for item in segment),
        font_names=tuple(sorted({item.font_name for item in segment})),
        fill_colors=tuple(
            sorted(
                {item.fill_color for item in segment},
                key=repr,
            )
        ),
        max_font_size_mpt=max(item.font_size_mpt for item in segment),
    )


def build_text_lines(pages: tuple[PageObjects, ...]) -> tuple[PageTextLines, ...]:
    """Group extracted characters without reopening or reparsing the PDF."""

    result: list[PageTextLines] = []
    for page in pages:
        segments = _attach_inline_affixes(
            _split_math_prefixes(
                [
                    segment
                    for cluster in _vertical_cluster(page.chars)
                    for segment in _horizontal_segments(cluster)
                    if _text(segment)
                ],
                page.chars,
            )
        )
        segments.sort(
            key=lambda segment: (
                -max(item.bbox_mpt[3] for item in segment),
                min(item.bbox_mpt[0] for item in segment),
                tuple(item.id for item in segment),
            )
        )
        lines = tuple(
            _line(page.page_number, ordinal, segment)
            for ordinal, segment in enumerate(segments, start=1)
        )
        result.append(PageTextLines(page_number=page.page_number, lines=lines))
    return tuple(result)
