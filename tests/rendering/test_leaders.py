# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

import academic_pdf_en_zh_reader.rendering.overlay_plan as overlay_plan_module
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.contracts import (
    LeaderReflowRequired,
    OverlayPlanError,
    OverlayPlanLimits,
)
from academic_pdf_en_zh_reader.rendering.leaders import (
    LeaderRequest,
    freeze_leader_routes,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from tests.rendering.test_text_styles import build_render_fixture


def test_single_column_leader_uses_exact_frozen_endpoints() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )

    plan = build_overlay_plan(source, graph, layout, annotations)
    [leader] = plan["pages"][0]["leader_routes"]
    first_block = next(
        block
        for block in layout["pages"][0]["blocks"]
        if block["content_kind"] == "unit"
    )
    first_line = first_block["lines"][0]
    expected_target_y = (
        first_line["baseline_y_mpt"]
        + (first_line["ascent_mpt"] + first_line["descent_mpt"]) // 2
    )

    assert (
        leader["points_mpt"][0] == first_block["selected_anchor"]["source_endpoint_mpt"]
    )
    assert leader["points_mpt"][-1] == [first_line["x_mpt"], expected_target_y]
    assert leader["route_kind"] == "rounded-three-segment"
    assert leader["color_hex"] == "#9A9A9A"
    assert leader["route_hash"] == sha256_canonical(
        {key: value for key, value in leader.items() if key != "route_hash"}
    )

    [horizontal] = freeze_leader_routes(
        (
            LeaderRequest(
                leader_id="horizontal",
                unit_id="unit-horizontal",
                source_block_id="source-horizontal",
                source_endpoint_mpt=(110_000, 700_000),
                target_endpoint_mpt=(640_000, 700_000),
                target_obstacle_ids=(),
            ),
        ),
        source_obstacles=(),
        right_obstacles=(),
        page_bbox_mpt=(0, 0, 1_190_551, 841_890),
        right_panel_left_mpt=595_276,
    )
    assert horizontal["route_kind"] == "horizontal"
    assert horizontal["points_mpt"] == [[110_000, 700_000], [640_000, 700_000]]


def test_nonzero_crop_is_parent_recomputed_and_obstacle_hash_is_frozen() -> None:
    base = build_render_fixture()
    shifted = build_render_fixture(crop_left=10_000, crop_bottom=20_000)
    base_plan = build_overlay_plan(base[0], base[5], base[6], base[4])
    shifted_plan = build_overlay_plan(shifted[0], shifted[5], shifted[6], shifted[4])

    assert shifted_plan["pages"][0]["leader_routes"][0]["points_mpt"][0] == [
        110_000,
        715_000,
    ]
    assert shifted_plan["source_hash"] != base_plan["source_hash"]
    assert (
        shifted_plan["pages"][0]["source_obstacles_hash"]
        == (base_plan["pages"][0]["source_obstacles_hash"])
    )


def test_graphic_or_source_block_collision_requires_reflow_before_canvas() -> None:
    fixture = build_render_fixture(graphic_bbox=[200_000, 710_000, 300_000, 720_000])
    source, _units, _translation, _review, annotations, graph, layout = fixture

    with pytest.raises(LeaderReflowRequired) as caught:
        build_overlay_plan(source, graph, layout, annotations)

    assert caught.value.code == "LEADER_REFLOW_REQUIRED"


def test_rounded_three_segment_lane_is_finite_stable_and_reusable() -> None:
    requests = (
        LeaderRequest(
            leader_id="leader-a",
            unit_id="unit-a",
            source_block_id="source-a",
            source_endpoint_mpt=(110_000, 720_000),
            target_endpoint_mpt=(640_000, 680_000),
            target_obstacle_ids=(),
        ),
        LeaderRequest(
            leader_id="leader-b",
            unit_id="unit-b",
            source_block_id="source-b",
            source_endpoint_mpt=(110_000, 660_000),
            target_endpoint_mpt=(640_000, 640_000),
            target_obstacle_ids=(),
        ),
    )

    first = freeze_leader_routes(
        requests,
        source_obstacles=(),
        right_obstacles=(),
        page_bbox_mpt=(0, 0, 1_190_551, 841_890),
        right_panel_left_mpt=595_276,
    )
    second = freeze_leader_routes(
        requests,
        source_obstacles=(),
        right_obstacles=(),
        page_bbox_mpt=(0, 0, 1_190_551, 841_890),
        right_panel_left_mpt=595_276,
    )

    assert first == second
    assert all(route["route_kind"] == "rounded-three-segment" for route in first)
    assert all(len(route["points_mpt"]) == 4 for route in first)
    assert {route["lane_index"] for route in first} == {0}
    assert all(route["corner_radius_mpt"] > 0 for route in first)


def test_more_than_32_disjoint_routes_safely_reuse_a_finite_lane() -> None:
    requests = tuple(
        LeaderRequest(
            leader_id=f"leader-{index:02d}",
            unit_id=f"unit-{index:02d}",
            source_block_id=f"source-{index:02d}",
            source_endpoint_mpt=(110_000, 820_000 - index * 19_000),
            target_endpoint_mpt=(640_000, 817_000 - index * 19_000),
            target_obstacle_ids=(),
        )
        for index in range(40)
    )

    routes = freeze_leader_routes(
        requests,
        source_obstacles=(),
        right_obstacles=(),
        page_bbox_mpt=(0, 0, 1_190_551, 841_890),
        right_panel_left_mpt=595_276,
    )

    assert len(routes) == 40
    assert {route["lane_index"] for route in routes} == {0}


def test_visual_order_prevents_identifier_order_from_blocking_nested_routes() -> None:
    """Real page-one geometry is routable despite non-visual unit ID ordering."""

    requests = (
        LeaderRequest(
            leader_id="leader:p1-r12-heading-346-354",
            unit_id="p1-r12-heading-346-354",
            source_block_id="source-heading",
            source_endpoint_mpt=(66_320, 570_927),
            target_endpoint_mpt=(632_232, 731_116),
            target_obstacle_ids=(),
        ),
        LeaderRequest(
            leader_id="leader:p1-r13-abstract-354-2563",
            unit_id="p1-r13-abstract-354-2563",
            source_block_id="source-abstract",
            source_endpoint_mpt=(553_016, 553_385),
            target_endpoint_mpt=(632_232, 707_788),
            target_obstacle_ids=(),
        ),
        LeaderRequest(
            leader_id="leader:p1-r15-keywords-2604-2715",
            unit_id="p1-r15-keywords-2604-2715",
            source_block_id="source-keywords",
            source_endpoint_mpt=(404_138, 359_377),
            target_endpoint_mpt=(632_232, 464_783),
            target_obstacle_ids=(),
        ),
        LeaderRequest(
            leader_id="leader:p1-r2-title-45-82",
            unit_id="p1-r2-title-45-82",
            source_block_id="source-title",
            source_endpoint_mpt=(420_335, 665_805),
            target_endpoint_mpt=(632_232, 760_224),
            target_obstacle_ids=(),
        ),
    )

    routes = freeze_leader_routes(
        requests,
        source_obstacles=(),
        right_obstacles=(),
        page_bbox_mpt=(0, 0, 1_190_551, 841_890),
        right_panel_left_mpt=595_276,
    )

    assert [route["leader_id"] for route in routes] == [
        "leader:p1-r2-title-45-82",
        "leader:p1-r12-heading-346-354",
        "leader:p1-r13-abstract-354-2563",
        "leader:p1-r15-keywords-2604-2715",
    ]
    assert [route["lane_index"] for route in routes] == [0, 2, 4, 0]


def test_unavoidable_route_crossing_requires_reflow() -> None:
    requests = (
        LeaderRequest(
            leader_id="leader-a",
            unit_id="unit-a",
            source_block_id="source-a",
            source_endpoint_mpt=(110_000, 720_000),
            target_endpoint_mpt=(640_000, 680_000),
            target_obstacle_ids=(),
        ),
        LeaderRequest(
            leader_id="leader-b",
            unit_id="unit-b",
            source_block_id="source-b",
            source_endpoint_mpt=(110_000, 660_000),
            target_endpoint_mpt=(640_000, 700_000),
            target_obstacle_ids=(),
        ),
    )

    with pytest.raises(LeaderReflowRequired):
        freeze_leader_routes(
            requests,
            source_obstacles=(),
            right_obstacles=(),
            page_bbox_mpt=(0, 0, 1_190_551, 841_890),
            right_panel_left_mpt=595_276,
        )


def test_source_anchor_tamper_cannot_be_hidden_by_rehashing_source() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    tampered = deepcopy(source)
    tampered["pages"][0]["blocks"][0]["first_line_bbox_mpt"][0] += 1_000
    tampered["source_sha256"] = "e" * 64

    with pytest.raises(Exception) as caught:
        build_overlay_plan(tampered, graph, layout, annotations)

    assert getattr(caught.value, "code", None) == "SOURCE_ANCHOR_MISMATCH"


def test_leader_collision_budget_fails_before_unbounded_candidate_scan() -> None:
    request = LeaderRequest(
        leader_id="leader-budget",
        unit_id="unit-budget",
        source_block_id="source-budget",
        source_endpoint_mpt=(110_000, 720_000),
        target_endpoint_mpt=(640_000, 680_000),
        target_obstacle_ids=(),
    )
    obstacles = tuple(
        {
            "obstacle_id": f"obstacle-{index}",
            "kind": "test",
            "bbox_mpt": [900_000, 10_000 + index * 2, 900_001, 10_001 + index * 2],
        }
        for index in range(20)
    )

    with pytest.raises(OverlayPlanError) as caught:
        freeze_leader_routes(
            (request,),
            source_obstacles=obstacles,
            right_obstacles=(),
            page_bbox_mpt=(0, 0, 1_190_551, 841_890),
            right_panel_left_mpt=595_276,
            max_collision_checks=1,
        )

    assert caught.value.code == "PLAN_COMPLEXITY_LIMIT"


def test_selected_anchor_limit_is_checked_before_text_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    duplicated = deepcopy(layout)
    duplicated["pages"][0]["blocks"].append(
        deepcopy(duplicated["pages"][0]["blocks"][0])
    )
    monkeypatch.setattr(overlay_plan_module, "_validate_inputs", lambda *_args: None)
    monkeypatch.setattr(
        overlay_plan_module,
        "_validate_annotation_projection",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        overlay_plan_module,
        "freeze_page_text",
        lambda *_args, **_kwargs: pytest.fail("text freeze ran before anchor limit"),
    )

    with pytest.raises(OverlayPlanError) as caught:
        build_overlay_plan(
            source,
            graph,
            duplicated,
            annotations,
            limits=replace(OverlayPlanLimits(), max_leaders=1),
        )

    assert caught.value.code == "PLAN_COMPLEXITY_LIMIT"


def test_leader_owned_ids_are_indexed_once_instead_of_scanned_per_anchor() -> None:
    source, _units, _translation, _review, _annotations, _graph, layout = (
        build_render_fixture()
    )
    template = layout["pages"][0]["blocks"][0]
    page = {"blocks": []}
    run_key_reads = 0

    class CountingRun(dict[str, object]):
        def __getitem__(self, key: str) -> object:
            nonlocal run_key_reads
            run_key_reads += 1
            return super().__getitem__(key)

    runs = []
    for index in range(16):
        block = deepcopy(template)
        block["content_id"] = f"content-{index}"
        block["unit_id"] = f"unit-{index}"
        page["blocks"].append(block)
        runs.append(
            CountingRun(
                draw_run_id=f"run-{index}",
                content_id=block["content_id"],
                part_index=block["part_index"],
                line_index=block["lines"][0]["index"],
            )
        )
    source_pages, source_blocks, source_bands = overlay_plan_module._source_indexes(
        source
    )

    requests = overlay_plan_module._leader_requests(
        page,
        draw_runs=runs,
        underlines=(),
        source_pages=source_pages,
        source_blocks=source_blocks,
        source_bands=source_bands,
    )

    assert len(requests) == 16
    assert run_key_reads <= len(runs) * 4
