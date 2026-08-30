# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.continuation_dp import (
    FlowDpComplexityError,
    FlowDpLimits,
    FlowSpec,
    FrameSlot,
    solve_fixed_slots,
)
from academic_pdf_en_zh_reader.layout.solver import (
    LayoutInfeasibleError,
    solve_layout,
)


def _equivalent_history_case(
    *,
    max_dp_states: int,
) -> tuple[tuple[FlowSpec, ...], tuple[FrameSlot, ...], FlowDpLimits]:
    flows: list[FlowSpec] = []
    slots: list[FrameSlot] = []
    for page_number in range(1, 9):
        native_frame_ids = tuple(
            f"page-{page_number:02d}-column-{column_index}" for column_index in range(3)
        )
        for flow_index in range(2):
            flow_id = f"page-{page_number:02d}-flow-{flow_index}"
            flows.append(
                FlowSpec(
                    content_id=flow_id,
                    unit_id=flow_id,
                    role="body",
                    lines=({"line_height_mpt": 10},),
                    legal_breaks=(),
                    allowed_native_frame_ids=native_frame_ids,
                    continuation_owner_page_number=page_number,
                    gap_before_mpt=0,
                )
            )
        for column_index, native_frame_id in enumerate(native_frame_ids):
            slots.append(
                FrameSlot(
                    instance_id=native_frame_id,
                    template_frame_id=native_frame_id,
                    native_frame_id=native_frame_id,
                    source_page_number=page_number,
                    page_kind="native",
                    continuation_index=0,
                    capacity_mpt=100,
                    page_height_mpt=100,
                    page_top_reserve_mpt=0,
                    bbox_mpt=(0, 0, 100, 100),
                    text_left_mpt=0,
                    text_right_mpt=100,
                    source_band_id=f"page-{page_number:02d}-band",
                    source_column_id=native_frame_id,
                    band_index=0,
                    column_index=column_index,
                    column_count=3,
                    width_ratio_ppm=333_333,
                )
            )
    limits = FlowDpLimits(
        max_dp_states=max_dp_states,
        max_dp_transitions=10_000,
        max_continuation_distributions=1,
        max_continuation_pages_per_source_page=1,
        max_total_continuation_pages=1,
        min_lines_before_break=1,
        min_lines_after_break=1,
        heading_with_next_lines=1,
        vertical_padding_mpt=0,
        band_gap_mpt=0,
    )
    return tuple(flows), tuple(slots), limits


def test_fixed_slot_dp_merges_equivalent_completed_geometry_histories() -> None:
    flows, slots, limits = _equivalent_history_case(max_dp_states=300)

    plan = solve_fixed_slots(flows, slots, limits=limits)

    assert plan.geometry_height_vector_mpt == (10,) * 8
    assert [placement.slot_index for placement in plan.placements] == [
        slot_index
        for page_start in range(0, 24, 3)
        for slot_index in (page_start, page_start + 1)
    ]


def test_fixed_slot_dp_keeps_fail_closed_state_limit_after_frontier_merge() -> None:
    flows, slots, limits = _equivalent_history_case(max_dp_states=32)

    with pytest.raises(FlowDpComplexityError) as captured:
        solve_fixed_slots(flows, slots, limits=limits)

    assert captured.value.limit_name == "max_dp_states"
    assert captured.value.observed == 33
    assert captured.value.maximum == 32


def test_native_feasible_layout_never_activates_continuation(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "fits", "line_count": 3}])

    layout = solve_layout(graph)

    assert layout["solver_trace"]["native_status"] == "solved"
    assert layout["solver_trace"]["continuation_reason"] is None
    assert layout["solver_trace"]["continuation_page_count"] == 0
    assert [page["page_kind"] for page in layout["pages"]] == ["native"]
    assert layout["pages"][0]["continuation_label"] is None
    assert layout["pages"][0]["bands"][0]["solved_top_offset_mpt"] == 10_000


def test_continuation_cost_prefers_sentence_then_earliest_stable_break(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {
                "unit_id": "long",
                "line_count": 5,
                "legal_breaks": [
                    (2, "sentence"),
                    (3, "line"),
                ],
            }
        ]
    )

    layout = solve_layout(graph)
    blocks = [block for page in layout["pages"] for block in page["blocks"]]

    assert layout["solver_trace"]["native_status"] == "exhausted"
    assert layout["solver_trace"]["native_window_and_band_search_exhausted"]
    assert layout["solver_trace"]["native_exhaustion_reason"] == (
        "native-fixed-slot-and-band-height-state-space-exhausted"
    )
    assert layout["solver_trace"]["continuation_page_count"] == 1
    assert layout["solver_trace"]["split_count"] == 1
    assert layout["solver_trace"]["break_quality_cost"] == 1
    assert [(block["line_start"], block["line_end"]) for block in blocks] == [
        (0, 2),
        (2, 5),
    ]
    assert [block["creates_anchor"] for block in blocks] == [True, False]

    continuation = layout["pages"][1]
    header = graph["continuation_header"]
    label = continuation["continuation_label"]
    assert (
        continuation["bands"][0]["solved_top_offset_mpt"]
        >= header["reserve_height_mpt"]
    )
    assert label["header_hash"] == header["header_hash"]
    assert label["runs"] == header["runs"]
    assert label["label_hash"] == sha256_canonical(
        {key: value for key, value in label.items() if key != "label_hash"}
    )


def test_continuation_header_reserve_enters_dp_and_every_band_solution(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "eight-lines", "line_count": 8}])

    layout = solve_layout(graph)

    assert layout["solver_trace"]["continuation_page_count"] == 2
    reserve = graph["continuation_header"]["reserve_height_mpt"]
    continuation_pages = [
        page for page in layout["pages"] if page["page_kind"] == "continuation"
    ]
    assert len(continuation_pages) == 2
    assert all(
        band["solved_top_offset_mpt"] >= reserve
        for page in continuation_pages
        for band in page["bands"]
    )
    assert all(
        block["bbox_mpt"][3] <= page["page_height_mpt"] - reserve
        for page in continuation_pages
        for block in page["blocks"]
    )


def test_terminal_heading_does_not_require_a_nonexistent_following_flow(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [{"unit_id": "references-heading", "role": "heading", "line_count": 1}]
    )

    layout = solve_layout(graph)

    assert layout["solver_trace"]["native_status"] == "solved"
    assert layout["solver_trace"]["continuation_page_count"] == 0
    assert [block["unit_id"] for block in layout["pages"][0]["blocks"]] == [
        "references-heading"
    ]


def test_widow_orphan_and_heading_with_next_are_hard_infeasibility(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    orphan_graph = frame_graph_factory(
        [{"unit_id": "three-lines", "line_count": 3, "line_height_mpt": 40_000}]
    )
    with pytest.raises(LayoutInfeasibleError):
        solve_layout(orphan_graph)

    heading_graph = frame_graph_factory(
        [
            {"unit_id": "heading", "role": "heading", "line_count": 1},
            {"unit_id": "body", "line_count": 3, "line_height_mpt": 30_000},
        ]
    )
    with pytest.raises(LayoutInfeasibleError):
        solve_layout(heading_graph)
