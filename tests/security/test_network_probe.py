# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import builtins
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest


def _network_request(monkeypatch: pytest.MonkeyPatch) -> WorkerRequest:
    monkeypatch.setattr(
        windows_worker, "_current_process_is_appcontainer", lambda: True
    )
    monkeypatch.setattr(
        windows_worker,
        "_resolve_prevalidated_appcontainer_path",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        windows_worker,
        "_current_process_has_zero_capability_appcontainer",
        lambda: True,
    )
    return WorkerRequest(
        "probe", "input.pdf", {"case": "network_connect", "port": 12345}
    )


def _socket_import(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    original = builtins.__import__

    def import_module(name: str, *args: object, **kwargs: object) -> object:
        if name == "socket":
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)


def test_worker_module_cold_import_does_not_initialize_winsock() -> None:
    source_root = Path(windows_worker.__file__).resolve().parents[2]
    code = f"""
import builtins
import sys
sys.path.insert(0, {str(source_root)!r})
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in ("socket", "_socket"):
        raise ImportError("WSAStartup failed: error code 10107")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from academic_pdf_en_zh_reader.security import windows_worker
assert "socket" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_network_probe_records_exact_winsock_initialization_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _network_request(monkeypatch)
    _socket_import(monkeypatch, ImportError("WSAStartup failed: error code 10107"))

    assert windows_worker._probe_case(request) == {
        "network_denied": True,
        "phase": "initialization",
        "error_code": 10107,
    }


@pytest.mark.parametrize(
    "error",
    [
        ModuleNotFoundError("No module named '_socket'"),
        ModuleNotFoundError("WSAStartup failed: error code 10107"),
        ImportError("DLL load failed while importing _socket"),
        ImportError("WSAStartup failed: error code 10091"),
        ImportError("WSAStartup failed: error code 10107 extra"),
        OSError(10107, "Winsock initialization failure"),
    ],
)
def test_network_probe_does_not_treat_broken_imports_as_isolation(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    request = _network_request(monkeypatch)
    _socket_import(monkeypatch, error)

    with pytest.raises(type(error)) as captured:
        windows_worker._probe_case(request)
    assert captured.value is error


def test_network_probe_requires_lpac_before_importing_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _network_request(monkeypatch)
    monkeypatch.setattr(
        windows_worker,
        "_current_process_has_zero_capability_appcontainer",
        lambda: False,
    )
    _socket_import(monkeypatch, AssertionError("socket import attempted before LPAC"))

    with pytest.raises(windows_worker.SandboxUnavailableError):
        windows_worker._probe_case(request)


@pytest.mark.parametrize("error_code", [10013, 10060, 10061])
def test_network_probe_only_accepts_explicit_connection_access_denial(
    monkeypatch: pytest.MonkeyPatch, error_code: int
) -> None:
    request = _network_request(monkeypatch)
    error = OSError(error_code, "simulated Winsock error")

    def connect(address: tuple[str, int], *, timeout: int) -> None:
        assert address == ("127.0.0.1", 12345)
        assert timeout == 1
        raise error

    _socket_import(monkeypatch, SimpleNamespace(create_connection=connect))
    if error_code != 10013:
        with pytest.raises(OSError) as captured:
            windows_worker._probe_case(request)
        assert captured.value is error
    else:
        assert windows_worker._probe_case(request) == {
            "network_denied": True,
            "phase": "connect",
            "error_code": 10013,
        }


def test_network_probe_does_not_pass_when_connection_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _network_request(monkeypatch)
    closed = []
    connection = SimpleNamespace(close=lambda: closed.append(True))
    _socket_import(
        monkeypatch, SimpleNamespace(create_connection=lambda *_a, **_k: connection)
    )

    assert windows_worker._probe_case(request) == {
        "network_denied": False,
        "phase": "connect",
        "error_code": None,
    }
    assert closed == [True]


@pytest.mark.parametrize(
    ("is_lpac", "evidence", "expected"),
    [
        (
            True,
            {"network_denied": True, "phase": "initialization", "error_code": 10107},
            True,
        ),
        (True, {"network_denied": True, "phase": "connect", "error_code": 10013}, True),
        (
            False,
            {"network_denied": True, "phase": "initialization", "error_code": 10107},
            False,
        ),
        (True, {"network_denied": True}, False),
        (
            True,
            {"network_denied": True, "phase": "connect", "error_code": 10013.0},
            False,
        ),
        (
            True,
            {"network_denied": True, "phase": "connect", "error_code": 10107},
            False,
        ),
        (
            True,
            {"network_denied": True, "phase": "initialization", "error_code": 10013},
            False,
        ),
        (
            True,
            {"network_denied": False, "phase": "connect", "error_code": 10013},
            False,
        ),
    ],
)
def test_parent_requires_lpac_and_exact_network_denial_evidence(
    is_lpac: bool, evidence: dict[str, object], expected: bool
) -> None:
    assert windows_worker._network_probe_denial_verified(is_lpac, evidence) is expected
