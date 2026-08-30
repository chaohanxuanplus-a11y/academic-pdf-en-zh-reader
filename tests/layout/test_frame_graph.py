# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.frame_graph import (
    FrameGraphConfig,
    FrameGraphError,
    build_frame_graph,
    validate_frame_graph_against_inputs,
)
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)

SHA = "a" * 64
NORMALIZED_SHA = "b" * 64


def _block(
    *,
    page: int,
    order: int,
    start: int,
    text: str,
    band_id: str,
    column_id: str,
    bbox: list[int],
) -> dict[str, object]:
    end = start + len(text)
    return {
        "id": stable_source_id(
            page_number=page,
            reading_order=order,
            role="body",
            source_char_start=start,
            source_char_end=end,
        ),
        "role": "body",
        "translation_policy": "required",
        "band_id": band_id,
        "column_id": column_id,
        "reading_order": order,
        "source_char_start": start,
        "source_char_end": end,
        "text": text,
        "bbox_mpt": bbox,
        "first_line_bbox_mpt": bbox,
        "confidence_ppm": 990_000,
    }


def _inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    first = _block(
        page=1,
        order=0,
        start=0,
        text="First paragraph.",
        band_id="p1-band-0",
        column_id="p1-col-0",
        bbox=[40_000, 700_000, 555_000, 730_000],
    )
    second = _block(
        page=1,
        order=1,
        start=len(first["text"]),
        text="Second paragraph.",
        band_id="p1-band-1",
        column_id="p1-col-1",
        bbox=[40_000, 500_000, 270_000, 530_000],
    )
    third = _block(
        page=1,
        order=2,
        start=int(second["source_char_end"]),
        text="Third paragraph.",
        band_id="p1-band-1",
        column_id="p1-col-2",
        bbox=[315_000, 500_000, 555_000, 530_000],
    )
    fourth = _block(
        page=2,
        order=3,
        start=0,
        text="Fourth paragraph.",
        band_id="p2-band-0",
        column_id="p2-col-0",
        bbox=[40_000, 700_000, 555_000, 730_000],
    )
    source: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": SHA,
        "normalized_pdf_sha256": NORMALIZED_SHA,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "p1-band-0",
                        "y_top_mpt": 800_000,
                        "y_bottom_mpt": 650_000,
                        "columns": [
                            {
                                "id": "p1-col-0",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 555_276,
                            }
                        ],
                    },
                    {
                        "id": "p1-band-1",
                        "y_top_mpt": 620_000,
                        "y_bottom_mpt": 40_000,
                        "columns": [
                            {
                                "id": "p1-col-1",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 280_000,
                            },
                            {
                                "id": "p1-col-2",
                                "x_left_mpt": 315_000,
                                "x_right_mpt": 555_276,
                            },
                        ],
                    },
                ],
                "graphic_nodes": [],
                "blocks": [first, second, third],
            },
            {
                "page_number": 2,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "p2-band-0",
                        "y_top_mpt": 800_000,
                        "y_bottom_mpt": 40_000,
                        "columns": [
                            {
                                "id": "p2-col-0",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 555_276,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": [fourth],
            },
        ],
    }
    units_list = [
        {
            "id": block["id"],
            "role": "body",
            "reading_order": block["reading_order"],
            "source_text": block["text"],
            "confidence_ppm": 990_000,
            "fragments": [
                {
                    "page_number": page,
                    "block_id": block["id"],
                    "source_char_start": block["source_char_start"],
                    "source_char_end": block["source_char_end"],
                }
            ],
        }
        for page, block in ((1, first), (1, second), (1, third), (2, fourth))
    ]
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": SHA,
        "normalized_pdf_sha256": NORMALIZED_SHA,
        "units": units_list,
    }
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": unit["id"],
                "chinese_text": f"完整中文译文{index}。",
                "spans": [],
                "terminology": [],
            }
            for index, unit in enumerate(units_list, start=1)
        ],
    }
    return source, units, translation


def _typography():
    styles = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    resolver = FontRunResolver(load_font_registry())
    return styles, resolver


def test_native_frames_and_edges_follow_page_band_column_reading_order() -> None:
    source, units, translation = _inputs()
    styles, resolver = _typography()

    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
    )

    native = [
        frame
        for page in graph["pages"]
        for frame in page["frames"]
        if frame["kind"] == "native"
    ]
    assert [
        (frame["source_page_number"], frame["band_index"], frame["column_index"])
        for frame in native
    ] == [(1, 0, 0), (1, 1, 0), (1, 1, 1), (2, 0, 0)]

    native_edges = [edge for edge in graph["edges"] if edge["activation"] == "always"]
    assert [(edge["from_frame_id"], edge["to_frame_id"]) for edge in native_edges] == [
        (left["id"], right["id"])
        for left, right in zip(native, native[1:], strict=False)
    ]
    assert [edge["transition"] for edge in native_edges] == [
        "next-band",
        "next-column",
        "next-source-page",
    ]


def test_continuation_templates_are_candidates_and_initial_parts_never_use_them() -> (
    None
):
    source, units, translation = _inputs()
    styles, resolver = _typography()

    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
    )

    candidates = {
        frame["id"]
        for page in graph["pages"]
        for frame in page["frames"]
        if frame["kind"] == "continuation-template"
    }
    assert candidates
    assert all(
        frame["activation"] == "candidate"
        for page in graph["pages"]
        for frame in page["frames"]
        if frame["id"] in candidates
    )
    assert candidates.isdisjoint(part["frame_id"] for part in graph["unit_parts"])


def test_flow_spacing_freezes_the_exact_frame_graph_configuration() -> None:
    source, units, translation = _inputs()
    styles, resolver = _typography()
    config = FrameGraphConfig(
        version=7,
        horizontal_padding_mpt=3_001,
        vertical_padding_mpt=4_002,
        block_gap_mpt=5_003,
        figure_note_gap_mpt=6_004,
    )

    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
        config=config,
    )

    assert graph["flow_spacing"] == {
        "config_version": 7,
        "horizontal_padding_mpt": 3_001,
        "vertical_padding_mpt": 4_002,
        "block_gap_mpt": 5_003,
        "figure_note_gap_mpt": 6_004,
    }
    header = graph["continuation_header"]
    assert header["contract_version"] == "1.0.0"
    assert header["text"] == "译文续页"
    assert header["style"] == {
        "style_id": "typography-v1:auxiliary:body:8600:14620",
        "semantic_role": "auxiliary",
        "font_role": "body",
        "size_mpt": 8_600,
        "line_height_mpt": 14_620,
    }
    assert header["runs"] == [
        {
            "run_index": 0,
            "text": "译文续页",
            "font_role": "body",
            "font_name": graph["font_fingerprint"][0]["reportlab_name"],
            "x_offset_mpt": 0,
            "width_mpt": 34_400,
        }
    ]
    assert header["width_mpt"] == 34_400
    assert header["line_height_mpt"] == 14_620
    assert header["ascent_mpt"] == 7_568
    assert header["descent_mpt"] == -1_032
    assert header["top_inset_mpt"] == config.vertical_padding_mpt
    assert header["gap_after_mpt"] == 3_000
    assert header["reserve_height_mpt"] == 21_622
    assert header["color_token"] == "muted_gray"
    assert header["color_hex"] == "#666666"
    assert header["horizontal_alignment"] == "right"
    assert header["header_hash"] == sha256_canonical(
        {key: value for key, value in header.items() if key != "header_hash"}
    )


def test_parent_recomputation_rejects_a_tampered_fixed_frame_coordinate() -> None:
    source, units, translation = _inputs()
    styles, resolver = _typography()
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=resolver,
    )
    tampered = deepcopy(graph)
    tampered["pages"][0]["frames"][0]["text_left_mpt"] += 1

    with pytest.raises(FrameGraphError, match="recomputed"):
        validate_frame_graph_against_inputs(
            source,
            units,
            translation,
            tampered,
            style_contract=styles,
            resolver=resolver,
        )
