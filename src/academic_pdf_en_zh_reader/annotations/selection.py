# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Two-stage optional annotation trial and immutable selection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from academic_pdf_en_zh_reader.annotations.ambiguity import AmbiguityMark
from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureEvidence,
    FigureNoteCandidate,
    FrozenEvidenceVerifier,
    FrozenObjectEvidence,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
    format_teaching_content,
)
from academic_pdf_en_zh_reader.annotations.red_emphasis import SelectedRed
from academic_pdf_en_zh_reader.annotations.validation import (
    ANNOTATION_COLORS,
    ANNOTATION_STYLE_VERSION,
    AnnotationIndex,
    AnnotationValidationError,
    BoundTargetSpan,
    annotation_index,
    is_content_character,
    spans_overlap,
    stable_annotation_id,
    validate_source_span,
    validate_target_span,
    visible_character_count,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical

MIN_AUXILIARY_SIZE_MPT = 7_000
MAX_FIGURE_NOTES_PER_FIGURE = 3
CANDIDATE_SET_CONTRACT_VERSION = "1.0.0"
ORANGE_SELECTION_POLICY_VERSION = "1.0.0"


@dataclass(frozen=True)
class OrangePlacement:
    kind: Literal["dark-orange-teaching", "figure-table-reading"]
    candidate_key: str
    unit_id: str
    target_start: int
    target_end: int
    content: str
    auxiliary_size_mpt: int
    priority: int
    attachment: Literal["below-translation"] = "below-translation"
    source_start: int | None = None
    source_end: int | None = None
    english_original: str | None = None
    chinese_meaning: str | None = None
    deferred_occurrences: int = 0
    figure_id: str | None = None
    evidence: tuple[FigureEvidence, ...] = ()
    essential: bool = False
    compacted: bool = False
    uses_continuation: bool = False

    def to_item(self) -> dict[str, object]:
        common: dict[str, object] = {
            "unit_id": self.unit_id,
            "kind": self.kind,
            "target_start": self.target_start,
            "target_end": self.target_end,
            "priority": self.priority,
            "candidate_key": self.candidate_key,
            "content": self.content,
            "auxiliary_size_mpt": self.auxiliary_size_mpt,
            "uses_continuation": self.uses_continuation,
            "attachment": self.attachment,
        }
        if self.kind == "dark-orange-teaching":
            common.update(
                {
                    "source_start": self.source_start,
                    "source_end": self.source_end,
                    "english_original": self.english_original,
                    "chinese_meaning": self.chinese_meaning,
                    "deferred_occurrences": self.deferred_occurrences,
                }
            )
        else:
            common.update(
                {
                    "figure_id": self.figure_id,
                    "evidence": [entry.to_dict() for entry in self.evidence],
                    "essential": self.essential,
                    "compacted": self.compacted,
                }
            )
        return {"id": stable_annotation_id(common), **common}


MandatoryAnnotation = SelectedRed | AmbiguityMark


@dataclass(frozen=True)
class LayoutTrialRequest:
    phase: Literal["mandatory-only", "candidate-trial", "final-frozen"]
    placements: tuple[OrangePlacement, ...]
    allow_continuation: bool
    mandatory_items: tuple[MandatoryAnnotation, ...]
    mandatory_items_hash: str
    frozen_selection_hash: str | None = None


@dataclass(frozen=True)
class LayoutTrialResult:
    """Feasibility and total continuation pages beyond native source pages."""

    feasible: bool
    added_continuation_pages: int

    def __post_init__(self) -> None:
        if type(self.feasible) is not bool:
            raise AnnotationValidationError("layout trial feasibility must be boolean")
        if (
            type(self.added_continuation_pages) is not int
            or self.added_continuation_pages < 0
        ):
            raise AnnotationValidationError(
                "layout trial continuation delta must be a non-negative integer"
            )


LayoutTrial = Callable[[LayoutTrialRequest], LayoutTrialResult]


@dataclass(frozen=True)
class FrozenOrangeCandidateSet:
    """Validated, canonically ordered optional annotation candidates."""

    teaching_candidates: tuple[TeachingCandidate, ...]
    figure_candidates: tuple[FigureNoteCandidate, ...]
    candidate_set_hash: str


@dataclass(frozen=True)
class OrangeSelectionResult:
    mandatory_items: tuple[MandatoryAnnotation, ...]
    mandatory_items_hash: str
    placements: tuple[OrangePlacement, ...]
    auxiliary_size_mpt: int
    candidate_set_hash: str
    selection_hash: str
    continuation_pages: int

    def to_artifact(
        self,
        *,
        units: Mapping[str, object],
        translation: Mapping[str, object],
        review: Mapping[str, object],
    ) -> dict[str, object]:
        """Combine already-frozen mandatory and optional items without regeneration."""

        index = annotation_index(units, translation)

        items = [item.to_item() for item in self.mandatory_items]
        items.extend(placement.to_item() for placement in self.placements)
        kind_order = {
            "dark-red-highlight": 0,
            "bright-red-ambiguity": 1,
            "figure-table-reading": 2,
            "dark-orange-teaching": 3,
        }
        items.sort(
            key=lambda item: (
                index.unit_order[str(item["unit_id"])],
                int(item["target_start"]),
                kind_order[str(item["kind"])],
                str(item["id"]),
            )
        )
        denominator = 0
        for unit_id, text in index.target_text.items():
            if index.roles[unit_id] in {"title", "abstract", "keywords"}:
                continue
            denominator += sum(is_content_character(character) for character in text)
        highlighted = sum(
            visible_character_count(
                index.target_text[str(item["unit_id"])][
                    int(item["target_start"]) : int(item["target_end"])
                ]
            )
            for item in items
            if item["kind"] == "dark-red-highlight"
        )
        if denominator == 0:
            highlight_ratio = 0
        else:
            highlight_ratio = (highlighted * 10_000 + denominator // 2) // denominator
        orange_characters = sum(
            visible_character_count(str(item.get("content") or ""))
            for item in items
            if item["kind"] in {"dark-orange-teaching", "figure-table-reading"}
        )
        translation_characters = sum(
            visible_character_count(text) for text in index.target_text.values()
        )
        total_reading_characters = translation_characters + orange_characters
        teaching_ratio = (
            0
            if total_reading_characters == 0
            else (orange_characters * 10_000 + total_reading_characters // 2)
            // total_reading_characters
        )
        selection_payload = {
            "style_version": ANNOTATION_STYLE_VERSION,
            "colors": dict(ANNOTATION_COLORS),
            "auxiliary_size_mpt": self.auxiliary_size_mpt,
            "highlight_ratio_basis_points": highlight_ratio,
            "teaching_ratio_basis_points": teaching_ratio,
            "items": items,
        }
        units_hash = sha256_canonical(units)
        translation_hash = sha256_canonical(translation)
        review_hash = sha256_canonical(review)
        input_payload = {
            "contract_version": "1.0.0",
            "units_hash": units_hash,
            "translation_hash": translation_hash,
            "review_hash": review_hash,
            "mandatory_items_hash": self.mandatory_items_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "orange_selection_hash": self.selection_hash,
        }
        return {
            "schema_version": "1.0.0",
            "artifact_kind": "annotations",
            "units_hash": units_hash,
            "translation_hash": translation_hash,
            "review_hash": review_hash,
            "mandatory_items_hash": self.mandatory_items_hash,
            "annotations_input_hash": sha256_canonical(input_payload),
            "candidate_set_hash": self.candidate_set_hash,
            "orange_selection_hash": self.selection_hash,
            "selection_hash": sha256_canonical(selection_payload),
            **selection_payload,
        }


def _positive_priority(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AnnotationValidationError(f"{label} must be a non-negative integer")
    return value


def _nonempty_line(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\n" in value
        or "\r" in value
    ):
        raise AnnotationValidationError(f"{label} must be non-empty one-line text")
    return value


def _trial(
    callback: LayoutTrial,
    request: LayoutTrialRequest,
) -> LayoutTrialResult:
    result = callback(request)
    if not isinstance(result, LayoutTrialResult):
        raise AnnotationValidationError("layout trial returned an invalid result")
    return result


def _freeze_mandatory_items(
    index: AnnotationIndex,
    values: Sequence[MandatoryAnnotation],
) -> tuple[MandatoryAnnotation, ...]:
    frozen: list[MandatoryAnnotation] = []
    red_spans: list[BoundTargetSpan] = []
    ambiguity_spans: list[BoundTargetSpan] = []
    identifiers: set[str] = set()
    for value in tuple(values):
        if not isinstance(value, (SelectedRed, AmbiguityMark)):
            raise AnnotationValidationError(
                "mandatory annotation must be selected red or reviewed ambiguity"
            )
        unit_id, start, end, target_slice = validate_target_span(
            index,
            value.unit_id,
            value.target_start,
            value.target_end,
        )
        if visible_character_count(target_slice) == 0:
            raise AnnotationValidationError(
                "mandatory annotation span has no visible content"
            )
        span = BoundTargetSpan(unit_id, start, end)
        if isinstance(value, SelectedRed):
            if index.roles[unit_id] in {"title", "abstract", "keywords"}:
                raise AnnotationValidationError(
                    "mandatory dark-red item uses an excluded role"
                )
            if (
                not value.candidate_id.strip()
                or type(value.priority) is not int
                or value.priority < 0
            ):
                raise AnnotationValidationError("mandatory dark-red item is invalid")
            if any(spans_overlap(span, previous) for previous in red_spans):
                raise AnnotationValidationError(
                    "mandatory dark-red spans must not overlap"
                )
            red_spans.append(span)
        else:
            if (
                len(value.ambiguity_key_id) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in value.ambiguity_key_id
                )
                or value.label not in {None, "（可能存在歧义）"}
                or value.underline is not True
                or value.label_after_span is not (value.label is not None)
                or type(value.label_size_mpt) is not int
                or value.label_size_mpt <= 0
            ):
                raise AnnotationValidationError("mandatory ambiguity item is invalid")
            if any(spans_overlap(span, previous) for previous in ambiguity_spans):
                raise AnnotationValidationError(
                    "mandatory ambiguity spans must not overlap"
                )
            ambiguity_spans.append(span)
        identifier = str(value.to_item()["id"])
        if identifier in identifiers:
            raise AnnotationValidationError("mandatory annotation IDs must be unique")
        identifiers.add(identifier)
        frozen.append(value)

    kind_order = {SelectedRed: 0, AmbiguityMark: 1}
    frozen.sort(
        key=lambda item: (
            index.unit_order[item.unit_id],
            item.target_start,
            kind_order[type(item)],
            str(item.to_item()["id"]),
        )
    )
    ambiguity_by_key: defaultdict[str, list[AmbiguityMark]] = defaultdict(list)
    for item in frozen:
        if isinstance(item, AmbiguityMark):
            ambiguity_by_key[item.ambiguity_key_id].append(item)
    for items in ambiguity_by_key.values():
        if items[0].label != "（可能存在歧义）" or any(
            item.label is not None for item in items[1:]
        ):
            raise AnnotationValidationError(
                "mandatory ambiguity label must follow its first occurrence"
            )
    return tuple(frozen)


def _candidate_snapshot(
    teaching: Sequence[TeachingCandidate],
    figures: Sequence[FigureNoteCandidate],
) -> dict[str, object]:
    return {
        "contract_version": CANDIDATE_SET_CONTRACT_VERSION,
        "teaching": [
            {
                "key": candidate.key,
                "english_original": candidate.english_original,
                "chinese_meaning": candidate.chinese_meaning,
                "value_priority": candidate.value_priority,
                "occurrences": [
                    {
                        "unit_id": occurrence.unit_id,
                        "source_start": occurrence.source_start,
                        "source_end": occurrence.source_end,
                        "target_start": occurrence.target_start,
                        "target_end": occurrence.target_end,
                    }
                    for occurrence in candidate.occurrences
                ],
            }
            for candidate in teaching
        ],
        "figures": [
            {
                "key": candidate.key,
                "figure_id": candidate.figure_id,
                "caption_unit_id": candidate.caption_unit_id,
                "target_start": candidate.target_start,
                "target_end": candidate.target_end,
                "content": candidate.content,
                "compact_content": candidate.compact_content,
                "evidence": [entry.to_dict() for entry in candidate.evidence],
                "value_priority": candidate.value_priority,
                "essential": candidate.essential,
            }
            for candidate in figures
        ],
    }


def orange_selection_policy_payload() -> dict[str, object]:
    """Return the fixed algorithm and threshold contract used by selection."""

    return {
        "contract_version": ORANGE_SELECTION_POLICY_VERSION,
        "candidate_set_contract_version": CANDIDATE_SET_CONTRACT_VERSION,
        "priority_order": ["figure-table-reading", "dark-orange-teaching"],
        "max_figure_notes_per_figure": MAX_FIGURE_NOTES_PER_FIGURE,
        "ordinary_may_add_continuation": False,
        "essential_figure_may_add_continuation": True,
        "essential_figure_variant_order": ["full", "compact", "compact-continuation"],
        "auxiliary_size_floor_mpt": MIN_AUXILIARY_SIZE_MPT,
    }


def freeze_orange_candidate_set(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    *,
    figure_candidates: Sequence[FigureNoteCandidate],
    teaching_candidates: Sequence[TeachingCandidate],
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> FrozenOrangeCandidateSet:
    """Validate and canonically freeze candidates before any layout trial."""

    index = annotation_index(units, translation)
    keys: set[str] = set()
    expressions: set[str] = set()
    validated_teaching: list[TeachingCandidate] = []
    for candidate in tuple(teaching_candidates):
        if not isinstance(candidate, TeachingCandidate):
            raise AnnotationValidationError("teaching candidate has an invalid type")
        key = _nonempty_line(candidate.key, label="teaching candidate key")
        if key in keys:
            raise AnnotationValidationError("annotation candidate keys must be unique")
        keys.add(key)
        formatted = format_teaching_content(
            candidate.english_original, candidate.chinese_meaning
        )
        assert formatted
        normalized_expression = " ".join(candidate.english_original.casefold().split())
        if normalized_expression in expressions:
            raise AnnotationValidationError("teaching expressions must be unique")
        expressions.add(normalized_expression)
        _positive_priority(candidate.value_priority, label="teaching priority")
        if not candidate.occurrences:
            raise AnnotationValidationError("teaching candidate requires an occurrence")
        occurrences: list[TeachingOccurrence] = []
        for occurrence in candidate.occurrences:
            if not isinstance(occurrence, TeachingOccurrence):
                raise AnnotationValidationError(
                    "teaching occurrence has an invalid type"
                )
            validate_source_span(
                index,
                occurrence.unit_id,
                occurrence.source_start,
                occurrence.source_end,
                candidate.english_original,
            )
            unit_id, start, end, target_slice = validate_target_span(
                index,
                occurrence.unit_id,
                occurrence.target_start,
                occurrence.target_end,
            )
            if visible_character_count(target_slice) == 0:
                raise AnnotationValidationError(
                    "teaching occurrence target has no visible content"
                )
            occurrences.append(
                TeachingOccurrence(
                    unit_id,
                    occurrence.source_start,
                    occurrence.source_end,
                    start,
                    end,
                )
            )
        occurrences.sort(
            key=lambda occurrence: (
                index.unit_order[occurrence.unit_id],
                occurrence.target_start,
                occurrence.target_end,
                occurrence.source_start,
            )
        )
        if len(set(occurrences)) != len(occurrences):
            raise AnnotationValidationError("teaching occurrences must be unique")
        validated_teaching.append(replace(candidate, occurrences=tuple(occurrences)))

    validated_figures: list[FigureNoteCandidate] = []
    for candidate in tuple(figure_candidates):
        if not isinstance(candidate, FigureNoteCandidate):
            raise AnnotationValidationError("figure candidate has an invalid type")
        key = _nonempty_line(candidate.key, label="figure candidate key")
        if key in keys:
            raise AnnotationValidationError("annotation candidate keys must be unique")
        keys.add(key)
        _nonempty_line(candidate.figure_id, label="figure id")
        content = _nonempty_line(candidate.content, label="figure note")
        compact = _nonempty_line(candidate.compact_content, label="compact figure note")
        if visible_character_count(compact) > visible_character_count(content):
            raise AnnotationValidationError("compact figure note cannot be longer")
        _positive_priority(candidate.value_priority, label="figure-note priority")
        if type(candidate.essential) is not bool:
            raise AnnotationValidationError(
                "figure-note essential flag must be boolean"
            )
        unit_id, _, _, target_slice = validate_target_span(
            index,
            candidate.caption_unit_id,
            candidate.target_start,
            candidate.target_end,
        )
        if index.roles[unit_id] not in {"figure-caption", "table-caption"}:
            raise AnnotationValidationError("figure note must bind to a caption unit")
        if visible_character_count(target_slice) == 0:
            raise AnnotationValidationError("figure-note anchor has no visible content")
        if not candidate.evidence:
            raise AnnotationValidationError("figure note requires direct evidence")
        for evidence in candidate.evidence:
            if isinstance(evidence, DirectEvidence):
                validate_source_span(
                    index,
                    evidence.unit_id,
                    evidence.source_start,
                    evidence.source_end,
                    evidence.quote,
                )
            elif isinstance(evidence, FrozenObjectEvidence):
                values = (
                    evidence.object_id,
                    evidence.locator,
                    evidence.evidence_text,
                )
                if any(
                    not isinstance(value, str)
                    or not value.strip()
                    or value != value.strip()
                    for value in values
                ):
                    raise AnnotationValidationError(
                        "frozen object evidence fields must be non-empty"
                    )
                if (
                    len(evidence.source_artifact_hash) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in evidence.source_artifact_hash
                    )
                    or evidence.evidence_sha256
                    != sha256_bytes(evidence.evidence_text.encode("utf-8"))
                ):
                    raise AnnotationValidationError(
                        "frozen object evidence hash is invalid"
                    )
                if verify_frozen_evidence is None:
                    raise AnnotationValidationError(
                        "frozen object evidence requires an upstream verifier"
                    )
                try:
                    verified = verify_frozen_evidence(evidence)
                except Exception as exc:
                    raise AnnotationValidationError(
                        "frozen object evidence upstream verification failed"
                    ) from exc
                if verified is not True:
                    raise AnnotationValidationError(
                        "frozen object evidence upstream verification failed"
                    )
            else:
                raise AnnotationValidationError(
                    "figure direct evidence has an invalid type"
                )
        ordered_evidence = tuple(
            sorted(
                candidate.evidence,
                key=lambda evidence: sha256_canonical(evidence.to_dict()),
            )
        )
        evidence_hashes = [
            sha256_canonical(evidence.to_dict()) for evidence in ordered_evidence
        ]
        if len(set(evidence_hashes)) != len(evidence_hashes):
            raise AnnotationValidationError("figure direct evidence must be unique")
        validated_figures.append(replace(candidate, evidence=ordered_evidence))

    validated_figures.sort(
        key=lambda candidate: (
            not candidate.essential,
            -candidate.value_priority,
            index.unit_order[candidate.caption_unit_id],
            candidate.target_start,
            candidate.key,
        )
    )
    validated_teaching.sort(
        key=lambda candidate: (
            len(candidate.occurrences) != 1,
            -candidate.value_priority,
            index.unit_order[candidate.occurrences[0].unit_id],
            candidate.occurrences[0].target_start,
            candidate.key,
        )
    )
    teaching = tuple(validated_teaching)
    figures = tuple(validated_figures)
    return FrozenOrangeCandidateSet(
        teaching_candidates=teaching,
        figure_candidates=figures,
        candidate_set_hash=sha256_canonical(_candidate_snapshot(teaching, figures)),
    )


def select_orange_annotations(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    *,
    figure_candidates: Sequence[FigureNoteCandidate] | None = None,
    teaching_candidates: Sequence[TeachingCandidate] | None = None,
    candidate_set: FrozenOrangeCandidateSet | None = None,
    mandatory_items: Sequence[MandatoryAnnotation] = (),
    auxiliary_size_mpt: int,
    trial_layout: LayoutTrial,
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> OrangeSelectionResult:
    """Freeze candidates, trial figures first, then defer ordinary notes as needed."""

    if (
        type(auxiliary_size_mpt) is not int
        or auxiliary_size_mpt < MIN_AUXILIARY_SIZE_MPT
    ):
        raise AnnotationValidationError("auxiliary size is below the fixed print floor")
    if not callable(trial_layout):
        raise AnnotationValidationError("layout trial callback is required")
    index = annotation_index(units, translation)
    frozen_mandatory = _freeze_mandatory_items(index, mandatory_items)
    mandatory_items_hash = sha256_canonical(
        {"items": [item.to_item() for item in frozen_mandatory]}
    )
    if candidate_set is None:
        if figure_candidates is None or teaching_candidates is None:
            raise AnnotationValidationError(
                "a frozen candidate set or both legacy candidate lists are required"
            )
        candidate_set = freeze_orange_candidate_set(
            units,
            translation,
            figure_candidates=figure_candidates,
            teaching_candidates=teaching_candidates,
            verify_frozen_evidence=verify_frozen_evidence,
        )
    else:
        if not isinstance(candidate_set, FrozenOrangeCandidateSet):
            raise AnnotationValidationError("candidate set has an invalid type")
        if figure_candidates is not None or teaching_candidates is not None:
            raise AnnotationValidationError(
                "frozen and legacy candidate inputs cannot be mixed"
            )
        recomputed = freeze_orange_candidate_set(
            units,
            translation,
            figure_candidates=candidate_set.figure_candidates,
            teaching_candidates=candidate_set.teaching_candidates,
            verify_frozen_evidence=verify_frozen_evidence,
        )
        if recomputed.candidate_set_hash != candidate_set.candidate_set_hash:
            raise AnnotationValidationError("candidate-set hash does not recompute")
        if recomputed != candidate_set:
            raise AnnotationValidationError("candidate set is not canonically frozen")

    validated_teaching = candidate_set.teaching_candidates
    validated_figures = candidate_set.figure_candidates
    candidate_set_hash = candidate_set.candidate_set_hash

    mandatory = _trial(
        trial_layout,
        LayoutTrialRequest(
            phase="mandatory-only",
            placements=(),
            allow_continuation=False,
            mandatory_items=frozen_mandatory,
            mandatory_items_hash=mandatory_items_hash,
        ),
    )
    if not mandatory.feasible:
        raise AnnotationValidationError("mandatory-only layout must be feasible")

    accepted: list[OrangePlacement] = []
    accepted_by_figure: defaultdict[str, int] = defaultdict(int)
    current_continuation_pages = mandatory.added_continuation_pages
    for candidate in validated_figures:
        if accepted_by_figure[candidate.figure_id] >= MAX_FIGURE_NOTES_PER_FIGURE:
            continue
        base = OrangePlacement(
            kind="figure-table-reading",
            candidate_key=candidate.key,
            unit_id=candidate.caption_unit_id,
            target_start=candidate.target_start,
            target_end=candidate.target_end,
            content=candidate.content,
            auxiliary_size_mpt=auxiliary_size_mpt,
            priority=candidate.value_priority,
            figure_id=candidate.figure_id,
            evidence=candidate.evidence,
            essential=candidate.essential,
        )
        chosen: OrangePlacement | None = None
        chosen_continuation_pages = current_continuation_pages
        native_variants = [base]
        if candidate.compact_content != candidate.content:
            native_variants.append(
                replace(base, content=candidate.compact_content, compacted=True)
            )
        for variant in native_variants:
            trial = _trial(
                trial_layout,
                LayoutTrialRequest(
                    phase="candidate-trial",
                    placements=tuple((*accepted, variant)),
                    allow_continuation=False,
                    mandatory_items=frozen_mandatory,
                    mandatory_items_hash=mandatory_items_hash,
                ),
            )
            if (
                trial.feasible
                and trial.added_continuation_pages == current_continuation_pages
            ):
                chosen = variant
                chosen_continuation_pages = trial.added_continuation_pages
                break
        if chosen is None and candidate.essential:
            compact = replace(
                base,
                content=candidate.compact_content,
                compacted=candidate.compact_content != candidate.content,
            )
            trial = _trial(
                trial_layout,
                LayoutTrialRequest(
                    phase="candidate-trial",
                    placements=tuple((*accepted, compact)),
                    allow_continuation=True,
                    mandatory_items=frozen_mandatory,
                    mandatory_items_hash=mandatory_items_hash,
                ),
            )
            if (
                trial.feasible
                and trial.added_continuation_pages >= current_continuation_pages
            ):
                chosen = replace(
                    compact,
                    uses_continuation=(
                        trial.added_continuation_pages > current_continuation_pages
                    ),
                )
                chosen_continuation_pages = trial.added_continuation_pages
        if chosen is not None:
            accepted.append(chosen)
            accepted_by_figure[candidate.figure_id] += 1
            current_continuation_pages = chosen_continuation_pages

    for candidate in validated_teaching:
        occurrences = candidate.occurrences
        content = format_teaching_content(
            candidate.english_original, candidate.chinese_meaning
        )
        for occurrence_index, occurrence in enumerate(occurrences):
            placement = OrangePlacement(
                kind="dark-orange-teaching",
                candidate_key=candidate.key,
                unit_id=occurrence.unit_id,
                target_start=occurrence.target_start,
                target_end=occurrence.target_end,
                content=content,
                auxiliary_size_mpt=auxiliary_size_mpt,
                priority=candidate.value_priority,
                source_start=occurrence.source_start,
                source_end=occurrence.source_end,
                english_original=candidate.english_original,
                chinese_meaning=candidate.chinese_meaning,
                deferred_occurrences=occurrence_index,
            )
            trial = _trial(
                trial_layout,
                LayoutTrialRequest(
                    phase="candidate-trial",
                    placements=tuple((*accepted, placement)),
                    allow_continuation=False,
                    mandatory_items=frozen_mandatory,
                    mandatory_items_hash=mandatory_items_hash,
                ),
            )
            if (
                trial.feasible
                and trial.added_continuation_pages == current_continuation_pages
            ):
                accepted.append(placement)
                break

    selection_items = [placement.to_item() for placement in accepted]
    orange_kind_order = {
        "figure-table-reading": 0,
        "dark-orange-teaching": 1,
    }
    selection_items.sort(
        key=lambda item: (
            index.unit_order[str(item["unit_id"])],
            int(item["target_start"]),
            orange_kind_order[str(item["kind"])],
            str(item["id"]),
        )
    )
    selection_hash = sha256_canonical({"placements": selection_items})
    final = _trial(
        trial_layout,
        LayoutTrialRequest(
            phase="final-frozen",
            placements=tuple(accepted),
            allow_continuation=any(
                placement.uses_continuation for placement in accepted
            ),
            mandatory_items=frozen_mandatory,
            mandatory_items_hash=mandatory_items_hash,
            frozen_selection_hash=selection_hash,
        ),
    )
    if (
        not final.feasible
        or final.added_continuation_pages != current_continuation_pages
    ):
        raise AnnotationValidationError("frozen annotation selection is not feasible")
    return OrangeSelectionResult(
        mandatory_items=frozen_mandatory,
        mandatory_items_hash=mandatory_items_hash,
        placements=tuple(accepted),
        auxiliary_size_mpt=auxiliary_size_mpt,
        candidate_set_hash=candidate_set_hash,
        selection_hash=selection_hash,
        continuation_pages=current_continuation_pages,
    )


__all__ = [
    "ANNOTATION_COLORS",
    "ANNOTATION_STYLE_VERSION",
    "FrozenOrangeCandidateSet",
    "LayoutTrial",
    "LayoutTrialRequest",
    "LayoutTrialResult",
    "OrangePlacement",
    "OrangeSelectionResult",
    "freeze_orange_candidate_set",
    "orange_selection_policy_payload",
    "select_orange_annotations",
]
