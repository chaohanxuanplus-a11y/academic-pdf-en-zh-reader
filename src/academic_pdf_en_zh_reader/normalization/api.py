# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Parent-side bridge for isolated, deterministic A4 source normalization."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from academic_pdf_en_zh_reader.extraction.api import (
    _read_valid_preflight,
    _write_preflight_handoff,
)
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    write_immutable_artifact,
    write_immutable_bytes,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.security.input_copy import (
    SafeInputCopy,
    UnsafeInputError,
    copy_untrusted_input,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    NORMALIZATION_POLICY_VERSION,
    ProtocolError,
    WorkerRequest,
)

NORMALIZED_PDF_NAME = "normalized-source.pdf"
NORMALIZATION_ARTIFACT_NAME = "normalization.json"

_ERROR_MESSAGES = {
    "INPUT_REJECTED": "The input file was rejected.",
    "PREFLIGHT_ARTIFACT_INVALID": "The preflight artifact was invalid.",
    "SANDBOX_UNAVAILABLE": "The required isolated PDF worker is unavailable.",
    "SANDBOX_CONTRACT_UNVERIFIED": (
        "The isolated worker contract could not be verified."
    ),
    "WORKER_LIMIT_EXCEEDED": "The isolated PDF worker exceeded a resource limit.",
    "WORKER_FAILED": "The isolated PDF worker failed.",
    "NORMALIZATION_ARTIFACT_INVALID": "The normalization result was invalid.",
    "ARTIFACT_EXISTS": "An immutable normalization artifact already exists.",
}


class _StableBridgeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error(code: str) -> dict[str, object]:
    return {
        "status": "error",
        "error": {"code": code, "message": _ERROR_MESSAGES[code]},
    }


def _run_platform_worker(
    request: WorkerRequest,
    workspace: Path,
    *,
    limits: WorkerLimits,
):
    if os.name != "nt":
        raise _StableBridgeError("SANDBOX_UNAVAILABLE")
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
        if error.code == "SANDBOX_CONTRACT_UNVERIFIED":
            raise _StableBridgeError(error.code) from error
        if error.code == "WORKER_LIMIT_EXCEEDED":
            raise _StableBridgeError(error.code) from error
        raise _StableBridgeError("WORKER_FAILED") from error
    except SandboxCleanupError as error:
        raise _StableBridgeError("SANDBOX_CONTRACT_UNVERIFIED") from error
    except SandboxUnavailableError as error:
        raise _StableBridgeError("SANDBOX_UNAVAILABLE") from error
    except (
        WorkerCpuLimitError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerTimeoutError,
    ) as error:
        raise _StableBridgeError("WORKER_LIMIT_EXCEEDED") from error
    except WorkerExecutionError as error:
        raise _StableBridgeError("WORKER_FAILED") from error


def _decode_json(encoded: bytes) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    value = json.loads(
        encoded.decode("utf-8", errors="strict"),
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("artifact root must be an object")
    return value


def _decode_normalization_artifact(
    encoded: object,
    normalized_pdf: object,
    *,
    preflight: Mapping[str, object],
    preflight_sha256: str,
    limits: WorkerLimits,
) -> dict[str, Any]:
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > limits.max_result_object_bytes
        or not isinstance(normalized_pdf, bytes)
        or not normalized_pdf
        or len(normalized_pdf) > limits.max_normalized_pdf_bytes
        or not normalized_pdf.startswith(b"%PDF-")
    ):
        raise ValueError("normalization bytes are absent or oversized")
    artifact = _decode_json(encoded)
    validate_artifact("normalization", artifact)
    if canonical_json_bytes(artifact) != encoded:
        raise ValueError("normalization artifact is not canonical JSON")
    pages = artifact.get("pages")
    preflight_pages = preflight.get("pages")
    if (
        artifact.get("source_sha256") != preflight.get("source_sha256")
        or artifact.get("preflight_sha256") != preflight_sha256
        or artifact.get("normalized_pdf_sha256") != sha256_bytes(normalized_pdf)
        or artifact.get("normalized_pdf_bytes") != len(normalized_pdf)
        or not isinstance(pages, list)
        or not isinstance(preflight_pages, list)
        or len(pages) != len(preflight_pages)
    ):
        raise ValueError("normalization identity or page count differs")
    for number, (page, raw_page) in enumerate(
        zip(pages, preflight_pages, strict=True), start=1
    ):
        if not isinstance(page, Mapping) or not isinstance(raw_page, Mapping):
            raise ValueError("normalization page is invalid")
        if (
            page.get("page_number") != number
            or raw_page.get("page_number") != number
            or page.get("source_media_box_mpt") != raw_page.get("media_box_mpt")
            or page.get("source_crop_box_mpt") != raw_page.get("crop_box_mpt")
            or page.get("source_rotation_degrees") != raw_page.get("rotation_degrees")
            or page.get("displayed_width_mpt") != raw_page.get("width_mpt")
            or page.get("displayed_height_mpt") != raw_page.get("height_mpt")
        ):
            raise ValueError("normalization page lineage differs")
    return artifact


def _cleanup_safe_copy(safe_copy: SafeInputCopy | None) -> bool:
    if safe_copy is None:
        return True
    try:
        safe_copy.cleanup()
    except OSError:
        return False
    return not safe_copy.root.exists()


def normalize_untrusted_pdf(
    source: str | Path,
    job_root: str | Path,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    """Normalize one passing raw preflight into immutable A4 PDF artifacts."""

    try:
        preflight, preflight_bytes, preflight_sha256 = _read_valid_preflight(
            Path(job_root), limits=limits
        )
    except Exception:
        return _error("PREFLIGHT_ARTIFACT_INVALID")

    safe_copy: SafeInputCopy | None = None
    normalized_pdf: bytes | None = None
    artifact_bytes: bytes | None = None
    artifact: dict[str, Any] | None = None
    failure_code: str | None = None
    try:
        safe_copy = copy_untrusted_input(Path(source), limits=limits)
        observed_file = preflight["limits"]["file_bytes"]
        if (
            safe_copy.size <= 0
            or preflight["source_sha256"] != safe_copy.sha256
            or observed_file["observed"] != safe_copy.size
        ):
            raise _StableBridgeError("PREFLIGHT_ARTIFACT_INVALID")
        _write_preflight_handoff(safe_copy.root, preflight_bytes)
        request = WorkerRequest(
            operation="normalize",
            input_path="input.pdf",
            parameters={
                "policy_version": NORMALIZATION_POLICY_VERSION,
                "source_sha256": safe_copy.sha256,
                "input_bytes": safe_copy.size,
                "preflight_sha256": preflight_sha256,
            },
        )
        result = _run_platform_worker(request, safe_copy.root, limits=limits)
        if (
            not isinstance(getattr(result, "provenance", None), dict)
            or result.provenance.get("appcontainer_cleanup_verified") is not True
        ):
            raise _StableBridgeError("SANDBOX_CONTRACT_UNVERIFIED")
        normalized_pdf = getattr(result, "normalized_pdf_bytes", None)
        artifact_bytes = getattr(result, "normalization_artifact_bytes", None)
        artifact = _decode_normalization_artifact(
            artifact_bytes,
            normalized_pdf,
            preflight=preflight,
            preflight_sha256=preflight_sha256,
            limits=limits,
        )
    except _StableBridgeError as error:
        failure_code = error.code
    except (OSError, UnsafeInputError):
        failure_code = "INPUT_REJECTED" if safe_copy is None else "WORKER_FAILED"
    except (ProtocolError, TypeError, UnicodeError, ValueError):
        failure_code = "NORMALIZATION_ARTIFACT_INVALID"
    except Exception:
        failure_code = "WORKER_FAILED"
    except BaseException:
        _cleanup_safe_copy(safe_copy)
        raise

    if not _cleanup_safe_copy(safe_copy):
        normalized_pdf = None
        artifact_bytes = None
        artifact = None
        failure_code = "SANDBOX_CONTRACT_UNVERIFIED"
    if failure_code is not None:
        return _error(failure_code)
    if normalized_pdf is None or artifact_bytes is None or artifact is None:
        return _error("WORKER_FAILED")

    root = Path(job_root)
    pdf_path = root / NORMALIZED_PDF_NAME
    artifact_path = root / NORMALIZATION_ARTIFACT_NAME
    try:
        pdf_hash = write_immutable_bytes(pdf_path, normalized_pdf)
        try:
            artifact_hash = write_immutable_artifact(
                artifact_path, artifact, "normalization"
            )
        except BaseException:
            if pdf_path.is_file() and sha256_bytes(pdf_path.read_bytes()) == pdf_hash:
                with suppress(OSError):
                    pdf_path.unlink()
            raise
    except ArtifactExistsError:
        return _error("ARTIFACT_EXISTS")
    except (OSError, TypeError, ValueError):
        return _error("NORMALIZATION_ARTIFACT_INVALID")
    return {
        "status": "ok",
        "artifacts": {
            "normalization": {
                "name": NORMALIZATION_ARTIFACT_NAME,
                "sha256": artifact_hash,
            },
            "normalized-pdf": {
                "name": NORMALIZED_PDF_NAME,
                "sha256": pdf_hash,
            },
        },
        "summary": {"page_count": len(artifact["pages"])},
    }


__all__ = [
    "NORMALIZATION_ARTIFACT_NAME",
    "NORMALIZED_PDF_NAME",
    "normalize_untrusted_pdf",
]
