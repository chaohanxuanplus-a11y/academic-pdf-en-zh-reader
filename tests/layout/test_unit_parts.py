# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.frame_graph import build_frame_graph
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    build_style_contract,
)


def _inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    first_text = "The observed microstruc-"
    second_text = "ture remained stable."
    first_id = stable_source_id(
        page_number=1,
        reading_order=0,
        role="body",
        source_char_start=0,
        source_char_end=len(first_text),
    )
    second_id = stable_source_id(
        page_number=1,
        reading_order=1,
        role="body",
        source_char_start=len(first_text),
        source_char_end=len(first_text) + len(second_text),
    )
    boxes = (
        (first_id, "left", first_text, 0, [40_000, 45_000, 120_000, 75_000]),
        (
            second_id,
            "right",
            second_text,
            len(first_text),
            [170_000, 765_000, 250_000, 795_000],
        ),
    )
    blocks = [
        {
            "id": identifier,
            "role": "body",
            "translation_policy": "required",
            "band_id": "two-col",
            "column_id": column,
            "reading_order": order,
            "source_char_start": start,
            "source_char_end": start + len(text),
            "text": text,
            "bbox_mpt": bbox,
            "first_line_bbox_mpt": bbox,
            "confidence_ppm": 990_000,
        }
        for order, (identifier, column, text, start, bbox) in enumerate(boxes)
    ]
    source = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": "d" * 64,
        "normalized_pdf_sha256": "e" * 64,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "two-col",
                        "y_top_mpt": 800_000,
                        "y_bottom_mpt": 40_000,
                        "columns": [
                            {
                                "id": "left",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 130_000,
                            },
                            {
                                "id": "right",
                                "x_left_mpt": 170_000,
                                "x_right_mpt": 260_000,
                            },
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": blocks,
            }
        ],
    }
    unit = {
        "id": first_id,
        "role": "body",
        "reading_order": 0,
        "source_text": "The observed microstructure remained stable.",
        "confidence_ppm": 990_000,
        "fragments": [
            {
                "page_number": 1,
                "block_id": block["id"],
                "source_char_start": block["source_char_start"],
                "source_char_end": block["source_char_end"],
            }
            for block in blocks
        ],
    }
    units = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "d" * 64,
        "normalized_pdf_sha256": "e" * 64,
        "units": [unit],
    }
    chinese = "结果显示 微观结构在 5 mg 处理后仍然保持稳定，且该结论在重复实验中一致。"
    translation = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": first_id,
                "chinese_text": chinese,
                "spans": [],
                "terminology": [],
            }
        ],
    }
    return source, units, translation, chinese


def _cross_page_inputs() -> tuple[
    dict[str, object], dict[str, object], dict[str, object]
]:
    source, units, translation, _chinese = _inputs()
    first_page = source["pages"][0]
    second_block = first_page["blocks"].pop()
    second_block["id"] = stable_source_id(
        page_number=2,
        reading_order=1,
        role="body",
        source_char_start=second_block["source_char_start"],
        source_char_end=second_block["source_char_end"],
    )
    second_block["band_id"] = "page-two"
    second_block["column_id"] = "page-two-col"
    second_block["bbox_mpt"] = [40_000, 765_000, 120_000, 795_000]
    second_block["first_line_bbox_mpt"] = [40_000, 765_000, 120_000, 795_000]
    first_page["bands"][0]["columns"] = [
        {"id": "left", "x_left_mpt": 40_000, "x_right_mpt": 130_000}
    ]
    first_page["bands"].append(
        {
            "id": "unrelated-middle-band",
            "y_top_mpt": 38_000,
            "y_bottom_mpt": 10_000,
            "columns": [
                {
                    "id": "unrelated-middle-col",
                    "x_left_mpt": 170_000,
                    "x_right_mpt": 260_000,
                }
            ],
        }
    )
    source["pages"].append(
        {
            "page_number": 2,
            "media_box_mpt": [0, 0, 595_276, 841_890],
            "crop_box_mpt": [0, 0, 595_276, 841_890],
            "rotation_degrees": 0,
            "bands": [
                {
                    "id": "page-two",
                    "y_top_mpt": 800_000,
                    "y_bottom_mpt": 40_000,
                    "columns": [
                        {
                            "id": "page-two-col",
                            "x_left_mpt": 40_000,
                            "x_right_mpt": 130_000,
                        }
                    ],
                }
            ],
            "graphic_nodes": [],
            "blocks": [second_block],
        }
    )
    units["units"][0]["fragments"][1] = {
        "page_number": 2,
        "block_id": second_block["id"],
        "source_char_start": second_block["source_char_start"],
        "source_char_end": second_block["source_char_end"],
    }
    translation["units_hash"] = sha256_canonical(units)
    return source, units, translation


def test_cross_column_source_fragments_share_one_continuous_target():
    source, units, translation, chinese = _inputs()
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=build_style_contract(()),
        resolver=FontRunResolver(load_font_registry()),
    )
    flow = graph["unit_flows"][0]
    assert flow["column_count"] == 2
    assert flow["source_page_numbers"] == [1]
    assert flow["lines"][-1]["target_end"] == len(chinese)


def test_cross_page_source_fragments_preserve_semantic_page_set():
    source, units, translation = _cross_page_inputs()
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=build_style_contract(()),
        resolver=FontRunResolver(load_font_registry()),
    )
    assert graph["unit_flows"][0]["source_page_numbers"] == [1, 2]
    assert len(graph["unit_flows"]) == 1
