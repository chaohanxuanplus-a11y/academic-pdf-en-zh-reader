# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security.limits import WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    PREFLIGHT_ARTIFACT_PATH,
    PREFLIGHT_POLICY_VERSION,
    ProtocolError,
    WorkerRequest,
    decode_request,
    encode_request,
)


def _request(**overrides: object) -> WorkerRequest:
    parameters: dict[str, object] = {
        "policy_version": PREFLIGHT_POLICY_VERSION,
        "source_sha256": "a" * 64,
        "input_bytes": 123,
    }
    parameters.update(overrides.pop("parameters", {}))
    return WorkerRequest(
        operation=str(overrides.pop("operation", "preflight")),
        input_path=str(overrides.pop("input_path", "input.pdf")),
        parameters=parameters,
    )


def test_preflight_request_has_one_exact_production_shape() -> None:
    limits = WorkerLimits(max_protocol_bytes=4096)
    request = _request()

    assert decode_request(encode_request(request, limits), limits) == request


@pytest.mark.parametrize(
    ("worker_request", "message"),
    [
        (_request(input_path="other.pdf"), "input_path"),
        (_request(parameters={"extra": 1}), "parameters"),
        (_request(parameters={"policy_version": "2.0.0"}), "policy_version"),
        (_request(parameters={"source_sha256": "A" * 64}), "source_sha256"),
        (_request(parameters={"input_bytes": True}), "input_bytes"),
        (_request(parameters={"input_bytes": 0}), "input_bytes"),
    ],
)
def test_preflight_request_rejects_ambient_authority_and_loose_types(
    worker_request: WorkerRequest,
    message: str,
) -> None:
    with pytest.raises(ProtocolError, match=message):
        encode_request(worker_request, WorkerLimits(max_protocol_bytes=4096))


def test_preflight_descriptor_is_fixed_and_small() -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_preflight_descriptor,
    )

    payload = b'{"artifact_kind":"preflight"}'
    descriptor = {
        "artifact_path": PREFLIGHT_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_bytes": len(payload),
    }

    assert _validate_preflight_descriptor(descriptor, WorkerLimits()) == (
        descriptor["artifact_sha256"],
        len(payload),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"artifact_path": "output/other.json"},
        {"artifact_path": "../preflight.json"},
        {"artifact_sha256": "A" * 64},
        {"artifact_bytes": True},
        {"artifact_bytes": 16 * 1024 * 1024 + 1},
        {"extra": "second-artifact"},
    ],
)
def test_preflight_descriptor_rejects_tampering(mutation: dict[str, object]) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_preflight_descriptor,
    )

    descriptor: dict[str, object] = {
        "artifact_path": PREFLIGHT_ARTIFACT_PATH,
        "artifact_sha256": "b" * 64,
        "artifact_bytes": 12,
    }
    descriptor.update(mutation)

    with pytest.raises(ProtocolError):
        _validate_preflight_descriptor(descriptor, WorkerLimits())


def test_restricted_test_adapter_refuses_preflight_before_parser_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker

    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (True, [], False, 0),
    )

    response = windows_worker._dispatch_request(_request(), WorkerLimits())

    assert response.status == "error"
    assert response.error == {
        "code": "SANDBOX_CONTRACT_UNVERIFIED",
        "message": "zero-capability less-privileged AppContainer token required",
    }


def test_artifact_reader_checks_hash_size_and_fixed_path(tmp_path: Path) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        WorkerExecutionError,
        _read_preflight_artifact,
    )

    output = tmp_path / "output"
    output.mkdir()
    payload = json.dumps({"passed": True}, separators=(",", ":")).encode()
    (output / "preflight.json").write_bytes(payload)
    descriptor = {
        "artifact_path": PREFLIGHT_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_bytes": len(payload),
    }

    assert _read_preflight_artifact(tmp_path, descriptor, WorkerLimits()) == payload

    descriptor["artifact_bytes"] = len(payload) + 1
    with pytest.raises(WorkerExecutionError, match="integrity"):
        _read_preflight_artifact(tmp_path, descriptor, WorkerLimits())

    descriptor["artifact_bytes"] = len(payload)
    descriptor["artifact_sha256"] = "0" * 64
    with pytest.raises(WorkerExecutionError, match="integrity"):
        _read_preflight_artifact(tmp_path, descriptor, WorkerLimits())


def test_artifact_reader_rejects_an_oversized_regular_file(tmp_path: Path) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        WorkerExecutionError,
        _read_preflight_artifact,
    )

    output = tmp_path / "output"
    output.mkdir()
    payload = b"12345"
    (output / "preflight.json").write_bytes(payload)
    descriptor = {
        "artifact_path": PREFLIGHT_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_bytes": 4,
    }

    with pytest.raises(WorkerExecutionError, match="read safely"):
        _read_preflight_artifact(
            tmp_path,
            descriptor,
            WorkerLimits(max_result_object_bytes=4),
        )


def test_artifact_reader_rejects_reparse_point(tmp_path: Path) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        WorkerExecutionError,
        _read_preflight_artifact,
    )

    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    output = tmp_path / "output"
    output.mkdir()
    link = output / "preflight.json"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"creating symlinks is unavailable: {error}")
    descriptor = {
        "artifact_path": PREFLIGHT_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(b"{}").hexdigest(),
        "artifact_bytes": 2,
    }

    with pytest.raises((WorkerExecutionError, ProtocolError)):
        _read_preflight_artifact(tmp_path, descriptor, WorkerLimits())
