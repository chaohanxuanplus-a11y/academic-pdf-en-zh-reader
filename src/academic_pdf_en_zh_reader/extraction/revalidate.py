# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Parent-safe recomputation of every derived extraction fact."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from academic_pdf_en_zh_reader.extraction.blocks import build_basic_blocks
from academic_pdf_en_zh_reader.extraction.page_objects import (
    CharacterObject,
    ImageObject,
    PageObjects,
    VectorObject,
)
from academic_pdf_en_zh_reader.extraction.repeated_marginals import (
    mark_repeated_marginals,
)
from academic_pdf_en_zh_reader.extraction.text_lines import (
    PageTextLines,
    TextLine,
    build_text_lines,
)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _color(value: object) -> tuple[int | str, ...] | None:
    return None if value is None else tuple(value)  # type: ignore[arg-type]


def _character(raw: dict[str, Any]) -> CharacterObject:
    return CharacterObject(
        id=raw["id"],
        page_number=raw["page_number"],
        text=raw["text"],
        bbox_mpt=tuple(raw["bbox_mpt"]),
        font_name=raw["font_name"],
        font_size_mpt=raw["font_size_mpt"],
        fill_color=_color(raw["fill_color"]),
        stroke_color=_color(raw["stroke_color"]),
        upright=raw["upright"],
    )


def _vector(raw: dict[str, Any]) -> VectorObject:
    return VectorObject(
        id=raw["id"],
        page_number=raw["page_number"],
        source_kind=raw["source_kind"],
        bbox_mpt=tuple(raw["bbox_mpt"]),
        line_width_mpt=raw["line_width_mpt"],
        fill_color=_color(raw["fill_color"]),
        stroke_color=_color(raw["stroke_color"]),
        filled=raw["filled"],
        stroked=raw["stroked"],
    )


def _image(raw: dict[str, Any]) -> ImageObject:
    return ImageObject(
        id=raw["id"],
        page_number=raw["page_number"],
        bbox_mpt=tuple(raw["bbox_mpt"]),
        pixel_width=raw["pixel_width"],
        pixel_height=raw["pixel_height"],
    )


def _base_line(raw: dict[str, Any]) -> TextLine:
    return TextLine(
        id=raw["id"],
        page_number=raw["page_number"],
        text=raw["text"],
        bbox_mpt=tuple(raw["bbox_mpt"]),
        character_ids=tuple(raw["character_ids"]),
        font_names=tuple(raw["font_names"]),
        fill_colors=tuple(_color(value) for value in raw["fill_colors"]),
        max_font_size_mpt=raw["max_font_size_mpt"],
    )


def validate_derived_semantics(pages: list[dict[str, Any]]) -> None:
    """Rebuild lines' semantics and all relations without loading a PDF parser."""

    objects = tuple(
        PageObjects(
            page_number=page["page_number"],
            media_box_mpt=tuple(page["media_box_mpt"]),
            crop_box_mpt=tuple(page["crop_box_mpt"]),
            rotation_degrees=page["rotation_degrees"],
            chars=tuple(_character(item) for item in page["chars"]),
            rectangles=tuple(_vector(item) for item in page["rectangles"]),
            curves=tuple(_vector(item) for item in page["curves"]),
            images=tuple(_image(item) for item in page["images"]),
        )
        for page in pages
    )
    child_base_lines = tuple(
        PageTextLines(
            page_number=page["page_number"],
            lines=tuple(_base_line(item) for item in page["lines"]),
        )
        for page in pages
    )
    rebuilt_base_lines = build_text_lines(objects)
    if _json_safe(child_base_lines) != _json_safe(rebuilt_base_lines):
        raise ValueError("derived base lines differ")
    marked_lines = mark_repeated_marginals(rebuilt_base_lines, objects)

    from academic_pdf_en_zh_reader.extraction import _reject_scanned_ocr_pages

    _reject_scanned_ocr_pages(objects, marked_lines)
    rebuilt = build_basic_blocks(objects, marked_lines)
    for actual, expected in zip(pages, rebuilt.pages, strict=True):
        if actual["lines"] != _json_safe(expected.lines):
            raise ValueError("derived line semantics differ")
        if actual["graphic_regions"] != _json_safe(expected.graphic_regions):
            raise ValueError("derived graphic regions differ")
        if actual["captions"] != _json_safe(expected.captions):
            raise ValueError("derived captions differ")
        if actual["references"] != _json_safe(expected.references):
            raise ValueError("derived references differ")
