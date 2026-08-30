# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed retention and deletion for private, app-managed job directories."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Literal

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

MAX_RETENTION_SECONDS = 24 * 60 * 60
SENTINEL_NAME = ".academic-pdf-en-zh-reader-job.json"
DELIVERY_INTENT_NAME = ".delivery-intent.json"
_SENTINEL_VERSION = 1
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_OUTCOMES = frozenset({"success", "failure", "cancel"})
_RETENTION_MODES = frozenset({"resume", "debug"})
_FILE_READ_ATTRIBUTES = 0x80
_FILE_LIST_DIRECTORY = 0x1
_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_DISPOSITION_INFO_CLASS = 4
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True, slots=True)
class CleanupResult:
    code: str
    cleaned: bool
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class SweepResult:
    code: str
    cleaned_jobs: int
    skipped_jobs: int
    failed_jobs: int


class _UnsafeCleanupPath(ValueError):
    pass


class _UnsafeFileIdentity(OSError):
    pass


class _WindowsFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTimeLow", ctypes.c_uint32),
        ("ftCreationTimeHigh", ctypes.c_uint32),
        ("ftLastAccessTimeLow", ctypes.c_uint32),
        ("ftLastAccessTimeHigh", ctypes.c_uint32),
        ("ftLastWriteTimeLow", ctypes.c_uint32),
        ("ftLastWriteTimeHigh", ctypes.c_uint32),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class _WindowsFileDisposition(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


def _now_utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("time must be timezone-aware")
    return observed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("invalid managed-job time")
    return datetime.fromisoformat(f"{value[:-1]}+00:00").astimezone(UTC)


def _contains_unresolved_parts(path: Path) -> bool:
    return any(part in {".", "..", "~"} for part in path.parts)


def _is_reparse_or_symlink(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _windows_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.GetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WindowsFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
    kernel32.ReadFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = ctypes.c_int
    kernel32.SetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetFileInformationByHandle.restype = ctypes.c_int
    return kernel32


@contextmanager
def _windows_locked_handle(path: Path, *, access: int, flags: int):
    kernel32 = _windows_kernel32()
    handle = kernel32.CreateFileW(
        str(path),
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(error, "locked path no longer exists", str(path))
        raise OSError(error, "locked path could not be opened", str(path))
    try:
        yield int(handle)
    finally:
        if not kernel32.CloseHandle(ctypes.c_void_p(handle)):
            raise OSError(ctypes.get_last_error(), "locked handle could not be closed")


def _windows_handle_information(handle: int) -> _WindowsFileInformation:
    kernel32 = _windows_kernel32()
    information = _WindowsFileInformation()
    if not kernel32.GetFileInformationByHandle(
        ctypes.c_void_p(handle), ctypes.byref(information)
    ):
        raise OSError(ctypes.get_last_error(), "locked identity is unavailable")
    return information


def _windows_handle_is_regular(information: _WindowsFileInformation) -> bool:
    return not information.dwFileAttributes & (
        _FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _windows_handle_chunks(handle: int):
    kernel32 = _windows_kernel32()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    while True:
        read = ctypes.c_uint32()
        if not kernel32.ReadFile(
            ctypes.c_void_p(handle),
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "locked file could not be read")
        if read.value == 0:
            return
        yield buffer.raw[: read.value]


def _hash_windows_handle(handle: int) -> str:
    digest = hashlib.sha256()
    for chunk in _windows_handle_chunks(handle):
        digest.update(chunk)
    return digest.hexdigest()


def _read_locked_regular_bytes(path: Path) -> bytes:
    if os.name != "nt":
        return path.read_bytes()
    with _windows_locked_handle(
        path,
        access=_GENERIC_READ,
        flags=_FILE_FLAG_OPEN_REPARSE_POINT,
    ) as handle:
        if not _windows_handle_is_regular(_windows_handle_information(handle)):
            raise _UnsafeCleanupPath
        return b"".join(_windows_handle_chunks(handle))


def _windows_mark_delete(handle: int) -> None:
    kernel32 = _windows_kernel32()
    disposition = _WindowsFileDisposition(1)
    if not kernel32.SetFileInformationByHandle(
        ctypes.c_void_p(handle),
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise OSError(ctypes.get_last_error(), "locked file could not be deleted")


def _hash_posix_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _delete_regular_file_if_hash_matches(
    path: Path, expected_sha256: str, *, missing_ok: bool
) -> bool:
    """Delete only the exact regular file kept locked while it is verified."""

    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise _UnsafeFileIdentity("expected file identity is invalid")
    if os.name == "nt":
        try:
            with _windows_locked_handle(
                path,
                access=_GENERIC_READ | _DELETE,
                flags=_FILE_FLAG_OPEN_REPARSE_POINT,
            ) as handle:
                information = _windows_handle_information(handle)
                if not _windows_handle_is_regular(information):
                    raise _UnsafeFileIdentity("locked path is not a regular file")
                if _hash_windows_handle(handle) != expected_sha256:
                    raise _UnsafeFileIdentity("locked file identity differs")
                _windows_mark_delete(handle)
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise

    parent = _canonical_existing_directory(path.parent)
    if parent / path.name != path:
        raise _UnsafeFileIdentity("file path is not canonical")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor = os.open(parent, directory_flags)
    try:
        try:
            descriptor = os.open(path.name, file_flags, dir_fd=directory_descriptor)
        except FileNotFoundError:
            if missing_ok:
                return False
            raise
        try:
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise _UnsafeFileIdentity("locked path is not a regular file")
            if _hash_posix_descriptor(descriptor) != expected_sha256:
                raise _UnsafeFileIdentity("locked file identity differs")
            current = os.stat(
                path.name, dir_fd=directory_descriptor, follow_symlinks=False
            )
            if (current.st_dev, current.st_ino) != (
                information.st_dev,
                information.st_ino,
            ):
                raise _UnsafeFileIdentity("locked file name was replaced")
            os.unlink(path.name, dir_fd=directory_descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)
    return True


def _canonical_existing_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise _UnsafeCleanupPath
    if _contains_unresolved_parts(path):
        raise _UnsafeCleanupPath
    try:
        if _is_reparse_or_symlink(path):
            raise _UnsafeCleanupPath
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise _UnsafeCleanupPath from error
    if os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise _UnsafeCleanupPath
    if not resolved.is_dir():
        raise _UnsafeCleanupPath
    return resolved


def _is_broad_or_repository_path(path: Path) -> bool:
    if path == Path(path.anchor):
        return True
    try:
        if path == Path.home().resolve(strict=True):
            return True
    except OSError:
        pass
    return (path / ".git").exists() or (
        (path / "pyproject.toml").is_file() and (path / "src").is_dir()
    )


def _decode_canonical_object(raw: bytes) -> dict[str, object]:
    value = json.loads(raw.decode("utf-8", errors="strict"))
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ValueError("managed JSON is invalid")
    return value


def _read_canonical_object(path: Path) -> dict[str, object]:
    return _decode_canonical_object(_read_locked_regular_bytes(path))


def _read_posix_regular_bytes(directory_descriptor: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _UnsafeCleanupPath
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _sentinel_value(
    *, job_id: str, created_at: datetime, expires_at: datetime, mode: str
) -> dict[str, object]:
    return {
        "created_at": _format_time(created_at),
        "expires_at": _format_time(expires_at),
        "job_id": job_id,
        "retention_mode": mode,
        "version": _SENTINEL_VERSION,
    }


def _write_replaced(path: Path, value: dict[str, object]) -> None:
    encoded = canonical_json_bytes(value)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _valid_job_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _JOB_ID.fullmatch(value) is not None
        and value == value.rstrip(" .")
        and PureWindowsPath(value).name == value
        and not PureWindowsPath(value).is_reserved()
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    information = path.lstat()
    if not stat.S_ISDIR(information.st_mode) or _is_reparse_or_symlink(path):
        raise OSError("managed job directory identity is invalid")
    return information.st_dev, information.st_ino


def _matches_directory_identity(path: Path, expected: tuple[int, int]) -> bool:
    try:
        return _directory_identity(path) == expected
    except OSError:
        return False


def _rollback_partial_job(
    job_root: Path,
    sentinel: Path,
    expected_identity: tuple[int, int],
) -> None:
    """Remove only this still-identical directory and its partial sentinel."""

    try:
        if not _matches_directory_identity(job_root, expected_identity):
            return
        children = list(job_root.iterdir())
        if any(child.name != SENTINEL_NAME for child in children):
            return
        if children:
            information = sentinel.lstat()
            if not stat.S_ISREG(information.st_mode) or _is_reparse_or_symlink(
                sentinel
            ):
                return
            if not _matches_directory_identity(job_root, expected_identity):
                return
            sentinel.unlink()
        if _matches_directory_identity(job_root, expected_identity):
            job_root.rmdir()
    except OSError:
        return


def create_managed_job(
    managed_root: str | Path,
    job_id: str,
    *,
    now: datetime | None = None,
) -> Path:
    """Create one direct managed child and its immutable ownership sentinel."""

    if not _valid_job_id(job_id):
        raise ValueError("job_id is invalid")
    root = _canonical_existing_directory(Path(managed_root))
    if _is_broad_or_repository_path(root):
        raise ValueError("managed root is too broad")
    observed = _now_utc(now)
    job_root = root / job_id
    sentinel = job_root / SENTINEL_NAME
    created_identity: tuple[int, int] | None = None
    try:
        job_root.mkdir(mode=0o700, exist_ok=False)
        created_identity = _directory_identity(job_root)
        encoded = canonical_json_bytes(
            _sentinel_value(
                job_id=job_id,
                created_at=observed,
                expires_at=observed + timedelta(seconds=MAX_RETENTION_SECONDS),
                mode="active",
            )
        )
        with sentinel.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if created_identity is not None:
            _rollback_partial_job(job_root, sentinel, created_identity)
        raise
    return job_root


def _validate_sentinel_value(
    sentinel: dict[str, object], job_name: str
) -> dict[str, object]:
    if (
        sentinel.get("version") != _SENTINEL_VERSION
        or sentinel.get("job_id") != job_name
        or sentinel.get("retention_mode") not in {"active", "resume", "debug"}
    ):
        raise _UnsafeCleanupPath
    _parse_time(sentinel.get("created_at"))
    _parse_time(sentinel.get("expires_at"))
    return sentinel


def _validate_scope(
    managed_root: Path, job_root: Path
) -> tuple[Path, Path, dict[str, object]]:
    root = _canonical_existing_directory(managed_root)
    job = _canonical_existing_directory(job_root)
    if _is_broad_or_repository_path(root) or _is_broad_or_repository_path(job):
        raise _UnsafeCleanupPath
    if job.parent != root:
        raise _UnsafeCleanupPath
    sentinel_path = job / SENTINEL_NAME
    if not sentinel_path.is_file() or _is_reparse_or_symlink(sentinel_path):
        raise _UnsafeCleanupPath
    sentinel = _validate_sentinel_value(_read_canonical_object(sentinel_path), job.name)
    return root, job, sentinel


def resolve_managed_job(
    managed_root: str | Path, job_root: str | Path
) -> tuple[Path, Path, str]:
    """Return one verified direct managed job identity or fail closed."""

    try:
        root, job, sentinel = _validate_scope(Path(managed_root), Path(job_root))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("managed job scope is invalid") from error
    return root, job, str(sentinel["job_id"])


def _tree_is_safe(path: Path) -> bool:
    try:
        for entry in os.scandir(path):
            child = Path(entry.path)
            if _is_reparse_or_symlink(child):
                return False
            info = child.lstat()
            if not stat.S_ISREG(info.st_mode):
                return False
    except OSError:
        return False
    return True


def _posix_tree_is_safe(directory_descriptor: int) -> bool:
    try:
        for name in os.listdir(directory_descriptor):
            information = os.stat(
                name, dir_fd=directory_descriptor, follow_symlinks=False
            )
            if not stat.S_ISREG(information.st_mode):
                return False
    except OSError:
        return False
    return True


def _flat_regular_children(path: Path) -> list[Path]:
    children: list[Path] = []
    for entry in os.scandir(path):
        child = Path(entry.path)
        if _is_reparse_or_symlink(child):
            raise _UnsafeCleanupPath
        info = child.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise _UnsafeCleanupPath
        children.append(child)
    return children


def _delete_windows_regular_file(path: Path) -> None:
    with _windows_locked_handle(
        path,
        access=_FILE_READ_ATTRIBUTES | _DELETE,
        flags=_FILE_FLAG_OPEN_REPARSE_POINT,
    ) as handle:
        if not _windows_handle_is_regular(_windows_handle_information(handle)):
            raise _UnsafeCleanupPath
        _windows_mark_delete(handle)


def _remove_flat_tree_windows(path: Path, handle: int) -> None:
    information = _windows_handle_information(handle)
    if (
        not information.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
        or information.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise _UnsafeCleanupPath
    children = _flat_regular_children(path)
    for child in children:
        _delete_windows_regular_file(child)
    if any(os.scandir(path)):
        raise OSError("job directory changed during cleanup")
    _windows_mark_delete(handle)


def _remove_flat_tree_posix(
    path: Path, root_descriptor: int, job_descriptor: int
) -> None:
    job_information = os.fstat(job_descriptor)
    if not stat.S_ISDIR(job_information.st_mode):
        raise _UnsafeCleanupPath
    names = os.listdir(job_descriptor)
    for name in names:
        info = os.stat(name, dir_fd=job_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise _UnsafeCleanupPath
    for name in names:
        os.unlink(name, dir_fd=job_descriptor)
    if os.listdir(job_descriptor):
        raise OSError("job directory changed during cleanup")
    current = os.stat(path.name, dir_fd=root_descriptor, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (
        job_information.st_dev,
        job_information.st_ino,
    ):
        raise _UnsafeCleanupPath
    os.rmdir(path.name, dir_fd=root_descriptor)


def _remove_tree(
    path: Path,
    *,
    windows_handle: int | None = None,
    posix_root_descriptor: int | None = None,
    posix_job_descriptor: int | None = None,
) -> None:
    """Remove one flat app-owned job without ever traversing a child path."""

    if os.name == "nt":
        if windows_handle is None:
            raise _UnsafeCleanupPath
        _remove_flat_tree_windows(path, windows_handle)
    else:
        if posix_root_descriptor is None or posix_job_descriptor is None:
            raise _UnsafeCleanupPath
        _remove_flat_tree_posix(path, posix_root_descriptor, posix_job_descriptor)


def staging_path_for(target: Path, job_id: str, candidate_sha256: str) -> Path:
    job_token = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return target.parent / (
        f".academic-pdf-en-zh-reader-{job_token}-{candidate_sha256}.staging"
    )


def _remove_external_staging(
    job_root: Path, *, posix_job_descriptor: int | None = None
) -> None:
    if posix_job_descriptor is None:
        intent_path = job_root / DELIVERY_INTENT_NAME
        if not intent_path.exists():
            return
        if _is_reparse_or_symlink(intent_path) or not intent_path.is_file():
            raise _UnsafeCleanupPath
        intent = _read_canonical_object(intent_path)
    else:
        try:
            intent = _decode_canonical_object(
                _read_posix_regular_bytes(posix_job_descriptor, DELIVERY_INTENT_NAME)
            )
        except FileNotFoundError:
            return
    if set(intent) != {"candidate_sha256", "job_id", "target"}:
        raise _UnsafeCleanupPath
    job_id = intent["job_id"]
    digest = intent["candidate_sha256"]
    target_raw = intent["target"]
    if (
        not isinstance(job_id, str)
        or job_id != job_root.name
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(target_raw, str)
    ):
        raise _UnsafeCleanupPath
    target = Path(target_raw)
    if not target.is_absolute() or _contains_unresolved_parts(target):
        raise _UnsafeCleanupPath
    parent = _canonical_existing_directory(target.parent)
    if os.path.normcase(str(parent / target.name)) != os.path.normcase(str(target)):
        raise _UnsafeCleanupPath
    staging = staging_path_for(target, job_id, digest)
    try:
        _delete_regular_file_if_hash_matches(staging, digest, missing_ok=True)
    except _UnsafeFileIdentity as error:
        raise _UnsafeCleanupPath from error


@contextmanager
def _locked_windows_job_path(managed_root: Path, job_root: Path):
    if (
        not managed_root.is_absolute()
        or not job_root.is_absolute()
        or _contains_unresolved_parts(managed_root)
        or _contains_unresolved_parts(job_root)
        or os.path.normcase(str(job_root.parent)) != os.path.normcase(str(managed_root))
    ):
        raise _UnsafeCleanupPath
    with _windows_locked_handle(
        job_root,
        access=_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _DELETE,
        flags=_FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
    ) as handle:
        information = _windows_handle_information(handle)
        if (
            not information.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY
            or information.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise _UnsafeCleanupPath
        yield handle


@contextmanager
def _locked_posix_job_path(managed_root: Path, job_root: Path):
    if (
        not managed_root.is_absolute()
        or not job_root.is_absolute()
        or _contains_unresolved_parts(managed_root)
        or _contains_unresolved_parts(job_root)
        or job_root.parent != managed_root
    ):
        raise _UnsafeCleanupPath
    root = _canonical_existing_directory(managed_root)
    if _is_broad_or_repository_path(root):
        raise _UnsafeCleanupPath
    job = root / job_root.name
    if job != job_root:
        raise _UnsafeCleanupPath
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_descriptor = os.open(root, flags)
    try:
        job_descriptor = os.open(job.name, flags, dir_fd=root_descriptor)
        try:
            job_information = os.fstat(job_descriptor)
            current = os.stat(job.name, dir_fd=root_descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(job_information.st_mode) or (
                current.st_dev,
                current.st_ino,
            ) != (job_information.st_dev, job_information.st_ino):
                raise _UnsafeCleanupPath
            try:
                home_information = Path.home().resolve(strict=True).stat()
            except OSError:
                home_information = None
            if home_information is not None and (
                home_information.st_dev,
                home_information.st_ino,
            ) == (job_information.st_dev, job_information.st_ino):
                raise _UnsafeCleanupPath
            names = set(os.listdir(job_descriptor))
            if ".git" in names:
                raise _UnsafeCleanupPath
            if "pyproject.toml" in names and "src" in names:
                source = os.stat("src", dir_fd=job_descriptor, follow_symlinks=False)
                if stat.S_ISDIR(source.st_mode):
                    raise _UnsafeCleanupPath
            sentinel = _validate_sentinel_value(
                _decode_canonical_object(
                    _read_posix_regular_bytes(job_descriptor, SENTINEL_NAME)
                ),
                job.name,
            )
            yield root, job, sentinel, root_descriptor, job_descriptor
        finally:
            os.close(job_descriptor)
    finally:
        os.close(root_descriptor)


def _write_replaced_posix(
    directory_descriptor: int, name: str, value: dict[str, object]
) -> None:
    encoded = canonical_json_bytes(value)
    temporary_name = f".{name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("managed sentinel write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_descriptor)


def _cleanup_validated_job(
    job: Path,
    sentinel: dict[str, object],
    *,
    observed: datetime,
    retention_mode: str | None,
    ttl_seconds: int | None,
    windows_handle: int | None = None,
    posix_root_descriptor: int | None = None,
    posix_job_descriptor: int | None = None,
) -> CleanupResult:
    if retention_mode is not None:
        ttl = MAX_RETENTION_SECONDS if ttl_seconds is None else ttl_seconds
        expires_at = observed + timedelta(seconds=ttl)
        try:
            created_at = _parse_time(sentinel["created_at"])
            value = _sentinel_value(
                job_id=job.name,
                created_at=created_at,
                expires_at=expires_at,
                mode=retention_mode,
            )
            if posix_job_descriptor is None:
                _write_replaced(job / SENTINEL_NAME, value)
            else:
                _write_replaced_posix(posix_job_descriptor, SENTINEL_NAME, value)
        except (OSError, UnicodeError, ValueError):
            return CleanupResult("CLEANUP_IO_FAILED", False)
        return CleanupResult(
            "CLEANUP_RETAINED", False, expires_at=_format_time(expires_at)
        )

    tree_is_safe = (
        _tree_is_safe(job)
        if posix_job_descriptor is None
        else _posix_tree_is_safe(posix_job_descriptor)
    )
    if not tree_is_safe:
        return CleanupResult("CLEANUP_UNSAFE_PATH", False)
    try:
        _remove_external_staging(job, posix_job_descriptor=posix_job_descriptor)
        _remove_tree(
            job,
            windows_handle=windows_handle,
            posix_root_descriptor=posix_root_descriptor,
            posix_job_descriptor=posix_job_descriptor,
        )
    except (_UnsafeCleanupPath, _UnsafeFileIdentity):
        return CleanupResult("CLEANUP_UNSAFE_PATH", False)
    except OSError:
        return CleanupResult("CLEANUP_IO_FAILED", False)
    return CleanupResult("CLEANUP_OK", True)


def cleanup_after_job(
    managed_root: str | Path,
    job_root: str | Path,
    *,
    outcome: Literal["success", "failure", "cancel"],
    retention_mode: Literal["resume", "debug"] | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> CleanupResult:
    """Delete by default, or retain explicitly for no more than 24 hours."""

    try:
        observed = _now_utc(now)
    except ValueError:
        return CleanupResult("RETENTION_POLICY_INVALID", False)
    if outcome not in _OUTCOMES:
        return CleanupResult("RETENTION_POLICY_INVALID", False)
    if retention_mode is not None:
        ttl = MAX_RETENTION_SECONDS if ttl_seconds is None else ttl_seconds
        if (
            retention_mode not in _RETENTION_MODES
            or isinstance(ttl, bool)
            or not isinstance(ttl, int)
            or not 1 <= ttl <= MAX_RETENTION_SECONDS
        ):
            return CleanupResult("RETENTION_POLICY_INVALID", False)
    elif ttl_seconds is not None:
        return CleanupResult("RETENTION_POLICY_INVALID", False)

    managed_path = Path(managed_root)
    job_path = Path(job_root)
    if os.name == "nt":
        try:
            with _locked_windows_job_path(managed_path, job_path) as handle:
                _, job, sentinel = _validate_scope(managed_path, job_path)
                return _cleanup_validated_job(
                    job,
                    sentinel,
                    observed=observed,
                    retention_mode=retention_mode,
                    ttl_seconds=ttl_seconds,
                    windows_handle=handle,
                )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return CleanupResult("CLEANUP_INVALID_SCOPE", False)

    try:
        with _locked_posix_job_path(managed_path, job_path) as (
            _,
            job,
            sentinel,
            root_descriptor,
            job_descriptor,
        ):
            return _cleanup_validated_job(
                job,
                sentinel,
                observed=observed,
                retention_mode=retention_mode,
                ttl_seconds=ttl_seconds,
                posix_root_descriptor=root_descriptor,
                posix_job_descriptor=job_descriptor,
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return CleanupResult("CLEANUP_INVALID_SCOPE", False)


def sweep_expired_jobs(
    managed_root: str | Path,
    *,
    now: datetime | None = None,
) -> SweepResult:
    """Delete expired direct managed children without following reparse entries."""

    try:
        observed = _now_utc(now)
        root = _canonical_existing_directory(Path(managed_root))
        if _is_broad_or_repository_path(root):
            raise _UnsafeCleanupPath
    except (OSError, ValueError):
        return SweepResult("CLEANUP_INVALID_SCOPE", 0, 0, 1)

    cleaned = 0
    skipped = 0
    failed = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return SweepResult("CLEANUP_IO_FAILED", 0, 0, 1)
    for entry in entries:
        candidate = Path(entry.path)
        try:
            if _is_reparse_or_symlink(candidate) or not entry.is_dir(
                follow_symlinks=False
            ):
                skipped += 1
                continue
            _, _, sentinel = _validate_scope(root, candidate)
            if _parse_time(sentinel["expires_at"]) > observed:
                continue
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            skipped += 1
            continue
        result = cleanup_after_job(
            root,
            candidate,
            outcome="failure",
            now=observed,
        )
        if result.cleaned:
            cleaned += 1
        else:
            failed += 1
    code = "CLEANUP_OK" if failed == 0 else "CLEANUP_PARTIAL"
    return SweepResult(code, cleaned, skipped, failed)
