# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.red_emphasis import (
    RedCandidate,
    RedImportance,
    select_red_emphasis,
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


def test_figure_notes_are_tried_before_teaching_and_ordinary_never_adds_page() -> None:
    units, translation, review = make_bundle(
        [
            ("body", "robust method", "采用稳健方法"),
            ("figure-caption", "Figure 1 rises", "图1呈上升趋势"),
        ]
    )
    teaching = TeachingCandidate(
        key="robust-method",
        english_original="robust method",
        chinese_meaning="稳健方法",
        occurrences=(TeachingOccurrence("u-0-body", 0, 13, 2, 6),),
        value_priority=90,
    )
    figure = FigureNoteCandidate(
        key="figure-rise",
        figure_id="figure-1",
        caption_unit_id="u-1-figure-caption",
        target_start=0,
        target_end=7,
        content="主要变量随时间上升。",
        compact_content="变量随时间上升。",
        evidence=(DirectEvidence("u-1-figure-caption", 0, 14, "Figure 1 rises"),),
        value_priority=80,
        essential=True,
    )
    calls: list[LayoutTrialRequest] = []

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        calls.append(request)
        if request.phase in {"mandatory-only", "final-frozen"}:
            return LayoutTrialResult(True, 0)
        newest = request.placements[-1]
        if newest.kind == "figure-table-reading":
            return LayoutTrialResult(True, 0)
        return LayoutTrialResult(True, 1)

    style = build_style_contract((FontSizeSample(10_000, 100),))
    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(figure,),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=trial,
    )

    assert [item.kind for item in result.placements] == ["figure-table-reading"]
    trial_kinds = [
        call.placements[-1].kind for call in calls if call.phase == "candidate-trial"
    ]
    assert trial_kinds == ["figure-table-reading", "dark-orange-teaching"]
    assert calls[0].phase == "mandatory-only"
    assert calls[-1].phase == "final-frozen"
    assert calls[-1].frozen_selection_hash == result.selection_hash

    artifact = result.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    validate_annotations_against_inputs(units, translation, review, artifact, style)
    artifact["items"][0]["english_original"] = "unexpected cross-kind field"
    with pytest.raises(AnnotationValidationError, match="fields"):
        validate_annotations_against_inputs(units, translation, review, artifact, style)


def test_essential_figure_may_use_continuation_only_after_compact_native_failure() -> (
    None
):
    units, translation, _ = make_bundle(
        [("figure-caption", "Figure 1 rises", "图1呈上升趋势")]
    )
    figure = FigureNoteCandidate(
        key="figure-rise",
        figure_id="figure-1",
        caption_unit_id="u-0-figure-caption",
        target_start=0,
        target_end=7,
        content="主要变量在所有时间点持续上升。",
        compact_content="变量持续上升。",
        evidence=(DirectEvidence("u-0-figure-caption", 0, 14, "Figure 1 rises"),),
        value_priority=100,
        essential=True,
    )
    attempts: list[tuple[str, bool]] = []

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        if request.phase == "mandatory-only":
            return LayoutTrialResult(True, 0)
        if request.phase == "final-frozen":
            return LayoutTrialResult(True, 1)
        attempts.append((request.placements[-1].content, request.allow_continuation))
        if request.allow_continuation:
            return LayoutTrialResult(True, 1)
        return LayoutTrialResult(False, 0)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(figure,),
        teaching_candidates=(),
        auxiliary_size_mpt=8_600,
        trial_layout=trial,
    )

    assert attempts == [
        ("主要变量在所有时间点持续上升。", False),
        ("变量持续上升。", False),
        ("变量持续上升。", True),
    ]
    assert result.placements[0].content == "变量持续上升。"
    assert result.placements[0].uses_continuation is True


def test_teaching_may_share_existing_figure_continuation_but_cannot_add_one() -> None:
    units, translation, _ = make_bundle(
        [
            ("body", "robust method", "采用稳健方法"),
            ("figure-caption", "Figure 1 rises", "图1呈上升趋势"),
        ]
    )
    teaching = TeachingCandidate(
        key="robust-method",
        english_original="robust method",
        chinese_meaning="稳健方法",
        occurrences=(TeachingOccurrence("u-0-body", 0, 13, 2, 6),),
        value_priority=90,
    )
    figure = FigureNoteCandidate(
        key="figure-rise",
        figure_id="figure-1",
        caption_unit_id="u-1-figure-caption",
        target_start=0,
        target_end=7,
        content="变量持续上升。",
        compact_content="变量上升。",
        evidence=(DirectEvidence("u-1-figure-caption", 0, 14, "Figure 1 rises"),),
        value_priority=100,
        essential=True,
    )

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        if request.phase == "mandatory-only":
            return LayoutTrialResult(True, 0)
        if request.phase == "final-frozen":
            return LayoutTrialResult(True, 1)
        if request.allow_continuation:
            return LayoutTrialResult(True, 1)
        if len(request.placements) == 1:
            return LayoutTrialResult(False, 0)
        return LayoutTrialResult(True, 1)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(figure,),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=8_600,
        trial_layout=trial,
    )

    assert [item.kind for item in result.placements] == [
        "figure-table-reading",
        "dark-orange-teaching",
    ]
    assert result.placements[0].uses_continuation is True
    assert result.placements[1].uses_continuation is False
    assert result.continuation_pages == 1


def test_mandatory_continuation_is_the_baseline_for_ordinary_trials() -> None:
    units, translation, _ = make_bundle(
        [
            ("body", "robust method", "稳健方法"),
            ("body", "robust method", "稳健方法"),
        ]
    )
    teaching = TeachingCandidate(
        key="robust-method",
        english_original="robust method",
        chinese_meaning="稳健方法",
        occurrences=(
            TeachingOccurrence("u-0-body", 0, 13, 0, 4),
            TeachingOccurrence("u-1-body", 0, 13, 0, 4),
        ),
        value_priority=100,
    )

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        if request.phase == "mandatory-only":
            return LayoutTrialResult(True, 2)
        if request.phase == "final-frozen":
            return LayoutTrialResult(True, 2)
        newest = request.placements[-1]
        return LayoutTrialResult(True, 3 if newest.unit_id == "u-0-body" else 2)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(teaching,),
        auxiliary_size_mpt=8_600,
        trial_layout=trial,
    )

    assert result.continuation_pages == 2
    assert result.placements[0].unit_id == "u-1-body"
    assert result.placements[0].deferred_occurrences == 1


def test_every_layout_trial_binds_the_same_frozen_mandatory_items() -> None:
    units, translation, review = make_bundle([("body", "Body", "甲" * 100)])
    red = select_red_emphasis(
        units,
        translation,
        (
            RedCandidate(
                "core",
                "u-0-body",
                0,
                6,
                RedImportance.CORE_CONCLUSION,
            ),
        ),
    )
    requests: list[LayoutTrialRequest] = []

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        requests.append(request)
        return LayoutTrialResult(True, 0)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(),
        mandatory_items=red.items,
        auxiliary_size_mpt=8_600,
        trial_layout=trial,
    )

    assert requests
    assert all(request.mandatory_items == red.items for request in requests)
    assert all(
        request.mandatory_items_hash == result.mandatory_items_hash
        for request in requests
    )
    artifact = result.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    assert artifact["mandatory_items_hash"] == result.mandatory_items_hash
