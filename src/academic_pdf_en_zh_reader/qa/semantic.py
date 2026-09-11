# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Mechanical semantic and fixed annotation gates without paper-text logging."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from academic_pdf_en_zh_reader.annotations.validation import (
    ANNOTATION_COLORS,
    BoundTargetSpan,
    annotation_index,
    spans_overlap,
    stable_annotation_id,
    visible_character_count,
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


class SemanticQaError(ValueError):
    """A stable semantic QA failure that never includes document content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_semantics(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
) -> dict[str, int]:
    """Require exact, ordered translation coverage and independent review."""

    try:
        validate_translation_artifact(units, translation)
    except (
        SchemaValidationError,
        TranslationValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise SemanticQaError("SEMANTIC_TRANSLATION_INVALID") from exc
    try:
        gate = validate_review(translation, review)
    except (ReviewValidationError, SchemaValidationError, TypeError, ValueError) as exc:
        raise SemanticQaError("SEMANTIC_REVIEW_INVALID") from exc
    return {
        "unit_count": len(translation["units"]),
        "ambiguity_key_count": len(gate.unresolved_ambiguity_keys),
    }


def _ratio_basis_points(numerator: int, denominator: int) -> int:
    return (
        0
        if denominator == 0
        else (numerator * 10_000 + denominator // 2) // denominator
    )


def _flow_styles(frame_graph: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for flow in frame_graph["unit_flows"]:  # type: ignore[index]
        result[str(flow["unit_id"])] = flow["style"]
    return result


def validate_annotation_policy(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    overlay_plan: Mapping[str, object],
) -> dict[str, int]:
    """Recheck the fixed red/orange/ambiguity visual rules from frozen parents."""

    try:
        validate_artifact("annotations", annotations)
        index = annotation_index(units, translation)
        gate = validate_review(translation, review)
    except Exception as exc:
        raise SemanticQaError("ANNOTATION_POLICY_INVALID") from exc
    if (
        annotations.get("units_hash") != sha256_canonical(units)
        or annotations.get("translation_hash") != sha256_canonical(translation)
        or annotations.get("review_hash") != sha256_canonical(review)
        or annotations.get("colors") != ANNOTATION_COLORS
    ):
        raise SemanticQaError("ANNOTATION_PARENT_MISMATCH")
    render_colors = overlay_plan.get("render_style", {})
    if not isinstance(render_colors, Mapping) or render_colors.get("colors") != {
        **ANNOTATION_COLORS,
        "muted_gray": "#666666",
    }:
        raise SemanticQaError("ANNOTATION_PAINT_INVALID")

    items = annotations.get("items")
    if not isinstance(items, list):
        raise SemanticQaError("ANNOTATION_POLICY_INVALID")
    unit_order = index.unit_order
    styles = _flow_styles(frame_graph)
    unresolved_keys = set(gate.unresolved_ambiguity_keys)
    labels: defaultdict[str, int] = defaultdict(int)
    occurrences: defaultdict[str, list[tuple[int, int, object]]] = defaultdict(list)
    red_spans: list[BoundTargetSpan] = []
    red_characters = 0
    item_ids: set[str] = set()
    kind_order = {
        "dark-red-highlight": 0,
        "bright-red-ambiguity": 1,
        "figure-table-reading": 2,
        "dark-orange-teaching": 3,
    }
    ordering: list[tuple[int, int, int, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise SemanticQaError("ANNOTATION_POLICY_INVALID")
        kind = str(item.get("kind"))
        unit_id = str(item.get("unit_id"))
        if kind not in kind_order or unit_id not in index.target_text:
            raise SemanticQaError("ANNOTATION_POLICY_INVALID")
        try:
            start = int(item["target_start"])
            end = int(item["target_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticQaError("ANNOTATION_POLICY_INVALID") from exc
        if (
            type(item.get("target_start")) is not int
            or type(item.get("target_end")) is not int
            or start < 0
            or end <= start
            or end > len(index.target_text[unit_id])
        ):
            raise SemanticQaError("ANNOTATION_SPAN_INVALID")
        identifier = str(item.get("id"))
        if identifier in item_ids or identifier != stable_annotation_id(item):
            raise SemanticQaError("ANNOTATION_ID_INVALID")
        item_ids.add(identifier)
        ordering.append((unit_order[unit_id], start, kind_order[kind], identifier))
        span = BoundTargetSpan(unit_id, start, end)
        if kind == "dark-red-highlight":
            if index.roles[unit_id] in {"title", "abstract", "keywords"}:
                raise SemanticQaError("ANNOTATION_RED_EXCLUDED_ROLE")
            if any(spans_overlap(span, previous) for previous in red_spans):
                raise SemanticQaError("ANNOTATION_RED_OVERLAP")
            red_spans.append(span)
            red_characters += visible_character_count(
                index.target_text[unit_id][start:end]
            )
        elif kind == "bright-red-ambiguity":
            key = str(item.get("ambiguity_key_id"))
            style = styles.get(unit_id)
            if (
                key not in unresolved_keys
                or item.get("underline") is not True
                or not isinstance(style, Mapping)
                or item.get("label_size_mpt") != style.get("size_mpt")
                or item.get("content") not in {None, "（可能存在歧义）"}
                or item.get("label_after_span") is not (item.get("content") is not None)
            ):
                raise SemanticQaError("ANNOTATION_AMBIGUITY_INVALID")
            occurrences[key].append((unit_order[unit_id], start, item.get("content")))
            if item.get("content") is not None:
                labels[key] += 1
        elif kind == "dark-orange-teaching":
            if (
                item.get("attachment") != "below-translation"
                or item.get("uses_continuation") is not False
                or item.get("auxiliary_size_mpt")
                != annotations.get("auxiliary_size_mpt")
            ):
                raise SemanticQaError("ANNOTATION_ORANGE_INVALID")
        else:
            if (
                index.roles[unit_id] not in {"figure-caption", "table-caption"}
                or item.get("attachment") != "below-translation"
                or item.get("auxiliary_size_mpt")
                != annotations.get("auxiliary_size_mpt")
                or (
                    item.get("uses_continuation") is True
                    and item.get("essential") is not True
                )
            ):
                raise SemanticQaError("ANNOTATION_FIGURE_INVALID")

    if ordering != sorted(ordering):
        raise SemanticQaError("ANNOTATION_ORDER_INVALID")
    if set(occurrences) != unresolved_keys or any(
        labels[key] != 1 or sorted(values)[0][2] != "（可能存在歧义）"
        for key, values in occurrences.items()
    ):
        raise SemanticQaError("ANNOTATION_AMBIGUITY_INVALID")
    denominator = sum(
        visible_character_count(text)
        for unit_id, text in index.target_text.items()
        if index.roles[unit_id] not in {"title", "abstract", "keywords"}
    )
    ratio = _ratio_basis_points(red_characters, denominator)
    if ratio > 1_000 or annotations.get("highlight_ratio_basis_points") != ratio:
        raise SemanticQaError("ANNOTATION_RED_RATIO_INVALID")

    expected_ambiguity_ids = {
        str(item["id"]) for item in items if item["kind"] == "bright-red-ambiguity"
    }
    rendered_ambiguity_ids = {
        str(underline["annotation_id"])
        for page in overlay_plan["pages"]  # type: ignore[index]
        for underline in page["underlines"]
    }
    if expected_ambiguity_ids != rendered_ambiguity_ids:
        raise SemanticQaError("ANNOTATION_UNDERLINE_PROJECTION_INVALID")
    return {
        "annotation_count": len(items),
        "highlight_ratio_basis_points": ratio,
    }


__all__ = [
    "SemanticQaError",
    "validate_annotation_policy",
    "validate_semantics",
]
