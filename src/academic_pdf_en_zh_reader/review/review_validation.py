# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Mechanical gate for independent translation review artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)

_AMBIGUITY_FIELDS = (
    "english_expression",
    "syntactic_structure",
    "candidate_meanings",
    "disciplinary_context",
    "ambiguity_reason",
)
_GENERIC_AMBIGUITY_VALUES = frozenset(
    {
        "ambiguous",
        "ambiguity",
        "general",
        "general ambiguity",
        "context unclear",
        "expression",
        "grammar",
        "insufficient context",
        "phrase",
        "syntax",
        "term",
        "term ambiguity",
        "terminology ambiguity",
        "unknown",
        "word",
        "有歧义",
        "上下文不足",
        "句法",
        "术语歧义",
        "术语",
        "语义歧义",
        "语境不明",
        "语法",
        "表达",
        "未知",
        "一般",
    }
)


class ReviewValidationError(ValueError):
    """Raised when an artifact cannot pass the independent-review gate."""


@dataclass(frozen=True)
class ReviewGateResult:
    translation_hash: str
    reviewed_unit_ids: tuple[str, ...]
    unresolved_ambiguity_keys: tuple[str, ...]
    style_improvements: tuple[Mapping[str, object], ...]


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _specific_text(value: object, *, field: str, minimum: int) -> str:
    if not isinstance(value, str):
        raise ReviewValidationError(f"ambiguity_key {field} must be text")
    normalized = _normalized(value)
    if len(normalized) < minimum or normalized in _GENERIC_AMBIGUITY_VALUES:
        raise ReviewValidationError(
            f"ambiguity_key {field} is too broad or insufficient"
        )
    return " ".join(value.split())


def make_ambiguity_key(
    *,
    english_expression: str,
    syntactic_structure: str,
    candidate_meanings: Sequence[str],
    disciplinary_context: str,
    ambiguity_reason: str,
) -> dict[str, object]:
    """Build a stable key from every fact needed to distinguish an ambiguity."""

    expression = _specific_text(
        english_expression,
        field="english_expression",
        minimum=2,
    )
    syntax = _specific_text(
        syntactic_structure,
        field="syntactic_structure",
        minimum=4,
    )
    context = _specific_text(
        disciplinary_context,
        field="disciplinary_context",
        minimum=4,
    )
    reason = _specific_text(
        ambiguity_reason,
        field="ambiguity_reason",
        minimum=8,
    )
    if isinstance(candidate_meanings, (str, bytes)):
        raise ReviewValidationError(
            "ambiguity_key candidate_meanings must contain alternatives"
        )
    candidates = [
        _specific_text(value, field="candidate_meanings", minimum=2)
        for value in candidate_meanings
    ]
    by_normalized = {_normalized(value): value for value in candidates}
    if len(by_normalized) < 2:
        raise ReviewValidationError(
            "ambiguity_key candidate_meanings must contain two distinct meanings"
        )
    ordered_candidates = tuple(by_normalized[key] for key in sorted(by_normalized))
    identity_payload = {
        "english_expression": _normalized(expression),
        "syntactic_structure": _normalized(syntax),
        "candidate_meanings": sorted(by_normalized),
        "disciplinary_context": _normalized(context),
        "ambiguity_reason": _normalized(reason),
    }
    return {
        "id": sha256_canonical(identity_payload),
        "english_expression": expression,
        "syntactic_structure": syntax,
        "candidate_meanings": list(ordered_candidates),
        "disciplinary_context": context,
        "ambiguity_reason": reason,
    }


def _validate_ambiguity_key(raw: object) -> str:
    if not isinstance(raw, Mapping):
        raise ReviewValidationError("unresolved ambiguity requires ambiguity_key")
    if any(field not in raw for field in ("id", *_AMBIGUITY_FIELDS)):
        raise ReviewValidationError("ambiguity_key is incomplete")
    candidates = raw["candidate_meanings"]
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise ReviewValidationError("ambiguity_key candidate_meanings are invalid")
    rebuilt = make_ambiguity_key(
        english_expression=raw["english_expression"],  # type: ignore[arg-type]
        syntactic_structure=raw["syntactic_structure"],  # type: ignore[arg-type]
        candidate_meanings=candidates,  # type: ignore[arg-type]
        disciplinary_context=raw["disciplinary_context"],  # type: ignore[arg-type]
        ambiguity_reason=raw["ambiguity_reason"],  # type: ignore[arg-type]
    )
    if raw["id"] != rebuilt["id"]:
        raise ReviewValidationError("ambiguity_key id does not match its evidence")
    return str(raw["id"])


def _identity(review: Mapping[str, object], name: str) -> str:
    value = review.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ReviewValidationError("translator and reviewer identity must exist")
    return _normalized(value)


def _translation_unit_ids(translation: Mapping[str, object]) -> tuple[str, ...]:
    units = translation.get("units")
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise ReviewValidationError("translation units must be a sequence")
    identifiers = tuple(str(unit["unit_id"]) for unit in units)  # type: ignore[index]
    if len(set(identifiers)) != len(identifiers):
        raise ReviewValidationError("translation unit IDs must be unique")
    return identifiers


def validate_review(
    translation: Mapping[str, object],
    review: Mapping[str, object],
) -> ReviewGateResult:
    """Validate exact translation binding and decide whether review may proceed."""

    try:
        validate_artifact("translation", translation)
        validate_artifact("review", review)
    except SchemaValidationError as exc:
        message = str(exc)
        if "reviewed_unit_ids" in message:
            raise ReviewValidationError(
                "reviewed unit IDs must match translation once and in order"
            ) from exc
        if "translator_id" in message or "reviewer_id" in message:
            raise ReviewValidationError(
                "translator and reviewer identity must exist"
            ) from exc
        raise ReviewValidationError(message) from exc

    batch_translator = _identity(translation, "translator_id")
    translator = _identity(review, "translator_id")
    reviewer = _identity(review, "reviewer_id")
    if translator != batch_translator:
        raise ReviewValidationError(
            "review translator_id does not match the translation batch"
        )
    if review["reviewer_role"] == "independent" and batch_translator == reviewer:
        raise ReviewValidationError(
            "translator and independent reviewer must be distinct"
        )

    translation_hash = sha256_canonical(translation)
    if review.get("translation_hash") != translation_hash:
        raise ReviewValidationError(
            "review translation_hash does not match translation"
        )

    unit_ids = _translation_unit_ids(translation)
    reviewed = review.get("reviewed_unit_ids")
    if (
        not isinstance(reviewed, Sequence)
        or isinstance(reviewed, (str, bytes))
        or (review["reviewer_role"] == "independent" and tuple(reviewed) != unit_ids)
        or tuple(reviewed) != tuple(uid for uid in unit_ids if uid in reviewed)
    ):
        raise ReviewValidationError(
            "reviewed unit IDs must match translation once and in order"
        )

    issues = review.get("issues")
    assert isinstance(issues, list)
    issue_ids: set[str] = set()
    ambiguity_keys: list[str] = []
    style_improvements: list[Mapping[str, object]] = []
    unresolved_hard_error = False
    for issue in issues:
        assert isinstance(issue, dict)
        identifier = issue["id"]
        if identifier in issue_ids:
            raise ReviewValidationError("review issue IDs must be unique")
        issue_ids.add(identifier)
        if issue["unit_id"] not in reviewed:
            raise ReviewValidationError("review issue references an unknown unit")
        severity = issue["severity"]
        if severity == "hard_error":
            if issue["status"] == "unresolved":
                unresolved_hard_error = True
            elif (
                not isinstance(issue.get("resolution"), str)
                or not issue["resolution"].strip()
            ):
                raise ReviewValidationError("resolved hard_error requires a resolution")
            if "ambiguity_key" in issue or "style_improvement" in issue:
                raise ReviewValidationError("hard_error has incompatible detail fields")
        elif severity == "style_improvement":
            improvement = issue.get("style_improvement")
            if not isinstance(improvement, Mapping):
                raise ReviewValidationError(
                    "style_improvement requires structured detail"
                )
            if "ambiguity_key" in issue:
                raise ReviewValidationError(
                    "style_improvement cannot carry ambiguity_key"
                )
            style_improvements.append(improvement)
        elif severity == "unresolved_ambiguity":
            if issue["status"] != "unresolved":
                raise ReviewValidationError(
                    "unresolved_ambiguity must remain unresolved for annotation"
                )
            if "style_improvement" in issue:
                raise ReviewValidationError(
                    "unresolved_ambiguity cannot carry style detail"
                )
            ambiguity_keys.append(_validate_ambiguity_key(issue.get("ambiguity_key")))
        else:
            raise ReviewValidationError("review issue severity is unsupported")

    if unresolved_hard_error:
        raise ReviewValidationError("unresolved hard_error blocks progression")
    if review.get("final_status") != "passed":
        raise ReviewValidationError("review final_status must be passed")
    return ReviewGateResult(
        translation_hash=translation_hash,
        reviewed_unit_ids=tuple(reviewed),
        unresolved_ambiguity_keys=tuple(ambiguity_keys),
        style_improvements=tuple(style_improvements),
    )
