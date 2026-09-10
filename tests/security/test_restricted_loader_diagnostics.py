# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.security import restricted_loader_diagnostics as diagnostics
from tests.security import test_windows_worker_limits as worker_tests


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_smoke_reports_loader_failure_without_replacing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic_fails: bool
) -> None:
    original = worker_tests.WorkerExecutionError("isolated worker exited 0xC0000135")
    calls: list[bool] = []

    def fail_worker(*_args: object, **_kwargs: object) -> None:
        raise original

    def diagnostic() -> None:
        calls.append(True)
        if diagnostic_fails:
            raise OSError("diagnostic failure must not replace the worker error")

    monkeypatch.setattr(worker_tests, "run_worker", fail_worker)
    monkeypatch.setattr(
        worker_tests, "emit_restricted_loader_diagnostics", diagnostic, raising=False
    )
    with pytest.raises(worker_tests.WorkerExecutionError) as caught:
        worker_tests.test_worker_uses_restricted_token_and_enforced_job_limits(
            tmp_path, None
        )
    assert caught.value is original
    assert calls == [True]


def test_diagnostic_error_does_not_expose_exception_details(
    monkeypatch, capsys
) -> None:
    def fail():
        raise OSError("private-user-SID-and-environment")

    monkeypatch.setattr(diagnostics, "collect_restricted_loader_diagnostics", fail)
    diagnostics.emit_restricted_loader_diagnostics()
    output = capsys.readouterr().out
    assert '"diagnostic_error_type": "OSError"' in output
    assert "private-user-SID-and-environment" not in output


def test_diagnostics_output_is_bounded(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        diagnostics,
        "collect_restricted_loader_diagnostics",
        lambda: {"oversized": "x" * 20_000},
    )
    diagnostics.emit_restricted_loader_diagnostics()
    output = capsys.readouterr().out
    assert "OutputLimitExceeded" in output
    assert len(output) < 16_384


def test_snapshot_distinguishes_missing_file_and_access_denial(tmp_path) -> None:
    def denied(_path):
        return {"read": False, "execute": False}

    path = tmp_path / "module.dll"
    assert diagnostics._file_snapshot(path, denied)["exists"] is False
    path.write_bytes(b"known bytes")
    snapshot = diagnostics._file_snapshot(path, denied)
    assert snapshot["exists"] is True
    assert snapshot["size"] == 11
    assert len(snapshot["sha256"]) == 64
    assert snapshot["restricted_access"] == {"read": False, "execute": False}


@pytest.mark.parametrize("allowed", [False, True])
def test_access_check_returns_actual_boolean_and_frees_descriptor(monkeypatch, allowed):
    freed = []

    def descriptor(*args):
        args[-1]._obj.value = 1234
        return 0

    def access(*args):
        args[-1]._obj.value = int(allowed)
        return True

    monkeypatch.setattr(
        diagnostics.windows_worker,
        "_kernel32",
        SimpleNamespace(LocalFree=lambda value: freed.append(value.value)),
        raising=False,
    )
    result = diagnostics._file_access(
        Path("module.dll"),
        object(),
        SimpleNamespace(GetNamedSecurityInfoW=descriptor, AccessCheck=access),
    )
    assert result == {"read": allowed, "execute": allowed}
    assert freed == [1234]
