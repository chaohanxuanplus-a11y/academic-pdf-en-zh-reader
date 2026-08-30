# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Select contextual correction evidence without changing translation text."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from academic_pdf_en_zh_reader.schema.validate import validate_artifact

_TOKEN = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return " ".join(value.split())


@dataclass(frozen=True)
class CorrectionContext:
    english: str
    domain: str
    source_part_of_speech: str
    target_grammar_function: str
    minimal_context: str
    semantic_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "english",
            "domain",
            "source_part_of_speech",
            "target_grammar_function",
            "minimal_context",
        ):
            _required_text(getattr(self, name), name)


@dataclass(frozen=True)
class CorrectionEvidence:
    id: str
    english: str
    preferred_chinese: str
    domain: str
    source_part_of_speech: str
    target_grammar_function: str
    minimal_context: str
    evidence_only: bool = True
    requires_grammar_adaptation: bool = False


def _tokens(value: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _TOKEN.finditer(value))


def _context_compatible(stored: str, current: str) -> bool:
    if _normalized(stored) == _normalized(current):
        return True
    stored_tokens = _tokens(stored)
    current_tokens = _tokens(current)
    minimum = min(len(stored_tokens), len(current_tokens))
    if minimum < 2:
        return False
    overlap = len(stored_tokens & current_tokens)
    return overlap * 3 >= minimum * 2


def select_correction_evidence(
    artifact: Mapping[str, object],
    context: CorrectionContext,
) -> tuple[CorrectionEvidence, ...]:
    """Return compatible evidence; never apply a preferred string to output."""

    validate_artifact("correction-suggestions", artifact)
    raw_suggestions = artifact["suggestions"]
    assert isinstance(raw_suggestions, list)
    selected: list[CorrectionEvidence] = []
    context_tags = {_normalized(tag) for tag in context.semantic_tags}
    for suggestion in raw_suggestions:
        assert isinstance(suggestion, dict)
        if suggestion.get("conflict") is True:
            continue
        if _normalized(suggestion["normalized_english"]) != _normalized(
            context.english
        ):
            continue
        if _normalized(suggestion["domain"]) != _normalized(context.domain):
            continue
        minimal_context = suggestion.get("minimal_context")
        if not isinstance(minimal_context, str) or not _context_compatible(
            minimal_context,
            context.minimal_context,
        ):
            continue
        suggestion_tags = {
            _normalized(tag) for tag in suggestion.get("semantic_tags", ())
        }
        if (
            not context_tags
            or not suggestion_tags
            or not context_tags & suggestion_tags
        ):
            continue
        same_part_of_speech = _normalized(
            suggestion["source_part_of_speech"]
        ) == _normalized(context.source_part_of_speech)
        same_grammar = _normalized(
            suggestion["target_grammar_function"]
        ) == _normalized(context.target_grammar_function)
        if same_part_of_speech and not same_grammar:
            continue
        if not same_part_of_speech and (
            not context_tags
            or not suggestion_tags
            or not context_tags & suggestion_tags
        ):
            continue
        requires_adaptation = not (same_part_of_speech and same_grammar)
        selected.append(
            CorrectionEvidence(
                id=suggestion["id"],
                english=suggestion["english"],
                preferred_chinese=suggestion["preferred_chinese"],
                domain=suggestion["domain"],
                source_part_of_speech=suggestion["source_part_of_speech"],
                target_grammar_function=suggestion["target_grammar_function"],
                minimal_context=minimal_context,
                requires_grammar_adaptation=requires_adaptation,
            )
        )
    preference_keys = {_normalized(item.preferred_chinese) for item in selected}
    if len(preference_keys) > 1:
        exact = [item for item in selected if not item.requires_grammar_adaptation]
        if not exact:
            return ()
        if len({_normalized(item.preferred_chinese) for item in exact}) > 1:
            return ()
        selected = exact
    return tuple(
        sorted(
            selected,
            key=lambda item: (item.requires_grammar_adaptation, item.id),
        )
    )
