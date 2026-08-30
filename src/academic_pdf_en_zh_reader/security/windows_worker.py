# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed Windows worker launcher built directly on audited Win32 APIs."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from .input_copy import (
    BoundedRegularFile,
    UnsafeInputError,
    _current_user_sid,
    copy_untrusted_input,
    private_directory_is_current_user_only,
    read_bounded_regular_file,
)
from .limits import DEFAULT_LIMITS, WorkerLimits
from .worker_protocol import (
    EXTRACTION_ARTIFACT_PATH,
    EXTRACTION_NORMALIZATION_PATH,
    EXTRACTION_PREFLIGHT_PATH,
    NORMALIZATION_ARTIFACT_PATH,
    NORMALIZATION_PDF_PATH,
    NORMALIZATION_PREFLIGHT_PATH,
    PREFLIGHT_ARTIFACT_PATH,
    ProtocolError,
    WorkerRequest,
    WorkerResponse,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
    resolve_controlled_path,
    validate_relative_path,
)


class SandboxUnavailableError(RuntimeError):
    """The minimum Windows isolation contract could not be established."""


class WorkerExecutionError(RuntimeError):
    """The isolated worker did not return a valid successful result."""


class WorkerReportedError(WorkerExecutionError):
    """The isolated worker returned one stable bounded error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"isolated worker reported {code}: {message}")
        self.code = code
        self.worker_message = message


class WorkerTimeoutError(WorkerExecutionError):
    """The wall-clock ceiling expired and the whole job was terminated."""


class WorkerOutputLimitError(WorkerExecutionError):
    """The worker exceeded the bounded protocol channel."""


class WorkerCpuLimitError(WorkerExecutionError):
    """The Job Object reported a per-process CPU-time limit event."""

    def __init__(self, message: str, job_messages: list[dict[str, object]]) -> None:
        super().__init__(message)
        self.job_messages = job_messages


class WorkerMemoryLimitError(WorkerExecutionError):
    """The Job Object reported a process or job memory-limit event."""

    def __init__(self, message: str, job_messages: list[dict[str, object]]) -> None:
        super().__init__(message)
        self.job_messages = job_messages


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    response: WorkerResponse
    provenance: dict[str, object]
    exit_code: int
    artifact_bytes: bytes | None = None
    normalized_pdf_bytes: bytes | None = None
    normalization_artifact_bytes: bytes | None = None


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _userenv = ctypes.WinDLL("userenv", use_last_error=True)
    _ole32 = ctypes.WinDLL("ole32", use_last_error=True)

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", _STARTUPINFOW),
            ("lpAttributeList", wintypes.LPVOID),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class _LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _TOKEN_GROUPS(ctypes.Structure):
        _fields_ = [
            ("GroupCount", wintypes.DWORD),
            ("Groups", _SID_AND_ATTRIBUTES * 1),
        ]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", wintypes.LPVOID),
            ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
        _fields_ = [
            ("CompletionKey", wintypes.LPVOID),
            ("CompletionPort", wintypes.HANDLE),
        ]

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    _kernel32.CreatePipe.restype = wintypes.BOOL
    _kernel32.CreateEventW.argtypes = [
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.GetHandleInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetHandleInformation.restype = wintypes.BOOL
    _kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.ResetEvent.restype = wintypes.BOOL
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    ]
    _kernel32.CreateDirectoryW.restype = wintypes.BOOL
    _kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.SetHandleInformation.restype = wintypes.BOOL
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CreateIoCompletionPort.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    _kernel32.CreateIoCompletionPort.restype = wintypes.HANDLE
    _kernel32.GetQueuedCompletionStatus.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.DWORD,
    ]
    _kernel32.GetQueuedCompletionStatus.restype = wintypes.BOOL
    _kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    _kernel32.DeleteProcThreadAttributeList.restype = None
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.CreateRestrictedToken.restype = wintypes.BOOL
    _advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    _advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
    _advapi32.IsTokenRestricted.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.LookupPrivilegeNameW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_LUID),
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.LookupPrivilegeNameW.restype = wintypes.BOOL
    _advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _advapi32.FreeSid.argtypes = [wintypes.LPVOID]
    _advapi32.FreeSid.restype = wintypes.LPVOID
    _userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(_SID_AND_ATTRIBUTES),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _userenv.CreateAppContainerProfile.restype = ctypes.c_long
    _userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    _userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    _userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    _userenv.CreateEnvironmentBlock.argtypes = [
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.HANDLE,
        wintypes.BOOL,
    ]
    _userenv.CreateEnvironmentBlock.restype = wintypes.BOOL
    _userenv.DestroyEnvironmentBlock.argtypes = [wintypes.LPVOID]
    _userenv.DestroyEnvironmentBlock.restype = wintypes.BOOL
    _userenv.GetAppContainerFolderPath.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _userenv.GetAppContainerFolderPath.restype = ctypes.c_long
    _ole32.CoTaskMemFree.argtypes = [wintypes.LPVOID]
    _ole32.CoTaskMemFree.restype = None


_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_EXPECTED_JOB_FLAGS = (
    _JOB_OBJECT_LIMIT_PROCESS_TIME
    | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
    | _JOB_OBJECT_LIMIT_JOB_MEMORY
    | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
)
_ALLOWED_ENABLED_PRIVILEGES = {"SeChangeNotifyPrivilege"}
_BUILTIN_USERS_SID = "S-1-5-32-545"
_EVERYONE_SID = "S-1-1-0"
_RESTRICTED_CODE_SID = "S-1-5-12"
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_JOB_MESSAGE_NAMES = {
    1: "END_OF_JOB_TIME",
    2: "END_OF_PROCESS_TIME",
    3: "ACTIVE_PROCESS_LIMIT",
    4: "ACTIVE_PROCESS_ZERO",
    6: "NEW_PROCESS",
    7: "EXIT_PROCESS",
    8: "ABNORMAL_EXIT_PROCESS",
    9: "PROCESS_MEMORY_LIMIT",
    10: "JOB_MEMORY_LIMIT",
}
_PYTHON_RUNTIME_EXCLUDED_PARTS = {
    "__pycache__",
    "ensurepip",
    "idlelib",
    "site-packages",
    "test",
    "tests",
    "tkinter",
    "turtledemo",
}
_PYTHON_RUNTIME_MANIFEST_CACHE: (
    tuple[Path, tuple[tuple[Path, int, str], ...], str] | None
) = None
_PYPDF_VERSION = "6.16.2"
_PYPDF_RUNTIME_MANIFEST_CACHE: (
    tuple[Path, tuple[tuple[Path, int, str], ...], str] | None
) = None
_EXTRACTION_DEPENDENCIES = (
    ("pdfplumber", "0.11.10", ("pdfplumber",)),
    ("pdfminer.six", "20260107", ("pdfminer",)),
    ("charset-normalizer", "3.5.1", ("charset_normalizer",)),
    ("cryptography", "50.0.1", ("cryptography",)),
    ("cffi", "2.1.1", ()),
)
_EXTRACTION_RUNTIME_MANIFEST_CACHE: (
    tuple[Path, tuple[tuple[Path, int, str], ...], str] | None
) = None
_PROJECT_RUNTIME_FILES = (
    Path("extraction/__init__.py"),
    Path("extraction/blocks.py"),
    Path("extraction/page_objects.py"),
    Path("extraction/repeated_marginals.py"),
    Path("extraction/text_lines.py"),
    Path("job/__init__.py"),
    Path("job/canonical_json.py"),
    Path("normalization/__init__.py"),
    Path("normalization/core.py"),
    Path("preflight/__init__.py"),
    Path("preflight/checks.py"),
    Path("preflight/page_boxes.py"),
    Path("preflight/pdf_catalog.py"),
    Path("security/input_copy.py"),
    Path("security/limits.py"),
    Path("security/unsupported_worker.py"),
    Path("security/windows_worker.py"),
    Path("security/worker_protocol.py"),
)


@dataclass(slots=True)
class _AppContainerContext:
    profile_name: str
    sid: object
    profile_folder: Path
    workspace: Path
    runtime_root: Path
    project_runtime_fingerprint: str
    project_runtime_file_count: int
    project_runtime_bytes: int
    pypdf_version: str
    pypdf_runtime_fingerprint: str
    pypdf_runtime_file_count: int
    pypdf_runtime_bytes: int
    pdfplumber_version: str
    pdfminer_version: str
    extraction_runtime_fingerprint: str
    extraction_runtime_file_count: int
    extraction_runtime_bytes: int
    python_runtime_root: Path
    python_runtime_source: Path
    python_runtime_fingerprint: str
    python_runtime_file_count: int
    python_runtime_bytes: int
    profile_mode: str
    profile_creation_hresult: int
    profile_created: bool = False

    def cleanup(self) -> list[str]:
        errors: list[str] = []
        if self.workspace.exists():
            try:
                shutil.rmtree(self.workspace)
            except OSError as error:
                errors.append(f"AppContainer workspace cleanup failed: {error}")
        if self.profile_created:
            result = _userenv.DeleteAppContainerProfile(self.profile_name)
            if result < 0:
                errors.append(
                    f"DeleteAppContainerProfile HRESULT 0x{result & 0xFFFFFFFF:08X}"
                )
            else:
                self.profile_created = False
        if self.sid:
            _advapi32.FreeSid(self.sid)
            self.sid = None
        if self.profile_folder.exists():
            try:
                shutil.rmtree(self.profile_folder)
            except OSError as error:
                errors.append(f"AppContainer profile-folder cleanup failed: {error}")
        return errors


@dataclass(slots=True)
class _ProcessAttributeContext:
    buffer: object
    attribute_list: object
    inherited_handles: object
    inherited_handle_values: tuple[int, ...]
    security_capabilities: object | None
    initialized: bool = True

    def cleanup(self) -> None:
        if self.initialized:
            _kernel32.DeleteProcThreadAttributeList(self.attribute_list)
            self.initialized = False


@dataclass(slots=True)
class _RestrictingSidContext:
    entries: object
    labels: tuple[str, ...]
    keepalive: tuple[object, ...]
    local_allocations: tuple[object, ...]

    def cleanup(self) -> None:
        for allocation in self.local_allocations:
            if allocation:
                _kernel32.LocalFree(allocation)


@dataclass(frozen=True, slots=True)
class _PythonRuntimeCopy:
    root: Path
    source: Path
    fingerprint: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class _WorkerRuntimeCopy:
    root: Path
    project_fingerprint: str
    project_file_count: int
    project_bytes: int
    pypdf_version: str
    pypdf_fingerprint: str
    pypdf_file_count: int
    pypdf_bytes: int
    pdfplumber_version: str
    pdfminer_version: str
    extraction_runtime_fingerprint: str
    extraction_runtime_file_count: int
    extraction_runtime_bytes: int


def _appcontainer_apis_available() -> bool:
    return os.name == "nt" and all(
        hasattr(_userenv, name)
        for name in (
            "CreateAppContainerProfile",
            "DeleteAppContainerProfile",
            "DeriveAppContainerSidFromAppContainerName",
            "GetAppContainerFolderPath",
        )
    )


def _sid_string(sid: object) -> str:
    output = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
        raise _win_error("ConvertSidToStringSidW")
    try:
        return output.value
    finally:
        _kernel32.LocalFree(ctypes.cast(output, wintypes.HLOCAL))


def _appcontainer_profile_folder(profile_name: str, sid_string: str) -> Path:
    folder_pointer = wintypes.LPWSTR()
    result = _userenv.GetAppContainerFolderPath(
        sid_string,
        ctypes.byref(folder_pointer),
    )
    if result < 0 or not folder_pointer.value:
        raise SandboxUnavailableError(
            f"GetAppContainerFolderPath failed (HRESULT 0x{result & 0xFFFFFFFF:08X})"
        )
    try:
        folder = Path(os.path.abspath(folder_pointer.value))
    finally:
        _ole32.CoTaskMemFree(ctypes.cast(folder_pointer, wintypes.LPVOID))

    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        raise SandboxUnavailableError("LOCALAPPDATA is unavailable")
    packages = Path(os.path.abspath(local_appdata)) / "Packages"
    try:
        common = os.path.commonpath((str(packages), str(folder)))
    except ValueError as error:
        raise SandboxUnavailableError(
            "AppContainer profile folder is on an unexpected root"
        ) from error
    profile_folder = folder.parent
    if (
        os.path.normcase(common) != os.path.normcase(str(packages))
        or folder.name.casefold() != "ac"
        or profile_folder.name.casefold() != profile_name.casefold()
        or profile_folder.parent != packages
    ):
        raise SandboxUnavailableError(
            "AppContainer profile folder is outside the expected unique package path"
        )
    if not folder.is_dir():
        raise SandboxUnavailableError("AppContainer profile folder was not created")
    for checked in (profile_folder, folder):
        information = checked.lstat()
        if checked.is_symlink() or (
            getattr(information, "st_file_attributes", 0) & 0x400
        ):
            raise SandboxUnavailableError(
                "AppContainer profile folder is a reparse point"
            )
    return folder


def _create_appcontainer_workspace(root: Path, appcontainer_sid: str) -> Path:
    workspace = root / f"appcontainer-{secrets.token_hex(12)}"
    descriptor = wintypes.LPVOID()
    owner_sid = _current_user_sid()
    sddl = f"D:P(A;OICI;FA;;;{owner_sid})(A;OICI;FA;;;{appcontainer_sid})"
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        None,
    ):
        raise _win_error("AppContainer workspace DACL creation")
    security = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    try:
        if not _kernel32.CreateDirectoryW(str(workspace), ctypes.byref(security)):
            raise _win_error("AppContainer workspace creation")
    finally:
        _kernel32.LocalFree(descriptor)
    return workspace


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_source_is_safe(root: Path, source: Path) -> bool:
    try:
        root = Path(os.path.abspath(root))
        source = Path(os.path.abspath(source))
        root_information = root.lstat()
        if (
            not root.is_dir()
            or stat.S_ISLNK(root_information.st_mode)
            or getattr(root_information, "st_file_attributes", 0) & 0x400
        ):
            return False
        common = os.path.commonpath((str(root), str(source)))
        if os.path.normcase(common) != os.path.normcase(str(root)):
            return False
        relative = source.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            information = current.lstat()
            if stat.S_ISLNK(information.st_mode) or (
                getattr(information, "st_file_attributes", 0) & 0x400
            ):
                return False
        return source.is_file() and stat.S_ISREG(source.stat().st_mode)
    except (OSError, ValueError):
        return False


def _manifest_fingerprint(
    manifest: tuple[tuple[Path, int, str], ...] | list[tuple[Path, int, str]],
) -> str:
    aggregate = hashlib.sha256()
    for relative, size, digest in manifest:
        aggregate.update(relative.as_posix().encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(str(size).encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def _project_runtime_manifest() -> tuple[
    Path,
    tuple[tuple[Path, int, str], ...],
    str,
]:
    package_root = Path(os.path.abspath(Path(__file__).resolve().parents[1]))
    manifest: list[tuple[Path, int, str]] = []
    for relative in _PROJECT_RUNTIME_FILES:
        source = package_root / relative
        if not _runtime_source_is_safe(package_root, source):
            raise SandboxUnavailableError(
                "project worker runtime contains an unavailable or reparse file: "
                + relative.as_posix()
            )
        size = source.stat().st_size
        manifest.append((relative, size, _file_sha256(source)))
    result = tuple(manifest)
    return package_root, result, _manifest_fingerprint(result)


def _pypdf_runtime_manifest() -> tuple[
    Path,
    tuple[tuple[Path, int, str], ...],
    str,
]:
    global _PYPDF_RUNTIME_MANIFEST_CACHE
    if _PYPDF_RUNTIME_MANIFEST_CACHE is not None:
        return _PYPDF_RUNTIME_MANIFEST_CACHE

    distribution = _pypdf_distribution()
    if distribution.version != _PYPDF_VERSION:
        raise SandboxUnavailableError(f"pypdf version must be exactly {_PYPDF_VERSION}")
    files = distribution.files
    if files is None:
        raise SandboxUnavailableError("pypdf distribution has no file manifest")
    source_root = Path(os.path.abspath(distribution.locate_file("")))
    manifest: list[tuple[Path, int, str]] = []
    seen_paths: set[str] = set()
    for entry in files:
        parts = tuple(entry.parts)
        if not parts or parts[0] != "pypdf":
            continue
        relative = Path(*parts)
        if "__pycache__" in relative.parts or relative.suffix.casefold() == ".pyc":
            continue
        if relative.suffix.casefold() != ".py" and relative.name != "py.typed":
            raise SandboxUnavailableError(
                "pypdf runtime is not pure Python: " + relative.as_posix()
            )
        normalized = relative.as_posix().casefold()
        if normalized in seen_paths:
            raise SandboxUnavailableError("pypdf package manifest has duplicates")
        seen_paths.add(normalized)
        source = Path(os.path.abspath(distribution.locate_file(entry)))
        if not _runtime_source_is_safe(source_root, source):
            raise SandboxUnavailableError(
                "pypdf runtime contains an unavailable or reparse file: "
                + relative.as_posix()
            )
        manifest.append((relative, source.stat().st_size, _file_sha256(source)))
    manifest.sort(key=lambda item: item[0].as_posix().casefold())
    if not manifest or manifest[0][0] != Path("pypdf/__init__.py"):
        raise SandboxUnavailableError("pypdf package manifest is incomplete")
    result = tuple(manifest)
    _PYPDF_RUNTIME_MANIFEST_CACHE = (
        source_root,
        result,
        _manifest_fingerprint(result),
    )
    return _PYPDF_RUNTIME_MANIFEST_CACHE


def _pypdf_distribution():
    from importlib import metadata

    try:
        return metadata.distribution("pypdf")
    except metadata.PackageNotFoundError as error:
        raise SandboxUnavailableError(
            "required pypdf distribution is missing"
        ) from error


def _extraction_runtime_manifest() -> tuple[
    Path,
    tuple[tuple[Path, int, str], ...],
    str,
]:
    global _EXTRACTION_RUNTIME_MANIFEST_CACHE
    if _EXTRACTION_RUNTIME_MANIFEST_CACHE is not None:
        return _EXTRACTION_RUNTIME_MANIFEST_CACHE

    from importlib import metadata

    source_root: Path | None = None
    manifest: list[tuple[Path, int, str]] = []
    seen_paths: set[str] = set()
    for distribution_name, expected_version, package_roots in _EXTRACTION_DEPENDENCIES:
        try:
            distribution = metadata.distribution(distribution_name)
        except metadata.PackageNotFoundError as error:
            raise SandboxUnavailableError(
                f"required {distribution_name} distribution is missing"
            ) from error
        if distribution.version != expected_version:
            raise SandboxUnavailableError(
                f"{distribution_name} version must be exactly {expected_version}"
            )
        located_root = Path(os.path.abspath(distribution.locate_file("")))
        if source_root is None:
            source_root = located_root
        elif source_root != located_root:
            raise SandboxUnavailableError(
                "extraction dependencies do not share one controlled runtime root"
            )
        files = distribution.files
        if files is None:
            raise SandboxUnavailableError(
                f"{distribution_name} distribution has no file manifest"
            )
        matched = 0
        for entry in files:
            parts = tuple(entry.parts)
            if not parts:
                continue
            relative = Path(*parts)
            if "__pycache__" in relative.parts or relative.suffix.casefold() == ".pyc":
                continue
            suffix = relative.suffix.casefold()
            include = False
            if distribution_name == "pdfplumber":
                include = parts[0] in package_roots and suffix in {".py", ".typed"}
            elif distribution_name == "pdfminer.six":
                include = parts[0] in package_roots and suffix in {
                    ".gz",
                    ".py",
                    ".txt",
                    ".typed",
                }
            elif distribution_name == "charset-normalizer":
                include = (
                    parts[0] in package_roots
                    and len(parts) == 2
                    and suffix in {".py", ".typed"}
                )
            elif distribution_name == "cryptography":
                include = parts[0] in package_roots and (
                    suffix in {".py", ".typed"}
                    or relative == Path("cryptography/hazmat/bindings/_rust.pyd")
                )
            elif distribution_name == "cffi":
                extension_suffix = sysconfig.get_config_var("EXT_SUFFIX")
                include = (
                    len(parts) == 1
                    and isinstance(extension_suffix, str)
                    and relative.name == f"_cffi_backend{extension_suffix}"
                )
            if not include:
                continue
            normalized = relative.as_posix().casefold()
            if normalized in seen_paths:
                raise SandboxUnavailableError(
                    "extraction dependency manifests contain duplicate paths"
                )
            seen_paths.add(normalized)
            source = Path(os.path.abspath(distribution.locate_file(entry)))
            if not _runtime_source_is_safe(located_root, source):
                raise SandboxUnavailableError(
                    "extraction runtime contains an unavailable or reparse file: "
                    + relative.as_posix()
                )
            manifest.append((relative, source.stat().st_size, _file_sha256(source)))
            matched += 1
        if matched == 0:
            raise SandboxUnavailableError(
                f"{distribution_name} package manifest is incomplete"
            )
    if source_root is None:
        raise SandboxUnavailableError("extraction runtime manifest is empty")
    manifest.sort(key=lambda item: item[0].as_posix().casefold())
    result = tuple(manifest)
    _EXTRACTION_RUNTIME_MANIFEST_CACHE = (
        source_root,
        result,
        _manifest_fingerprint(result),
    )
    return _EXTRACTION_RUNTIME_MANIFEST_CACHE


def _copy_verified_manifest(
    source_root: Path,
    destination_root: Path,
    manifest: tuple[tuple[Path, int, str], ...],
) -> int:
    total_bytes = 0
    for relative, expected_size, expected_hash in manifest:
        source = source_root / relative
        if not _runtime_source_is_safe(source_root, source):
            raise SandboxUnavailableError(
                "runtime source changed or became a reparse point: "
                + relative.as_posix()
            )
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        information = destination.lstat()
        actual_size = information.st_size
        actual_hash = _file_sha256(destination)
        if (
            not stat.S_ISREG(information.st_mode)
            or stat.S_ISLNK(information.st_mode)
            or getattr(information, "st_file_attributes", 0) & 0x400
            or actual_size != expected_size
            or actual_hash != expected_hash
        ):
            raise SandboxUnavailableError(
                "copied runtime failed size/hash verification: " + relative.as_posix()
            )
        total_bytes += actual_size
    return total_bytes


def _copy_worker_runtime(
    workspace: Path,
    *,
    operation: str = "preflight",
) -> _WorkerRuntimeCopy:
    runtime_root = workspace / "runtime"
    runtime_root.mkdir()
    project_root, project_manifest, project_fingerprint = _project_runtime_manifest()
    package_destination = runtime_root / "academic_pdf_en_zh_reader"
    project_bytes = _copy_verified_manifest(
        project_root,
        package_destination,
        project_manifest,
    )
    pypdf_fingerprint = ""
    pypdf_file_count = 0
    pypdf_bytes = 0
    if operation != "extract":
        pypdf_root, pypdf_manifest, pypdf_fingerprint = _pypdf_runtime_manifest()
        pypdf_bytes = _copy_verified_manifest(
            pypdf_root,
            runtime_root,
            pypdf_manifest,
        )
        pypdf_file_count = len(pypdf_manifest)
    extraction_fingerprint = ""
    extraction_file_count = 0
    extraction_bytes = 0
    if operation == "extract":
        extraction_root, extraction_manifest, extraction_fingerprint = (
            _extraction_runtime_manifest()
        )
        extraction_bytes = _copy_verified_manifest(
            extraction_root,
            runtime_root,
            extraction_manifest,
        )
        extraction_file_count = len(extraction_manifest)
    return _WorkerRuntimeCopy(
        root=runtime_root,
        project_fingerprint=project_fingerprint,
        project_file_count=len(project_manifest),
        project_bytes=project_bytes,
        pypdf_version=_PYPDF_VERSION,
        pypdf_fingerprint=pypdf_fingerprint,
        pypdf_file_count=pypdf_file_count,
        pypdf_bytes=pypdf_bytes,
        pdfplumber_version=_EXTRACTION_DEPENDENCIES[0][1],
        pdfminer_version=_EXTRACTION_DEPENDENCIES[1][1],
        extraction_runtime_fingerprint=extraction_fingerprint,
        extraction_runtime_file_count=extraction_file_count,
        extraction_runtime_bytes=extraction_bytes,
    )


def _python_runtime_manifest() -> tuple[
    Path,
    tuple[tuple[Path, int, str], ...],
    str,
]:
    global _PYTHON_RUNTIME_MANIFEST_CACHE
    if _PYTHON_RUNTIME_MANIFEST_CACHE is not None:
        return _PYTHON_RUNTIME_MANIFEST_CACHE

    source_root = Path(sys.base_prefix).resolve(strict=True)
    base_executable = Path(_python_executable()).resolve(strict=True)
    if base_executable.parent != source_root:
        raise SandboxUnavailableError(
            "the base Python executable is outside sys.base_prefix"
        )
    required_root_files = {
        "LICENSE.txt",
        base_executable.name,
        "python3.dll",
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        "pythonw.exe",
    }
    candidates: set[Path] = set()
    for name in required_root_files:
        candidate = source_root / name
        if not candidate.is_file():
            raise SandboxUnavailableError(
                f"required Python runtime file is missing: {candidate}"
            )
        candidates.add(candidate)
    candidates.update(
        path for path in source_root.glob("vcruntime*.dll") if path.is_file()
    )

    dlls = source_root / "DLLs"
    standard_library = source_root / "Lib"
    if not dlls.is_dir() or not standard_library.is_dir():
        raise SandboxUnavailableError("Python DLLs or standard library is missing")
    candidates.update(path for path in dlls.rglob("*") if path.is_file())
    for path in standard_library.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        parts = {part.casefold() for part in relative.parts}
        if parts & _PYTHON_RUNTIME_EXCLUDED_PARTS or path.suffix.casefold() == ".pyc":
            continue
        candidates.add(path)

    manifest: list[tuple[Path, int, str]] = []
    aggregate = hashlib.sha256()
    for source in sorted(
        candidates,
        key=lambda path: path.relative_to(source_root).as_posix().casefold(),
    ):
        relative = source.relative_to(source_root)
        size = source.stat().st_size
        digest = _file_sha256(source)
        manifest.append((relative, size, digest))
        aggregate.update(relative.as_posix().encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(str(size).encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    _PYTHON_RUNTIME_MANIFEST_CACHE = (
        source_root,
        tuple(manifest),
        aggregate.hexdigest(),
    )
    return _PYTHON_RUNTIME_MANIFEST_CACHE


def _copy_minimal_python_runtime(workspace: Path) -> _PythonRuntimeCopy:
    source_root, manifest, fingerprint = _python_runtime_manifest()
    destination_root = workspace / "python-runtime"
    destination_root.mkdir()
    total_bytes = 0
    for relative, expected_size, expected_hash in manifest:
        source = source_root / relative
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        actual_size = destination.stat().st_size
        actual_hash = _file_sha256(destination)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise SandboxUnavailableError(
                "copied Python runtime failed size/hash verification: "
                + relative.as_posix()
            )
        total_bytes += actual_size
    return _PythonRuntimeCopy(
        root=destination_root,
        source=source_root,
        fingerprint=fingerprint,
        file_count=len(manifest),
        total_bytes=total_bytes,
    )


def _prepare_appcontainer(
    root: Path,
    request: WorkerRequest,
) -> _AppContainerContext:
    if not _appcontainer_apis_available():
        raise SandboxUnavailableError(
            "required AppContainer APIs are unavailable; restricted-token-only "
            "execution is not a production fallback"
        )
    profile_name = f"academicpdfworker.{os.getpid()}.{secrets.token_hex(8)}"
    sid = wintypes.LPVOID()
    result = _userenv.CreateAppContainerProfile(
        profile_name,
        "Academic PDF Worker",
        "Ephemeral zero-capability PDF worker",
        None,
        0,
        ctypes.byref(sid),
    )
    if result < 0:
        raise SandboxUnavailableError(
            "CreateAppContainerProfile failed; an unregistered derived SID is not "
            "a valid fallback "
            f"(HRESULT 0x{result & 0xFFFFFFFF:08X})"
        )

    profile_folder: Path | None = None
    workspace: Path | None = None
    try:
        appcontainer_sid = _sid_string(sid)
        appcontainer_folder = _appcontainer_profile_folder(
            profile_name,
            appcontainer_sid,
        )
        profile_folder = appcontainer_folder.parent
        workspace = _create_appcontainer_workspace(
            appcontainer_folder,
            appcontainer_sid,
        )
        worker_runtime = _copy_worker_runtime(
            workspace,
            operation=request.operation,
        )
        python_runtime = _copy_minimal_python_runtime(workspace)
        source = resolve_controlled_path(root, request.input_path, must_exist=True)
        relative = Path(*request.input_path.split("/"))
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if _file_sha256(source) != _file_sha256(destination):
            raise SandboxUnavailableError("AppContainer input copy hash mismatch")
        handoffs: tuple[str, ...] = ()
        if request.operation == "extract":
            handoffs = (EXTRACTION_PREFLIGHT_PATH, EXTRACTION_NORMALIZATION_PATH)
        elif request.operation == "normalize":
            handoffs = (NORMALIZATION_PREFLIGHT_PATH,)
        for relative_handoff in handoffs:
            handoff_source = resolve_controlled_path(
                root,
                relative_handoff,
                must_exist=True,
            )
            handoff_destination = workspace / relative_handoff
            shutil.copyfile(handoff_source, handoff_destination)
            if _file_sha256(handoff_source) != _file_sha256(handoff_destination):
                raise SandboxUnavailableError("AppContainer handoff hash mismatch")
        return _AppContainerContext(
            profile_name=profile_name,
            sid=sid,
            profile_folder=profile_folder,
            workspace=workspace,
            runtime_root=worker_runtime.root,
            project_runtime_fingerprint=worker_runtime.project_fingerprint,
            project_runtime_file_count=worker_runtime.project_file_count,
            project_runtime_bytes=worker_runtime.project_bytes,
            pypdf_version=worker_runtime.pypdf_version,
            pypdf_runtime_fingerprint=worker_runtime.pypdf_fingerprint,
            pypdf_runtime_file_count=worker_runtime.pypdf_file_count,
            pypdf_runtime_bytes=worker_runtime.pypdf_bytes,
            pdfplumber_version=worker_runtime.pdfplumber_version,
            pdfminer_version=worker_runtime.pdfminer_version,
            extraction_runtime_fingerprint=(
                worker_runtime.extraction_runtime_fingerprint
            ),
            extraction_runtime_file_count=(
                worker_runtime.extraction_runtime_file_count
            ),
            extraction_runtime_bytes=worker_runtime.extraction_runtime_bytes,
            python_runtime_root=python_runtime.root,
            python_runtime_source=python_runtime.source,
            python_runtime_fingerprint=python_runtime.fingerprint,
            python_runtime_file_count=python_runtime.file_count,
            python_runtime_bytes=python_runtime.total_bytes,
            profile_mode="ephemeral_profile",
            profile_creation_hresult=result & 0xFFFFFFFF,
            profile_created=True,
        )
    except Exception as error:
        cleanup_errors: list[str] = []
        if workspace is not None and workspace.exists():
            try:
                shutil.rmtree(workspace)
            except OSError as cleanup_error:
                cleanup_errors.append(f"workspace cleanup failed: {cleanup_error}")
        delete_result = _userenv.DeleteAppContainerProfile(profile_name)
        if delete_result < 0:
            cleanup_errors.append(
                "DeleteAppContainerProfile failed "
                f"(HRESULT 0x{delete_result & 0xFFFFFFFF:08X})"
            )
        if sid:
            _advapi32.FreeSid(sid)
        if profile_folder is not None and profile_folder.exists():
            try:
                shutil.rmtree(profile_folder)
            except OSError as cleanup_error:
                cleanup_errors.append(f"profile-folder cleanup failed: {cleanup_error}")
        if cleanup_errors:
            error.add_note("; ".join(cleanup_errors))
        raise


def _prepare_process_attributes(
    inherited_handles: tuple[object, object, object],
    appcontainer: _AppContainerContext | None,
) -> _ProcessAttributeContext:
    attribute_count = 1 + int(appcontainer is not None)
    attribute_size = ctypes.c_size_t()
    _kernel32.InitializeProcThreadAttributeList(
        None, attribute_count, 0, ctypes.byref(attribute_size)
    )
    if not attribute_size.value:
        raise _win_error("InitializeProcThreadAttributeList sizing")

    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attribute_list = ctypes.cast(attribute_buffer, wintypes.LPVOID)
    initialized = False
    try:
        if not _kernel32.InitializeProcThreadAttributeList(
            attribute_list,
            attribute_count,
            0,
            ctypes.byref(attribute_size),
        ):
            raise _win_error("InitializeProcThreadAttributeList")
        initialized = True

        handle_values = tuple(_handle_value(handle) for handle in inherited_handles)
        handle_array = (wintypes.HANDLE * len(handle_values))(*handle_values)
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_array, wintypes.LPVOID),
            ctypes.sizeof(handle_array),
            None,
            None,
        ):
            raise _win_error("UpdateProcThreadAttribute(HandleList)")

        capabilities: object | None = None
        if appcontainer is not None:
            capabilities = _SECURITY_CAPABILITIES(appcontainer.sid, None, 0, 0)
            if not _kernel32.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(capabilities),
                ctypes.sizeof(capabilities),
                None,
                None,
            ):
                raise _win_error("UpdateProcThreadAttribute(SecurityCapabilities)")
        return _ProcessAttributeContext(
            buffer=attribute_buffer,
            attribute_list=attribute_list,
            inherited_handles=handle_array,
            inherited_handle_values=handle_values,
            security_capabilities=capabilities,
        )
    except Exception:
        if initialized:
            _kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise


def _require_windows() -> None:
    if os.name != "nt":
        raise SandboxUnavailableError(
            "the Windows sandbox is unavailable and no subprocess fallback is allowed"
        )


def _win_error(action: str, *, error: int | None = None) -> SandboxUnavailableError:
    code = ctypes.get_last_error() if error is None else error
    detail = ctypes.FormatError(code).strip() if code else "unknown Win32 error"
    return SandboxUnavailableError(f"{action} failed (WinError {code}: {detail})")


def _close_handle(handle: object | None) -> None:
    if os.name == "nt" and handle:
        _kernel32.CloseHandle(handle)


def _handle_value(handle: object) -> int:
    value = getattr(handle, "value", handle)
    if not isinstance(value, int):
        raise WorkerExecutionError("Win32 returned an invalid handle value")
    return value


def _current_process_handle_count() -> int:
    count = wintypes.DWORD()
    if not _kernel32.GetProcessHandleCount(
        _kernel32.GetCurrentProcess(), ctypes.byref(count)
    ):
        raise _win_error("GetProcessHandleCount")
    return count.value


def _open_current_token(access: int) -> object:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), access, ctypes.byref(token)
    ):
        raise _win_error("OpenProcessToken")
    return token


def _enabled_privileges(token: object) -> list[str]:
    size = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, 3, None, 0, ctypes.byref(size))
    if not size.value:
        raise _win_error("GetTokenInformation(TokenPrivileges) sizing")
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(token, 3, buffer, size, ctypes.byref(size)):
        raise _win_error("GetTokenInformation(TokenPrivileges)")
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    base = ctypes.addressof(buffer) + ctypes.sizeof(wintypes.DWORD)
    names: list[str] = []
    for index in range(count):
        entry = _LUID_AND_ATTRIBUTES.from_address(
            base + index * ctypes.sizeof(_LUID_AND_ATTRIBUTES)
        )
        if not entry.Attributes & 0x00000002:
            continue
        length = wintypes.DWORD()
        _advapi32.LookupPrivilegeNameW(
            None,
            ctypes.byref(entry.Luid),
            None,
            ctypes.byref(length),
        )
        if not length.value:
            raise _win_error("LookupPrivilegeNameW sizing")
        name = ctypes.create_unicode_buffer(length.value + 1)
        if not _advapi32.LookupPrivilegeNameW(
            None, ctypes.byref(entry.Luid), name, ctypes.byref(length)
        ):
            raise _win_error("LookupPrivilegeNameW")
        names.append(name.value)
    return sorted(names)


def _token_user_sid(token: object) -> tuple[object, object]:
    size = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
    if not size.value:
        raise _win_error("GetTokenInformation(TokenUser) sizing")
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(
        token,
        1,
        buffer,
        size,
        ctypes.byref(size),
    ):
        raise _win_error("GetTokenInformation(TokenUser)")
    token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents
    return buffer, token_user.User.Sid


def _token_logon_sid(token: object) -> tuple[object, object]:
    size = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, 28, None, 0, ctypes.byref(size))
    if not size.value:
        raise _win_error("GetTokenInformation(TokenLogonSid) sizing")
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(
        token,
        28,
        buffer,
        size,
        ctypes.byref(size),
    ):
        raise _win_error("GetTokenInformation(TokenLogonSid)")
    token_groups = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_GROUPS)).contents
    if token_groups.GroupCount != 1 or not token_groups.Groups[0].Sid:
        raise SandboxUnavailableError("source token has no unique logon SID")
    return buffer, token_groups.Groups[0].Sid


def _build_restricting_sids(token: object) -> _RestrictingSidContext:
    # Restricted-token access checks need the private-workspace owner, the unique
    # logon session, and the system SID classes used by Windows/Python loader objects.
    # Production never reaches this test-adapter path; it requires AppContainer.
    user_buffer, user_sid = _token_user_sid(token)
    logon_buffer, logon_sid = _token_logon_sid(token)
    builtin_users_sid = wintypes.LPVOID()
    if not _advapi32.ConvertStringSidToSidW(
        _BUILTIN_USERS_SID,
        ctypes.byref(builtin_users_sid),
    ):
        raise _win_error("ConvertStringSidToSidW(Builtin Users)")
    everyone_sid = wintypes.LPVOID()
    if not _advapi32.ConvertStringSidToSidW(
        _EVERYONE_SID,
        ctypes.byref(everyone_sid),
    ):
        _kernel32.LocalFree(builtin_users_sid)
        raise _win_error("ConvertStringSidToSidW(Everyone)")
    restricted_code_sid = wintypes.LPVOID()
    if not _advapi32.ConvertStringSidToSidW(
        _RESTRICTED_CODE_SID,
        ctypes.byref(restricted_code_sid),
    ):
        _kernel32.LocalFree(builtin_users_sid)
        _kernel32.LocalFree(everyone_sid)
        raise _win_error("ConvertStringSidToSidW(Restricted Code)")
    entries = (_SID_AND_ATTRIBUTES * 5)(
        _SID_AND_ATTRIBUTES(user_sid, 0),
        _SID_AND_ATTRIBUTES(logon_sid, 0),
        _SID_AND_ATTRIBUTES(builtin_users_sid, 0),
        _SID_AND_ATTRIBUTES(everyone_sid, 0),
        _SID_AND_ATTRIBUTES(restricted_code_sid, 0),
    )
    return _RestrictingSidContext(
        entries=entries,
        labels=(
            "current_user",
            "logon_sid",
            "builtin_users",
            "everyone",
            "restricted_code",
        ),
        keepalive=(user_buffer, logon_buffer),
        local_allocations=(builtin_users_sid, everyone_sid, restricted_code_sid),
    )


def _create_restricted_token_from_unrestricted_source(
    source: object,
) -> tuple[object, tuple[str, ...]]:
    restricting = _build_restricting_sids(source)
    restricted = wintypes.HANDLE()
    try:
        if not _advapi32.CreateRestrictedToken(
            source,
            0x00000001,
            0,
            None,
            0,
            None,
            len(restricting.labels),
            ctypes.cast(restricting.entries, wintypes.LPVOID),
            ctypes.byref(restricted),
        ):
            error = ctypes.get_last_error()
            raise _win_error("CreateRestrictedToken", error=error)
    finally:
        restricting.cleanup()
    return restricted, restricting.labels


def _create_restricted_token() -> tuple[object, list[str], str]:
    source = _open_current_token(0x0001 | 0x0002 | 0x0008 | 0x0080 | 0x0100)
    restricted = wintypes.HANDLE()
    try:
        source_privileges = _enabled_privileges(source)
        if _advapi32.IsTokenRestricted(source):
            unexpected = set(source_privileges) - _ALLOWED_ENABLED_PRIVILEGES
            if unexpected:
                raise SandboxUnavailableError(
                    "inherited restricted token retained unexpected privileges: "
                    + ", ".join(sorted(unexpected))
                )
            if not _advapi32.DuplicateTokenEx(
                source,
                0x0001 | 0x0002 | 0x0008 | 0x0080 | 0x0100,
                None,
                2,
                1,
                ctypes.byref(restricted),
            ):
                raise _win_error("DuplicateTokenEx(restricted token)")
            origin = "inherited_restricted_token_duplicated"
        else:
            restricted, restricting_labels = (
                _create_restricted_token_from_unrestricted_source(source)
            )
            origin = (
                "CreateRestrictedToken(DISABLE_MAX_PRIVILEGE; RestrictingSids="
                + ",".join(restricting_labels)
                + ")"
            )
    finally:
        _close_handle(source)
    if not _advapi32.IsTokenRestricted(restricted):
        _close_handle(restricted)
        raise SandboxUnavailableError(
            "CreateRestrictedToken returned a non-restricted token"
        )
    privileges = _enabled_privileges(restricted)
    unexpected = set(privileges) - _ALLOWED_ENABLED_PRIVILEGES
    if unexpected:
        _close_handle(restricted)
        raise SandboxUnavailableError(
            "restricted token retained unexpected enabled privileges: "
            + ", ".join(sorted(unexpected))
        )
    return restricted, privileges, origin


def _configure_job(
    limits: WorkerLimits,
) -> tuple[object, object, dict[str, object]]:
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _win_error("CreateJobObjectW")
    completion_port = _kernel32.CreateIoCompletionPort(
        wintypes.HANDLE(-1),
        None,
        0,
        1,
    )
    if not completion_port:
        error = _win_error("CreateIoCompletionPort")
        _close_handle(job)
        raise error
    association = _JOBOBJECT_ASSOCIATE_COMPLETION_PORT(None, completion_port)
    if not _kernel32.SetInformationJobObject(
        job,
        7,
        ctypes.byref(association),
        ctypes.sizeof(association),
    ):
        error = _win_error("SetInformationJobObject(CompletionPort)")
        _close_handle(completion_port)
        _close_handle(job)
        raise error
    information = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    basic = information.BasicLimitInformation
    basic.LimitFlags = _EXPECTED_JOB_FLAGS
    basic.PerProcessUserTimeLimit = int(limits.cpu_time_seconds * 10_000_000)
    basic.ActiveProcessLimit = limits.active_process_limit
    information.ProcessMemoryLimit = limits.process_memory_bytes
    information.JobMemoryLimit = limits.job_memory_bytes
    if not _kernel32.SetInformationJobObject(
        job,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = _win_error("SetInformationJobObject")
        _close_handle(completion_port)
        _close_handle(job)
        raise error

    actual = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    returned = wintypes.DWORD()
    if not _kernel32.QueryInformationJobObject(
        job,
        9,
        ctypes.byref(actual),
        ctypes.sizeof(actual),
        ctypes.byref(returned),
    ):
        error = _win_error("QueryInformationJobObject")
        _close_handle(completion_port)
        _close_handle(job)
        raise error
    actual_basic = actual.BasicLimitInformation
    if actual_basic.LimitFlags & _EXPECTED_JOB_FLAGS != _EXPECTED_JOB_FLAGS:
        _close_handle(completion_port)
        _close_handle(job)
        raise SandboxUnavailableError("the Job Object omitted required limit flags")
    if actual_basic.ActiveProcessLimit != limits.active_process_limit:
        _close_handle(completion_port)
        _close_handle(job)
        raise SandboxUnavailableError("the Job Object changed the process limit")
    if actual.ProcessMemoryLimit != limits.process_memory_bytes:
        _close_handle(completion_port)
        _close_handle(job)
        raise SandboxUnavailableError("the Job Object changed the process memory limit")
    if actual.JobMemoryLimit != limits.job_memory_bytes:
        _close_handle(completion_port)
        _close_handle(job)
        raise SandboxUnavailableError("the Job Object changed the job memory limit")
    if actual_basic.PerProcessUserTimeLimit != int(
        limits.cpu_time_seconds * 10_000_000
    ):
        _close_handle(completion_port)
        _close_handle(job)
        raise SandboxUnavailableError("the Job Object changed the CPU time limit")
    return (
        job,
        completion_port,
        {
            "completion_port_associated": True,
            "created_suspended_before_job_assignment": True,
            "kill_on_job_close": bool(
                actual_basic.LimitFlags & _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ),
            "active_process_limit": int(actual_basic.ActiveProcessLimit),
            "process_memory_limit": bool(
                actual_basic.LimitFlags & _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            ),
            "job_memory_limit": bool(
                actual_basic.LimitFlags & _JOB_OBJECT_LIMIT_JOB_MEMORY
            ),
            "cpu_time_limit": bool(
                actual_basic.LimitFlags & _JOB_OBJECT_LIMIT_PROCESS_TIME
            ),
            "wall_time_limit": True,
            "subprocess_fallback": False,
        },
    )


def _make_pipe() -> tuple[object, object]:
    security = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    read_handle = wintypes.HANDLE()
    write_handle = wintypes.HANDLE()
    if not _kernel32.CreatePipe(
        ctypes.byref(read_handle),
        ctypes.byref(write_handle),
        ctypes.byref(security),
        0,
    ):
        raise _win_error("CreatePipe")
    return read_handle, write_handle


def _make_inheritable_probe_event() -> object:
    security = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES),
        None,
        True,
    )
    event = _kernel32.CreateEventW(ctypes.byref(security), True, True, None)
    if not event:
        raise _win_error("CreateEventW")
    return event


def _drain_job_messages(
    completion_port: object,
    *,
    first_wait_ms: int = 0,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    wait_ms = first_wait_ms
    while True:
        message = wintypes.DWORD()
        completion_key = ctypes.c_size_t()
        process_id = wintypes.LPVOID()
        if not _kernel32.GetQueuedCompletionStatus(
            completion_port,
            ctypes.byref(message),
            ctypes.byref(completion_key),
            ctypes.byref(process_id),
            wait_ms,
        ):
            error = ctypes.get_last_error()
            if error == 258:
                break
            raise _win_error("GetQueuedCompletionStatus", error=error)
        messages.append(
            {
                "code": int(message.value),
                "name": _JOB_MESSAGE_NAMES.get(message.value, "UNKNOWN"),
                "process_id": int(process_id.value or 0),
            }
        )
        wait_ms = 0
    return messages


def _make_parent_end_private(handle: object) -> None:
    if not _kernel32.SetHandleInformation(handle, 0x00000001, 0):
        raise _win_error("SetHandleInformation")


def _base_user_environment() -> dict[str, str]:
    token = _open_current_token(0x0008)
    environment = wintypes.LPVOID()
    try:
        if not _userenv.CreateEnvironmentBlock(
            ctypes.byref(environment),
            token,
            False,
        ):
            raise _win_error("CreateEnvironmentBlock")
    finally:
        _close_handle(token)

    variables: dict[str, str] = {}
    try:
        address = int(environment.value or 0)
        if not address:
            raise SandboxUnavailableError("CreateEnvironmentBlock returned null")
        offset = 0
        wchar_size = ctypes.sizeof(ctypes.c_wchar)
        while True:
            entry = ctypes.wstring_at(address + offset * wchar_size)
            if not entry:
                break
            offset += len(entry) + 1
            if entry.startswith("=") or "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            variables[key] = value
    finally:
        if environment and not _userenv.DestroyEnvironmentBlock(environment):
            raise _win_error("DestroyEnvironmentBlock")
    return variables


def _environment_block(
    workspace: Path,
    python_executable: Path | None = None,
) -> ctypes.Array[str]:
    base = _base_user_environment()
    safe_names = {
        "allusersprofile",
        "commonprogramfiles",
        "commonprogramfiles(x86)",
        "commonprogramw6432",
        "computername",
        "comspec",
        "driverdata",
        "number_of_processors",
        "os",
        "pathext",
        "processor_architecture",
        "processor_identifier",
        "processor_level",
        "processor_revision",
        "programdata",
        "programfiles",
        "programfiles(x86)",
        "programw6432",
        "public",
        "systemdrive",
        "systemroot",
        "userdomain",
        "userdomain_roamingprofile",
        "username",
        "windir",
    }
    variables = {
        key: value for key, value in base.items() if key.casefold() in safe_names
    }
    system_root = next(
        (value for key, value in base.items() if key.casefold() == "systemroot"),
        r"C:\Windows",
    )
    python_dir = str((python_executable or Path(_python_executable())).resolve().parent)
    profile = workspace / "profile"
    appdata = profile / "AppData" / "Roaming"
    local_appdata = profile / "AppData" / "Local"
    appdata.mkdir(parents=True, exist_ok=True)
    local_appdata.mkdir(parents=True, exist_ok=True)
    profile_text = str(profile)
    profile_windows = Path(profile_text)
    home_path = (
        profile_text[len(profile_windows.drive) :] if profile_windows.drive else "\\"
    )
    variables.update(
        {
            "APPDATA": str(appdata),
            "HOMEDRIVE": profile_windows.drive or system_root[:2],
            "HOMEPATH": home_path,
            "LOCALAPPDATA": str(local_appdata),
            "PATH": f"{python_dir};{system_root}\\System32",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "SYSTEMROOT": system_root,
            "TEMP": str(workspace),
            "TMP": str(workspace),
            "USERPROFILE": profile_text,
            "WINDIR": system_root,
        }
    )
    block = (
        "\0".join(
            f"{key}={value}"
            for key, value in sorted(
                variables.items(), key=lambda item: item[0].casefold()
            )
        )
        + "\0\0"
    )
    return ctypes.create_unicode_buffer(block)


def _python_executable() -> str:
    return str(getattr(sys, "_base_executable", None) or sys.executable)


def _child_command(
    runtime_root: Path | None = None,
    python_executable: Path | None = None,
    limits: WorkerLimits | None = None,
) -> ctypes.Array[str]:
    src = runtime_root or Path(__file__).resolve().parents[2]
    if limits is None:
        child_call = "child_main()"
        limits_import = ""
    else:
        limit_arguments = ",".join(
            f"{item.name}={getattr(limits, item.name)!r}"
            for item in fields(WorkerLimits)
        )
        limits_import = (
            "from academic_pdf_en_zh_reader.security.limits import WorkerLimits;"
        )
        child_call = f"child_main(WorkerLimits({limit_arguments}))"
    bootstrap = (
        "import sys;"
        f"sys.path.insert(0,{str(src)!r});"
        f"{limits_import}"
        "from academic_pdf_en_zh_reader.security.windows_worker import child_main;"
        f"raise SystemExit({child_call})"
    )
    executable = str(python_executable or Path(_python_executable()))
    arguments = [executable, "-I"]
    if runtime_root is not None:
        arguments.append("-S")
    arguments.append("-B")
    arguments.extend(("-X", "no_debug_ranges"))
    arguments.extend(("-c", bootstrap))
    command = subprocess.list2cmdline(arguments)
    return ctypes.create_unicode_buffer(command)


def _create_process_as_user(
    token: object,
    command: object,
    flags: int,
    environment: object,
    workspace: Path,
    startup: object,
    process_info: object,
) -> bool:
    startup_pointer = ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW))
    return bool(
        _advapi32.CreateProcessAsUserW(
            token,
            None,
            command,
            None,
            None,
            True,
            flags,
            environment,
            str(workspace),
            startup_pointer,
            ctypes.byref(process_info),
        )
    )


def _create_process(
    command: object,
    flags: int,
    environment: object,
    workspace: Path,
    startup: object,
    process_info: object,
) -> bool:
    startup_pointer = ctypes.cast(ctypes.byref(startup), ctypes.POINTER(_STARTUPINFOW))
    return bool(
        _kernel32.CreateProcessW(
            None,
            command,
            None,
            None,
            True,
            flags,
            environment,
            str(workspace),
            startup_pointer,
            ctypes.byref(process_info),
        )
    )


def _bounded_pipe_reader(
    handle: object,
    limit: int,
    job: object,
    state: dict[str, Any],
) -> None:
    import msvcrt

    data = bytearray()
    converted = False
    try:
        descriptor = msvcrt.open_osfhandle(
            _handle_value(handle), os.O_RDONLY | os.O_BINARY
        )
        converted = True
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                if len(data) + len(chunk) > limit:
                    state["overflow"] = True
                    _kernel32.TerminateJobObject(job, 1)
                    break
                data.extend(chunk)
    except OSError as error:
        state["reader_error"] = str(error)
    finally:
        if not converted:
            _close_handle(handle)
        state["data"] = bytes(data)


def _write_pipe(handle: object, payload: bytes) -> None:
    import msvcrt

    try:
        descriptor = msvcrt.open_osfhandle(
            _handle_value(handle), os.O_WRONLY | os.O_BINARY
        )
    except OSError:
        _close_handle(handle)
        raise
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        stream.write(payload)
        stream.flush()


def _process_is_alive(process_id: int) -> bool:
    handle = _kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(
            _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            and exit_code.value == 259
        )
    finally:
        _close_handle(handle)


def _descendant_died(process_id: int) -> bool:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _process_is_alive(process_id):
            return True
        time.sleep(0.02)
    return not _process_is_alive(process_id)


def _close_job_and_verify_descendant(
    job: object,
    process_id: int,
) -> dict[str, object]:
    descendant = _kernel32.OpenProcess(0x00100000 | 0x1000, False, process_id)
    open_error = ctypes.get_last_error() if not descendant else 0
    _close_handle(job)
    if not descendant:
        return {
            "verified": False,
            "reason": (
                "OpenProcess(SYNCHRONIZE) failed before Job close "
                f"(WinError {open_error})"
            ),
        }
    try:
        wait_result = _kernel32.WaitForSingleObject(descendant, 2000)
        if wait_result != 0:
            return {
                "verified": False,
                "reason": f"descendant wait returned {wait_result}",
            }
        exit_code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(descendant, ctypes.byref(exit_code)):
            return {
                "verified": False,
                "reason": "GetExitCodeProcess(descendant) failed",
            }
        return {
            "verified": exit_code.value != 259,
            "exit_code": int(exit_code.value),
        }
    finally:
        _close_handle(descendant)


def _run_worker(
    request: WorkerRequest,
    workspace_root: Path,
    *,
    limits: WorkerLimits = DEFAULT_LIMITS,
    test_only_restricted_adapter: bool = False,
) -> WorkerRunResult:
    """Run one request; never fall back if native isolation cannot be created."""

    _require_windows()
    workspace = Path(workspace_root).resolve(strict=True)
    if not private_directory_is_current_user_only(workspace):
        raise SandboxUnavailableError(
            "worker root is not a verified current-user-only directory"
        )
    resolve_controlled_path(workspace, request.input_path, must_exist=True)
    request_bytes = encode_request(request, limits)

    restricted_token: object | None = None
    job: object | None = None
    completion_port: object | None = None
    process: object | None = None
    thread: object | None = None
    child_stdin: object | None = None
    parent_stdin: object | None = None
    parent_stdout: object | None = None
    child_stdout: object | None = None
    parent_stderr: object | None = None
    child_stderr: object | None = None
    stdout_thread: threading.Thread | None = None
    stderr_thread: threading.Thread | None = None
    stdout_reader_started = False
    stderr_reader_started = False
    stdout_state: dict[str, Any] = {}
    stderr_state: dict[str, Any] = {}
    provenance: dict[str, object] = {}
    timed_out = False
    appcontainer: _AppContainerContext | None = None
    process_attributes: _ProcessAttributeContext | None = None

    try:
        appcontainer = (
            None
            if test_only_restricted_adapter
            else _prepare_appcontainer(workspace, request)
        )
        process_workspace = (
            appcontainer.workspace if appcontainer is not None else workspace
        )
        privileges: list[str] = []
        token_origin = "AppContainer lowbox token via SECURITY_CAPABILITIES"
        if appcontainer is None:
            restricted_token, privileges, token_origin = _create_restricted_token()
        job, completion_port, provenance = _configure_job(limits)
        provenance["enabled_privileges"] = privileges
        provenance["restricted_token_origin"] = token_origin
        provenance["appcontainer_api_available"] = _appcontainer_apis_available()
        provenance["appcontainer_implemented"] = appcontainer is not None
        provenance["network_isolation_implemented"] = appcontainer is not None
        provenance["restricted_token"] = appcontainer is None
        provenance["appcontainer_lowbox"] = appcontainer is not None
        provenance["launcher"] = (
            "CreateProcessW" if appcontainer is not None else "CreateProcessAsUserW"
        )
        if appcontainer is not None:
            provenance["appcontainer_profile_mode"] = appcontainer.profile_mode
            provenance["profile_creation_hresult"] = (
                f"0x{appcontainer.profile_creation_hresult:08X}"
            )
            provenance["python_runtime_source"] = str(
                appcontainer.python_runtime_source
            )
            provenance["python_runtime_fingerprint"] = (
                appcontainer.python_runtime_fingerprint
            )
            provenance["python_runtime_file_count"] = (
                appcontainer.python_runtime_file_count
            )
            provenance["python_runtime_bytes"] = appcontainer.python_runtime_bytes
            provenance["project_runtime_fingerprint"] = getattr(
                appcontainer,
                "project_runtime_fingerprint",
                "test-double-unavailable",
            )
            provenance["project_runtime_file_count"] = getattr(
                appcontainer,
                "project_runtime_file_count",
                0,
            )
            provenance["project_runtime_bytes"] = getattr(
                appcontainer,
                "project_runtime_bytes",
                0,
            )
            provenance["pypdf_version"] = getattr(
                appcontainer,
                "pypdf_version",
                "test-double-unavailable",
            )
            provenance["pypdf_runtime_fingerprint"] = getattr(
                appcontainer,
                "pypdf_runtime_fingerprint",
                "test-double-unavailable",
            )
            provenance["pypdf_runtime_file_count"] = getattr(
                appcontainer,
                "pypdf_runtime_file_count",
                0,
            )
            provenance["pypdf_runtime_bytes"] = getattr(
                appcontainer,
                "pypdf_runtime_bytes",
                0,
            )
            provenance["pdfplumber_version"] = getattr(
                appcontainer,
                "pdfplumber_version",
                "test-double-unavailable",
            )
            provenance["pdfminer_version"] = getattr(
                appcontainer,
                "pdfminer_version",
                "test-double-unavailable",
            )
            provenance["extraction_runtime_fingerprint"] = getattr(
                appcontainer,
                "extraction_runtime_fingerprint",
                "test-double-unavailable",
            )
            provenance["extraction_runtime_file_count"] = getattr(
                appcontainer,
                "extraction_runtime_file_count",
                0,
            )
            provenance["extraction_runtime_bytes"] = getattr(
                appcontainer,
                "extraction_runtime_bytes",
                0,
            )
        child_stdin, parent_stdin = _make_pipe()
        parent_stdout, child_stdout = _make_pipe()
        parent_stderr, child_stderr = _make_pipe()
        _make_parent_end_private(parent_stdin)
        _make_parent_end_private(parent_stdout)
        _make_parent_end_private(parent_stderr)

        process_attributes = _prepare_process_attributes(
            (child_stdin, child_stdout, child_stderr),
            appcontainer,
        )
        provenance["handle_list_enforced"] = True
        provenance["inherited_handle_count"] = len(
            process_attributes.inherited_handle_values
        )
        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x00000100
        startup.StartupInfo.hStdInput = child_stdin
        startup.StartupInfo.hStdOutput = child_stdout
        startup.StartupInfo.hStdError = child_stderr
        startup.lpAttributeList = process_attributes.attribute_list
        process_info = _PROCESS_INFORMATION()
        child_python = (
            appcontainer.python_runtime_root / "pythonw.exe"
            if appcontainer is not None
            else Path(_python_executable()).with_name("pythonw.exe")
        )
        if not child_python.is_file():
            raise SandboxUnavailableError(
                "headless Python worker executable is missing"
            )
        provenance["worker_executable"] = child_python.name
        command = _child_command(
            appcontainer.runtime_root if appcontainer is not None else None,
            child_python,
            limits if appcontainer is not None else None,
        )
        environment = _environment_block(process_workspace, child_python)
        flags = 0x00000004 | 0x00000400 | 0x00080000 | 0x08000000
        if appcontainer is not None:
            created = _create_process(
                command,
                flags,
                environment,
                process_workspace,
                startup,
                process_info,
            )
            create_action = "CreateProcessW(AppContainer)"
        else:
            created = _create_process_as_user(
                restricted_token,
                command,
                flags,
                environment,
                process_workspace,
                startup,
                process_info,
            )
            create_action = "CreateProcessAsUserW(restricted token)"
        if not created:
            error = ctypes.get_last_error()
            detail = _win_error(create_action, error=error)
            if appcontainer is not None:
                raise SandboxUnavailableError(
                    f"{detail}; AppContainer profile mode={appcontainer.profile_mode}, "
                    "zero capabilities, "
                    f"profile HRESULT 0x{appcontainer.profile_creation_hresult:08X}"
                )
            raise detail
        process_attributes.cleanup()
        process_attributes = None
        process = process_info.hProcess
        thread = process_info.hThread
        _close_handle(child_stdin)
        child_stdin = None
        _close_handle(child_stdout)
        child_stdout = None
        _close_handle(child_stderr)
        child_stderr = None

        if not _kernel32.AssignProcessToJobObject(job, process):
            assign_error = ctypes.get_last_error()
            cleanup_errors: list[str] = []
            if not _kernel32.TerminateProcess(process, 1):
                cleanup_errors.append(
                    str(_win_error("TerminateProcess(unassigned child)"))
                )
            wait_result = _kernel32.WaitForSingleObject(process, 5000)
            if wait_result != 0:
                cleanup_errors.append(
                    "WaitForSingleObject(unassigned child) "
                    f"returned {wait_result} instead of WAIT_OBJECT_0"
                )
            detail = _win_error("AssignProcessToJobObject", error=assign_error)
            if cleanup_errors:
                detail.add_note("; ".join(cleanup_errors))
            raise detail

        stdout_thread = threading.Thread(
            target=_bounded_pipe_reader,
            args=(parent_stdout, limits.max_protocol_bytes, job, stdout_state),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_bounded_pipe_reader,
            args=(parent_stderr, limits.max_protocol_bytes, job, stderr_state),
            daemon=True,
        )
        stdout_thread.start()
        stdout_reader_started = True
        parent_stdout = None
        stderr_thread.start()
        stderr_reader_started = True
        parent_stderr = None

        if _kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            _kernel32.TerminateJobObject(job, 1)
            raise _win_error("ResumeThread")
        _close_handle(thread)
        thread = None
        with suppress(BrokenPipeError, OSError):
            _write_pipe(parent_stdin, request_bytes)
        parent_stdin = None

        wait_result = _kernel32.WaitForSingleObject(
            process, max(1, int(limits.wall_time_seconds * 1000))
        )
        if wait_result == 258:
            timed_out = True
            _kernel32.TerminateJobObject(job, 1460)
            _kernel32.WaitForSingleObject(process, 5000)
        elif wait_result != 0:
            _kernel32.TerminateJobObject(job, 1)
            raise _win_error("WaitForSingleObject")

        exit_code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            raise _win_error("GetExitCodeProcess")
        job_messages = _drain_job_messages(completion_port, first_wait_ms=100)
        provenance["job_messages"] = job_messages
        message_names = {str(message["name"]) for message in job_messages}
        _close_handle(process)
        process = None
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        readers_alive = stdout_thread.is_alive() or stderr_thread.is_alive()
        stdout_thread = None
        stderr_thread = None

        if stdout_state.get("overflow") or stderr_state.get("overflow"):
            raise WorkerOutputLimitError("worker output exceeded the protocol limit")
        if readers_alive:
            raise WorkerExecutionError("worker pipe did not close with the Job Object")
        if timed_out:
            raise WorkerTimeoutError("worker exceeded the wall-clock limit")
        if exit_code.value:
            if "END_OF_PROCESS_TIME" in message_names:
                raise WorkerCpuLimitError(
                    "Job completion port reported END_OF_PROCESS_TIME",
                    job_messages,
                )
            if message_names & {"PROCESS_MEMORY_LIMIT", "JOB_MEMORY_LIMIT"}:
                raise WorkerMemoryLimitError(
                    "Job completion port reported a memory-limit event",
                    job_messages,
                )
            detail = stderr_state.get("data", b"")[:512].decode(
                "utf-8", errors="replace"
            )
            raise WorkerExecutionError(
                f"isolated worker exited with code {exit_code.value}: {detail}"
            )
        try:
            response = decode_response(stdout_state.get("data", b""), limits)
        except ProtocolError as error:
            raise WorkerExecutionError(
                f"worker returned invalid protocol data: {error}"
            ) from error
        if response.status != "ok":
            error = response.error or {
                "code": "WORKER_EXECUTION_FAILED",
                "message": "isolated operation failed",
            }
            raise WorkerReportedError(error["code"], error["message"])

        artifact_bytes: bytes | None = None
        normalized_pdf_bytes: bytes | None = None
        normalization_artifact_bytes: bytes | None = None
        if request.operation == "preflight":
            if appcontainer is None:
                raise SandboxUnavailableError(
                    "preflight requires a zero-capability AppContainer"
                )
            if response.result is None:
                raise WorkerExecutionError(
                    "preflight worker omitted its artifact descriptor"
                )
            artifact_bytes = _read_preflight_artifact(
                appcontainer.workspace,
                response.result,
                limits,
            )
        elif request.operation == "extract":
            if appcontainer is None:
                raise SandboxUnavailableError(
                    "extraction requires a zero-capability AppContainer"
                )
            if response.result is None:
                raise WorkerExecutionError(
                    "extraction worker omitted its artifact descriptor"
                )
            artifact_bytes = _read_extraction_artifact(
                appcontainer.workspace,
                response.result,
                limits,
            )
        elif request.operation == "normalize":
            if appcontainer is None:
                raise SandboxUnavailableError(
                    "normalization requires a zero-capability AppContainer"
                )
            if response.result is None:
                raise WorkerExecutionError(
                    "normalization worker omitted its artifact descriptors"
                )
            normalized_pdf_bytes, normalization_artifact_bytes = (
                _read_normalization_artifacts(
                    appcontainer.workspace,
                    response.result,
                    limits,
                )
            )
            artifact_bytes = normalization_artifact_bytes

        child_pid = response.result.get("child_pid") if response.result else None
        if isinstance(child_pid, int):
            kill_evidence = _close_job_and_verify_descendant(job, child_pid)
            job = None
            provenance["descendant_kill_evidence"] = kill_evidence
            provenance["descendant_killed_on_job_close"] = bool(
                kill_evidence.get("verified")
            )
        else:
            _close_handle(job)
            job = None
        _close_handle(completion_port)
        completion_port = None
        return WorkerRunResult(
            response=response,
            provenance=provenance,
            exit_code=exit_code.value,
            artifact_bytes=artifact_bytes,
            normalized_pdf_bytes=normalized_pdf_bytes,
            normalization_artifact_bytes=normalization_artifact_bytes,
        )
    finally:
        if job:
            _kernel32.TerminateJobObject(job, 1)
        for handle in (
            process,
            thread,
            child_stdin,
            parent_stdin,
            parent_stdout,
            child_stdout,
            parent_stderr,
            child_stderr,
            job,
            completion_port,
            restricted_token,
        ):
            _close_handle(handle)
        if stdout_thread is not None and stdout_reader_started:
            stdout_thread.join(timeout=2)
        if stderr_thread is not None and stderr_reader_started:
            stderr_thread.join(timeout=2)
        reader_cleanup_failed = bool(
            (
                stdout_thread is not None
                and stdout_reader_started
                and stdout_thread.is_alive()
            )
            or (
                stderr_thread is not None
                and stderr_reader_started
                and stderr_thread.is_alive()
            )
        )
        stdout_thread = None
        stderr_thread = None
        if process_attributes is not None:
            process_attributes.cleanup()
        if appcontainer is not None:
            cleanup_errors = appcontainer.cleanup()
            provenance["appcontainer_cleanup_verified"] = not cleanup_errors and not (
                appcontainer.workspace.exists() or appcontainer.profile_folder.exists()
            )
            if cleanup_errors:
                cleanup_detail = "; ".join(cleanup_errors)
                if sys.exception() is None:
                    raise SandboxUnavailableError(cleanup_detail)
                sys.exception().add_note(cleanup_detail)  # type: ignore[union-attr]
        if reader_cleanup_failed:
            cleanup_detail = "worker pipe readers did not stop during cleanup"
            if sys.exception() is None:
                raise WorkerExecutionError(cleanup_detail)
            sys.exception().add_note(cleanup_detail)  # type: ignore[union-attr]


def run_worker(
    request: WorkerRequest,
    workspace_root: Path,
    *,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> WorkerRunResult:
    """Run only with the complete AppContainer contract; never weaken production."""

    return _run_worker(request, workspace_root, limits=limits)


def _run_restricted_worker_for_test(
    request: WorkerRequest,
    workspace_root: Path,
    *,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> WorkerRunResult:
    """Exercise restricted-token/Job mechanics without a production fallback."""

    return _run_worker(
        request,
        workspace_root,
        limits=limits,
        test_only_restricted_adapter=True,
    )


def _token_capability_count(token: object) -> int:
    size = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, 30, None, 0, ctypes.byref(size))
    if not size.value:
        raise _win_error("GetTokenInformation(TokenCapabilities) sizing")
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(
        token,
        30,
        buffer,
        size,
        ctypes.byref(size),
    ):
        raise _win_error("GetTokenInformation(TokenCapabilities)")
    return int(ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value)


def _current_process_security() -> tuple[bool, list[str], bool, int]:
    token = _open_current_token(0x0008)
    try:
        appcontainer = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not _advapi32.GetTokenInformation(
            token,
            29,
            ctypes.byref(appcontainer),
            ctypes.sizeof(appcontainer),
            ctypes.byref(returned),
        ):
            raise _win_error("GetTokenInformation(TokenIsAppContainer)")
        is_appcontainer = bool(appcontainer.value)
        capability_count = _token_capability_count(token) if is_appcontainer else 0
        return (
            bool(_advapi32.IsTokenRestricted(token)),
            _enabled_privileges(token),
            is_appcontainer,
            capability_count,
        )
    finally:
        _close_handle(token)


def _current_process_is_appcontainer() -> bool:
    token = _open_current_token(0x0008)
    try:
        appcontainer = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not _advapi32.GetTokenInformation(
            token,
            29,
            ctypes.byref(appcontainer),
            ctypes.sizeof(appcontainer),
            ctypes.byref(returned),
        ):
            raise _win_error("GetTokenInformation(TokenIsAppContainer)")
        return bool(appcontainer.value)
    finally:
        _close_handle(token)


def _child_path_is_reparse(path: Path) -> bool:
    information = path.lstat()
    return stat.S_ISLNK(information.st_mode) or bool(
        getattr(information, "st_file_attributes", 0) & 0x400
    )


def _resolve_prevalidated_appcontainer_path(
    relative_path: str,
    *,
    must_exist: bool = True,
) -> Path:
    """Stay lexical inside the parent-verified workspace; never resolve ancestors."""

    relative = validate_relative_path(relative_path)
    root = Path(os.path.abspath(os.getcwd()))
    try:
        if not root.is_dir() or _child_path_is_reparse(root):
            raise ProtocolError("AppContainer workspace root is not a real directory")
    except OSError as error:
        raise ProtocolError(
            f"AppContainer workspace is unavailable: {error}"
        ) from error

    candidate = root.joinpath(*relative.parts)
    try:
        common = os.path.commonpath((str(root), str(candidate)))
    except ValueError as error:
        raise ProtocolError("path is on a different root") from error
    if os.path.normcase(common) != os.path.normcase(str(root)):
        raise ProtocolError("path escapes the AppContainer workspace")

    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        final = index == len(relative.parts) - 1
        try:
            is_reparse = _child_path_is_reparse(current)
        except FileNotFoundError as error:
            if not final or must_exist:
                raise ProtocolError(
                    "path does not exist inside the AppContainer workspace"
                ) from error
            break
        except OSError as error:
            raise ProtocolError(f"workspace path is unavailable: {error}") from error
        if is_reparse:
            raise ProtocolError("path contains a symlink or reparse point")
    return candidate


def _read_prevalidated_appcontainer_file(
    relative_path: str,
    *,
    max_bytes: int,
) -> BoundedRegularFile:
    """Read one parent-verified worker file without leaving the workspace.

    The AppContainer workspace and its input path were already copied, hashed,
    and checked by the parent.  Reusing the parent-side input reader here would
    inspect ancestors outside that workspace, which a zero-capability token is
    deliberately unable to access.
    """

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")
    path = _resolve_prevalidated_appcontainer_path(
        relative_path,
        must_exist=True,
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_size <= 0
            or information.st_size > max_bytes
        ):
            raise ProtocolError("worker-local file is not a bounded regular file")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while total < information.st_size:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, information.st_size - total),
            )
            if not chunk:
                raise ProtocolError("worker-local file size changed while reading")
            chunks.append(chunk)
            digest.update(chunk)
            total += len(chunk)
        if os.fstat(descriptor).st_size != information.st_size:
            raise ProtocolError("worker-local file size changed while reading")
    finally:
        os.close(descriptor)
    return BoundedRegularFile(
        data=b"".join(chunks),
        sha256=digest.hexdigest(),
        size=total,
    )


def _normalized_windows_error(error: OSError) -> int:
    value = getattr(error, "winerror", None)
    if not isinstance(value, int):
        value = error.errno if isinstance(error.errno, int) else 0
    return value & 0xFFFFFFFF


def _excluded_event_evidence(handle_value: int) -> dict[str, object]:
    handle = wintypes.HANDLE(handle_value)
    try:
        flags = wintypes.DWORD()
        if not _kernel32.GetHandleInformation(handle, ctypes.byref(flags)):
            error = ctypes.get_last_error() & 0xFFFFFFFF
            return {
                "excluded": error in {6, 0xC0000008},
                "error_code": error,
                "event_handshake": False,
            }
        reset = bool(_kernel32.ResetEvent(handle))
        reset_error = ctypes.get_last_error() & 0xFFFFFFFF if not reset else 0
        wait_after_reset = _kernel32.WaitForSingleObject(handle, 0) if reset else -1
        set_again = bool(_kernel32.SetEvent(handle)) if reset else False
        wait_after_set = _kernel32.WaitForSingleObject(handle, 0) if set_again else -1
        inherited_event = bool(
            reset and wait_after_reset == 258 and set_again and wait_after_set == 0
        )
        return {
            "excluded": not inherited_event,
            "error_code": reset_error,
            "event_handshake": inherited_event,
        }
    except OSError as error:
        code = _normalized_windows_error(error)
        if code not in {6, 0xC0000008}:
            raise
        return {
            "excluded": True,
            "error_code": code,
            "event_handshake": False,
        }


def _probe_case(request: WorkerRequest) -> dict[str, object]:
    case = request.parameters.get("case")
    if not isinstance(case, str):
        raise ValueError("probe case must be a string")
    if _current_process_is_appcontainer():
        _resolve_prevalidated_appcontainer_path(request.input_path, must_exist=True)
    else:
        resolve_controlled_path(Path.cwd(), request.input_path, must_exist=True)
    if case == "inspect":
        restricted, privileges, appcontainer, capability_count = (
            _current_process_security()
        )
        return {
            "restricted_token": restricted,
            "enabled_privileges": privileges,
            "appcontainer": appcontainer,
            "capability_count": capability_count,
            "process_id": os.getpid(),
        }
    if case == "outside_access":
        outside_parameter = request.parameters.get("path")
        outside = (
            Path(outside_parameter)
            if isinstance(outside_parameter, str)
            else Path.cwd().parent / "input.pdf"
        )
        try:
            outside.read_bytes()
        except OSError as error:
            return {
                "outside_access_denied": True,
                "error_code": getattr(error, "winerror", None),
            }
        return {"outside_access_denied": False, "error_code": None}
    if case == "network_connect":
        import socket

        port = int(request.parameters.get("port", 0))
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=1)
        except OSError as error:
            return {
                "network_denied": True,
                "error_code": getattr(error, "winerror", None),
            }
        connection.close()
        return {"network_denied": False, "error_code": None}
    if case == "parent_process_access":
        parent_id = int(request.parameters.get("parent_id", 0))
        access_masks = {
            "write": 0x0008 | 0x0020,
            "duplicate_handle": 0x0040,
            "inject_thread": 0x0002 | 0x0008 | 0x0020,
        }
        results: dict[str, object] = {}
        for name, access in access_masks.items():
            handle = _kernel32.OpenProcess(access, False, parent_id)
            error = ctypes.get_last_error() if not handle else 0
            if handle:
                _close_handle(handle)
            results[name] = {"denied": not bool(handle), "error_code": error}
        return {"access": results}
    if case == "excluded_inheritable_handle":
        handle_value = int(request.parameters.get("handle", 0))
        return _excluded_event_evidence(handle_value)
    if case == "sleep":
        seconds = float(request.parameters.get("seconds", 0))
        time.sleep(seconds)
        return {"slept": seconds}
    if case == "spawn_child":
        child: subprocess.Popen[bytes] | None = None
        try:
            child = subprocess.Popen(  # noqa: S603 - adversarial sandbox probe
                [_python_executable(), "-I", "-c", "import time;time.sleep(10)"],
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.2)
            denied = child.poll() is not None
            if not denied:
                child.terminate()
            return {"spawn_denied": denied, "error_code": None}
        except OSError as error:
            return {
                "spawn_denied": True,
                "error_code": _normalized_windows_error(error),
            }
        finally:
            if child is not None:
                with suppress(OSError):
                    if child.poll() is None:
                        child.terminate()
    if case == "spawn_for_kill_probe":
        child = subprocess.Popen(  # noqa: S603 - verifies kill-on-close
            [_python_executable(), "-I", "-c", "import time;time.sleep(10)"],
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"child_pid": child.pid}
    if case == "busy_loop":
        while True:
            pass
    if case == "busy_for":
        duration = float(request.parameters.get("seconds", 0))
        deadline = time.process_time() + duration
        while time.process_time() < deadline:
            pass
        return {"cpu_seconds": duration}
    if case == "allocate":
        amount = int(request.parameters.get("bytes", 0))
        allocation = bytearray(amount)
        for offset in range(0, amount, 4096):
            allocation[offset] = 1
        time.sleep(float(request.parameters.get("seconds", 10)))
        return {"allocated": len(allocation)}
    if case == "oversize_output":
        sys.stdout.buffer.write(b"x" * (1024 * 1024))
        sys.stdout.buffer.flush()
        raise SystemExit(0)
    if case == "malformed_output":
        sys.stdout.buffer.write(b"not-json")
        sys.stdout.buffer.flush()
        raise SystemExit(0)
    raise ValueError("unknown probe case")


def _preflight_limits_for_worker(limits: WorkerLimits):
    from academic_pdf_en_zh_reader.preflight.checks import PreflightLimits

    return PreflightLimits(
        max_file_bytes=limits.max_input_bytes,
        max_pages=limits.max_pages,
        max_objects=limits.max_objects,
        max_recursion_depth=limits.max_recursion_depth,
        max_decoded_stream_bytes=limits.max_uncompressed_bytes,
        max_image_bytes=limits.max_image_bytes,
    )


def _write_preflight_artifact(
    artifact: Mapping[str, object],
    limits: WorkerLimits,
) -> dict[str, object]:
    from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

    encoded = canonical_json_bytes(artifact)
    if not encoded or len(encoded) > limits.max_result_object_bytes:
        raise ProtocolError("preflight artifact exceeds the result-object limit")
    root = Path(os.path.abspath(os.getcwd()))
    output_directory = root / "output"
    try:
        output_directory.mkdir()
    except FileExistsError as error:
        raise ProtocolError("preflight output directory already exists") from error
    if _child_path_is_reparse(output_directory) or not output_directory.is_dir():
        raise ProtocolError("preflight output directory is not a real directory")
    destination = _resolve_prevalidated_appcontainer_path(
        PREFLIGHT_ARTIFACT_PATH,
        must_exist=False,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(encoded)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("preflight artifact write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "artifact_path": PREFLIGHT_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
        "artifact_bytes": len(encoded),
    }


def _write_extraction_artifact(
    artifact: Mapping[str, object],
    limits: WorkerLimits,
) -> dict[str, object]:
    from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

    encoded = canonical_json_bytes(artifact)
    if not encoded or len(encoded) > limits.max_extraction_artifact_bytes:
        raise ProtocolError("extraction artifact exceeds its size limit")
    root = Path(os.path.abspath(os.getcwd()))
    output_directory = root / "output"
    try:
        output_directory.mkdir()
    except FileExistsError as error:
        raise ProtocolError("extraction output directory already exists") from error
    if _child_path_is_reparse(output_directory) or not output_directory.is_dir():
        raise ProtocolError("extraction output directory is not a real directory")
    destination = _resolve_prevalidated_appcontainer_path(
        EXTRACTION_ARTIFACT_PATH,
        must_exist=False,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(encoded)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("extraction artifact write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "artifact_path": EXTRACTION_ARTIFACT_PATH,
        "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
        "artifact_bytes": len(encoded),
    }


def _write_normalization_artifacts(
    pdf_bytes: bytes,
    artifact: Mapping[str, object],
    limits: WorkerLimits,
) -> dict[str, object]:
    from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

    if (
        not isinstance(pdf_bytes, bytes)
        or not pdf_bytes
        or len(pdf_bytes) > limits.max_normalized_pdf_bytes
    ):
        raise ProtocolError("normalized PDF exceeds its size limit")
    encoded = canonical_json_bytes(artifact)
    if not encoded or len(encoded) > limits.max_result_object_bytes:
        raise ProtocolError("normalization artifact exceeds the result-object limit")

    root = Path(os.path.abspath(os.getcwd()))
    output_directory = root / "output"
    try:
        output_directory.mkdir()
    except FileExistsError as error:
        raise ProtocolError("normalization output directory already exists") from error
    if _child_path_is_reparse(output_directory) or not output_directory.is_dir():
        raise ProtocolError("normalization output directory is not a real directory")

    def write_one(relative_path: str, payload: bytes) -> None:
        destination = _resolve_prevalidated_appcontainer_path(
            relative_path,
            must_exist=False,
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o600)
        try:
            view = memoryview(payload)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise OSError("normalization artifact write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    try:
        write_one(NORMALIZATION_PDF_PATH, pdf_bytes)
        write_one(NORMALIZATION_ARTIFACT_PATH, encoded)
    except BaseException:
        for relative_path in (
            NORMALIZATION_PDF_PATH,
            NORMALIZATION_ARTIFACT_PATH,
        ):
            with suppress(OSError, ProtocolError):
                _resolve_prevalidated_appcontainer_path(
                    relative_path,
                    must_exist=True,
                ).unlink()
        raise
    return {
        "normalized_pdf": {
            "artifact_path": NORMALIZATION_PDF_PATH,
            "artifact_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "artifact_bytes": len(pdf_bytes),
        },
        "normalization_artifact": {
            "artifact_path": NORMALIZATION_ARTIFACT_PATH,
            "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
            "artifact_bytes": len(encoded),
        },
    }


def _run_preflight_request(
    request: WorkerRequest,
    limits: WorkerLimits,
) -> WorkerResponse:
    _restricted, _privileges, is_appcontainer, capability_count = (
        _current_process_security()
    )
    if not is_appcontainer or capability_count != 0:
        return WorkerResponse.failed(
            "SANDBOX_CONTRACT_UNVERIFIED",
            "zero-capability AppContainer token required",
        )

    input_path = _resolve_prevalidated_appcontainer_path(
        request.input_path,
        must_exist=True,
    )
    from academic_pdf_en_zh_reader.preflight.checks import (
        run_preflight_in_worker,
    )

    artifact = run_preflight_in_worker(
        input_path,
        limits=_preflight_limits_for_worker(limits),
    )
    source_sha256 = request.parameters["source_sha256"]
    input_bytes = request.parameters["input_bytes"]
    observed_limits = artifact.get("limits")
    observed_file = (
        observed_limits.get("file_bytes") if isinstance(observed_limits, dict) else None
    )
    if (
        artifact.get("source_sha256") != source_sha256
        or not isinstance(observed_file, dict)
        or observed_file.get("observed") != input_bytes
    ):
        raise ProtocolError("preflight core source identity mismatch")
    return WorkerResponse.ok(_write_preflight_artifact(artifact, limits))


def _strict_json_object(encoded: bytes) -> dict[str, object]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    decoded = json.loads(
        encoded.decode("utf-8", errors="strict"),
        parse_constant=reject_constant,
    )
    if not isinstance(decoded, dict):
        raise ProtocolError("JSON artifact root must be an object")
    return decoded


def _validated_extraction_preflight(
    request: WorkerRequest,
    limits: WorkerLimits,
) -> tuple[dict[str, object], bytes]:
    from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

    path = _resolve_prevalidated_appcontainer_path(
        EXTRACTION_PREFLIGHT_PATH,
        must_exist=True,
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_size <= 0
            or information.st_size > limits.max_result_object_bytes
        ):
            raise ProtocolError("preflight handoff is not a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(
            descriptor,
            min(1024 * 1024, information.st_size - total),
        ):
            chunks.append(chunk)
            total += len(chunk)
        if total != information.st_size:
            raise ProtocolError("preflight handoff size changed")
        handoff_bytes = b"".join(chunks)
    finally:
        os.close(descriptor)
    expected_hash = request.parameters["preflight_sha256"]
    if hashlib.sha256(handoff_bytes).hexdigest() != expected_hash:
        raise ProtocolError("preflight handoff hash differs")
    artifact = _strict_json_object(handoff_bytes)
    if canonical_json_bytes(artifact) != handoff_bytes:
        raise ProtocolError("preflight handoff is not canonical JSON")
    observed_limits = artifact.get("limits")
    observed_file = (
        observed_limits.get("file_bytes") if isinstance(observed_limits, dict) else None
    )
    pages = artifact.get("pages")
    if (
        artifact.get("schema_version") != "1.0.0"
        or artifact.get("artifact_kind") != "preflight"
        or artifact.get("passed") is not True
        or artifact.get("source_sha256") != request.parameters["source_sha256"]
        or not isinstance(observed_file, dict)
        or type(observed_file.get("observed")) is not int
        or observed_file["observed"] <= 0
        or not isinstance(pages, list)
    ):
        raise ProtocolError("preflight handoff identity differs")
    return artifact, handoff_bytes


def _validated_extraction_normalization(
    request: WorkerRequest,
    limits: WorkerLimits,
) -> tuple[dict[str, object], bytes]:
    from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

    path = _resolve_prevalidated_appcontainer_path(
        EXTRACTION_NORMALIZATION_PATH,
        must_exist=True,
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_size <= 0
            or information.st_size > limits.max_result_object_bytes
        ):
            raise ProtocolError("normalization handoff is not a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(
            descriptor,
            min(1024 * 1024, information.st_size - total),
        ):
            chunks.append(chunk)
            total += len(chunk)
        if total != information.st_size:
            raise ProtocolError("normalization handoff size changed")
        handoff_bytes = b"".join(chunks)
    finally:
        os.close(descriptor)
    if (
        hashlib.sha256(handoff_bytes).hexdigest()
        != request.parameters["normalization_sha256"]
    ):
        raise ProtocolError("normalization handoff hash differs")
    artifact = _strict_json_object(handoff_bytes)
    if canonical_json_bytes(artifact) != handoff_bytes:
        raise ProtocolError("normalization handoff is not canonical JSON")
    pages = artifact.get("pages")
    if (
        set(artifact)
        != {
            "schema_version",
            "artifact_kind",
            "policy_version",
            "source_sha256",
            "preflight_sha256",
            "normalized_pdf_sha256",
            "normalized_pdf_bytes",
            "pages",
        }
        or artifact.get("schema_version") != "1.0.0"
        or artifact.get("artifact_kind") != "normalization"
        or artifact.get("policy_version") != "1.0.0"
        or artifact.get("source_sha256") != request.parameters["source_sha256"]
        or artifact.get("preflight_sha256") != request.parameters["preflight_sha256"]
        or artifact.get("normalized_pdf_sha256")
        != request.parameters["normalized_pdf_sha256"]
        or artifact.get("normalized_pdf_bytes") != request.parameters["input_bytes"]
        or not isinstance(pages, list)
        or not pages
    ):
        raise ProtocolError("normalization handoff identity differs")
    return artifact, handoff_bytes


def _extraction_counts(pages: list[object]) -> dict[str, int]:
    names = {
        "chars": "character_count",
        "rectangles": "vector_count",
        "curves": "vector_count",
        "images": "image_count",
        "lines": "line_count",
        "graphic_regions": "graphic_region_count",
        "captions": "caption_count",
        "references": "reference_count",
    }
    counts = {
        "page_count": len(pages),
        "character_count": 0,
        "vector_count": 0,
        "image_count": 0,
        "line_count": 0,
        "graphic_region_count": 0,
        "caption_count": 0,
        "reference_count": 0,
    }
    for page in pages:
        if not isinstance(page, dict):
            raise ProtocolError("extraction core returned a non-object page")
        for field, count_name in names.items():
            items = page.get(field)
            if not isinstance(items, list):
                raise ProtocolError("extraction core returned an invalid collection")
            counts[count_name] += len(items)
    return counts


def _run_extraction_request(
    request: WorkerRequest,
    limits: WorkerLimits,
) -> WorkerResponse:
    _restricted, _privileges, is_appcontainer, capability_count = (
        _current_process_security()
    )
    if not is_appcontainer or capability_count != 0:
        return WorkerResponse.failed(
            "SANDBOX_CONTRACT_UNVERIFIED",
            "zero-capability AppContainer token required",
        )
    input_path = _resolve_prevalidated_appcontainer_path(
        request.input_path,
        must_exist=True,
    )
    information = input_path.stat()
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_size > limits.max_normalized_pdf_bytes
        or information.st_size != request.parameters["input_bytes"]
        or _file_sha256(input_path) != request.parameters["normalized_pdf_sha256"]
    ):
        raise ProtocolError("extraction source identity differs")
    try:
        preflight, preflight_bytes = _validated_extraction_preflight(request, limits)
    except (OSError, ProtocolError, UnsafeInputError, ValueError):
        return WorkerResponse.failed(
            "PREFLIGHT_IDENTITY_INVALID",
            "preflight identity verification failed",
        )
    try:
        normalization, normalization_bytes = _validated_extraction_normalization(
            request,
            limits,
        )
    except (OSError, ProtocolError, UnsafeInputError, ValueError):
        return WorkerResponse.failed(
            "NORMALIZATION_IDENTITY_INVALID",
            "normalization identity verification failed",
        )
    try:
        from academic_pdf_en_zh_reader.extraction import (
            ScannedPdfUnsupportedError,
            extract_document,
        )
    except (ImportError, OSError):
        return WorkerResponse.failed(
            "EXTRACTION_RUNTIME_UNAVAILABLE",
            "extraction runtime could not be loaded",
        )
    try:
        extracted = extract_document(input_path)
    except ScannedPdfUnsupportedError:
        return WorkerResponse.failed(
            "SCANNED_PDF_UNSUPPORTED",
            "scanned or OCR-overlay PDFs are unsupported",
        )
    except MemoryError:
        return WorkerResponse.failed(
            "WORKER_LIMIT_EXCEEDED",
            "PDF extraction exceeded the worker memory limit",
        )
    except Exception:
        return WorkerResponse.failed(
            "EXTRACTION_FAILED",
            "PDF extraction failed",
        )
    pages = extracted.get("pages")
    if (
        extracted.get("format_version") != "1.0.0"
        or not isinstance(pages, list)
        or len(pages) != len(preflight["pages"])
        or len(pages) != len(normalization["pages"])
    ):
        raise ProtocolError("extraction core result differs from preflight")
    if any(
        not isinstance(page, dict)
        or page.get("media_box_mpt") != [0, 0, 595_276, 841_890]
        or page.get("crop_box_mpt") != [0, 0, 595_276, 841_890]
        or page.get("rotation_degrees") != 0
        for page in pages
    ):
        raise ProtocolError("extraction core result is not normalized A4")
    artifact: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "extraction",
        "source_sha256": request.parameters["source_sha256"],
        "normalized_pdf_sha256": request.parameters["normalized_pdf_sha256"],
        "preflight_sha256": hashlib.sha256(preflight_bytes).hexdigest(),
        "normalization_sha256": hashlib.sha256(normalization_bytes).hexdigest(),
        "format_version": "1.1.0",
        "counts": _extraction_counts(pages),
        "pages": pages,
    }
    try:
        descriptor = _write_extraction_artifact(artifact, limits)
    except MemoryError:
        return WorkerResponse.failed(
            "WORKER_LIMIT_EXCEEDED",
            "PDF extraction exceeded the worker memory limit",
        )
    except (OSError, ProtocolError, ValueError):
        return WorkerResponse.failed(
            "EXTRACTION_ARTIFACT_WRITE_FAILED",
            "extraction artifact could not be written",
        )
    return WorkerResponse.ok(descriptor)


def _stable_normalization_error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if (
        isinstance(code, str)
        and 1 <= len(code) <= 64
        and "A" <= code[0] <= "Z"
        and all(
            character == "_" or "0" <= character <= "9" or "A" <= character <= "Z"
            for character in code
        )
    ):
        return code
    return "NORMALIZATION_FAILED"


def _run_normalization_request(
    request: WorkerRequest,
    limits: WorkerLimits,
) -> WorkerResponse:
    _restricted, _privileges, is_appcontainer, capability_count = (
        _current_process_security()
    )
    if not is_appcontainer or capability_count != 0:
        return WorkerResponse.failed(
            "SANDBOX_CONTRACT_UNVERIFIED",
            "zero-capability AppContainer token required",
        )
    try:
        source = _read_prevalidated_appcontainer_file(
            request.input_path,
            max_bytes=limits.max_input_bytes,
        )
    except (OSError, UnsafeInputError, ValueError):
        return WorkerResponse.failed(
            "NORMALIZATION_SOURCE_INVALID",
            "normalization source identity verification failed",
        )
    if (
        source.size != request.parameters["input_bytes"]
        or source.sha256 != request.parameters["source_sha256"]
    ):
        return WorkerResponse.failed(
            "NORMALIZATION_SOURCE_INVALID",
            "normalization source identity verification failed",
        )
    try:
        preflight, _preflight_bytes = _validated_extraction_preflight(
            request,
            limits,
        )
    except (OSError, ProtocolError, UnsafeInputError, ValueError):
        return WorkerResponse.failed(
            "PREFLIGHT_IDENTITY_INVALID",
            "preflight identity verification failed",
        )
    try:
        from academic_pdf_en_zh_reader.normalization.core import (
            NormalizationError,
            normalize_pdf_bytes,
        )
    except (ImportError, OSError):
        return WorkerResponse.failed(
            "NORMALIZATION_RUNTIME_UNAVAILABLE",
            "normalization runtime could not be loaded",
        )
    try:
        result = normalize_pdf_bytes(
            source.data,
            preflight=preflight,
            preflight_sha256=str(request.parameters["preflight_sha256"]),
            max_output_bytes=limits.max_normalized_pdf_bytes,
        )
    except NormalizationError as error:
        return WorkerResponse.failed(
            _stable_normalization_error_code(error),
            "PDF normalization rejected the input",
        )
    except MemoryError:
        return WorkerResponse.failed(
            "WORKER_LIMIT_EXCEEDED",
            "PDF normalization exceeded the worker memory limit",
        )
    except Exception:
        return WorkerResponse.failed(
            "NORMALIZATION_FAILED",
            "PDF normalization failed",
        )

    pdf_bytes = getattr(result, "pdf_bytes", None)
    artifact = getattr(result, "artifact", None)
    pdf_sha256 = (
        hashlib.sha256(pdf_bytes).hexdigest() if isinstance(pdf_bytes, bytes) else None
    )
    expected_fields = {
        "schema_version",
        "artifact_kind",
        "policy_version",
        "source_sha256",
        "preflight_sha256",
        "normalized_pdf_sha256",
        "normalized_pdf_bytes",
        "pages",
    }
    preflight_pages = preflight.get("pages")
    normalized_pages = artifact.get("pages") if isinstance(artifact, Mapping) else None
    if (
        not isinstance(pdf_bytes, bytes)
        or not isinstance(artifact, Mapping)
        or set(artifact) != expected_fields
        or artifact.get("schema_version") != "1.0.0"
        or artifact.get("artifact_kind") != "normalization"
        or artifact.get("policy_version") != request.parameters["policy_version"]
        or artifact.get("source_sha256") != source.sha256
        or artifact.get("preflight_sha256") != request.parameters["preflight_sha256"]
        or artifact.get("normalized_pdf_sha256") != pdf_sha256
        or artifact.get("normalized_pdf_bytes") != len(pdf_bytes)
        or not isinstance(preflight_pages, list)
        or not isinstance(normalized_pages, list)
        or len(normalized_pages) != len(preflight_pages)
    ):
        return WorkerResponse.failed(
            "NORMALIZATION_ARTIFACT_INVALID",
            "normalization core returned invalid bound artifacts",
        )
    try:
        descriptors = _write_normalization_artifacts(pdf_bytes, artifact, limits)
    except MemoryError:
        return WorkerResponse.failed(
            "WORKER_LIMIT_EXCEEDED",
            "PDF normalization exceeded the worker memory limit",
        )
    except (OSError, ProtocolError, ValueError):
        return WorkerResponse.failed(
            "NORMALIZATION_ARTIFACT_WRITE_FAILED",
            "normalization artifacts could not be written",
        )
    return WorkerResponse.ok(descriptors)


def _dispatch_request(
    request: WorkerRequest,
    limits: WorkerLimits,
) -> WorkerResponse:
    if request.operation == "probe":
        return WorkerResponse.ok(_probe_case(request))
    if request.operation == "preflight":
        return _run_preflight_request(request, limits)
    if request.operation == "extract":
        return _run_extraction_request(request, limits)
    if request.operation == "normalize":
        return _run_normalization_request(request, limits)
    return WorkerResponse.failed("PROTOCOL_ERROR", "unsupported operation")


def _validate_preflight_descriptor(
    descriptor: Mapping[str, object],
    limits: WorkerLimits,
) -> tuple[str, int]:
    if set(descriptor) != {
        "artifact_path",
        "artifact_sha256",
        "artifact_bytes",
    }:
        raise ProtocolError("preflight descriptor fields do not match the schema")
    if descriptor["artifact_path"] != PREFLIGHT_ARTIFACT_PATH:
        raise ProtocolError("preflight descriptor path is not fixed")
    sha256 = descriptor["artifact_sha256"]
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ProtocolError("preflight descriptor hash is invalid")
    size = descriptor["artifact_bytes"]
    if type(size) is not int or size <= 0 or size > limits.max_result_object_bytes:
        raise ProtocolError("preflight descriptor size is invalid")
    return sha256, size


def _read_preflight_artifact(
    workspace: Path,
    descriptor: Mapping[str, object],
    limits: WorkerLimits,
) -> bytes:
    try:
        expected_hash, expected_size = _validate_preflight_descriptor(
            descriptor,
            limits,
        )
        path = resolve_controlled_path(
            workspace,
            PREFLIGHT_ARTIFACT_PATH,
            must_exist=True,
        )
        artifact = read_bounded_regular_file(
            path,
            max_bytes=limits.max_result_object_bytes,
        )
    except (OSError, ProtocolError, UnsafeInputError, ValueError) as error:
        raise WorkerExecutionError(
            "preflight artifact could not be read safely"
        ) from error
    if artifact.size != expected_size or artifact.sha256 != expected_hash:
        raise WorkerExecutionError("preflight artifact integrity check failed")
    return artifact.data


def _validate_extraction_descriptor(
    descriptor: Mapping[str, object],
    limits: WorkerLimits,
) -> tuple[str, int]:
    if set(descriptor) != {
        "artifact_path",
        "artifact_sha256",
        "artifact_bytes",
    }:
        raise ProtocolError("extraction descriptor fields do not match the schema")
    if descriptor["artifact_path"] != EXTRACTION_ARTIFACT_PATH:
        raise ProtocolError("extraction descriptor path is not fixed")
    sha256 = descriptor["artifact_sha256"]
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ProtocolError("extraction descriptor hash is invalid")
    size = descriptor["artifact_bytes"]
    if (
        type(size) is not int
        or size <= 0
        or size > limits.max_extraction_artifact_bytes
    ):
        raise ProtocolError("extraction descriptor size is invalid")
    return sha256, size


def _read_extraction_artifact(
    workspace: Path,
    descriptor: Mapping[str, object],
    limits: WorkerLimits,
) -> bytes:
    try:
        expected_hash, expected_size = _validate_extraction_descriptor(
            descriptor,
            limits,
        )
        path = resolve_controlled_path(
            workspace,
            EXTRACTION_ARTIFACT_PATH,
            must_exist=True,
        )
        artifact = read_bounded_regular_file(
            path,
            max_bytes=limits.max_extraction_artifact_bytes,
        )
    except (OSError, ProtocolError, UnsafeInputError, ValueError) as error:
        raise WorkerExecutionError(
            "extraction artifact could not be read safely"
        ) from error
    if artifact.size != expected_size or artifact.sha256 != expected_hash:
        raise WorkerExecutionError("extraction artifact integrity check failed")
    return artifact.data


def _validate_normalization_descriptor(
    descriptor: object,
    *,
    expected_path: str,
    max_bytes: int,
    label: str,
) -> tuple[str, int]:
    if not isinstance(descriptor, Mapping) or set(descriptor) != {
        "artifact_path",
        "artifact_sha256",
        "artifact_bytes",
    }:
        raise ProtocolError(f"{label} descriptor fields do not match the schema")
    if descriptor["artifact_path"] != expected_path:
        raise ProtocolError(f"{label} descriptor path is not fixed")
    sha256 = descriptor["artifact_sha256"]
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ProtocolError(f"{label} descriptor hash is invalid")
    size = descriptor["artifact_bytes"]
    if type(size) is not int or size <= 0 or size > max_bytes:
        raise ProtocolError(f"{label} size is invalid")
    return sha256, size


def _validate_normalization_descriptors(
    descriptors: Mapping[str, object],
    limits: WorkerLimits,
) -> tuple[tuple[str, int], tuple[str, int]]:
    if set(descriptors) != {"normalized_pdf", "normalization_artifact"}:
        raise ProtocolError("normalization descriptor set does not match the schema")
    pdf = _validate_normalization_descriptor(
        descriptors["normalized_pdf"],
        expected_path=NORMALIZATION_PDF_PATH,
        max_bytes=limits.max_normalized_pdf_bytes,
        label="normalized PDF",
    )
    artifact = _validate_normalization_descriptor(
        descriptors["normalization_artifact"],
        expected_path=NORMALIZATION_ARTIFACT_PATH,
        max_bytes=limits.max_result_object_bytes,
        label="normalization artifact",
    )
    return pdf, artifact


def _read_normalization_artifacts(
    workspace: Path,
    descriptors: Mapping[str, object],
    limits: WorkerLimits,
) -> tuple[bytes, bytes]:
    try:
        (pdf_hash, pdf_size), (artifact_hash, artifact_size) = (
            _validate_normalization_descriptors(descriptors, limits)
        )
        pdf = read_bounded_regular_file(
            resolve_controlled_path(
                workspace,
                NORMALIZATION_PDF_PATH,
                must_exist=True,
            ),
            max_bytes=limits.max_normalized_pdf_bytes,
        )
        artifact = read_bounded_regular_file(
            resolve_controlled_path(
                workspace,
                NORMALIZATION_ARTIFACT_PATH,
                must_exist=True,
            ),
            max_bytes=limits.max_result_object_bytes,
        )
    except (OSError, ProtocolError, UnsafeInputError, ValueError) as error:
        raise WorkerExecutionError(
            "normalization artifacts could not be read safely"
        ) from error
    if (
        pdf.size != pdf_size
        or pdf.sha256 != pdf_hash
        or artifact.size != artifact_size
        or artifact.sha256 != artifact_hash
    ):
        raise WorkerExecutionError("normalization artifact integrity check failed")
    return pdf.data, artifact.data


def child_main(limits: WorkerLimits = DEFAULT_LIMITS) -> int:
    """Restricted child entry point; it accepts exactly one bounded request."""

    raw = sys.stdin.buffer.read(limits.max_protocol_bytes + 1)
    try:
        request = decode_request(raw, limits)
        response = _dispatch_request(request, limits)
    except ProtocolError:
        response = WorkerResponse.failed("PROTOCOL_ERROR", "request rejected")
    except (ValueError, OSError):
        response = WorkerResponse.failed(
            "WORKER_EXECUTION_FAILED",
            "isolated operation failed",
        )
    sys.stdout.buffer.write(encode_response(response, limits))
    sys.stdout.buffer.flush()
    return 0


def run_security_probe() -> dict[str, object]:
    """Exercise every minimum contract and report failures as evidence."""

    minimum = {
        "private_input_copy": False,
        "sha256_recomputed": False,
        "copy_size_verified": False,
        "restricted_token": False,
        "appcontainer_zero_capabilities": False,
        "appcontainer_cleanup": False,
        "external_file_isolation": False,
        "loopback_isolation": False,
        "parent_process_isolation": False,
        "handle_allowlist": False,
        "kill_on_job_close": False,
        "process_count_limit": False,
        "cpu_limit_control": False,
        "cpu_time_limit": False,
        "memory_limit_control": False,
        "memory_limit": False,
        "wall_clock_limit": False,
        "bounded_versioned_json": False,
        "controlled_relative_paths": False,
        "parent_handle_cleanup": False,
    }
    report: dict[str, object] = {
        "schema_version": 1,
        "platform": sys.platform,
        "passed": False,
        "minimum_contract": minimum,
        "provenance": {
            "appcontainer_api_available": _appcontainer_apis_available(),
            "appcontainer_implemented": False,
            "appcontainer_launch_verified": False,
            "network_isolation_implemented": False,
            "subprocess_fallback": False,
        },
        "errors": [],
        "handle_trace": [],
    }
    errors: list[str] = report["errors"]  # type: ignore[assignment]
    handle_trace: list[dict[str, object]] = report["handle_trace"]  # type: ignore[assignment]
    appcontainer_cleanup_checks: list[bool] = []

    def record_handles(phase: str) -> None:
        handle_trace.append({"phase": phase, "count": _current_process_handle_count()})

    def run_checked_worker(
        request: WorkerRequest,
        workspace_root: Path,
        *,
        limits: WorkerLimits = DEFAULT_LIMITS,
    ) -> WorkerRunResult:
        result = run_worker(request, workspace_root, limits=limits)
        cleanup_verified = (
            result.provenance.get("appcontainer_cleanup_verified") is True
        )
        appcontainer_cleanup_checks.append(cleanup_verified)
        if not cleanup_verified:
            case = request.parameters.get("case", "unknown")
            errors.append(f"AppContainer cleanup was not verified after case={case}")
        return result

    def expected_limit_has_clean_teardown(
        error: WorkerExecutionError,
        check_name: str,
    ) -> bool:
        notes = [str(note) for note in getattr(error, "__notes__", ())]
        clean = not notes
        appcontainer_cleanup_checks.append(clean)
        if notes:
            errors.append(f"{check_name} teardown failed: {'; '.join(notes)}")
        return clean

    if os.name != "nt":
        errors.append(
            "Windows APIs are unavailable; no equivalent adapter is implemented"
        )
        return report

    initialization_handle_count: int | None = None
    handle_count_before: int | None = None
    handle_count_after: int | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="worker-probe-source-") as source_root:
            source_root_path = Path(source_root).resolve(strict=True)
            source = source_root_path / "paper.pdf"
            sandbox_parent = source_root_path / "sandboxes"
            probe_bytes = b"%PDF-1.7\n%%EOF\n"
            source.write_bytes(probe_bytes)
            with copy_untrusted_input(source, temp_parent=sandbox_parent) as copied:
                minimum["private_input_copy"] = private_directory_is_current_user_only(
                    copied.root
                )
                minimum["sha256_recomputed"] = (
                    copied.sha256 == hashlib.sha256(probe_bytes).hexdigest()
                )
                minimum["copy_size_verified"] = copied.size == len(probe_bytes)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as warm_listener:
                    warm_listener.bind(("127.0.0.1", 0))
                    warm_listener.listen(1)
                initialization_handle_count = _current_process_handle_count()
                record_handles("after_parent_runtime_warmup")

                inspect = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={"case": "inspect"},
                    ),
                    copied.root,
                )
                inspect_result = inspect.response.result or {}
                is_appcontainer = bool(inspect_result.get("appcontainer"))
                zero_capabilities = inspect_result.get("capability_count") == 0
                minimum["restricted_token"] = bool(
                    (inspect_result.get("restricted_token") or is_appcontainer)
                    and set(inspect_result.get("enabled_privileges", []))
                    <= _ALLOWED_ENABLED_PRIVILEGES
                )
                minimum["appcontainer_zero_capabilities"] = bool(
                    is_appcontainer and zero_capabilities
                )
                provenance: dict[str, object] = report["provenance"]  # type: ignore[assignment]
                provenance.update(inspect.provenance)
                provenance["appcontainer_launch_verified"] = is_appcontainer
                provenance["child_token_evidence"] = inspect_result
                minimum["appcontainer_cleanup"] = bool(
                    inspect.provenance.get("appcontainer_cleanup_verified")
                )
                handle_count_before = _current_process_handle_count()
                record_handles("baseline_after_inspect")
                provenance["one_time_initialization_handle_delta"] = (
                    handle_count_before - initialization_handle_count
                )

                outside = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={
                            "case": "outside_access",
                            "path": str(copied.path),
                        },
                    ),
                    copied.root,
                )
                outside_result = outside.response.result or {}
                minimum["external_file_isolation"] = bool(
                    is_appcontainer and outside_result.get("outside_access_denied")
                )
                provenance["external_file_evidence"] = outside_result
                record_handles("after_external_file")

                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    loopback = run_checked_worker(
                        WorkerRequest(
                            operation="probe",
                            input_path="input.pdf",
                            parameters={
                                "case": "network_connect",
                                "port": listener.getsockname()[1],
                            },
                        ),
                        copied.root,
                    )
                finally:
                    listener.close()
                loopback_result = loopback.response.result or {}
                minimum["loopback_isolation"] = bool(
                    is_appcontainer and loopback_result.get("network_denied")
                )
                provenance["loopback_evidence"] = loopback_result
                record_handles("after_loopback")

                parent_access = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={
                            "case": "parent_process_access",
                            "parent_id": os.getpid(),
                        },
                    ),
                    copied.root,
                )
                parent_access_result = parent_access.response.result or {}
                access_results = parent_access_result.get("access", {})
                minimum["parent_process_isolation"] = bool(
                    is_appcontainer
                    and isinstance(access_results, dict)
                    and access_results
                    and all(
                        isinstance(result, dict) and result.get("denied")
                        for result in access_results.values()
                    )
                )
                provenance["parent_process_access_evidence"] = parent_access_result
                record_handles("after_parent_process_access")

                excluded_event = _make_inheritable_probe_event()
                try:
                    excluded_handle = run_checked_worker(
                        WorkerRequest(
                            operation="probe",
                            input_path="input.pdf",
                            parameters={
                                "case": "excluded_inheritable_handle",
                                "handle": _handle_value(excluded_event),
                            },
                        ),
                        copied.root,
                    )
                finally:
                    _close_handle(excluded_event)
                excluded_result = excluded_handle.response.result or {}
                minimum["handle_allowlist"] = bool(excluded_result.get("excluded"))
                provenance["handle_allowlist_evidence"] = excluded_result
                record_handles("after_handle_allowlist")

                child_limit = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={"case": "spawn_child"},
                    ),
                    copied.root,
                )
                process_messages = child_limit.provenance.get("job_messages", [])
                minimum["process_count_limit"] = bool(
                    child_limit.response.result
                    and child_limit.response.result.get("spawn_denied")
                    and any(
                        isinstance(message, dict)
                        and message.get("name") == "ACTIVE_PROCESS_LIMIT"
                        for message in process_messages
                    )
                )
                provenance["process_limit_job_messages"] = process_messages
                record_handles("after_process_count_limit")

                kill_probe = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={"case": "spawn_for_kill_probe"},
                    ),
                    copied.root,
                    limits=replace(DEFAULT_LIMITS, active_process_limit=2),
                )
                minimum["kill_on_job_close"] = bool(
                    kill_probe.provenance.get("descendant_killed_on_job_close")
                )
                provenance["kill_on_close_evidence"] = kill_probe.provenance.get(
                    "descendant_kill_evidence"
                )
                record_handles("after_kill_on_close")

                try:
                    run_checked_worker(
                        WorkerRequest(
                            operation="probe",
                            input_path="input.pdf",
                            parameters={"case": "sleep", "seconds": 5},
                        ),
                        copied.root,
                        limits=replace(
                            DEFAULT_LIMITS,
                            wall_time_seconds=0.2,
                            cpu_time_seconds=5.0,
                        ),
                    )
                except WorkerTimeoutError as error:
                    minimum["wall_clock_limit"] = expected_limit_has_clean_teardown(
                        error, "wall_clock_limit"
                    )
                record_handles("after_wall_clock_limit")

                cpu_control = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={"case": "busy_for", "seconds": 0.05},
                    ),
                    copied.root,
                    limits=replace(
                        DEFAULT_LIMITS,
                        cpu_time_seconds=1.0,
                        wall_time_seconds=2.0,
                    ),
                )
                minimum["cpu_limit_control"] = bool(
                    cpu_control.response.result
                    and cpu_control.response.result.get("cpu_seconds") == 0.05
                )
                record_handles("after_cpu_control")
                try:
                    run_checked_worker(
                        WorkerRequest(
                            operation="probe",
                            input_path="input.pdf",
                            parameters={"case": "busy_loop"},
                        ),
                        copied.root,
                        limits=replace(
                            DEFAULT_LIMITS,
                            cpu_time_seconds=0.2,
                            wall_time_seconds=10.0,
                        ),
                    )
                except WorkerCpuLimitError as error:
                    minimum["cpu_time_limit"] = expected_limit_has_clean_teardown(
                        error, "cpu_time_limit"
                    )
                    provenance["cpu_limit_job_messages"] = error.job_messages
                record_handles("after_cpu_limit")

                memory_control = run_checked_worker(
                    WorkerRequest(
                        operation="probe",
                        input_path="input.pdf",
                        parameters={
                            "case": "allocate",
                            "bytes": 1024 * 1024,
                            "seconds": 0,
                        },
                    ),
                    copied.root,
                    limits=replace(
                        DEFAULT_LIMITS,
                        process_memory_bytes=160 * 1024 * 1024,
                        job_memory_bytes=160 * 1024 * 1024,
                        wall_time_seconds=2.0,
                    ),
                )
                minimum["memory_limit_control"] = bool(
                    memory_control.response.result
                    and memory_control.response.result.get("allocated") == 1024 * 1024
                )
                record_handles("after_memory_control")
                try:
                    run_checked_worker(
                        WorkerRequest(
                            operation="probe",
                            input_path="input.pdf",
                            parameters={"case": "allocate", "bytes": 384 * 1024 * 1024},
                        ),
                        copied.root,
                        limits=replace(
                            DEFAULT_LIMITS,
                            process_memory_bytes=160 * 1024 * 1024,
                            job_memory_bytes=160 * 1024 * 1024,
                            wall_time_seconds=3.0,
                        ),
                    )
                except WorkerMemoryLimitError as error:
                    minimum["memory_limit"] = expected_limit_has_clean_teardown(
                        error, "memory_limit"
                    )
                    provenance["memory_limit_job_messages"] = error.job_messages
                record_handles("after_memory_limit")

                try:
                    run_checked_worker(
                        WorkerRequest(
                            operation="probe",
                            input_path="input.pdf",
                            parameters={"case": "oversize_output"},
                        ),
                        copied.root,
                    )
                except WorkerOutputLimitError as error:
                    minimum["bounded_versioned_json"] = (
                        expected_limit_has_clean_teardown(
                            error, "bounded_versioned_json"
                        )
                    )
                record_handles("after_bounded_output")

                try:
                    resolve_controlled_path(
                        copied.root, "../outside.pdf", must_exist=False
                    )
                except ProtocolError:
                    minimum["controlled_relative_paths"] = True
                record_handles("after_controlled_path")
    except Exception as error:  # probe must return evidence instead of masking failure
        errors.append(f"{type(error).__name__}: {error}")
    finally:
        if handle_count_before is not None:
            try:
                handle_count_after = _current_process_handle_count()
            except Exception as error:
                errors.append(f"handle cleanup measurement failed: {error}")

    minimum["parent_handle_cleanup"] = bool(
        handle_count_before is not None
        and handle_count_after is not None
        and handle_count_after <= handle_count_before + 2
    )
    report["handle_count"] = {
        "before_initialization": initialization_handle_count,
        "before": handle_count_before,
        "after": handle_count_after,
    }
    minimum["appcontainer_cleanup"] = bool(appcontainer_cleanup_checks) and all(
        appcontainer_cleanup_checks
    )

    failed_checks = [name for name, passed in minimum.items() if not passed]
    if failed_checks:
        errors.append("minimum contract checks failed: " + ", ".join(failed_checks))
    report["passed"] = all(minimum.values()) and not errors
    return report
