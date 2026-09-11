# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.annotations.ambiguity import (
    AMBIGUITY_LABEL,
    AmbiguityOccurrence,
    build_ambiguity_marks,
)
from academic_pdf_en_zh_reader.annotations.red_emphasis import (
    RedCandidate,
    RedImportance,
    select_red_emphasis,
)
from academic_pdf_en_zh_reader.annotations.selection import (
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


def test_every_ambiguity_is_underlined_and_each_key_has_one_same_size_label(
    ambiguity_key: dict[str, object],
) -> None:
    units, translation, review = make_bundle(
        [
            ("abstract", "associated with", "与其相关"),
            ("body", "again associated with", "再次与其相关"),
        ]
    )
    review["issues"] = [
        {
            "id": "ambiguity-1",
            "unit_id": "u-0-abstract",
            "severity": "unresolved_ambiguity",
            "status": "unresolved",
            "message": "Association may not imply mechanism.",
            "ambiguity_key": ambiguity_key,
        }
    ]
    style = build_style_contract((FontSizeSample(10_000, 100),))
    marks = build_ambiguity_marks(
        units,
        translation,
        review,
        (
            AmbiguityOccurrence(str(ambiguity_key["id"]), "u-1-body", 2, 6),
            AmbiguityOccurrence(str(ambiguity_key["id"]), "u-0-abstract", 0, 4),
        ),
        style,
    )

    assert [mark.unit_id for mark in marks] == ["u-0-abstract", "u-1-body"]
    assert all(mark.underline for mark in marks)
    assert [mark.label for mark in marks] == [AMBIGUITY_LABEL, None]
    assert marks[0].label_after_span is True
    assert marks[0].label_size_mpt == style.style_for("abstract").size_mpt
    assert marks[1].label_size_mpt == style.style_for("body").size_mpt


def test_every_unresolved_key_requires_an_exact_in_bounds_occurrence(
    ambiguity_key: dict[str, object],
) -> None:
    units, translation, review = make_bundle([("body", "associated", "存在相关")])
    review["issues"] = [
        {
            "id": "ambiguity-1",
            "unit_id": "u-0-body",
            "severity": "unresolved_ambiguity",
            "status": "unresolved",
            "message": "Meaning remains unresolved.",
            "ambiguity_key": ambiguity_key,
        }
    ]
    style = build_style_contract((FontSizeSample(10_000, 100),))
    with pytest.raises(AnnotationValidationError, match="has no occurrence"):
        build_ambiguity_marks(units, translation, review, (), style)
    with pytest.raises(AnnotationValidationError, match="target span"):
        build_ambiguity_marks(
            units,
            translation,
            review,
            (AmbiguityOccurrence(str(ambiguity_key["id"]), "u-0-body", 0, 99),),
            style,
        )


def test_ambiguity_span_must_contain_translated_content(
    ambiguity_key: dict[str, object],
) -> None:
    units, translation, review = make_bundle([("body", "associated", "，相关")])
    review["issues"] = [
        {
            "id": "ambiguity-1",
            "unit_id": "u-0-body",
            "severity": "unresolved_ambiguity",
            "status": "unresolved",
            "message": "Meaning remains unresolved.",
            "ambiguity_key": ambiguity_key,
        }
    ]
    style = build_style_contract((FontSizeSample(10_000, 100),))
    with pytest.raises(AnnotationValidationError, match="visible content"):
        build_ambiguity_marks(
            units,
            translation,
            review,
            (AmbiguityOccurrence(str(ambiguity_key["id"]), "u-0-body", 0, 1),),
            style,
        )


def test_complete_artifact_rebinds_ratios_selection_and_parent_hashes(
    ambiguity_key: dict[str, object],
) -> None:
    units, translation, review = make_bundle(
        [
            ("abstract", "associated with", "与其相关"),
            ("body", "result", "甲" * 100),
        ]
    )
    review["issues"] = [
        {
            "id": "ambiguity-1",
            "unit_id": "u-0-abstract",
            "severity": "unresolved_ambiguity",
            "status": "unresolved",
            "message": "Association may not imply mechanism.",
            "ambiguity_key": ambiguity_key,
        }
    ]
    style = build_style_contract((FontSizeSample(10_000, 100),))
    marks = build_ambiguity_marks(
        units,
        translation,
        review,
        (AmbiguityOccurrence(str(ambiguity_key["id"]), "u-0-abstract", 0, 4),),
        style,
    )
    red = select_red_emphasis(
        units,
        translation,
        (
            RedCandidate(
                "result",
                "u-1-body",
                0,
                6,
                RedImportance.CORE_CONCLUSION,
            ),
        ),
        ambiguity_spans=tuple(mark.span for mark in marks),
    )
    orange = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(),
        mandatory_items=(*red.items, *marks),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _: LayoutTrialResult(True, 0, 1, 1),
    )
    artifact = orange.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )

    assert artifact["highlight_ratio_basis_points"] == 600
    validate_annotations_against_inputs(units, translation, review, artifact, style)
    artifact["candidate_set_hash"] = "c" * 64
    with pytest.raises(AnnotationValidationError, match="input hash"):
        validate_annotations_against_inputs(units, translation, review, artifact, style)
