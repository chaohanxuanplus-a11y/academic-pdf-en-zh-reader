# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace

import pytest

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    FrozenOrangeCandidateSet,
    LayoutTrialResult,
    freeze_orange_candidate_set,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
)

from .conftest import make_bundle


def _candidates():
    teaching = (
        TeachingCandidate(
            key="later-term",
            english_original="later term",
            chinese_meaning="后一个术语",
            occurrences=(TeachingOccurrence("u-1-body", 0, 10, 0, 5),),
            value_priority=10,
        ),
        TeachingCandidate(
            key="core-term",
            english_original="core term",
            chinese_meaning="核心术语",
            occurrences=(TeachingOccurrence("u-0-body", 0, 9, 0, 4),),
            value_priority=100,
        ),
    )
    figure = FigureNoteCandidate(
        key="figure-trend",
        figure_id="figure-1",
        caption_unit_id="u-2-figure-caption",
        target_start=0,
        target_end=7,
        content="变量持续上升。",
        compact_content="变量上升。",
        evidence=(DirectEvidence("u-2-figure-caption", 0, 14, "Figure 1 rises"),),
        value_priority=80,
        essential=True,
    )
    return teaching, (figure,)


def test_candidate_freeze_is_order_independent_and_stably_sorted() -> None:
    units, translation, _ = make_bundle(
        [
            ("body", "core term", "核心术语"),
            ("body", "later term", "后一个术语"),
            ("figure-caption", "Figure 1 rises", "图1呈上升趋势"),
        ]
    )
    teaching, figures = _candidates()

    forward = freeze_orange_candidate_set(
        units,
        translation,
        teaching_candidates=teaching,
        figure_candidates=figures,
    )
    reverse = freeze_orange_candidate_set(
        units,
        translation,
        teaching_candidates=tuple(reversed(teaching)),
        figure_candidates=tuple(reversed(figures)),
    )

    assert isinstance(forward, FrozenOrangeCandidateSet)
    assert forward == reverse
    assert [candidate.key for candidate in forward.teaching_candidates] == [
        "core-term",
        "later-term",
    ]


def test_candidate_change_changes_hash_and_forged_hash_is_rejected() -> None:
    units, translation, _ = make_bundle(
        [
            ("body", "core term", "核心术语"),
            ("body", "later term", "后一个术语"),
            ("figure-caption", "Figure 1 rises", "图1呈上升趋势"),
        ]
    )
    teaching, figures = _candidates()
    frozen = freeze_orange_candidate_set(
        units,
        translation,
        teaching_candidates=teaching,
        figure_candidates=figures,
    )
    changed = freeze_orange_candidate_set(
        units,
        translation,
        teaching_candidates=(
            replace(teaching[0], chinese_meaning="后续术语"),
            teaching[1],
        ),
        figure_candidates=figures,
    )

    assert frozen.candidate_set_hash != changed.candidate_set_hash

    forged = replace(frozen, candidate_set_hash="f" * 64)
    with pytest.raises(AnnotationValidationError, match="candidate-set hash"):
        select_orange_annotations(
            units,
            translation,
            candidate_set=forged,
            auxiliary_size_mpt=9_000,
            trial_layout=lambda _: LayoutTrialResult(True, 0, 1, 1),
        )
