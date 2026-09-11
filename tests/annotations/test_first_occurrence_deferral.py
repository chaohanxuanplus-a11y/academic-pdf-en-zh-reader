# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
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
        return LayoutTrialResult(True, 0, 1, 1)

    a = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(first, second),
        auxiliary_size_mpt=9_000,
        trial_layout=fit,
    )
    b = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(second, first),
        auxiliary_size_mpt=9_000,
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
