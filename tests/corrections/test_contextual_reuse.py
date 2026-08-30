# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.corrections.database import (
    CorrectionDraft,
    CorrectionStore,
)
from academic_pdf_en_zh_reader.corrections.retrieve import (
    RetrievalContext,
    retrieve_suggestions,
)


def _private_for_tests(_path: Path, _is_directory: bool) -> None:
    return None


@pytest.fixture
def store(tmp_path: Path) -> CorrectionStore:
    result = CorrectionStore(
        tmp_path / "personal-corrections.sqlite3",
        permission_hardener=_private_for_tests,
    )
    result.record_authorized_correction(
        CorrectionDraft(
            english_expression="robust",
            preferred_chinese="稳健的",
            ambiguity_key_id="a" * 64,
            domain="statistics",
            source_part_of_speech="adjective",
            source_syntax="attributive adjective",
            target_grammar_function="modifier",
            core_collocation="robust estimator",
            semantic_tags=("method quality",),
        ),
        source_annotation={
            "kind": "bright-red-ambiguity",
            "underline": True,
            "ambiguity_key_id": "a" * 64,
        },
        correction_source="user-explicit",
        authorized=True,
    )
    return result


def _context(**changes: object) -> RetrievalContext:
    values: dict[str, object] = {
        "english_expression": "robust",
        "domain": "statistics",
        "source_part_of_speech": "adjective",
        "source_syntax": "attributive adjective",
        "target_grammar_function": "modifier",
        "core_collocation": "robust estimator",
        "semantic_tags": ("method quality",),
    }
    values.update(changes)
    return RetrievalContext(**values)  # type: ignore[arg-type]


def test_exact_context_returns_scored_evidence_never_an_auto_replacement(
    store: CorrectionStore,
) -> None:
    [suggestion] = retrieve_suggestions(store, _context())

    assert suggestion.preferred_chinese == "稳健的"
    assert suggestion.score_basis_points == 10_000
    assert suggestion.evidence_only is True
    assert suggestion.auto_apply is False
    assert suggestion.requires_re_review is False
    assert "exact_expression" in suggestion.reasons
    assert "exact_context_fingerprint" in suggestion.reasons


def test_caller_supplied_near_synonym_is_suggestion_only(
    store: CorrectionStore,
) -> None:
    [suggestion] = retrieve_suggestions(
        store,
        _context(english_expression="resilient", related_expressions=("robust",)),
    )

    assert suggestion.auto_apply is False
    assert suggestion.score_basis_points < 10_000
    assert "caller_supplied_related_expression" in suggestion.reasons


def test_cross_part_of_speech_requires_adaptation_and_review(
    store: CorrectionStore,
) -> None:
    [suggestion] = retrieve_suggestions(
        store,
        _context(
            source_part_of_speech="noun",
            source_syntax="subject noun",
            target_grammar_function="subject",
        ),
    )

    assert suggestion.requires_grammar_adaptation is True
    assert suggestion.requires_re_review is True
    assert "source_part_of_speech_changed" in suggestion.reasons


def test_new_domain_or_context_conflict_triggers_re_review(
    store: CorrectionStore,
) -> None:
    [suggestion] = retrieve_suggestions(
        store,
        _context(
            domain="control engineering",
            core_collocation="robust control law",
            semantic_tags=("controller stability",),
        ),
    )

    assert suggestion.requires_re_review is True
    assert "domain_conflict" in suggestion.reasons
    assert "context_conflict" in suggestion.reasons


def test_unrelated_expression_is_not_invented_as_a_synonym(
    store: CorrectionStore,
) -> None:
    assert (
        retrieve_suggestions(
            store,
            _context(english_expression="accurate", related_expressions=()),
        )
        == ()
    )


def test_suggestion_order_is_stable_by_score_then_record_id(
    store: CorrectionStore,
) -> None:
    second_key = "b" * 64
    store.record_authorized_correction(
        CorrectionDraft(
            english_expression="robust",
            preferred_chinese="鲁棒的",
            ambiguity_key_id=second_key,
            domain="control engineering",
            source_part_of_speech="adjective",
            source_syntax="attributive adjective",
            target_grammar_function="modifier",
            core_collocation="robust control",
            semantic_tags=("controller stability",),
        ),
        source_annotation={
            "kind": "bright-red-ambiguity",
            "underline": True,
            "ambiguity_key_id": second_key,
        },
        correction_source="user-explicit",
        authorized=True,
    )

    first = retrieve_suggestions(store, _context())
    second = retrieve_suggestions(store, _context())

    assert first == second
    assert [item.score_basis_points for item in first] == sorted(
        (item.score_basis_points for item in first), reverse=True
    )
