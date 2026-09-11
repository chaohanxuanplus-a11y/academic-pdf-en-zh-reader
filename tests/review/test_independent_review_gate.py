# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.corrections.contracts import (
    CorrectionContext,
    select_correction_evidence,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.review.review_validation import (
    ReviewValidationError,
    validate_review,
)


def _translation() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": "a" * 64,
        "translator_id": "translator-agent-1",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": "unit-1",
                "chinese_text": "第一段译文。",
                "spans": [],
                "terminology": [],
            },
            {
                "unit_id": "unit-2",
                "chinese_text": "第二段译文。",
                "spans": [],
                "terminology": [],
            },
        ],
    }


def _review(translation: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent-1",
        "reviewer_id": "reviewer-agent-2",
        "reviewed_unit_ids": ["unit-1", "unit-2"],
        "issues": [],
        "final_status": "passed",
    }


def test_independent_review_binds_identity_hash_and_all_unit_ids() -> None:
    translation = _translation()

    result = validate_review(translation, _review(translation))

    assert result.translation_hash == sha256_canonical(translation)
    assert result.reviewed_unit_ids == ("unit-1", "unit-2")
    assert result.unresolved_ambiguity_keys == ()


@pytest.mark.parametrize("missing", ["translator_id", "reviewer_id"])
def test_both_agent_identities_are_required(missing: str) -> None:
    translation = _translation()
    review = _review(translation)
    del review[missing]

    with pytest.raises(ReviewValidationError, match="identity"):
        validate_review(translation, review)


def test_reviewer_must_be_distinct_from_translator() -> None:
    translation = _translation()
    review = _review(translation)
    review["reviewer_id"] = " TRANSLATOR-Agent-1 "

    with pytest.raises(ReviewValidationError, match="distinct"):
        validate_review(translation, review)


def test_review_translator_identity_must_match_translation_batch() -> None:
    translation = _translation()
    review = _review(translation)
    review["translator_id"] = "different-translator"

    with pytest.raises(ReviewValidationError, match="translator_id"):
        validate_review(translation, review)


def test_translation_hash_is_exactly_bound() -> None:
    translation = _translation()
    review = _review(translation)
    review["translation_hash"] = "b" * 64

    with pytest.raises(ReviewValidationError, match="translation_hash"):
        validate_review(translation, review)


@pytest.mark.parametrize(
    "reviewed_ids",
    [
        ["unit-1"],
        ["unit-1", "unit-2", "unit-extra"],
        ["unit-1", "unit-1"],
        ["unit-2", "unit-1"],
    ],
)
def test_reviewed_unit_ids_must_match_translation_once_and_in_order(
    reviewed_ids: list[str],
) -> None:
    translation = _translation()
    review = _review(translation)
    review["reviewed_unit_ids"] = reviewed_ids

    with pytest.raises(ReviewValidationError, match="unit IDs"):
        validate_review(translation, review)


def test_issue_unit_id_must_belong_to_bound_translation() -> None:
    translation = _translation()
    review = _review(translation)
    review["issues"] = [
        {
            "id": "issue-1",
            "unit_id": "unit-missing",
            "severity": "hard_error",
            "status": "resolved",
            "message": "A number was corrected.",
            "resolution": "Restored the source value.",
        }
    ]

    with pytest.raises(ReviewValidationError, match="unknown unit"):
        validate_review(translation, review)


def test_unresolved_hard_error_and_failed_status_block_progression() -> None:
    translation = _translation()
    review = _review(translation)
    review["final_status"] = "failed"
    review["issues"] = [
        {
            "id": "issue-1",
            "unit_id": "unit-1",
            "severity": "hard_error",
            "status": "unresolved",
            "message": "The direction of change is wrong.",
        }
    ]

    with pytest.raises(ReviewValidationError, match="hard_error"):
        validate_review(translation, review)

    review["issues"][0]["status"] = "resolved"
    review["issues"][0]["resolution"] = "Corrected increase to decrease."
    with pytest.raises(ReviewValidationError, match="final_status"):
        validate_review(translation, review)


def test_style_improvement_is_structured_and_does_not_block() -> None:
    translation = _translation()
    review = _review(translation)
    review["issues"] = [
        {
            "id": "style-1",
            "unit_id": "unit-2",
            "severity": "style_improvement",
            "status": "unresolved",
            "message": "The Chinese can be more idiomatic without changing meaning.",
            "style_improvement": {
                "current_chinese": "第二段译文。",
                "suggested_chinese": "第二段的译文。",
                "rationale": "Improves idiomatic flow without changing the claim.",
            },
        }
    ]

    result = validate_review(translation, review)

    assert len(result.style_improvements) == 1
    del review["issues"][0]["style_improvement"]
    with pytest.raises(ReviewValidationError, match="style_improvement"):
        validate_review(translation, review)


def _suggestion(
    identifier: str,
    *,
    domain: str = "statistics",
    source_part_of_speech: str = "adjective",
    target_grammar_function: str = "modifier",
    minimal_context: str = "robust estimator under severe contamination",
    conflict: bool = False,
) -> dict[str, object]:
    return {
        "id": identifier,
        "english": "robust",
        "normalized_english": "robust",
        "preferred_chinese": "稳健的",
        "context_hash": "c" * 64,
        "domain": domain,
        "source_part_of_speech": source_part_of_speech,
        "target_grammar_function": target_grammar_function,
        "semantic_tags": ["method-quality"],
        "minimal_context": minimal_context,
        "conflict": conflict,
    }


def test_correction_suggestions_are_contextual_evidence_not_replacements() -> None:
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "correction-suggestions",
        "read_only": True,
        "suggestions": [
            _suggestion("match"),
            _suggestion("wrong-domain", domain="materials science"),
            _suggestion("wrong-grammar", target_grammar_function="subject"),
            _suggestion("weak-context", minimal_context="unrelated endpoint"),
            _suggestion("conflict", conflict=True),
        ],
    }
    context = CorrectionContext(
        english="robust",
        domain="statistics",
        source_part_of_speech="adjective",
        target_grammar_function="modifier",
        minimal_context="robust estimator under contamination",
        semantic_tags=("method-quality",),
    )

    evidence = select_correction_evidence(artifact, context)

    assert [item.id for item in evidence] == ["match"]
    assert evidence[0].preferred_chinese == "稳健的"
    assert evidence[0].evidence_only is True
    assert evidence[0].requires_grammar_adaptation is False


def test_cross_part_of_speech_is_only_adaptation_evidence() -> None:
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "correction-suggestions",
        "read_only": True,
        "suggestions": [
            _suggestion(
                "cross-pos",
                source_part_of_speech="noun",
                target_grammar_function="subject",
            )
        ],
    }
    context = CorrectionContext(
        english="robust",
        domain="statistics",
        source_part_of_speech="adjective",
        target_grammar_function="modifier",
        minimal_context="robust estimator under contamination",
        semantic_tags=("method-quality",),
    )

    [evidence] = select_correction_evidence(artifact, context)

    assert evidence.evidence_only is True
    assert evidence.requires_grammar_adaptation is True
    assert evidence.preferred_chinese == "稳健的"

    without_semantic_support = CorrectionContext(
        english=context.english,
        domain=context.domain,
        source_part_of_speech=context.source_part_of_speech,
        target_grammar_function=context.target_grammar_function,
        minimal_context=context.minimal_context,
    )
    assert select_correction_evidence(artifact, without_semantic_support) == ()


def test_same_part_of_speech_still_requires_shared_semantic_support() -> None:
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "correction-suggestions",
        "read_only": True,
        "suggestions": [_suggestion("same-pos")],
    }
    context = CorrectionContext(
        english="robust",
        domain="statistics",
        source_part_of_speech="adjective",
        target_grammar_function="modifier",
        minimal_context="robust estimator under contamination",
    )

    assert select_correction_evidence(artifact, context) == ()


def test_unflagged_conflicting_preferences_are_withheld() -> None:
    first = _suggestion("first")
    second = _suggestion("second")
    second["preferred_chinese"] = "鲁棒的"
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "correction-suggestions",
        "read_only": True,
        "suggestions": [first, second],
    }
    context = CorrectionContext(
        english="robust",
        domain="statistics",
        source_part_of_speech="adjective",
        target_grammar_function="modifier",
        minimal_context="robust estimator under contamination",
        semantic_tags=("method-quality",),
    )

    assert select_correction_evidence(artifact, context) == ()
