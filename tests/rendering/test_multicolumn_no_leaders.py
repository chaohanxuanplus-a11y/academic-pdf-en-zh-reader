# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.rendering.contracts import OverlayPlanError
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from tests.rendering.test_text_styles import build_render_fixture


def test_multicolumn_soft_y_anchor_never_produces_a_leader() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(column_count=2)
    )
    unit_block = next(
        block
        for block in layout["pages"][0]["blocks"]
        if block["content_kind"] == "unit"
    )
    assert unit_block["selected_anchor"]["kind"] == "soft-y"

    plan = build_overlay_plan(source, graph, layout, annotations)

    assert plan["pages"][0]["leader_routes"] == []


def test_only_actual_first_part_can_claim_a_leader() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    tampered = deepcopy(layout)
    block = tampered["pages"][0]["blocks"][0]
    block["part_index"] = 1

    with pytest.raises(OverlayPlanError) as caught:
        build_overlay_plan(source, graph, tampered, annotations)

    assert caught.value.code == "LAYOUT_GRAPH_MISMATCH"


def test_soft_y_cannot_be_relabelled_as_a_leader_after_layout() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(column_count=2)
    )
    tampered = deepcopy(layout)
    unit_block = next(
        block
        for block in tampered["pages"][0]["blocks"]
        if block["content_kind"] == "unit"
    )
    unit_block["selected_anchor"]["kind"] = "leader"

    with pytest.raises(OverlayPlanError) as caught:
        build_overlay_plan(source, graph, tampered, annotations)

    assert caught.value.code == "LAYOUT_GRAPH_MISMATCH"
