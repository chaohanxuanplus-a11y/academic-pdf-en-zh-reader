# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Finish one extracted job through reviewed, validated, atomic PDF delivery."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from academic_pdf_en_zh_reader.annotations.input_manifest import (
    SemanticCandidateManifestError,
    adapt_semantic_candidate_manifest,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    orange_selection_policy_payload,
)
from academic_pdf_en_zh_reader.cli import CliStageError, validate_translation_stage
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import (
    MAX_RETENTION_SECONDS,
    cleanup_after_job,
    resolve_managed_job,
)
from academic_pdf_en_zh_reader.job.deliver import deliver_validated_pdf
from academic_pdf_en_zh_reader.job.finalize import (
    _font_fingerprint_payload,
    _style_contract_payload,
    finalize_annotations_and_layout,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    JobState,
    advance_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    write_immutable_artifact,
    write_job_state,
)
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    DEFAULT_ANNOTATION_ADAPTER_LIMITS,
)
from academic_pdf_en_zh_reader.layout.frame_graph import DEFAULT_FRAME_GRAPH_CONFIG
from academic_pdf_en_zh_reader.layout.solver import DEFAULT_LAYOUT_LIMITS
from academic_pdf_en_zh_reader.normalization.api import NORMALIZED_PDF_NAME
from academic_pdf_en_zh_reader.qa.persist import QaCommitError
from academic_pdf_en_zh_reader.qa.worker_bridge import validate_qa_in_worker
from academic_pdf_en_zh_reader.rendering.compose import CompositionError
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from academic_pdf_en_zh_reader.rendering.worker_bridge import (
    render_bilingual_pdf_in_worker,
)
from academic_pdf_en_zh_reader.review.semantic_checks import (
    check_mechanical_semantics,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    TranslationValidationError,
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    TypographyStyleContract,
    build_style_contract,
)

MAX_AGENT_JSON_BYTES = 128 * 1024 * 1024
_MAX_INTERNAL_JSON_BYTES = 256 * 1024 * 1024
_STABLE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class FinishJobError(RuntimeError):
    """Stable, content-free reason that production finishing stopped."""

    def __init__(self, code: str, stage: str) -> None:
        self.code = code
        self.stage = stage
        super().__init__(f"{stage}:{code}")


def _is_reparse_or_symlink(path: Path, information: os.stat_result) -> bool:
    if stat.S_ISLNK(information.st_mode):
        return True
    attributes = getattr(information, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        os.path.samestat(left, right)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _read_stable_regular_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    unsafe_code: str,
    too_large_code: str,
    stage: str,
) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        if _is_reparse_or_symlink(path, before) or not stat.S_ISREG(before.st_mode):
            raise FinishJobError(unsafe_code, stage)
        if before.st_size > maximum_bytes:
            raise FinishJobError(too_large_code, stage)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same_file_snapshot(
                before, opened
            ):
                raise FinishJobError(unsafe_code, stage)
            raw = stream.read(maximum_bytes + 1)
        after = path.lstat()
        if _is_reparse_or_symlink(path, after) or not _same_file_snapshot(
            opened, after
        ):
            raise FinishJobError(unsafe_code, stage)
    except FinishJobError:
        raise
    except OSError as exc:
        raise FinishJobError(unsafe_code, stage) from exc
    if len(raw) > maximum_bytes:
        raise FinishJobError(too_large_code, stage)
    if not raw:
        raise FinishJobError(unsafe_code, stage)
    return raw, opened


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _load_external_agent_json(
    path: str | Path,
    *,
    job_root: Path,
    stage: str,
) -> dict[str, object]:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise FinishJobError("AGENT_INPUT_UNSAFE", stage) from exc
    if resolved.is_relative_to(job_root):
        raise FinishJobError("AGENT_INPUT_UNSAFE", stage)
    raw, opened = _read_stable_regular_bytes(
        candidate,
        maximum_bytes=MAX_AGENT_JSON_BYTES,
        unsafe_code="AGENT_INPUT_UNSAFE",
        too_large_code="AGENT_INPUT_TOO_LARGE",
        stage=stage,
    )
    try:
        for child in job_root.iterdir():
            child_info = child.lstat()
            if stat.S_ISREG(child_info.st_mode) and os.path.samestat(
                opened, child_info
            ):
                raise FinishJobError("AGENT_INPUT_UNSAFE", stage)
    except FinishJobError:
        raise
    except OSError as exc:
        raise FinishJobError("AGENT_INPUT_UNSAFE", stage) from exc
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FinishJobError("AGENT_INPUT_INVALID", stage) from exc
    if not isinstance(value, dict):
        raise FinishJobError("AGENT_INPUT_INVALID", stage)
    return value


def _decode_canonical_internal(
    path: Path,
    *,
    schema_name: str,
    mismatch_code: str,
) -> tuple[dict[str, object], str]:
    raw, _information = _read_stable_regular_bytes(
        path,
        maximum_bytes=_MAX_INTERNAL_JSON_BYTES,
        unsafe_code=mismatch_code,
        too_large_code=mismatch_code,
        stage="load",
    )
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(value, dict):
            raise ValueError
        validate_artifact(schema_name, value)
        if canonical_json_bytes(value) != raw:
            raise ValueError
    except (
        UnicodeError,
        json.JSONDecodeError,
        SchemaValidationError,
        ValueError,
    ) as exc:
        raise FinishJobError(mismatch_code, "load") from exc
    return value, sha256_bytes(raw)


def _load_job_state(path: Path) -> JobState:
    value, _digest = _decode_canonical_internal(
        path,
        schema_name="job-state",
        mismatch_code="JOB_STATE_INVALID",
    )
    try:
        return JobState.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise FinishJobError("JOB_STATE_INVALID", "load") from exc


def _bound_artifact(
    job_root: Path,
    state: JobState,
    *,
    artifact_name: str,
    schema_name: str,
    mismatch_code: str,
) -> dict[str, object]:
    value, digest = _decode_canonical_internal(
        job_root / f"{artifact_name}.json",
        schema_name=schema_name,
        mismatch_code=mismatch_code,
    )
    if state.artifact_hashes.get(artifact_name) != digest:
        raise FinishJobError(mismatch_code, "load")
    return value


def _style_contract_from_source(
    source: Mapping[str, object],
) -> TypographyStyleContract:
    pages = source.get("pages")
    if not isinstance(pages, list):
        raise FinishJobError("TYPOGRAPHY_EVIDENCE_INVALID", "typography")
    required_blocks: list[Mapping[str, object]] = []
    for page in pages:
        if not isinstance(page, Mapping) or not isinstance(page.get("blocks"), list):
            raise FinishJobError("TYPOGRAPHY_EVIDENCE_INVALID", "typography")
        required_blocks.extend(
            block
            for block in page["blocks"]
            if isinstance(block, Mapping)
            and block.get("translation_policy") == "required"
        )
    selected = [block for block in required_blocks if block.get("role") == "body"]
    if not selected:
        selected = [
            block for block in required_blocks if block.get("role") == "abstract"
        ]
    if not selected:
        raise FinishJobError("TYPOGRAPHY_EVIDENCE_MISSING", "typography")

    samples: list[FontSizeSample] = []
    for block in selected:
        size = block.get("source_font_size_mpt")
        text = block.get("text")
        weight = (
            sum(not character.isspace() for character in text)
            if isinstance(text, str)
            else 0
        )
        if type(size) is not int or size <= 0 or weight <= 0:
            raise FinishJobError("TYPOGRAPHY_EVIDENCE_INVALID", "typography")
        samples.append(FontSizeSample(size_mpt=size, character_count=weight))
    try:
        return build_style_contract(tuple(samples))
    except ValueError as exc:
        raise FinishJobError("TYPOGRAPHY_EVIDENCE_INVALID", "typography") from exc


def _commit_stage(
    state: JobState,
    target_stage: JobStage,
    artifacts: dict[str, str],
    state_path: Path,
) -> JobState:
    previous_hash = state_hash(state)
    try:
        advanced = advance_job(
            state,
            target_stage,
            artifacts,
            expected_previous_state_hash=previous_hash,
        )
        write_job_state(
            state_path,
            advanced,
            expected_previous_state_hash=previous_hash,
        )
    except Exception as exc:
        raise FinishJobError("STATE_CAS_FAILED", target_stage.value) from exc
    return advanced


def _write_artifact(
    job_root: Path,
    filename: str,
    value: dict[str, object],
    schema_name: str,
    *,
    stage: str,
) -> str:
    try:
        return write_immutable_artifact(
            job_root / filename,
            value,
            schema_name,
        )
    except Exception as exc:
        raise FinishJobError("ARTIFACT_COMMIT_FAILED", stage) from exc


def _policy_inputs(
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
) -> dict[str, object]:
    return {
        "style-contract": _style_contract_payload(style_contract),
        "font-fingerprint": _font_fingerprint_payload(resolver),
        "frame-graph-config": asdict(DEFAULT_FRAME_GRAPH_CONFIG),
        "layout-limits": asdict(DEFAULT_LAYOUT_LIMITS),
        "annotation-adapter-limits": asdict(DEFAULT_ANNOTATION_ADAPTER_LIMITS),
        "orange-selection-policy": orange_selection_policy_payload(),
    }


def _cleanup_before_delivery(
    *,
    managed_root: Path,
    job_root: Path,
    outcome: str,
    retain_debug: bool,
    ttl_seconds: int | None,
    original: BaseException,
) -> None:
    try:
        cleanup = cleanup_after_job(
            managed_root,
            job_root,
            outcome=outcome,  # type: ignore[arg-type]
            retention_mode="debug" if retain_debug else None,
            ttl_seconds=ttl_seconds if retain_debug else None,
        )
    except Exception:
        raise FinishJobError("CLEANUP_FAILED", "cleanup") from original
    if cleanup.code not in {"CLEANUP_OK", "CLEANUP_RETAINED"}:
        raise FinishJobError("CLEANUP_FAILED", "cleanup") from original


def _validate_retention(retain_debug: bool, ttl_seconds: int | None) -> None:
    if type(retain_debug) is not bool:
        raise FinishJobError("RETENTION_POLICY_INVALID", "scope")
    if not retain_debug:
        if ttl_seconds is not None:
            raise FinishJobError("RETENTION_POLICY_INVALID", "scope")
        return
    if ttl_seconds is not None and (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= MAX_RETENTION_SECONDS
    ):
        raise FinishJobError("RETENTION_POLICY_INVALID", "scope")


def finish_managed_job(
    *,
    managed_root: str | Path,
    job_id: str,
    source_pdf: str | Path,
    translation_json: str | Path,
    review_json: str | Path,
    semantic_candidates_json: str | Path,
    output_pdf: str | Path,
    retain_debug: bool = False,
    ttl_seconds: int | None = None,
) -> dict[str, object]:
    """Finish exactly one EXTRACTED job, or clean it without publishing a PDF."""

    _validate_retention(retain_debug, ttl_seconds)
    managed = Path(managed_root)
    requested_job = managed / job_id
    job_root: Path | None = None
    delivery_handled_cleanup = False
    stage = "scope"
    try:
        try:
            managed, job_root, managed_job_id = resolve_managed_job(
                managed, requested_job
            )
        except ValueError as exc:
            raise FinishJobError("JOB_SCOPE_INVALID", stage) from exc
        if managed_job_id != job_id:
            raise FinishJobError("JOB_SCOPE_INVALID", stage)

        stage = "load"
        state_path = job_root / "job-state.json"
        state = _load_job_state(state_path)
        if state.job_id != job_id or state.stage is not JobStage.EXTRACTED:
            raise FinishJobError("JOB_NOT_EXTRACTED", stage)
        preflight = _bound_artifact(
            job_root,
            state,
            artifact_name="preflight",
            schema_name="preflight",
            mismatch_code="PREFLIGHT_ARTIFACT_MISMATCH",
        )
        normalization = _bound_artifact(
            job_root,
            state,
            artifact_name="normalization",
            schema_name="normalization",
            mismatch_code="NORMALIZATION_ARTIFACT_MISMATCH",
        )
        source = _bound_artifact(
            job_root,
            state,
            artifact_name="source",
            schema_name="source",
            mismatch_code="SOURCE_ARTIFACT_MISMATCH",
        )
        units = _bound_artifact(
            job_root,
            state,
            artifact_name="units",
            schema_name="units",
            mismatch_code="UNITS_ARTIFACT_MISMATCH",
        )
        raw_source_bytes, _raw_source_information = _read_stable_regular_bytes(
            Path(source_pdf),
            maximum_bytes=DEFAULT_LIMITS.max_input_bytes,
            unsafe_code="SOURCE_PDF_UNSAFE",
            too_large_code="SOURCE_PDF_TOO_LARGE",
            stage=stage,
        )
        normalized_source_path = job_root / NORMALIZED_PDF_NAME
        normalized_source_bytes, _normalized_source_information = (
            _read_stable_regular_bytes(
                normalized_source_path,
                maximum_bytes=DEFAULT_LIMITS.max_normalized_pdf_bytes,
                unsafe_code="NORMALIZED_SOURCE_UNSAFE",
                too_large_code="NORMALIZED_SOURCE_TOO_LARGE",
                stage=stage,
            )
        )
        raw_source_sha256 = sha256_bytes(raw_source_bytes)
        normalized_source_sha256 = sha256_bytes(normalized_source_bytes)
        if (
            raw_source_sha256 != state.source_sha256
            or preflight.get("source_sha256") != state.source_sha256
            or normalization.get("source_sha256") != state.source_sha256
            or source.get("source_sha256") != state.source_sha256
            or units.get("source_sha256") != state.source_sha256
            or normalization.get("preflight_sha256")
            != state.artifact_hashes.get("preflight")
            or normalization.get("normalized_pdf_sha256") != normalized_source_sha256
            or normalization.get("normalized_pdf_bytes") != len(normalized_source_bytes)
            or state.artifact_hashes.get("normalized-pdf") != normalized_source_sha256
            or source.get("normalized_pdf_sha256") != normalized_source_sha256
            or units.get("normalized_pdf_sha256") != normalized_source_sha256
        ):
            raise FinishJobError("JOB_PARENT_MISMATCH", stage)

        stage = "typography"
        style_contract = _style_contract_from_source(source)

        stage = "translation"
        translation = _load_external_agent_json(
            translation_json,
            job_root=job_root,
            stage=stage,
        )
        try:
            validate_translation_artifact(units, translation)
            if translation.get("translation_revision") != state.translation_revision:
                raise TranslationValidationError("translation revision mismatch")
            if check_mechanical_semantics(units, translation):
                raise FinishJobError("MECHANICAL_SEMANTIC_MISMATCH", stage)
        except FinishJobError:
            raise
        except (TranslationValidationError, SchemaValidationError, TypeError) as exc:
            raise FinishJobError("TRANSLATION_INVALID", stage) from exc
        translation_hash = _write_artifact(
            job_root,
            "translation.json",
            translation,
            "translation",
            stage=stage,
        )
        state = _commit_stage(
            state,
            JobStage.TRANSLATED,
            {"translation": translation_hash},
            state_path,
        )

        stage = "review"
        review = _load_external_agent_json(
            review_json,
            job_root=job_root,
            stage=stage,
        )
        try:
            validate_translation_stage(state, units, translation, review)
        except CliStageError as exc:
            raise FinishJobError(exc.code, stage) from exc
        review_hash = _write_artifact(
            job_root,
            "review.json",
            review,
            "review",
            stage=stage,
        )
        state = _commit_stage(
            state,
            JobStage.INDEPENDENTLY_REVIEWED,
            {"review": review_hash},
            state_path,
        )

        stage = "semantic-candidates"
        semantic_candidates = _load_external_agent_json(
            semantic_candidates_json,
            job_root=job_root,
            stage=stage,
        )
        try:
            adapted = adapt_semantic_candidate_manifest(
                units,
                translation,
                review,
                semantic_candidates,
                style_contract=style_contract,
            )
        except SemanticCandidateManifestError as exc:
            raise FinishJobError(exc.code, stage) from exc
        _write_artifact(
            job_root,
            "semantic-candidates.json",
            semantic_candidates,
            "semantic-candidates",
            stage=stage,
        )

        stage = "typography"
        try:
            resolver = FontRunResolver(load_font_registry())
        except Exception as exc:
            raise FinishJobError("APPROVED_FONT_CHAIN_INVALID", stage) from exc
        policy_inputs = _policy_inputs(style_contract, resolver)

        stage = "finalize"
        try:
            finalized = finalize_annotations_and_layout(
                source,
                units,
                translation,
                review,
                candidate_set=adapted.candidate_set,
                mandatory_items=adapted.mandatory_items,
                style_contract=style_contract,
                resolver=resolver,
            )
        except Exception as exc:
            raise FinishJobError("FINALIZATION_FAILED", stage) from exc
        annotations_hash = _write_artifact(
            job_root,
            "annotations.json",
            finalized.annotations,
            "annotations",
            stage=stage,
        )
        state = _commit_stage(
            state,
            JobStage.ANNOTATED,
            {"annotations": annotations_hash},
            state_path,
        )
        frame_graph_hash = _write_artifact(
            job_root,
            "frame-graph.json",
            finalized.frame_graph,
            "frame-graph",
            stage=stage,
        )
        layout_hash = _write_artifact(
            job_root,
            "layout.json",
            finalized.layout,
            "layout",
            stage=stage,
        )
        receipt_hash = _write_artifact(
            job_root,
            "finalization-receipt.json",
            finalized.receipt,
            "finalization-receipt",
            stage=stage,
        )
        state = _commit_stage(
            state,
            JobStage.LAID_OUT,
            {
                "frame-graph": frame_graph_hash,
                "layout": layout_hash,
                "finalization-receipt": receipt_hash,
            },
            state_path,
        )

        stage = "overlay"
        try:
            overlay_plan = build_overlay_plan(
                source,
                finalized.frame_graph,
                finalized.layout,
                finalized.annotations,
            )
            disclaimer_page_appended = (
                overlay_plan["branding"]["appended_page_number"] is not None
            )
        except Exception as exc:
            raise FinishJobError("OVERLAY_PLAN_FAILED", stage) from exc

        stage = "render"
        candidate_path = job_root / "candidate.pdf"
        render_manifest_path = job_root / "render-manifest.json"
        try:
            composition = render_bilingual_pdf_in_worker(
                source_pdf_path=normalized_source_path,
                source=source,
                units=units,
                translation=translation,
                review=review,
                annotations=finalized.annotations,
                frame_graph=finalized.frame_graph,
                layout=finalized.layout,
                finalization_receipt=finalized.receipt,
                policy_inputs=policy_inputs,
                overlay_plan=overlay_plan,
                expected_finalization_receipt_hash=receipt_hash,
                expected_overlay_plan_hash=str(overlay_plan["overlay_plan_hash"]),
                job_root=job_root,
                output_pdf_path=candidate_path,
                render_manifest_path=render_manifest_path,
            )
        except Exception as exc:
            candidate_code = getattr(exc, "code", None)
            code = (
                candidate_code
                if isinstance(exc, CompositionError)
                and isinstance(candidate_code, str)
                and _STABLE_ERROR_CODE.fullmatch(candidate_code)
                else "RENDER_FAILED"
            )
            raise FinishJobError(code, stage) from exc
        state = _commit_stage(
            state,
            JobStage.RENDERED,
            {
                "render-manifest": composition.render_manifest_hash,
                "pdf": composition.output_pdf_sha256,
            },
            state_path,
        )
        rendered_state_hash = state_hash(state)
        render_manifest = _bound_artifact(
            job_root,
            state,
            artifact_name="render-manifest",
            schema_name="render-manifest",
            mismatch_code="RENDER_MANIFEST_MISMATCH",
        )

        stage = "qa"
        try:
            qa = validate_qa_in_worker(
                job_root=job_root,
                expected_rendered_state_hash=rendered_state_hash,
                source_pdf_path=normalized_source_path,
                output_pdf_path=candidate_path,
                source=source,
                units=units,
                translation=translation,
                review=review,
                annotations=finalized.annotations,
                frame_graph=finalized.frame_graph,
                layout=finalized.layout,
                finalization_receipt=finalized.receipt,
                policy_inputs=policy_inputs,
                overlay_plan=overlay_plan,
                render_manifest=render_manifest,
                expected_render_manifest_hash=composition.render_manifest_hash,
            )
        except Exception as exc:
            candidate_code = getattr(exc, "code", None)
            code = (
                candidate_code
                if isinstance(exc, QaCommitError)
                and isinstance(candidate_code, str)
                and _STABLE_ERROR_CODE.fullmatch(candidate_code)
                else "QA_COMMIT_FAILED"
            )
            raise FinishJobError(code, stage) from exc
        if not qa.passed or qa.code not in {"QA_VALIDATED", "QA_ALREADY_VALIDATED"}:
            raise FinishJobError("QA_FAILED", stage)
        validated_state = _load_job_state(state_path)
        if (
            validated_state.stage is not JobStage.VALIDATED
            or qa.validated_state_hash != state_hash(validated_state)
        ):
            raise FinishJobError("QA_COMMIT_FAILED", stage)

        stage = "delivery"
        delivery = deliver_validated_pdf(
            managed_root=managed,
            job_root=job_root,
            state_path=state_path,
            source_path=normalized_source_path,
            candidate_path=candidate_path,
            render_manifest_path=render_manifest_path,
            qa_path=job_root / "qa.json",
            output_path=output_pdf,
            retention_mode="debug" if retain_debug else None,
            ttl_seconds=ttl_seconds if retain_debug else None,
        )
        delivery_handled_cleanup = True
        if (
            delivery.status != "ok"
            or delivery.code != "DELIVERY_OK"
            or not delivery.delivered
        ):
            raise FinishJobError(delivery.code, stage)
        result: dict[str, object] = {"status": "ok", "code": "FINISH_OK"}
        if disclaimer_page_appended:
            result["notices"] = ["DISCLAIMER_PAGE_APPENDED"]
        return result
    except KeyboardInterrupt as exc:
        if job_root is not None and not delivery_handled_cleanup:
            _cleanup_before_delivery(
                managed_root=managed,
                job_root=job_root,
                outcome="cancel",
                retain_debug=retain_debug,
                ttl_seconds=ttl_seconds,
                original=exc,
            )
        raise FinishJobError("FINISH_CANCELLED", stage) from exc
    except FinishJobError as exc:
        if job_root is not None and not delivery_handled_cleanup:
            _cleanup_before_delivery(
                managed_root=managed,
                job_root=job_root,
                outcome="failure",
                retain_debug=retain_debug,
                ttl_seconds=ttl_seconds,
                original=exc,
            )
        raise
    except Exception as exc:
        if job_root is not None and not delivery_handled_cleanup:
            _cleanup_before_delivery(
                managed_root=managed,
                job_root=job_root,
                outcome="failure",
                retain_debug=retain_debug,
                ttl_seconds=ttl_seconds,
                original=exc,
            )
        raise FinishJobError("FINISH_STAGE_FAILED", stage) from exc


__all__ = ["FinishJobError", "finish_managed_job"]
