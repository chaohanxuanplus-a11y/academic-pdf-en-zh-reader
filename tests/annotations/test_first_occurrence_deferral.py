# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
    format_teaching_content,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialRequest,
    LayoutTrialResult,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    validate_annotations_against_inputs,
)
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)

from .conftest import make_bundle


def test_repeated_term_can_defer_but_single_high_value_term_is_kept_first() -> None:
    units, translation, _ = make_bundle(
        [
            ("body", "rare term and common term", "罕见术语与常见术语"),
            ("body", "common term again", "再次出现常见术语"),
        ]
    )
    single = TeachingCandidate(
        key="rare-term",
        english_original="rare term",
        chinese_meaning="罕见术语",
        occurrences=(TeachingOccurrence("u-0-body", 0, 9, 0, 4),),
        value_priority=80,
    )
    repeated = TeachingCandidate(
        key="common-term",
        english_original="common term",
        chinese_meaning="常见术语",
        occurrences=(
            TeachingOccurrence("u-0-body", 14, 25, 5, 9),
            TeachingOccurrence("u-1-body", 0, 11, 4, 8),
        ),
        value_priority=100,
    )
    attempted: list[tuple[str, str]] = []

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        if request.phase != "candidate-trial":
            return LayoutTrialResult(True, 0)
        newest = request.placements[-1]
        attempted.append((newest.candidate_key, newest.unit_id))
        if newest.candidate_key == "common-term" and newest.unit_id == "u-0-body":
            return LayoutTrialResult(False, 0)
        return LayoutTrialResult(True, 0)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(repeated, single),
        auxiliary_size_mpt=8_600,
        trial_layout=trial,
    )

    assert attempted == [
        ("rare-term", "u-0-body"),
        ("common-term", "u-0-body"),
        ("common-term", "u-1-body"),
    ]
    assert [item.candidate_key for item in result.placements] == [
        "rare-term",
        "common-term",
    ]
    deferred = result.placements[1]
    assert deferred.unit_id == "u-1-body"
    assert deferred.deferred_occurrences == 1
    assert deferred.content == "common term — 常见术语"
    assert deferred.attachment == "below-translation"
    assert format_teaching_content("common term", "常见术语") == deferred.content
    assert {item.auxiliary_size_mpt for item in result.placements} == {8_600}


def test_selection_is_stable_under_candidate_input_order() -> None:
    units, translation, review = make_bundle([("body", "alpha beta", "阿尔法贝塔")])
    first = TeachingCandidate(
        key="alpha",
        english_original="alpha",
        chinese_meaning="阿尔法",
        occurrences=(TeachingOccurrence("u-0-body", 0, 5, 0, 3),),
        value_priority=10,
    )
    second = TeachingCandidate(
        key="beta",
        english_original="beta",
        chinese_meaning="贝塔",
        occurrences=(TeachingOccurrence("u-0-body", 6, 10, 3, 5),),
        value_priority=20,
    )

    def fit(_: LayoutTrialRequest) -> LayoutTrialResult:
        return LayoutTrialResult(True, 0)

    a = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(first, second),
        auxiliary_size_mpt=8_600,
        trial_layout=fit,
    )
    b = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(second, first),
        auxiliary_size_mpt=8_600,
        trial_layout=fit,
    )
    assert a == b
    style = build_style_contract((FontSizeSample(10_000, 100),))
    artifact = a.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    validate_annotations_against_inputs(units, translation, review, artifact, style)
