# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.review.review_validation import (
    ReviewValidationError,
    make_ambiguity_key,
    validate_review,
)


def _translation() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": "a" * 64,
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": "unit-1",
                "chinese_text": "该响应可能与载荷有关。",
                "spans": [],
                "terminology": [],
            }
        ],
    }


def _review(translation: dict[str, object]) -> dict[str, object]:
    key = make_ambiguity_key(
        english_expression="may be associated with",
        syntactic_structure="modal may plus passive association predicate",
        candidate_meanings=("可能相关", "可能由其间接伴随"),
        disciplinary_context="biomaterials loading-response analysis",
        ambiguity_reason=(
            "The local evidence cannot distinguish statistical association "
            "from an indirect mechanistic relation."
        ),
    )
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": ["unit-1"],
        "issues": [
            {
                "id": "ambiguity-1",
                "unit_id": "unit-1",
                "severity": "unresolved_ambiguity",
                "status": "unresolved",
                "message": "The modal relation remains materially ambiguous.",
                "ambiguity_key": key,
            }
        ],
        "final_status": "passed",
    }


def test_unresolved_ambiguity_can_pass_with_sufficient_stable_key() -> None:
    translation = _translation()
    review = _review(translation)

    result = validate_review(translation, review)

    assert result.unresolved_ambiguity_keys == (
        review["issues"][0]["ambiguity_key"]["id"],
    )


def test_ambiguity_key_is_stable_under_whitespace_and_candidate_order() -> None:
    first = make_ambiguity_key(
        english_expression="may be associated with",
        syntactic_structure="modal may plus passive association predicate",
        candidate_meanings=("可能相关", "可能由其间接伴随"),
        disciplinary_context="biomaterials loading-response analysis",
        ambiguity_reason="Context cannot distinguish association from mechanism.",
    )
    second = make_ambiguity_key(
        english_expression="  MAY   be associated with ",
        syntactic_structure="Modal may plus passive association predicate",
        candidate_meanings=("可能由其间接伴随", "可能相关"),
        disciplinary_context="Biomaterials loading-response analysis",
        ambiguity_reason="Context cannot distinguish association from mechanism.",
    )

    assert first["id"] == second["id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("english_expression", "term ambiguity"),
        ("syntactic_structure", "ambiguous"),
        ("candidate_meanings", ["可能相关", "可能相关"]),
        ("candidate_meanings", ["A", "B"]),
        ("disciplinary_context", "general"),
        ("ambiguity_reason", "术语歧义"),
    ],
)
def test_broad_or_insufficient_ambiguity_key_is_rejected(
    field: str,
    value: object,
) -> None:
    translation = _translation()
    review = _review(translation)
    review["issues"][0]["ambiguity_key"][field] = value

    with pytest.raises(ReviewValidationError, match="ambiguity_key"):
        validate_review(translation, review)


def test_tampered_ambiguity_key_id_is_rejected() -> None:
    translation = _translation()
    review = _review(translation)
    review["issues"][0]["ambiguity_key"]["id"] = "f" * 64

    with pytest.raises(ReviewValidationError, match="ambiguity_key"):
        validate_review(translation, review)


def test_ambiguity_key_is_required_only_for_unresolved_ambiguity() -> None:
    translation = _translation()
    review = _review(translation)
    missing = deepcopy(review)
    del missing["issues"][0]["ambiguity_key"]

    with pytest.raises(ReviewValidationError, match="ambiguity_key"):
        validate_review(translation, missing)
