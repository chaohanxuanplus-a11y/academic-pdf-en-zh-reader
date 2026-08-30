# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.security.limits import WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    EXTRACTION_ARTIFACT_PATH,
    EXTRACTION_NORMALIZATION_PATH,
    EXTRACTION_POLICY_VERSION,
    ProtocolError,
    WorkerRequest,
    decode_request,
    encode_request,
)


def _request(**overrides: object) -> WorkerRequest:
    parameters: dict[str, object] = {
        "policy_version": EXTRACTION_POLICY_VERSION,
        "source_sha256": "a" * 64,
        "normalized_pdf_sha256": "b" * 64,
        "input_bytes": 123,
        "preflight_sha256": "c" * 64,
        "normalization_sha256": "d" * 64,
    }
    parameters.update(overrides.pop("parameters", {}))
    return WorkerRequest(
        operation=str(overrides.pop("operation", "extract")),
        input_path=str(overrides.pop("input_path", "input.pdf")),
        parameters=parameters,
    )


def _normalization_handoff() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": "1.0.0",
        "source_sha256": "a" * 64,
        "preflight_sha256": "c" * 64,
        "normalized_pdf_sha256": "b" * 64,
        "normalized_pdf_bytes": 123,
        "pages": [{}],
    }


def test_extraction_request_has_one_exact_production_shape() -> None:
    limits = WorkerLimits(max_protocol_bytes=4096)
    request = _request()

    assert decode_request(encode_request(request, limits), limits) == request
    assert EXTRACTION_NORMALIZATION_PATH == "normalization.json"


@pytest.mark.parametrize(
    ("worker_request", "message"),
    [
        (_request(input_path="other.pdf"), "input_path"),
        (_request(parameters={"extra": 1}), "parameters"),
        (_request(parameters={"policy_version": "2.0.0"}), "policy_version"),
        (_request(parameters={"source_sha256": "A" * 64}), "source_sha256"),
        (
            _request(parameters={"normalized_pdf_sha256": "B" * 64}),
            "normalized_pdf_sha256",
        ),
        (_request(parameters={"preflight_sha256": "B" * 64}), "preflight_sha256"),
        (
            _request(parameters={"normalization_sha256": "D" * 64}),
            "normalization_sha256",
        ),
        (_request(parameters={"input_bytes": True}), "input_bytes"),
        (_request(parameters={"input_bytes": 0}), "input_bytes"),
    ],
)
def test_extraction_request_rejects_loose_types(
    worker_request: WorkerRequest,
    message: str,
) -> None:
    with pytest.raises(ProtocolError, match=message):
        encode_request(worker_request, WorkerLimits(max_protocol_bytes=4096))


def test_extraction_descriptor_is_fixed_and_small() -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_extraction_descriptor,
    )

    payload = b'{"artifact_kind":"extraction"}'
    descriptor = {
        "artifact_path": EXTRACTION_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_bytes": len(payload),
    }

    assert _validate_extraction_descriptor(descriptor, WorkerLimits()) == (
        descriptor["artifact_sha256"],
        len(payload),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"artifact_path": "output/other.json"},
        {"artifact_path": "../extraction.json"},
        {"artifact_sha256": "A" * 64},
        {"artifact_bytes": True},
        {"artifact_bytes": 128 * 1024 * 1024 + 1},
        {"extra": "second-artifact"},
    ],
)
def test_extraction_descriptor_rejects_tampering(
    mutation: dict[str, object],
) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_extraction_descriptor,
    )

    descriptor: dict[str, object] = {
        "artifact_path": EXTRACTION_ARTIFACT_PATH,
        "artifact_sha256": "b" * 64,
        "artifact_bytes": 12,
    }
    descriptor.update(mutation)

    with pytest.raises(ProtocolError):
        _validate_extraction_descriptor(descriptor, WorkerLimits())


def test_extraction_descriptor_honors_custom_small_limit() -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_extraction_descriptor,
    )

    descriptor = {
        "artifact_path": EXTRACTION_ARTIFACT_PATH,
        "artifact_sha256": "b" * 64,
        "artifact_bytes": 5,
    }

    with pytest.raises(ProtocolError, match="size"):
        _validate_extraction_descriptor(
            descriptor,
            WorkerLimits(max_extraction_artifact_bytes=4),
        )


def test_child_validates_canonical_normalization_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker

    encoded = canonical_json_bytes(_normalization_handoff())
    path = tmp_path / "normalization.json"
    path.write_bytes(encoded)
    request = _request(
        parameters={"normalization_sha256": hashlib.sha256(encoded).hexdigest()}
    )
    monkeypatch.setattr(
        windows_worker,
        "_resolve_prevalidated_appcontainer_path",
        lambda *_args, **_kwargs: path,
    )

    artifact, observed = windows_worker._validated_extraction_normalization(
        request,
        WorkerLimits(),
    )

    assert artifact == _normalization_handoff()
    assert observed == encoded


@pytest.mark.parametrize("mutation", ["noncanonical", "wrong_identity"])
def test_child_rejects_tampered_normalization_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker

    artifact = _normalization_handoff()
    if mutation == "wrong_identity":
        artifact["normalized_pdf_sha256"] = "e" * 64
    encoded = (
        json.dumps(artifact, indent=2).encode()
        if mutation == "noncanonical"
        else canonical_json_bytes(artifact)
    )
    path = tmp_path / "normalization.json"
    path.write_bytes(encoded)
    request = _request(
        parameters={"normalization_sha256": hashlib.sha256(encoded).hexdigest()}
    )
    monkeypatch.setattr(
        windows_worker,
        "_resolve_prevalidated_appcontainer_path",
        lambda *_args, **_kwargs: path,
    )

    with pytest.raises(ProtocolError, match="canonical|identity"):
        windows_worker._validated_extraction_normalization(
            request,
            WorkerLimits(),
        )


def test_restricted_test_adapter_refuses_extraction_before_parser_import(
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
        "message": "zero-capability AppContainer token required",
    }


def test_child_rejects_normalized_input_over_its_byte_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker

    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"x" * 123)
    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (True, [], True, 0),
    )
    monkeypatch.setattr(
        windows_worker,
        "_resolve_prevalidated_appcontainer_path",
        lambda *_args, **_kwargs: input_path,
    )

    with pytest.raises(ProtocolError, match="source identity"):
        windows_worker._run_extraction_request(
            _request(),
            WorkerLimits(max_normalized_pdf_bytes=122),
        )


def test_scanned_ocr_rejection_crosses_worker_boundary_with_stable_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader import extraction
    from academic_pdf_en_zh_reader.security import windows_worker

    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"x" * 123)
    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (True, [], True, 0),
    )
    monkeypatch.setattr(
        windows_worker,
        "_resolve_prevalidated_appcontainer_path",
        lambda *_args, **_kwargs: input_path,
    )
    monkeypatch.setattr(windows_worker, "_file_sha256", lambda _path: "b" * 64)
    monkeypatch.setattr(
        windows_worker,
        "_validated_extraction_preflight",
        lambda *_args, **_kwargs: ({"pages": [{}]}, b"preflight"),
    )
    monkeypatch.setattr(
        windows_worker,
        "_validated_extraction_normalization",
        lambda *_args, **_kwargs: ({"pages": [{}]}, b"normalization"),
    )

    def reject_scan(_path: Path) -> dict[str, object]:
        raise extraction.ScannedPdfUnsupportedError("scan")

    monkeypatch.setattr(extraction, "extract_document", reject_scan)

    response = windows_worker._run_extraction_request(_request(), WorkerLimits())

    assert response.status == "error"
    assert response.error == {
        "code": "SCANNED_PDF_UNSUPPORTED",
        "message": "scanned or OCR-overlay PDFs are unsupported",
    }


def test_memory_exhaustion_crosses_worker_boundary_with_stable_limit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader import extraction
    from academic_pdf_en_zh_reader.security import windows_worker

    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"x" * 123)
    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (True, [], True, 0),
    )
    monkeypatch.setattr(
        windows_worker,
        "_resolve_prevalidated_appcontainer_path",
        lambda *_args, **_kwargs: input_path,
    )
    monkeypatch.setattr(windows_worker, "_file_sha256", lambda _path: "b" * 64)
    monkeypatch.setattr(
        windows_worker,
        "_validated_extraction_preflight",
        lambda *_args, **_kwargs: ({"pages": [{}]}, b"preflight"),
    )
    monkeypatch.setattr(
        windows_worker,
        "_validated_extraction_normalization",
        lambda *_args, **_kwargs: ({"pages": [{}]}, b"normalization"),
    )

    def exhaust_memory(_path: Path) -> dict[str, object]:
        raise MemoryError

    monkeypatch.setattr(extraction, "extract_document", exhaust_memory)

    response = windows_worker._run_extraction_request(_request(), WorkerLimits())

    assert response.status == "error"
    assert response.error == {
        "code": "WORKER_LIMIT_EXCEEDED",
        "message": "PDF extraction exceeded the worker memory limit",
    }


def test_extraction_artifact_reader_checks_hash_size_and_fixed_path(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        WorkerExecutionError,
        _read_extraction_artifact,
    )

    output = tmp_path / "output"
    output.mkdir()
    payload = json.dumps({"pages": []}, separators=(",", ":")).encode()
    (output / "extraction.json").write_bytes(payload)
    descriptor = {
        "artifact_path": EXTRACTION_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_bytes": len(payload),
    }

    assert _read_extraction_artifact(tmp_path, descriptor, WorkerLimits()) == payload

    descriptor["artifact_sha256"] = "0" * 64
    with pytest.raises(WorkerExecutionError, match="integrity"):
        _read_extraction_artifact(tmp_path, descriptor, WorkerLimits())
