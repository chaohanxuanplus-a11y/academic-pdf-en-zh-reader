# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from inspect import signature

import pytest

import academic_pdf_en_zh_reader.job.finalize as finalize_module
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    freeze_orange_candidate_set,
)
from academic_pdf_en_zh_reader.job.finalize import (
    FinalizationError,
    finalize_annotations_and_layout,
    finalize_resumed_annotations_and_layout,
    resume_must_run_finalizer,
    validate_finalization_receipt_against_inputs,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    DEFAULT_ANNOTATION_ADAPTER_LIMITS,
)
from academic_pdf_en_zh_reader.layout.frame_graph import DEFAULT_FRAME_GRAPH_CONFIG
from academic_pdf_en_zh_reader.layout.solver import (
    DEFAULT_LAYOUT_LIMITS,
    validate_layout_against_frame_graph,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)


def _parents() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
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
        "normalized_pdf_sha256": "3" * 64,
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
                                "x_right_mpt": 555_276,
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
                        "bbox_mpt": [45_000, 700_000, 150_000, 730_000],
                        "first_line_bbox_mpt": [
                            45_000,
                            700_000,
                            150_000,
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
        "normalized_pdf_sha256": "3" * 64,
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
                "chinese_text": "关键术语。",
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
    return source, units, translation, review


def _inputs():
    source, units, translation, review = _parents()
    style = build_style_contract((FontSizeSample(10_000, 500),))
    resolver = FontRunResolver(load_font_registry())
    candidates = freeze_orange_candidate_set(
        units,
        translation,
        teaching_candidates=(),
        figure_candidates=(),
    )
    return source, units, translation, review, style, resolver, candidates


def test_finalizer_uses_one_frozen_chain_and_emits_valid_receipt(monkeypatch) -> None:
    source, units, translation, review, style, resolver, candidates = _inputs()
    graph_config = replace(DEFAULT_FRAME_GRAPH_CONFIG, horizontal_padding_mpt=4_001)
    layout_limits = replace(DEFAULT_LAYOUT_LIMITS, max_window_attempts=8_191)
    adapter_limits = replace(
        DEFAULT_ANNOTATION_ADAPTER_LIMITS,
        max_items_total=9_999,
    )
    seen: dict[str, list[object]] = {
        "trial": [],
        "selection": [],
        "graph": [],
        "layout": [],
    }
    real_make_trial = finalize_module.make_annotation_layout_trial
    real_select = finalize_module.select_orange_annotations
    real_build_graph = finalize_module.build_final_annotated_frame_graph
    real_solve = finalize_module.solve_layout

    def make_trial(*args, **kwargs):
        seen["trial"].append(
            (
                kwargs["style_contract"],
                kwargs["resolver"],
                kwargs["config"],
                kwargs["limits"],
                kwargs["adapter_limits"],
            )
        )
        return real_make_trial(*args, **kwargs)

    def select(*args, **kwargs):
        seen["selection"].append(kwargs["candidate_set"])
        return real_select(*args, **kwargs)

    def build_graph(*args, **kwargs):
        seen["graph"].append(
            (
                kwargs["style_contract"],
                kwargs["resolver"],
                kwargs["config"],
                kwargs["adapter_limits"],
            )
        )
        return real_build_graph(*args, **kwargs)

    def solve(*args, **kwargs):
        seen["layout"].append(kwargs["limits"])
        return real_solve(*args, **kwargs)

    monkeypatch.setattr(finalize_module, "make_annotation_layout_trial", make_trial)
    monkeypatch.setattr(finalize_module, "select_orange_annotations", select)
    monkeypatch.setattr(
        finalize_module,
        "build_final_annotated_frame_graph",
        build_graph,
    )
    monkeypatch.setattr(finalize_module, "solve_layout", solve)

    result = finalize_annotations_and_layout(
        source,
        units,
        translation,
        review,
        candidate_set=candidates,
        style_contract=style,
        resolver=resolver,
        frame_graph_config=graph_config,
        layout_limits=layout_limits,
        annotation_adapter_limits=adapter_limits,
    )

    assert seen["trial"] == [
        (style, resolver, graph_config, layout_limits, adapter_limits)
    ]
    assert seen["selection"] == [candidates]
    assert seen["selection"][0] is candidates
    assert seen["graph"] == [(style, resolver, graph_config, adapter_limits)]
    assert seen["layout"] == [layout_limits, layout_limits]
    assert result.candidate_set is candidates
    assert result.receipt["candidate_set_hash"] == candidates.candidate_set_hash
    assert result.receipt["selection_hash"] == result.selection.selection_hash
    assert result.receipt["solver_input_hash"] == result.layout["solver_input_hash"]
    assert result.receipt["continuation_page_count"] == 0
    assert result.receipt["artifact_hashes"] == {
        "source": sha256_canonical(source),
        "units": sha256_canonical(units),
        "translation": sha256_canonical(translation),
        "review": sha256_canonical(review),
        "annotations": sha256_canonical(result.annotations),
        "frame-graph": sha256_canonical(result.frame_graph),
        "layout": sha256_canonical(result.layout),
    }
    assert set(result.receipt["policy_hashes"]) == {
        "style-contract",
        "font-fingerprint",
        "frame-graph-config",
        "layout-limits",
        "annotation-adapter-limits",
        "orange-selection-policy",
    }
    validate_artifact("finalization-receipt", result.receipt)
    validate_finalization_receipt_against_inputs(
        source,
        units,
        translation,
        review,
        result.annotations,
        result.frame_graph,
        result.layout,
        result.receipt,
        style_contract=style,
        resolver=resolver,
        frame_graph_config=graph_config,
        layout_limits=layout_limits,
        annotation_adapter_limits=adapter_limits,
    )


def test_finalizer_fails_stably_when_final_continuation_differs(monkeypatch) -> None:
    source, units, translation, review, style, resolver, candidates = _inputs()
    real_solve = finalize_module.solve_layout

    def mismatched_solve(*args, **kwargs):
        layout = deepcopy(real_solve(*args, **kwargs))
        layout["solver_trace"]["continuation_page_count"] += 1
        return layout

    monkeypatch.setattr(finalize_module, "solve_layout", mismatched_solve)

    with pytest.raises(
        FinalizationError,
        match="FINAL_CONTINUATION_COUNT_MISMATCH",
    ):
        finalize_annotations_and_layout(
            source,
            units,
            translation,
            review,
            candidate_set=candidates,
            style_contract=style,
            resolver=resolver,
        )


def test_receipt_rejects_parent_tamper_even_when_receipt_is_rehashed() -> None:
    source, units, translation, review, style, resolver, candidates = _inputs()
    result = finalize_annotations_and_layout(
        source,
        units,
        translation,
        review,
        candidate_set=candidates,
        style_contract=style,
        resolver=resolver,
    )
    forged_receipt = deepcopy(result.receipt)
    forged_receipt["artifact_hashes"]["source"] = "0" * 64
    forged_receipt["receipt_hash"] = sha256_canonical(
        {key: value for key, value in forged_receipt.items() if key != "receipt_hash"}
    )

    with pytest.raises(FinalizationError, match="source"):
        validate_finalization_receipt_against_inputs(
            source,
            units,
            translation,
            review,
            result.annotations,
            result.frame_graph,
            result.layout,
            forged_receipt,
            style_contract=style,
            resolver=resolver,
        )


def test_receipt_rejects_synchronized_one_mpt_layout_tamper() -> None:
    source, units, translation, review, style, resolver, candidates = _inputs()
    result = finalize_annotations_and_layout(
        source,
        units,
        translation,
        review,
        candidate_set=candidates,
        style_contract=style,
        resolver=resolver,
    )
    tampered_layout = deepcopy(result.layout)
    page = tampered_layout["pages"][0]
    band = page["bands"][0]
    band["solved_top_offset_mpt"] += 1
    band["bbox_mpt"][1] -= 1
    band["bbox_mpt"][3] -= 1
    for frame in page["frames"]:
        frame["bbox_mpt"][1] -= 1
        frame["bbox_mpt"][3] -= 1
    for block in page["blocks"]:
        block["solved_top_offset_mpt"] += 1
        block["bbox_mpt"][1] -= 1
        block["bbox_mpt"][3] -= 1
        for line in block["lines"]:
            line["baseline_y_mpt"] -= 1
    validate_layout_against_frame_graph(result.frame_graph, tampered_layout)

    forged_receipt = deepcopy(result.receipt)
    forged_receipt["artifact_hashes"]["layout"] = sha256_canonical(tampered_layout)
    forged_receipt["receipt_hash"] = sha256_canonical(
        {key: value for key, value in forged_receipt.items() if key != "receipt_hash"}
    )
    with pytest.raises(FinalizationError, match="layout recomputation"):
        validate_finalization_receipt_against_inputs(
            source,
            units,
            translation,
            review,
            result.annotations,
            result.frame_graph,
            tampered_layout,
            forged_receipt,
            style_contract=style,
            resolver=resolver,
        )

    swapped_source = deepcopy(source)
    swapped_source["source_sha256"] = "e" * 64
    with pytest.raises(FinalizationError, match="source"):
        validate_finalization_receipt_against_inputs(
            swapped_source,
            units,
            translation,
            review,
            result.annotations,
            result.frame_graph,
            result.layout,
            result.receipt,
            style_contract=style,
            resolver=resolver,
        )


def test_resume_from_annotations_or_earlier_must_rerun_finalizer() -> None:
    assert resume_must_run_finalizer(JobStage.INITIALIZED)
    assert resume_must_run_finalizer(JobStage.INDEPENDENTLY_REVIEWED)
    assert resume_must_run_finalizer(JobStage.ANNOTATED)
    assert not resume_must_run_finalizer(JobStage.LAID_OUT)
    assert "annotations" not in signature(finalize_annotations_and_layout).parameters


def test_annotated_resume_rejects_candidate_change_instead_of_mixing_ledgers() -> None:
    source, units, translation, review, style, resolver, empty_candidates = _inputs()
    original = finalize_annotations_and_layout(
        source,
        units,
        translation,
        review,
        candidate_set=empty_candidates,
        style_contract=style,
        resolver=resolver,
    )
    artifact_hashes = {
        JobStage.PREFLIGHTED: {
            "preflight": "1" * 64,
            "normalization": "2" * 64,
            "normalized-pdf": "3" * 64,
        },
        JobStage.EXTRACTED: {
            "source": sha256_canonical(source),
            "units": sha256_canonical(units),
        },
        JobStage.TRANSLATED: {"translation": sha256_canonical(translation)},
        JobStage.INDEPENDENTLY_REVIEWED: {"review": sha256_canonical(review)},
        JobStage.ANNOTATED: {"annotations": sha256_canonical(original.annotations)},
    }
    state = create_job(
        job_id="job-001",
        source_sha256=str(source["source_sha256"]),
        translation_revision=1,
    )
    for stage, hashes in artifact_hashes.items():
        state = advance_job(
            state,
            stage,
            hashes,
            expected_previous_state_hash=state_hash(state),
        )
    resumed = finalize_resumed_annotations_and_layout(
        state,
        source,
        units,
        translation,
        review,
        candidate_set=empty_candidates,
        style_contract=style,
        resolver=resolver,
    )
    assert sha256_canonical(resumed.annotations) == state.artifact_hashes["annotations"]

    unit_id = str(units["units"][0]["id"])
    changed_candidates = freeze_orange_candidate_set(
        units,
        translation,
        teaching_candidates=(
            TeachingCandidate(
                key="key-term",
                english_original="Key term",
                chinese_meaning="关键术语",
                occurrences=(TeachingOccurrence(unit_id, 0, 8, 0, 4),),
                value_priority=100,
            ),
        ),
        figure_candidates=(),
    )

    with pytest.raises(FinalizationError, match="RESUME_ARTIFACT_MISMATCH"):
        finalize_resumed_annotations_and_layout(
            state,
            source,
            units,
            translation,
            review,
            candidate_set=changed_candidates,
            style_contract=style,
            resolver=resolver,
        )


def test_resume_rejects_pdf_identity_separate_from_the_artifact_ledger() -> None:
    source, units, translation, review, style, resolver, candidates = _inputs()
    state = create_job(
        job_id="job-001",
        source_sha256="e" * 64,
        translation_revision=1,
    )
    artifact_hashes = (
        (
            JobStage.PREFLIGHTED,
            {
                "preflight": "1" * 64,
                "normalization": "2" * 64,
                "normalized-pdf": "3" * 64,
            },
        ),
        (
            JobStage.EXTRACTED,
            {
                "source": sha256_canonical(source),
                "units": sha256_canonical(units),
            },
        ),
        (JobStage.TRANSLATED, {"translation": sha256_canonical(translation)}),
        (
            JobStage.INDEPENDENTLY_REVIEWED,
            {"review": sha256_canonical(review)},
        ),
    )
    for stage, hashes in artifact_hashes:
        state = advance_job(
            state,
            stage,
            hashes,
            expected_previous_state_hash=state_hash(state),
        )

    with pytest.raises(FinalizationError, match="source_sha256"):
        finalize_resumed_annotations_and_layout(
            state,
            source,
            units,
            translation,
            review,
            candidate_set=candidates,
            style_contract=style,
            resolver=resolver,
        )
