# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Reading behavior, independent of the English page's column geometry."""

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.layout.frame_graph import build_frame_graph
from academic_pdf_en_zh_reader.layout.solver import solve_layout
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)
from scripts.generate_synthetic_fixtures import load_fixture_spec
from tests.integration.conftest import SPECS, _artifact_inputs


@pytest.fixture
def inputs():
    spec = load_fixture_spec(SPECS / "first-page-mixed.json")
    source, units, translation, _ = _artifact_inputs(spec, source_sha256="a" * 64)
    return source, units, translation


def graph_for(inputs):
    return build_frame_graph(
        *inputs,
        style_contract=build_style_contract([FontSizeSample(10_000, 100)]),
        resolver=FontRunResolver(load_font_registry()),
    )


def test_front_matter_spans_and_body_has_fixed_two_columns(inputs):
    graph = graph_for(inputs)
    assert graph["schema_version"] == "2.0.0"
    for flow in graph["unit_flows"]:
        expected = 1 if flow["role"] in {"title", "abstract", "keywords"} else 2
        assert flow["column_count"] == expected
    layout = solve_layout(graph)
    assert all("selected_anchor" not in b for p in layout["pages"] for b in p["blocks"])
    assert all(p["continuation_label"] is None for p in layout["pages"])


def test_long_paragraph_flows_without_losing_text_or_changing_size(inputs):
    source, units, translation = deepcopy(inputs)
    body_id = next(u["id"] for u in units["units"] if u["role"] == "body")
    target = next(t for t in translation["units"] if t["unit_id"] == body_id)
    target["chinese_text"] = "连续跨页的完整中文段落保留所有实验条件与结果。" * 180
    graph = graph_for((source, units, translation))
    layout = solve_layout(graph)
    blocks = [
        b
        for p in layout["pages"]
        for b in p["blocks"]
        if b["unit_id"] == target["unit_id"]
    ]
    assert len({b["target_page_number"] for b in blocks}) > 1
    assert (
        "".join(line["text"] for b in blocks for line in b["lines"])
        == target["chinese_text"]
    )
    assert {b["style"]["size_mpt"] for b in blocks} == {11_000}
    assert all(
        p["source_page_number"] is None for p in layout["pages"][len(source["pages"]) :]
    )


def test_long_abstract_stays_full_width_until_complete(inputs):
    source, units, translation = deepcopy(inputs)
    abstract_ids = {u["id"] for u in units["units"] if u["role"] == "abstract"}
    for item in translation["units"]:
        if item["unit_id"] in abstract_ids:
            item["chinese_text"] = "摘要内容完整跨页保留。" * 500
    layout = solve_layout(graph_for((source, units, translation)))
    blocks = [
        b for p in layout["pages"] for b in p["blocks"] if b["unit_id"] in abstract_ids
    ]
    assert len({b["target_page_number"] for b in blocks}) > 1
    assert all(b["column_count"] == 1 for b in blocks)


def test_targeted_review_records_actual_subset_and_allows_same_agent(inputs):
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
    from academic_pdf_en_zh_reader.review.review_validation import validate_review

    translation = inputs[2]
    ids = [translation["units"][0]["unit_id"]]
    review = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "targeted",
        "translator_id": translation["translator_id"],
        "reviewer_id": translation["translator_id"],
        "reviewed_unit_ids": ids,
        "issues": [],
        "final_status": "passed",
    }
    assert validate_review(translation, review).reviewed_unit_ids == tuple(ids)


def test_layout_determinism_and_geometry_binding(inputs):
    from academic_pdf_en_zh_reader.layout.solver import (
        validate_layout_against_frame_graph,
    )

    graph = graph_for(inputs)
    layout = solve_layout(graph)
    assert layout == solve_layout(graph)
    layout["pages"][0]["blocks"][0]["lines"][0]["baseline_y_mpt"] -= 1000
    with pytest.raises(ValueError):
        validate_layout_against_frame_graph(graph, layout)


def test_limits_and_rehashed_truncation_cannot_drop_required_text(inputs):
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
    from academic_pdf_en_zh_reader.layout.solver import LayoutLimits

    graph = graph_for(inputs)
    with pytest.raises(ValueError):
        solve_layout(graph, limits=LayoutLimits(max_lines=1))
    flow = graph["unit_flows"][0]
    flow["lines"] = []
    flow["line_count"] = 0
    flow["line_sequence_hash"] = sha256_canonical(
        {
            "style": flow["style"],
            "lines": [],
            "composite_segments": flow["composite_segments"],
        }
    )
    with pytest.raises(ValueError):
        solve_layout(graph)


def test_different_source_columns_do_not_change_chinese_reading_width(inputs):
    graph = graph_for(inputs)
    frames = solve_layout(graph)["pages"][0]["frames"]
    widths = {
        f["text_right_mpt"] - f["text_left_mpt"]
        for f in frames
        if f["column_count"] == 2
    }
    assert len(widths) == 1
    assert all(f["bbox_mpt"][0] >= 595276 for f in frames)


def test_spacing_and_footnote_keep_fixed_readable_type():
    style = build_style_contract(())
    assert style.style_for("body").size_mpt == 11000
    assert style.style_for("auxiliary").size_mpt == 10000
    assert style.style_for("footnote").size_mpt >= 10000


def test_heading_moves_with_two_actual_larger_body_lines(inputs):
    from academic_pdf_en_zh_reader.layout.solver import DEFAULT_LAYOUT_LIMITS, _paginate

    graph = graph_for(inputs)
    body = deepcopy(next(f for f in graph["unit_flows"] if f["role"] == "body"))
    heading = deepcopy(next(f for f in graph["unit_flows"] if f["role"] == "heading"))
    prefix = deepcopy(body)
    prefix["unit_id"] = "prefix"
    # Isolate the pagination boundary using measured body lines: 47 * 15.4 pt.
    prefix["lines"] = [deepcopy(body["lines"][0]) for _ in range(47)]
    prefix["line_count"] = 47
    body["lines"] = [deepcopy(body["lines"][0]) for _ in range(3)]
    body["line_count"] = 3
    graph["unit_flows"] = [prefix, heading, body]
    graph["auxiliary_flows"] = []
    graph["flow_order"] = [f["unit_id"] for f in graph["unit_flows"]]
    page = _paginate(graph, DEFAULT_LAYOUT_LIMITS)[0]
    heading_block = next(
        b for b in page["blocks"] if b["unit_id"] == heading["unit_id"]
    )
    body_block = next(b for b in page["blocks"] if b["unit_id"] == body["unit_id"])
    assert heading_block["column_index"] == body_block["column_index"] == 1
    assert len(body_block["lines"]) >= 2
    assert heading_block["bbox_mpt"][3] == 841_890 - 32_000


def test_body_columns_start_at_equal_height_after_front_matter(inputs):
    from academic_pdf_en_zh_reader.layout.solver import DEFAULT_LAYOUT_LIMITS, _paginate

    graph = graph_for(inputs)
    title = deepcopy(next(f for f in graph["unit_flows"] if f["role"] == "title"))
    body = deepcopy(next(f for f in graph["unit_flows"] if f["role"] == "body"))
    body["lines"] = [deepcopy(body["lines"][0]) for _ in range(60)]
    body["line_count"] = 60
    graph["unit_flows"] = [title, body]
    graph["auxiliary_flows"] = []
    graph["flow_order"] = [title["unit_id"], body["unit_id"]]
    page = _paginate(graph, DEFAULT_LAYOUT_LIMITS)[0]
    blocks = [b for b in page["blocks"] if b["unit_id"] == body["unit_id"]]
    assert [b["column_index"] for b in blocks] == [0, 1]
    assert blocks[0]["bbox_mpt"][3] == blocks[1]["bbox_mpt"][3]


def test_warning_space_is_reserved_before_filling_both_columns():
    from tests.rendering.test_text_styles import build_render_fixture

    *_, graph, layout = build_render_fixture(
        chinese_text="甲乙丙丁戊己" * 160,
        include_red=False,
        include_ambiguity=False,
        include_auxiliary=False,
    )
    single_column_capacity = (
        841_890 - 2 * 32_000 - graph["warning_height_mpt"] - 3_500
    ) // 15_400
    assert (
        single_column_capacity
        < graph["unit_flows"][0]["line_count"]
        <= 2 * single_column_capacity
    )
    assert len(layout["pages"]) == 1
    page = layout["pages"][0]
    assert {b["column_index"] for b in page["blocks"]} == {0, 1}
    assert (
        min(b["bbox_mpt"][1] for b in page["blocks"]) >= page["warning_region_mpt"][1]
    )
