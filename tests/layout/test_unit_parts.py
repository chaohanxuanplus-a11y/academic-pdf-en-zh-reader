# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.frame_graph import build_frame_graph
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
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


def test_cross_column_unit_has_one_unsplit_initial_part() -> None:
    source, units, translation, _chinese = _inputs()
    styles = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=FontRunResolver(load_font_registry()),
    )

    flow = graph["unit_flows"][0]
    part = graph["unit_parts"][0]
    assert len(graph["unit_parts"]) == 1
    assert len(flow["allowed_native_frame_ids"]) == 2
    assert part["frame_id"] == flow["home_frame_id"]
    assert (part["line_start"], part["line_end"]) == (0, flow["line_count"])
    assert (part["source_fragment_start"], part["source_fragment_end"]) == (0, 2)
    assert part["is_first_part"] is True
    assert part["creates_anchor"] is True
    assert flow["anchor"]["kind"] == "soft-y"
    assert flow["anchor"]["source_visual_center_offset_mpt"] == 841_890 - 60_000


def test_line_offsets_cover_translation_exactly_despite_removed_line_end_spaces() -> (
    None
):
    source, units, translation, chinese = _inputs()
    styles = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=FontRunResolver(load_font_registry()),
    )

    flow = graph["unit_flows"][0]
    lines = flow["lines"]
    assert len(lines) > 1
    assert lines[0]["target_start"] == 0
    assert lines[-1]["target_end"] == len(chinese)
    assert all(
        left["target_end"] == right["target_start"]
        for left, right in zip(lines, lines[1:], strict=False)
    )
    assert all(
        line["text"] == chinese[line["target_start"] : line["target_end"]].strip()
        for line in lines
    )

    frames = {
        frame["id"]: frame
        for frame in graph["pages"][0]["frames"]
        if frame["kind"] == "native"
    }
    minimum_width = min(
        frames[identifier]["text_right_mpt"] - frames[identifier]["text_left_mpt"]
        for identifier in flow["allowed_native_frame_ids"]
    )
    assert all(line["width_mpt"] <= minimum_width for line in lines)


def test_line_hashes_bind_fixed_style_metrics_and_exact_resolved_runs() -> None:
    source, units, translation, _chinese = _inputs()
    styles = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=FontRunResolver(load_font_registry()),
    )

    flow = graph["unit_flows"][0]
    body_style = styles.style_for("body")
    assert flow["style"] == {
        "style_id": (
            f"typography-v1:body:{body_style.font_role}:"
            f"{body_style.size_mpt}:{body_style.line_height_mpt}"
        ),
        "semantic_role": "body",
        "font_role": body_style.font_role,
        "size_mpt": body_style.size_mpt,
        "line_height_mpt": body_style.line_height_mpt,
    }
    for line in flow["lines"]:
        assert "".join(run["text"] for run in line["runs"]) == line["text"]
        payload = {key: value for key, value in line.items() if key != "line_box_hash"}
        assert line["line_box_hash"] == sha256_canonical(
            {"line_box_contract_version": "1.0.0", **payload}
        )
    assert flow["line_sequence_hash"] == sha256_canonical(
        {"style": flow["style"], "lines": flow["lines"]}
    )
    assert [face["role"] for face in graph["font_fingerprint"]] == [
        "body",
        "heading",
        "symbols",
    ]


def test_cross_page_fragments_expand_allowlist_without_splitting_target() -> None:
    source, units, translation = _cross_page_inputs()
    styles = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=styles,
        resolver=FontRunResolver(load_font_registry()),
    )

    flow = graph["unit_flows"][0]
    part = graph["unit_parts"][0]
    frames = {frame["id"]: frame for page in graph["pages"] for frame in page["frames"]}
    assert [
        (
            frames[identifier]["source_page_number"],
            frames[identifier]["source_column_id"],
        )
        for identifier in flow["allowed_native_frame_ids"]
    ] == [(1, "left"), (2, "page-two-col")]
    assert flow["continuation_owner_page_number"] == 2
    assert len(graph["unit_parts"]) == 1
    assert (part["line_start"], part["line_end"]) == (0, flow["line_count"])
