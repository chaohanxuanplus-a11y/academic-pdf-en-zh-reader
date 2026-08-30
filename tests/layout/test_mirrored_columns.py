# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.frame_graph import (
    DEFAULT_FRAME_GRAPH_CONFIG,
    build_frame_graph,
)
from academic_pdf_en_zh_reader.rendering.page_geometry import A4_WIDTH_MPT
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)


def _artifacts() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    blocks = []
    cursor = 0
    for order, (column, bbox, text) in enumerate(
        (
            ("wide", [50_000, 700_000, 545_000, 725_000], "Spanning title."),
            ("left", [50_000, 500_000, 240_000, 525_000], "Left body."),
            ("right", [290_000, 500_000, 545_000, 525_000], "Right body."),
        )
    ):
        role = "title" if order == 0 else "body"
        end = cursor + len(text)
        block = {
            "id": stable_source_id(
                page_number=1,
                reading_order=order,
                role=role,
                source_char_start=cursor,
                source_char_end=end,
            ),
            "role": role,
            "translation_policy": "required",
            "band_id": "top" if order == 0 else "bottom",
            "column_id": column,
            "reading_order": order,
            "source_char_start": cursor,
            "source_char_end": end,
            "text": text,
            "bbox_mpt": bbox,
            "first_line_bbox_mpt": bbox,
            "confidence_ppm": 990_000,
        }
        blocks.append(block)
        cursor = end
    source = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": "b" * 64,
        "normalized_pdf_sha256": "c" * 64,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "top",
                        "y_top_mpt": 800_000,
                        "y_bottom_mpt": 650_000,
                        "columns": [
                            {"id": "wide", "x_left_mpt": 50_000, "x_right_mpt": 545_000}
                        ],
                    },
                    {
                        "id": "bottom",
                        "y_top_mpt": 620_000,
                        "y_bottom_mpt": 40_000,
                        "columns": [
                            {
                                "id": "left",
                                "x_left_mpt": 50_000,
                                "x_right_mpt": 250_000,
                            },
                            {
                                "id": "right",
                                "x_left_mpt": 290_000,
                                "x_right_mpt": 545_000,
                            },
                        ],
                    },
                ],
                "graphic_nodes": [],
                "blocks": blocks,
            }
        ],
    }
    units_list = [
        {
            "id": block["id"],
            "role": block["role"],
            "reading_order": block["reading_order"],
            "source_text": block["text"],
            "confidence_ppm": 990_000,
            "fragments": [
                {
                    "page_number": 1,
                    "block_id": block["id"],
                    "source_char_start": block["source_char_start"],
                    "source_char_end": block["source_char_end"],
                }
            ],
        }
        for block in blocks
    ]
    units = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "b" * 64,
        "normalized_pdf_sha256": "c" * 64,
        "units": units_list,
    }
    translation = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": unit["id"],
                "chinese_text": f"译文{index}。",
                "spans": [],
                "terminology": [],
            }
            for index, unit in enumerate(units_list)
        ],
    }
    return source, units, translation


def test_mixed_bands_mirror_column_count_ratio_and_frozen_left_edges() -> None:
    source, units, translation = _artifacts()
    styles = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    resolver = FontRunResolver(load_font_registry())

    first = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
    )
    second = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
    )

    assert first == second
    native = [
        frame for frame in first["pages"][0]["frames"] if frame["kind"] == "native"
    ]
    assert [frame["column_count"] for frame in native] == [1, 2, 2]
    assert [frame["bbox_mpt"][0] for frame in native] == [
        A4_WIDTH_MPT + 50_000,
        A4_WIDTH_MPT + 50_000,
        A4_WIDTH_MPT + 290_000,
    ]
    assert all(
        frame["text_left_mpt"]
        == frame["bbox_mpt"][0] + DEFAULT_FRAME_GRAPH_CONFIG.horizontal_padding_mpt
        for frame in native
    )
    bottom = native[1:]
    assert sum(frame["width_ratio_ppm"] for frame in bottom) == 1_000_000
    assert bottom[0]["width_ratio_ppm"] < bottom[1]["width_ratio_ppm"]
