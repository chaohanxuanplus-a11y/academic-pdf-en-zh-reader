# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from .conftest import build_case


def _unit_blocks(layout: dict[str, object]) -> list[dict[str, object]]:
    return [
        block
        for page in layout["pages"]
        for block in page["blocks"]
        if block["content_kind"] == "unit"
    ]


def test_mixed_first_page_uses_continuous_two_columns_without_leaders(tmp_path):
    case = build_case(tmp_path, "first-page-mixed")
    assert all(
        f["column_count"]
        == (1 if f["role"] in {"title", "abstract", "keywords"} else 2)
        for f in case.frame_graph["unit_flows"]
    )
    assert all("leader_routes" not in p for p in case.overlay_plan["pages"])
    assert [
        p["page_number"] for p in case.overlay_plan["pages"] if p["brand_block"]
    ] == [len(case.layout["pages"])]


def test_overflow_preserves_size_order_and_complete_translation(tmp_path: Path) -> None:
    long_text = "完整译文保持事实、程度、逻辑、数据与专业术语准确。" * 100
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
