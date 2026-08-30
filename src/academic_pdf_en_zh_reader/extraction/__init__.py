# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic, geometry-preserving PDF content extraction."""

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

__all__ = ["ScannedPdfUnsupportedError", "extract_document"]


class ScannedPdfUnsupportedError(ValueError):
    """Raised when a page is a raster scan with a substantial OCR overlay."""


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _reject_scanned_ocr_pages(page_objects: object, line_pages: object) -> None:
    for objects, text_page in zip(page_objects, line_pages, strict=True):
        crop = objects.crop_box_mpt
        page_width = crop[2] - crop[0]
        page_height = crop[3] - crop[1]
        page_area = page_width * page_height
        has_scan_sized_image = any(
            (image.bbox_mpt[2] - image.bbox_mpt[0])
            * (image.bbox_mpt[3] - image.bbox_mpt[1])
            * 10
            >= page_area * 9
            for image in objects.images
        )
        if not has_scan_sized_image:
            continue
        text_lines = [line for line in text_page.lines if line.text.strip()]
        nonspace_characters = sum(
            not character.isspace() for line in text_lines for character in line.text
        )
        if len(text_lines) < 5 or nonspace_characters < 200:
            continue
        raise ScannedPdfUnsupportedError(
            "raster scan with substantial OCR text is unsupported"
        )


def extract_document(pdf_path: Path) -> dict[str, object]:
    """Return the complete pure-extraction result as a JSON-safe mapping."""

    if not isinstance(pdf_path, Path):
        raise TypeError("pdf_path must be a pathlib.Path")

    # Keep package import parent-safe. The PDF parser is loaded only after the
    # isolated child calls this function.
    from academic_pdf_en_zh_reader.extraction.blocks import build_basic_blocks
    from academic_pdf_en_zh_reader.extraction.page_objects import extract_page_objects
    from academic_pdf_en_zh_reader.extraction.repeated_marginals import (
        mark_repeated_marginals,
    )
    from academic_pdf_en_zh_reader.extraction.text_lines import build_text_lines

    page_objects = extract_page_objects(pdf_path).pages
    line_pages = mark_repeated_marginals(build_text_lines(page_objects), page_objects)
    _reject_scanned_ocr_pages(page_objects, line_pages)
    block_document = build_basic_blocks(page_objects, line_pages)
    pages: list[dict[str, object]] = []
    for objects, blocks in zip(page_objects, block_document.pages, strict=True):
        page = _json_safe(objects)
        page.update(
            {
                "lines": _json_safe(blocks.lines),
                "graphic_regions": _json_safe(blocks.graphic_regions),
                "captions": _json_safe(blocks.captions),
                "references": _json_safe(blocks.references),
            }
        )
        pages.append(page)
    return {"format_version": "1.0.0", "pages": pages}
