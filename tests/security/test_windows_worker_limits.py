# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ctypes
import gc
import json
import os
import stat
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.input_copy import copy_untrusted_input
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.windows_worker import (
    SandboxCleanupError,
    SandboxUnavailableError,
    WorkerCpuLimitError,
    WorkerExecutionError,
    WorkerMemoryLimitError,
    WorkerOutputLimitError,
    WorkerTimeoutError,
    run_security_probe,
)
from academic_pdf_en_zh_reader.security.worker_protocol import (
    ProtocolError,
    WorkerRequest,
)
from tests.security.restricted_adapter_runtime import prepare_restricted_probe_runtime
from tests.security.restricted_loader_diagnostics import (
    emit_restricted_loader_diagnostics,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only sandbox")


def run_worker(
    request: WorkerRequest,
    workspace: Path,
    *,
    limits: WorkerLimits = DEFAULT_LIMITS,
):
    return windows_worker._run_restricted_worker_for_test(
        request,
        workspace,
        limits=limits,
    )


def _request(
    case: str,
    input_path: str = "input.pdf",
    **parameters: object,
) -> WorkerRequest:
    return WorkerRequest(
        operation="probe",
        input_path=input_path,
        parameters={"case": case, **parameters},
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied = copy_untrusted_input(source, temp_parent=tmp_path / "sandboxes")
    try:
        yield copied.root
    finally:
        copied.cleanup()


@pytest.fixture
def restricted_job_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Exercise the explicit non-AppContainer adapter in this test group."""

    monkeypatch.setattr(windows_worker, "_appcontainer_apis_available", lambda: False)
    with prepare_restricted_probe_runtime(tmp_path / "adapter-runtime") as runtime:
        monkeypatch.setattr(f"{__name__}.run_worker", runtime.run_probe)
        yield


def test_worker_uses_restricted_token_and_enforced_job_limits(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    try:
        result = run_worker(_request("inspect"), workspace)
    except WorkerExecutionError:
        # Diagnostics must never replace the original worker failure.
        with suppress(Exception):
            emit_restricted_loader_diagnostics()
        raise

    assert result.response.status == "ok"
    assert result.response.result["restricted_token"] is True
    assert set(result.response.result["enabled_privileges"]) <= {
        "SeChangeNotifyPrivilege"
    }
    assert result.provenance["restricted_token"] is True
    assert result.provenance["worker_executable"] == "pythonw.exe"
    token_origin = result.provenance["restricted_token_origin"]
    assert token_origin == "inherited_restricted_token_duplicated" or (
        "logon_sid" in token_origin
    )
    assert result.provenance["created_suspended_before_job_assignment"] is True
    assert result.provenance["kill_on_job_close"] is True
    assert result.provenance["active_process_limit"] == 1
    assert result.provenance["process_memory_limit"] is True
    assert result.provenance["job_memory_limit"] is True
    assert result.provenance["cpu_time_limit"] is True
    assert result.provenance["appcontainer_implemented"] is False
    assert result.provenance["network_isolation_implemented"] is False
    assert result.provenance["launcher"] == "CreateProcessAsUserW"
    assert result.provenance["handle_list_enforced"] is True
    assert result.provenance["inherited_handle_count"] == 3


def test_minimal_python_runtime_is_content_addressed_and_excludes_tooling(
    tmp_path: Path,
) -> None:
    runtime = windows_worker._copy_minimal_python_runtime(tmp_path)
    source, manifest, _source_fingerprint = windows_worker._python_runtime_manifest()
    path_config_relative = Path(
        f"python{windows_worker.sys.version_info.major}"
        f"{windows_worker.sys.version_info.minor}._pth"
    )
    path_config = runtime.root / path_config_relative
    path_config_size = path_config.stat().st_size
    path_config_hash = windows_worker._file_sha256(path_config)

    assert runtime.source == source
    assert runtime.fingerprint == windows_worker._runtime_manifest_fingerprint(
        (*manifest, (path_config_relative, path_config_size, path_config_hash))
    )
    assert runtime.file_count == len(manifest) + 1
    assert runtime.total_bytes == (
        sum(size for _path, size, _digest in manifest) + path_config_size
    )
    assert path_config.read_bytes() == b"Lib\nDLLs\n"
    assert (runtime.root / "python.exe").is_file()
    assert (runtime.root / "python312.dll").is_file()
    assert (runtime.root / "Lib" / "json" / "__init__.py").is_file()
    assert (runtime.root / "DLLs" / "_socket.pyd").is_file()
    for excluded in ("site-packages", "test", "idlelib", "tkinter"):
        assert not any(
            part.casefold() == excluded
            for path in runtime.root.rglob("*")
            for part in path.relative_to(runtime.root).parts
        )
    command = windows_worker._child_command(
        tmp_path,
        runtime.root / "python.exe",
    ).value
    assert str(runtime.root / "python.exe") in command
    assert " -I -S " in command


def test_appcontainer_child_path_mode_never_resolves_protected_ancestors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "AC" / "run"
    workspace.mkdir(parents=True)
    input_path = workspace / "input.pdf"
    input_path.write_bytes(b"pdf")

    def forbidden_resolve(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Path.resolve must not run in AppContainer child mode")

    monkeypatch.setattr(windows_worker.os, "getcwd", lambda: str(workspace))
    monkeypatch.setattr(Path, "resolve", forbidden_resolve)

    resolved = windows_worker._resolve_prevalidated_appcontainer_path("input.pdf")

    assert resolved == input_path
    with pytest.raises(ProtocolError):
        windows_worker._resolve_prevalidated_appcontainer_path("../outside.pdf")


def test_appcontainer_child_path_mode_still_rejects_reparse_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "AC" / "run"
    workspace.mkdir(parents=True)
    (workspace / "input.pdf").write_bytes(b"pdf")
    monkeypatch.setattr(windows_worker.os, "getcwd", lambda: str(workspace))
    monkeypatch.setattr(
        windows_worker,
        "_child_path_is_reparse",
        lambda path: path.name == "input.pdf",
    )

    with pytest.raises(ProtocolError, match="reparse"):
        windows_worker._resolve_prevalidated_appcontainer_path("input.pdf")


def test_handle_allowlist_probe_distinguishes_event_from_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = windows_worker._make_inheritable_probe_event()
    try:
        inherited = windows_worker._excluded_event_evidence(
            windows_worker._handle_value(event)
        )
    finally:
        windows_worker._close_handle(event)
    assert inherited["excluded"] is False
    assert inherited["event_handshake"] is True

    def invalid_handle(*_args: object) -> bool:
        raise OSError(-1073741816, "STATUS_INVALID_HANDLE")

    monkeypatch.setattr(
        windows_worker._kernel32,
        "GetHandleInformation",
        invalid_handle,
    )
    excluded = windows_worker._excluded_event_evidence(123456)
    assert excluded["excluded"] is True
    assert excluded["error_code"] == 0xC0000008


def test_process_limit_probe_treats_invalid_popen_handle_as_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidHandleProcess:
        def poll(self) -> int:
            raise OSError(-1073741816, "STATUS_INVALID_HANDLE")

        def terminate(self) -> None:
            raise AssertionError("invalid process handle must not be terminated")

    monkeypatch.setattr(
        windows_worker,
        "_current_process_is_appcontainer",
        lambda: False,
    )
    monkeypatch.setattr(
        windows_worker,
        "resolve_controlled_path",
        lambda _root, relative, **_kwargs: tmp_path / str(relative),
    )
    stdio_streams = []

    def fake_popen(*_args: object, **kwargs: object) -> InvalidHandleProcess:
        stdio_streams.extend([kwargs["stdin"], kwargs["stdout"], kwargs["stderr"]])
        return InvalidHandleProcess()

    monkeypatch.setattr(
        windows_worker.subprocess,
        "Popen",
        fake_popen,
    )

    result = windows_worker._probe_case(_request("spawn_child"))

    assert result["spawn_denied"] is True
    assert result["error_code"] == 0xC0000008
    assert {Path(stream.name).name for stream in stdio_streams} == {
        "probe-child-stdin.bin",
        "probe-child-stdout.bin",
        "probe-child-stderr.bin",
    }
    assert all(stream.closed for stream in stdio_streams)


def test_wall_clock_timeout_terminates_the_job(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    limits = WorkerLimits(wall_time_seconds=0.2, cpu_time_seconds=5.0)

    with pytest.raises(WorkerTimeoutError):
        run_worker(
            _request("sleep", seconds=5),
            workspace,
            limits=limits,
        )


def test_process_creation_is_denied_by_active_process_limit(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    result = run_worker(_request("spawn_child"), workspace)

    assert result.response.status == "ok"
    assert result.response.result["spawn_denied"] is True
    assert "ACTIVE_PROCESS_LIMIT" in {
        message["name"] for message in result.provenance["job_messages"]
    }


def test_kill_on_close_uses_retained_synchronize_handle(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    result = run_worker(
        _request("spawn_for_kill_probe"),
        workspace,
        limits=WorkerLimits(active_process_limit=2),
    )

    assert result.provenance["descendant_killed_on_job_close"] is True
    assert result.provenance["descendant_kill_evidence"]["verified"] is True
    assert result.provenance["descendant_kill_evidence"]["alive_before_close"] is True
    assert "reason" not in result.provenance["descendant_kill_evidence"]


def test_kill_on_close_rejects_a_descendant_that_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        windows_worker._kernel32,
        "OpenProcess",
        lambda *_args: 222,
    )
    monkeypatch.setattr(
        windows_worker._kernel32,
        "WaitForSingleObject",
        lambda _handle, _timeout: 0,
    )
    monkeypatch.setattr(
        windows_worker,
        "_close_handle",
        lambda handle: closed.append(int(handle)),
    )

    evidence = windows_worker._close_job_and_verify_descendant(111, 1234)

    assert evidence["verified"] is False
    assert evidence["alive_before_close"] is False
    assert "not alive before Job close" in str(evidence["reason"])
    assert closed == [111, 222]


def test_repeated_restricted_workers_do_not_leak_parent_handles(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    run_worker(_request("inspect"), workspace)
    before = windows_worker._current_process_handle_count()

    for _index in range(6):
        run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert after <= before + 2


def test_unrestricted_token_branch_passes_required_restricting_sids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = (windows_worker._SID_AND_ATTRIBUTES * 5)()
    restricting = windows_worker._RestrictingSidContext(
        entries=entries,
        labels=(
            "current_user",
            "logon_sid",
            "builtin_users",
            "everyone",
            "restricted_code",
        ),
        keepalive=(),
        local_allocations=(),
    )
    captured: dict[str, object] = {}

    def fake_create_restricted_token(*args: object) -> bool:
        captured["restricted_sid_count"] = args[6]
        captured["restricting_sids"] = args[7]
        output = ctypes.cast(args[8], ctypes.POINTER(windows_worker.wintypes.HANDLE))
        output.contents.value = 4242
        return True

    monkeypatch.setattr(
        windows_worker,
        "_build_restricting_sids",
        lambda _source: restricting,
    )
    monkeypatch.setattr(
        windows_worker._advapi32,
        "CreateRestrictedToken",
        fake_create_restricted_token,
    )

    token, labels = windows_worker._create_restricted_token_from_unrestricted_source(
        windows_worker.wintypes.HANDLE(111)
    )

    assert token.value == 4242
    assert labels == (
        "current_user",
        "logon_sid",
        "builtin_users",
        "everyone",
        "restricted_code",
    )
    assert captured["restricted_sid_count"] == 5
    assert bool(captured["restricting_sids"])


def test_lpac_workspace_dacl_is_limited_to_user_and_specific_package() -> None:
    appcontainer_sid = "S-1-15-2-1234"
    sddl = windows_worker._appcontainer_workspace_sddl(
        "S-1-5-21-1234-1001",
        appcontainer_sid,
    )

    assert f"(A;OICI;0x1301ff;;;{appcontainer_sid})" in sddl
    worker_mask = 0x1301FF
    assert worker_mask & 0x1FF == 0x1FF
    assert worker_mask & 0x10000
    assert worker_mask & 0x20000
    assert worker_mask & 0x100000
    assert worker_mask & (0x40000 | 0x80000) == 0
    assert f"(D;;SD;;;{appcontainer_sid})" in sddl
    assert f"(A;OICI;FA;;;{appcontainer_sid})" not in sddl
    assert ";;;S-1-15-2-1)" not in sddl
    assert ";;;S-1-15-2-2)" not in sddl


def test_cleanup_error_replaces_and_chains_the_active_worker_error() -> None:
    primary = WorkerExecutionError("primary worker failure")

    with pytest.raises(SandboxCleanupError, match="workspace cleanup failed") as caught:
        windows_worker._raise_cleanup_error(
            ["workspace cleanup failed"],
            active_error=primary,
        )

    assert caught.value.__cause__ is primary


def test_cleanup_lstat_failure_is_not_treated_as_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def deny_lstat(_path: Path) -> os.stat_result:
        raise PermissionError("simulated ACL denial")

    monkeypatch.setattr(Path, "lstat", deny_lstat)

    errors = windows_worker._remove_tree_and_verify(
        workspace,
        label="AppContainer workspace",
    )

    assert errors == ["AppContainer workspace inspection failed: simulated ACL denial"]


def test_cleanup_removes_worker_created_readonly_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    readonly = workspace / "attacker-readonly.bin"
    readonly.write_bytes(b"residual")
    readonly.chmod(stat.S_IREAD)
    assert readonly.lstat().st_file_attributes & 0x00000001

    errors = windows_worker._remove_tree_and_verify(
        workspace,
        label="AppContainer workspace",
    )

    assert errors == []
    with pytest.raises(FileNotFoundError):
        workspace.lstat()


def test_appcontainer_cleanup_retries_profile_delete_after_native_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_folder = tmp_path / "profile"
    workspace = profile_folder / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "residual.bin").write_bytes(b"residual")
    events: list[str] = []
    delete_results = iter((-2147024891, 0))
    original_remove = windows_worker._remove_tree_and_verify

    def delete_profile(_profile_name: str) -> int:
        events.append("delete-profile")
        return next(delete_results)

    def remove_tree(path: Path, *, label: str) -> list[str]:
        events.append(f"remove:{label}")
        return original_remove(path, label=label)

    monkeypatch.setattr(
        windows_worker._userenv,
        "DeleteAppContainerProfile",
        delete_profile,
    )
    monkeypatch.setattr(windows_worker, "_remove_tree_and_verify", remove_tree)

    errors, profile_deleted = windows_worker._cleanup_appcontainer_profile(
        "academicpdfworker.test",
        profile_folder=profile_folder,
        workspace=workspace,
    )

    assert errors == []
    assert profile_deleted is True
    assert events == [
        "delete-profile",
        "remove:AppContainer workspace",
        "remove:AppContainer profile folder",
        "delete-profile",
    ]


def test_appcontainer_cleanup_requires_profile_api_success_even_if_paths_are_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_folder = tmp_path / "profile"
    workspace = profile_folder / "workspace"
    workspace.mkdir(parents=True)
    delete_results = iter((-2147024891, -2147024891))

    monkeypatch.setattr(
        windows_worker._userenv,
        "DeleteAppContainerProfile",
        lambda _profile_name: next(delete_results),
    )

    errors, profile_deleted = windows_worker._cleanup_appcontainer_profile(
        "academicpdfworker.test",
        profile_folder=profile_folder,
        workspace=workspace,
    )

    assert profile_deleted is False
    assert errors == [
        "DeleteAppContainerProfile attempt 1 failed (HRESULT 0x80070005)",
        "DeleteAppContainerProfile attempt 2 failed (HRESULT 0x80070005)",
    ]
    with pytest.raises(FileNotFoundError):
        profile_folder.lstat()
    with pytest.raises(FileNotFoundError):
        workspace.lstat()


@pytest.mark.parametrize("remaining_root", ["profile", "workspace"])
def test_appcontainer_cleanup_requires_every_exact_root_to_be_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remaining_root: str,
) -> None:
    profile_folder = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    if remaining_root == "profile":
        profile_folder.mkdir()
    else:
        workspace.mkdir()

    monkeypatch.setattr(
        windows_worker._userenv,
        "DeleteAppContainerProfile",
        lambda _profile_name: 0,
    )

    errors, profile_deleted = windows_worker._cleanup_appcontainer_profile(
        "academicpdfworker.test",
        profile_folder=profile_folder,
        workspace=workspace,
    )

    assert profile_deleted is True
    expected_label = (
        "AppContainer profile folder"
        if remaining_root == "profile"
        else "AppContainer workspace"
    )
    assert errors == [f"{expected_label} still exists after profile cleanup"]


def test_job_cleanup_terminates_then_waits_for_zero_active_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    active_processes = iter((1, 0))

    def terminate(_job: object, _exit_code: int) -> bool:
        calls.append("terminate")
        return True

    def wait(_process: object, _timeout_ms: int) -> int:
        calls.append("wait")
        return 0

    def query(
        _job: object,
        information_class: int,
        information: object,
        _size: int,
        returned: object,
    ) -> bool:
        calls.append("query")
        assert information_class == 1
        accounting = ctypes.cast(
            information,
            ctypes.POINTER(windows_worker._JOBOBJECT_BASIC_ACCOUNTING_INFORMATION),
        )
        accounting.contents.ActiveProcesses = next(active_processes)
        returned_size = ctypes.cast(
            returned,
            ctypes.POINTER(windows_worker.wintypes.DWORD),
        )
        returned_size.contents.value = ctypes.sizeof(
            windows_worker._JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
        )
        return True

    monkeypatch.setattr(windows_worker._kernel32, "TerminateJobObject", terminate)
    monkeypatch.setattr(windows_worker._kernel32, "WaitForSingleObject", wait)
    monkeypatch.setattr(windows_worker._kernel32, "QueryInformationJobObject", query)
    monkeypatch.setattr(windows_worker.time, "sleep", lambda _seconds: None)

    assert windows_worker._terminate_job_and_wait(111, 222) == []
    assert calls == ["terminate", "wait", "query", "query"]


def test_production_worker_rejects_a_multi_process_job_limit(
    workspace: Path,
) -> None:
    request = WorkerRequest(
        operation="preflight",
        input_path="input.pdf",
        parameters={
            "policy_version": "1.0.0",
            "source_sha256": "a" * 64,
            "input_bytes": 16,
        },
    )

    with pytest.raises(SandboxUnavailableError, match="exactly one"):
        windows_worker._run_restricted_worker_for_test(
            request,
            workspace,
            limits=WorkerLimits(active_process_limit=2),
        )


def test_lpac_token_query_treats_unsupported_class_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedTokenInformation:
        def GetTokenInformation(self, *_args: object) -> bool:
            ctypes.set_last_error(87)
            return False

    monkeypatch.setattr(
        windows_worker,
        "_advapi32",
        UnsupportedTokenInformation(),
    )

    assert windows_worker._token_lpac_status(windows_worker.wintypes.HANDLE(123)) == (
        None,
        False,
    )


@pytest.mark.parametrize(
    ("low_part", "high_part", "attributes", "expected"),
    [
        (23, 0, 2, ["SeChangeNotifyPrivilege"]),
        (23, 0, 0, []),
        (22, 0, 0, []),
        (22, 0, 2, None),
        (24, 0, 2, None),
        (23, 1, 2, None),
    ],
)
def test_privilege_check_recognizes_only_exact_change_notify_luid_without_lookup(
    monkeypatch: pytest.MonkeyPatch,
    low_part: int,
    high_part: int,
    attributes: int,
    expected: list[str] | None,
) -> None:
    class TokenPrivileges(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", windows_worker.wintypes.DWORD),
            ("Privileges", windows_worker._LUID_AND_ATTRIBUTES * 1),
        ]

    payload = TokenPrivileges()
    payload.PrivilegeCount = 1
    payload.Privileges[0].Luid.LowPart = low_part
    payload.Privileges[0].Luid.HighPart = high_part
    payload.Privileges[0].Attributes = attributes
    lookups = []

    class LpacTokenApi:
        def GetTokenInformation(self, _token, info, buffer, _size, returned):
            assert info == 3
            returned._obj.value = ctypes.sizeof(payload)
            if buffer is not None:
                ctypes.memmove(buffer, ctypes.byref(payload), ctypes.sizeof(payload))
                return True
            return False

        def LookupPrivilegeNameW(self, *_args):
            lookups.append(True)
            ctypes.set_last_error(6)
            return False

    monkeypatch.setattr(windows_worker, "_advapi32", LpacTokenApi())
    if expected is None:
        with pytest.raises(SandboxUnavailableError, match="LookupPrivilegeNameW"):
            windows_worker._enabled_privileges(123)
        assert lookups == [True]
    else:
        assert windows_worker._enabled_privileges(123) == expected
        assert lookups == []


def test_change_notify_well_known_luid_matches_the_host_windows_api() -> None:
    # The lookup starts Windows RPC housekeeping: its idle cleanup can add a
    # thread/event after this test ends. Keep that unrelated activity out of
    # the process whose subsequent worker tests require zero handle growth.
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).with_name("privilege_value_probe.py")),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert json.loads(completed.stdout) == {"low_part": 23, "high_part": 0}


@pytest.mark.parametrize(
    ("lpac_status", "query_supported", "expected"),
    (
        (True, True, True),
        (False, True, False),
        (None, False, True),
    ),
)
def test_zero_capability_child_contract_uses_class_46_when_supported(
    monkeypatch: pytest.MonkeyPatch,
    lpac_status: bool | None,
    query_supported: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (False, [], True, 0),
    )
    monkeypatch.setattr(
        windows_worker,
        "_current_process_lpac_status",
        lambda: (lpac_status, query_supported),
    )

    assert (
        windows_worker._current_process_has_zero_capability_appcontainer() is expected
    )


@pytest.mark.parametrize(
    ("appcontainer", "capability_count"),
    ((False, 0), (True, 1)),
)
def test_lpac_child_contract_rejects_wrong_token_shape_before_class_46(
    monkeypatch: pytest.MonkeyPatch,
    appcontainer: bool,
    capability_count: int,
) -> None:
    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (False, [], appcontainer, capability_count),
    )
    monkeypatch.setattr(
        windows_worker,
        "_current_process_lpac_status",
        lambda: (_ for _ in ()).throw(AssertionError("class 46 was queried")),
    )

    assert windows_worker._current_process_has_zero_capability_appcontainer() is False


def test_ordinary_token_dispatch_does_not_depend_on_restricted_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = windows_worker.wintypes.HANDLE(101)
    restricted = windows_worker.wintypes.HANDLE(202)
    branch_called = False

    def is_token_restricted(token: object) -> bool:
        return getattr(token, "value", token) == restricted.value

    def create_from_unrestricted(
        token: object,
    ) -> tuple[object, tuple[str, ...]]:
        nonlocal branch_called
        branch_called = True
        assert getattr(token, "value", token) == source.value
        return restricted, (
            "current_user",
            "logon_sid",
            "builtin_users",
            "everyone",
            "restricted_code",
        )

    def enabled_privileges(token: object) -> list[str]:
        # An unrestricted source's names are unused. Query only the token
        # that must satisfy the worker's enabled-privilege allowlist.
        assert getattr(token, "value", token) == restricted.value
        return []

    monkeypatch.setattr(windows_worker, "_open_current_token", lambda _access: source)
    monkeypatch.setattr(windows_worker, "_enabled_privileges", enabled_privileges)
    monkeypatch.setattr(windows_worker, "_close_handle", lambda _handle: None)
    monkeypatch.setattr(
        windows_worker._advapi32,
        "IsTokenRestricted",
        is_token_restricted,
    )
    monkeypatch.setattr(
        windows_worker,
        "_create_restricted_token_from_unrestricted_source",
        create_from_unrestricted,
    )

    actual, privileges, origin = windows_worker._create_restricted_token()

    assert branch_called is True
    assert actual.value == restricted.value
    assert privileges == []
    assert (
        "RestrictingSids=current_user,logon_sid,builtin_users,everyone,restricted_code"
        in origin
    )


@pytest.mark.parametrize("failure", ["query_error", "unexpected", "not_restricted"])
def test_created_token_validation_failure_closes_both_owned_handles(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    source = windows_worker.wintypes.HANDLE(101)
    restricted = windows_worker.wintypes.HANDLE(202)
    closed: list[int] = []

    def enabled_privileges(token: object) -> list[str]:
        if getattr(token, "value", token) == source.value:
            return []
        if failure == "query_error":
            raise SandboxUnavailableError("privilege query failed")
        return ["SeDebugPrivilege"]

    monkeypatch.setattr(windows_worker, "_open_current_token", lambda _access: source)
    monkeypatch.setattr(windows_worker, "_enabled_privileges", enabled_privileges)
    monkeypatch.setattr(
        windows_worker,
        "_close_handle",
        lambda handle: closed.append(getattr(handle, "value", handle)),
    )
    monkeypatch.setattr(
        windows_worker._advapi32,
        "IsTokenRestricted",
        lambda token: (
            getattr(token, "value", token) == restricted.value
            and failure != "not_restricted"
        ),
    )
    monkeypatch.setattr(
        windows_worker,
        "_create_restricted_token_from_unrestricted_source",
        lambda _source: (restricted, ("current_user",)),
    )

    with pytest.raises(SandboxUnavailableError):
        windows_worker._create_restricted_token()

    assert closed == [source.value, restricted.value]


@pytest.mark.parametrize("unexpected_at", [None, "source", "duplicate"])
def test_inherited_token_checks_both_source_and_duplicate_privileges(
    monkeypatch: pytest.MonkeyPatch,
    unexpected_at: str | None,
) -> None:
    source = windows_worker.wintypes.HANDLE(101)
    duplicate = windows_worker.wintypes.HANDLE(202)
    queried: list[int] = []
    closed: list[int] = []

    def enabled_privileges(token: object) -> list[str]:
        value = getattr(token, "value", token)
        queried.append(value)
        role = "source" if value == source.value else "duplicate"
        return [
            "SeDebugPrivilege" if role == unexpected_at else "SeChangeNotifyPrivilege"
        ]

    def duplicate_token(*args):
        args[-1]._obj.value = duplicate.value
        return True

    monkeypatch.setattr(windows_worker, "_open_current_token", lambda _access: source)
    monkeypatch.setattr(windows_worker, "_enabled_privileges", enabled_privileges)
    monkeypatch.setattr(
        windows_worker,
        "_close_handle",
        lambda handle: closed.append(getattr(handle, "value", handle)),
    )
    monkeypatch.setattr(windows_worker._advapi32, "IsTokenRestricted", lambda _: True)
    monkeypatch.setattr(windows_worker._advapi32, "DuplicateTokenEx", duplicate_token)

    if unexpected_at is not None:
        with pytest.raises(SandboxUnavailableError, match="unexpected"):
            windows_worker._create_restricted_token()
    else:
        token, privileges, origin = windows_worker._create_restricted_token()
        assert token.value == duplicate.value
        assert privileges == ["SeChangeNotifyPrivilege"]
        assert origin == "inherited_restricted_token_duplicated"

    assert queried == ([101] if unexpected_at == "source" else [101, 202])
    assert closed == ([101, 202] if unexpected_at == "duplicate" else [101])


def test_create_process_failure_is_fail_closed_and_closes_handles(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    restricted_job_adapter: None,
) -> None:
    windows_worker._environment_block(workspace)
    before = windows_worker._current_process_handle_count()
    closed: list[int] = []
    original_close = windows_worker._close_handle

    def track_close(handle: object | None) -> None:
        if handle:
            value = getattr(handle, "value", handle)
            if isinstance(value, int):
                closed.append(value)
        original_close(handle)

    def fail_create_process(*_: object) -> bool:
        ctypes.set_last_error(5)
        return False

    def forbidden_fallback(*_: object, **__: object) -> None:
        raise AssertionError("ordinary subprocess fallback was attempted")

    monkeypatch.setattr(windows_worker, "_close_handle", track_close)
    monkeypatch.setattr(
        windows_worker,
        "_create_process_as_user",
        fail_create_process,
    )
    monkeypatch.setattr(windows_worker.subprocess, "Popen", forbidden_fallback)

    with pytest.raises(SandboxUnavailableError, match="CreateProcessAsUserW"):
        run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert len(set(closed)) >= 8
    assert after <= before + 1


@pytest.mark.parametrize(
    "report_terminate_failure",
    [False, True],
)
def test_job_assignment_failure_terminates_unassigned_suspended_process(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    restricted_job_adapter: None,
    report_terminate_failure: bool,
) -> None:
    run_worker(_request("inspect"), workspace)
    before = windows_worker._current_process_handle_count()
    original_create = windows_worker._create_process_as_user
    original_terminate = windows_worker._kernel32.TerminateProcess
    original_wait = windows_worker._kernel32.WaitForSingleObject
    create_calls = 0
    terminated: list[tuple[int, int]] = []
    waits: list[tuple[int, int, int]] = []

    def track_create(*args: object) -> bool:
        nonlocal create_calls
        create_calls += 1
        return original_create(*args)

    def fail_assignment(_job: object, _process: object) -> bool:
        ctypes.set_last_error(5)
        return False

    def track_terminate(process: object, exit_code: int) -> bool:
        terminated.append((windows_worker._handle_value(process), exit_code))
        terminated_ok = bool(original_terminate(process, exit_code))
        if report_terminate_failure:
            ctypes.set_last_error(5)
            return False
        return terminated_ok

    def track_wait(process: object, timeout_ms: int) -> int:
        result = int(original_wait(process, timeout_ms))
        waits.append((windows_worker._handle_value(process), timeout_ms, result))
        return result

    def forbidden_fallback(*_: object, **__: object) -> None:
        raise AssertionError("a weaker launcher fallback was attempted")

    monkeypatch.setattr(windows_worker, "_create_process_as_user", track_create)
    monkeypatch.setattr(
        windows_worker._kernel32,
        "AssignProcessToJobObject",
        fail_assignment,
    )
    monkeypatch.setattr(windows_worker._kernel32, "TerminateProcess", track_terminate)
    monkeypatch.setattr(windows_worker._kernel32, "WaitForSingleObject", track_wait)
    monkeypatch.setattr(windows_worker.subprocess, "Popen", forbidden_fallback)

    with pytest.raises(SandboxUnavailableError) as caught:
        run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert create_calls == 1
    assert len(terminated) == 1
    assert terminated[0][1] == 1
    assert waits == [(terminated[0][0], 5000, 0)]
    assert after == before
    assert "AssignProcessToJobObject" in str(caught.value)


def test_direct_process_cleanup_retries_until_exit_is_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminate_results = iter((False, True))
    wait_results = iter((258, 0))
    terminate_calls: list[tuple[object, int]] = []

    def terminate(process: object, exit_code: int) -> bool:
        terminate_calls.append((process, exit_code))
        ctypes.set_last_error(5)
        return next(terminate_results)

    monkeypatch.setattr(windows_worker._kernel32, "TerminateProcess", terminate)
    monkeypatch.setattr(
        windows_worker._kernel32,
        "WaitForSingleObject",
        lambda _process, _timeout_ms: next(wait_results),
    )

    errors, confirmed = windows_worker._terminate_process_and_wait(
        object(),
        label="test child",
    )

    assert errors == []
    assert confirmed is True
    assert len(terminate_calls) == 2


def test_direct_process_cleanup_reports_persistent_termination_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_terminate(_process: object, _exit_code: int) -> bool:
        ctypes.set_last_error(5)
        return False

    monkeypatch.setattr(
        windows_worker._kernel32,
        "TerminateProcess",
        fail_terminate,
    )
    monkeypatch.setattr(
        windows_worker._kernel32,
        "WaitForSingleObject",
        lambda _process, _timeout_ms: 258,
    )

    errors, confirmed = windows_worker._terminate_process_and_wait(
        object(),
        label="test child",
    )

    assert confirmed is False
    assert len(errors) == 4
    assert "TerminateProcess(test child) attempt 1" in errors[0]
    assert "WaitForSingleObject(test child) attempt 2" in errors[-1]


def test_final_cleanup_retries_an_unassigned_suspended_process(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    restricted_job_adapter: None,
) -> None:
    run_worker(_request("inspect"), workspace)
    before = windows_worker._current_process_handle_count()
    original_cleanup = windows_worker._terminate_process_and_wait
    cleanup_calls = 0

    def fail_assignment(_job: object, _process: object) -> bool:
        ctypes.set_last_error(5)
        return False

    def staged_cleanup(
        process: object,
        *,
        label: str,
    ) -> tuple[list[str], bool]:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            return ["initial termination attempts failed"], False
        return original_cleanup(process, label=label)

    monkeypatch.setattr(
        windows_worker._kernel32,
        "AssignProcessToJobObject",
        fail_assignment,
    )
    monkeypatch.setattr(
        windows_worker,
        "_terminate_process_and_wait",
        staged_cleanup,
    )

    with pytest.raises(SandboxUnavailableError, match="AssignProcessToJobObject"):
        run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert cleanup_calls == 2
    assert after == before


def test_second_reader_start_failure_preserves_handle_ownership(
    workspace: Path,
    restricted_job_adapter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_worker(_request("inspect"), workspace)
    before = windows_worker._current_process_handle_count()
    original_start = windows_worker.threading.Thread.start
    starts = 0

    def fail_second_start(thread: object) -> None:
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("stderr reader start failed")
        original_start(thread)  # type: ignore[arg-type]

    monkeypatch.setattr(
        windows_worker.threading.Thread,
        "start",
        fail_second_start,
    )

    with pytest.raises(RuntimeError, match="stderr reader start failed"):
        run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert after == before


def test_appcontainer_create_process_failure_has_no_weaker_launcher_fallback(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows_worker._environment_block(workspace)
    before = windows_worker._current_process_handle_count()
    fake_appcontainer = SimpleNamespace(
        workspace=workspace,
        profile_folder=workspace,
        runtime_root=Path(windows_worker.__file__).resolve().parents[2],
        python_runtime_root=Path(windows_worker.sys.base_prefix),
        python_runtime_source=Path(windows_worker.sys.base_prefix),
        python_runtime_fingerprint="test-fingerprint",
        python_runtime_file_count=1,
        python_runtime_bytes=1,
        profile_mode="ephemeral_profile",
        profile_creation_hresult=0,
        cleanup=lambda: [],
    )
    attributes_cleaned = False

    def fake_attributes(*_args: object) -> SimpleNamespace:
        def cleanup() -> None:
            nonlocal attributes_cleaned
            attributes_cleaned = True

        return SimpleNamespace(
            attribute_list=windows_worker.wintypes.LPVOID(1),
            inherited_handle_values=(1, 2, 3),
            all_application_packages_policy=windows_worker.wintypes.DWORD(1),
            cleanup=cleanup,
        )

    def fail_appcontainer_create(*_args: object) -> bool:
        ctypes.set_last_error(5)
        return False

    def forbidden_launcher(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a weaker launcher fallback was attempted")

    monkeypatch.setattr(
        windows_worker,
        "_prepare_appcontainer",
        lambda _workspace, _request: fake_appcontainer,
    )
    monkeypatch.setattr(
        windows_worker,
        "_prepare_process_attributes",
        fake_attributes,
    )
    monkeypatch.setattr(windows_worker, "_create_process", fail_appcontainer_create)
    monkeypatch.setattr(
        windows_worker,
        "_create_process_as_user",
        forbidden_launcher,
    )
    monkeypatch.setattr(
        windows_worker,
        "_create_restricted_token",
        forbidden_launcher,
    )
    monkeypatch.setattr(windows_worker.subprocess, "Popen", forbidden_launcher)

    with pytest.raises(
        SandboxUnavailableError,
        match=r"CreateProcessW\(AppContainer\).*zero capabilities",
    ):
        windows_worker.run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert attributes_cleaned is True
    assert after <= before + 1


def test_production_entry_fails_closed_when_appcontainer_apis_are_missing(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_launcher(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("production attempted a weaker process launcher")

    monkeypatch.setattr(
        windows_worker,
        "_appcontainer_apis_available",
        lambda: False,
    )
    monkeypatch.setattr(windows_worker, "_create_process", forbidden_launcher)
    monkeypatch.setattr(
        windows_worker,
        "_create_process_as_user",
        forbidden_launcher,
    )
    monkeypatch.setattr(
        windows_worker,
        "_create_restricted_token",
        forbidden_launcher,
    )
    monkeypatch.setattr(windows_worker.subprocess, "Popen", forbidden_launcher)

    with pytest.raises(SandboxUnavailableError, match="AppContainer APIs"):
        windows_worker.run_worker(_request("inspect"), workspace)


def test_cpu_and_memory_abuse_are_terminated(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    cpu_control = run_worker(
        _request("busy_for", seconds=0.05),
        workspace,
        limits=WorkerLimits(cpu_time_seconds=1.0, wall_time_seconds=2.0),
    )
    assert cpu_control.response.result["cpu_seconds"] == 0.05

    with pytest.raises(WorkerCpuLimitError):
        run_worker(
            _request("busy_loop"),
            workspace,
            limits=WorkerLimits(
                cpu_time_seconds=0.2,
                wall_time_seconds=10.0,
            ),
        )

    memory_control = run_worker(
        _request("allocate", bytes=1024 * 1024, seconds=0),
        workspace,
        limits=WorkerLimits(
            process_memory_bytes=128 * 1024 * 1024,
            job_memory_bytes=128 * 1024 * 1024,
            wall_time_seconds=2.0,
        ),
    )
    assert memory_control.response.result["allocated"] == 1024 * 1024

    with pytest.raises(WorkerMemoryLimitError):
        run_worker(
            _request("allocate", bytes=512 * 1024 * 1024),
            workspace,
            limits=WorkerLimits(
                process_memory_bytes=128 * 1024 * 1024,
                job_memory_bytes=128 * 1024 * 1024,
                wall_time_seconds=3.0,
            ),
        )


@pytest.mark.parametrize(
    ("worker_request", "limits", "expected_error"),
    [
        (
            _request("sleep", seconds=5),
            WorkerLimits(wall_time_seconds=0.2, cpu_time_seconds=5.0),
            WorkerTimeoutError,
        ),
        (
            _request("busy_loop"),
            WorkerLimits(cpu_time_seconds=0.2, wall_time_seconds=10.0),
            WorkerCpuLimitError,
        ),
        (
            _request("allocate", bytes=512 * 1024 * 1024),
            WorkerLimits(
                process_memory_bytes=128 * 1024 * 1024,
                job_memory_bytes=128 * 1024 * 1024,
                wall_time_seconds=3.0,
            ),
            WorkerMemoryLimitError,
        ),
        (
            _request("oversize_output"),
            WorkerLimits(),
            WorkerOutputLimitError,
        ),
    ],
    ids=("timeout", "cpu", "memory", "output"),
)
def test_exceptional_worker_paths_have_zero_parent_handle_growth(
    workspace: Path,
    restricted_job_adapter: None,
    worker_request: WorkerRequest,
    limits: WorkerLimits,
    expected_error: type[Exception],
) -> None:
    run_worker(_request("inspect"), workspace)
    # This count covers the entire pytest process, not just worker-owned handles.
    # Settle finalizers first; unrelated handles may close, but no growth is allowed.
    gc.collect()
    before = windows_worker._current_process_handle_count()

    with pytest.raises(expected_error):
        run_worker(worker_request, workspace, limits=limits)

    gc.collect()
    after = windows_worker._current_process_handle_count()
    assert after <= before, f"parent handle count grew: {before} -> {after}"


def test_repeated_output_limit_failures_do_not_accumulate_handles(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    run_worker(_request("inspect"), workspace)
    gc.collect()
    before = windows_worker._current_process_handle_count()

    for _index in range(5):
        with pytest.raises(WorkerOutputLimitError):
            run_worker(_request("oversize_output"), workspace)
        gc.collect()
        after = windows_worker._current_process_handle_count()
        assert after <= before, f"parent handle count grew: {before} -> {after}"
        # An earlier drop must not provide a cushion for a later leaked handle.
        before = after


@pytest.mark.parametrize("after", [289, 291, 292])
def test_exceptional_handle_growth_assertion_rejects_even_one_new_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after: int,
) -> None:
    counts = iter((291, after))
    limits = WorkerLimits(wall_time_seconds=0.2, cpu_time_seconds=5.0)
    request = _request("sleep", seconds=5)

    def fake_worker(worker_request, _workspace, **kwargs):
        if worker_request.parameters["case"] == "inspect":
            return None
        assert worker_request == request
        assert kwargs["limits"] is limits
        raise WorkerTimeoutError("expected timeout")

    monkeypatch.setitem(globals(), "run_worker", fake_worker)
    monkeypatch.setattr(
        windows_worker, "_current_process_handle_count", lambda: next(counts)
    )
    arguments = (tmp_path, None, request, limits, WorkerTimeoutError)
    if after > 291:
        with pytest.raises(
            AssertionError, match="parent handle count grew: 291 -> 292"
        ):
            test_exceptional_worker_paths_have_zero_parent_handle_growth(*arguments)
    else:
        test_exceptional_worker_paths_have_zero_parent_handle_growth(*arguments)


@pytest.mark.parametrize(
    "observations",
    [(289,) * 5, (291,) * 5, (291, 291, 292, 291, 291), (289, 290, 290, 290, 290)],
)
def test_repeated_handle_growth_assertion_rejects_a_transient_one_handle_increase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observations: tuple[int, ...],
) -> None:
    counts = iter((291, *observations))

    def fake_worker(worker_request, _workspace, **_kwargs):
        if worker_request.parameters["case"] == "inspect":
            return None
        assert worker_request.parameters["case"] == "oversize_output"
        raise WorkerOutputLimitError("expected oversized output")

    monkeypatch.setitem(globals(), "run_worker", fake_worker)
    monkeypatch.setattr(
        windows_worker, "_current_process_handle_count", lambda: next(counts)
    )
    samples = (291, *observations)
    if any(right > left for left, right in zip(samples, samples[1:], strict=False)):
        with pytest.raises(AssertionError, match="parent handle count grew"):
            test_repeated_output_limit_failures_do_not_accumulate_handles(
                tmp_path, None
            )
    else:
        test_repeated_output_limit_failures_do_not_accumulate_handles(tmp_path, None)


@pytest.mark.parametrize(
    ("cleanup_failure_case", "failed_check"),
    [
        ("sleep", "wall_clock_limit"),
        ("busy_loop", "cpu_time_limit"),
        ("allocate_limit", "memory_limit"),
        ("oversize_output", "bounded_versioned_json"),
        ("single_worker_kill_on_close", "kill_on_job_close"),
    ],
)
def test_probe_rejects_expected_limit_with_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_failure_case: str,
    failed_check: str,
) -> None:
    def expected_error(error: WorkerExecutionError, case: str) -> None:
        if cleanup_failure_case == case:
            raise SandboxCleanupError(
                "simulated AppContainer cleanup failure"
            ) from error
        raise error

    def fake_worker(
        request: WorkerRequest,
        _workspace: Path,
        *,
        limits: WorkerLimits = DEFAULT_LIMITS,
    ) -> windows_worker.WorkerRunResult:
        case = str(request.parameters.get("case"))
        provenance: dict[str, object] = {
            "appcontainer_cleanup_verified": True,
            "appcontainer_lpac_policy_applied": True,
        }
        result: dict[str, object] = {}
        if case == "inspect":
            result = {
                "appcontainer": True,
                "capability_count": 0,
                "less_privileged_appcontainer": None,
                "less_privileged_appcontainer_query_supported": False,
                "restricted_token": True,
                "enabled_privileges": [],
            }
        elif case == "outside_access":
            result = {"outside_access_denied": True}
        elif case == "network_connect":
            result = {
                "network_denied": True,
                "phase": "connect",
                "error_code": 10013,
            }
        elif case == "parent_process_access":
            result = {"access": {"vm_write": {"denied": True}}}
        elif case == "excluded_inheritable_handle":
            result = {"excluded": True}
        elif case == "spawn_child":
            result = {"spawn_denied": True}
            provenance["job_messages"] = [{"name": "ACTIVE_PROCESS_LIMIT"}]
        elif case == "single_worker_kill_on_close":
            if cleanup_failure_case == case:
                raise SandboxCleanupError("simulated AppContainer cleanup failure")
            provenance["worker_killed_on_job_close"] = True
            provenance["worker_kill_on_close_evidence"] = {
                "verified": True,
                "scope": "single-worker-kill-on-close",
            }
        elif case == "sleep":
            expected_error(WorkerTimeoutError("expected timeout"), case)
        elif case == "busy_for":
            result = {"cpu_seconds": 0.05}
        elif case == "busy_loop":
            expected_error(
                WorkerCpuLimitError(
                    "expected CPU limit",
                    [{"name": "END_OF_PROCESS_TIME"}],
                ),
                case,
            )
        elif (
            case == "allocate" and request.parameters.get("bytes") == 384 * 1024 * 1024
        ):
            expected_error(
                WorkerMemoryLimitError(
                    "expected memory limit",
                    [{"name": "PROCESS_MEMORY_LIMIT"}],
                ),
                "allocate_limit",
            )
        elif case == "allocate":
            result = {"allocated": request.parameters["bytes"]}
        elif case == "oversize_output":
            expected_error(WorkerOutputLimitError("expected output limit"), case)
        return windows_worker.WorkerRunResult(
            response=windows_worker.WorkerResponse.ok(result),
            provenance=provenance,
            exit_code=0,
        )

    monkeypatch.setattr(windows_worker, "run_worker", fake_worker)
    monkeypatch.setattr(windows_worker, "_current_process_handle_count", lambda: 200)

    probe = run_security_probe()

    assert probe["passed"] is False
    assert probe["minimum_contract"][failed_check] is False
    assert probe["minimum_contract"]["less_privileged_appcontainer"] is True
    assert probe["minimum_contract"]["appcontainer_cleanup"] is False
    assert any(
        "SandboxCleanupError: simulated AppContainer cleanup failure" in error
        for error in probe["errors"]
    ), probe["errors"][0] if probe["errors"] else repr(probe)


@pytest.mark.parametrize("case", ["oversize_output", "malformed_output"])
def test_invalid_worker_output_is_rejected(
    workspace: Path,
    case: str,
    restricted_job_adapter: None,
) -> None:
    error = (
        WorkerOutputLimitError if case == "oversize_output" else WorkerExecutionError
    )
    with pytest.raises(error):
        run_worker(_request(case), workspace)


def test_full_security_probe_is_truthful_and_fail_closed() -> None:
    probe = run_security_probe()

    print(json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True))
    first_error = probe["errors"][0] if probe["errors"] else repr(probe)
    assert probe["minimum_contract"]["sha256_recomputed"] is True, first_error
    assert probe["minimum_contract"]["copy_size_verified"] is True, first_error
    assert probe["provenance"]["subprocess_fallback"] is False
    if probe["passed"] is not True:
        pytest.fail("security probe failed; full report is above", pytrace=False)
    assert all(probe["minimum_contract"].values())
    assert probe["minimum_contract"]["less_privileged_appcontainer"] is True
    assert probe["provenance"]["appcontainer_implemented"] is True
    assert probe["provenance"]["appcontainer_lpac"] is True
    assert probe["provenance"]["network_isolation_implemented"] is True


def test_appcontainer_process_attributes_apply_lpac_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[tuple[int, int | None]] = []

    class FakeKernel32:
        def InitializeProcThreadAttributeList(
            self,
            attribute_list: object,
            count: int,
            _flags: int,
            size: object,
        ) -> bool:
            assert count == 3
            size._obj.value = 512  # type: ignore[attr-defined]
            return attribute_list is not None

        def UpdateProcThreadAttribute(
            self,
            _attribute_list: object,
            _flags: int,
            attribute: int,
            value: object,
            _size: int,
            _previous: object,
            _return_size: object,
        ) -> bool:
            policy = None
            if attribute == 0x0002000F:
                policy = ctypes.cast(
                    value,
                    ctypes.POINTER(windows_worker.wintypes.DWORD),
                ).contents.value
            updates.append((attribute, policy))
            return True

        def DeleteProcThreadAttributeList(self, _attributes: object) -> None:
            return None

    monkeypatch.setattr(windows_worker, "_kernel32", FakeKernel32())
    appcontainer = SimpleNamespace(sid=windows_worker.wintypes.LPVOID(1234))

    attributes = windows_worker._prepare_process_attributes(
        (windows_worker.wintypes.HANDLE(1),) * 3,
        appcontainer,
    )
    try:
        assert updates == [
            (0x00020002, None),
            (0x00020009, None),
            (0x0002000F, 1),
        ]
        assert getattr(attributes, "all_application_packages_policy", None) is not None
    finally:
        attributes.cleanup()
