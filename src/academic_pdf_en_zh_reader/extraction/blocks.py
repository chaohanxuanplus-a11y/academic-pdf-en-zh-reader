# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build only high-evidence figure, table, caption, and reference relations."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from academic_pdf_en_zh_reader.extraction.page_objects import (
    BoxMpt,
    PageObjects,
    VectorObject,
)
from academic_pdf_en_zh_reader.extraction.text_lines import PageTextLines, TextLine

_CAPTION = re.compile(r"^(Figure|Fig\.?|Table)\s+(\d+)\b", re.IGNORECASE)
_REFERENCE = re.compile(r"\b(Figure|Fig\.?|Table)\s+(\d+)\b", re.IGNORECASE)
_BARE_TABLE_LABEL = re.compile(r"^Table\s+\d+$", re.IGNORECASE)
_TABLE_NOTE_MARKER = re.compile(r"^[a-z]$", re.ASCII)
_TABLE_NOTE_TEXT = re.compile(
    r"^(?:adapted\s+from|notes?\s*:|source\s*:|bold\s*:)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GraphicRegion:
    id: str
    page_number: int
    kind: str
    bbox_mpt: BoxMpt
    evidence: str


@dataclass(frozen=True)
class CaptionRelation:
    id: str
    page_number: int
    kind: str
    number: int
    text: str
    line_ids: tuple[str, ...]
    target_id: str


@dataclass(frozen=True)
class ReferenceRelation:
    id: str
    page_number: int
    line_id: str
    label: str
    target_id: str


@dataclass(frozen=True)
class PageBasicBlocks:
    page_number: int
    lines: tuple[TextLine, ...]
    graphic_regions: tuple[GraphicRegion, ...]
    captions: tuple[CaptionRelation, ...]
    references: tuple[ReferenceRelation, ...]


@dataclass(frozen=True)
class BasicBlockDocument:
    pages: tuple[PageBasicBlocks, ...]


def _union(boxes: list[BoxMpt]) -> BoxMpt:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _contains(outer: BoxMpt, inner: BoxMpt, tolerance: int = 1_000) -> bool:
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and outer[2] + tolerance >= inner[2]
        and outer[3] + tolerance >= inner[3]
    )


def _intersects(first: BoxMpt, second: BoxMpt, tolerance: int = 1_000) -> bool:
    return not (
        first[2] + tolerance < second[0]
        or second[2] + tolerance < first[0]
        or first[3] + tolerance < second[1]
        or second[3] + tolerance < first[1]
    )


def _line_components(lines: list[VectorObject]) -> list[list[VectorObject]]:
    remaining = list(lines)
    components: list[list[VectorObject]] = []
    while remaining:
        component = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for item in remaining[:]:
                if any(
                    _intersects(item.bbox_mpt, member.bbox_mpt) for member in component
                ):
                    component.append(item)
                    remaining.remove(item)
                    changed = True
        components.append(component)
    return components


def _table_boxes(page: PageObjects) -> list[BoxMpt]:
    straight = [item for item in page.curves if item.source_kind == "line"]
    boxes: list[BoxMpt] = []
    for component in _line_components(straight):
        horizontal = [
            item
            for item in component
            if item.bbox_mpt[2] - item.bbox_mpt[0]
            > 4 * max(1, item.bbox_mpt[3] - item.bbox_mpt[1])
        ]
        vertical = [
            item
            for item in component
            if item.bbox_mpt[3] - item.bbox_mpt[1]
            > 4 * max(1, item.bbox_mpt[2] - item.bbox_mpt[0])
        ]
        if len(horizontal) >= 3 and len(vertical) >= 3:
            boxes.append(_union([item.bbox_mpt for item in component]))
    return boxes


def _horizontal_rules(page: PageObjects) -> list[VectorObject]:
    return [
        item
        for item in page.curves
        if item.source_kind == "line"
        and item.stroked
        and item.bbox_mpt[2] - item.bbox_mpt[0] >= 40_000
        and item.bbox_mpt[2] - item.bbox_mpt[0]
        > 4 * max(1, item.bbox_mpt[3] - item.bbox_mpt[1])
    ]


def _rule_y(rule: VectorObject) -> int:
    return (rule.bbox_mpt[1] + rule.bbox_mpt[3]) // 2


def _similar_rule_span(first: VectorObject, second: VectorObject) -> bool:
    first_width = first.bbox_mpt[2] - first.bbox_mpt[0]
    second_width = second.bbox_mpt[2] - second.bbox_mpt[0]
    tolerance = max(5_000, max(first_width, second_width) // 10)
    return (
        abs(first_width - second_width) <= tolerance
        and abs(first.bbox_mpt[0] - second.bbox_mpt[0]) <= tolerance
        and abs(first.bbox_mpt[2] - second.bbox_mpt[2]) <= tolerance
    )


def _rule_groups(page: PageObjects) -> list[list[VectorObject]]:
    remaining = _horizontal_rules(page)
    span_groups: list[list[VectorObject]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in remaining[:]:
                if any(_similar_rule_span(candidate, rule) for rule in group):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        span_groups.append(group)

    result: list[list[VectorObject]] = []
    page_height = page.crop_box_mpt[3] - page.crop_box_mpt[1]
    max_vertical_gap = max(120_000, page_height // 3)
    for group in span_groups:
        ordered = sorted(group, key=lambda item: (-_rule_y(item), item.id))
        vertical_group: list[VectorObject] = []
        for rule in ordered:
            if (
                vertical_group
                and _rule_y(vertical_group[-1]) - _rule_y(rule) > max_vertical_gap
            ):
                result.append(vertical_group)
                vertical_group = []
            if (
                not vertical_group
                or abs(_rule_y(vertical_group[-1]) - _rule_y(rule)) > 1_000
            ):
                vertical_group.append(rule)
        if vertical_group:
            result.append(vertical_group)
    return result


def _is_table_caption(line: TextLine) -> bool:
    match = _CAPTION.match(line.text)
    return match is not None and match.group(1).lower() == "table"


def _caption_matches_box(line: TextLine, box: BoxMpt) -> bool:
    return bool(
        _is_table_caption(line)
        and (line.bbox_mpt[1] >= box[3] or line.bbox_mpt[3] <= box[1])
        and _horizontal_overlap(line.bbox_mpt, box)
        >= min(
            line.bbox_mpt[2] - line.bbox_mpt[0],
            box[2] - box[0],
        )
        // 4
        and _vertical_distance(line.bbox_mpt, box) <= 50_000
    )


def _aligned_cell_lines(
    rules: list[VectorObject],
    lines: tuple[TextLine, ...],
) -> list[TextLine]:
    rule_box = _union([rule.bbox_mpt for rule in rules])
    rule_levels = sorted(_rule_y(rule) for rule in rules)
    cells: list[tuple[TextLine, int]] = []
    for line in lines:
        if _CAPTION.match(line.text) is not None:
            continue
        if not _contains(rule_box, line.bbox_mpt, tolerance=3_000):
            continue
        midpoint = (line.bbox_mpt[1] + line.bbox_mpt[3]) // 2
        interval = next(
            (
                index
                for index, (bottom, top) in enumerate(
                    zip(rule_levels, rule_levels[1:], strict=False)
                )
                if bottom < midpoint < top
            ),
            None,
        )
        if interval is not None:
            cells.append((line, interval))
    if len({interval for _line, interval in cells}) < 2:
        return []

    candidate_column_spans: list[tuple[int, int]] = []
    for index, (first, first_interval) in enumerate(cells):
        for second, second_interval in cells[index + 1 :]:
            if first_interval == second_interval:
                continue
            left_aligned = abs(first.bbox_mpt[0] - second.bbox_mpt[0]) <= 20_000
            right_aligned = abs(first.bbox_mpt[2] - second.bbox_mpt[2]) <= 20_000
            if left_aligned or right_aligned:
                candidate_column_spans.append(
                    (
                        min(first.bbox_mpt[0], second.bbox_mpt[0]),
                        max(first.bbox_mpt[2], second.bbox_mpt[2]),
                    )
                )

    column_clusters: list[tuple[int, int]] = []
    for left, right in sorted(candidate_column_spans):
        if column_clusters and left <= column_clusters[-1][1] + 8_000:
            previous_left, previous_right = column_clusters[-1]
            column_clusters[-1] = (previous_left, max(previous_right, right))
        else:
            column_clusters.append((left, right))
    return [line for line, _interval in cells] if len(column_clusters) >= 2 else []


def _captioned_rule_table_boxes(
    page: PageObjects,
    lines: tuple[TextLine, ...],
) -> list[BoxMpt]:
    captions = [line for line in lines if _is_table_caption(line)]
    boxes: list[BoxMpt] = []
    for rules in _rule_groups(page):
        if len(rules) < 3:
            continue
        rule_box = _union([rule.bbox_mpt for rule in rules])
        if not any(_caption_matches_box(caption, rule_box) for caption in captions):
            continue
        cells = _aligned_cell_lines(rules, lines)
        if not cells:
            continue
        box = _union([rule_box, *(line.bbox_mpt for line in cells)])
        if box not in boxes:
            boxes.append(box)
    return boxes


def _graphic_regions(
    page: PageObjects,
    lines: tuple[TextLine, ...],
) -> tuple[GraphicRegion, ...]:
    grid_boxes = _table_boxes(page)
    candidates: list[tuple[str, BoxMpt, str]] = [
        ("table", box, "intersecting-grid") for box in grid_boxes
    ]
    for box in _captioned_rule_table_boxes(page, lines):
        if not any(
            _contains(existing, box) or _contains(box, existing)
            for existing in grid_boxes
        ):
            candidates.append(("table", box, "captioned-three-rule-table"))
    table_boxes = [box for kind, box, _evidence in candidates if kind == "table"]
    for image in page.images:
        candidates.append(("figure", image.bbox_mpt, "embedded-image"))
    for rectangle in page.rectangles:
        contained = [
            curve
            for curve in page.curves
            if _contains(rectangle.bbox_mpt, curve.bbox_mpt)
        ]
        if len(contained) >= 2 and not any(
            _contains(rectangle.bbox_mpt, table_box) for table_box in table_boxes
        ):
            candidates.append(("figure", rectangle.bbox_mpt, "enclosed-vector-drawing"))
    candidates.sort(key=lambda item: (-item[1][3], item[1][0], item[0], item[1]))
    return tuple(
        GraphicRegion(
            id=f"p{page.page_number:04d}-{kind}-{ordinal:04d}",
            page_number=page.page_number,
            kind=kind,
            bbox_mpt=box,
            evidence=evidence,
        )
        for ordinal, (kind, box, evidence) in enumerate(candidates, start=1)
    )


def _horizontal_overlap(first: BoxMpt, second: BoxMpt) -> int:
    return max(0, min(first[2], second[2]) - max(first[0], second[0]))


def _vertical_distance(first: BoxMpt, second: BoxMpt) -> int:
    if first[3] < second[1]:
        return second[1] - first[3]
    if second[3] < first[1]:
        return first[1] - second[3]
    return 0


def _has_caption_continuation_evidence(
    current: TextLine,
    candidate: TextLine,
) -> bool:
    """Keep only lexical continuations when geometry cannot separate body prose."""

    text = candidate.text.lstrip()
    if not text:
        return False
    first = text[0]
    if first.islower() or first.isdigit() or first in "([{,.;:–—-":
        return True
    return current.text.rstrip().endswith(("-", "–", "—", "/", "(", "["))


def _is_bare_table_title_continuation(
    label: TextLine,
    current: TextLine,
    candidate: TextLine,
    target: GraphicRegion,
) -> bool:
    """Accept one uppercase title line after a bare label, above the table."""

    text = candidate.text.lstrip()
    return bool(
        current.id == label.id
        and _BARE_TABLE_LABEL.fullmatch(label.text.strip()) is not None
        and text
        and text[0].isupper()
        and candidate.bbox_mpt[1] >= target.bbox_mpt[3]
    )


def _captions(
    page_number: int,
    lines: tuple[TextLine, ...],
    regions: tuple[GraphicRegion, ...],
) -> tuple[CaptionRelation, ...]:
    result: list[CaptionRelation] = []
    claimed_line_ids: set[str] = set()
    ordered_lines = sorted(
        lines,
        key=lambda item: (-item.bbox_mpt[3], item.bbox_mpt[0], item.id),
    )
    for line in ordered_lines:
        if line.id in claimed_line_ids:
            continue
        if line.container_id is not None:
            continue
        match = _CAPTION.match(line.text)
        if match is None:
            continue
        kind = "table" if match.group(1).lower() == "table" else "figure"
        number = int(match.group(2))
        compatible = [
            region
            for region in regions
            if region.kind == kind
            and _horizontal_overlap(line.bbox_mpt, region.bbox_mpt)
            >= min(
                line.bbox_mpt[2] - line.bbox_mpt[0],
                region.bbox_mpt[2] - region.bbox_mpt[0],
            )
            // 4
            and _vertical_distance(line.bbox_mpt, region.bbox_mpt) <= 50_000
        ]
        if not compatible:
            continue
        target = min(
            compatible,
            key=lambda region: (
                _vertical_distance(line.bbox_mpt, region.bbox_mpt),
                -_horizontal_overlap(line.bbox_mpt, region.bbox_mpt),
                region.id,
            ),
        )
        caption_lines = [line]
        claimed_line_ids.add(line.id)
        current = line
        while True:
            continuation = [
                candidate
                for candidate in ordered_lines
                if candidate.id not in claimed_line_ids
                and -1_000
                <= current.bbox_mpt[1] - candidate.bbox_mpt[3]
                <= max(4_000, current.max_font_size_mpt)
                and abs(candidate.bbox_mpt[0] - line.bbox_mpt[0]) <= 3_000
                and _horizontal_overlap(candidate.bbox_mpt, target.bbox_mpt) > 0
                and _CAPTION.match(candidate.text) is None
                and candidate.container_id is None
                and (
                    _has_caption_continuation_evidence(current, candidate)
                    or _is_bare_table_title_continuation(
                        line,
                        current,
                        candidate,
                        target,
                    )
                )
            ]
            selected = min(
                continuation,
                key=lambda item: (-item.bbox_mpt[3], item.bbox_mpt[0], item.id),
                default=None,
            )
            if selected is None:
                break
            caption_lines.append(selected)
            claimed_line_ids.add(selected.id)
            current = selected
        result.append(
            CaptionRelation(
                id=f"p{page_number:04d}-{kind}-caption-{number:04d}",
                page_number=page_number,
                kind=kind,
                number=number,
                text=" ".join(item.text for item in caption_lines),
                line_ids=tuple(item.id for item in caption_lines),
                target_id=target.id,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.number, item.kind, item.id)))


def _raised_note_suffix(marker: TextLine, anchor: TextLine) -> bool:
    marker_height = marker.bbox_mpt[3] - marker.bbox_mpt[1]
    vertical_overlap = max(
        0,
        min(marker.bbox_mpt[3], anchor.bbox_mpt[3])
        - max(marker.bbox_mpt[1], anchor.bbox_mpt[1]),
    )
    marker_center = marker.bbox_mpt[1] + marker.bbox_mpt[3]
    anchor_center = anchor.bbox_mpt[1] + anchor.bbox_mpt[3]
    horizontal_gap = marker.bbox_mpt[0] - anchor.bbox_mpt[2]
    return bool(
        marker.max_font_size_mpt * 10 <= anchor.max_font_size_mpt * 8
        and marker_center > anchor_center
        and vertical_overlap * 2 >= marker_height
        and -1_000 <= horizontal_gap <= max(2_000, anchor.max_font_size_mpt // 2)
    )


def _mark_table_notes(
    lines: tuple[TextLine, ...],
    regions: tuple[GraphicRegion, ...],
    captions: tuple[CaptionRelation, ...],
) -> tuple[TextLine, ...]:
    by_id = {line.id: line for line in lines}
    caption_line_ids = {line_id for caption in captions for line_id in caption.line_ids}
    excluded: set[str] = set()
    markers = [
        line
        for line in lines
        if line.id not in caption_line_ids
        and line.container_id is None
        and line.exclusion_kind is None
        and _TABLE_NOTE_MARKER.fullmatch(line.text.strip()) is not None
    ]

    for caption in captions:
        if caption.kind != "table":
            continue
        anchors = [by_id[line_id] for line_id in caption.line_ids]
        excluded.update(
            marker.id
            for marker in markers
            if any(_raised_note_suffix(marker, anchor) for anchor in anchors)
        )

    note_lines = [
        line
        for line in lines
        if line.container_id is None
        and line.exclusion_kind is None
        and _TABLE_NOTE_TEXT.match(line.text.strip()) is not None
    ]
    caption_font_by_target = {
        caption.target_id: max(
            by_id[line_id].max_font_size_mpt for line_id in caption.line_ids
        )
        for caption in captions
        if caption.kind == "table"
    }
    for region in regions:
        if region.kind != "table":
            continue
        caption_font = caption_font_by_target.get(region.id)
        if caption_font is None:
            continue
        direct_notes = [
            note
            for note in note_lines
            if note.max_font_size_mpt <= caption_font * 11 // 10
            and 0
            <= region.bbox_mpt[1] - note.bbox_mpt[3]
            <= max(5_000, note.max_font_size_mpt)
            and _horizontal_overlap(note.bbox_mpt, region.bbox_mpt) > 0
        ]
        excluded.update(note.id for note in direct_notes)
        marker_anchor_bottoms = [
            region.bbox_mpt[1],
            *(note.bbox_mpt[1] for note in direct_notes),
        ]
        for marker in markers:
            if not (
                region.bbox_mpt[0] - 1_000 <= marker.bbox_mpt[0]
                and marker.bbox_mpt[2] <= region.bbox_mpt[2] + 1_000
                and any(
                    0
                    <= anchor_bottom - marker.bbox_mpt[3]
                    <= max(5_000, marker.max_font_size_mpt)
                    for anchor_bottom in marker_anchor_bottoms
                )
            ):
                continue
            for note in note_lines:
                horizontal_gap = note.bbox_mpt[0] - marker.bbox_mpt[2]
                if (
                    marker.max_font_size_mpt * 10 <= note.max_font_size_mpt * 8
                    and note.bbox_mpt[3] <= marker.bbox_mpt[3]
                    and -1_000 <= horizontal_gap <= max(5_000, note.max_font_size_mpt)
                    and _vertical_distance(marker.bbox_mpt, note.bbox_mpt)
                    <= marker.max_font_size_mpt
                ):
                    excluded.update((marker.id, note.id))

    return tuple(
        replace(
            line,
            body_eligible=False,
            coverage_eligible=False,
            exclusion_kind="footnote",
        )
        if line.id in excluded
        else line
        for line in lines
    )


def _mark_internal_lines(
    lines: tuple[TextLine, ...], regions: tuple[GraphicRegion, ...]
) -> tuple[TextLine, ...]:
    marked: list[TextLine] = []
    for line in lines:
        containing = [
            region for region in regions if _contains(region.bbox_mpt, line.bbox_mpt)
        ]
        if containing:
            region = min(
                containing,
                key=lambda item: (
                    (item.bbox_mpt[2] - item.bbox_mpt[0])
                    * (item.bbox_mpt[3] - item.bbox_mpt[1]),
                    item.id,
                ),
            )
            marked.append(
                replace(
                    line,
                    confidence_ppm=400_000,
                    body_eligible=False,
                    coverage_eligible=False,
                    container_kind=region.kind,
                    container_id=region.id,
                )
            )
        else:
            marked.append(line)
    return tuple(marked)


def _references(
    page_number: int,
    lines: tuple[TextLine, ...],
    caption_line_ids: set[str],
    targets: dict[tuple[str, int], str],
) -> tuple[ReferenceRelation, ...]:
    result: list[ReferenceRelation] = []
    ordinal = 0
    for line in lines:
        if line.id in caption_line_ids or line.container_id is not None:
            continue
        for match in _REFERENCE.finditer(line.text):
            kind = "table" if match.group(1).lower() == "table" else "figure"
            number = int(match.group(2))
            target = targets.get((kind, number))
            if target is None:
                continue
            ordinal += 1
            result.append(
                ReferenceRelation(
                    id=f"p{page_number:04d}-reference-{ordinal:04d}",
                    page_number=page_number,
                    line_id=line.id,
                    label=f"{kind.title()} {number}",
                    target_id=target,
                )
            )
    return tuple(result)


def build_basic_blocks(
    pages: tuple[PageObjects, ...],
    line_pages: tuple[PageTextLines, ...],
) -> BasicBlockDocument:
    """Create conservative relations without assigning final semantic roles."""

    if [page.page_number for page in pages] != [
        page.page_number for page in line_pages
    ]:
        raise ValueError("page objects and text lines must have matching pages")
    prepared: list[
        tuple[
            PageObjects,
            tuple[TextLine, ...],
            tuple[GraphicRegion, ...],
            tuple[CaptionRelation, ...],
        ]
    ] = []
    for page, line_page in zip(pages, line_pages, strict=True):
        regions = _graphic_regions(page, line_page.lines)
        lines = _mark_internal_lines(line_page.lines, regions)
        captions = _captions(page.page_number, lines, regions)
        lines = _mark_table_notes(lines, regions, captions)
        caption_line_ids = {
            line_id for caption in captions for line_id in caption.line_ids
        }
        lines = tuple(
            replace(line, body_eligible=False) if line.id in caption_line_ids else line
            for line in lines
        )
        prepared.append((page, lines, regions, captions))

    target_candidates: dict[tuple[str, int], set[str]] = {}
    for _page, _lines, _regions, captions in prepared:
        for caption in captions:
            target_candidates.setdefault((caption.kind, caption.number), set()).add(
                caption.target_id
            )
    targets = {
        key: next(iter(values))
        for key, values in target_candidates.items()
        if len(values) == 1
    }

    result: list[PageBasicBlocks] = []
    for page, lines, regions, captions in prepared:
        caption_line_ids = {
            line_id for caption in captions for line_id in caption.line_ids
        }
        result.append(
            PageBasicBlocks(
                page_number=page.page_number,
                lines=lines,
                graphic_regions=regions,
                captions=captions,
                references=_references(
                    page.page_number,
                    lines,
                    caption_line_ids,
                    targets,
                ),
            )
        )
    return BasicBlockDocument(pages=tuple(result))
