# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security.limits import WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    NORMALIZATION_ARTIFACT_PATH,
    NORMALIZATION_PDF_PATH,
    NORMALIZATION_POLICY_VERSION,
    ProtocolError,
    WorkerRequest,
    decode_request,
    encode_request,
)


def _request(**overrides: object) -> WorkerRequest:
    parameters: dict[str, object] = {
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "source_sha256": "a" * 64,
        "input_bytes": 123,
        "preflight_sha256": "b" * 64,
    }
    parameters.update(overrides.pop("parameters", {}))
    return WorkerRequest(
        operation=str(overrides.pop("operation", "normalize")),
        input_path=str(overrides.pop("input_path", "input.pdf")),
        parameters=parameters,
    )


def _descriptor(path: str, payload: bytes) -> dict[str, object]:
    return {
        "artifact_path": path,
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_bytes": len(payload),
    }


def test_normalization_request_has_one_exact_production_shape() -> None:
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
        (_request(parameters={"preflight_sha256": "B" * 64}), "preflight_sha256"),
        (_request(parameters={"input_bytes": True}), "input_bytes"),
        (_request(parameters={"input_bytes": 0}), "input_bytes"),
    ],
)
def test_normalization_request_rejects_loose_types(
    worker_request: WorkerRequest,
    message: str,
) -> None:
    with pytest.raises(ProtocolError, match=message):
        encode_request(worker_request, WorkerLimits(max_protocol_bytes=4096))


def test_normalization_dual_descriptors_are_fixed_and_bounded() -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_normalization_descriptors,
    )

    pdf = b"%PDF-1.7\n"
    artifact = b'{"artifact_kind":"normalization"}\n'
    descriptors = {
        "normalized_pdf": _descriptor(NORMALIZATION_PDF_PATH, pdf),
        "normalization_artifact": _descriptor(
            NORMALIZATION_ARTIFACT_PATH,
            artifact,
        ),
    }

    assert _validate_normalization_descriptors(descriptors, WorkerLimits()) == (
        (
            descriptors["normalized_pdf"]["artifact_sha256"],
            len(pdf),
        ),
        (
            descriptors["normalization_artifact"]["artifact_sha256"],
            len(artifact),
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"normalized_pdf": {"artifact_path": "output/other.pdf"}},
        {"normalized_pdf": {"artifact_path": "../normalized-source.pdf"}},
        {"normalized_pdf": {"artifact_sha256": "A" * 64}},
        {"normalized_pdf": {"artifact_bytes": True}},
        {
            "normalized_pdf": {
                "artifact_bytes": 128 * 1024 * 1024 + 1,
            }
        },
        {"normalization_artifact": {"artifact_path": "output/other.json"}},
        {"normalization_artifact": {"artifact_sha256": "A" * 64}},
        {"normalization_artifact": {"artifact_bytes": True}},
        {"extra": {}},
    ],
)
def test_normalization_descriptors_reject_tampering(
    mutation: dict[str, object],
) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_normalization_descriptors,
    )

    descriptors: dict[str, object] = {
        "normalized_pdf": _descriptor(NORMALIZATION_PDF_PATH, b"pdf"),
        "normalization_artifact": _descriptor(
            NORMALIZATION_ARTIFACT_PATH,
            b"json",
        ),
    }
    for name, value in mutation.items():
        if name in descriptors and isinstance(value, dict):
            nested = dict(descriptors[name])
            nested.update(value)
            descriptors[name] = nested
        else:
            descriptors[name] = value

    with pytest.raises(ProtocolError):
        _validate_normalization_descriptors(descriptors, WorkerLimits())


def test_normalization_descriptor_honors_independent_limits() -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _validate_normalization_descriptors,
    )

    descriptors = {
        "normalized_pdf": _descriptor(NORMALIZATION_PDF_PATH, b"12345"),
        "normalization_artifact": _descriptor(
            NORMALIZATION_ARTIFACT_PATH,
            b"12345",
        ),
    }

    with pytest.raises(ProtocolError, match="normalized PDF size"):
        _validate_normalization_descriptors(
            descriptors,
            WorkerLimits(max_normalized_pdf_bytes=4),
        )
    with pytest.raises(ProtocolError, match="artifact size"):
        _validate_normalization_descriptors(
            descriptors,
            WorkerLimits(max_result_object_bytes=4),
        )


def test_normalization_runtime_is_content_addressed_and_pinned(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.normalization.core import (
        NORMALIZATION_POLICY_VERSION as CORE_POLICY_VERSION,
    )
    from academic_pdf_en_zh_reader.security import windows_worker

    runtime = windows_worker._copy_worker_runtime(
        tmp_path,
        operation="normalize",
    )

    assert runtime.pypdf_version == "6.16.2"
    assert CORE_POLICY_VERSION == NORMALIZATION_POLICY_VERSION
    assert runtime.pypdf_file_count > 0
    assert runtime.pypdf_fingerprint
    assert runtime.extraction_runtime_file_count == 0
    assert (
        runtime.root / "academic_pdf_en_zh_reader" / "normalization" / "core.py"
    ).is_file()


def test_child_local_source_reader_does_not_reenter_parent_path_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _read_prevalidated_appcontainer_file,
    )

    source = b"worker-local PDF bytes"
    (tmp_path / "input.pdf").write_bytes(source)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        windows_worker,
        "read_bounded_regular_file",
        lambda *_args, **_kwargs: pytest.fail(
            "the child must not traverse the AppContainer path as a parent input"
        ),
    )

    observed = _read_prevalidated_appcontainer_file(
        "input.pdf",
        max_bytes=len(source),
    )

    assert observed.data == source
    assert observed.size == len(source)
    assert observed.sha256 == hashlib.sha256(source).hexdigest()


def test_restricted_adapter_refuses_normalization_before_core_import(
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


def test_normalization_handler_binds_core_outputs_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.normalization import core
    from academic_pdf_en_zh_reader.security import windows_worker
    from academic_pdf_en_zh_reader.security.input_copy import BoundedRegularFile

    source = b"source"
    source_sha256 = hashlib.sha256(source).hexdigest()
    request = _request(
        parameters={
            "source_sha256": source_sha256,
            "input_bytes": len(source),
        }
    )
    pdf = b"%PDF-normalized"
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "source_sha256": source_sha256,
        "preflight_sha256": "b" * 64,
        "normalized_pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "normalized_pdf_bytes": len(pdf),
        "pages": [],
    }

    @dataclass(frozen=True)
    class Result:
        pdf_bytes: bytes
        artifact: dict[str, object]

    observed: dict[str, object] = {}

    def normalize(
        source_bytes: bytes,
        *,
        preflight: dict[str, object],
        preflight_sha256: str,
        max_output_bytes: int,
    ) -> Result:
        observed.update(
            source_bytes=source_bytes,
            preflight=preflight,
            preflight_sha256=preflight_sha256,
            max_output_bytes=max_output_bytes,
        )
        return Result(pdf, artifact)

    def write(
        actual_pdf: bytes,
        actual_artifact: dict[str, object],
        _limits: WorkerLimits,
    ) -> dict[str, object]:
        observed.update(pdf=actual_pdf, artifact=actual_artifact)
        return {
            "normalized_pdf": _descriptor(NORMALIZATION_PDF_PATH, pdf),
            "normalization_artifact": _descriptor(
                NORMALIZATION_ARTIFACT_PATH,
                b"artifact",
            ),
        }

    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (True, [], True, 0),
    )
    monkeypatch.setattr(
        windows_worker,
        "_read_prevalidated_appcontainer_file",
        lambda *_args, **_kwargs: BoundedRegularFile(
            data=source,
            sha256=source_sha256,
            size=len(source),
        ),
    )
    monkeypatch.setattr(
        windows_worker,
        "read_bounded_regular_file",
        lambda *_args, **_kwargs: pytest.fail(
            "normalization input must use the child-local reader"
        ),
    )
    monkeypatch.setattr(
        windows_worker,
        "_validated_extraction_preflight",
        lambda *_args, **_kwargs: ({"passed": True, "pages": []}, b"preflight"),
    )
    monkeypatch.setattr(core, "normalize_pdf_bytes", normalize)
    monkeypatch.setattr(windows_worker, "_write_normalization_artifacts", write)

    limits = WorkerLimits(max_normalized_pdf_bytes=9876)
    response = windows_worker._run_normalization_request(request, limits)

    assert response.status == "ok"
    assert observed == {
        "source_bytes": source,
        "preflight": {"passed": True, "pages": []},
        "preflight_sha256": "b" * 64,
        "max_output_bytes": 9876,
        "pdf": pdf,
        "artifact": artifact,
    }


def test_normalization_handler_rejects_unbound_core_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.normalization import core
    from academic_pdf_en_zh_reader.security import windows_worker
    from academic_pdf_en_zh_reader.security.input_copy import BoundedRegularFile

    source = b"source"
    source_sha256 = hashlib.sha256(source).hexdigest()
    request = _request(
        parameters={
            "source_sha256": source_sha256,
            "input_bytes": len(source),
        }
    )
    pdf = b"%PDF-normalized"
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "source_sha256": "0" * 64,
        "preflight_sha256": "b" * 64,
        "normalized_pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "normalized_pdf_bytes": len(pdf),
        "pages": [],
    }
    result = core.NormalizationResult(pdf_bytes=pdf, artifact=artifact)
    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (True, [], True, 0),
    )
    monkeypatch.setattr(
        windows_worker,
        "_read_prevalidated_appcontainer_file",
        lambda *_args, **_kwargs: BoundedRegularFile(
            data=source,
            sha256=source_sha256,
            size=len(source),
        ),
    )
    monkeypatch.setattr(
        windows_worker,
        "_validated_extraction_preflight",
        lambda *_args, **_kwargs: ({"passed": True, "pages": []}, b"preflight"),
    )
    monkeypatch.setattr(core, "normalize_pdf_bytes", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        windows_worker,
        "_write_normalization_artifacts",
        lambda *_args, **_kwargs: pytest.fail("unbound artifacts must not be written"),
    )

    response = windows_worker._run_normalization_request(request, WorkerLimits())

    assert response.status == "error"
    assert response.error is not None
    assert response.error["code"] == "NORMALIZATION_ARTIFACT_INVALID"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NORMALIZATION_GEOMETRY_INVALID", "NORMALIZATION_GEOMETRY_INVALID"),
        ("lowercase", "NORMALIZATION_FAILED"),
        ("NÖN_ASCII", "NORMALIZATION_FAILED"),
        ("A" * 65, "NORMALIZATION_FAILED"),
    ],
)
def test_normalization_error_codes_are_ascii_and_bounded(
    code: str,
    expected: str,
) -> None:
    from academic_pdf_en_zh_reader.normalization.core import NormalizationError
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _stable_normalization_error_code,
    )

    assert _stable_normalization_error_code(NormalizationError(code)) == expected


def test_normalization_reader_checks_both_hashes_sizes_and_fixed_paths(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        WorkerExecutionError,
        _read_normalization_artifacts,
    )

    output = tmp_path / "output"
    output.mkdir()
    pdf = b"%PDF-1.7\n"
    artifact = b'{"artifact_kind":"normalization"}\n'
    (output / "normalized-source.pdf").write_bytes(pdf)
    (output / "normalization.json").write_bytes(artifact)
    descriptors = {
        "normalized_pdf": _descriptor(NORMALIZATION_PDF_PATH, pdf),
        "normalization_artifact": _descriptor(
            NORMALIZATION_ARTIFACT_PATH,
            artifact,
        ),
    }

    assert _read_normalization_artifacts(
        tmp_path,
        descriptors,
        WorkerLimits(),
    ) == (pdf, artifact)

    descriptors["normalization_artifact"]["artifact_sha256"] = "0" * 64
    with pytest.raises(WorkerExecutionError, match="integrity"):
        _read_normalization_artifacts(tmp_path, descriptors, WorkerLimits())


def test_normalization_reader_rejects_reparse_point(tmp_path: Path) -> None:
    from academic_pdf_en_zh_reader.security.windows_worker import (
        WorkerExecutionError,
        _read_normalization_artifacts,
    )

    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"pdf")
    link = output / "normalized-source.pdf"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"creating symlinks is unavailable: {error}")
    artifact = b"{}\n"
    (output / "normalization.json").write_bytes(artifact)
    descriptors = {
        "normalized_pdf": _descriptor(NORMALIZATION_PDF_PATH, b"pdf"),
        "normalization_artifact": _descriptor(
            NORMALIZATION_ARTIFACT_PATH,
            artifact,
        ),
    }

    with pytest.raises((WorkerExecutionError, ProtocolError)):
        _read_normalization_artifacts(tmp_path, descriptors, WorkerLimits())
