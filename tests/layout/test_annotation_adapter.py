# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from inspect import Parameter, signature
from itertools import combinations

import pytest

import academic_pdf_en_zh_reader.annotations.validation as annotation_validation_module
import academic_pdf_en_zh_reader.layout.annotation_adapter as annotation_adapter_module
from academic_pdf_en_zh_reader.annotations.ambiguity import (
    AMBIGUITY_LABEL,
    AmbiguityOccurrence,
    build_ambiguity_marks,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.red_emphasis import SelectedRed
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialResult,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    validate_annotations_against_inputs,
)
from academic_pdf_en_zh_reader.extraction.unit_merge import build_semantic_units
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    DEFAULT_ANNOTATION_ADAPTER_LIMITS,
    AnnotationAdapterError,
    AnnotationAdapterLimits,
    build_final_annotated_frame_graph,
    make_annotation_layout_trial,
    validate_annotated_frame_graph_against_inputs,
)
from academic_pdf_en_zh_reader.layout.continuation_dp import (
    FlowPlacement,
    instantiate_slots,
)
from academic_pdf_en_zh_reader.layout.frame_graph import build_frame_graph
from academic_pdf_en_zh_reader.layout.solver import (
    DEFAULT_LAYOUT_LIMITS,
    LayoutSolverError,
    _content_records,
    _flow_spacing,
    _page_templates,
    _selected_anchors,
    solve_layout,
)
from academic_pdf_en_zh_reader.review.review_validation import make_ambiguity_key
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    _validate_auxiliary_flow_mapping,
    _validate_composite_flow,
    _validate_frame_lines,
)
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)


def _parents(
    *,
    chinese_text: str,
    column_right_mpt: int = 120_000,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    source_text = "Key term."
    unit_id = stable_source_id(
        page_number=1,
        reading_order=0,
        role="body",
        source_char_start=0,
        source_char_end=len(source_text),
    )
    source: dict[str, object] = {
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
                        "id": "body-band",
                        "y_top_mpt": 760_000,
                        "y_bottom_mpt": 200_000,
                        "columns": [
                            {
                                "id": "body-column",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": column_right_mpt,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": [
                    {
                        "id": unit_id,
                        "role": "body",
                        "translation_policy": "required",
                        "band_id": "body-band",
                        "column_id": "body-column",
                        "reading_order": 0,
                        "source_char_start": 0,
                        "source_char_end": len(source_text),
                        "text": source_text,
                        "bbox_mpt": [45_000, 700_000, 110_000, 730_000],
                        "first_line_bbox_mpt": [
                            45_000,
                            700_000,
                            110_000,
                            730_000,
                        ],
                        "confidence_ppm": 990_000,
                    }
                ],
            }
        ],
    }
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "d" * 64,
        "normalized_pdf_sha256": "e" * 64,
        "units": [
            {
                "id": unit_id,
                "role": "body",
                "reading_order": 0,
                "source_text": source_text,
                "confidence_ppm": 990_000,
                "fragments": [
                    {
                        "page_number": 1,
                        "block_id": unit_id,
                        "source_char_start": 0,
                        "source_char_end": len(source_text),
                    }
                ],
            }
        ],
    }
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": unit_id,
                "chinese_text": chinese_text,
                "spans": [],
                "terminology": [],
            }
        ],
    }
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": [unit_id],
        "issues": [],
        "final_status": "passed",
    }
    return source, units, translation, review, unit_id


def _typography():
    return (
        build_style_contract((FontSizeSample(10_000, 500),)),
        FontRunResolver(load_font_registry()),
    )


def _ambiguity_annotations(
    units: dict[str, object],
    translation: dict[str, object],
    review: dict[str, object],
    unit_id: str,
    *,
    style,
    red_items: tuple[SelectedRed, ...] = (),
) -> dict[str, object]:
    key = make_ambiguity_key(
        english_expression="Key term",
        syntactic_structure="attributive scientific noun phrase",
        candidate_meanings=("measurement label", "mechanistic construct"),
        disciplinary_context="experimental materials analysis",
        ambiguity_reason=(
            "The local sentence does not distinguish label from mechanism."
        ),
    )
    review["issues"] = [
        {
            "id": "ambiguity-1",
            "unit_id": unit_id,
            "severity": "unresolved_ambiguity",
            "status": "unresolved",
            "message": "The technical meaning remains unresolved.",
            "ambiguity_key": key,
        }
    ]
    marks = build_ambiguity_marks(
        units,
        translation,
        review,
        (AmbiguityOccurrence(str(key["id"]), unit_id, 0, 1),),
        style,
    )
    selected = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(),
        mandatory_items=(*red_items, *marks),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    )
    return selected.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )


def _cross_page_parents(
    *, chinese_text: str
) -> tuple[
    dict[str, object], dict[str, object], dict[str, object], dict[str, object], str
]:
    first_text = "These findings indicate"
    second_text = "that the effect is robust."

    def block(
        page_number: int,
        reading_order: int,
        text: str,
        bbox: list[int],
    ) -> dict[str, object]:
        identifier = stable_source_id(
            page_number=page_number,
            reading_order=reading_order,
            role="body",
            source_char_start=0,
            source_char_end=len(text),
        )
        return {
            "id": identifier,
            "role": "body",
            "translation_policy": "required",
            "band_id": f"p{page_number}-band",
            "column_id": f"p{page_number}-column",
            "reading_order": reading_order,
            "source_char_start": 0,
            "source_char_end": len(text),
            "text": text,
            "bbox_mpt": bbox,
            "first_line_bbox_mpt": bbox,
            "confidence_ppm": 990_000,
        }

    def page(page_number: int, row: dict[str, object]) -> dict[str, object]:
        return {
            "page_number": page_number,
            "media_box_mpt": [0, 0, 595_276, 841_890],
            "crop_box_mpt": [0, 0, 595_276, 841_890],
            "rotation_degrees": 0,
            "bands": [
                {
                    "id": f"p{page_number}-band",
                    "y_top_mpt": 810_000,
                    "y_bottom_mpt": 30_000,
                    "columns": [
                        {
                            "id": f"p{page_number}-column",
                            "x_left_mpt": 40_000,
                            "x_right_mpt": 555_276,
                        }
                    ],
                }
            ],
            "graphic_nodes": [],
            "blocks": [row],
        }

    source: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": "d" * 64,
        "normalized_pdf_sha256": "e" * 64,
        "pages": [
            page(1, block(1, 0, first_text, [40_000, 45_000, 555_000, 75_000])),
            page(2, block(2, 1, second_text, [40_000, 765_000, 555_000, 795_000])),
        ],
    }
    outcome = build_semantic_units(source)
    assert outcome.artifact is not None
    units = outcome.artifact
    unit_id = str(units["units"][0]["id"])
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": unit_id,
                "chinese_text": chinese_text,
                "spans": [],
                "terminology": [],
            }
        ],
    }
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": [unit_id],
        "issues": [],
        "final_status": "passed",
    }
    return source, units, translation, review, unit_id


def test_composite_label_reflows_before_solver_without_consuming_target_offsets() -> (
    None
):
    source, units, translation, review, unit_id = _parents(chinese_text="甲")
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )

    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    flow = graph["unit_flows"][0]
    lines = flow["lines"]

    assert graph["line_mapping_version"] == 2
    assert graph["annotation_binding"] == {
        "kind": "final",
        "parent_hash": sha256_canonical(annotations),
        "selection_hash": annotations["selection_hash"],
    }
    assert flow["base_target_length"] == 1
    assert "".join(segment["text"] for segment in flow["composite_segments"]) == (
        f"甲{AMBIGUITY_LABEL}"
    )
    assert [
        (segment["target_start"], segment["target_end"])
        for segment in flow["composite_segments"]
        if segment["kind"] == "target"
    ] == [(0, 1)]
    assert any(
        line["target_start"] == line["target_end"] and line["synthetic_annotation_ids"]
        for line in lines
    )
    assert lines[0]["composite_start"] == 0
    assert lines[-1]["composite_end"] == flow["composite_length"]
    assert all(
        left["composite_end"] == right["composite_start"]
        for left, right in zip(lines, lines[1:], strict=False)
    )
    assert flow["anchor"]["source_first_line_bbox_mpt"] == [
        45_000,
        700_000,
        110_000,
        730_000,
    ]
    assert flow["anchor"]["source_endpoint_mpt"] == [110_000, 715_000]

    layout = solve_layout(graph)
    output_lines = [
        line
        for page in layout["pages"]
        for block in page["blocks"]
        for line in block["lines"]
    ]
    assert [line["line_box_hash"] for line in output_lines] == [
        line["line_box_hash"] for line in lines
    ]


def test_trial_callback_builds_below_translation_auxiliary_flow() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="术语含义。")
    style, resolver = _typography()
    trial = make_annotation_layout_trial(
        source,
        units,
        translation,
        review,
        style_contract=style,
        resolver=resolver,
    )
    teaching = TeachingCandidate(
        key="key-term",
        english_original="Key",
        chinese_meaning="关键术语",
        occurrences=(TeachingOccurrence(unit_id, 0, 3, 0, 2),),
        value_priority=500,
    )

    selected = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=trial,
    )
    annotations = selected.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )

    auxiliary = graph["auxiliary_flows"]
    assert len(auxiliary) == 1
    assert auxiliary[0]["annotation_kind"] == "dark-orange-teaching"
    assert auxiliary[0]["attachment"] == "below-translation"
    assert (
        auxiliary[0]["allowed_native_frame_ids"]
        == graph["unit_flows"][0]["allowed_native_frame_ids"]
    )
    layout = solve_layout(graph)
    assert [
        block["content_kind"] for page in layout["pages"] for block in page["blocks"]
    ] == ["unit", "auxiliary"]


def test_final_parent_validation_rejects_a_synthetic_anchor_tamper() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="甲")
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    tampered = deepcopy(graph)
    synthetic = next(
        segment
        for segment in tampered["unit_flows"][0]["composite_segments"]
        if segment["kind"] == "ambiguity-label"
    )
    synthetic["target_offset"] = 0

    with pytest.raises(AnnotationAdapterError, match="recomputed"):
        validate_annotated_frame_graph_against_inputs(
            source,
            units,
            translation,
            review,
            annotations,
            tampered,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=annotations["candidate_set_hash"],
        )


def test_public_solver_rejects_a_rehashed_truncated_v2_unit_flow() -> None:
    source, units, translation, review, unit_id = _parents(
        chinese_text="甲乙丙丁戊己庚辛壬癸" * 8,
    )
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    flow = graph["unit_flows"][0]
    assert len(flow["lines"]) > 1

    flow["lines"] = flow["lines"][:-1]
    flow["line_count"] = len(flow["lines"])
    flow["legal_breaks"] = [
        item for item in flow["legal_breaks"] if item["after_line"] < flow["line_count"]
    ]
    flow["line_sequence_hash"] = sha256_canonical(
        {
            "line_sequence_contract_version": "2.0.0",
            "style": flow["style"],
            "composite_segments": flow["composite_segments"],
            "lines": flow["lines"],
        }
    )
    graph["unit_parts"][0]["line_end"] = flow["line_count"]

    with pytest.raises(LayoutSolverError, match="structurally invalid"):
        solve_layout(graph)


def test_legacy_unannotated_frame_graph_remains_v1() -> None:
    source, units, translation, _review, _unit_id = _parents(chinese_text="普通译文。")
    style, resolver = _typography()

    graph = build_frame_graph(
        source,
        units,
        translation,
        style_contract=style,
        resolver=resolver,
    )

    assert "line_mapping_version" not in graph
    assert "annotation_binding" not in graph
    assert "auxiliary_flows" not in graph
    solve_layout(graph)


def test_styled_span_cannot_split_a_combining_grapheme() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="e\u0301后文")
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )

    with pytest.raises(AnnotationAdapterError, match="grapheme"):
        build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            annotations,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=annotations["candidate_set_hash"],
        )


def test_auxiliary_annotation_span_cannot_split_a_combining_grapheme() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="e\u0301后文")
    style, resolver = _typography()
    teaching = TeachingCandidate(
        key="key-term",
        english_original="Key",
        chinese_meaning="关键术语",
        occurrences=(TeachingOccurrence(unit_id, 0, 3, 0, 1),),
        value_priority=500,
    )
    annotations = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    ).to_artifact(units=units, translation=translation, review=review)

    with pytest.raises(AnnotationAdapterError, match="grapheme"):
        build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            annotations,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=annotations["candidate_set_hash"],
        )


def test_public_solver_rejects_auxiliary_parent_length_tamper() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="术语含义。")
    style, resolver = _typography()
    teaching = TeachingCandidate(
        key="key-term",
        english_original="Key",
        chinese_meaning="关键术语",
        occurrences=(TeachingOccurrence(unit_id, 0, 3, 0, 2),),
        value_priority=500,
    )
    annotations = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    ).to_artifact(units=units, translation=translation, review=review)
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    graph["auxiliary_flows"][0]["parent_target_length"] += 100

    with pytest.raises(LayoutSolverError, match="structurally invalid"):
        solve_layout(graph)


def test_source_anchor_is_crop_relative_and_stays_in_the_a4_left_panel() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="甲")
    page = source["pages"][0]
    page["media_box_mpt"] = [10_000, 20_000, 605_276, 861_890]
    page["crop_box_mpt"] = [10_000, 20_000, 605_276, 861_890]
    band = page["bands"][0]
    band["y_top_mpt"] += 20_000
    band["y_bottom_mpt"] += 20_000
    column = band["columns"][0]
    column["x_left_mpt"] += 10_000
    column["x_right_mpt"] += 10_000
    block = page["blocks"][0]
    block["bbox_mpt"] = [55_000, 720_000, 120_000, 750_000]
    block["first_line_bbox_mpt"] = [55_000, 720_000, 120_000, 750_000]
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )

    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    candidate = graph["unit_flows"][0]["source_anchor_candidates"][0]

    assert candidate["source_first_line_bbox_mpt"] == [
        45_000,
        700_000,
        110_000,
        730_000,
    ]
    assert candidate["source_endpoint_mpt"] == [110_000, 715_000]


def test_cross_page_unit_selects_the_visible_page_candidate_for_a_continuation() -> (
    None
):
    source, units, translation, review, unit_id = _cross_page_parents(
        chinese_text="甲乙丙丁"
    )
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    flow = graph["unit_flows"][0]

    assert [
        candidate["source_page_number"]
        for candidate in flow["source_anchor_candidates"]
    ] == [1, 2]
    spacing = _flow_spacing(graph)
    templates = _page_templates(graph, spacing)
    slots = instantiate_slots(templates, {2: 1})
    continuation_index = next(
        index
        for index, slot in enumerate(slots)
        if slot.source_page_number == 2 and slot.page_kind == "continuation"
    )
    _flows, records = _content_records(graph, DEFAULT_LAYOUT_LIMITS, spacing)
    placement = FlowPlacement(
        content_id=unit_id,
        unit_id=unit_id,
        role="body",
        line_start=0,
        line_end=len(flow["lines"]),
        slot_index=continuation_index,
        break_kind_after=None,
    )

    selected = _selected_anchors(
        slots=slots,
        placements=(placement,),
        records=records,
    )

    assert selected[0]["source_page_number"] == 2
    assert (
        selected[0]["source_block_id"] == units["units"][0]["fragments"][1]["block_id"]
    )


def test_actual_first_part_on_a_continuation_creates_exactly_one_anchor() -> None:
    source, units, translation, review, first_unit_id = _parents(
        chinese_text="甲。" * 300
    )
    page = source["pages"][0]
    first_source_end = page["blocks"][0]["source_char_end"]
    second_source = "Second unit."
    second_unit_id = stable_source_id(
        page_number=1,
        reading_order=1,
        role="body",
        source_char_start=first_source_end,
        source_char_end=first_source_end + len(second_source),
    )
    page["blocks"].append(
        {
            "id": second_unit_id,
            "role": "body",
            "translation_policy": "required",
            "band_id": "body-band",
            "column_id": "body-column",
            "reading_order": 1,
            "source_char_start": first_source_end,
            "source_char_end": first_source_end + len(second_source),
            "text": second_source,
            "bbox_mpt": [45_000, 640_000, 110_000, 670_000],
            "first_line_bbox_mpt": [45_000, 640_000, 110_000, 670_000],
            "confidence_ppm": 990_000,
        }
    )
    units["units"].append(
        {
            "id": second_unit_id,
            "role": "body",
            "reading_order": 1,
            "source_text": second_source,
            "confidence_ppm": 990_000,
            "fragments": [
                {
                    "page_number": 1,
                    "block_id": second_unit_id,
                    "source_char_start": first_source_end,
                    "source_char_end": first_source_end + len(second_source),
                }
            ],
        }
    )
    translation["units_hash"] = sha256_canonical(units)
    translation["units"].append(
        {
            "unit_id": second_unit_id,
            "chinese_text": "第二单元。",
            "spans": [],
            "terminology": [],
        }
    )
    review["translation_hash"] = sha256_canonical(translation)
    review["reviewed_unit_ids"] = [first_unit_id, second_unit_id]
    style, resolver = _typography()
    annotations = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    ).to_artifact(units=units, translation=translation, review=review)
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )

    layout = solve_layout(graph)
    second_parts = [
        (output_page, block)
        for output_page in layout["pages"]
        for block in output_page["blocks"]
        if block["unit_id"] == second_unit_id
    ]

    assert second_parts[0][0]["page_kind"] == "continuation"
    assert second_parts[0][1]["creates_anchor"] is True
    assert second_parts[0][1]["selected_anchor"]["source_page_number"] == 1
    assert sum(block["creates_anchor"] for _page, block in second_parts) == 1


def test_overlapping_dark_red_and_ambiguity_styles_do_not_change_geometry() -> None:
    chinese_text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
    source, units, translation, review, unit_id = _parents(chinese_text=chinese_text)
    style, resolver = _typography()
    ambiguity_only = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )
    with_red = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
        red_items=(SelectedRed("core-result", unit_id, 0, 1, 400),),
    )
    ambiguity_graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        ambiguity_only,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=ambiguity_only["candidate_set_hash"],
    )
    red_graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        with_red,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=with_red["candidate_set_hash"],
    )
    ambiguity_flow = ambiguity_graph["unit_flows"][0]
    red_flow = red_graph["unit_flows"][0]

    assert red_flow["lines"] == ambiguity_flow["lines"]
    assert red_flow["composite_segments"] == ambiguity_flow["composite_segments"]
    assert [span["kind"] for span in red_flow["styled_spans"]] == [
        "dark-red-highlight",
        "bright-red-ambiguity",
    ]
    assert {
        (span["target_start"], span["target_end"]) for span in red_flow["styled_spans"]
    } == {(0, 1)}


def test_adapter_count_limits_reject_boundary_plus_one_before_measurement() -> None:
    chinese_text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
    source, units, translation, review, unit_id = _parents(chinese_text=chinese_text)
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
        red_items=(SelectedRed("core-result", unit_id, 0, 1, 400),),
    )
    limits_to_reject = (
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, version=True),
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, max_items_total=1),
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, max_labels_per_unit=0),
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, max_styled_per_unit=1),
    )

    for limits in limits_to_reject:
        assert isinstance(limits, AnnotationAdapterLimits)
        with pytest.raises(AnnotationAdapterError, match="limits"):
            build_final_annotated_frame_graph(
                source,
                units,
                translation,
                review,
                annotations,
                style_contract=style,
                resolver=resolver,
                expected_candidate_set_hash=annotations["candidate_set_hash"],
                adapter_limits=limits,
            )


def test_raw_limits_preflight_runs_before_full_annotation_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, units, translation, review, unit_id = _parents(
        chinese_text="甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
    )
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
        red_items=(SelectedRed("core-result", unit_id, 0, 1, 400),),
    )

    def unexpected_full_validation(*_args, **_kwargs) -> None:
        raise AssertionError("full validation ran before raw limits")

    monkeypatch.setattr(
        annotation_adapter_module,
        "validate_annotations_against_inputs",
        unexpected_full_validation,
    )
    with pytest.raises(AnnotationAdapterError, match="limits exceeded"):
        build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            annotations,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=annotations["candidate_set_hash"],
            adapter_limits=replace(
                DEFAULT_ANNOTATION_ADAPTER_LIMITS,
                max_items_total=1,
            ),
        )
    malformed = deepcopy(annotations)
    malformed["items"] = tuple(malformed["items"])
    with pytest.raises(AnnotationAdapterError, match="limits input is invalid"):
        build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            malformed,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=annotations["candidate_set_hash"],
        )


def test_final_candidate_set_hash_is_required_and_independently_revalidated() -> None:
    parameter = signature(build_final_annotated_frame_graph).parameters[
        "expected_candidate_set_hash"
    ]
    assert parameter.default is Parameter.empty

    source, units, translation, review, unit_id = _parents(chinese_text="甲")
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
    )
    expected_candidate_set_hash = str(annotations["candidate_set_hash"])
    tampered = deepcopy(annotations)
    tampered["candidate_set_hash"] = "f" * 64
    assert tampered["candidate_set_hash"] != expected_candidate_set_hash
    tampered["annotations_input_hash"] = sha256_canonical(
        {
            "contract_version": "1.0.0",
            "units_hash": tampered["units_hash"],
            "translation_hash": tampered["translation_hash"],
            "review_hash": tampered["review_hash"],
            "mandatory_items_hash": tampered["mandatory_items_hash"],
            "candidate_set_hash": tampered["candidate_set_hash"],
            "orange_selection_hash": tampered["orange_selection_hash"],
        }
    )
    validate_annotations_against_inputs(
        units,
        translation,
        review,
        tampered,
        style,
    )

    with pytest.raises(AnnotationAdapterError, match="trusted"):
        build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            tampered,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=expected_candidate_set_hash,
        )
    with pytest.raises(AnnotationAdapterError, match="required"):
        build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            annotations,
            style_contract=style,
            resolver=resolver,
            expected_candidate_set_hash=None,  # type: ignore[arg-type]
        )


def test_grapheme_boundaries_are_computed_once_per_annotated_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, units, translation, review, unit_id = _parents(
        chinese_text="甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
    )
    style, resolver = _typography()
    annotations = _ambiguity_annotations(
        units,
        translation,
        review,
        unit_id,
        style=style,
        red_items=(SelectedRed("core-result", unit_id, 0, 1, 400),),
    )
    calls = 0
    original = annotation_adapter_module.grapheme_boundaries

    def counted(text: str) -> frozenset[int]:
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(annotation_adapter_module, "grapheme_boundaries", counted)
    build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )

    assert calls == 1


def test_semantic_validator_rejects_label_inserted_inside_a_grapheme() -> None:
    label = AMBIGUITY_LABEL
    flow = {
        "base_target_length": 3,
        "composite_length": 3 + len(label),
        "composite_segments": [
            {
                "index": 0,
                "kind": "target",
                "target_start": 0,
                "target_end": 1,
                "composite_start": 0,
                "composite_end": 1,
                "text": "e",
            },
            {
                "index": 1,
                "kind": "ambiguity-label",
                "annotation_id": "ambiguity",
                "target_offset": 1,
                "composite_start": 1,
                "composite_end": 1 + len(label),
                "text": label,
            },
            {
                "index": 2,
                "kind": "target",
                "target_start": 1,
                "target_end": 3,
                "composite_start": 1 + len(label),
                "composite_end": 3 + len(label),
                "text": "\u0301后",
            },
        ],
        "styled_spans": [
            {
                "annotation_id": "ambiguity",
                "kind": "bright-red-ambiguity",
                "target_start": 0,
                "target_end": 2,
                "color_token": "bright_red",
                "underline": True,
            }
        ],
        "lines": [
            {
                "target_start": 0,
                "target_end": 3,
                "composite_start": 0,
                "composite_end": 3 + len(label),
                "synthetic_annotation_ids": ["ambiguity"],
                "text": f"e{label}\u0301后",
            }
        ],
    }

    with pytest.raises(SchemaValidationError, match="grapheme"):
        _validate_composite_flow(flow, label="unit flow", allow_synthetic=True)


def test_semantic_validator_rejects_unit_line_split_inside_a_grapheme() -> None:
    flow = {
        "base_target_length": 3,
        "composite_length": 3,
        "composite_segments": [
            {
                "index": 0,
                "kind": "target",
                "target_start": 0,
                "target_end": 3,
                "composite_start": 0,
                "composite_end": 3,
                "text": "e\u0301后",
            }
        ],
        "styled_spans": [],
        "lines": [
            {
                "target_start": 0,
                "target_end": 1,
                "composite_start": 0,
                "composite_end": 1,
                "synthetic_annotation_ids": [],
                "text": "e",
            },
            {
                "target_start": 1,
                "target_end": 3,
                "composite_start": 1,
                "composite_end": 3,
                "synthetic_annotation_ids": [],
                "text": "\u0301后",
            },
        ],
    }

    with pytest.raises(SchemaValidationError, match="grapheme"):
        _validate_composite_flow(flow, label="unit flow", allow_synthetic=True)


def test_semantic_validator_rejects_aux_line_split_inside_a_grapheme() -> None:
    flow = {
        "annotation_id": "teaching",
        "parent_target_length": 2,
        "attachment_target_start": 0,
        "attachment_target_end": 1,
        "composite_length": 3,
        "composite_segments": [
            {
                "kind": "auxiliary-content",
                "target_offset": 1,
                "composite_start": 0,
                "composite_end": 3,
                "text": "e\u0301后",
            }
        ],
        "lines": [
            {
                "target_start": 1,
                "target_end": 1,
                "composite_start": 0,
                "composite_end": 1,
                "synthetic_annotation_ids": ["teaching"],
                "text": "e",
            },
            {
                "target_start": 1,
                "target_end": 1,
                "composite_start": 1,
                "composite_end": 3,
                "synthetic_annotation_ids": ["teaching"],
                "text": "\u0301后",
            },
        ],
    }

    with pytest.raises(SchemaValidationError, match="grapheme"):
        _validate_auxiliary_flow_mapping(flow, label="auxiliary flow")


def test_frame_line_validator_rejects_hard_newlines_for_unit_and_auxiliary() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="术语含义。")
    style, resolver = _typography()
    teaching = TeachingCandidate(
        key="key-term",
        english_original="Key",
        chinese_meaning="关键术语",
        occurrences=(TeachingOccurrence(unit_id, 0, 3, 0, 2),),
        value_priority=500,
    )
    annotations = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    ).to_artifact(units=units, translation=translation, review=review)
    graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    font_names = {
        face["role"]: face["reportlab_name"] for face in graph["font_fingerprint"]
    }

    for flow, initial_target_start in (
        (graph["unit_flows"][0], 0),
        (
            graph["auxiliary_flows"][0],
            graph["auxiliary_flows"][0]["attachment_target_end"],
        ),
    ):
        line = deepcopy(flow["lines"][0])
        line["text"] += "\n"
        line["runs"][-1]["text"] += "\n"
        line["line_box_hash"] = sha256_canonical(
            {
                "line_box_contract_version": "2.0.0",
                **{key: value for key, value in line.items() if key != "line_box_hash"},
            }
        )

        with pytest.raises(SchemaValidationError, match="hard line break"):
            _validate_frame_lines(
                [line],
                flow["style"],
                font_names,
                label="annotated flow",
                line_mapping_version=2,
                initial_target_start=initial_target_start,
            )


def test_same_kind_overlap_validation_uses_an_adjacent_sorted_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans = [
        (
            annotation_validation_module.BoundTargetSpan(
                "unit",
                index * 2,
                index * 2 + 1,
            ),
            f"annotation-{index:04d}",
        )
        for index in range(1_000)
    ]
    calls = 0
    original = annotation_validation_module.spans_overlap

    def counted(first, second) -> bool:
        nonlocal calls
        calls += 1
        return original(first, second)

    monkeypatch.setattr(annotation_validation_module, "spans_overlap", counted)

    assert annotation_validation_module._same_kind_spans_overlap(spans) is False
    assert calls == len(spans) - 1


def test_same_kind_overlap_sweep_matches_pairwise_small_exhaustive_cases() -> None:
    candidates = [
        annotation_validation_module.BoundTargetSpan(unit_id, start, end)
        for unit_id in ("unit-a", "unit-b")
        for start in range(3)
        for end in range(start + 1, 4)
    ]
    for size in range(5):
        for case in combinations(candidates, size):
            expected = any(
                annotation_validation_module.spans_overlap(first, second)
                for first, second in combinations(case, 2)
            )
            entries = [
                (span, f"annotation-{index:02d}") for index, span in enumerate(case)
            ]
            assert (
                annotation_validation_module._same_kind_spans_overlap(entries)
                is expected
            )


def test_adapter_auxiliary_limit_rejects_boundary_plus_one() -> None:
    source, units, translation, review, unit_id = _parents(chinese_text="术语含义。")
    style, resolver = _typography()
    teaching = TeachingCandidate(
        key="key-term",
        english_original="Key",
        chinese_meaning="关键术语",
        occurrences=(TeachingOccurrence(unit_id, 0, 3, 0, 2),),
        value_priority=500,
    )
    annotations = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    ).to_artifact(units=units, translation=translation, review=review)
    content_length = len(str(annotations["items"][0]["content"]))
    limits_to_reject = (
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, max_auxiliary_per_unit=0),
        replace(
            DEFAULT_ANNOTATION_ADAPTER_LIMITS,
            max_content_chars_per_item=content_length - 1,
        ),
    )

    for limits in limits_to_reject:
        with pytest.raises(AnnotationAdapterError, match="limits"):
            build_final_annotated_frame_graph(
                source,
                units,
                translation,
                review,
                annotations,
                style_contract=style,
                resolver=resolver,
                expected_candidate_set_hash=annotations["candidate_set_hash"],
                adapter_limits=limits,
            )


def test_adapter_composite_limits_reject_boundary_plus_one() -> None:
    source, units, translation, review, _unit_id = _parents(chinese_text="甲")
    style, resolver = _typography()
    annotations = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
    ).to_artifact(units=units, translation=translation, review=review)
    limits_to_reject = (
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, max_composite_chars_per_unit=0),
        replace(DEFAULT_ANNOTATION_ADAPTER_LIMITS, max_composite_chars_total=0),
    )

    for limits in limits_to_reject:
        with pytest.raises(AnnotationAdapterError, match="limits"):
            build_final_annotated_frame_graph(
                source,
                units,
                translation,
                review,
                annotations,
                style_contract=style,
                resolver=resolver,
                expected_candidate_set_hash=annotations["candidate_set_hash"],
                adapter_limits=limits,
            )
