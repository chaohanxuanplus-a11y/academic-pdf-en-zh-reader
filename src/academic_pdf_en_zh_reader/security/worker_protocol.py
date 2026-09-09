# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Small, versioned, and length-bounded worker JSON protocol."""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .limits import WorkerLimits

PROTOCOL_VERSION = 1
PREFLIGHT_POLICY_VERSION = "1.0.0"
PREFLIGHT_INPUT_PATH = "input.pdf"
PREFLIGHT_ARTIFACT_PATH = "output/preflight.json"
EXTRACTION_POLICY_VERSION = "1.1.0"
EXTRACTION_ARTIFACT_PATH = "output/extraction.json"
EXTRACTION_PREFLIGHT_PATH = "preflight.json"
EXTRACTION_NORMALIZATION_PATH = "normalization.json"
NORMALIZATION_POLICY_VERSION = "1.0.0"
NORMALIZATION_PDF_PATH = "output/normalized-source.pdf"
NORMALIZATION_ARTIFACT_PATH = "output/normalization.json"
NORMALIZATION_PREFLIGHT_PATH = "preflight.json"
RENDER_POLICY_VERSION = "1.0.0"
RENDER_INPUT_PATH = "input.pdf"
RENDER_HANDOFF_PATH = "render-input.json"
RENDER_PDF_PATH = "candidate.pdf"
RENDER_MANIFEST_PATH = "render-manifest.json"
QA_POLICY_VERSION = "1.0.0"
QA_INPUT_PATH = "input.pdf"
QA_HANDOFF_PATH = "qa-input.json"
QA_CANDIDATE_PATH = "candidate.pdf"
QA_ARTIFACT_PATH = "qa.json"
_MAX_STRING_LENGTH = 4096
_MAX_INTEGER_DIGITS = 64
_MAX_CONTAINER_ITEMS = 64
_MAX_NESTING_DEPTH = 4


class ProtocolError(ValueError):
    """The peer supplied data outside the fixed protocol contract."""


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    operation: str
    input_path: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    version: int = PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    status: str
    result: Mapping[str, Any] | None
    error: Mapping[str, str] | None
    version: int = PROTOCOL_VERSION

    @classmethod
    def ok(cls, result: Mapping[str, Any]) -> WorkerResponse:
        return cls(status="ok", result=dict(result), error=None)

    @classmethod
    def failed(cls, code: str, message: str) -> WorkerResponse:
        return cls(
            status="error",
            result=None,
            error={"code": code, "message": message},
        )


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > _MAX_NESTING_DEPTH:
        raise ProtocolError("JSON nesting depth exceeds the protocol limit")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if len(str(abs(value))) > _MAX_INTEGER_DIGITS:
            raise ProtocolError("JSON integer exceeds the protocol digit limit")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("JSON numbers must be finite")
        return
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise ProtocolError("JSON string exceeds the protocol length limit")
        if "\x00" in value:
            raise ProtocolError("JSON strings must not contain NUL")
        return
    if isinstance(value, list):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise ProtocolError("JSON array exceeds the protocol item limit")
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_CONTAINER_ITEMS:
            raise ProtocolError("JSON object exceeds the protocol item limit")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                raise ProtocolError("JSON object keys must be short strings")
            _validate_json_value(item, depth=depth + 1)
        return
    raise ProtocolError(f"unsupported JSON value type: {type(value).__name__}")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ProtocolError(f"invalid JSON value: {error}") from error
    return (serialized + "\n").encode("utf-8")


def _check_length(raw: bytes, limits: WorkerLimits) -> None:
    if not raw or len(raw) > limits.max_protocol_bytes:
        raise ProtocolError(
            f"JSON length must be between 1 and {limits.max_protocol_bytes} bytes"
        )


def _parse_json_integer(value: str) -> int:
    digits = value.removeprefix("-")
    if len(digits) > _MAX_INTEGER_DIGITS:
        raise ProtocolError("JSON integer exceeds the protocol digit limit")
    return int(value)


def _parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ProtocolError("JSON numbers must be finite")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ProtocolError(f"JSON number must be finite, got {value}")


def validate_relative_path(value: str) -> PurePosixPath:
    """Accept one normalized relative path, never a drive or device name."""

    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ProtocolError("path must be a non-empty bounded string")
    if "\x00" in value or "\\" in value or ":" in value:
        raise ProtocolError("path contains an unsafe separator or drive form")
    candidate = PurePosixPath(value)
    has_unsafe_part = any(part in {"", ".", ".."} for part in candidate.parts)
    if candidate.is_absolute() or has_unsafe_part:
        raise ProtocolError("path must be normalized and relative")
    for part in candidate.parts:
        if PureWindowsPath(part).is_reserved():
            raise ProtocolError("path contains a reserved device name")
    return candidate


def _is_reparse_or_symlink(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def resolve_controlled_path(
    root: Path,
    relative_path: str,
    *,
    must_exist: bool = True,
) -> Path:
    """Resolve a protocol path without following reparse components."""

    relative = validate_relative_path(relative_path)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as error:
        raise ProtocolError(f"controlled root is unavailable: {error}") from error
    if not root_resolved.is_dir() or _is_reparse_or_symlink(root_resolved):
        raise ProtocolError("controlled root must be a real directory")

    current = root_resolved
    for index, part in enumerate(relative.parts):
        current = current / part
        final = index == len(relative.parts) - 1
        if current.exists() or current.is_symlink():
            if _is_reparse_or_symlink(current):
                raise ProtocolError("path contains a symlink or reparse point")
        elif not final or must_exist:
            raise ProtocolError("path does not exist inside the controlled root")

    resolved = current.resolve(strict=must_exist)
    try:
        common = os.path.commonpath((str(root_resolved), str(resolved)))
    except ValueError as error:
        raise ProtocolError("path is on a different root") from error
    if os.path.normcase(common) != os.path.normcase(str(root_resolved)):
        raise ProtocolError("path resolves outside the controlled root")
    return resolved


def _parse_json(raw: bytes, limits: WorkerLimits) -> dict[str, Any]:
    _check_length(raw, limits)
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(
            decoded,
            parse_constant=_reject_json_constant,
            parse_float=_parse_json_float,
            parse_int=_parse_json_integer,
        )
    except ProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProtocolError(f"invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ProtocolError("top-level JSON must be an object")
    _validate_json_value(payload)
    return payload


def encode_request(request: WorkerRequest, limits: WorkerLimits) -> bytes:
    payload = {
        "input_path": request.input_path,
        "operation": request.operation,
        "parameters": dict(request.parameters),
        "version": request.version,
    }
    raw = _json_bytes(payload)
    _check_length(raw, limits)
    _request_from_payload(payload)
    return raw


def decode_request(raw: bytes, limits: WorkerLimits) -> WorkerRequest:
    return _request_from_payload(_parse_json(raw, limits))


def _request_from_payload(payload: Mapping[str, Any]) -> WorkerRequest:
    required = {"version", "operation", "input_path"}
    allowed = required | {"parameters"}
    if not required <= payload.keys() or not payload.keys() <= allowed:
        raise ProtocolError("request fields do not match the schema")
    if type(payload["version"]) is not int or payload["version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    operation = payload["operation"]
    if operation not in {
        "extract",
        "normalize",
        "preflight",
        "probe",
        "qa",
        "render",
    }:
        raise ProtocolError("unsupported worker operation")
    validate_relative_path(payload["input_path"])
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ProtocolError("request parameters must be an object")
    _validate_json_value(parameters)
    if operation == "preflight":
        _validate_preflight_request(payload["input_path"], parameters)
    if operation == "extract":
        _validate_extraction_request(payload["input_path"], parameters)
    if operation == "normalize":
        _validate_normalization_request(payload["input_path"], parameters)
    if operation == "render":
        _validate_render_request(payload["input_path"], parameters)
    if operation == "qa":
        _validate_qa_request(payload["input_path"], parameters)
    return WorkerRequest(
        operation=operation,
        input_path=payload["input_path"],
        parameters=dict(parameters),
    )


def _validate_preflight_request(
    input_path: object,
    parameters: Mapping[str, Any],
) -> None:
    if input_path != PREFLIGHT_INPUT_PATH:
        raise ProtocolError(f"preflight input_path must be {PREFLIGHT_INPUT_PATH!r}")
    required = {"policy_version", "source_sha256", "input_bytes"}
    if set(parameters) != required:
        raise ProtocolError("preflight parameters do not match the schema")
    if parameters["policy_version"] != PREFLIGHT_POLICY_VERSION:
        raise ProtocolError("unsupported preflight policy_version")
    source_sha256 = parameters["source_sha256"]
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise ProtocolError("preflight source_sha256 must be lowercase SHA-256")
    input_bytes = parameters["input_bytes"]
    if type(input_bytes) is not int or input_bytes <= 0:
        raise ProtocolError("preflight input_bytes must be a positive integer")


def _validate_extraction_request(
    input_path: object,
    parameters: Mapping[str, Any],
) -> None:
    if input_path != PREFLIGHT_INPUT_PATH:
        raise ProtocolError(f"extraction input_path must be {PREFLIGHT_INPUT_PATH!r}")
    required = {
        "policy_version",
        "source_sha256",
        "normalized_pdf_sha256",
        "input_bytes",
        "preflight_sha256",
        "normalization_sha256",
    }
    if set(parameters) != required:
        raise ProtocolError("extraction parameters do not match the schema")
    if parameters["policy_version"] != EXTRACTION_POLICY_VERSION:
        raise ProtocolError("unsupported extraction policy_version")
    for name in (
        "source_sha256",
        "normalized_pdf_sha256",
        "preflight_sha256",
        "normalization_sha256",
    ):
        digest = parameters[name]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ProtocolError(f"extraction {name} must be lowercase SHA-256")
    input_bytes = parameters["input_bytes"]
    if type(input_bytes) is not int or input_bytes <= 0:
        raise ProtocolError("extraction input_bytes must be a positive integer")


def _validate_normalization_request(
    input_path: object,
    parameters: Mapping[str, Any],
) -> None:
    if input_path != PREFLIGHT_INPUT_PATH:
        raise ProtocolError(
            f"normalization input_path must be {PREFLIGHT_INPUT_PATH!r}"
        )
    required = {
        "policy_version",
        "source_sha256",
        "input_bytes",
        "preflight_sha256",
    }
    if set(parameters) != required:
        raise ProtocolError("normalization parameters do not match the schema")
    if parameters["policy_version"] != NORMALIZATION_POLICY_VERSION:
        raise ProtocolError("unsupported normalization policy_version")
    for name in ("source_sha256", "preflight_sha256"):
        digest = parameters[name]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ProtocolError(f"normalization {name} must be lowercase SHA-256")
    input_bytes = parameters["input_bytes"]
    if type(input_bytes) is not int or input_bytes <= 0:
        raise ProtocolError("normalization input_bytes must be a positive integer")


def _validate_sha256(value: object, *, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProtocolError(f"{label} must be lowercase SHA-256")


def _validate_positive_size(value: object, *, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise ProtocolError(f"{label} must be a positive integer")


def _validate_render_request(
    input_path: object,
    parameters: Mapping[str, Any],
) -> None:
    if input_path != RENDER_INPUT_PATH:
        raise ProtocolError(f"render input_path must be {RENDER_INPUT_PATH!r}")
    required = {
        "policy_version",
        "input_bytes",
        "normalized_pdf_sha256",
        "handoff_bytes",
        "handoff_sha256",
    }
    if set(parameters) != required:
        raise ProtocolError("render parameters do not match the schema")
    if parameters["policy_version"] != RENDER_POLICY_VERSION:
        raise ProtocolError("unsupported render policy_version")
    _validate_positive_size(parameters["input_bytes"], label="render input_bytes")
    _validate_sha256(
        parameters["normalized_pdf_sha256"],
        label="render normalized_pdf_sha256",
    )
    _validate_positive_size(
        parameters["handoff_bytes"],
        label="render handoff_bytes",
    )
    _validate_sha256(
        parameters["handoff_sha256"],
        label="render handoff_sha256",
    )


def _validate_qa_request(
    input_path: object,
    parameters: Mapping[str, Any],
) -> None:
    if input_path != QA_INPUT_PATH:
        raise ProtocolError(f"qa input_path must be {QA_INPUT_PATH!r}")
    required = {
        "policy_version",
        "input_bytes",
        "normalized_pdf_sha256",
        "candidate_bytes",
        "candidate_pdf_sha256",
        "handoff_bytes",
        "handoff_sha256",
    }
    if set(parameters) != required:
        raise ProtocolError("qa parameters do not match the schema")
    if parameters["policy_version"] != QA_POLICY_VERSION:
        raise ProtocolError("unsupported qa policy_version")
    _validate_positive_size(parameters["input_bytes"], label="qa input_bytes")
    _validate_sha256(
        parameters["normalized_pdf_sha256"],
        label="qa normalized_pdf_sha256",
    )
    _validate_positive_size(
        parameters["candidate_bytes"],
        label="qa candidate_bytes",
    )
    _validate_sha256(
        parameters["candidate_pdf_sha256"],
        label="qa candidate_pdf_sha256",
    )
    _validate_positive_size(parameters["handoff_bytes"], label="qa handoff_bytes")
    _validate_sha256(parameters["handoff_sha256"], label="qa handoff_sha256")


def encode_response(response: WorkerResponse, limits: WorkerLimits) -> bytes:
    payload = {
        "error": dict(response.error) if response.error is not None else None,
        "result": dict(response.result) if response.result is not None else None,
        "status": response.status,
        "version": response.version,
    }
    _validate_response_payload(payload)
    raw = _json_bytes(payload)
    _check_length(raw, limits)
    return raw


def decode_response(raw: bytes, limits: WorkerLimits) -> WorkerResponse:
    payload = _parse_json(raw, limits)
    return _validate_response_payload(payload)


def _validate_response_payload(payload: Mapping[str, Any]) -> WorkerResponse:
    if set(payload) != {"version", "status", "result", "error"}:
        raise ProtocolError("response fields do not match the schema")
    if type(payload["version"]) is not int or payload["version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    status = payload["status"]
    result = payload["result"]
    error = payload["error"]
    if status == "ok":
        if not isinstance(result, dict) or error is not None:
            raise ProtocolError("successful response has invalid result fields")
        _validate_json_value(result)
        return WorkerResponse.ok(result)
    if status == "error":
        if result is not None or not isinstance(error, dict):
            raise ProtocolError("error response has invalid fields")
        if set(error) != {"code", "message"} or not all(
            isinstance(error[key], str) for key in error
        ):
            raise ProtocolError("error response does not match the schema")
        return WorkerResponse.failed(error["code"], error["message"])
    raise ProtocolError("response status is invalid")
