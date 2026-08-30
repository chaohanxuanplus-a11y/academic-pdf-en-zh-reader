# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Copy an untrusted file through a non-reparse handle into a private root."""

from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
import shutil
import stat
import tempfile
from contextlib import closing, suppress
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from .limits import DEFAULT_LIMITS, WorkerLimits


class UnsafeInputError(ValueError):
    """The input is not an ordinary bounded file safe to copy."""


@dataclass(frozen=True, slots=True)
class SafeInputCopy:
    root: Path
    path: Path
    sha256: str
    size: int

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def __enter__(self) -> SafeInputCopy:
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()


@dataclass(frozen=True, slots=True)
class BoundedRegularFile:
    data: bytes
    sha256: str
    size: int


if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE),
            ("Sbz1", wintypes.BYTE),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    ]
    _kernel32.CreateDirectoryW.restype = wintypes.BOOL
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    _kernel32.GetFileType.restype = wintypes.DWORD
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [
        ctypes.POINTER(_ACL),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    _advapi32.GetAce.restype = wintypes.BOOL


def _win_error(action: str) -> OSError:
    error = ctypes.get_last_error()
    return OSError(error, f"{action}: {ctypes.FormatError(error).strip()}")


def _sid_to_string(sid: object) -> str:
    output = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
        raise _win_error("ConvertSidToStringSidW failed")
    try:
        return output.value
    finally:
        _kernel32.LocalFree(output)


def _current_user_sid() -> str:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise _win_error("OpenProcessToken failed")
    try:
        size = wintypes.DWORD()
        _advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise _win_error("GetTokenInformation sizing failed")
        buffer = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
            token, 1, buffer, size, ctypes.byref(size)
        ):
            raise _win_error("GetTokenInformation failed")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents
        return _sid_to_string(token_user.User.Sid)
    finally:
        _kernel32.CloseHandle(token)


def _path_is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def _create_private_directory(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    parent = parent.resolve(strict=True)
    if not parent.is_dir() or _path_is_reparse(parent):
        raise UnsafeInputError("temporary parent must be a real directory")
    if os.name != "nt":
        root = Path(tempfile.mkdtemp(prefix="academic-pdf-worker-", dir=parent))
        try:
            root.chmod(0o700)
            if not private_directory_is_current_user_only(root):
                raise UnsafeInputError("could not create a private temporary directory")
            return root
        except BaseException:
            with suppress(OSError):
                shutil.rmtree(root)
            raise

    descriptor = wintypes.LPVOID()
    sid = _current_user_sid()
    sddl = f"O:{sid}D:P(A;OICI;FA;;;{sid})"
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise UnsafeInputError(str(_win_error("private DACL creation failed")))
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    try:
        for _ in range(20):
            root = parent / f"academic-pdf-worker-{secrets.token_hex(16)}"
            if _kernel32.CreateDirectoryW(str(root), ctypes.byref(attributes)):
                try:
                    if not private_directory_is_current_user_only(root):
                        raise UnsafeInputError(
                            "private temporary directory DACL verification failed"
                        )
                    return root
                except BaseException:
                    with suppress(OSError):
                        shutil.rmtree(root)
                    raise
            if ctypes.get_last_error() != 183:
                raise UnsafeInputError(str(_win_error("CreateDirectoryW failed")))
    finally:
        _kernel32.LocalFree(descriptor)
    raise UnsafeInputError("could not allocate a unique temporary directory")


def private_directory_is_current_user_only(path: Path) -> bool:
    """Verify mode 0700 on POSIX or a protected owner-only DACL on Windows."""

    try:
        if not path.is_dir() or _path_is_reparse(path):
            return False
        if os.name != "nt":
            info = path.stat()
            return info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700

        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        result = _advapi32.GetNamedSecurityInfoW(
            str(path),
            1,
            0x00000001 | 0x00000004,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            return False
        try:
            if not owner or not dacl:
                return False
            current_sid = _current_user_sid()
            if _sid_to_string(owner) != current_sid:
                return False
            control = wintypes.WORD()
            revision = wintypes.DWORD()
            if not _advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                return False
            if not control.value & 0x1000:
                return False
            acl = ctypes.cast(dacl, ctypes.POINTER(_ACL)).contents
            if acl.AceCount != 1:
                return False
            ace_pointer = wintypes.LPVOID()
            if not _advapi32.GetAce(
                ctypes.cast(dacl, ctypes.POINTER(_ACL)), 0, ctypes.byref(ace_pointer)
            ):
                return False
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
            if ace.Header.AceType != 0 or ace.Header.AceFlags & 0x10:
                return False
            sid_address = int(ace_pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
            return _sid_to_string(wintypes.LPVOID(sid_address)) == current_sid
        finally:
            _kernel32.LocalFree(descriptor)
    except (OSError, ValueError):
        return False


def _reject_reserved_or_reparse_path(source: Path) -> Path:
    text = os.fspath(source)
    windows = PureWindowsPath(text)
    lowered = text.replace("/", "\\").casefold()
    if windows.name and windows.is_reserved():
        raise UnsafeInputError("reserved device paths are not accepted")
    if lowered.startswith(("\\\\.\\", "\\\\?\\", "\\??\\", "\\device\\")):
        raise UnsafeInputError("Windows device namespaces are not accepted")
    absolute = Path(os.path.abspath(text))
    try:
        info = absolute.lstat()
    except OSError as error:
        raise UnsafeInputError(f"input is unavailable: {error}") from error
    if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & 0x400):
        raise UnsafeInputError("input is a symlink or reparse point")
    if not stat.S_ISREG(info.st_mode):
        raise UnsafeInputError("input must be a regular file")
    if os.name == "nt":
        for parent in absolute.parents:
            if _path_is_reparse(parent):
                raise UnsafeInputError(
                    "input path contains a junction or reparse point"
                )
    return absolute


def _final_path_from_handle(handle: object) -> Path:
    required = _kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not required:
        raise UnsafeInputError(str(_win_error("final input path query failed")))
    buffer = ctypes.create_unicode_buffer(required + 1)
    length = _kernel32.GetFinalPathNameByHandleW(
        handle,
        buffer,
        len(buffer),
        0,
    )
    if not length:
        raise UnsafeInputError(str(_win_error("final input path query failed")))
    if length >= len(buffer):
        raise UnsafeInputError("final input path changed while it was queried")

    final_name = buffer.value
    if final_name.startswith("\\\\?\\"):
        final_name = final_name[4:]
    windows = PureWindowsPath(final_name)
    drive = windows.drive
    if (
        len(drive) != 2
        or drive[1] != ":"
        or not drive[0].isalpha()
        or not windows.is_absolute()
    ):
        raise UnsafeInputError("final input path is a device namespace")
    return Path(final_name)


def _open_source(source: Path, limits: WorkerLimits) -> tuple[BinaryIO, int]:
    source = _reject_reserved_or_reparse_path(source)
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(source, flags)
        except OSError as error:
            raise UnsafeInputError(
                f"input could not be opened safely: {error}"
            ) from error
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise UnsafeInputError("opened input is not a regular file")
        if info.st_size > limits.max_input_bytes:
            os.close(descriptor)
            raise UnsafeInputError("input exceeds the configured size limit")
        return os.fdopen(descriptor, "rb", closefd=True), info.st_size

    handle = _kernel32.CreateFileW(
        str(source),
        0x80000000,
        0x00000001,
        None,
        3,
        0x00200000 | 0x08000000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise UnsafeInputError(str(_win_error("input handle creation failed")))
    try:
        final_path = _final_path_from_handle(handle)
        expected = os.path.normcase(os.path.normpath(os.fspath(source)))
        opened = os.path.normcase(os.path.normpath(os.fspath(final_path)))
        if opened != expected:
            raise UnsafeInputError(
                "opened input final path differs from the requested path"
            )
        information = _BY_HANDLE_FILE_INFORMATION()
        if _kernel32.GetFileType(handle) != 1:
            raise UnsafeInputError("input handle is a device or pipe, not a disk file")
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise UnsafeInputError(str(_win_error("input handle inspection failed")))
        if information.dwFileAttributes & (0x10 | 0x400):
            raise UnsafeInputError("opened input is a directory or reparse point")
        size = (information.nFileSizeHigh << 32) | information.nFileSizeLow
        if size > limits.max_input_bytes:
            raise UnsafeInputError("input exceeds the configured size limit")
        import msvcrt

        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        handle = None
        try:
            return os.fdopen(descriptor, "rb", closefd=True), size
        except Exception:
            os.close(descriptor)
            raise
    finally:
        if handle not in (None, invalid_handle):
            _kernel32.CloseHandle(handle)


def _copy_and_hash(
    source: BinaryIO,
    destination: BinaryIO,
    max_bytes: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UnsafeInputError("input exceeds the configured size limit")
        digest.update(chunk)
        destination.write(chunk)
    destination.flush()
    os.fsync(destination.fileno())
    return digest.hexdigest(), total


def read_bounded_regular_file(
    source: Path,
    *,
    max_bytes: int,
) -> BoundedRegularFile:
    """Read one ordinary non-reparse file from its verified open handle."""

    if isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")
    read_limits = replace(DEFAULT_LIMITS, max_input_bytes=max_bytes)
    source_stream, reported_size = _open_source(Path(source), read_limits)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    with closing(source_stream):
        while True:
            chunk = source_stream.read(min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UnsafeInputError("file exceeds the configured size limit")
            digest.update(chunk)
            chunks.append(chunk)
    if total != reported_size:
        raise UnsafeInputError("file size changed while it was being read")
    return BoundedRegularFile(
        data=b"".join(chunks),
        sha256=digest.hexdigest(),
        size=total,
    )


def copy_untrusted_input(
    source: Path,
    *,
    temp_parent: Path | None = None,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> SafeInputCopy:
    """Copy from a validated open handle, never by reopening the source path."""

    root: Path | None = None
    source_stream, reported_size = _open_source(Path(source), limits)
    try:
        parent = temp_parent or Path(tempfile.gettempdir())
        root = _create_private_directory(Path(parent))
        destination = root / "input.pdf"
        with closing(source_stream), destination.open("xb") as output:
            sha256, copied_size = _copy_and_hash(
                source_stream, output, limits.max_input_bytes
            )
        if copied_size != reported_size:
            raise UnsafeInputError("input size changed while it was being copied")
        return SafeInputCopy(
            root=root,
            path=destination,
            sha256=sha256,
            size=copied_size,
        )
    except BaseException:
        with suppress(OSError):
            source_stream.close()
        if root is not None and root.exists():
            with suppress(OSError):
                shutil.rmtree(root)
        raise
