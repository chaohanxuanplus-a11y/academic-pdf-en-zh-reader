# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Parent-only fail-closed entry point for an untrusted PDF preflight."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from academic_pdf_en_zh_reader.job.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
)
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    write_immutable_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.security.input_copy import (
    SafeInputCopy,
    UnsafeInputError,
    copy_untrusted_input,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    PREFLIGHT_POLICY_VERSION,
    ProtocolError,
    WorkerRequest,
)

_ARTIFACT_NAME = "preflight.json"
_ERROR_MESSAGES = {
    "INPUT_REJECTED": "The input file was rejected.",
    "SANDBOX_UNAVAILABLE": "The required isolated PDF worker is unavailable.",
    "SANDBOX_CONTRACT_UNVERIFIED": (
        "The isolated worker contract could not be verified."
    ),
    "WORKER_LIMIT_EXCEEDED": "The isolated PDF worker exceeded a resource limit.",
    "WORKER_FAILED": "The isolated PDF worker failed.",
    "PREFLIGHT_REJECTED": "The PDF did not pass mandatory preflight checks.",
    "PREFLIGHT_ARTIFACT_INVALID": "The isolated preflight result was invalid.",
    "ARTIFACT_EXISTS": "The immutable preflight artifact already exists.",
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


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _decode_preflight_artifact(
    encoded: object,
    *,
    expected_sha256: str,
    expected_size: int,
    limits: WorkerLimits,
) -> dict[str, Any]:
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > limits.max_result_object_bytes
    ):
        raise ValueError("artifact bytes are absent or oversized")
    try:
        decoded = json.loads(
            encoded.decode("utf-8", errors="strict"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("artifact is not strict UTF-8 JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("artifact root must be an object")
    validate_artifact("preflight", decoded)
    if canonical_json_bytes(decoded) != encoded:
        raise ValueError("artifact is not canonical JSON")
    observed_limits = decoded.get("limits")
    expected_maximums = {
        "file_bytes": limits.max_input_bytes,
        "page_count": limits.max_pages,
        "object_count": limits.max_objects,
        "recursion_depth": limits.max_recursion_depth,
        "decompressed_stream_bytes": limits.max_uncompressed_bytes,
        "image_bytes": limits.max_image_bytes,
    }
    if decoded.get("source_sha256") != expected_sha256 or not isinstance(
        observed_limits, dict
    ):
        raise ValueError("artifact source identity or limits differ")
    for name, maximum in expected_maximums.items():
        observed = observed_limits.get(name)
        if not isinstance(observed, dict) or observed.get("maximum") != maximum:
            raise ValueError("artifact limit policy differs")
    if observed_limits["file_bytes"].get("observed") != expected_size:
        raise ValueError("artifact source byte count differs")
    pages = decoded.get("pages")
    observed_page_count = observed_limits["page_count"].get("observed")
    if decoded.get("passed") is True:
        if not isinstance(pages, list) or observed_page_count != len(pages):
            raise ValueError("passing artifact page count differs")
        if [page.get("page_number") for page in pages] != list(
            range(1, len(pages) + 1)
        ):
            raise ValueError("passing artifact page numbers are not contiguous")
    return decoded


def _cleanup_safe_copy(safe_copy: SafeInputCopy | None) -> bool:
    if safe_copy is None:
        return True
    try:
        safe_copy.cleanup()
    except OSError:
        return False
    return not safe_copy.root.exists()


def preflight_untrusted_pdf(
    source: str | Path,
    job_root: str | Path,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    """Preflight one untrusted PDF; persist only a valid passing result."""

    safe_copy: SafeInputCopy | None = None
    artifact: dict[str, Any] | None = None
    failure_code: str | None = None
    try:
        safe_copy = copy_untrusted_input(Path(source), limits=limits)
        if safe_copy.size <= 0:
            raise _StableBridgeError("INPUT_REJECTED")
        request = WorkerRequest(
            operation="preflight",
            input_path="input.pdf",
            parameters={
                "policy_version": PREFLIGHT_POLICY_VERSION,
                "source_sha256": safe_copy.sha256,
                "input_bytes": safe_copy.size,
            },
        )
        result = _run_platform_worker(request, safe_copy.root, limits=limits)
        if (
            not isinstance(getattr(result, "provenance", None), dict)
            or result.provenance.get("appcontainer_cleanup_verified") is not True
        ):
            raise _StableBridgeError("SANDBOX_CONTRACT_UNVERIFIED")
        artifact = _decode_preflight_artifact(
            getattr(result, "artifact_bytes", None),
            expected_sha256=safe_copy.sha256,
            expected_size=safe_copy.size,
            limits=limits,
        )
    except (OSError, UnsafeInputError):
        failure_code = "INPUT_REJECTED"
    except _StableBridgeError as error:
        failure_code = error.code
    except (
        CanonicalJsonError,
        ProtocolError,
        SchemaValidationError,
        TypeError,
        ValueError,
    ):
        failure_code = "PREFLIGHT_ARTIFACT_INVALID"
    except Exception:
        failure_code = "WORKER_FAILED"
    except BaseException:
        _cleanup_safe_copy(safe_copy)
        raise

    if not _cleanup_safe_copy(safe_copy):
        artifact = None
        failure_code = "SANDBOX_CONTRACT_UNVERIFIED"
    if failure_code is not None:
        return _error(failure_code)
    if artifact is None:
        return _error("WORKER_FAILED")
    if artifact["passed"] is not True:
        outcome = _error("PREFLIGHT_REJECTED")
        error_codes = artifact.get("error_codes")
        outcome["preflight_error_codes"] = (
            list(error_codes) if isinstance(error_codes, list) else []
        )
        return outcome

    try:
        artifact_sha256 = write_immutable_artifact(
            Path(job_root) / _ARTIFACT_NAME,
            artifact,
            "preflight",
        )
    except ArtifactExistsError:
        return _error("ARTIFACT_EXISTS")
    except (CanonicalJsonError, SchemaValidationError, OSError, ValueError):
        return _error("PREFLIGHT_ARTIFACT_INVALID")
    warnings = artifact.get("warnings", [])
    pages = artifact.get("pages", [])
    return {
        "status": "ok",
        "artifact": {"name": _ARTIFACT_NAME, "sha256": artifact_sha256},
        "summary": {
            "page_count": len(pages) if isinstance(pages, list) else 0,
            "warning_count": len(warnings) if isinstance(warnings, list) else 0,
        },
    }
