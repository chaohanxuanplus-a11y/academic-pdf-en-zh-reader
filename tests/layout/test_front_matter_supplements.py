# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Attached notes inherit the reading width, including across page boundaries."""

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialResult,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    build_final_annotated_frame_graph,
)
from academic_pdf_en_zh_reader.layout.solver import solve_layout
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import build_style_contract
from scripts.generate_synthetic_fixtures import load_fixture_spec
from tests.integration.conftest import SPECS, _artifact_inputs


def annotated_front_matter(*, long_role=None):
    source, units, translation, review = _artifact_inputs(
        load_fixture_spec(SPECS / "first-page-mixed.json"), source_sha256="a" * 64
    )
    style = build_style_contract(())
    candidates = []
    for unit in units["units"]:
        if unit["role"] not in {"title", "abstract", "keywords", "body"}:
            continue
        for number in range(2):
            expression = unit["source_text"][
                : len(unit["source_text"]) - number
            ].strip()
            candidates.append(
                TeachingCandidate(
                    key=f"{unit['id']}-{number}",
                    english_original=expression,
                    chinese_meaning="说明实验条件和术语含义并保留完整信息。"
                    * (250 if unit["role"] == long_role else 4),
                    occurrences=(
                        TeachingOccurrence(unit["id"], 0, len(expression), 0, 1),
                    ),
                    value_priority=500 - number,
                )
            )
    selected = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=tuple(candidates),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    )
    annotations = selected.to_artifact(
        units=units, translation=translation, review=review
    )
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=FontRunResolver(load_font_registry()),
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    return source, units, translation, review, annotations, graph


def test_notes_inherit_parent_width_and_are_actually_remeasured():
    *_, graph = annotated_front_matter()
    parents = {flow["unit_id"]: flow for flow in graph["unit_flows"]}
    assert {p["role"] for p in parents.values()} >= {"title", "abstract", "keywords"}
    for note in graph["auxiliary_flows"]:
        parent = parents[note["unit_id"]]
        expected = 1 if parent["role"] in {"title", "abstract", "keywords"} else 2
        assert note["column_count"] == parent["column_count"] == expected
        if expected == 1:
            assert max(line["width_mpt"] for line in note["lines"]) > 257_638
        assert note["style"]["semantic_role"] == "auxiliary"
    layout = solve_layout(graph)
    for page in layout["pages"]:
        frames = {f["id"]: f for f in page["frames"]}
        for block in page["blocks"]:
            if parents[block["unit_id"]]["column_count"] == 1:
                frame = frames[block["frame_id"]]
                assert frame["column_count"] == 1
                assert frame["column_index"] == 0
                assert frame["text_right_mpt"] - frame["text_left_mpt"] == 531_276


@pytest.mark.parametrize("role", ["title", "abstract", "keywords"])
def test_front_notes_cross_pages_without_narrowing_or_losing_content(role):
    *_, graph = annotated_front_matter(long_role=role)
    uid = next(f["unit_id"] for f in graph["unit_flows"] if f["role"] == role)
    layout = solve_layout(graph)
    for note in (f for f in graph["auxiliary_flows"] if f["unit_id"] == uid):
        parts = [
            b
            for p in layout["pages"]
            for b in p["blocks"]
            if b["content_id"] == note["id"]
        ]
        assert len({b["target_page_number"] for b in parts}) > 1
        assert all(b["column_count"] == 1 for b in parts)
        assert "".join(line["text"] for b in parts for line in b["lines"]) == (
            "".join(line["text"] for line in note["lines"])
        )


@pytest.mark.parametrize(
    "defect", ["wrong-width", "orphan", "before-parent", "split-group"]
)
def test_invalid_note_binding_or_reading_group_is_rejected(defect):
    *_, original = annotated_front_matter()
    graph = deepcopy(original)
    note = graph["auxiliary_flows"][0]
    if defect == "wrong-width":
        note["column_count"] = 2
    elif defect == "orphan":
        note["unit_id"] = "missing-parent"
    elif defect == "before-parent":
        graph["flow_order"].remove(note["id"])
        graph["flow_order"].insert(0, note["id"])
    else:
        graph["flow_order"].remove(note["id"])
        graph["flow_order"].append(note["id"])
    with pytest.raises(ValueError):
        validate_artifact("frame-graph", graph)
