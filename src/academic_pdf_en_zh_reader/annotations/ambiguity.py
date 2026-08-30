# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Turn reviewed, unresolved ambiguity occurrences into exact visual marks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
    BoundTargetSpan,
    annotation_index,
    spans_overlap,
    stable_annotation_id,
    validate_target_span,
    visible_character_count,
)
from academic_pdf_en_zh_reader.review.review_validation import (
    ReviewValidationError,
    validate_independent_review,
)
from academic_pdf_en_zh_reader.typography.style_contract import (
    TypographyStyleContract,
)

AMBIGUITY_LABEL = "（可能存在歧义）"


@dataclass(frozen=True)
class AmbiguityOccurrence:
    ambiguity_key_id: str
    unit_id: str
    target_start: int
    target_end: int


@dataclass(frozen=True)
class AmbiguityMark:
    ambiguity_key_id: str
    unit_id: str
    target_start: int
    target_end: int
    label: str | None
    label_size_mpt: int
    underline: bool = True
    label_after_span: bool = False

    @property
    def span(self) -> BoundTargetSpan:
        return BoundTargetSpan(self.unit_id, self.target_start, self.target_end)

    def to_item(self) -> dict[str, object]:
        item: dict[str, object] = {
            "unit_id": self.unit_id,
            "kind": "bright-red-ambiguity",
            "target_start": self.target_start,
            "target_end": self.target_end,
            "priority": 1_000,
            "ambiguity_key_id": self.ambiguity_key_id,
            "content": self.label,
            "underline": self.underline,
            "label_after_span": self.label_after_span,
            "label_size_mpt": self.label_size_mpt,
        }
        return {"id": stable_annotation_id(item), **item}


def _unresolved_keys(review: Mapping[str, object]) -> set[str]:
    issues = review["issues"]
    assert isinstance(issues, list)
    return {
        str(issue["ambiguity_key"]["id"])
        for issue in issues
        if issue["severity"] == "unresolved_ambiguity"
    }


def build_ambiguity_marks(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    occurrences: Sequence[AmbiguityOccurrence],
    style: TypographyStyleContract,
) -> tuple[AmbiguityMark, ...]:
    """Validate every unresolved key and label its first reading-order occurrence."""

    index = annotation_index(units, translation)
    try:
        validate_independent_review(translation, review)
    except ReviewValidationError as exc:
        raise AnnotationValidationError("ambiguity review input is invalid") from exc
    unresolved = _unresolved_keys(review)
    validated: list[tuple[AmbiguityOccurrence, BoundTargetSpan]] = []
    seen_spans: list[BoundTargetSpan] = []
    for occurrence in occurrences:
        if not isinstance(occurrence, AmbiguityOccurrence):
            raise AnnotationValidationError("ambiguity occurrence has an invalid type")
        if occurrence.ambiguity_key_id not in unresolved:
            raise AnnotationValidationError("ambiguity occurrence uses an unknown key")
        unit_id, start, end, target_slice = validate_target_span(
            index,
            occurrence.unit_id,
            occurrence.target_start,
            occurrence.target_end,
        )
        if visible_character_count(target_slice) == 0:
            raise AnnotationValidationError(
                "ambiguity target span has no visible content"
            )
        span = BoundTargetSpan(unit_id, start, end)
        if any(spans_overlap(span, previous) for previous in seen_spans):
            raise AnnotationValidationError("ambiguity target spans must not overlap")
        seen_spans.append(span)
        validated.append((occurrence, span))
    present = {occurrence.ambiguity_key_id for occurrence, _ in validated}
    missing = sorted(unresolved - present)
    if missing:
        raise AnnotationValidationError(
            f"unresolved ambiguity {missing[0]} has no occurrence"
        )
    validated.sort(
        key=lambda entry: (
            index.unit_order[entry[1].unit_id],
            entry[1].target_start,
            entry[1].target_end,
            entry[0].ambiguity_key_id,
        )
    )
    labeled_keys: set[str] = set()
    marks: list[AmbiguityMark] = []
    for occurrence, span in validated:
        is_first = occurrence.ambiguity_key_id not in labeled_keys
        try:
            label_size = style.style_for(index.roles[span.unit_id]).size_mpt
        except ValueError as exc:
            raise AnnotationValidationError(
                "ambiguity role has no fixed typography style"
            ) from exc
        marks.append(
            AmbiguityMark(
                ambiguity_key_id=occurrence.ambiguity_key_id,
                unit_id=span.unit_id,
                target_start=span.target_start,
                target_end=span.target_end,
                label=AMBIGUITY_LABEL if is_first else None,
                label_size_mpt=label_size,
                label_after_span=is_first,
            )
        )
        labeled_keys.add(occurrence.ambiguity_key_id)
    return tuple(marks)


__all__ = [
    "AMBIGUITY_LABEL",
    "AmbiguityMark",
    "AmbiguityOccurrence",
    "build_ambiguity_marks",
]
