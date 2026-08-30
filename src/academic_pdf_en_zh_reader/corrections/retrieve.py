# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Retrieve scored personal evidence without ever applying a replacement."""

from __future__ import annotations

from dataclasses import dataclass, replace

from academic_pdf_en_zh_reader.corrections.database import (
    CorrectionError,
    CorrectionRecord,
    CorrectionStore,
)
from academic_pdf_en_zh_reader.corrections.normalize import (
    NormalizationError,
    compact_text,
    context_fingerprint,
    normalized_english,
    normalized_label,
    normalized_tags,
)


@dataclass(frozen=True)
class RetrievalContext:
    english_expression: str
    domain: str
    source_part_of_speech: str
    source_syntax: str
    target_grammar_function: str
    core_collocation: str
    semantic_tags: tuple[str, ...]
    related_expressions: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorrectionSuggestion:
    record_id: str
    english_expression: str
    preferred_chinese: str
    score_basis_points: int
    reasons: tuple[str, ...]
    requires_re_review: bool
    requires_grammar_adaptation: bool
    evidence_only: bool = True
    auto_apply: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "auto_apply": self.auto_apply,
            "english_expression": self.english_expression,
            "evidence_only": self.evidence_only,
            "preferred_chinese": self.preferred_chinese,
            "reasons": list(self.reasons),
            "record_id": self.record_id,
            "requires_grammar_adaptation": self.requires_grammar_adaptation,
            "requires_re_review": self.requires_re_review,
            "score_basis_points": self.score_basis_points,
        }


@dataclass(frozen=True)
class _NormalizedContext:
    expression: str
    related: tuple[str, ...]
    domain: str
    source_part_of_speech: str
    source_syntax: str
    target_grammar_function: str
    core_collocation: str
    semantic_tags: tuple[str, ...]
    fingerprint: str


def _normalize(context: RetrievalContext) -> _NormalizedContext:
    try:
        expression = normalized_english(context.english_expression)
        related = tuple(
            sorted({normalized_english(value) for value in context.related_expressions})
        )
        domain = normalized_label(context.domain, maximum=80)
        part_of_speech = normalized_label(
            context.source_part_of_speech,
            maximum=80,
        )
        syntax = normalized_label(context.source_syntax, maximum=160)
        target = normalized_label(context.target_grammar_function, maximum=80)
        collocation = compact_text(
            context.core_collocation,
            maximum=120,
            casefold=True,
        )
        tags = normalized_tags(context.semantic_tags)
    except NormalizationError as exc:
        raise CorrectionError("INVALID_INPUT") from exc
    fingerprint = context_fingerprint(
        domain=domain,
        source_part_of_speech=part_of_speech,
        source_syntax=syntax,
        target_grammar_function=target,
        core_collocation=collocation,
        semantic_tags=tags,
    )
    return _NormalizedContext(
        expression=expression,
        related=related,
        domain=domain,
        source_part_of_speech=part_of_speech,
        source_syntax=syntax,
        target_grammar_function=target,
        core_collocation=collocation,
        semantic_tags=tags,
        fingerprint=fingerprint,
    )


def _score(
    record: CorrectionRecord,
    context: _NormalizedContext,
) -> CorrectionSuggestion:
    score = 0
    reasons: list[str] = []
    requires_review = False
    grammar_adaptation = False

    if record.normalized_english == context.expression:
        score += 4_500
        reasons.append("exact_expression")
    else:
        score += 3_000
        reasons.append("caller_supplied_related_expression")

    if record.domain == context.domain:
        score += 1_000
        reasons.append("same_domain")
    else:
        reasons.append("domain_conflict")
        requires_review = True

    if record.context_fingerprint == context.fingerprint:
        score += 2_500
        reasons.append("exact_context_fingerprint")
    else:
        if record.core_collocation == context.core_collocation:
            score += 1_000
            reasons.append("same_core_collocation")
        shared_tags = set(record.semantic_tags) & set(context.semantic_tags)
        if shared_tags:
            score += 500
            reasons.append("shared_semantic_tags")
        if record.core_collocation != context.core_collocation:
            reasons.append("context_conflict")
            requires_review = True

    if record.source_part_of_speech == context.source_part_of_speech:
        score += 500
        reasons.append("same_source_part_of_speech")
    else:
        reasons.append("source_part_of_speech_changed")
        requires_review = True
        grammar_adaptation = True

    if record.source_syntax == context.source_syntax:
        score += 500
        reasons.append("same_source_syntax")
    else:
        reasons.append("source_syntax_changed")
        requires_review = True
        grammar_adaptation = True

    if record.target_grammar_function == context.target_grammar_function:
        score += 500
        reasons.append("same_target_grammar_function")
    else:
        reasons.append("target_grammar_function_changed")
        requires_review = True
        grammar_adaptation = True

    if set(record.semantic_tags) & set(context.semantic_tags):
        score += 500
        reasons.append("semantic_support")
    else:
        reasons.append("semantic_context_conflict")
        requires_review = True

    return CorrectionSuggestion(
        record_id=record.id,
        english_expression=record.english_expression,
        preferred_chinese=record.preferred_chinese,
        score_basis_points=min(score, 10_000),
        reasons=tuple(reasons),
        requires_re_review=requires_review,
        requires_grammar_adaptation=grammar_adaptation,
    )


def retrieve_suggestions(
    store: CorrectionStore,
    context: RetrievalContext,
) -> tuple[CorrectionSuggestion, ...]:
    """Return deterministic evidence with reasons and scores, never output text."""

    normalized = _normalize(context)
    records = store.find_active_by_expressions(
        (normalized.expression, *normalized.related)
    )
    suggestions = [_score(record, normalized) for record in records]

    preferences: dict[str, set[str]] = {}
    for record in records:
        preferences.setdefault(record.normalized_english, set()).add(
            record.preferred_chinese
        )
    conflicting_ids = {
        record.id
        for record in records
        if len(preferences[record.normalized_english]) > 1
    }
    suggestions = [
        replace(
            suggestion,
            reasons=(*suggestion.reasons, "conflicting_personal_records"),
            requires_re_review=True,
        )
        if suggestion.record_id in conflicting_ids
        else suggestion
        for suggestion in suggestions
    ]
    return tuple(
        sorted(
            suggestions,
            key=lambda item: (-item.score_basis_points, item.record_id),
        )
    )


__all__ = [
    "CorrectionSuggestion",
    "RetrievalContext",
    "retrieve_suggestions",
]
