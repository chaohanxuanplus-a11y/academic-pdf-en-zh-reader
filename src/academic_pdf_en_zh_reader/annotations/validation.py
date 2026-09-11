# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Exact source/translation binding for annotation inputs and artifacts."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    FrozenEvidenceVerifier,
    FrozenObjectEvidence,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
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

ANNOTATION_STYLE_VERSION = 1
ANNOTATION_COLORS = {
    "body": "#111111",
    "dark_red": "#7F1D1D",
    "dark_orange": "#A84F08",
    "bright_red": "#D00000",
}
_COMMON_ITEM_FIELDS = frozenset(
    {"id", "unit_id", "kind", "target_start", "target_end", "priority", "content"}
)
_ALLOWED_ITEM_FIELDS = {
    "dark-red-highlight": _COMMON_ITEM_FIELDS | {"candidate_id"},
    "dark-orange-teaching": _COMMON_ITEM_FIELDS
    | {
        "candidate_key",
        "attachment",
        "auxiliary_size_mpt",
        "uses_continuation",
        "source_start",
        "source_end",
        "english_original",
        "chinese_meaning",
        "deferred_occurrences",
        "essential",
    },
    "figure-table-reading": _COMMON_ITEM_FIELDS
    | {
        "candidate_key",
        "attachment",
        "auxiliary_size_mpt",
        "uses_continuation",
        "figure_id",
        "evidence",
        "essential",
        "compacted",
    },
    "bright-red-ambiguity": _COMMON_ITEM_FIELDS
    | {
        "ambiguity_key_id",
        "underline",
        "label_after_span",
        "label_size_mpt",
    },
}


class AnnotationValidationError(ValueError):
    """Raised when annotation evidence or output is not exactly bound."""


@dataclass(frozen=True, order=True)
class BoundTargetSpan:
    unit_id: str
    target_start: int
    target_end: int


@dataclass(frozen=True)
class AnnotationIndex:
    unit_order: Mapping[str, int]
    roles: Mapping[str, str]
    source_text: Mapping[str, str]
    target_text: Mapping[str, str]


def annotation_index(
    units: Mapping[str, object], translation: Mapping[str, object]
) -> AnnotationIndex:
    """Validate the translation contract and return exact unit lookup tables."""

    try:
        validate_translation_artifact(units, translation)
    except TranslationValidationError as exc:
        raise AnnotationValidationError(
            "annotation translation input is invalid"
        ) from exc
    source_units = units["units"]
    translated_units = translation["units"]
    assert isinstance(source_units, list)
    assert isinstance(translated_units, list)
    return AnnotationIndex(
        unit_order={str(row["id"]): index for index, row in enumerate(source_units)},
        roles={str(row["id"]): str(row["role"]) for row in source_units},
        source_text={str(row["id"]): str(row["source_text"]) for row in source_units},
        target_text={
            str(row["unit_id"]): str(row["chinese_text"]) for row in translated_units
        },
    )


def _integer(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise AnnotationValidationError(f"{label} must be an integer")
    return value


def validate_target_span(
    index: AnnotationIndex, unit_id: object, start: object, end: object
) -> tuple[str, int, int, str]:
    if not isinstance(unit_id, str) or unit_id not in index.target_text:
        raise AnnotationValidationError("annotation references an unknown unit")
    target_start = _integer(start, label="target span start")
    target_end = _integer(end, label="target span end")
    text = index.target_text[unit_id]
    if target_start < 0 or target_end <= target_start or target_end > len(text):
        raise AnnotationValidationError("annotation target span is out of bounds")
    return unit_id, target_start, target_end, text[target_start:target_end]


def validate_source_span(
    index: AnnotationIndex,
    unit_id: object,
    start: object,
    end: object,
    quote: object,
) -> tuple[str, int, int, str]:
    if not isinstance(unit_id, str) or unit_id not in index.source_text:
        raise AnnotationValidationError(
            "annotation evidence references an unknown unit"
        )
    source_start = _integer(start, label="source span start")
    source_end = _integer(end, label="source span end")
    text = index.source_text[unit_id]
    if source_start < 0 or source_end <= source_start or source_end > len(text):
        raise AnnotationValidationError("annotation source span is out of bounds")
    if not isinstance(quote, str) or text[source_start:source_end] != quote:
        raise AnnotationValidationError(
            "annotation evidence does not match source slice"
        )
    return unit_id, source_start, source_end, quote


def is_content_character(character: str) -> bool:
    return not character.isspace() and not unicodedata.category(character).startswith(
        "P"
    )


def visible_character_count(text: str) -> int:
    return sum(is_content_character(character) for character in text)


def spans_overlap(first: BoundTargetSpan, second: BoundTargetSpan) -> bool:
    return (
        first.unit_id == second.unit_id
        and first.target_start < second.target_end
        and second.target_start < first.target_end
    )


def _same_kind_spans_overlap(
    spans: list[tuple[BoundTargetSpan, str]],
) -> bool:
    ordered = sorted(
        spans,
        key=lambda entry: (
            entry[0].unit_id,
            entry[0].target_start,
            entry[0].target_end,
            entry[1],
        ),
    )
    return any(
        spans_overlap(first[0], second[0])
        for first, second in zip(ordered, ordered[1:], strict=False)
    )


def _item_id_payload(item: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in item.items() if key != "id"}


def stable_annotation_id(item: Mapping[str, object]) -> str:
    return f"annotation-{sha256_canonical(_item_id_payload(item))[:24]}"


def _ratio_basis_points(numerator: int, denominator: int) -> int:
    if denominator == 0:
        if numerator:
            raise AnnotationValidationError("annotation ratio has a zero denominator")
        return 0
    return (numerator * 10_000 + denominator // 2) // denominator


def _ambiguity_keys(review: Mapping[str, object]) -> set[str]:
    issues = review.get("issues")
    assert isinstance(issues, list)
    return {
        str(issue["ambiguity_key"]["id"])
        for issue in issues
        if issue["severity"] == "unresolved_ambiguity"
    }


def _validate_generated_item(
    item: Mapping[str, object],
    *,
    index: AnnotationIndex,
    ambiguity_keys: set[str],
    style: TypographyStyleContract,
    auxiliary_size_mpt: int,
    verify_frozen_evidence: FrozenEvidenceVerifier | None,
) -> None:
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in _ALLOWED_ITEM_FIELDS:
        raise AnnotationValidationError("annotation kind is unsupported")
    if set(item) != _ALLOWED_ITEM_FIELDS[kind]:
        raise AnnotationValidationError(
            f"{kind} annotation fields do not match its discriminated contract"
        )
    unit_id, start, end, target_slice = validate_target_span(
        index, item.get("unit_id"), item.get("target_start"), item.get("target_end")
    )
    if item.get("id") != stable_annotation_id(item):
        raise AnnotationValidationError("annotation item id does not match its content")
    if kind == "dark-red-highlight":
        if index.roles[unit_id] in {"title", "abstract", "keywords"}:
            raise AnnotationValidationError("dark-red highlight uses an excluded role")
        if visible_character_count(target_slice) == 0:
            raise AnnotationValidationError("dark-red highlight has no visible content")
    elif kind == "dark-orange-teaching":
        english = item.get("english_original")
        chinese = item.get("chinese_meaning")
        content = item.get("content")
        if (
            not isinstance(english, str)
            or not english.strip()
            or not isinstance(chinese, str)
            or not chinese.strip()
            or content != f"{english} — {chinese}"
        ):
            raise AnnotationValidationError("teaching content format is invalid")
        if item.get("auxiliary_size_mpt") != auxiliary_size_mpt:
            raise AnnotationValidationError("teaching auxiliary size is not fixed")
        if item.get("attachment") != "below-translation":
            raise AnnotationValidationError(
                "teaching note must attach below translation"
            )
        validate_source_span(
            index,
            unit_id,
            item.get("source_start"),
            item.get("source_end"),
            english,
        )
    elif kind == "figure-table-reading":
        if index.roles[unit_id] not in {"figure-caption", "table-caption"}:
            raise AnnotationValidationError("figure note must bind to a caption unit")
        if item.get("auxiliary_size_mpt") != auxiliary_size_mpt:
            raise AnnotationValidationError("figure-note auxiliary size is not fixed")
        if item.get("attachment") != "below-translation":
            raise AnnotationValidationError("figure note must attach below translation")
        if item.get("uses_continuation") is True and item.get("essential") is not True:
            raise AnnotationValidationError(
                "only an essential figure note may enable a continuation"
            )
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise AnnotationValidationError("figure note requires direct evidence")
        for entry in evidence:
            if not isinstance(entry, Mapping):
                raise AnnotationValidationError("figure evidence is invalid")
            evidence_kind = entry.get("evidence_kind")
            if evidence_kind == "semantic-unit":
                validate_source_span(
                    index,
                    entry.get("unit_id"),
                    entry.get("source_start"),
                    entry.get("source_end"),
                    entry.get("quote"),
                )
            elif evidence_kind == "frozen-object":
                try:
                    record = FrozenObjectEvidence(
                        source_artifact_hash=str(entry["source_artifact_hash"]),
                        object_id=str(entry["object_id"]),
                        locator=str(entry["locator"]),
                        evidence_text=str(entry["evidence_text"]),
                        evidence_sha256=str(entry["evidence_sha256"]),
                    )
                except KeyError as exc:
                    raise AnnotationValidationError(
                        "frozen object evidence is incomplete"
                    ) from exc
                if (
                    len(record.source_artifact_hash) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in record.source_artifact_hash
                    )
                    or record.evidence_sha256
                    != sha256_bytes(record.evidence_text.encode("utf-8"))
                    or not record.object_id.strip()
                    or not record.locator.strip()
                    or not record.evidence_text.strip()
                ):
                    raise AnnotationValidationError("frozen object evidence is invalid")
                if verify_frozen_evidence is None:
                    raise AnnotationValidationError(
                        "frozen object evidence requires an upstream verifier"
                    )
                try:
                    verified = verify_frozen_evidence(record)
                except Exception as exc:
                    raise AnnotationValidationError(
                        "frozen object evidence upstream verification failed"
                    ) from exc
                if verified is not True:
                    raise AnnotationValidationError(
                        "frozen object evidence upstream verification failed"
                    )
            else:
                raise AnnotationValidationError("figure evidence kind is invalid")
    elif kind == "bright-red-ambiguity":
        key = item.get("ambiguity_key_id")
        if key not in ambiguity_keys:
            raise AnnotationValidationError("ambiguity annotation has an unknown key")
        if item.get("underline") is not True:
            raise AnnotationValidationError("ambiguity span must be underlined")
        expected_size = style.style_for(index.roles[unit_id]).size_mpt
        if item.get("label_size_mpt") != expected_size:
            raise AnnotationValidationError("ambiguity label size must match its role")
        label = item.get("content")
        if label not in {None, "（可能存在歧义）"}:
            raise AnnotationValidationError("ambiguity label text is invalid")
        if item.get("label_after_span") is not (label is not None):
            raise AnnotationValidationError("ambiguity label placement is invalid")
        if visible_character_count(target_slice) == 0:
            raise AnnotationValidationError(
                "ambiguity target span has no visible content"
            )


def validate_annotations_against_inputs(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    style: TypographyStyleContract,
    *,
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
    expected_candidate_set_hash: str | None = None,
) -> None:
    """Recompute bindings; production also passes the job's candidate-set hash."""

    index = annotation_index(units, translation)
    try:
        validate_review(translation, review)
        validate_artifact("annotations", annotations)
    except (ReviewValidationError, SchemaValidationError) as exc:
        raise AnnotationValidationError(
            "annotation parent or schema is invalid"
        ) from exc
    if annotations.get("units_hash") != sha256_canonical(units):
        raise AnnotationValidationError("annotation units_hash does not match")
    if annotations.get("translation_hash") != sha256_canonical(translation):
        raise AnnotationValidationError("annotation translation_hash does not match")
    if annotations.get("review_hash") != sha256_canonical(review):
        raise AnnotationValidationError("annotation review_hash does not match")
    if annotations.get("style_version") != ANNOTATION_STYLE_VERSION:
        raise AnnotationValidationError("annotation style version does not match")
    if annotations.get("colors") != ANNOTATION_COLORS:
        raise AnnotationValidationError("annotation colors do not match fixed tokens")
    if expected_candidate_set_hash is not None:
        if len(expected_candidate_set_hash) != 64 or any(
            character not in "0123456789abcdef"
            for character in expected_candidate_set_hash
        ):
            raise AnnotationValidationError("expected candidate-set hash is invalid")
        if annotations.get("candidate_set_hash") != expected_candidate_set_hash:
            raise AnnotationValidationError(
                "annotation candidate-set hash does not match job state"
            )
    auxiliary_size = style.style_for("auxiliary").size_mpt
    if annotations.get("auxiliary_size_mpt") != auxiliary_size:
        raise AnnotationValidationError(
            "annotation auxiliary size does not match style"
        )

    items = annotations.get("items")
    assert isinstance(items, list)
    keys = _ambiguity_keys(review)
    identifiers: set[str] = set()
    ambiguity_labels: defaultdict[str, int] = defaultdict(int)
    ambiguity_by_key: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    ambiguity_spans: list[tuple[BoundTargetSpan, str]] = []
    red_spans: list[tuple[BoundTargetSpan, str]] = []
    figure_counts: defaultdict[str, int] = defaultdict(int)
    teaching_keys: set[str] = set()
    for item in items:
        assert isinstance(item, Mapping)
        _validate_generated_item(
            item,
            index=index,
            ambiguity_keys=keys,
            style=style,
            auxiliary_size_mpt=auxiliary_size,
            verify_frozen_evidence=verify_frozen_evidence,
        )
        identifier = str(item["id"])
        if identifier in identifiers:
            raise AnnotationValidationError("annotation item IDs must be unique")
        identifiers.add(identifier)
        if item["kind"] == "bright-red-ambiguity":
            key = str(item["ambiguity_key_id"])
            ambiguity_by_key[key].append(item)
            if item.get("content") is not None:
                ambiguity_labels[key] += 1
            ambiguity_spans.append(
                (
                    BoundTargetSpan(
                        str(item["unit_id"]),
                        int(item["target_start"]),
                        int(item["target_end"]),
                    ),
                    identifier,
                )
            )
        elif item["kind"] == "dark-red-highlight":
            red_spans.append(
                (
                    BoundTargetSpan(
                        str(item["unit_id"]),
                        int(item["target_start"]),
                        int(item["target_end"]),
                    ),
                    identifier,
                )
            )
        elif item["kind"] == "figure-table-reading":
            figure_counts[str(item["figure_id"])] += 1
        elif item["kind"] == "dark-orange-teaching":
            candidate_key = str(item["candidate_key"])
            if candidate_key in teaching_keys:
                raise AnnotationValidationError(
                    "teaching candidate may appear only once"
                )
            teaching_keys.add(candidate_key)
    if set(ambiguity_labels) != keys or any(
        count != 1 for count in ambiguity_labels.values()
    ):
        raise AnnotationValidationError("each unresolved ambiguity needs one label")
    if _same_kind_spans_overlap(ambiguity_spans):
        raise AnnotationValidationError("ambiguity target spans must not overlap")
    if _same_kind_spans_overlap(red_spans):
        raise AnnotationValidationError("dark-red target spans must not overlap")
    for key, marked_items in ambiguity_by_key.items():
        ordered = sorted(
            marked_items,
            key=lambda item: (
                index.unit_order[str(item["unit_id"])],
                int(item["target_start"]),
                int(item["target_end"]),
                str(item["id"]),
            ),
        )
        if ordered[0].get("content") != "（可能存在歧义）" or any(
            item.get("content") is not None for item in ordered[1:]
        ):
            raise AnnotationValidationError(
                f"ambiguity {key} label must follow its first occurrence"
            )

    denominator = 0
    for unit_id, text in index.target_text.items():
        if index.roles[unit_id] in {"title", "abstract", "keywords"}:
            continue
        denominator += sum(is_content_character(character) for character in text)
    highlighted = 0
    for item in items:
        if item["kind"] != "dark-red-highlight":
            continue
        span = BoundTargetSpan(
            str(item["unit_id"]), int(item["target_start"]), int(item["target_end"])
        )
        highlighted += visible_character_count(
            index.target_text[span.unit_id][span.target_start : span.target_end]
        )
    if highlighted * 100 > denominator * 10:
        raise AnnotationValidationError(
            "dark-red highlight exceeds the ten percent cap"
        )
    if annotations.get("highlight_ratio_basis_points") != _ratio_basis_points(
        highlighted, denominator
    ):
        raise AnnotationValidationError("dark-red ratio does not recompute")

    orange_characters = sum(
        visible_character_count(str(item.get("content") or ""))
        for item in items
        if item["kind"] in {"dark-orange-teaching", "figure-table-reading"}
    )
    translation_characters = sum(
        visible_character_count(text) for text in index.target_text.values()
    )
    if annotations.get("teaching_ratio_basis_points") != _ratio_basis_points(
        orange_characters, translation_characters + orange_characters
    ):
        raise AnnotationValidationError("teaching ratio does not recompute")

    kind_order = {
        "dark-red-highlight": 0,
        "bright-red-ambiguity": 1,
        "figure-table-reading": 2,
        "dark-orange-teaching": 3,
    }
    ordered_items = sorted(
        items,
        key=lambda item: (
            index.unit_order[str(item["unit_id"])],
            int(item["target_start"]),
            kind_order[str(item["kind"])],
            str(item["id"]),
        ),
    )
    if items != ordered_items:
        raise AnnotationValidationError("annotation items are not in stable order")

    mandatory_items = [
        item
        for item in items
        if item["kind"] in {"dark-red-highlight", "bright-red-ambiguity"}
    ]
    if annotations.get("mandatory_items_hash") != sha256_canonical(
        {"items": mandatory_items}
    ):
        raise AnnotationValidationError(
            "annotation mandatory_items_hash does not recompute"
        )

    selection_payload = {
        "style_version": annotations.get("style_version"),
        "colors": annotations.get("colors"),
        "auxiliary_size_mpt": auxiliary_size,
        "highlight_ratio_basis_points": annotations.get("highlight_ratio_basis_points"),
        "teaching_ratio_basis_points": annotations.get("teaching_ratio_basis_points"),
        "items": items,
    }
    if annotations.get("selection_hash") != sha256_canonical(selection_payload):
        raise AnnotationValidationError("annotation selection_hash does not recompute")
    orange_items = [
        item
        for item in items
        if item["kind"] in {"dark-orange-teaching", "figure-table-reading"}
    ]
    if annotations.get("orange_selection_hash") != sha256_canonical(
        {"placements": orange_items}
    ):
        raise AnnotationValidationError(
            "annotation orange_selection_hash does not recompute"
        )
    input_payload = {
        "contract_version": "1.0.0",
        "units_hash": annotations.get("units_hash"),
        "translation_hash": annotations.get("translation_hash"),
        "review_hash": annotations.get("review_hash"),
        "mandatory_items_hash": annotations.get("mandatory_items_hash"),
        "candidate_set_hash": annotations.get("candidate_set_hash"),
        "orange_selection_hash": annotations.get("orange_selection_hash"),
    }
    if annotations.get("annotations_input_hash") != sha256_canonical(input_payload):
        raise AnnotationValidationError("annotation input hash does not recompute")


__all__ = [
    "ANNOTATION_COLORS",
    "ANNOTATION_STYLE_VERSION",
    "AnnotationIndex",
    "AnnotationValidationError",
    "BoundTargetSpan",
    "annotation_index",
    "is_content_character",
    "spans_overlap",
    "stable_annotation_id",
    "validate_annotations_against_inputs",
    "validate_source_span",
    "validate_target_span",
    "visible_character_count",
]
