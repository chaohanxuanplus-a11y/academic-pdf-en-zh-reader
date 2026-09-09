# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security.limits import WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    WorkerRequest,
    WorkerResponse,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
    resolve_controlled_path,
)


def test_versioned_request_and_response_round_trip() -> None:
    limits = WorkerLimits(max_protocol_bytes=4096)
    request = WorkerRequest(
        operation="probe",
        input_path="input/paper.pdf",
        parameters={"case": "inspect"},
    )
    response = WorkerResponse.ok({"restricted_token": True})

    assert decode_request(encode_request(request, limits), limits) == request
    assert decode_response(encode_response(response, limits), limits) == response
    assert request.version == PROTOCOL_VERSION
    assert response.version == PROTOCOL_VERSION


@pytest.mark.parametrize(
    ("operation", "parameters"),
    (
        (
            "render",
            {
                "policy_version": "1.0.0",
                "input_bytes": 1024,
                "normalized_pdf_sha256": "a" * 64,
                "handoff_bytes": 2048,
                "handoff_sha256": "b" * 64,
            },
        ),
        (
            "qa",
            {
                "policy_version": "1.0.0",
                "input_bytes": 1024,
                "normalized_pdf_sha256": "a" * 64,
                "candidate_bytes": 4096,
                "candidate_pdf_sha256": "b" * 64,
                "handoff_bytes": 2048,
                "handoff_sha256": "c" * 64,
            },
        ),
    ),
)
def test_render_and_qa_requests_have_fixed_bounded_handoff_schemas(
    operation: str,
    parameters: dict[str, object],
) -> None:
    limits = WorkerLimits(max_protocol_bytes=4096)
    request = WorkerRequest(
        operation=operation,
        input_path="input.pdf",
        parameters=parameters,
    )

    assert decode_request(encode_request(request, limits), limits) == request

    unexpected = WorkerRequest(
        operation=operation,
        input_path="input.pdf",
        parameters={**parameters, "caller_selected_path": "outside.json"},
    )
    with pytest.raises(ProtocolError, match="schema"):
        encode_request(unexpected, limits)


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"not-json", "JSON"),
        (b'{"version":999,"operation":"probe","input_path":"x"}', "version"),
        (b'{"version":1,"operation":"shell","input_path":"x"}', "operation"),
        (
            b'{"version":1,"operation":"probe","input_path":"x","unexpected":true}',
            "fields",
        ),
    ],
)
def test_request_rejects_non_schema_data(payload: bytes, message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        decode_request(payload, WorkerLimits(max_protocol_bytes=4096))


@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_protocol_version_requires_an_exact_integer_type(
    invalid_version: object,
) -> None:
    limits = WorkerLimits(max_protocol_bytes=4096)
    request = WorkerRequest(
        operation="probe",
        input_path="input.pdf",
        parameters={"case": "inspect"},
        version=invalid_version,  # type: ignore[arg-type]
    )
    response = WorkerResponse(
        status="ok",
        result={},
        error=None,
        version=invalid_version,  # type: ignore[arg-type]
    )
    encoded_version = b"true" if invalid_version is True else b"1.0"
    raw_request = (
        b'{"input_path":"input.pdf","operation":"probe","parameters":'
        b'{"case":"inspect"},"version":' + encoded_version + b"}"
    )
    raw_response = (
        b'{"error":null,"result":{},"status":"ok","version":' + encoded_version + b"}"
    )

    with pytest.raises(ProtocolError, match="version"):
        encode_request(request, limits)
    with pytest.raises(ProtocolError, match="version"):
        decode_request(raw_request, limits)
    with pytest.raises(ProtocolError, match="version"):
        encode_response(response, limits)
    with pytest.raises(ProtocolError, match="version"):
        decode_response(raw_response, limits)


def test_protocol_rejects_oversized_json() -> None:
    limits = WorkerLimits(max_protocol_bytes=64)
    request = WorkerRequest(
        operation="probe",
        input_path="input.pdf",
        parameters={"case": "inspect", "padding": "x" * 200},
    )

    with pytest.raises(ProtocolError, match="length"):
        encode_request(request, limits)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_protocol_rejects_non_finite_numbers_as_protocol_errors(
    number: str,
) -> None:
    payload = (
        '{"version":1,"operation":"probe","input_path":"input.pdf",'
        f'"parameters":{{"value":{number}}}}}'
    ).encode()

    with pytest.raises(ProtocolError, match="JSON|finite"):
        decode_request(payload, WorkerLimits(max_protocol_bytes=4096))


def test_protocol_rejects_an_overlong_integer_as_a_protocol_error() -> None:
    payload = (
        '{"version":1,"operation":"probe","input_path":"input.pdf",'
        f'"parameters":{{"value":{"9" * 65}}}}}'
    ).encode()

    with pytest.raises(ProtocolError, match="JSON|integer"):
        decode_request(payload, WorkerLimits(max_protocol_bytes=4096))


@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside.pdf",
        "/absolute.pdf",
        "C:/outside.pdf",
        "folder\\outside.pdf",
        "NUL",
        "folder/../../outside.pdf",
    ],
)
def test_controlled_paths_reject_escape_and_device_forms(
    tmp_path: Path,
    relative_path: str,
) -> None:
    with pytest.raises(ProtocolError, match="path"):
        resolve_controlled_path(tmp_path, relative_path, must_exist=False)


def test_controlled_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"creating symlinks is unavailable: {error}")

    with pytest.raises(ProtocolError, match="reparse|symlink|root"):
        resolve_controlled_path(root, "escape/result.json", must_exist=False)
