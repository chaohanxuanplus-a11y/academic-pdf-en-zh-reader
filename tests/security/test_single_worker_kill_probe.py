# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.security import windows_worker as worker
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS
from academic_pdf_en_zh_reader.security.worker_protocol import (
    WorkerResponse,
    encode_response,
)


def _ready_result() -> dict[str, object]:
    return {
        "ready": True,
        "process_id": 42,
        "appcontainer": True,
        "capability_count": 0,
        "enabled_privileges": ["SeChangeNotifyPrivilege"],
        "less_privileged_appcontainer": None,
        "less_privileged_appcontainer_query_supported": False,
    }


def test_probe_ready_closes_inherited_pipe_fds_while_worker_stays_alive() -> None:
    source_root = Path(worker.__file__).resolve().parents[2]
    code = f"""
import sys
sys.path.insert(0, {str(source_root)!r})
from dataclasses import replace
from academic_pdf_en_zh_reader.security import windows_worker as w
from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest
w._current_process_is_appcontainer = lambda: True
w._resolve_prevalidated_appcontainer_path = lambda *a, **k: None
w._current_process_security = lambda: (False, ["SeChangeNotifyPrivilege"], True, 0)
w._current_process_lpac_status = lambda: (None, False)
w._probe_case(
    WorkerRequest("probe", "input.pdf", {{"case": "single_worker_kill_on_close"}}),
    replace(w.DEFAULT_LIMITS, wall_time_seconds=5),
)
"""
    streams = {}
    with subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=0x08000000 if os.name == "nt" else 0,
    ) as process:
        readers = []
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            reader = threading.Thread(
                target=lambda name=name, stream=stream: streams.update(
                    {name: stream.read()}
                ),
                daemon=True,
            )
            reader.start()
            readers.append(reader)
        try:
            for reader in readers:
                reader.join(timeout=2)
                assert not reader.is_alive(), "ready pipe did not reach EOF"
            assert process.poll() is None
            assert json.loads(streams["stdout"])["result"]["ready"] is True
            assert streams["stderr"] == b""
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=2)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "not_ready",
        "stdout_open",
        "stderr_open",
        "overflow",
        "reader_error",
        "malformed",
        "wrong_pid",
        "not_lpac",
        "capability",
        "privilege",
    ],
)
def test_single_worker_ready_is_bounded_and_bound_to_the_lpac_process(invalid) -> None:
    ready = _ready_result()
    if invalid == "not_ready":
        ready["ready"] = False
    elif invalid == "wrong_pid":
        ready["process_id"] = 43
    elif invalid == "not_lpac":
        ready["less_privileged_appcontainer_query_supported"] = True
        ready["less_privileged_appcontainer"] = False
    elif invalid == "capability":
        ready["capability_count"] = 1
    elif invalid == "privilege":
        ready["enabled_privileges"] = ["SeDebugPrivilege"]
    stdout = {"data": encode_response(WorkerResponse.ok(ready), DEFAULT_LIMITS)}
    stderr = {"data": b""}
    if invalid == "overflow":
        stdout["overflow"] = True
    elif invalid == "reader_error":
        stderr["reader_error"] = "read failed"
    elif invalid == "malformed":
        stdout["data"] = b"not-json"
    waits = []
    stdout_thread = SimpleNamespace(
        join=lambda timeout: waits.append(timeout),
        is_alive=lambda: invalid == "stdout_open",
    )
    stderr_thread = SimpleNamespace(
        join=lambda timeout: waits.append(timeout),
        is_alive=lambda: invalid == "stderr_open",
    )
    args = (stdout_thread, stderr_thread, stdout, stderr, 42, DEFAULT_LIMITS)
    if invalid is None:
        assert worker._single_worker_probe_ready(*args).result == ready
    else:
        with pytest.raises(worker.WorkerExecutionError):
            worker._single_worker_probe_ready(*args)
    assert waits
    assert all(0 < value <= DEFAULT_LIMITS.wall_time_seconds for value in waits)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "dead_before",
        "wrong_job",
        "membership_error",
        "still_alive",
        "normal_exit",
        "still_active_exit_code",
        "close_failure",
    ],
)
def test_single_worker_kill_requires_job_close_to_preempt_natural_exit(
    monkeypatch: pytest.MonkeyPatch, invalid: str | None
) -> None:
    events = []

    class Kernel:
        def WaitForSingleObject(self, process, timeout):
            assert process == 222
            events.append(("wait", timeout))
            if timeout == 0:
                return 0 if invalid == "dead_before" else 258
            return 258 if invalid == "still_alive" else 0

        def IsProcessInJob(self, process, job, result):
            assert (process, job) == (222, 111)
            events.append("membership")
            result._obj.value = invalid != "wrong_job"
            return invalid != "membership_error"

        def GetExitCodeProcess(self, process, result):
            assert process == 222
            result._obj.value = (
                73
                if invalid == "normal_exit"
                else 259
                if invalid == "still_active_exit_code"
                else 0
            )
            return True

        def TerminateJobObject(self, *_args):
            raise AssertionError("termination must be caused by closing the Job")

    def close_job():
        events.append("close")
        if invalid == "close_failure":
            raise worker.SandboxCleanupError("Job handle close failed")

    monkeypatch.setattr(worker, "_kernel32", Kernel(), raising=False)
    monkeypatch.setattr(
        worker,
        "wintypes",
        SimpleNamespace(BOOL=ctypes.c_int, DWORD=ctypes.c_uint32),
        raising=False,
    )
    monkeypatch.setattr(
        worker, "_win_error", lambda label: worker.SandboxUnavailableError(label)
    )
    if invalid is None:
        result = worker._close_job_and_verify_worker(111, 222, close_job)
        assert result == {
            "verified": True,
            "scope": "single-worker-kill-on-close",
            "alive_before_close": True,
            "job_membership_verified": True,
            "exit_code": 0,
            "expected_natural_exit_code": 73,
        }
        assert events == [("wait", 0), "membership", "close", ("wait", 2000)]
    else:
        with pytest.raises(
            (worker.WorkerExecutionError, worker.SandboxUnavailableError)
        ):
            worker._close_job_and_verify_worker(111, 222, close_job)
        assert events.count("close") <= 1
        if invalid in {"dead_before", "wrong_job", "membership_error"}:
            assert "close" not in events
