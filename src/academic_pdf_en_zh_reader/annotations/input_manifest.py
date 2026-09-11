# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Adapt one inert, parent-bound manifest into validated annotation inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from academic_pdf_en_zh_reader.annotations.ambiguity import (
    AmbiguityOccurrence,
    build_ambiguity_marks,
)
from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.red_emphasis import (
    RedCandidate,
    RedImportance,
    select_red_emphasis,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    FrozenOrangeCandidateSet,
    MandatoryAnnotation,
    freeze_orange_candidate_set,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.review.review_validation import (
    ReviewValidationError,
    validate_review,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    TranslationValidationError,
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.typography.style_contract import (
    TypographyStyleContract,
)

_IMPORTANCE = {
    "other": RedImportance.OTHER,
    "key_mechanism": RedImportance.KEY_MECHANISM,
    "direct_result": RedImportance.DIRECT_RESULT,
    "core_conclusion": RedImportance.CORE_CONCLUSION,
}
_CANDIDATE_ARRAY_FIELDS = (
    "red_candidates",
    "ambiguity_occurrences",
    "teaching_candidates",
    "figure_candidates",
)
MAX_SEMANTIC_CANDIDATES = 10_000


class SemanticCandidateManifestError(ValueError):
    """Raised when an untrusted candidate manifest cannot be adapted safely."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AdaptedSemanticCandidates:
    candidate_set: FrozenOrangeCandidateSet
    mandatory_items: tuple[MandatoryAnnotation, ...]


def _rows(manifest: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    value = manifest[field]
    assert isinstance(value, list)
    assert all(isinstance(row, Mapping) for row in value)
    return value


def _red_candidates(
    manifest: Mapping[str, object],
) -> tuple[RedCandidate, ...]:
    return tuple(
        RedCandidate(
            candidate_id=str(row["candidate_id"]),
            unit_id=str(row["unit_id"]),
            target_start=int(row["target_start"]),
            target_end=int(row["target_end"]),
            importance=_IMPORTANCE[str(row["importance"])],
        )
        for row in _rows(manifest, "red_candidates")
    )


def _ambiguity_occurrences(
    manifest: Mapping[str, object],
) -> tuple[AmbiguityOccurrence, ...]:
    return tuple(
        AmbiguityOccurrence(
            ambiguity_key_id=str(row["ambiguity_key_id"]),
            unit_id=str(row["unit_id"]),
            target_start=int(row["target_start"]),
            target_end=int(row["target_end"]),
        )
        for row in _rows(manifest, "ambiguity_occurrences")
    )


def _teaching_candidates(
    manifest: Mapping[str, object],
) -> tuple[TeachingCandidate, ...]:
    candidates: list[TeachingCandidate] = []
    for row in _rows(manifest, "teaching_candidates"):
        occurrences = row["occurrences"]
        assert isinstance(occurrences, list)
        candidates.append(
            TeachingCandidate(
                key=str(row["key"]),
                english_original=str(row["english_original"]),
                chinese_meaning=str(row["chinese_meaning"]),
                occurrences=tuple(
                    TeachingOccurrence(
                        unit_id=str(occurrence["unit_id"]),
                        source_start=int(occurrence["source_start"]),
                        source_end=int(occurrence["source_end"]),
                        target_start=int(occurrence["target_start"]),
                        target_end=int(occurrence["target_end"]),
                    )
                    for occurrence in occurrences
                ),
                value_priority=int(row["value_priority"]),
                essential=bool(row.get("essential", True)),
            )
        )
    return tuple(candidates)


def _figure_candidates(
    manifest: Mapping[str, object],
) -> tuple[FigureNoteCandidate, ...]:
    candidates: list[FigureNoteCandidate] = []
    for row in _rows(manifest, "figure_candidates"):
        evidence_rows = row["evidence"]
        assert isinstance(evidence_rows, list)
        evidence = tuple(
            DirectEvidence(
                unit_id=str(entry["unit_id"]),
                source_start=int(entry["source_start"]),
                source_end=int(entry["source_end"]),
                quote=str(entry["quote"]),
            )
            for entry in evidence_rows
        )
        candidates.append(
            FigureNoteCandidate(
                key=str(row["key"]),
                figure_id=str(row["figure_id"]),
                caption_unit_id=str(row["caption_unit_id"]),
                target_start=int(row["target_start"]),
                target_end=int(row["target_end"]),
                content=str(row["content"]),
                compact_content=str(row["compact_content"]),
                evidence=evidence,
                value_priority=int(row["value_priority"]),
                essential=bool(row["essential"]),
            )
        )
    return tuple(candidates)


def adapt_semantic_candidate_manifest(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    manifest: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
) -> AdaptedSemanticCandidates:
    """Validate one inert manifest and invoke the existing deterministic policies."""

    try:
        validate_artifact("semantic-candidates", manifest)
    except SchemaValidationError as exc:
        raise SemanticCandidateManifestError(
            "SEMANTIC_CANDIDATES_SCHEMA_INVALID"
        ) from exc

    candidate_count = 0
    for field in _CANDIDATE_ARRAY_FIELDS:
        candidates = manifest[field]
        assert isinstance(candidates, list)
        candidate_count += len(candidates)
    if candidate_count > MAX_SEMANTIC_CANDIDATES:
        raise SemanticCandidateManifestError("SEMANTIC_CANDIDATES_LIMIT_EXCEEDED")

    try:
        validate_translation_artifact(units, translation)
        validate_review(translation, review)
        if not isinstance(style_contract, TypographyStyleContract):
            raise TypeError("style contract has an invalid type")
    except (TranslationValidationError, ReviewValidationError, TypeError) as exc:
        raise SemanticCandidateManifestError("SEMANTIC_CANDIDATES_INVALID") from exc

    expected_parents = {
        "units_hash": sha256_canonical(units),
        "translation_hash": sha256_canonical(translation),
        "review_hash": sha256_canonical(review),
    }
    if any(manifest[field] != digest for field, digest in expected_parents.items()):
        raise SemanticCandidateManifestError("SEMANTIC_CANDIDATES_PARENT_MISMATCH")

    if units["units"] and not manifest["teaching_candidates"]:
        raise SemanticCandidateManifestError("CORE_VOCABULARY_MISSING")
    captions = {
        u["id"]
        for u in units["units"]
        if u["role"] in {"figure-caption", "table-caption"}
    }
    covered = {c["caption_unit_id"] for c in manifest["figure_candidates"]}
    if captions - covered:
        raise SemanticCandidateManifestError("CORE_FIGURE_READING_MISSING")

    try:
        ambiguity_marks = build_ambiguity_marks(
            units,
            translation,
            review,
            _ambiguity_occurrences(manifest),
            style_contract,
        )
        red_selection = select_red_emphasis(
            units,
            translation,
            _red_candidates(manifest),
            ambiguity_spans=tuple(mark.span for mark in ambiguity_marks),
        )
        candidate_set = freeze_orange_candidate_set(
            units,
            translation,
            figure_candidates=_figure_candidates(manifest),
            teaching_candidates=_teaching_candidates(manifest),
        )
    except (AnnotationValidationError, KeyError, TypeError, ValueError) as exc:
        raise SemanticCandidateManifestError("SEMANTIC_CANDIDATES_INVALID") from exc

    return AdaptedSemanticCandidates(
        candidate_set=candidate_set,
        mandatory_items=(*red_selection.items, *ambiguity_marks),
    )


__all__ = [
    "AdaptedSemanticCandidates",
    "MAX_SEMANTIC_CANDIDATES",
    "SemanticCandidateManifestError",
    "adapt_semantic_candidate_manifest",
]
