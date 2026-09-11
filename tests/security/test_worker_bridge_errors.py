# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.extraction import api as extraction_api
from academic_pdf_en_zh_reader.normalization import api as normalization_api
from academic_pdf_en_zh_reader.preflight import api as preflight_api
from academic_pdf_en_zh_reader.qa import worker_bridge as qa_bridge
from academic_pdf_en_zh_reader.qa.persist import QaCommitError
from academic_pdf_en_zh_reader.rendering import worker_bridge as render_bridge
from academic_pdf_en_zh_reader.rendering.compose import CompositionError
from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.limits import WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest


def test_extraction_diagnostic_reports_code_location_not_private_content() -> None:
    from academic_pdf_en_zh_reader.extraction.page_objects import _mpt

    private_text = "PRIVATE_PAPER_CONTENT_DO_NOT_LOG"
    try:
        _mpt(private_text)
    except Exception as error:
        summary = windows_worker._extraction_failure_summary(error)
    else:
        pytest.fail("synthetic invalid coordinate should fail")
    assert "InvalidOperation" in summary
    assert "page_objects:" in summary
    assert private_text not in summary
    assert str(Path(__file__).parents[2]) not in summary
    assert len(summary) <= 512


def test_extraction_diagnostic_does_not_echo_arbitrary_exception_names() -> None:
    error_type = type("PRIVATE_CUSTOM_EXCEPTION", (Exception,), {})
    summary = windows_worker._extraction_failure_summary(
        error_type("PRIVATE_PAPER_CONTENT_DO_NOT_LOG")
    )
    assert "PRIVATE" not in summary


def _reported_error(code: str):
    def fail(*_args: object, **_kwargs: object) -> None:
        raise windows_worker.WorkerReportedError(code, "worker detail")

    return fail


def _cleanup_error(*_args: object, **_kwargs: object) -> None:
    raise windows_worker.SandboxCleanupError("simulated cleanup failure")


@pytest.mark.parametrize(
    ("worker_code", "expected_code"),
    [
        ("SANDBOX_CONTRACT_UNVERIFIED", "SANDBOX_CONTRACT_UNVERIFIED"),
        ("ATTACKER_CHOSEN_CODE", "RENDER_FAILED"),
    ],
)
def test_render_bridge_uses_a_closed_worker_error_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_code: str,
    expected_code: str,
) -> None:
    monkeypatch.setattr(render_bridge.os, "name", "nt")
    monkeypatch.setattr(windows_worker, "run_worker", _reported_error(worker_code))

    with pytest.raises(CompositionError) as captured:
        render_bridge._run_platform_worker(
            WorkerRequest("probe", "input.pdf"),
            tmp_path,
            limits=WorkerLimits(),
        )

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("worker_code", "expected_code"),
    [
        ("WORKER_LIMIT_EXCEEDED", "WORKER_LIMIT_EXCEEDED"),
        ("ATTACKER_CHOSEN_CODE", "QA_WORKER_FAILED"),
    ],
)
def test_qa_bridge_uses_a_closed_worker_error_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_code: str,
    expected_code: str,
) -> None:
    monkeypatch.setattr(qa_bridge.os, "name", "nt")
    monkeypatch.setattr(windows_worker, "run_worker", _reported_error(worker_code))

    with pytest.raises(QaCommitError) as captured:
        qa_bridge._run_platform_worker(
            WorkerRequest("probe", "input.pdf"),
            tmp_path,
            limits=WorkerLimits(),
        )

    assert str(captured.value) == expected_code


@pytest.mark.parametrize(
    ("bridge", "error_type", "error_code"),
    [
        (render_bridge, CompositionError, "SANDBOX_CONTRACT_UNVERIFIED"),
        (qa_bridge, QaCommitError, "SANDBOX_CONTRACT_UNVERIFIED"),
        (
            preflight_api,
            preflight_api._StableBridgeError,
            "SANDBOX_CONTRACT_UNVERIFIED",
        ),
        (
            extraction_api,
            extraction_api._StableBridgeError,
            "SANDBOX_CONTRACT_UNVERIFIED",
        ),
        (
            normalization_api,
            normalization_api._StableBridgeError,
            "SANDBOX_CONTRACT_UNVERIFIED",
        ),
    ],
)
def test_all_parent_bridges_elevate_worker_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bridge: object,
    error_type: type[Exception],
    error_code: str,
) -> None:
    monkeypatch.setattr(bridge.os, "name", "nt")  # type: ignore[attr-defined]
    monkeypatch.setattr(windows_worker, "run_worker", _cleanup_error)

    with pytest.raises(error_type) as captured:
        bridge._run_platform_worker(  # type: ignore[attr-defined]
            WorkerRequest("probe", "input.pdf"),
            tmp_path,
            limits=WorkerLimits(),
        )

    observed_code = getattr(captured.value, "code", str(captured.value))
    assert observed_code == error_code
