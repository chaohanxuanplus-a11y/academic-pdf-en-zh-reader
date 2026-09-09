# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Parent bridge for mechanical QA in the Windows LPAC worker."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.qa.persist import (
    QaCommitError,
    QaCommitResult,
    persist_qa_result,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.security.input_copy import (
    SafeInputCopy,
    UnsafeInputError,
    copy_untrusted_input,
    read_bounded_regular_file,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    QA_CANDIDATE_PATH,
    QA_HANDOFF_PATH,
    QA_POLICY_VERSION,
    WorkerRequest,
)

_ALLOWED_WORKER_ERROR_CODES = frozenset(
    {
        "QA_FAILED",
        "SANDBOX_CONTRACT_UNVERIFIED",
        "WORKER_LIMIT_EXCEEDED",
    }
)


def _run_platform_worker(
    request: WorkerRequest,
    workspace: Path,
    *,
    limits: WorkerLimits,
):
    if os.name != "nt":
        raise QaCommitError("SANDBOX_UNAVAILABLE")
    from academic_pdf_en_zh_reader.security.windows_worker import (
        SandboxCleanupError,
        SandboxUnavailableError,
        WorkerCpuLimitError,
        WorkerExecutionError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerReportedError,
        WorkerTimeoutError,
        run_worker,
    )

    try:
        return run_worker(request, workspace, limits=limits)
    except WorkerReportedError as error:
        code = (
            error.code
            if error.code in _ALLOWED_WORKER_ERROR_CODES
            else "QA_WORKER_FAILED"
        )
        raise QaCommitError(code) from error
    except SandboxCleanupError as error:
        raise QaCommitError("SANDBOX_CONTRACT_UNVERIFIED") from error
    except SandboxUnavailableError as error:
        raise QaCommitError("SANDBOX_UNAVAILABLE") from error
    except (
        WorkerCpuLimitError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerTimeoutError,
    ) as error:
        raise QaCommitError("WORKER_LIMIT_EXCEEDED") from error
    except WorkerExecutionError as error:
        raise QaCommitError("QA_WORKER_FAILED") from error


def _stage_bytes(path: Path, payload: bytes, *, maximum: int, code: str) -> None:
    if not payload or len(payload) > maximum:
        raise QaCommitError(code)
    try:
        with path.open("x+b") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            information = os.fstat(stream.fileno())
            stream.seek(0)
            persisted = stream.read(maximum + 1)
            final_information = os.fstat(stream.fileno())
    except OSError as error:
        raise QaCommitError(code) from error
    if (
        not stat.S_ISREG(information.st_mode)
        or stat.S_ISLNK(information.st_mode)
        or bool(
            getattr(information, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        or final_information.st_size != information.st_size
        or information.st_size != len(payload)
        or persisted != payload
    ):
        raise QaCommitError(code)


def _decode_qa(encoded: object, limits: WorkerLimits) -> dict[str, object]:
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > limits.max_result_object_bytes
    ):
        raise QaCommitError("QA_ARTIFACT_INVALID")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        decoded = json.loads(
            encoded.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise QaCommitError("QA_ARTIFACT_INVALID") from error
    if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != encoded:
        raise QaCommitError("QA_ARTIFACT_INVALID")
    try:
        validate_artifact("qa", decoded)
    except Exception as error:
        raise QaCommitError("QA_ARTIFACT_INVALID") from error
    return decoded


def validate_qa_in_worker(
    *,
    job_root: str | Path,
    expected_rendered_state_hash: str,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    finalization_receipt: Mapping[str, object],
    policy_inputs: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    render_manifest: Mapping[str, object],
    expected_render_manifest_hash: str,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> QaCommitResult:
    """Run mechanical PDF QA in LPAC, then commit only validated JSON in parent."""

    payload = {
        "schema_version": "1.0.0",
        "operation": "qa",
        "source": dict(source),
        "units": dict(units),
        "translation": dict(translation),
        "review": dict(review),
        "annotations": dict(annotations),
        "frame_graph": dict(frame_graph),
        "layout": dict(layout),
        "finalization_receipt": dict(finalization_receipt),
        "policy_inputs": dict(policy_inputs),
        "overlay_plan": dict(overlay_plan),
        "render_manifest": dict(render_manifest),
        "expected_render_manifest_hash": expected_render_manifest_hash,
    }
    encoded = canonical_json_bytes(payload)
    safe_copy: SafeInputCopy | None = None
    result = None
    try:
        input_limits = replace(
            limits,
            max_input_bytes=limits.max_normalized_pdf_bytes,
        )
        safe_copy = copy_untrusted_input(Path(source_pdf_path), limits=input_limits)
        if safe_copy.sha256 != source.get("normalized_pdf_sha256"):
            raise QaCommitError("QA_SOURCE_IDENTITY_INVALID")
        candidate = read_bounded_regular_file(
            Path(output_pdf_path),
            max_bytes=limits.max_output_pdf_bytes,
        )
        _stage_bytes(
            safe_copy.root / QA_CANDIDATE_PATH,
            candidate.data,
            maximum=limits.max_output_pdf_bytes,
            code="QA_CANDIDATE_INVALID",
        )
        _stage_bytes(
            safe_copy.root / QA_HANDOFF_PATH,
            encoded,
            maximum=limits.max_extraction_artifact_bytes,
            code="QA_HANDOFF_INVALID",
        )
        request = WorkerRequest(
            operation="qa",
            input_path="input.pdf",
            parameters={
                "policy_version": QA_POLICY_VERSION,
                "input_bytes": safe_copy.size,
                "normalized_pdf_sha256": safe_copy.sha256,
                "candidate_bytes": candidate.size,
                "candidate_pdf_sha256": candidate.sha256,
                "handoff_bytes": len(encoded),
                "handoff_sha256": hashlib.sha256(encoded).hexdigest(),
            },
        )
        result = _run_platform_worker(request, safe_copy.root, limits=limits)
        provenance = getattr(result, "provenance", None)
        if (
            not isinstance(provenance, dict)
            or provenance.get("appcontainer_cleanup_verified") is not True
        ):
            raise QaCommitError("SANDBOX_CONTRACT_UNVERIFIED")
    except (OSError, UnsafeInputError) as error:
        raise QaCommitError("QA_INPUT_INVALID") from error
    finally:
        if safe_copy is not None:
            try:
                safe_copy.cleanup()
                try:
                    safe_copy.root.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise OSError("QA staging root still exists")
            except OSError as cleanup_error:
                raise QaCommitError("SANDBOX_CONTRACT_UNVERIFIED") from cleanup_error
    if result is None:
        raise QaCommitError("QA_WORKER_FAILED")
    qa = _decode_qa(result.qa_artifact_bytes, limits)
    return persist_qa_result(
        job_root=job_root,
        expected_rendered_state_hash=expected_rendered_state_hash,
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_pdf_path,
        source=source,
        render_manifest=render_manifest,
        expected_render_manifest_hash=expected_render_manifest_hash,
        expected_finalization_receipt_hash=sha256_canonical(finalization_receipt),
        qa=qa,
        limits=limits,
    )


__all__ = ["validate_qa_in_worker"]
