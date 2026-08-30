# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.frame_graph import build_frame_graph
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)


def _inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    caption_text = "Figure 1. Stable response."
    caption_id = stable_source_id(
        page_number=1,
        reading_order=0,
        role="figure-caption",
        source_char_start=0,
        source_char_end=len(caption_text),
    )
    source = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": "c" * 64,
        "normalized_pdf_sha256": "d" * 64,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "figure-band",
                        "y_top_mpt": 760_000,
                        "y_bottom_mpt": 200_000,
                        "columns": [
                            {
                                "id": "figure-col",
                                "x_left_mpt": 50_000,
                                "x_right_mpt": 545_000,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [
                    {
                        "id": "figure-1",
                        "kind": "figure",
                        "bbox_mpt": [50_000, 300_000, 545_000, 700_000],
                        "confidence_ppm": 990_000,
                    }
                ],
                "blocks": [
                    {
                        "id": caption_id,
                        "role": "figure-caption",
                        "translation_policy": "required",
                        "band_id": "figure-band",
                        "column_id": "figure-col",
                        "reading_order": 0,
                        "source_char_start": 0,
                        "source_char_end": len(caption_text),
                        "text": caption_text,
                        "bbox_mpt": [50_000, 220_000, 545_000, 250_000],
                        "first_line_bbox_mpt": [50_000, 220_000, 545_000, 250_000],
                        "confidence_ppm": 990_000,
                        "target_graphic_id": "figure-1",
                    }
                ],
            }
        ],
    }
    units = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "c" * 64,
        "normalized_pdf_sha256": "d" * 64,
        "units": [
            {
                "id": caption_id,
                "role": "figure-caption",
                "reading_order": 0,
                "source_text": caption_text,
                "confidence_ppm": 990_000,
                "fragments": [
                    {
                        "page_number": 1,
                        "block_id": caption_id,
                        "source_char_start": 0,
                        "source_char_end": len(caption_text),
                    }
                ],
            }
        ],
    }
    translation = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": caption_id,
                "chinese_text": "图1。响应稳定。",
                "spans": [],
                "terminology": [],
            }
        ],
    }
    return source, units, translation, caption_id


def _typography():
    return (
        build_style_contract((FontSizeSample(size_mpt=10_000, character_count=500),)),
        FontRunResolver(load_font_registry()),
    )


def test_graphic_height_never_contributes_to_right_band_height() -> None:
    source, units, translation, caption_id = _inputs()
    styles, resolver = _typography()
    notes = {caption_id: ("横轴表示时间，曲线显示响应保持稳定。",)}

    first = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
        approved_figure_notes=notes,
    )
    taller_graphic = deepcopy(source)
    taller_graphic["pages"][0]["graphic_nodes"][0]["bbox_mpt"] = [
        50_000,
        260_000,
        545_000,
        740_000,
    ]
    second = build_frame_graph(
        taller_graphic,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
        approved_figure_notes=notes,
    )

    first_band = first["pages"][0]["bands"][0]
    second_band = second["pages"][0]["bands"][0]
    assert (
        first_band["height_basis"]
        == "initial-unsplit-caption-plus-approved-figure-notes"
    )
    assert "minimum_content_height_mpt" not in first_band
    assert "content_item_ids" not in first_band
    assert first_band["initial_unsplit_content_item_ids"] == [
        f"unit:{caption_id}",
        f"note:{caption_id}:000",
    ]
    assert (
        first_band["initial_unsplit_content_height_mpt"]
        == second_band["initial_unsplit_content_height_mpt"]
    )
    assert first_band["initial_unsplit_content_height_mpt"] < 100_000


def test_approved_figure_note_adds_only_its_measured_flow_and_fixed_gap() -> None:
    source, units, translation, caption_id = _inputs()
    styles, resolver = _typography()

    without = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
    )
    with_note = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
        approved_figure_notes={caption_id: ("横轴表示时间。",)},
    )

    assert (
        with_note["pages"][0]["bands"][0]["initial_unsplit_content_height_mpt"]
        > without["pages"][0]["bands"][0]["initial_unsplit_content_height_mpt"]
    )
