# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.annotations.red_emphasis import (
    RedCandidate,
    RedImportance,
    select_red_emphasis,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
    BoundTargetSpan,
)

from .conftest import make_bundle


def test_red_target_is_six_percent_without_minimum_and_cap_is_exact() -> None:
    units, translation, _ = make_bundle(
        [
            ("abstract", "Abstract", "摘要中的结论本来就很重要"),
            ("body", "Body", "甲" * 100),
        ]
    )
    body_id = "u-1-body"
    selected = select_red_emphasis(
        units,
        translation,
        (
            RedCandidate(
                "too-large",
                body_id,
                20,
                31,
                RedImportance.CORE_CONCLUSION,
            ),
            RedCandidate(
                "six-percent",
                body_id,
                0,
                6,
                RedImportance.DIRECT_RESULT,
            ),
        ),
    )

    assert [item.candidate_id for item in selected.items] == ["six-percent"]
    assert selected.denominator_characters == 100
    assert selected.highlighted_characters == 6
    assert selected.ratio_basis_points == 600
    assert select_red_emphasis(units, translation, ()).ratio_basis_points == 0


def test_equal_distance_from_target_keeps_the_smaller_highlight_set() -> None:
    units, translation, _ = make_bundle([("body", "Body", "甲" * 100)])
    selected = select_red_emphasis(
        units,
        translation,
        (
            RedCandidate(
                "first-four",
                "u-0-body",
                0,
                4,
                RedImportance.DIRECT_RESULT,
            ),
            RedCandidate(
                "second-four",
                "u-0-body",
                4,
                8,
                RedImportance.DIRECT_RESULT,
            ),
        ),
    )

    assert [item.candidate_id for item in selected.items] == ["first-four"]
    assert selected.ratio_basis_points == 400


def test_red_denominator_ignores_excluded_roles_and_punctuation() -> None:
    units, translation, _ = make_bundle(
        [
            ("title", "Title", "题目甲乙"),
            ("abstract", "Abstract", "摘要甲乙"),
            ("keywords", "Keywords", "关键词甲乙"),
            ("body", "Body", "甲乙， 丙丁。戊己"),
        ]
    )
    body_id = "u-3-body"
    selected = select_red_emphasis(
        units,
        translation,
        (),
        ambiguity_spans=(BoundTargetSpan(body_id, 7, 9),),
    )

    assert selected.denominator_characters == 6
    assert selected.highlighted_characters == 0


def test_ambiguity_translation_span_remains_in_the_body_denominator() -> None:
    units, translation, _ = make_bundle([("body", "Body", "甲" * 100)])
    selected = select_red_emphasis(
        units,
        translation,
        (
            RedCandidate(
                "nine-percent",
                "u-0-body",
                10,
                19,
                RedImportance.DIRECT_RESULT,
            ),
        ),
        ambiguity_spans=(BoundTargetSpan("u-0-body", 0, 10),),
    )

    assert selected.denominator_characters == 100
    assert selected.ratio_basis_points == 900


@pytest.mark.parametrize("role", ["title", "abstract", "keywords"])
def test_red_candidates_in_excluded_roles_fail_closed(role: str) -> None:
    units, translation, _ = make_bundle([(role, "Source", "中文")])
    with pytest.raises(AnnotationValidationError, match="excluded role"):
        select_red_emphasis(
            units,
            translation,
            (RedCandidate("invalid", f"u-0-{role}", 0, 1, RedImportance.OTHER),),
        )


def test_red_and_ambiguity_styles_can_overlap_but_punctuation_fails_closed() -> None:
    units, translation, _ = make_bundle([("body", "Source", "甲" * 20)])
    unit_id = "u-0-body"
    selected = select_red_emphasis(
        units,
        translation,
        (RedCandidate("overlap", unit_id, 0, 1, RedImportance.OTHER),),
        ambiguity_spans=(BoundTargetSpan(unit_id, 0, 1),),
    )
    assert [item.candidate_id for item in selected.items] == ["overlap"]

    punctuation_units, punctuation_translation, _ = make_bundle(
        [("body", "Source", "甲，。乙")]
    )
    with pytest.raises(AnnotationValidationError, match="visible content"):
        select_red_emphasis(
            punctuation_units,
            punctuation_translation,
            (RedCandidate("punctuation", unit_id, 1, 3, RedImportance.OTHER),),
        )
