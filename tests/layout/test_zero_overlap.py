# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.solver import (
    LayoutLimits,
    LayoutSolverError,
    _solve_chain,
    solve_layout,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


def test_overlapping_first_line_targets_reflow_without_changing_frozen_lines(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {"unit_id": "unit-a", "anchor_offset_mpt": 35_000},
            {"unit_id": "unit-b", "anchor_offset_mpt": 35_000},
        ]
    )

    layout = solve_layout(graph)
    validate_artifact("layout", layout)

    blocks = layout["pages"][0]["blocks"]
    assert len(blocks) == 2
    assert blocks[0]["preferred_top_offset_mpt"] == 25_000
    assert blocks[1]["bbox_mpt"][3] <= blocks[0]["bbox_mpt"][1]
    flows = {flow["unit_id"]: flow for flow in graph["unit_flows"]}
    for block in blocks:
        flow = flows[block["unit_id"]]
        assert block["style"] == flow["style"]
        assert block["line_sequence_hash"] == flow["line_sequence_hash"]
        source_line = flow["lines"][block["line_start"]]
        output_line = block["lines"][0]
        for field in (
            "index",
            "target_start",
            "target_end",
            "text",
            "style_id",
            "width_mpt",
            "line_height_mpt",
            "ascent_mpt",
            "descent_mpt",
            "runs",
            "line_box_hash",
        ):
            assert output_line[field] == source_line[field]

    attempts = layout["solver_trace"]["window_attempts"]
    column_attempts = [
        attempt for attempt in attempts if attempt["scope_kind"] == "column"
    ]
    accepted = [
        attempt for attempt in column_attempts if attempt["result"] == "accepted"
    ]
    assert accepted
    assert all(type(attempt["minimum_d_mpt"]) is int for attempt in accepted)

    page = layout["pages"][0]
    assert len(page["bands"]) == 1
    assert len(page["frames"]) == 1
    frame = page["frames"][0]
    assert all(block["frame_id"] == frame["id"] for block in blocks)
    assert all(
        frame["bbox_mpt"][0] <= block["bbox_mpt"][0]
        and block["bbox_mpt"][2] <= frame["bbox_mpt"][2]
        and frame["bbox_mpt"][1] <= block["bbox_mpt"][1]
        and block["bbox_mpt"][3] <= frame["bbox_mpt"][3]
        for block in blocks
    )


def test_custom_frame_graph_spacing_controls_real_band_height_and_block_gap(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "a"}, {"unit_id": "b"}])
    graph["flow_spacing"]["vertical_padding_mpt"] = 7_000
    graph["flow_spacing"]["block_gap_mpt"] = 9_000
    header = graph["continuation_header"]
    header["top_inset_mpt"] = 7_000
    header["reserve_height_mpt"] = (
        header["top_inset_mpt"] + header["line_height_mpt"] + header["gap_after_mpt"]
    )
    header["header_hash"] = sha256_canonical(
        {key: value for key, value in header.items() if key != "header_hash"}
    )

    layout = solve_layout(graph)
    blocks = layout["pages"][0]["blocks"]

    assert blocks[1]["solved_top_offset_mpt"] >= (
        blocks[0]["solved_top_offset_mpt"] + 20_000 + 9_000
    )
    assert (
        layout["solver_trace"]["band_heights"][0]["actual_content_height_mpt"]
        == 20_000 + 9_000 + 20_000 + 2 * 7_000
    )


def test_unsupported_flow_spacing_version_fails_closed(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "unsupported"}])
    graph["flow_spacing"]["config_version"] = 2

    with pytest.raises(LayoutSolverError, match="flow_spacing config_version"):
        solve_layout(graph)


def test_multiband_window_expands_and_keeps_outside_coordinates_frozen() -> None:
    attempts: list[dict[str, object]] = []
    solved = _solve_chain(
        chain_id="seven-bands",
        scope_kind="band",
        preferred_tops=(0, 10, 20, 30, 40, 50, 60),
        heights=(10, 10, 10, 30, 10, 10, 10),
        gaps=(0, 0, 0, 0, 0, 0, 0),
        weights=(1, 1, 1, 1, 1, 1, 1),
        frame_top=0,
        frame_bottom=90,
        limits=LayoutLimits(),
        attempts=attempts,
    )

    assert [attempt["release_stage"] for attempt in attempts] == [
        "current",
        "neighbors",
        "radius",
    ]
    assert [attempt["result"] for attempt in attempts] == [
        "rejected",
        "rejected",
        "accepted",
    ]
    assert solved[:2] == (0, 10)
    assert all(
        attempt["frozen_before_hash"] == attempt["frozen_after_hash"]
        for attempt in attempts
    )


def test_figure_note_stays_between_its_caption_and_the_following_unit(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "caption"}, {"unit_id": "following"}])
    note_style = {
        "style_id": "typography-v1:auxiliary:body:10000:20000",
        "semantic_role": "auxiliary",
        "font_role": "body",
        "size_mpt": 10_000,
        "line_height_mpt": 20_000,
    }
    note_line = deepcopy(graph["unit_flows"][0]["lines"][0])
    note_line["style_id"] = note_style["style_id"]
    note_line["line_box_hash"] = sha256_canonical(
        {
            "line_box_contract_version": "1.0.0",
            **{
                key: value for key, value in note_line.items() if key != "line_box_hash"
            },
        }
    )
    graph["figure_note_flows"] = [
        {
            "id": "figure-note:caption:0000",
            "unit_id": "caption",
            "frame_id": "native-0",
            "note_index": 0,
            "line_count": 1,
            "style": note_style,
            "line_sequence_hash": sha256_canonical(
                {"style": note_style, "lines": [note_line]}
            ),
            "lines": [note_line],
        }
    ]

    layout = solve_layout(graph)

    assert [block["content_id"] for block in layout["pages"][0]["blocks"]] == [
        "caption",
        "figure-note:caption:0000",
        "following",
    ]


def test_real_line_heights_not_task13_unsplit_diagnostic_control_capacity(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {
                "unit_id": "dense-unit",
                "line_count": 5,
                "legal_breaks": [(2, "sentence"), (3, "line")],
            }
        ],
        diagnostic_height_mpt=1,
    )

    layout = solve_layout(graph)

    assert layout["solver_trace"]["continuation_page_count"] == 1
    band_trace = layout["solver_trace"]["band_heights"][0]
    assert band_trace["diagnostic_initial_unsplit_height_mpt"] == 1
    assert band_trace["actual_content_height_mpt"] > 1
