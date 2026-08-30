# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
    FrozenObjectEvidence,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialRequest,
    LayoutTrialResult,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
    validate_annotations_against_inputs,
)
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)

from .conftest import make_bundle


def _candidate(index: int, *, evidence: bool = True) -> FigureNoteCandidate:
    return FigureNoteCandidate(
        key=f"note-{index}",
        figure_id="figure-1",
        caption_unit_id="u-0-figure-caption",
        target_start=0,
        target_end=4,
        content=f"发现{index}",
        compact_content=f"发现{index}",
        evidence=(DirectEvidence("u-0-figure-caption", 0, 8, "Figure 1"),)
        if evidence
        else (),
        value_priority=100 - index,
        essential=index == 0,
    )


def test_each_figure_keeps_at_most_three_directly_supported_notes() -> None:
    units, translation, _ = make_bundle(
        [("figure-caption", "Figure 1 rises", "图1趋势")]
    )

    def fit(_: LayoutTrialRequest) -> LayoutTrialResult:
        return LayoutTrialResult(True, 0)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=tuple(_candidate(index) for index in range(5)),
        teaching_candidates=(),
        auxiliary_size_mpt=8_600,
        trial_layout=fit,
    )
    assert [item.candidate_key for item in result.placements] == [
        "note-0",
        "note-1",
        "note-2",
    ]


def test_figure_note_without_exact_direct_evidence_fails_closed() -> None:
    units, translation, _ = make_bundle(
        [("figure-caption", "Figure 1 rises", "图1趋势")]
    )
    with pytest.raises(AnnotationValidationError, match="direct evidence"):
        select_orange_annotations(
            units,
            translation,
            figure_candidates=(_candidate(0, evidence=False),),
            teaching_candidates=(),
            auxiliary_size_mpt=8_600,
            trial_layout=lambda _: LayoutTrialResult(True, 0),
        )


def test_figure_evidence_must_match_the_bound_source_slice() -> None:
    units, translation, _ = make_bundle(
        [("figure-caption", "Figure 1 rises", "图1趋势")]
    )
    candidate = FigureNoteCandidate(
        key="bad-evidence",
        figure_id="figure-1",
        caption_unit_id="u-0-figure-caption",
        target_start=0,
        target_end=4,
        content="变量上升。",
        compact_content="变量上升。",
        evidence=(DirectEvidence("u-0-figure-caption", 0, 8, "Figure X"),),
        value_priority=100,
        essential=True,
    )
    with pytest.raises(AnnotationValidationError, match="source slice"):
        select_orange_annotations(
            units,
            translation,
            figure_candidates=(candidate,),
            teaching_candidates=(),
            auxiliary_size_mpt=8_600,
            trial_layout=lambda _: LayoutTrialResult(True, 0),
        )


def test_frozen_graphic_evidence_requires_an_upstream_record_verifier() -> None:
    units, translation, review = make_bundle([("figure-caption", "Figure 1", "图1")])
    evidence = FrozenObjectEvidence.from_text(
        source_artifact_hash="b" * 64,
        object_id="graphic-p1-7",
        locator="page=1;series=treatment;points=0..4",
        evidence_text="Treatment rises from 2.1 to 4.8.",
    )
    candidate = FigureNoteCandidate(
        key="object-evidence",
        figure_id="figure-1",
        caption_unit_id="u-0-figure-caption",
        target_start=0,
        target_end=2,
        content="治疗组由2.1升至4.8。",
        compact_content="治疗组升至4.8。",
        evidence=(evidence,),
        value_priority=100,
        essential=True,
    )
    with pytest.raises(AnnotationValidationError, match="upstream verifier"):
        select_orange_annotations(
            units,
            translation,
            figure_candidates=(candidate,),
            teaching_candidates=(),
            auxiliary_size_mpt=8_600,
            trial_layout=lambda _: LayoutTrialResult(True, 0),
        )

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(candidate,),
        teaching_candidates=(),
        auxiliary_size_mpt=8_600,
        trial_layout=lambda _: LayoutTrialResult(True, 0),
        verify_frozen_evidence=lambda record: record.object_id == "graphic-p1-7",
    )
    assert result.placements[0].evidence == (evidence,)
    artifact = result.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    style = build_style_contract((FontSizeSample(10_000, 100),))
    with pytest.raises(AnnotationValidationError, match="upstream verifier"):
        validate_annotations_against_inputs(units, translation, review, artifact, style)
    validate_annotations_against_inputs(
        units,
        translation,
        review,
        artifact,
        style,
        verify_frozen_evidence=lambda record: record.object_id == "graphic-p1-7",
    )
