# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Validated same-directory temporary writes and atomic replacement."""

from __future__ import annotations

import errno
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    JobState,
    JobStateError,
    _build_rerun,
    state_hash,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.security.input_copy import read_bounded_regular_file
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS


class ArtifactExistsError(FileExistsError):
    """Raised when code attempts to replace an immutable artifact."""


class ConcurrentStateError(RuntimeError):
    """Raised when the on-disk job state changed since it was read."""


class ReusableArtifactError(ValueError):
    """Raised when recorded extraction artifacts cannot be safely reused."""


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _exclusive_lock(destination: Path):
    lock_path = destination.with_name(f".{destination.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        try:
            _lock_descriptor(descriptor)
            locked = True
        except OSError as exc:
            contention_errors = {
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EDEADLK", errno.EACCES),
            }
            if exc.errno in contention_errors:
                raise ConcurrentStateError(
                    f"write lock is held for {destination.name}"
                ) from exc
            raise
        yield
    finally:
        try:
            if locked:
                _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read validated JSON artifact {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact {path.name} must be an object")
    return value


def _read_canonical_artifact(
    path: str | Path, schema_name: str
) -> tuple[dict[str, Any], str]:
    artifact_path = Path(path)
    try:
        raw = artifact_path.read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReusableArtifactError(
            f"cannot read reusable {schema_name} artifact"
        ) from exc
    if not isinstance(value, dict):
        raise ReusableArtifactError(f"reusable {schema_name} must be a JSON object")
    validate_artifact(schema_name, value)
    if canonical_json_bytes(value) != raw:
        raise ReusableArtifactError(
            f"reusable {schema_name} artifact is not canonical JSON"
        )
    return value, sha256_bytes(raw)


def create_verified_rerun(
    *,
    state: JobState,
    preflight_path: str | Path,
    normalization_path: str | Path,
    normalized_pdf_path: str | Path,
    source_path: str | Path,
    units_path: str | Path,
    new_job_id: str,
    next_translation_revision: int | None = None,
) -> JobState:
    """Verify reusable bytes and create the next translation revision atomically."""

    validate_artifact("job-state", state.to_dict())
    if list(JobStage).index(state.stage) < list(JobStage).index(JobStage.EXTRACTED):
        raise JobStateError("reuse requires a verified extracted stage")

    artifacts: dict[str, dict[str, Any]] = {}
    actual_hashes: dict[str, str] = {}
    for name, path in (
        ("preflight", preflight_path),
        ("normalization", normalization_path),
        ("source", source_path),
        ("units", units_path),
    ):
        artifact, digest = _read_canonical_artifact(path, name)
        artifacts[name] = artifact
        actual_hashes[name] = digest
        if state.artifact_hashes.get(name) != digest:
            raise ReusableArtifactError(
                f"reusable {name} hash does not match the recorded state"
            )

    try:
        normalized_pdf = read_bounded_regular_file(
            Path(normalized_pdf_path),
            max_bytes=DEFAULT_LIMITS.max_normalized_pdf_bytes,
        )
    except Exception as exc:
        raise ReusableArtifactError(
            "cannot read reusable normalized PDF artifact"
        ) from exc
    actual_hashes["normalized-pdf"] = normalized_pdf.sha256
    if state.artifact_hashes.get("normalized-pdf") != normalized_pdf.sha256:
        raise ReusableArtifactError(
            "reusable normalized-pdf hash does not match the recorded state"
        )

    source_hashes = {
        state.source_sha256,
        artifacts["preflight"]["source_sha256"],
        artifacts["normalization"]["source_sha256"],
        artifacts["source"]["source_sha256"],
        artifacts["units"]["source_sha256"],
    }
    if len(source_hashes) != 1:
        raise ReusableArtifactError(
            "reusable artifacts do not share the job source_sha256"
        )
    normalized_hashes = {
        normalized_pdf.sha256,
        artifacts["normalization"]["normalized_pdf_sha256"],
        artifacts["source"]["normalized_pdf_sha256"],
        artifacts["units"]["normalized_pdf_sha256"],
    }
    if len(normalized_hashes) != 1:
        raise ReusableArtifactError(
            "reusable artifacts do not share normalized_pdf_sha256"
        )
    if artifacts["normalization"]["preflight_sha256"] != actual_hashes["preflight"]:
        raise ReusableArtifactError(
            "reusable normalization does not bind the preflight artifact"
        )
    if artifacts["normalization"]["normalized_pdf_bytes"] != normalized_pdf.size:
        raise ReusableArtifactError(
            "reusable normalization does not bind the normalized PDF size"
        )
    if artifacts["preflight"]["passed"] is not True:
        raise ReusableArtifactError("reusable preflight must have passed")

    return _build_rerun(
        previous_job_id=state.job_id,
        source_sha256=state.source_sha256,
        translation_revision=state.translation_revision,
        artifact_hashes=actual_hashes,
        new_job_id=new_job_id,
        next_translation_revision=next_translation_revision,
    )


def _replace_with_validated_json(
    destination: Path, value: dict[str, object], schema_name: str
) -> bytes:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(value)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        staged = _read_json(temporary_path)
        validate_artifact(schema_name, staged)
        if canonical_json_bytes(staged) != encoded:
            raise ValueError("staged JSON did not round-trip canonically")
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    persisted = destination.read_bytes()
    if persisted != encoded:
        raise OSError("atomic JSON replacement did not preserve exact bytes")
    validate_artifact(schema_name, _read_json(destination))
    return persisted


def write_immutable_artifact(
    path: str | Path, value: dict[str, object], schema_name: str
) -> str:
    """Write one validated artifact once; existing paths are never replaced."""

    destination = Path(path)
    encoded = canonical_json_bytes(value)
    staged = json.loads(encoded.decode("utf-8", errors="strict"))
    validate_artifact(schema_name, staged)
    digest = write_immutable_bytes(destination, encoded)
    persisted = _read_json(destination)
    validate_artifact(schema_name, persisted)
    if canonical_json_bytes(persisted) != encoded:
        raise OSError("immutable JSON commit did not preserve canonical content")
    return digest


def write_immutable_bytes(path: str | Path, encoded: bytes) -> str:
    """Commit prevalidated bytes once without exposing a partial final file."""

    if not isinstance(encoded, bytes) or not encoded:
        raise ValueError("immutable artifact bytes must be non-empty")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        if temporary_path.read_bytes() != encoded:
            raise OSError("staged immutable bytes differ")
        with _exclusive_lock(destination):
            if destination.exists() or destination.is_symlink():
                raise ArtifactExistsError(
                    f"artifact already exists: {destination.name}"
                )
            try:
                os.link(temporary_path, destination)
            except FileExistsError as exc:
                raise ArtifactExistsError(
                    f"artifact already exists: {destination.name}"
                ) from exc
            temporary_path.unlink()
            temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    persisted = destination.read_bytes()
    if persisted != encoded:
        raise OSError("atomic immutable commit did not preserve exact bytes")
    return sha256_bytes(persisted)


def load_job_state(path: str | Path) -> JobState:
    """Load a schema-valid state snapshot and validate its history."""

    value = _read_json(Path(path))
    validate_artifact("job-state", value)
    return JobState.from_dict(value)


def write_job_state(
    path: str | Path,
    state: JobState,
    *,
    expected_previous_state_hash: str | None = None,
) -> str:
    """Create or compare-and-swap one mutable state ledger atomically."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(destination):
        if destination.exists():
            current = load_job_state(destination)
            current_hash = state_hash(current)
            if expected_previous_state_hash != current_hash:
                raise ConcurrentStateError("persisted job state hash does not match")
            if state.history[-1].previous_state_hash != current_hash:
                raise ConcurrentStateError(
                    "new state does not extend the persisted hash"
                )
        elif expected_previous_state_hash is not None:
            raise ConcurrentStateError("cannot compare-and-swap a missing job state")
        persisted = _replace_with_validated_json(
            destination, state.to_dict(), "job-state"
        )
    return sha256_bytes(persisted)
