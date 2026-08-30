# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Freeze target lines without copying English column or page breaks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.typography.cjk_breaker import break_text
from academic_pdf_en_zh_reader.typography.font_runs import (
    FontRunResolver,
    grapheme_clusters,
)
from academic_pdf_en_zh_reader.typography.style_contract import RoleStyle


class UnitPartError(ValueError):
    """Raised when target lines cannot be bound to the complete translation."""


def grapheme_boundaries(text: str) -> frozenset[int]:
    """Return the only code-point offsets safe for exact rendered spans."""

    cursor = 0
    boundaries = {0}
    for cluster in grapheme_clusters(text):
        cursor += len(cluster)
        boundaries.add(cursor)
    return frozenset(boundaries)


def _mpt_ceil(value_pt: float) -> int:
    return int(
        (Decimal(str(value_pt)) * 1000).to_integral_value(rounding=ROUND_CEILING)
    )


def _mpt_floor(value_pt: float) -> int:
    return int((Decimal(str(value_pt)) * 1000).to_integral_value(rounding=ROUND_FLOOR))


def _target_ranges(
    text: str, visible_lines: tuple[str, ...]
) -> tuple[tuple[int, int], ...]:
    """Bind visible lines to contiguous ranges that consume all source whitespace."""

    if not visible_lines:
        raise UnitPartError("a translated unit must produce at least one line")
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for index, visible in enumerate(visible_lines):
        if not visible:
            raise UnitPartError("a visible target line must not be empty")
        position = text.find(visible, cursor)
        if position < 0 or text[cursor:position].strip():
            raise UnitPartError("line text cannot be aligned to the translation")
        visible_end = position + len(visible)
        if index + 1 < len(visible_lines):
            following = visible_lines[index + 1]
            target_end = text.find(following, visible_end)
            if target_end < 0 or text[visible_end:target_end].strip():
                raise UnitPartError("line boundaries lose translated content")
        else:
            target_end = len(text)
            if text[visible_end:target_end].strip():
                raise UnitPartError("the final line does not consume the translation")
        if text[cursor:target_end].strip() != visible:
            raise UnitPartError("line range and visible text disagree")
        ranges.append((cursor, target_end))
        cursor = target_end
    if cursor != len(text):
        raise UnitPartError("target line ranges do not cover the translation")
    return tuple(ranges)


def measure_target_lines(
    text: str,
    *,
    maximum_width_mpt: int,
    resolver: FontRunResolver,
    style: RoleStyle,
    semantic_role: str,
    style_contract_version: int,
) -> tuple[dict[str, object], ...]:
    """Measure one immutable line sequence and bind complete target offsets."""

    if type(maximum_width_mpt) is not int or maximum_width_mpt <= 0:
        raise UnitPartError("maximum target width must be a positive integer")
    style_id = (
        f"typography-v{style_contract_version}:{semantic_role}:"
        f"{style.font_role}:{style.size_mpt}:{style.line_height_mpt}"
    )
    boxes = break_text(
        text,
        max_width_pt=maximum_width_mpt / 1000,
        resolver=resolver,
        font_role=style.font_role,
        size_pt=style.size_pt,
        line_height_pt=style.line_height_pt,
    )
    visible = tuple(box.text for box in boxes)
    ranges = _target_ranges(text, visible)
    result: list[dict[str, object]] = []
    for index, (box, (start, end)) in enumerate(zip(boxes, ranges, strict=True)):
        width = _mpt_ceil(box.width_pt)
        if width > maximum_width_mpt:
            raise UnitPartError("rounded target line exceeds its fixed width")
        line: dict[str, object] = {
            "index": index,
            "target_start": start,
            "target_end": end,
            "text": box.text,
            "style_id": style_id,
            "width_mpt": width,
            "line_height_mpt": _mpt_ceil(box.line_height_pt),
            "ascent_mpt": _mpt_ceil(box.ascent_pt),
            "descent_mpt": _mpt_floor(box.descent_pt),
            "runs": [
                {
                    "font_role": run.font_role,
                    "font_name": run.font_name,
                    "text": run.text,
                }
                for run in box.resolved.runs
            ],
        }
        line["line_box_hash"] = sha256_canonical(
            {"line_box_contract_version": "1.0.0", **line}
        )
        result.append(line)
    return tuple(result)


def measure_composite_target_lines(
    text: str,
    insertions: Sequence[Mapping[str, object]],
    *,
    maximum_width_mpt: int,
    resolver: FontRunResolver,
    style: RoleStyle,
    semantic_role: str,
    style_contract_version: int,
) -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    """Measure text plus frozen labels while retaining the original target axis."""

    ordered: list[tuple[int, str, str]] = []
    identifiers: set[str] = set()
    safe_offsets = grapheme_boundaries(text)
    for insertion in insertions:
        annotation_id = insertion.get("annotation_id")
        target_offset = insertion.get("target_offset")
        label = insertion.get("text")
        if (
            not isinstance(annotation_id, str)
            or not annotation_id
            or annotation_id in identifiers
            or type(target_offset) is not int
            or target_offset < 0
            or target_offset > len(text)
            or target_offset not in safe_offsets
            or not isinstance(label, str)
            or not label
        ):
            raise UnitPartError("composite insertion is invalid")
        identifiers.add(annotation_id)
        ordered.append((target_offset, annotation_id, label))
    ordered.sort(key=lambda item: (item[0], item[1]))

    segments: list[dict[str, object]] = []
    composite_parts: list[str] = []
    target_cursor = 0
    composite_cursor = 0
    for target_offset, annotation_id, label in ordered:
        if target_offset > target_cursor:
            value = text[target_cursor:target_offset]
            segments.append(
                {
                    "index": len(segments),
                    "kind": "target",
                    "composite_start": composite_cursor,
                    "composite_end": composite_cursor + len(value),
                    "target_start": target_cursor,
                    "target_end": target_offset,
                    "text": value,
                }
            )
            composite_parts.append(value)
            composite_cursor += len(value)
        segments.append(
            {
                "index": len(segments),
                "kind": "ambiguity-label",
                "composite_start": composite_cursor,
                "composite_end": composite_cursor + len(label),
                "target_offset": target_offset,
                "annotation_id": annotation_id,
                "text": label,
            }
        )
        composite_parts.append(label)
        composite_cursor += len(label)
        target_cursor = target_offset
    if target_cursor < len(text):
        value = text[target_cursor:]
        segments.append(
            {
                "index": len(segments),
                "kind": "target",
                "composite_start": composite_cursor,
                "composite_end": composite_cursor + len(value),
                "target_start": target_cursor,
                "target_end": len(text),
                "text": value,
            }
        )
        composite_parts.append(value)
    composite_text = "".join(composite_parts)
    raw_lines = measure_target_lines(
        composite_text,
        maximum_width_mpt=maximum_width_mpt,
        resolver=resolver,
        style=style,
        semantic_role=semantic_role,
        style_contract_version=style_contract_version,
    )
    lines: list[dict[str, object]] = []
    for raw in raw_lines:
        composite_start = int(raw["target_start"])
        composite_end = int(raw["target_end"])
        target_ranges: list[tuple[int, int]] = []
        synthetic_ids: list[str] = []
        synthetic_anchors: set[int] = set()
        for segment in segments:
            segment_start = int(segment["composite_start"])
            segment_end = int(segment["composite_end"])
            overlap_start = max(composite_start, segment_start)
            overlap_end = min(composite_end, segment_end)
            if overlap_start >= overlap_end:
                continue
            if segment["kind"] == "target":
                target_start = int(segment["target_start"]) + (
                    overlap_start - segment_start
                )
                target_ranges.append(
                    (target_start, target_start + overlap_end - overlap_start)
                )
            else:
                synthetic_ids.append(str(segment["annotation_id"]))
                synthetic_anchors.add(int(segment["target_offset"]))
        if target_ranges:
            target_start = target_ranges[0][0]
            target_end = target_ranges[-1][1]
        elif len(synthetic_anchors) == 1:
            target_start = target_end = next(iter(synthetic_anchors))
        else:
            raise UnitPartError("composite line cannot be rebound to target text")
        line = {
            key: value
            for key, value in raw.items()
            if key not in {"target_start", "target_end", "line_box_hash"}
        }
        line.update(
            {
                "target_start": target_start,
                "target_end": target_end,
                "composite_start": composite_start,
                "composite_end": composite_end,
                "synthetic_annotation_ids": list(dict.fromkeys(synthetic_ids)),
            }
        )
        line["line_box_hash"] = sha256_canonical(
            {"line_box_contract_version": "2.0.0", **line}
        )
        lines.append(line)
    return tuple(lines), tuple(segments)


def measure_auxiliary_lines(
    text: str,
    *,
    annotation_id: str,
    target_offset: int,
    maximum_width_mpt: int,
    resolver: FontRunResolver,
    style: RoleStyle,
    style_contract_version: int,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    """Measure auxiliary text without assigning it original-translation ranges."""

    if not annotation_id or type(target_offset) is not int or target_offset < 0:
        raise UnitPartError("auxiliary line parent binding is invalid")
    raw_lines = measure_target_lines(
        text,
        maximum_width_mpt=maximum_width_mpt,
        resolver=resolver,
        style=style,
        semantic_role="auxiliary",
        style_contract_version=style_contract_version,
    )
    lines: list[dict[str, object]] = []
    for raw in raw_lines:
        line = {
            key: value
            for key, value in raw.items()
            if key not in {"target_start", "target_end", "line_box_hash"}
        }
        line.update(
            {
                "target_start": target_offset,
                "target_end": target_offset,
                "composite_start": raw["target_start"],
                "composite_end": raw["target_end"],
                "synthetic_annotation_ids": [annotation_id],
            }
        )
        line["line_box_hash"] = sha256_canonical(
            {"line_box_contract_version": "2.0.0", **line}
        )
        lines.append(line)
    segments = (
        {
            "index": 0,
            "kind": "auxiliary-content",
            "composite_start": 0,
            "composite_end": len(text),
            "target_offset": target_offset,
            "annotation_id": annotation_id,
            "text": text,
        },
    )
    return tuple(lines), segments


def style_descriptor(
    *,
    semantic_role: str,
    style: RoleStyle,
    style_contract_version: int,
) -> dict[str, object]:
    """Return the exact fixed style record shared by a flow and its lines."""

    return {
        "style_id": (
            f"typography-v{style_contract_version}:{semantic_role}:"
            f"{style.font_role}:{style.size_mpt}:{style.line_height_mpt}"
        ),
        "semantic_role": semantic_role,
        "font_role": style.font_role,
        "size_mpt": style.size_mpt,
        "line_height_mpt": style.line_height_mpt,
    }


def legal_line_breaks(
    text: str,
    lines: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Record target line boundaries; Task 14 decides whether to use them."""

    sentence_end = frozenset("。！？!?")
    breaks: list[dict[str, object]] = []
    for line in lines[:-1]:
        target_end = line["target_end"]
        assert isinstance(target_end, int)
        prefix = text[:target_end].rstrip()
        kind = "sentence" if prefix and prefix[-1] in sentence_end else "line"
        breaks.append({"after_line": line["index"] + 1, "kind": kind})
    return tuple(breaks)


def legal_composite_line_breaks(
    text: str,
    lines: tuple[dict[str, object], ...],
    segments: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Classify composite boundaries without treating a partial label as a sentence."""

    sentence_end = frozenset("。！？!?")
    breaks: list[dict[str, object]] = []
    for line in lines[:-1]:
        composite_end = int(line["composite_end"])
        inside_label = any(
            segment["kind"] == "ambiguity-label"
            and int(segment["composite_start"])
            < composite_end
            < int(segment["composite_end"])
            for segment in segments
        )
        target_end = int(line["target_end"])
        prefix = text[:target_end].rstrip()
        kind = (
            "sentence"
            if not inside_label and prefix and prefix[-1] in sentence_end
            else "line"
        )
        breaks.append({"after_line": int(line["index"]) + 1, "kind": kind})
    return tuple(breaks)


def initial_unit_part(
    *,
    unit_id: str,
    home_frame_id: str,
    line_count: int,
    source_fragment_count: int,
) -> dict[str, object]:
    """Return the sole unsplit Task 13 part for one complete semantic unit."""

    if line_count < 1 or source_fragment_count < 1:
        raise UnitPartError("initial unit part ranges must be positive")
    return {
        "id": f"{unit_id}:part:0000",
        "unit_id": unit_id,
        "part_index": 0,
        "frame_id": home_frame_id,
        "line_start": 0,
        "line_end": line_count,
        "source_fragment_start": 0,
        "source_fragment_end": source_fragment_count,
        "is_first_part": True,
        "creates_anchor": True,
    }
