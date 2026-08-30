# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from academic_pdf_en_zh_reader.rendering.page_geometry import A4_WIDTH_MPT

from .conftest import build_case


def _unit_blocks(layout: dict[str, object]) -> list[dict[str, object]]:
    return [
        block
        for page in layout["pages"]
        for block in page["blocks"]
        if block["content_kind"] == "unit"
    ]


def test_mixed_first_page_mirrors_columns_and_omits_multicolumn_leaders(
    tmp_path: Path,
) -> None:
    case = build_case(tmp_path, "first-page-mixed")
    source_page = case.source["pages"][0]
    graph_page = case.frame_graph["pages"][0]
    native_frames = [
        frame for frame in graph_page["frames"] if frame["kind"] == "native"
    ]

    assert [frame["column_count"] for frame in native_frames] == [1, 2, 2]
    source_columns = {
        column["id"]: column
        for band in source_page["bands"]
        for column in band["columns"]
    }
    for frame in native_frames:
        source_column = source_columns[frame["source_column_id"]]
        assert frame["bbox_mpt"][0] == (A4_WIDTH_MPT + source_column["x_left_mpt"])

    multicolumn_bands = {
        band["id"] for band in source_page["bands"] if len(band["columns"]) > 1
    }
    multicolumn_units = {
        block["id"]
        for block in source_page["blocks"]
        if block["band_id"] in multicolumn_bands
        and block["translation_policy"] == "required"
    }
    multicolumn_blocks = [
        block
        for block in _unit_blocks(case.layout)
        if block["unit_id"] in multicolumn_units
    ]
    assert multicolumn_blocks
    assert all(
        block["selected_anchor"]["kind"] == "soft-y"
        for block in multicolumn_blocks
        if block["creates_anchor"]
    )
    leader_units = {
        route["unit_id"]
        for page in case.overlay_plan["pages"]
        for route in page["leader_routes"]
    }
    assert leader_units
    assert leader_units.isdisjoint(multicolumn_units)


def test_overflow_preserves_size_order_and_complete_translation(tmp_path: Path) -> None:
    long_text = "完整译文保持事实、程度、逻辑、数据与专业术语准确。" * 40
    case = build_case(
        tmp_path,
        "long-translation",
        translation_overrides={
            "p1-body-1": long_text,
            "p1-body-2": long_text,
            "p1-body-3": long_text,
        },
    )

    assert case.layout["solver_trace"]["continuation_page_count"] > 0
    target_by_unit = {
        unit["unit_id"]: unit["chinese_text"] for unit in case.translation["units"]
    }
    flow_by_unit = {flow["unit_id"]: flow for flow in case.frame_graph["unit_flows"]}
    blocks = _unit_blocks(case.layout)

    seen: list[str] = []
    for block in blocks:
        if block["unit_id"] not in seen:
            seen.append(block["unit_id"])
    assert seen == [unit["unit_id"] for unit in case.translation["units"]]

    for unit_id, target in target_by_unit.items():
        flow = flow_by_unit[unit_id]
        lines = flow["lines"]
        assert lines[0]["target_start"] == 0
        assert lines[-1]["target_end"] == len(target)
        assert all(
            left["target_end"] == right["target_start"]
            for left, right in zip(lines, lines[1:], strict=False)
        )
        assert all(
            line["text"] == target[line["target_start"] : line["target_end"]].strip()
            for line in lines
        )

        placed = [block for block in blocks if block["unit_id"] == unit_id]
        assert placed[0]["line_start"] == 0
        assert placed[-1]["line_end"] == flow["line_count"]
        assert all(
            left["line_end"] == right["line_start"]
            for left, right in zip(placed, placed[1:], strict=False)
        )
        assert all(block["style"] == flow["style"] for block in placed)
        assert all(
            line["line_height_mpt"] == flow["style"]["line_height_mpt"]
            for block in placed
            for line in block["lines"]
        )
