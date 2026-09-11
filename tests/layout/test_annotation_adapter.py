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
    validate_annotated_frame_graph_against_inputs,
)
from academic_pdf_en_zh_reader.review.review_validation import make_ambiguity_key
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
