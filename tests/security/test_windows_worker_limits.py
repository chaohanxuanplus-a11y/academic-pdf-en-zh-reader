# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.input_copy import copy_untrusted_input
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.windows_worker import (
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
def restricted_job_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the explicit non-AppContainer adapter in this test group."""

    monkeypatch.setattr(windows_worker, "_appcontainer_apis_available", lambda: False)


def test_worker_uses_restricted_token_and_enforced_job_limits(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    result = run_worker(_request("inspect"), workspace)

    assert result.response.status == "ok"
    assert result.response.result["restricted_token"] is True
    assert set(result.response.result["enabled_privileges"]) <= {
        "SeChangeNotifyPrivilege"
    }
    assert result.provenance["restricted_token"] is True
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
    source, manifest, fingerprint = windows_worker._python_runtime_manifest()

    assert runtime.source == source
    assert runtime.fingerprint == fingerprint
    assert runtime.file_count == len(manifest)
    assert runtime.total_bytes == sum(size for _path, size, _digest in manifest)
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
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        windows_worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: InvalidHandleProcess(),
    )

    result = windows_worker._probe_case(_request("spawn_child"))

    assert result["spawn_denied"] is True
    assert result["error_code"] == 0xC0000008


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
    assert "reason" not in result.provenance["descendant_kill_evidence"]


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
    entries = (windows_worker._SID_AND_ATTRIBUTES * 4)()
    restricting = windows_worker._RestrictingSidContext(
        entries=entries,
        labels=(
            "current_user",
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
        "builtin_users",
        "everyone",
        "restricted_code",
    )
    assert captured["restricted_sid_count"] == 4
    assert bool(captured["restricting_sids"])


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
            "builtin_users",
            "everyone",
            "restricted_code",
        )

    monkeypatch.setattr(windows_worker, "_open_current_token", lambda _access: source)
    monkeypatch.setattr(windows_worker, "_enabled_privileges", lambda _token: [])
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
        "RestrictingSids=current_user,builtin_users,everyone,restricted_code" in origin
    )


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


def test_job_assignment_failure_terminates_unassigned_suspended_process(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    restricted_job_adapter: None,
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
        return bool(original_terminate(process, exit_code))

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

    with pytest.raises(SandboxUnavailableError, match="AssignProcessToJobObject"):
        run_worker(_request("inspect"), workspace)

    after = windows_worker._current_process_handle_count()
    assert create_calls == 1
    assert len(terminated) == 1
    assert terminated[0][1] == 1
    assert waits == [(terminated[0][0], 5000, 0)]
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
    before = windows_worker._current_process_handle_count()

    with pytest.raises(expected_error):
        run_worker(worker_request, workspace, limits=limits)

    after = windows_worker._current_process_handle_count()
    assert after == before


def test_repeated_output_limit_failures_do_not_accumulate_handles(
    workspace: Path,
    restricted_job_adapter: None,
) -> None:
    run_worker(_request("inspect"), workspace)
    before = windows_worker._current_process_handle_count()

    for _index in range(5):
        with pytest.raises(WorkerOutputLimitError):
            run_worker(_request("oversize_output"), workspace)
        assert windows_worker._current_process_handle_count() == before


@pytest.mark.parametrize(
    ("noted_case", "failed_check"),
    [
        ("sleep", "wall_clock_limit"),
        ("busy_loop", "cpu_time_limit"),
        ("allocate_limit", "memory_limit"),
        ("oversize_output", "bounded_versioned_json"),
    ],
)
def test_probe_rejects_expected_limit_with_cleanup_failure_note(
    monkeypatch: pytest.MonkeyPatch,
    noted_case: str,
    failed_check: str,
) -> None:
    def expected_error(error: WorkerExecutionError, case: str) -> None:
        if noted_case == case:
            error.add_note("simulated AppContainer cleanup failure")
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
        }
        result: dict[str, object] = {}
        if case == "inspect":
            result = {
                "appcontainer": True,
                "capability_count": 0,
                "restricted_token": True,
                "enabled_privileges": [],
            }
        elif case == "outside_access":
            result = {"outside_access_denied": True}
        elif case == "network_connect":
            result = {"network_denied": True}
        elif case == "parent_process_access":
            result = {"access": {"vm_write": {"denied": True}}}
        elif case == "excluded_inheritable_handle":
            result = {"excluded": True}
        elif case == "spawn_child":
            result = {"spawn_denied": True}
            provenance["job_messages"] = [{"name": "ACTIVE_PROCESS_LIMIT"}]
        elif case == "spawn_for_kill_probe":
            provenance["descendant_killed_on_job_close"] = True
            provenance["descendant_kill_evidence"] = {"verified": True}
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
    assert probe["minimum_contract"]["appcontainer_cleanup"] is False
    assert any(
        f"{failed_check} teardown failed: simulated AppContainer cleanup failure"
        in error
        for error in probe["errors"]
    )


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

    assert probe["minimum_contract"]["sha256_recomputed"] is True
    assert probe["minimum_contract"]["copy_size_verified"] is True
    assert probe["provenance"]["subprocess_fallback"] is False
    profile_blocked = any(
        "CreateAppContainerProfile" in error and "0x80070002" in error
        for error in probe["errors"]
    )
    if profile_blocked:
        assert probe["passed"] is False
        assert probe["provenance"]["appcontainer_api_available"] is True
        assert probe["provenance"]["appcontainer_launch_verified"] is False
    else:
        assert probe["passed"] is True, probe
        assert all(probe["minimum_contract"].values())
        assert probe["provenance"]["appcontainer_implemented"] is True
        assert probe["provenance"]["network_isolation_implemented"] is True
