# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.layout.solver import (
    LayoutComplexityError,
    LayoutLimits,
    LayoutSolverError,
    solve_layout,
    validate_layout_against_frame_graph,
)


def test_same_solver_input_hash_produces_byte_identical_layout(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {"unit_id": "a", "anchor_offset_mpt": 30_000},
            {"unit_id": "b", "anchor_offset_mpt": 30_000},
        ]
    )

    first = solve_layout(graph)
    second = solve_layout(graph)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_breakpoint_and_dp_limits_fail_without_greedy_fallback(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "limited", "line_count": 5}])
    limits = LayoutLimits(max_breakpoints_per_unit=2)

    with pytest.raises(LayoutComplexityError) as raised:
        solve_layout(graph, limits=limits)

    assert raised.value.code == "LAYOUT_COMPLEXITY_LIMIT"
    assert raised.value.limit_name == "max_breakpoints_per_unit"
    assert raised.value.observed == 4
    assert raised.value.maximum == 2


def test_solver_hash_is_recomputed_from_graph_spacing_and_solver_policy(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "hash-bound"}])
    baseline = solve_layout(graph)

    changed_graph = frame_graph_factory([{"unit_id": "hash-bound"}])
    changed_graph["flow_spacing"]["block_gap_mpt"] = 4_001
    spacing_changed = solve_layout(changed_graph)
    policy_changed = solve_layout(graph, limits=LayoutLimits(band_gap_mpt=4_001))

    assert baseline["solver_input_hash"] != spacing_changed["solver_input_hash"]
    assert baseline["solver_input_hash"] != policy_changed["solver_input_hash"]
    assert baseline["solver_policy"] == {
        key: value for key, value in asdict(LayoutLimits()).items()
    }

    changed_anchor = deepcopy(graph)
    changed_anchor["unit_flows"][0]["anchor"]["source_visual_center_offset_mpt"] += 1
    graph_changed = solve_layout(changed_anchor)
    assert baseline["frame_graph_input_hash"] == graph_changed["frame_graph_input_hash"]
    assert baseline["frame_graph_hash"] != graph_changed["frame_graph_hash"]
    assert baseline["solver_input_hash"] != graph_changed["solver_input_hash"]


def test_explicit_solver_hash_is_only_an_expected_value(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "expected-hash"}])
    computed = solve_layout(graph)["solver_input_hash"]

    assert (
        solve_layout(graph, expected_solver_input_hash=computed)["solver_input_hash"]
        == computed
    )
    with pytest.raises(LayoutSolverError, match="does not match"):
        solve_layout(graph, expected_solver_input_hash="f" * 64)


def test_framegraph_rebind_rejects_a_mirrored_frame_substitution(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory([{"unit_id": "rebind"}])
    layout = solve_layout(graph)
    tampered = deepcopy(layout)
    tampered["pages"][0]["frames"][0]["native_frame_id"] = "foreign-frame"

    with pytest.raises(LayoutSolverError, match="mirrored frame geometry"):
        validate_layout_against_frame_graph(graph, tampered)
