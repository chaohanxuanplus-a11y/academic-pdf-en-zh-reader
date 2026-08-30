# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""No-clobber atomic publication of a fully validated candidate PDF."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from academic_pdf_en_zh_reader.atomic_file import atomic_publish_no_clobber
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import (
    DELIVERY_INTENT_NAME,
    MAX_RETENTION_SECONDS,
    _delete_regular_file_if_hash_matches,
    _windows_mark_delete,
    cleanup_after_job,
    resolve_managed_job,
    staging_path_for,
)
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    JobStateError,
    advance_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    ConcurrentStateError,
    load_job_state,
    write_job_state,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_CONTROL_BYTES = 16 * 1024 * 1024
_WINDOWS_FILE_EXISTS = {80, 183}
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_TEMPORARY = 0x100
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_CREATE_NEW = 1
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: Literal["ok", "error"]
    code: str
    delivered: bool
    output_sha256: str | None = None
    cleanup_code: str | None = None


class AtomicPublishError(OSError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DeliveryFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_reparse_or_symlink(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _normalized(path: Path, *, strict: bool) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    return path.resolve(strict=strict)


def _same_path(first: Path, second: Path) -> bool:
    try:
        if first.exists() and second.exists() and first.samefile(second):
            return True
    except OSError:
        pass
    return os.path.normcase(str(_normalized(first, strict=False))) == os.path.normcase(
        str(_normalized(second, strict=False))
    )


def _path_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((str(path), str(root)))
        return os.path.normcase(common) == os.path.normcase(str(root))
    except ValueError:
        return False


def _controlled_regular_file(path: Path, job_root: Path) -> Path:
    try:
        resolved = _normalized(path, strict=True)
        if (
            not _path_within(resolved, job_root)
            or _is_reparse_or_symlink(path)
            or not resolved.is_file()
        ):
            raise _DeliveryFailure("DELIVERY_BINDING_MISMATCH")
    except (OSError, RuntimeError) as error:
        if isinstance(error, _DeliveryFailure):
            raise
        raise _DeliveryFailure("DELIVERY_BINDING_MISMATCH") from error
    return resolved


def _safe_output(path: Path, job_root: Path, managed_root: Path) -> Path:
    try:
        if not path.is_absolute() or any(
            part in {".", "..", "~"} for part in path.parts
        ):
            raise _DeliveryFailure("OUTPUT_PATH_UNSAFE")
        parent = path.parent.resolve(strict=True)
        if (
            _is_reparse_or_symlink(path.parent)
            or os.path.normcase(str(path.parent)) != os.path.normcase(str(parent))
            or not parent.is_dir()
        ):
            raise _DeliveryFailure("OUTPUT_PATH_UNSAFE")
        resolved = parent / path.name
        if _path_within(resolved, job_root) or _path_within(resolved, managed_root):
            raise _DeliveryFailure("OUTPUT_PATH_UNSAFE")
        if os.path.lexists(resolved) and _is_reparse_or_symlink(resolved):
            raise _DeliveryFailure("OUTPUT_PATH_UNSAFE")
        return resolved
    except (OSError, RuntimeError) as error:
        if isinstance(error, _DeliveryFailure):
            raise
        raise _DeliveryFailure("OUTPUT_PATH_UNSAFE") from error


def _read_canonical_json(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        if path.stat().st_size > _MAX_CONTROL_BYTES:
            raise ValueError
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
            raise ValueError
        return value, raw
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise _DeliveryFailure("DELIVERY_BINDING_MISMATCH") from error


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _files_equal(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(_COPY_CHUNK_BYTES)
            right_chunk = right.read(_COPY_CHUNK_BYTES)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


@contextmanager
def _open_staging_exclusive(path: Path):
    if os.name != "nt":
        with path.open("x+b") as stream:
            yield stream
        return

    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        _GENERIC_READ | _GENERIC_WRITE | _DELETE,
        0,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_TEMPORARY,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error in _WINDOWS_FILE_EXISTS:
            raise FileExistsError(error, "staging already exists")
        raise OSError(error, "staging creation failed")
    try:
        descriptor = msvcrt.open_osfhandle(
            int(handle), os.O_RDWR | getattr(os, "O_BINARY", 0)
        )
    except Exception:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise
    with os.fdopen(descriptor, "w+b") as stream:
        try:
            yield stream
        except BaseException:
            _windows_mark_delete(int(handle))
            raise


def _clear_temporary_attributes(path: Path) -> None:
    if os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    set_attributes = kernel32.SetFileAttributesW
    set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    set_attributes.restype = ctypes.c_int
    attributes = get_attributes(str(path))
    if attributes == _INVALID_FILE_ATTRIBUTES:
        raise OSError("published file attributes unavailable")
    normalized = attributes & ~(_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_TEMPORARY)
    if not set_attributes(str(path), normalized):
        raise OSError("published file attributes could not be normalized")


def _copy_candidate_to_staging(
    candidate: Path, staging: Path, expected_hash: str
) -> None:
    with (
        candidate.open("rb") as source,
        _open_staging_exclusive(staging) as destination,
    ):
        while chunk := source.read(_COPY_CHUNK_BYTES):
            destination.write(chunk)
        destination.flush()
        os.fsync(destination.fileno())
        source.seek(0)
        destination.seek(0)
        digest = hashlib.sha256()
        while True:
            source_chunk = source.read(_COPY_CHUNK_BYTES)
            staging_chunk = destination.read(_COPY_CHUNK_BYTES)
            if source_chunk != staging_chunk:
                raise OSError("staging verification failed")
            if not source_chunk:
                break
            digest.update(staging_chunk)
        if digest.hexdigest() != expected_hash:
            raise OSError("staging verification failed")


def _atomic_publish(staging: Path, output: Path) -> None:
    """Publish without replacement; both platform branches are race-safe."""

    if staging.parent != output.parent:
        raise AtomicPublishError("ATOMIC_DELIVERY_UNAVAILABLE")
    try:
        atomic_publish_no_clobber(staging, output)
    except FileExistsError as error:
        raise AtomicPublishError("OUTPUT_EXISTS") from error
    except OSError as error:
        unsupported = {
            errno.EPERM,
            errno.EACCES,
            errno.EXDEV,
            getattr(errno, "ENOTSUP", errno.EPERM),
            getattr(errno, "EOPNOTSUPP", errno.EPERM),
        }
        code = (
            "ATOMIC_DELIVERY_UNAVAILABLE"
            if error.errno in unsupported
            else "DELIVERY_COMMIT_FAILED"
        )
        raise AtomicPublishError(code) from error


def _write_delivery_intent(
    job_root: Path, *, job_id: str, candidate_sha256: str, output: Path
) -> None:
    encoded = canonical_json_bytes(
        {
            "candidate_sha256": candidate_sha256,
            "job_id": job_id,
            "target": str(output),
        }
    )
    path = job_root / DELIVERY_INTENT_NAME
    try:
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise _DeliveryFailure("DELIVERY_STAGE_FAILED") from error


def _validate_retention(retention_mode: str | None, ttl_seconds: int | None) -> bool:
    if retention_mode is None:
        return ttl_seconds is None
    ttl = MAX_RETENTION_SECONDS if ttl_seconds is None else ttl_seconds
    return (
        retention_mode in {"resume", "debug"}
        and not isinstance(ttl, bool)
        and isinstance(ttl, int)
        and 1 <= ttl <= MAX_RETENTION_SECONDS
    )


def _verify_bindings(
    *,
    candidate: Path,
    manifest_path: Path,
    qa_path: Path,
    state,
) -> str:
    manifest, manifest_bytes = _read_canonical_json(manifest_path)
    qa, qa_bytes = _read_canonical_json(qa_path)
    candidate_sha256 = _hash_file(candidate)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    qa_sha256 = hashlib.sha256(qa_bytes).hexdigest()
    artifact_hashes = state.artifact_hashes
    expected = (
        manifest.get("output_pdf_sha256"),
        qa.get("output_pdf_sha256"),
        artifact_hashes.get("pdf"),
    )
    if (
        not _SHA256.fullmatch(candidate_sha256)
        or any(value != candidate_sha256 for value in expected)
        or qa.get("render_manifest_hash") != manifest_sha256
        or artifact_hashes.get("render-manifest") != manifest_sha256
        or artifact_hashes.get("qa") != qa_sha256
    ):
        raise _DeliveryFailure("DELIVERY_BINDING_MISMATCH")
    if qa.get("passed") is not True:
        raise _DeliveryFailure("DELIVERY_NOT_VALIDATED")
    return candidate_sha256


def deliver_validated_pdf(
    *,
    managed_root: str | Path,
    job_root: str | Path,
    state_path: str | Path,
    source_path: str | Path,
    candidate_path: str | Path,
    render_manifest_path: str | Path,
    qa_path: str | Path,
    output_path: str | Path,
    retention_mode: Literal["resume", "debug"] | None = None,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
) -> DeliveryResult:
    """Publish exact candidate bytes only after all persisted bindings revalidate."""

    managed = Path(managed_root)
    job = Path(job_root)
    source = Path(source_path)
    candidate_input = Path(candidate_path)
    output_input = Path(output_path)
    staging: Path | None = None
    staging_owned = False
    delivered = False
    digest: str | None = None
    output: Path | None = None
    code = "DELIVERY_OK"
    retention_valid = _validate_retention(retention_mode, ttl_seconds)

    try:
        if not retention_valid:
            raise _DeliveryFailure("RETENTION_POLICY_INVALID")
        if (
            _same_path(source, candidate_input)
            or _same_path(source, output_input)
            or _same_path(candidate_input, output_input)
        ):
            raise _DeliveryFailure("OUTPUT_PATH_CONFLICT")
        try:
            managed_resolved, job_resolved, managed_job_id = resolve_managed_job(
                managed, job
            )
        except ValueError as error:
            raise _DeliveryFailure("DELIVERY_BINDING_MISMATCH") from error
        candidate = _controlled_regular_file(candidate_input, job_resolved)
        manifest_path = _controlled_regular_file(
            Path(render_manifest_path), job_resolved
        )
        checked_qa_path = _controlled_regular_file(Path(qa_path), job_resolved)
        checked_state_path = _controlled_regular_file(Path(state_path), job_resolved)
        output = _safe_output(output_input, job_resolved, managed_resolved)
        if os.path.lexists(output):
            raise _DeliveryFailure("OUTPUT_EXISTS")
        try:
            state = load_job_state(checked_state_path)
        except Exception as error:
            raise _DeliveryFailure("DELIVERY_NOT_VALIDATED") from error
        if state.stage is not JobStage.VALIDATED or state.job_id != managed_job_id:
            raise _DeliveryFailure("DELIVERY_NOT_VALIDATED")
        digest = _verify_bindings(
            candidate=candidate,
            manifest_path=manifest_path,
            qa_path=checked_qa_path,
            state=state,
        )
        _write_delivery_intent(
            job_resolved,
            job_id=state.job_id,
            candidate_sha256=digest,
            output=output,
        )
        staging = staging_path_for(output, state.job_id, digest)
        try:
            _copy_candidate_to_staging(candidate, staging, digest)
        except OSError as error:
            (job_resolved / DELIVERY_INTENT_NAME).unlink(missing_ok=True)
            raise _DeliveryFailure("DELIVERY_STAGE_FAILED") from error
        staging_owned = True
        try:
            _atomic_publish(staging, output)
        except AtomicPublishError as error:
            raise _DeliveryFailure(error.code) from error
        delivered = True
        try:
            _clear_temporary_attributes(output)
        except OSError as error:
            raise _DeliveryFailure("DELIVERY_VERIFY_FAILED") from error
        if _hash_file(output) != digest or not _files_equal(candidate, output):
            raise _DeliveryFailure("DELIVERY_VERIFY_FAILED")
        try:
            previous_hash = state_hash(state)
            final_state = advance_job(
                state,
                JobStage.FINALIZED,
                {"final-pdf": digest},
                expected_previous_state_hash=previous_hash,
            )
            write_job_state(
                checked_state_path,
                final_state,
                expected_previous_state_hash=previous_hash,
            )
        except (ConcurrentStateError, JobStateError, OSError, ValueError) as error:
            raise _DeliveryFailure("DELIVERY_STATE_FAILED") from error
    except _DeliveryFailure as error:
        code = error.code
    except KeyboardInterrupt:
        code = "DELIVERY_CANCELLED"
        if output is not None and digest is not None and output.is_file():
            try:
                delivered = _hash_file(output) == digest
            except OSError:
                delivered = False
    except Exception:
        code = "DELIVERY_COMMIT_FAILED" if delivered else "DELIVERY_STAGE_FAILED"
    finally:
        if staging_owned and staging is not None and digest is not None:
            try:
                _delete_regular_file_if_hash_matches(staging, digest, missing_ok=True)
            except OSError:
                if code == "DELIVERY_OK":
                    code = "CLEANUP_FAILED"

    cleanup = cleanup_after_job(
        managed,
        job,
        outcome="success" if code == "DELIVERY_OK" else "failure",
        retention_mode=retention_mode if retention_valid else None,
        ttl_seconds=ttl_seconds if retention_valid else None,
        now=now,
    )
    cleanup_failed = cleanup.code not in {"CLEANUP_OK", "CLEANUP_RETAINED"}
    if code == "DELIVERY_OK" and cleanup_failed:
        code = "CLEANUP_FAILED"
    return DeliveryResult(
        status="ok" if code == "DELIVERY_OK" else "error",
        code=code,
        delivered=delivered,
        output_sha256=digest if delivered else None,
        cleanup_code=cleanup.code,
    )
