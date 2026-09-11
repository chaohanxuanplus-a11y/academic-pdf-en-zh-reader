# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Extract the small, deterministic page-object inventory used downstream."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

type BoxMpt = tuple[int, int, int, int]
type ColorValue = tuple[int | str, ...] | None


def _mpt(value: object) -> int:
    return int(
        (Decimal(str(value)) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _ppm(value: object) -> int:
    return int(
        (Decimal(str(value)) * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _box(raw: object) -> BoxMpt:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError("PDF object has no valid four-coordinate box")
    x0, y0, x1, y1 = (_mpt(value) for value in raw)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _top_box(raw: object) -> tuple[object, object, object, object]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError("PDF page has no valid four-coordinate box")
    return raw[0], raw[1], raw[2], raw[3]


def _relative_bottom_left_box(
    raw: object,
    crop_top_box: tuple[object, object, object, object],
) -> BoxMpt:
    """Convert displayed top-left coordinates to crop-relative bottom-left."""

    x0, top, x1, bottom = _top_box(raw)
    crop_x0, _crop_top, _crop_x1, crop_bottom = crop_top_box
    return _box(
        (
            Decimal(str(x0)) - Decimal(str(crop_x0)),
            Decimal(str(crop_bottom)) - Decimal(str(bottom)),
            Decimal(str(x1)) - Decimal(str(crop_x0)),
            Decimal(str(crop_bottom)) - Decimal(str(top)),
        )
    )


def _object_box(
    raw: dict[str, Any],
    crop_top_box: tuple[object, object, object, object],
) -> BoxMpt:
    return _relative_bottom_left_box(
        (raw["x0"], raw["top"], raw["x1"], raw["bottom"]),
        crop_top_box,
    )


def _color(raw: object) -> ColorValue:
    if raw is None:
        return None
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    normalized: list[int | str] = []
    for value in values:
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            normalized.append(_ppm(value))
        else:
            normalized.append(str(value))
    return tuple(normalized)


@dataclass(frozen=True)
class CharacterObject:
    id: str
    page_number: int
    text: str
    bbox_mpt: BoxMpt
    font_name: str
    font_size_mpt: int
    fill_color: ColorValue
    stroke_color: ColorValue
    upright: bool


@dataclass(frozen=True)
class VectorObject:
    id: str
    page_number: int
    source_kind: str
    bbox_mpt: BoxMpt
    line_width_mpt: int
    fill_color: ColorValue
    stroke_color: ColorValue
    filled: bool
    stroked: bool


@dataclass(frozen=True)
class ImageObject:
    id: str
    page_number: int
    bbox_mpt: BoxMpt
    pixel_width: int | None
    pixel_height: int | None


@dataclass(frozen=True)
class PageObjects:
    page_number: int
    media_box_mpt: BoxMpt
    crop_box_mpt: BoxMpt
    rotation_degrees: int
    chars: tuple[CharacterObject, ...]
    rectangles: tuple[VectorObject, ...]
    curves: tuple[VectorObject, ...]
    images: tuple[ImageObject, ...]


@dataclass(frozen=True)
class ExtractedDocument:
    pages: tuple[PageObjects, ...]


def _sort_key(
    raw: dict[str, Any],
    crop_top_box: tuple[object, object, object, object],
) -> tuple[object, ...]:
    x0, y0, x1, y1 = _object_box(raw, crop_top_box)
    return (
        -y1,
        x0,
        y0,
        x1,
        str(raw.get("text", "")),
        str(raw.get("object_type", "")),
    )


def _chars(
    page_number: int,
    raw_items: list[dict[str, Any]],
    crop_top_box: tuple[object, object, object, object],
) -> tuple[CharacterObject, ...]:
    ordered = sorted(
        enumerate(raw_items),
        key=lambda pair: (*_sort_key(pair[1], crop_top_box), pair[0]),
    )
    return tuple(
        CharacterObject(
            id=f"p{page_number:04d}-char-{ordinal:06d}",
            page_number=page_number,
            text=str(raw.get("text", "")),
            bbox_mpt=_object_box(raw, crop_top_box),
            font_name=str(raw.get("fontname", "unknown")),
            font_size_mpt=_mpt(raw.get("size", 0)),
            fill_color=_color(raw.get("non_stroking_color")),
            stroke_color=_color(raw.get("stroking_color")),
            upright=bool(raw.get("upright", True)),
        )
        for ordinal, (_source_index, raw) in enumerate(ordered, start=1)
    )


def _vectors(
    page_number: int,
    kind: str,
    raw_items: list[dict[str, Any]],
    crop_top_box: tuple[object, object, object, object],
) -> tuple[VectorObject, ...]:
    ordered = sorted(
        enumerate(raw_items),
        key=lambda pair: (*_sort_key(pair[1], crop_top_box), pair[0]),
    )
    return tuple(
        VectorObject(
            id=f"p{page_number:04d}-{kind}-{ordinal:06d}",
            page_number=page_number,
            source_kind=str(raw.get("object_type", kind)),
            bbox_mpt=_object_box(raw, crop_top_box),
            line_width_mpt=_mpt(raw.get("linewidth", 0)),
            fill_color=_color(raw.get("non_stroking_color")),
            stroke_color=_color(raw.get("stroking_color")),
            filled=bool(raw.get("fill", False)),
            stroked=bool(raw.get("stroke", False)),
        )
        for ordinal, (_source_index, raw) in enumerate(ordered, start=1)
    )


def _images(
    page_number: int,
    raw_items: list[dict[str, Any]],
    crop_top_box: tuple[object, object, object, object],
) -> tuple[ImageObject, ...]:
    ordered = sorted(
        enumerate(raw_items),
        key=lambda pair: (*_sort_key(pair[1], crop_top_box), pair[0]),
    )
    result: list[ImageObject] = []
    for ordinal, (_source_index, raw) in enumerate(ordered, start=1):
        source_size = raw.get("srcsize")
        width: int | None = None
        height: int | None = None
        if isinstance(source_size, (list, tuple)) and len(source_size) == 2:
            width, height = int(source_size[0]), int(source_size[1])
        result.append(
            ImageObject(
                id=f"p{page_number:04d}-image-{ordinal:06d}",
                page_number=page_number,
                bbox_mpt=_object_box(raw, crop_top_box),
                pixel_width=width,
                pixel_height=height,
            )
        )
    return tuple(result)


def extract_page_objects(pdf_path: str | Path) -> ExtractedDocument:
    """Read each PDF page once and return only deterministic source facts.

    The extractor intentionally never reads the document metadata. Synthetic
    fixture truth is test scaffolding, not an extraction oracle.
    """

    from academic_pdf_en_zh_reader.extraction.runtime import initialize_pinned_pdfminer

    initialize_pinned_pdfminer()
    import pdfplumber

    pages: list[PageObjects] = []
    with pdfplumber.open(Path(pdf_path), unicode_norm="NFC") as document:
        for page_number, page in enumerate(document.pages, start=1):
            visible_page = page
            try:
                crop_top_box = _top_box(page.cropbox or page.mediabox)
                visible_page = page.crop(crop_top_box, strict=True)
                rectangles = _vectors(
                    page_number,
                    "rect",
                    list(visible_page.rects),
                    crop_top_box,
                )
                curves = _vectors(
                    page_number,
                    "curve",
                    [*visible_page.lines, *visible_page.curves],
                    crop_top_box,
                )
                pages.append(
                    PageObjects(
                        page_number=page_number,
                        media_box_mpt=_relative_bottom_left_box(
                            page.mediabox,
                            crop_top_box,
                        ),
                        crop_box_mpt=_relative_bottom_left_box(
                            crop_top_box,
                            crop_top_box,
                        ),
                        rotation_degrees=int(page.rotation or 0) % 360,
                        chars=_chars(
                            page_number,
                            list(visible_page.chars),
                            crop_top_box,
                        ),
                        rectangles=rectangles,
                        curves=curves,
                        images=_images(
                            page_number,
                            list(visible_page.images),
                            crop_top_box,
                        ),
                    )
                )
            finally:
                if visible_page is not page:
                    visible_page.close()
                page.close()
    return ExtractedDocument(pages=tuple(pages))
