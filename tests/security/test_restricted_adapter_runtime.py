# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security import windows_worker as worker
from academic_pdf_en_zh_reader.security.input_copy import copy_untrusted_input
from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest
from tests.security.restricted_adapter_runtime import prepare_restricted_probe_runtime
from tests.security.restricted_loader_diagnostics import _GenericMapping

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only sandbox")


@pytest.fixture
def workspace(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied = copy_untrusted_input(source, temp_parent=tmp_path / "sandboxes")
    try:
        yield copied.root
    finally:
        copied.cleanup()


@contextmanager
def _restricted_access():
    api = worker._advapi32
    pointer = ctypes.POINTER(wintypes.LPVOID)
    api.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        pointer,
        pointer,
        pointer,
        pointer,
        pointer,
    ]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.AccessCheck.argtypes = [
        wintypes.LPVOID,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(_GenericMapping),
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.BOOL),
    ]
    api.AccessCheck.restype = wintypes.BOOL
    token, _privileges, _origin = worker._create_restricted_token()
    impersonation = wintypes.HANDLE()
    try:
        assert api.DuplicateTokenEx(
            token, 0x0008, None, 2, 2, ctypes.byref(impersonation)
        )

        def access(path: Path, mask: int) -> bool:
            descriptor = wintypes.LPVOID()
            assert (
                api.GetNamedSecurityInfoW(
                    str(path), 1, 7, None, None, None, None, ctypes.byref(descriptor)
                )
                == 0
            )
            try:
                mapping = _GenericMapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
                privileges = ctypes.create_string_buffer(4096)
                length = wintypes.DWORD(ctypes.sizeof(privileges))
                granted = wintypes.DWORD()
                allowed = wintypes.BOOL()
                assert api.AccessCheck(
                    descriptor,
                    impersonation,
                    mask,
                    ctypes.byref(mapping),
                    privileges,
                    ctypes.byref(length),
                    ctypes.byref(granted),
                    ctypes.byref(allowed),
                )
                return bool(allowed.value)
            finally:
                worker._kernel32.LocalFree(descriptor)

        yield access
    finally:
        worker._close_handle(impersonation)
        worker._close_handle(token)


def _security_descriptor(path: Path) -> bytes:
    api = worker._advapi32
    api.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetFileSecurityW.restype = wintypes.BOOL
    size = wintypes.DWORD()
    api.GetFileSecurityW(str(path), 7, None, 0, ctypes.byref(size))
    assert size.value
    descriptor = ctypes.create_string_buffer(size.value)
    assert api.GetFileSecurityW(str(path), 7, descriptor, size, ctypes.byref(size))
    return descriptor.raw


def _deny_restricted_reads(root: Path) -> None:
    """Deny only this fresh fixture; never change the real base or its ancestors."""
    api = worker._advapi32
    api.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    api.SetFileSecurityW.restype = wintypes.BOOL
    descriptor = wintypes.LPVOID()
    owner = worker._current_user_sid()
    sddl = f"D:P(D;OICI;FRFX;;;RC)(A;OICI;FA;;;{owner})"
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    )
    try:
        for path in (root, *root.rglob("*")):
            assert api.SetFileSecurityW(str(path), 0x80000004, descriptor)
    finally:
        worker._kernel32.LocalFree(descriptor)


def test_adapter_copies_unreadable_base_without_mutating_original(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, manifest, fingerprint = worker._python_runtime_manifest()
    originals = (original, *(original / relative for relative, _, _ in manifest))
    original_security = {path: _security_descriptor(path) for path in originals}
    original_hashes = {
        relative: worker._file_sha256(original / relative)
        for relative, _size, _digest in manifest
    }
    fixture_parent = tmp_path / "unreadable-base-fixture"
    fixture_parent.mkdir()
    source = worker._copy_minimal_python_runtime(fixture_parent).root
    _deny_restricted_reads(source)
    source_security = _security_descriptor(source)
    monkeypatch.setattr(
        worker, "_python_executable", lambda: str(source / "python.exe")
    )
    monkeypatch.setattr(
        worker, "_PYTHON_RUNTIME_MANIFEST_CACHE", (source, manifest, fingerprint)
    )
    launched = []
    original_create = worker._create_process_as_user

    def checked_create(token, command, *args):
        # Check before CreateProcess: the child must not select the denied base.
        assert str(source / "pythonw.exe") not in command.value
        assert str(runtime.root) in command.value
        assert " -I -S " in command.value
        launched.append(command.value)
        return original_create(token, command, *args)

    with _restricted_access() as access:
        assert access(source, 0x120089) is False
        assert access(source / "pythonw.exe", 0x1200A0) is False
        request = WorkerRequest(
            operation="probe", input_path="input.pdf", parameters={"case": "inspect"}
        )
        with pytest.raises(
            (worker.WorkerExecutionError, worker.SandboxUnavailableError)
        ) as denied:
            worker._run_restricted_worker_for_test(request, workspace)
        print(f"UNREADABLE_BASELINE: {type(denied.value).__name__}: {denied.value}")
        monkeypatch.setattr(worker, "_create_process_as_user", checked_create)
        with prepare_restricted_probe_runtime(tmp_path / "adapter-copies") as runtime:
            assert access(runtime.python.root, 0x120089) is True
            assert access(runtime.python.root / "pythonw.exe", 0x1200A0) is True
            assert worker.private_directory_is_current_user_only(runtime.root)
            result = runtime.run_probe(request, workspace)
        assert not runtime.root.exists()
    assert len(launched) == 1
    assert result.response.status == "ok"
    assert result.provenance["restricted_token"] is True
    assert result.provenance["launcher"] == "CreateProcessAsUserW"
    assert result.provenance["appcontainer_implemented"] is False
    assert _security_descriptor(source) == source_security
    assert {path: _security_descriptor(path) for path in originals} == original_security
    assert {
        relative: worker._file_sha256(original / relative)
        for relative, _size, _digest in manifest
    } == original_hashes


def test_adapter_copy_hashes_match_and_rejects_non_probe(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_public_run = worker.run_worker
    original_command = worker._child_command
    original_python = worker._python_executable
    with prepare_restricted_probe_runtime(tmp_path / "adapter-copies") as runtime:
        copied = runtime.python
        source, manifest, _fingerprint = worker._python_runtime_manifest()
        for relative, size, digest in manifest:
            path = copied.root / relative
            assert path.stat().st_size == size
            assert worker._file_sha256(path) == digest
            assert path.read_bytes() == (source / relative).read_bytes()

        def forbidden_launch(*_args, **_kwargs):
            raise AssertionError("non-probe request reached the launcher")

        monkeypatch.setattr(worker, "_run_restricted_worker_for_test", forbidden_launch)
        for operation in ("preflight", "extract", "normalize", "render", "qa"):
            with pytest.raises(
                worker.SandboxUnavailableError, match="only trusted probe"
            ):
                runtime.run_probe(
                    WorkerRequest(
                        operation=operation, input_path="input.pdf", parameters={}
                    ),
                    workspace,
                )
        assert worker.run_worker is original_public_run
        assert worker._child_command is original_command
        assert worker._python_executable is original_python
    assert not runtime.root.exists()


def test_adapter_copy_restores_callables_and_cleans_after_failure(
    tmp_path: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_command = worker._child_command
    original_python = worker._python_executable
    error = worker.WorkerExecutionError("controlled failure")

    def fail_launch(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(worker, "_run_restricted_worker_for_test", fail_launch)
    with (
        pytest.raises(worker.WorkerExecutionError) as caught,
        prepare_restricted_probe_runtime(tmp_path / "adapter-copies") as runtime,
    ):
        runtime.run_probe(
            WorkerRequest(
                operation="probe",
                input_path="input.pdf",
                parameters={"case": "inspect"},
            ),
            workspace,
        )
    assert caught.value is error
    assert worker._child_command is original_command
    assert worker._python_executable is original_python
    assert not runtime.root.exists()


def test_adapter_copy_removes_partial_tree_when_hash_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, manifest, fingerprint = worker._python_runtime_manifest()
    relative, size, _digest = manifest[0]
    monkeypatch.setattr(
        worker,
        "_PYTHON_RUNTIME_MANIFEST_CACHE",
        (source, ((relative, size, "0" * 64), *manifest[1:]), fingerprint),
    )
    parent = tmp_path / "adapter-copies"
    with (
        pytest.raises(worker.SandboxUnavailableError, match="size/hash"),
        prepare_restricted_probe_runtime(parent),
    ):
        pytest.fail("corrupt copy was accepted")
    assert list(parent.iterdir()) == []
