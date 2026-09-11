# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Assemble source topology or fail closed on insufficient evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from academic_pdf_en_zh_reader.job.hashing import stable_source_id
from academic_pdf_en_zh_reader.topology.bands import (
    PageBands,
    detect_document_bands,
)
from academic_pdf_en_zh_reader.topology.contracts import (
    TopologyOutcome,
    TopologyStatus,
)
from academic_pdf_en_zh_reader.topology.reading_order import build_reading_order
from academic_pdf_en_zh_reader.topology.roles import (
    RoleBlock,
    classify_document_lines,
)

_CONFIDENCE_THRESHOLD_PPM = 800_000
_GRAPHIC_CONFIDENCE_PPM = {
    "embedded-image": 950_000,
    "intersecting-grid": 950_000,
    "captioned-three-rule-table": 950_000,
    "enclosed-vector-drawing": 850_000,
    "caption-bounded-composite": 900_000,
}


def _pages(extraction: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = extraction.get("pages")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ValueError("extraction must contain at least one page")
    if not all(isinstance(page, Mapping) for page in raw):
        raise ValueError("every extraction page must be an object")
    return list(raw)


def _is_blank(page: Mapping[str, object]) -> bool:
    return not any(
        page.get(name)
        for name in (
            "chars",
            "rectangles",
            "curves",
            "images",
            "lines",
            "graphic_regions",
        )
    )


def _graphic_confidence(graphic: Mapping[str, object]) -> int:
    evidence = graphic.get("evidence")
    if not isinstance(evidence, str):
        return 0
    return _GRAPHIC_CONFIDENCE_PPM.get(evidence, 0)


def _needs_review(
    pages: Sequence[Mapping[str, object]],
    page_bands: Sequence[PageBands],
    blocks: Sequence[RoleBlock],
) -> bool:
    for page, detected in zip(pages, page_bands, strict=True):
        if _is_blank(page):
            continue
        if detected.score_ppm < _CONFIDENCE_THRESHOLD_PPM or any(
            band.score_ppm < _CONFIDENCE_THRESHOLD_PPM for band in detected.bands
        ):
            return True
        graphics = page.get("graphic_regions", ())
        if not isinstance(graphics, Sequence) or isinstance(graphics, (str, bytes)):
            raise ValueError("page graphic regions must be a sequence")
        if any(
            not isinstance(graphic, Mapping)
            or _graphic_confidence(graphic) < _CONFIDENCE_THRESHOLD_PPM
            for graphic in graphics
        ):
            return True
    return any(
        block.translation_policy == "required"
        and block.confidence_ppm < _CONFIDENCE_THRESHOLD_PPM
        for block in blocks
    )


def _source_bands(detected: PageBands) -> list[dict[str, object]]:
    return [
        {
            "id": band.id,
            "y_top_mpt": band.y_top_mpt,
            "y_bottom_mpt": band.y_bottom_mpt,
            "columns": [
                {
                    "id": column.id,
                    "x_left_mpt": column.x_left_mpt,
                    "x_right_mpt": column.x_right_mpt,
                }
                for column in band.columns
            ],
        }
        for band in detected.bands
    ]


def _source_graphics(page: Mapping[str, object]) -> list[dict[str, object]]:
    raw = page.get("graphic_regions", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("page graphic regions must be a sequence")
    graphics: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("every graphic region must be an object")
        identifier = item.get("id")
        kind = item.get("kind")
        bbox = item.get("bbox_mpt")
        if not isinstance(identifier, str) or kind not in {"figure", "table"}:
            raise ValueError("graphic region identity is invalid")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("graphic region box is invalid")
        graphics.append(
            {
                "id": identifier,
                "kind": kind,
                "bbox_mpt": list(bbox),
                "confidence_ppm": _graphic_confidence(item),
            }
        )
    return sorted(graphics, key=lambda item: str(item["id"]))


def _source_blocks(
    blocks: Sequence[RoleBlock],
    reading_order_by_id: Mapping[str, int],
) -> list[dict[str, object]]:
    ordered = sorted(blocks, key=lambda block: reading_order_by_id[block.id])
    cursor = 0
    result: list[dict[str, object]] = []
    for block in ordered:
        start = cursor
        end = start + len(block.text)
        cursor = end
        reading_order = reading_order_by_id[block.id]
        source: dict[str, object] = {
            "id": stable_source_id(
                page_number=block.page_number,
                reading_order=reading_order,
                role=block.role,
                source_char_start=start,
                source_char_end=end,
            ),
            "role": block.role,
            "translation_policy": block.translation_policy,
            "band_id": block.band_id,
            "column_id": block.column_id,
            "reading_order": reading_order,
            "source_char_start": start,
            "source_char_end": end,
            "text": block.text,
            "bbox_mpt": list(block.bbox_mpt),
            "first_line_bbox_mpt": list(block.first_line_bbox_mpt),
            "confidence_ppm": block.confidence_ppm,
            "source_font_size_mpt": block.max_font_size_mpt,
        }
        if block.target_graphic_id is not None:
            source["target_graphic_id"] = block.target_graphic_id
        result.append(source)
    return result


def _integer_box(page: Mapping[str, object], name: str) -> list[int]:
    raw = page.get(name)
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 4
        or not all(type(value) is int for value in raw)
    ):
        raise ValueError(f"page {name} must contain four integer milli-points")
    return list(raw)


def build_topology(extraction: Mapping[str, object]) -> TopologyOutcome:
    """Return schema-valid source topology or an explicit review stop."""

    if not isinstance(extraction, Mapping):
        raise TypeError("extraction must be a mapping")
    pages = _pages(extraction)
    page_bands = detect_document_bands(extraction)
    blocks_by_page = classify_document_lines(pages, page_bands)
    all_blocks = tuple(block for blocks in blocks_by_page for block in blocks)
    if _needs_review(pages, page_bands, all_blocks):
        return TopologyOutcome(
            status=TopologyStatus.NEEDS_TOPOLOGY_REVIEW,
            source=None,
        )

    ordered = build_reading_order(all_blocks).ordered_blocks
    reading_order_by_id = {block.id: index for index, block in enumerate(ordered)}
    source_pages: list[dict[str, Any]] = []
    for page, detected, blocks in zip(
        pages,
        page_bands,
        blocks_by_page,
        strict=True,
    ):
        source_pages.append(
            {
                "page_number": page["page_number"],
                "media_box_mpt": _integer_box(page, "media_box_mpt"),
                "crop_box_mpt": _integer_box(page, "crop_box_mpt"),
                "rotation_degrees": page["rotation_degrees"],
                "bands": _source_bands(detected),
                "graphic_nodes": _source_graphics(page),
                "blocks": _source_blocks(blocks, reading_order_by_id),
            }
        )
    source = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": extraction["source_sha256"],
        "normalized_pdf_sha256": extraction["normalized_pdf_sha256"],
        "pages": source_pages,
    }
    return TopologyOutcome(status=TopologyStatus.OK, source=source)
