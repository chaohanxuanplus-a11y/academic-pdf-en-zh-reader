# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Bind frozen annotations to pre-layout composite text and auxiliary flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from academic_pdf_en_zh_reader.annotations.figure_notes import FrozenEvidenceVerifier
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrial,
    LayoutTrialRequest,
    LayoutTrialResult,
    OrangeSelectionResult,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
    annotation_index,
    validate_annotations_against_inputs,
)
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.frame_graph import (
    DEFAULT_FRAME_GRAPH_CONFIG,
    FrameGraphConfig,
    FrameGraphError,
    _build_annotated_frame_graph,
)
from academic_pdf_en_zh_reader.layout.solver import (
    DEFAULT_LAYOUT_LIMITS,
    LayoutInfeasibleError,
    LayoutLimits,
    solve_layout,
)
from academic_pdf_en_zh_reader.layout.unit_parts import grapheme_boundaries
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    TypographyStyleContract,
)


class AnnotationAdapterError(ValueError):
    """Raised when an annotation/layout parent binding cannot be trusted."""


@dataclass(frozen=True)
class AnnotationAdapterLimits:
    """Versioned refusal-only limits applied before text measurement."""

    version: int = 1
    max_items_total: int = 10_000
    max_labels_per_unit: int = 128
    max_styled_per_unit: int = 512
    max_auxiliary_per_unit: int = 128
    max_content_chars_per_item: int = 50_000
    max_composite_chars_per_unit: int = 100_000
    max_composite_chars_total: int = 5_000_000


DEFAULT_ANNOTATION_ADAPTER_LIMITS = AnnotationAdapterLimits()


def _enforce_annotation_adapter_limits(
    annotations: Mapping[str, object],
    translation: Mapping[str, object],
    limits: AnnotationAdapterLimits,
) -> None:
    if (
        not isinstance(limits, AnnotationAdapterLimits)
        or type(limits.version) is not int
        or limits.version != 1
    ):
        raise AnnotationAdapterError("annotation adapter limits are invalid")
    numeric_limits = (
        limits.max_items_total,
        limits.max_labels_per_unit,
        limits.max_styled_per_unit,
        limits.max_auxiliary_per_unit,
        limits.max_content_chars_per_item,
        limits.max_composite_chars_per_unit,
        limits.max_composite_chars_total,
    )
    if any(type(value) is not int or value < 0 for value in numeric_limits):
        raise AnnotationAdapterError("annotation adapter limits are invalid")

    if not isinstance(annotations, Mapping) or not isinstance(translation, Mapping):
        raise AnnotationAdapterError("annotation adapter limits input is invalid")
    items = annotations.get("items")
    translated_units = translation.get("units")
    if not isinstance(items, list) or not isinstance(translated_units, list):
        raise AnnotationAdapterError("annotation adapter limits input is invalid")
    if len(items) > limits.max_items_total:
        raise AnnotationAdapterError("annotation adapter limits exceeded")

    target_text: dict[str, str] = {}
    for translated in translated_units:
        if not isinstance(translated, Mapping):
            raise AnnotationAdapterError("annotation adapter limits input is invalid")
        unit_id = translated.get("unit_id")
        chinese_text = translated.get("chinese_text")
        if (
            not isinstance(unit_id, str)
            or not isinstance(chinese_text, str)
            or unit_id in target_text
        ):
            raise AnnotationAdapterError("annotation adapter limits input is invalid")
        target_text[unit_id] = chinese_text
    labels = dict.fromkeys(target_text, 0)
    styled = dict.fromkeys(target_text, 0)
    auxiliary = dict.fromkeys(target_text, 0)
    composite_chars = {unit_id: len(text) for unit_id, text in target_text.items()}
    if (
        any(
            value > limits.max_composite_chars_per_unit
            for value in composite_chars.values()
        )
        or sum(composite_chars.values()) > limits.max_composite_chars_total
    ):
        raise AnnotationAdapterError("annotation adapter limits exceeded")
    for item in items:
        if not isinstance(item, Mapping):
            raise AnnotationAdapterError("annotation adapter limits input is invalid")
        unit_id = item.get("unit_id")
        kind = item.get("kind")
        content = item.get("content")
        if (
            not isinstance(unit_id, str)
            or unit_id not in target_text
            or not isinstance(kind, str)
            or (content is not None and not isinstance(content, str))
        ):
            raise AnnotationAdapterError("annotation adapter limits input is invalid")
        content_length = 0 if content is None else len(content)
        if content_length > limits.max_content_chars_per_item:
            raise AnnotationAdapterError("annotation adapter limits exceeded")
        composite_chars[unit_id] += content_length
        if kind in {"dark-red-highlight", "bright-red-ambiguity"}:
            styled[unit_id] += 1
        if kind == "bright-red-ambiguity" and content is not None:
            labels[unit_id] += 1
        elif kind in {"dark-orange-teaching", "figure-table-reading"}:
            auxiliary[unit_id] += 1
        if (
            labels[unit_id] > limits.max_labels_per_unit
            or styled[unit_id] > limits.max_styled_per_unit
            or auxiliary[unit_id] > limits.max_auxiliary_per_unit
            or composite_chars[unit_id] > limits.max_composite_chars_per_unit
        ):
            raise AnnotationAdapterError("annotation adapter limits exceeded")
    if sum(composite_chars.values()) > limits.max_composite_chars_total:
        raise AnnotationAdapterError("annotation adapter limits exceeded")


def _validate_annotation_span_boundaries(
    annotations: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
) -> None:
    index = annotation_index(units, translation)
    safe_by_unit: dict[str, frozenset[int]] = {}
    for item in annotations["items"]:  # type: ignore[index]
        unit_id = str(item["unit_id"])
        if unit_id not in safe_by_unit:
            safe_by_unit[unit_id] = grapheme_boundaries(index.target_text[unit_id])
        safe = safe_by_unit[unit_id]
        if item["target_start"] not in safe or item["target_end"] not in safe:
            raise AnnotationAdapterError("annotation span splits a grapheme cluster")


def _orange_items(
    request: LayoutTrialRequest,
    units: Mapping[str, object],
    translation: Mapping[str, object],
) -> list[dict[str, object]]:
    index = annotation_index(units, translation)
    items = [placement.to_item() for placement in request.placements]
    kind_order = {"figure-table-reading": 0, "dark-orange-teaching": 1}
    items.sort(
        key=lambda item: (
            index.unit_order[str(item["unit_id"])],
            int(item["target_start"]),
            kind_order[str(item["kind"])],
            str(item["id"]),
        )
    )
    return items


def _trial_annotations(
    request: LayoutTrialRequest,
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    style: TypographyStyleContract,
    *,
    verify_frozen_evidence: FrozenEvidenceVerifier | None,
    adapter_limits: AnnotationAdapterLimits,
) -> tuple[dict[str, object], dict[str, object]]:
    mandatory_items = tuple(request.mandatory_items)
    mandatory_hash = sha256_canonical(
        {"items": [item.to_item() for item in mandatory_items]}
    )
    if request.mandatory_items_hash != mandatory_hash:
        raise AnnotationAdapterError("layout trial mandatory parent hash is invalid")
    orange_items = _orange_items(request, units, translation)
    orange_hash = sha256_canonical({"placements": orange_items})
    if request.phase == "final-frozen":
        if request.frozen_selection_hash != orange_hash:
            raise AnnotationAdapterError(
                "final layout trial selection hash is not frozen"
            )
    elif request.frozen_selection_hash is not None:
        raise AnnotationAdapterError(
            "non-final layout trial cannot carry a frozen selection hash"
        )
    auxiliary_size = style.style_for("auxiliary").size_mpt
    selection = OrangeSelectionResult(
        mandatory_items=mandatory_items,
        mandatory_items_hash=mandatory_hash,
        placements=tuple(request.placements),
        auxiliary_size_mpt=auxiliary_size,
        candidate_set_hash="0" * 64,
        selection_hash=orange_hash,
        continuation_pages=0,
    )
    artifact = selection.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    _enforce_annotation_adapter_limits(artifact, translation, adapter_limits)
    validate_annotations_against_inputs(
        units,
        translation,
        review,
        artifact,
        style,
        verify_frozen_evidence=verify_frozen_evidence,
    )
    _validate_annotation_span_boundaries(artifact, units, translation)
    normalized_request = {
        "request_contract_version": "1.0.0",
        "phase": request.phase,
        "allow_continuation": request.allow_continuation,
        "mandatory_items_hash": mandatory_hash,
        "mandatory_items": [item.to_item() for item in mandatory_items],
        "placements": orange_items,
        "frozen_selection_hash": request.frozen_selection_hash,
    }
    binding = {
        "kind": "trial",
        "parent_hash": sha256_canonical(normalized_request),
        "selection_hash": artifact["selection_hash"],
    }
    return artifact, binding


def make_annotation_layout_trial(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
    limits: LayoutLimits = DEFAULT_LAYOUT_LIMITS,
    adapter_limits: AnnotationAdapterLimits = DEFAULT_ANNOTATION_ADAPTER_LIMITS,
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> LayoutTrial:
    """Return the real two-stage feasibility callback used by annotation selection."""

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        if not isinstance(request, LayoutTrialRequest):
            raise AnnotationAdapterError("layout trial request type is invalid")
        try:
            artifact, binding = _trial_annotations(
                request,
                units,
                translation,
                review,
                style_contract,
                verify_frozen_evidence=verify_frozen_evidence,
                adapter_limits=adapter_limits,
            )
            graph = _build_annotated_frame_graph(
                source,
                units,
                translation,
                style_contract=style_contract,
                resolver=resolver,
                annotation_binding=binding,
                annotation_items=artifact["items"],  # type: ignore[arg-type]
                config=config,
            )
            layout = solve_layout(graph, limits=limits)
        except LayoutInfeasibleError:
            return LayoutTrialResult(False, 0)
        except (AnnotationValidationError, FrameGraphError) as exc:
            raise AnnotationAdapterError(
                "annotation layout trial inputs are invalid"
            ) from exc
        return LayoutTrialResult(
            True,
            int(layout["solver_trace"]["continuation_page_count"]),  # type: ignore[index]
        )

    return trial


def build_final_annotated_frame_graph(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    expected_candidate_set_hash: str,
    config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
    adapter_limits: AnnotationAdapterLimits = DEFAULT_ANNOTATION_ADAPTER_LIMITS,
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> dict[str, object]:
    """Build v2 geometry only after validating the final annotation parent."""

    if not isinstance(expected_candidate_set_hash, str):
        raise AnnotationAdapterError("expected candidate-set hash is required")
    try:
        _enforce_annotation_adapter_limits(
            annotations,
            translation,
            adapter_limits,
        )
        validate_annotations_against_inputs(
            units,
            translation,
            review,
            annotations,
            style_contract,
            verify_frozen_evidence=verify_frozen_evidence,
            expected_candidate_set_hash=expected_candidate_set_hash,
        )
        _validate_annotation_span_boundaries(annotations, units, translation)
        binding = {
            "kind": "final",
            "parent_hash": sha256_canonical(annotations),
            "selection_hash": annotations["selection_hash"],
        }
        return _build_annotated_frame_graph(
            source,
            units,
            translation,
            style_contract=style_contract,
            resolver=resolver,
            annotation_binding=binding,
            annotation_items=annotations["items"],  # type: ignore[arg-type]
            config=config,
        )
    except (AnnotationValidationError, FrameGraphError, KeyError) as exc:
        raise AnnotationAdapterError(
            "final annotations cannot produce a trusted frame graph"
        ) from exc


def validate_annotated_frame_graph_against_inputs(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    artifact: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    expected_candidate_set_hash: str,
    config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
    adapter_limits: AnnotationAdapterLimits = DEFAULT_ANNOTATION_ADAPTER_LIMITS,
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> None:
    """Reject every graph field not equal to exact final-parent recomputation."""

    expected = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style_contract,
        resolver=resolver,
        expected_candidate_set_hash=expected_candidate_set_hash,
        config=config,
        adapter_limits=adapter_limits,
        verify_frozen_evidence=verify_frozen_evidence,
    )
    if canonical_json_bytes(artifact) != canonical_json_bytes(expected):
        raise AnnotationAdapterError(
            "annotated frame graph differs from parent-recomputed content"
        )


__all__ = [
    "DEFAULT_ANNOTATION_ADAPTER_LIMITS",
    "AnnotationAdapterLimits",
    "AnnotationAdapterError",
    "build_final_annotated_frame_graph",
    "make_annotation_layout_trial",
    "validate_annotated_frame_graph_against_inputs",
]
