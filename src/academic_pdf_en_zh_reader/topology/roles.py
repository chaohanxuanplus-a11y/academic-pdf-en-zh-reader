# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Classify extracted lines into deterministic same-column role blocks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import median

from academic_pdf_en_zh_reader.topology.bands import (
    BandGeometry,
    ColumnGeometry,
    PageBands,
)
from academic_pdf_en_zh_reader.topology.contracts import TRANSLATABLE_ROLES
from academic_pdf_en_zh_reader.topology.projection import BoxMpt

_KEYWORDS = re.compile(r"^\s*(?:key\s*words?|keywords?)\s*[:—-]", re.IGNORECASE)
_NUMBERED_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.|[IVXLCDM]+[.)])\s+\S",
    re.IGNORECASE,
)
_ABSTRACT_LABEL = re.compile(r"^\s*abstract\s*$", re.IGNORECASE)
_ABSTRACT_INLINE = re.compile(r"^\s*abstract\s*[:—-]\s*\S", re.IGNORECASE)
_ACKNOWLEDGEMENTS_LABEL = re.compile(
    r"^\s*acknowledg(?:e)?ments?\s*$",
    re.IGNORECASE,
)
_REFERENCES_LABEL = re.compile(r"^\s*references\s*$", re.IGNORECASE)
_AFFILIATION_START = re.compile(
    r"^\s*(?:department|faculty|school|institute|centre|center|laboratory|"
    r"university|hospital)\b",
    re.IGNORECASE,
)
_AFFILIATION_MARKER = re.compile(r"^\s*[a-z](?:\s*,\s*[a-z])*\s*$", re.IGNORECASE)
_FOOTNOTE_MARKER = re.compile(r"^\s*[∗*†‡]\s*$")
_CORRESPONDING_AUTHOR = re.compile(r"^\s*corresponding\s+author\b", re.IGNORECASE)
_EQUAL_CONTRIBUTION = re.compile(
    r"^\s*\d+\s+all\s+authors\s+contributed\b",
    re.IGNORECASE,
)
_BIBLIOGRAPHIC_METADATA = re.compile(
    r"^\s*(?:doi\s*:|\d{4}-\d{3}[\dXx]/|©\s*\d{4}\b)",
    re.IGNORECASE,
)
_ADJACENT_GLYPH_OVERLAP_TOLERANCE_MPT = 1_000
_BODY_INDENT_TOLERANCE_MPT = 10_000

_EXCLUSION_ROLES = {
    "repeated-header": "header",
    "repeated-footer": "footer",
    "page-number": "page-number",
    "watermark": "watermark",
    "header": "header",
    "footer": "footer",
    "footnote": "footnote",
    "endnote": "endnote",
    "bibliographic-metadata": "bibliographic-metadata",
    "acknowledgements": "acknowledgements",
    "equation": "equation",
    "variable": "variable",
    "code": "code",
    "chemical-formula": "chemical-formula",
    "pure-data": "pure-data",
    "reference-entry": "reference-entry",
}


@dataclass(frozen=True)
class RoleBlock:
    """One stable semantic block produced solely from extracted page evidence."""

    id: str
    page_number: int
    role: str
    translation_policy: str
    text: str
    bbox_mpt: BoxMpt
    first_line_bbox_mpt: BoxMpt
    line_ids: tuple[str, ...]
    band_id: str
    column_id: str
    band_index: int
    column_index: int
    confidence_ppm: int
    source_ordinal: int
    max_font_size_mpt: int
    target_graphic_id: str | None = None


@dataclass(frozen=True)
class _Unit:
    role: str
    text: str
    bbox_mpt: BoxMpt
    first_line_bbox_mpt: BoxMpt
    line_ids: tuple[str, ...]
    band: BandGeometry
    column: ColumnGeometry
    confidence_ppm: int
    target_graphic_id: str | None = None
    max_font_size_mpt: int = 0


def _box(raw: object) -> BoxMpt:
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 4
        or not all(type(value) is int for value in raw)
    ):
        raise ValueError("line box must contain four integer milli-points")
    x0, y0, x1, y1 = raw
    if x0 >= x1 or y0 >= y1:
        raise ValueError("line box must have positive area")
    return x0, y0, x1, y1


def _union(boxes: Sequence[BoxMpt]) -> BoxMpt:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _overlap(start: int, end: int, other_start: int, other_end: int) -> int:
    return max(0, min(end, other_end) - max(start, other_start))


def _assignment(
    bbox: BoxMpt,
    page_bands: PageBands,
) -> tuple[BandGeometry, ColumnGeometry]:
    band = max(
        page_bands.bands,
        key=lambda item: (
            _overlap(bbox[1], bbox[3], item.y_bottom_mpt, item.y_top_mpt),
            -abs((bbox[1] + bbox[3]) - (item.y_bottom_mpt + item.y_top_mpt)),
            -item.index,
        ),
    )
    column = max(
        band.columns,
        key=lambda item: (
            _overlap(bbox[0], bbox[2], item.x_left_mpt, item.x_right_mpt),
            -abs((bbox[0] + bbox[2]) - (item.x_left_mpt + item.x_right_mpt)),
            -item.index,
        ),
    )
    return band, column


def _line_sort_key(
    line: Mapping[str, object],
    page_bands: PageBands,
) -> tuple[int, int, int, int, str]:
    bbox = _box(line.get("bbox_mpt"))
    band, column = _assignment(bbox, page_bands)
    identifier = line.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("line must have an identifier")
    return band.index, column.index, -bbox[3], bbox[0], identifier


def _confidence(line: Mapping[str, object], cap: int) -> int:
    value = line.get("confidence_ppm", 1_000_000)
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise ValueError("line confidence must be integer parts per million")
    return min(value, cap)


def _font_size(line: Mapping[str, object]) -> int:
    value = line.get("max_font_size_mpt", 0)
    if type(value) is not int or value < 0:
        raise ValueError("line font size must be a non-negative integer")
    return value


def _is_bold(line: Mapping[str, object]) -> bool:
    names = line.get("font_names", ())
    return bool(
        isinstance(names, Sequence)
        and not isinstance(names, (str, bytes))
        and any("bold" in str(name).casefold() for name in names)
    )


def _is_emphasized(line: Mapping[str, object], typical_font_mpt: int) -> bool:
    return _is_bold(line) and _font_size(line) >= typical_font_mpt * 11 // 10


def _continues_heading(
    previous: Mapping[str, object] | None,
    current: Mapping[str, object],
    roles: Mapping[str, str],
    typical_font_mpt: int,
) -> bool:
    if previous is None or roles.get(str(previous.get("id"))) != "heading":
        return False
    text = str(current.get("text", "")).strip()
    if (
        not text
        or _NUMBERED_HEADING.match(text)
        or _ABSTRACT_LABEL.fullmatch(text)
        or _REFERENCES_LABEL.fullmatch(text)
        or not _is_bold(current)
        or _font_size(current) < typical_font_mpt * 9 // 10
    ):
        return False
    previous_box = _box(previous.get("bbox_mpt"))
    current_box = _box(current.get("bbox_mpt"))
    gap = previous_box[1] - current_box[3]
    font_size = max(_font_size(previous), _font_size(current))
    return 0 <= gap <= max(5_000, font_size) and abs(
        previous_box[0] - current_box[0]
    ) <= max(2_000, font_size // 2)


def _plain_roles(
    lines: Sequence[Mapping[str, object]],
    *,
    page_number: int,
    title_ids: set[str],
    typical_font_mpt: int,
) -> dict[str, str]:
    """Classify ordinary lines, keeping abstract state local to each column."""

    roles: dict[str, str] = {}
    abstract_active = False
    title_seen = False
    front_matter_open = bool(title_ids.intersection(str(line["id"]) for line in lines))
    affiliation_active = False
    footnote_active = False
    previous: Mapping[str, object] | None = None
    for index, line in enumerate(lines):
        identifier = str(line["id"])
        text = str(line.get("text", "")).strip()
        if (
            line.get("exclusion_kind") is not None
            or line.get("container_kind") is not None
        ):
            continue
        if identifier in title_ids:
            roles[identifier] = "title"
            title_seen = True
            previous = line
            continue
        if page_number == 1 and front_matter_open and not title_seen:
            roles[identifier] = "bibliographic-metadata"
            previous = line
            continue
        if page_number == 1 and _BIBLIOGRAPHIC_METADATA.match(text):
            roles[identifier] = "bibliographic-metadata"
            footnote_active = False
            previous = line
            continue
        if page_number == 1 and (
            footnote_active
            or (
                not front_matter_open
                and (
                    _FOOTNOTE_MARKER.fullmatch(text)
                    or _CORRESPONDING_AUTHOR.match(text)
                    or _EQUAL_CONTRIBUTION.match(text)
                )
            )
        ):
            roles[identifier] = "footnote"
            footnote_active = True
            previous = line
            continue
        if _ABSTRACT_LABEL.fullmatch(text):
            roles[identifier] = "heading"
            abstract_active = True
            front_matter_open = False
            affiliation_active = False
            previous = line
            continue
        if _ABSTRACT_INLINE.match(text):
            roles[identifier] = "abstract"
            abstract_active = True
            front_matter_open = False
            affiliation_active = False
            previous = line
            continue
        if _KEYWORDS.match(text):
            roles[identifier] = "keywords"
            abstract_active = False
            front_matter_open = False
            previous = line
            continue
        if _continues_heading(previous, line, roles, typical_font_mpt):
            roles[identifier] = "heading"
            abstract_active = False
            front_matter_open = False
            previous = line
            continue
        if _NUMBERED_HEADING.match(text) or _is_emphasized(line, typical_font_mpt):
            roles[identifier] = "heading"
            abstract_active = False
            front_matter_open = False
            previous = line
            continue
        if abstract_active:
            roles[identifier] = "abstract"
        elif title_seen and front_matter_open:
            following_text = (
                str(lines[index + 1].get("text", "")).strip()
                if index + 1 < len(lines)
                else ""
            )
            if (
                affiliation_active
                or _AFFILIATION_START.match(text)
                or (
                    _AFFILIATION_MARKER.fullmatch(text)
                    and _AFFILIATION_START.match(following_text)
                )
            ):
                roles[identifier] = "affiliation"
                affiliation_active = True
            else:
                roles[identifier] = "author"
        else:
            roles[identifier] = "body"
        previous = line
    return roles


def _title_ids(
    lines: Sequence[Mapping[str, object]],
    *,
    page_number: int,
) -> set[str]:
    if page_number != 1:
        return set()
    eligible = [
        line
        for line in lines
        if line.get("exclusion_kind") is None and line.get("container_kind") is None
    ]
    maximum_font = max((_font_size(line) for line in eligible), default=0)
    if not eligible or not maximum_font:
        return set()
    maximum_lines = [line for line in eligible if _font_size(line) == maximum_font]
    anchor_top = max(_box(line.get("bbox_mpt"))[3] for line in maximum_lines)
    return {
        str(line["id"])
        for line in eligible
        if _font_size(line) * 10 >= maximum_font * 9
        and abs(anchor_top - _box(line.get("bbox_mpt"))[3]) <= maximum_font * 5 // 2
    }


def _unit_from_line(
    line: Mapping[str, object],
    *,
    role: str,
    page_bands: PageBands,
    target_graphic_id: str | None = None,
) -> _Unit:
    bbox = _box(line.get("bbox_mpt"))
    band, column = _assignment(bbox, page_bands)
    identifier = line.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("line must have an identifier")
    text = line.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("line must have non-empty text")
    caps = {
        "title": 950_000,
        "author": 850_000,
        "heading": 950_000,
        "abstract": 900_000,
        "body": 850_000,
    }
    return _Unit(
        role=role,
        text=text.strip(),
        bbox_mpt=bbox,
        first_line_bbox_mpt=bbox,
        line_ids=(identifier,),
        band=band,
        column=column,
        confidence_ppm=_confidence(line, caps.get(role, 1_000_000)),
        target_graphic_id=target_graphic_id,
        max_font_size_mpt=_font_size(line),
    )


def _can_merge(previous: _Unit, current: _Unit) -> bool:
    if previous.role not in {
        "title",
        "author",
        "affiliation",
        "heading",
        "abstract",
        "body",
        "keywords",
    }:
        return False
    if (
        previous.role != current.role
        or previous.band.id != current.band.id
        or previous.column.id != current.column.id
        or previous.target_graphic_id != current.target_graphic_id
    ):
        return False
    gap = previous.bbox_mpt[1] - current.bbox_mpt[3]
    threshold = max(
        5_000,
        max(previous.max_font_size_mpt, current.max_font_size_mpt) * 3 // 2,
    )
    if not -_ADJACENT_GLYPH_OVERLAP_TOLERANCE_MPT <= gap <= threshold:
        return False
    if previous.role == "heading":
        if _NUMBERED_HEADING.match(current.text):
            return False
        return abs(previous.bbox_mpt[0] - current.bbox_mpt[0]) <= max(
            2_000,
            max(previous.max_font_size_mpt, current.max_font_size_mpt) // 2,
        )
    return not (
        previous.role == "body"
        and current.first_line_bbox_mpt[0] - current.column.x_left_mpt
        > _BODY_INDENT_TOLERANCE_MPT
    )


def _merge_units(units: Sequence[_Unit]) -> tuple[_Unit, ...]:
    merged: list[_Unit] = []
    for unit in units:
        if merged and _can_merge(merged[-1], unit):
            previous = merged[-1]
            merged[-1] = replace(
                previous,
                text=f"{previous.text} {unit.text}",
                bbox_mpt=_union((previous.bbox_mpt, unit.bbox_mpt)),
                line_ids=(*previous.line_ids, *unit.line_ids),
                confidence_ppm=min(previous.confidence_ppm, unit.confidence_ppm),
                max_font_size_mpt=max(
                    previous.max_font_size_mpt,
                    unit.max_font_size_mpt,
                ),
            )
        else:
            merged.append(unit)
    return tuple(merged)


def classify_page_lines(
    page: Mapping[str, object],
    page_bands: PageBands,
) -> tuple[RoleBlock, ...]:
    """Classify and locally merge one page without reparsing the source PDF."""

    page_number = page.get("page_number")
    if type(page_number) is not int or page_number != page_bands.page_number:
        raise ValueError("page and band page numbers must match")
    raw_lines = page.get("lines")
    if not isinstance(raw_lines, Sequence) or isinstance(raw_lines, (str, bytes)):
        raise ValueError("page lines must be a sequence")
    if not all(isinstance(line, Mapping) for line in raw_lines):
        raise ValueError("every page line must be an object")
    lines = sorted(raw_lines, key=lambda line: _line_sort_key(line, page_bands))
    line_by_id = {str(line.get("id")): line for line in lines}
    if len(line_by_id) != len(lines):
        raise ValueError("page line identifiers must be unique")

    captions = page.get("captions", ())
    if not isinstance(captions, Sequence) or isinstance(captions, (str, bytes)):
        raise ValueError("page captions must be a sequence")
    caption_by_line: dict[str, Mapping[str, object]] = {}
    for caption in captions:
        if not isinstance(caption, Mapping):
            raise ValueError("every caption must be an object")
        line_ids = caption.get("line_ids")
        if not isinstance(line_ids, Sequence) or isinstance(line_ids, (str, bytes)):
            raise ValueError("caption line identifiers must be a sequence")
        for identifier in line_ids:
            if not isinstance(identifier, str) or identifier in caption_by_line:
                raise ValueError("caption line identifiers must be unique")
            caption_by_line[identifier] = caption

    ordinary = [line for line in lines if str(line.get("id")) not in caption_by_line]
    font_samples = [
        _font_size(line)
        for line in ordinary
        if line.get("body_eligible", True) is not False and _font_size(line) > 0
    ]
    typical_font = int(median(font_samples)) if font_samples else 10_000
    title_ids = _title_ids(ordinary, page_number=page_number)
    roles: dict[str, str] = {}
    for band in page_bands.bands:
        for column in band.columns:
            local = [
                line
                for line in ordinary
                if _assignment(_box(line.get("bbox_mpt")), page_bands) == (band, column)
            ]
            roles.update(
                _plain_roles(
                    local,
                    page_number=page_number,
                    title_ids=title_ids,
                    typical_font_mpt=typical_font,
                )
            )

    units: list[_Unit] = []
    consumed_caption_ids: set[str] = set()
    for line in lines:
        identifier = str(line.get("id"))
        caption = caption_by_line.get(identifier)
        if caption is not None:
            caption_id = caption.get("id")
            if not isinstance(caption_id, str) or not caption_id:
                raise ValueError("caption must have an identifier")
            if caption_id in consumed_caption_ids:
                continue
            consumed_caption_ids.add(caption_id)
            caption_line_ids = tuple(str(item) for item in caption["line_ids"])
            try:
                caption_lines = [line_by_id[item] for item in caption_line_ids]
            except KeyError as exc:
                raise ValueError("caption references an unknown line") from exc
            caption_lines.sort(key=lambda item: _line_sort_key(item, page_bands))
            boxes = [_box(item.get("bbox_mpt")) for item in caption_lines]
            bbox = _union(boxes)
            band, column = _assignment(bbox, page_bands)
            kind = caption.get("kind")
            if kind not in {"figure", "table"}:
                raise ValueError("caption kind must be figure or table")
            target = caption.get("target_id")
            if not isinstance(target, str) or not target:
                raise ValueError("caption must target a graphic region")
            text = caption.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("caption must have non-empty text")
            units.append(
                _Unit(
                    role=f"{kind}-caption",
                    text=text.strip(),
                    bbox_mpt=bbox,
                    first_line_bbox_mpt=boxes[0],
                    line_ids=tuple(str(item["id"]) for item in caption_lines),
                    band=band,
                    column=column,
                    confidence_ppm=min(
                        _confidence(item, 1_000_000) for item in caption_lines
                    ),
                    target_graphic_id=target,
                    max_font_size_mpt=max(_font_size(item) for item in caption_lines),
                )
            )
            continue

        exclusion = line.get("exclusion_kind")
        container_kind = line.get("container_kind")
        target: str | None = None
        if exclusion is not None:
            role = _EXCLUSION_ROLES.get(str(exclusion), "bibliographic-metadata")
        elif container_kind in {"figure", "table"}:
            role = "figure-text" if container_kind == "figure" else "table-cell"
            raw_target = line.get("container_id")
            if not isinstance(raw_target, str) or not raw_target:
                raise ValueError("graphic-internal line must target its container")
            target = raw_target
        else:
            role = roles[identifier]
        units.append(
            _unit_from_line(
                line,
                role=role,
                page_bands=page_bands,
                target_graphic_id=target,
            )
        )

    units.sort(
        key=lambda unit: (
            unit.band.index,
            unit.column.index,
            -unit.first_line_bbox_mpt[3],
            unit.first_line_bbox_mpt[0],
            unit.line_ids,
        )
    )
    merged = _merge_units(units)
    return tuple(
        RoleBlock(
            id=f"p{page_number:04d}-role-block-{ordinal:05d}",
            page_number=page_number,
            role=unit.role,
            translation_policy=(
                "required" if unit.role in TRANSLATABLE_ROLES else "excluded"
            ),
            text=unit.text,
            bbox_mpt=unit.bbox_mpt,
            first_line_bbox_mpt=unit.first_line_bbox_mpt,
            line_ids=unit.line_ids,
            band_id=unit.band.id,
            column_id=unit.column.id,
            band_index=unit.band.index,
            column_index=unit.column.index,
            confidence_ppm=unit.confidence_ppm,
            source_ordinal=ordinal,
            max_font_size_mpt=unit.max_font_size_mpt,
            target_graphic_id=unit.target_graphic_id,
        )
        for ordinal, unit in enumerate(merged, start=1)
    )


def classify_document_lines(
    pages: Sequence[Mapping[str, object]],
    page_bands: Sequence[PageBands],
) -> tuple[tuple[RoleBlock, ...], ...]:
    """Classify pages and carry only explicit terminal section state."""

    if len(pages) != len(page_bands):
        raise ValueError("page and band collections must have equal lengths")
    blocks_by_page = tuple(
        classify_page_lines(page, detected)
        for page, detected in zip(pages, page_bands, strict=True)
    )
    acknowledgements_active = False
    references_active = False
    classified: list[tuple[RoleBlock, ...]] = []
    for blocks in blocks_by_page:
        page_blocks: list[RoleBlock] = []
        for block in blocks:
            starts_references = (
                not references_active
                and block.role == "heading"
                and _REFERENCES_LABEL.fullmatch(block.text) is not None
            )
            starts_acknowledgements = (
                not references_active
                and block.role == "heading"
                and _ACKNOWLEDGEMENTS_LABEL.fullmatch(block.text) is not None
            )
            if starts_references:
                acknowledgements_active = False
                references_active = True
            elif references_active and block.translation_policy == "required":
                block = replace(
                    block,
                    role="reference-entry",
                    translation_policy="excluded",
                )
            elif starts_acknowledgements:
                block = replace(
                    block,
                    role="acknowledgements",
                    translation_policy="excluded",
                )
                acknowledgements_active = True
            elif acknowledgements_active and block.role == "heading":
                acknowledgements_active = False
            elif acknowledgements_active and block.translation_policy == "required":
                block = replace(
                    block,
                    role="acknowledgements",
                    translation_policy="excluded",
                )
            page_blocks.append(block)
        classified.append(tuple(page_blocks))
    return tuple(classified)
