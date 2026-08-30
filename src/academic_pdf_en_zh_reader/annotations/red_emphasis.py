# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Select a sparse, exact-span set of dark-red reading emphasis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum

from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationIndex,
    AnnotationValidationError,
    BoundTargetSpan,
    annotation_index,
    spans_overlap,
    stable_annotation_id,
    validate_target_span,
    visible_character_count,
)

TARGET_BASIS_POINTS = 600
HARD_CAP_BASIS_POINTS = 1_000
_EXCLUDED_ROLES = frozenset({"title", "abstract", "keywords"})


class RedImportance(IntEnum):
    OTHER = 100
    KEY_MECHANISM = 200
    DIRECT_RESULT = 300
    CORE_CONCLUSION = 400


@dataclass(frozen=True)
class RedCandidate:
    candidate_id: str
    unit_id: str
    target_start: int
    target_end: int
    importance: RedImportance


@dataclass(frozen=True)
class SelectedRed:
    candidate_id: str
    unit_id: str
    target_start: int
    target_end: int
    priority: int

    def to_item(self) -> dict[str, object]:
        item: dict[str, object] = {
            "unit_id": self.unit_id,
            "kind": "dark-red-highlight",
            "target_start": self.target_start,
            "target_end": self.target_end,
            "priority": self.priority,
            "candidate_id": self.candidate_id,
            "content": None,
        }
        return {"id": stable_annotation_id(item), **item}


@dataclass(frozen=True)
class RedSelection:
    items: tuple[SelectedRed, ...]
    highlighted_characters: int
    denominator_characters: int
    ratio_basis_points: int


def _basis_points(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0
    return (numerator * 10_000 + denominator // 2) // denominator


def _validated_ambiguity_spans(
    index: AnnotationIndex, spans: Sequence[BoundTargetSpan]
) -> tuple[BoundTargetSpan, ...]:
    validated: list[BoundTargetSpan] = []
    for span in spans:
        if not isinstance(span, BoundTargetSpan):
            raise AnnotationValidationError("ambiguity span has an invalid type")
        unit_id, start, end, _ = validate_target_span(
            index,
            span.unit_id,
            span.target_start,
            span.target_end,
        )
        current = BoundTargetSpan(unit_id, start, end)
        if any(spans_overlap(current, previous) for previous in validated):
            raise AnnotationValidationError("ambiguity spans must not overlap")
        validated.append(current)
    return tuple(sorted(validated))


def _denominator(index: AnnotationIndex) -> int:
    total = 0
    for unit_id, text in index.target_text.items():
        if index.roles[unit_id] in _EXCLUDED_ROLES:
            continue
        total += visible_character_count(text)
    return total


def select_red_emphasis(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    candidates: Sequence[RedCandidate],
    *,
    ambiguity_spans: Sequence[BoundTargetSpan] = (),
) -> RedSelection:
    """Select candidates only while they move the exact ratio toward six percent."""

    index = annotation_index(units, translation)
    _validated_ambiguity_spans(index, ambiguity_spans)
    denominator = _denominator(index)
    seen_ids: set[str] = set()
    validated: list[tuple[RedCandidate, BoundTargetSpan, int]] = []
    for candidate in candidates:
        if not isinstance(candidate, RedCandidate):
            raise AnnotationValidationError("red candidate has an invalid type")
        if not candidate.candidate_id.strip() or candidate.candidate_id in seen_ids:
            raise AnnotationValidationError(
                "red candidate IDs must be unique and non-empty"
            )
        seen_ids.add(candidate.candidate_id)
        if not isinstance(candidate.importance, RedImportance):
            raise AnnotationValidationError("red candidate importance is invalid")
        unit_id, start, end, text = validate_target_span(
            index,
            candidate.unit_id,
            candidate.target_start,
            candidate.target_end,
        )
        if index.roles[unit_id] in _EXCLUDED_ROLES:
            raise AnnotationValidationError("red candidate uses an excluded role")
        count = visible_character_count(text)
        if count == 0:
            raise AnnotationValidationError("red candidate has no visible content")
        span = BoundTargetSpan(unit_id, start, end)
        validated.append((candidate, span, count))

    validated.sort(
        key=lambda entry: (
            -int(entry[0].importance),
            index.unit_order[entry[0].unit_id],
            entry[0].target_start,
            entry[0].target_end,
            entry[0].candidate_id,
        )
    )
    selected: list[SelectedRed] = []
    selected_spans: list[BoundTargetSpan] = []
    highlighted = 0
    for candidate, span, count in validated:
        if any(spans_overlap(span, previous) for previous in selected_spans):
            continue
        proposed = highlighted + count
        if denominator == 0 or proposed * 10_000 > denominator * HARD_CAP_BASIS_POINTS:
            continue
        current_distance = abs(highlighted * 10_000 - denominator * TARGET_BASIS_POINTS)
        proposed_distance = abs(proposed * 10_000 - denominator * TARGET_BASIS_POINTS)
        if proposed_distance >= current_distance:
            continue
        selected.append(
            SelectedRed(
                candidate_id=candidate.candidate_id,
                unit_id=span.unit_id,
                target_start=span.target_start,
                target_end=span.target_end,
                priority=int(candidate.importance),
            )
        )
        selected_spans.append(span)
        highlighted = proposed

    selected.sort(
        key=lambda item: (
            index.unit_order[item.unit_id],
            item.target_start,
            item.target_end,
            item.candidate_id,
        )
    )
    return RedSelection(
        items=tuple(selected),
        highlighted_characters=highlighted,
        denominator_characters=denominator,
        ratio_basis_points=_basis_points(highlighted, denominator),
    )


__all__ = [
    "HARD_CAP_BASIS_POINTS",
    "TARGET_BASIS_POINTS",
    "RedCandidate",
    "RedImportance",
    "RedSelection",
    "SelectedRed",
    "select_red_emphasis",
]
