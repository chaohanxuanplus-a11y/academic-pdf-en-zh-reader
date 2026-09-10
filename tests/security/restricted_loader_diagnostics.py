# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Read-only, bounded diagnostics for the non-AppContainer test adapter only."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import sys
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path

from academic_pdf_en_zh_reader.security import windows_worker


class _GenericMapping(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in ("read", "write", "execute", "all")]


def _file_access(path: Path, token: object, api: object) -> dict[str, object]:
    descriptor = wintypes.LPVOID()
    error = api.GetNamedSecurityInfoW(
        str(path), 1, 7, None, None, None, None, ctypes.byref(descriptor)
    )
    if error:
        return {"security_descriptor_error": int(error)}
    try:
        result: dict[str, object] = {}
        mapping = _GenericMapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
        for name, mask in (("read", 0x120089), ("execute", 0x1200A0)):
            privileges = ctypes.create_string_buffer(4096)
            length = wintypes.DWORD(ctypes.sizeof(privileges))
            granted = wintypes.DWORD()
            allowed = wintypes.BOOL()
            succeeded = api.AccessCheck(
                descriptor,
                token,
                mask,
                ctypes.byref(mapping),
                privileges,
                ctypes.byref(length),
                ctypes.byref(granted),
                ctypes.byref(allowed),
            )
            result[name] = (
                bool(allowed.value)
                if succeeded
                else {"api_error": ctypes.get_last_error()}
            )
        return result
    finally:
        windows_worker._kernel32.LocalFree(descriptor)


def _file_snapshot(path: Path, access) -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        info = path.lstat()
    except OSError as error:
        return {"exists": False, "stat_error": getattr(error, "winerror", None)}
    result["exists"] = True
    result["reparse"] = bool(getattr(info, "st_file_attributes", 0) & 0x400)
    result["directory"] = stat.S_ISDIR(info.st_mode)
    if result["reparse"]:
        return result
    if stat.S_ISREG(info.st_mode):
        result["size"] = info.st_size
        if info.st_size <= 64 * 1024 * 1024:
            try:
                with path.open("rb") as stream:
                    result["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError as error:
                result["hash_error"] = getattr(error, "winerror", None)
    result["restricted_access"] = access(path)
    return result


def collect_restricted_loader_diagnostics() -> dict[str, object]:
    if os.name != "nt":
        return {"unsupported_platform": sys.platform}
    api = windows_worker._advapi32
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
    base = Path(windows_worker._python_executable()).parent
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    targets = {
        "base": base,
        "base-parent": base.parent,
        "base-grandparent": base.parent.parent,
    }
    for name in (
        "pythonw.exe",
        "python.exe",
        f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ):
        targets[f"base/{name}"] = base / name
    # Physical imports and the CRT/API-set hosts; no claim to trace SxS binding.
    for name in (
        "KERNEL32.dll",
        "KernelBase.dll",
        "ucrtbase.dll",
        "VERSION.dll",
        "WS2_32.dll",
        "bcrypt.dll",
        "ADVAPI32.dll",
    ):
        targets[f"System32/{name}"] = system / name
    with ExitStack() as cleanup:
        restricted, privileges, origin = windows_worker._create_restricted_token()
        cleanup.callback(windows_worker._close_handle, restricted)
        impersonation = wintypes.HANDLE()
        if not api.DuplicateTokenEx(
            restricted, 0x0008, None, 2, 2, ctypes.byref(impersonation)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        cleanup.callback(windows_worker._close_handle, impersonation)
        return {
            "scope": "test-adapter-only; not a complete loader/SxS trace",
            "base_directory": str(base),
            "token_origin": origin,
            "enabled_privileges": privileges,
            "files": {
                label: _file_snapshot(
                    path, lambda item: _file_access(item, impersonation, api)
                )
                for label, path in targets.items()
            },
        }


def emit_restricted_loader_diagnostics() -> None:
    try:
        result = collect_restricted_loader_diagnostics()
    except Exception as error:
        result = {"diagnostic_error_type": type(error).__name__}
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True)
    if len(encoded) > 16_384:
        encoded = '{"diagnostic_error_type":"OutputLimitExceeded"}'
    print("RESTRICTED_LOADER_DIAGNOSTIC " + encoded, flush=True)
