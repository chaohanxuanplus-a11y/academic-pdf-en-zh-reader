# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Single-chain annotation selection and deterministic final layout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from academic_pdf_en_zh_reader.annotations.figure_notes import FrozenEvidenceVerifier
from academic_pdf_en_zh_reader.annotations.selection import (
    FrozenOrangeCandidateSet,
    MandatoryAnnotation,
    OrangeSelectionResult,
    orange_selection_policy_payload,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.annotations.validation import (
    AnnotationValidationError,
    validate_annotations_against_inputs,
)
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.job.state import JobStage, JobState
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    DEFAULT_ANNOTATION_ADAPTER_LIMITS,
    AnnotationAdapterError,
    AnnotationAdapterLimits,
    build_final_annotated_frame_graph,
    make_annotation_layout_trial,
    validate_annotated_frame_graph_against_inputs,
)
from academic_pdf_en_zh_reader.layout.frame_graph import (
    DEFAULT_FRAME_GRAPH_CONFIG,
    FrameGraphConfig,
)
from academic_pdf_en_zh_reader.layout.solver import (
    DEFAULT_LAYOUT_LIMITS,
    LayoutLimits,
    LayoutSolverError,
    solve_layout,
    validate_layout_against_frame_graph,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    TypographyStyleContract,
)


class FinalizationError(ValueError):
    """Raised when the final annotation/layout chain cannot be trusted."""


@dataclass(frozen=True)
class FinalizationResult:
    """The one frozen selection and its exact final artifacts."""

    candidate_set: FrozenOrangeCandidateSet
    selection: OrangeSelectionResult
    annotations: dict[str, object]
    frame_graph: dict[str, object]
    layout: dict[str, object]
    receipt: dict[str, object]


def _style_contract_payload(style: TypographyStyleContract) -> dict[str, object]:
    if not isinstance(style, TypographyStyleContract):
        raise FinalizationError("FINALIZATION_POLICY_INVALID: style contract")
    return {
        "version": style.version,
        "body_source_size_mpt": style.body_source_size_mpt,
        "styles": [
            {
                "role": role,
                "font_role": role_style.font_role,
                "size_mpt": role_style.size_mpt,
                "line_height_mpt": role_style.line_height_mpt,
            }
            for role, role_style in style.styles
        ],
    }


def _font_fingerprint_payload(resolver: FontRunResolver) -> list[dict[str, str]]:
    if not isinstance(resolver, FontRunResolver):
        raise FinalizationError("FINALIZATION_POLICY_INVALID: font resolver")
    return [
        {"role": role, "reportlab_name": reportlab_name, "sha256": digest}
        for role, reportlab_name, digest in resolver.font_fingerprint
    ]


def _policy_hashes(
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    frame_graph_config: FrameGraphConfig,
    layout_limits: LayoutLimits,
    annotation_adapter_limits: AnnotationAdapterLimits,
) -> dict[str, str]:
    if not isinstance(frame_graph_config, FrameGraphConfig):
        raise FinalizationError("FINALIZATION_POLICY_INVALID: frame graph config")
    if not isinstance(layout_limits, LayoutLimits):
        raise FinalizationError("FINALIZATION_POLICY_INVALID: layout limits")
    if not isinstance(annotation_adapter_limits, AnnotationAdapterLimits):
        raise FinalizationError("FINALIZATION_POLICY_INVALID: adapter limits")
    return {
        "style-contract": sha256_canonical(_style_contract_payload(style_contract)),
        "font-fingerprint": sha256_canonical(_font_fingerprint_payload(resolver)),
        "frame-graph-config": sha256_canonical(asdict(frame_graph_config)),
        "layout-limits": sha256_canonical(asdict(layout_limits)),
        "annotation-adapter-limits": sha256_canonical(
            asdict(annotation_adapter_limits)
        ),
        "orange-selection-policy": sha256_canonical(orange_selection_policy_payload()),
    }


def _build_receipt(
    *,
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    candidate_set_hash: str,
    selection_hash: str,
    policy_hashes: Mapping[str, str],
    continuation_page_count: int,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "finalization-receipt",
        "artifact_hashes": {
            "source": sha256_canonical(source),
            "units": sha256_canonical(units),
            "translation": sha256_canonical(translation),
            "review": sha256_canonical(review),
            "annotations": sha256_canonical(annotations),
            "frame-graph": sha256_canonical(frame_graph),
            "layout": sha256_canonical(layout),
        },
        "policy_hashes": dict(policy_hashes),
        "candidate_set_hash": candidate_set_hash,
        "selection_hash": selection_hash,
        "solver_input_hash": layout["solver_input_hash"],
        "continuation_page_count": continuation_page_count,
    }
    return {**payload, "receipt_hash": sha256_canonical(payload)}


def validate_finalization_receipt_against_inputs(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    receipt: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    frame_graph_config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
    layout_limits: LayoutLimits = DEFAULT_LAYOUT_LIMITS,
    annotation_adapter_limits: AnnotationAdapterLimits = (
        DEFAULT_ANNOTATION_ADAPTER_LIMITS
    ),
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> None:
    """Recompute every receipt parent and policy binding from current inputs."""

    try:
        validate_artifact("finalization-receipt", receipt)
        expected_artifacts = {
            "source": sha256_canonical(source),
            "units": sha256_canonical(units),
            "translation": sha256_canonical(translation),
            "review": sha256_canonical(review),
            "annotations": sha256_canonical(annotations),
            "frame-graph": sha256_canonical(frame_graph),
            "layout": sha256_canonical(layout),
        }
        recorded_artifacts = receipt["artifact_hashes"]
        for name, expected_hash in expected_artifacts.items():
            if recorded_artifacts[name] != expected_hash:  # type: ignore[index]
                raise FinalizationError(f"FINALIZATION_PARENT_MISMATCH: {name}")

        expected_policies = _policy_hashes(
            style_contract=style_contract,
            resolver=resolver,
            frame_graph_config=frame_graph_config,
            layout_limits=layout_limits,
            annotation_adapter_limits=annotation_adapter_limits,
        )
        recorded_policies = receipt["policy_hashes"]
        for name, expected_hash in expected_policies.items():
            if recorded_policies[name] != expected_hash:  # type: ignore[index]
                raise FinalizationError(f"FINALIZATION_POLICY_MISMATCH: {name}")

        candidate_set_hash = annotations["candidate_set_hash"]
        if receipt["candidate_set_hash"] != candidate_set_hash:
            raise FinalizationError("FINALIZATION_PARENT_MISMATCH: candidate_set_hash")
        if receipt["selection_hash"] != annotations["orange_selection_hash"]:
            raise FinalizationError("FINALIZATION_PARENT_MISMATCH: selection_hash")
        if receipt["solver_input_hash"] != layout["solver_input_hash"]:
            raise FinalizationError("FINALIZATION_PARENT_MISMATCH: solver_input_hash")
        continuation_count = layout["solver_trace"]["continuation_page_count"]
        if receipt["continuation_page_count"] != continuation_count:
            raise FinalizationError(
                "FINALIZATION_PARENT_MISMATCH: continuation_page_count"
            )
        if layout["solver_policy"] != asdict(layout_limits):
            raise FinalizationError("FINALIZATION_POLICY_MISMATCH: layout-limits")

        validate_annotations_against_inputs(
            units,
            translation,
            review,
            annotations,
            style_contract,
            verify_frozen_evidence=verify_frozen_evidence,
            expected_candidate_set_hash=str(candidate_set_hash),
        )
        validate_annotated_frame_graph_against_inputs(
            source,
            units,
            translation,
            review,
            annotations,
            frame_graph,
            style_contract=style_contract,
            resolver=resolver,
            expected_candidate_set_hash=str(candidate_set_hash),
            config=frame_graph_config,
            adapter_limits=annotation_adapter_limits,
            verify_frozen_evidence=verify_frozen_evidence,
        )
        validate_layout_against_frame_graph(frame_graph, layout)
        expected_layout = solve_layout(frame_graph, limits=layout_limits)
        if canonical_json_bytes(layout) != canonical_json_bytes(expected_layout):
            raise FinalizationError(
                "FINALIZATION_PARENT_MISMATCH: layout recomputation"
            )
    except FinalizationError:
        raise
    except (
        AnnotationAdapterError,
        AnnotationValidationError,
        KeyError,
        LayoutSolverError,
        SchemaValidationError,
        TypeError,
    ) as exc:
        raise FinalizationError(
            "FINALIZATION_RECEIPT_INVALID: parent recomputation failed"
        ) from exc


def finalize_annotations_and_layout(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    *,
    candidate_set: FrozenOrangeCandidateSet,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    mandatory_items: Sequence[MandatoryAnnotation] = (),
    frame_graph_config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
    layout_limits: LayoutLimits = DEFAULT_LAYOUT_LIMITS,
    annotation_adapter_limits: AnnotationAdapterLimits = (
        DEFAULT_ANNOTATION_ADAPTER_LIMITS
    ),
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> FinalizationResult:
    """Run selection trials and the final solve under one immutable policy chain."""

    if not isinstance(candidate_set, FrozenOrangeCandidateSet):
        raise FinalizationError(
            "FINALIZATION_CANDIDATE_SET_INVALID: frozen candidate set required"
        )
    try:
        trial_layout = make_annotation_layout_trial(
            source,
            units,
            translation,
            review,
            style_contract=style_contract,
            resolver=resolver,
            config=frame_graph_config,
            limits=layout_limits,
            adapter_limits=annotation_adapter_limits,
            verify_frozen_evidence=verify_frozen_evidence,
        )
        selection = select_orange_annotations(
            units,
            translation,
            candidate_set=candidate_set,
            mandatory_items=mandatory_items,
            auxiliary_size_mpt=style_contract.style_for("auxiliary").size_mpt,
            trial_layout=trial_layout,
            verify_frozen_evidence=verify_frozen_evidence,
        )
        annotations = selection.to_artifact(
            units=units,
            translation=translation,
            review=review,
        )
        frame_graph = build_final_annotated_frame_graph(
            source,
            units,
            translation,
            review,
            annotations,
            style_contract=style_contract,
            resolver=resolver,
            expected_candidate_set_hash=candidate_set.candidate_set_hash,
            config=frame_graph_config,
            adapter_limits=annotation_adapter_limits,
            verify_frozen_evidence=verify_frozen_evidence,
        )
        layout = solve_layout(frame_graph, limits=layout_limits)
        continuation_count = int(layout["solver_trace"]["continuation_page_count"])
        if continuation_count != selection.continuation_pages:
            raise FinalizationError(
                "FINAL_CONTINUATION_COUNT_MISMATCH: selection and final solve differ"
            )
        policy_hashes = _policy_hashes(
            style_contract=style_contract,
            resolver=resolver,
            frame_graph_config=frame_graph_config,
            layout_limits=layout_limits,
            annotation_adapter_limits=annotation_adapter_limits,
        )
        receipt = _build_receipt(
            source=source,
            units=units,
            translation=translation,
            review=review,
            annotations=annotations,
            frame_graph=frame_graph,
            layout=layout,
            candidate_set_hash=candidate_set.candidate_set_hash,
            selection_hash=selection.selection_hash,
            policy_hashes=policy_hashes,
            continuation_page_count=continuation_count,
        )
        validate_finalization_receipt_against_inputs(
            source,
            units,
            translation,
            review,
            annotations,
            frame_graph,
            layout,
            receipt,
            style_contract=style_contract,
            resolver=resolver,
            frame_graph_config=frame_graph_config,
            layout_limits=layout_limits,
            annotation_adapter_limits=annotation_adapter_limits,
            verify_frozen_evidence=verify_frozen_evidence,
        )
    except FinalizationError:
        raise
    except (
        AnnotationAdapterError,
        AnnotationValidationError,
        KeyError,
        LayoutSolverError,
        SchemaValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise FinalizationError(
            "FINALIZATION_FAILED: trusted chain not produced"
        ) from exc
    return FinalizationResult(
        candidate_set=candidate_set,
        selection=selection,
        annotations=annotations,
        frame_graph=frame_graph,
        layout=layout,
        receipt=receipt,
    )


def finalize_resumed_annotations_and_layout(
    state: JobState,
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    *,
    candidate_set: FrozenOrangeCandidateSet,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    mandatory_items: Sequence[MandatoryAnnotation] = (),
    frame_graph_config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
    layout_limits: LayoutLimits = DEFAULT_LAYOUT_LIMITS,
    annotation_adapter_limits: AnnotationAdapterLimits = (
        DEFAULT_ANNOTATION_ADAPTER_LIMITS
    ),
    verify_frozen_evidence: FrozenEvidenceVerifier | None = None,
) -> FinalizationResult:
    """Rerun finalization without allowing a resumed ledger to change parents."""

    if not isinstance(state, JobState) or state.stage not in {
        JobStage.REVIEWED,
        JobStage.ANNOTATED,
    }:
        raise FinalizationError("RESUME_STAGE_INVALID: expected reviewed or annotated")
    try:
        validate_artifact("job-state", state.to_dict())
    except (SchemaValidationError, TypeError, ValueError) as exc:
        raise FinalizationError("RESUME_ARTIFACT_MISMATCH: job-state") from exc
    if source.get("source_sha256") != state.source_sha256:
        raise FinalizationError("RESUME_ARTIFACT_MISMATCH: source_sha256")
    expected_parents = {
        "source": sha256_canonical(source),
        "units": sha256_canonical(units),
        "translation": sha256_canonical(translation),
        "review": sha256_canonical(review),
    }
    ledger = state.artifact_hashes
    for name, expected_hash in expected_parents.items():
        if ledger.get(name) != expected_hash:
            raise FinalizationError(f"RESUME_ARTIFACT_MISMATCH: {name}")

    result = finalize_annotations_and_layout(
        source,
        units,
        translation,
        review,
        candidate_set=candidate_set,
        style_contract=style_contract,
        resolver=resolver,
        mandatory_items=mandatory_items,
        frame_graph_config=frame_graph_config,
        layout_limits=layout_limits,
        annotation_adapter_limits=annotation_adapter_limits,
        verify_frozen_evidence=verify_frozen_evidence,
    )
    if state.stage is JobStage.ANNOTATED and ledger.get(
        "annotations"
    ) != sha256_canonical(result.annotations):
        raise FinalizationError(
            "RESUME_ARTIFACT_MISMATCH: annotations require a new job/revision"
        )
    return result


def resume_must_run_finalizer(stage: JobStage) -> bool:
    """Tell orchestration whether old annotations/layout must not be reused."""

    if not isinstance(stage, JobStage):
        raise FinalizationError("FINALIZATION_RESUME_STAGE_INVALID")
    return list(JobStage).index(stage) <= list(JobStage).index(JobStage.ANNOTATED)


__all__ = [
    "FinalizationError",
    "FinalizationResult",
    "finalize_annotations_and_layout",
    "finalize_resumed_annotations_and_layout",
    "resume_must_run_finalizer",
    "validate_finalization_receipt_against_inputs",
]
