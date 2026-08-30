# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import (
    DELIVERY_INTENT_NAME,
    MAX_RETENTION_SECONDS,
    cleanup_after_job,
    create_managed_job,
    staging_path_for,
    sweep_expired_jobs,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _managed_job(tmp_path: Path, job_id: str = "job-001") -> tuple[Path, Path]:
    managed_root = tmp_path / "managed-jobs"
    managed_root.mkdir()
    job_root = create_managed_job(managed_root, job_id, now=NOW)
    (job_root / "private.txt").write_text("private", encoding="utf-8")
    return managed_root, job_root


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_success_failure_and_cancel_delete_managed_job_by_default(
    tmp_path: Path, outcome: str
) -> None:
    managed_root, job_root = _managed_job(tmp_path)

    result = cleanup_after_job(
        managed_root,
        job_root,
        outcome=outcome,
        now=NOW,
    )

    assert result.code == "CLEANUP_OK"
    assert result.cleaned is True
    assert not job_root.exists()


@pytest.mark.parametrize("mode", ["resume", "debug"])
def test_explicit_retention_is_bounded_and_swept_at_expiry(
    tmp_path: Path, mode: str
) -> None:
    managed_root, job_root = _managed_job(tmp_path)

    retained = cleanup_after_job(
        managed_root,
        job_root,
        outcome="failure",
        retention_mode=mode,
        ttl_seconds=MAX_RETENTION_SECONDS,
        now=NOW,
    )

    assert retained.code == "CLEANUP_RETAINED"
    assert retained.cleaned is False
    assert job_root.exists()

    before = sweep_expired_jobs(
        managed_root, now=NOW + timedelta(seconds=MAX_RETENTION_SECONDS - 1)
    )
    assert before.cleaned_jobs == 0
    assert job_root.exists()

    expired = sweep_expired_jobs(
        managed_root, now=NOW + timedelta(seconds=MAX_RETENTION_SECONDS)
    )
    assert expired.code == "CLEANUP_OK"
    assert expired.cleaned_jobs == 1
    assert not job_root.exists()


def test_retention_over_24_hours_fails_without_claiming_cleanup(
    tmp_path: Path,
) -> None:
    managed_root, job_root = _managed_job(tmp_path)

    result = cleanup_after_job(
        managed_root,
        job_root,
        outcome="failure",
        retention_mode="debug",
        ttl_seconds=MAX_RETENTION_SECONDS + 1,
        now=NOW,
    )

    assert result.code == "RETENTION_POLICY_INVALID"
    assert result.cleaned is False
    assert job_root.exists()


def test_cleanup_requires_a_direct_managed_child_and_app_sentinel(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "managed-jobs"
    managed_root.mkdir()
    unmarked = managed_root / "unmarked"
    unmarked.mkdir()
    (unmarked / "keep.txt").write_text("keep", encoding="utf-8")

    missing = cleanup_after_job(managed_root, unmarked, outcome="failure", now=NOW)
    assert missing.code == "CLEANUP_INVALID_SCOPE"
    assert unmarked.exists()

    marked = create_managed_job(managed_root, "job-002", now=NOW)
    wrong_root = tmp_path / "other-managed-root"
    wrong_root.mkdir()
    indirect = cleanup_after_job(wrong_root, marked, outcome="failure", now=NOW)
    assert indirect.code == "CLEANUP_INVALID_SCOPE"
    assert marked.exists()


def test_cleanup_rejects_broad_or_repository_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    result = cleanup_after_job(
        repository.parent,
        repository,
        outcome="failure",
        now=NOW,
    )
    assert result.code == "CLEANUP_INVALID_SCOPE"
    assert repository.exists()

    filesystem_root = Path(Path.cwd().anchor)
    root_result = cleanup_after_job(
        filesystem_root,
        filesystem_root,
        outcome="failure",
        now=NOW,
    )
    assert root_result.code == "CLEANUP_INVALID_SCOPE"

    home = Path.home().resolve()
    home_result = cleanup_after_job(
        home.parent,
        home,
        outcome="failure",
        now=NOW,
    )
    assert home_result.code == "CLEANUP_INVALID_SCOPE"


def test_cleanup_never_follows_a_symlink_inside_the_job(tmp_path: Path) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "must-survive.txt"
    outside_file.write_text("survive", encoding="utf-8")
    link = job_root / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert result.code == "CLEANUP_UNSAFE_PATH"
    assert job_root.exists()
    assert outside_file.read_text(encoding="utf-8") == "survive"


def test_cleanup_rejects_nested_directories_without_touching_their_contents(
    tmp_path: Path,
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    nested = job_root / "nested"
    nested.mkdir()
    nested_file = nested / "must-survive.txt"
    nested_file.write_text("survive", encoding="utf-8")

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert result.code == "CLEANUP_UNSAFE_PATH"
    assert result.cleaned is False
    assert nested_file.read_text(encoding="utf-8") == "survive"
    assert job_root.exists()


def test_cleanup_delete_phase_never_traverses_a_swapped_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    nested = job_root / "nested"
    nested.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "must-survive.txt"
    outside_file.write_text("survive", encoding="utf-8")
    from academic_pdf_en_zh_reader.job import cleanup as cleanup_module

    real_scandir = cleanup_module.os.scandir

    def swapped_scandir(path: Path | str):
        if Path(path) == nested:
            return real_scandir(outside)
        return real_scandir(path)

    # Model a safe preflight followed by a directory-entry swap before deletion.
    monkeypatch.setattr(cleanup_module, "_tree_is_safe", lambda _path: True)
    monkeypatch.setattr(cleanup_module.os, "scandir", swapped_scandir)

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert result.code == "CLEANUP_UNSAFE_PATH"
    assert result.cleaned is False
    assert outside_file.read_text(encoding="utf-8") == "survive"
    assert job_root.exists()


def test_cleanup_never_deletes_a_job_root_swapped_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    moved_job = managed_root / "original-moved"
    victim = managed_root / "victim"
    victim.mkdir()
    victim_file = victim / "must-survive.txt"
    victim_file.write_text("survive", encoding="utf-8")
    from academic_pdf_en_zh_reader.job import cleanup as cleanup_module

    real_check = (
        cleanup_module._tree_is_safe
        if os.name == "nt"
        else cleanup_module._posix_tree_is_safe
    )

    def race(path_or_descriptor) -> bool:
        safe = real_check(path_or_descriptor)
        try:
            job_root.rename(moved_job)
            victim.rename(job_root)
        except OSError:
            pass
        return safe

    monkeypatch.setattr(
        cleanup_module,
        "_tree_is_safe" if os.name == "nt" else "_posix_tree_is_safe",
        race,
    )

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert victim_file.exists() or (job_root / victim_file.name).exists()
    if os.name == "nt":
        assert result.code == "CLEANUP_OK"
        assert result.cleaned is True
    else:
        assert result.code == "CLEANUP_UNSAFE_PATH"
        assert result.cleaned is False


def test_reparse_detection_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    from academic_pdf_en_zh_reader.job import cleanup as cleanup_module

    real_check = cleanup_module._is_reparse_or_symlink

    def injected(path: Path) -> bool:
        if path.name == "private.txt":
            return True
        return real_check(path)

    if os.name == "nt":
        monkeypatch.setattr(cleanup_module, "_is_reparse_or_symlink", injected)
    else:
        monkeypatch.setattr(cleanup_module, "_posix_tree_is_safe", lambda _fd: False)

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert result.code == "CLEANUP_UNSAFE_PATH"
    assert job_root.exists()


def test_cleanup_io_failure_returns_a_stable_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    from academic_pdf_en_zh_reader.job import cleanup as cleanup_module

    def fail_remove(_path: Path, **_kwargs) -> None:
        raise OSError("injected failure without document data")

    monkeypatch.setattr(cleanup_module, "_remove_tree", fail_remove)

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert result.code == "CLEANUP_IO_FAILED"
    assert result.cleaned is False
    assert job_root.exists()


def test_cleanup_never_deletes_an_external_staging_file_with_wrong_bytes(
    tmp_path: Path,
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    target = delivery / "paper.pdf"
    expected_digest = "a" * 64
    intent = {
        "candidate_sha256": expected_digest,
        "job_id": job_root.name,
        "target": str(target.resolve()),
    }
    (job_root / DELIVERY_INTENT_NAME).write_bytes(canonical_json_bytes(intent))
    staging = staging_path_for(target.resolve(), job_root.name, expected_digest)
    staging.write_bytes(b"unowned bytes")

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    assert result.code == "CLEANUP_UNSAFE_PATH"
    assert result.cleaned is False
    assert staging.read_bytes() == b"unowned bytes"
    assert job_root.exists()


def test_cleanup_never_unlinks_a_staging_path_swapped_after_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    target = (delivery / "paper.pdf").resolve()
    owned = b"owned staging bytes"
    import hashlib

    expected_digest = hashlib.sha256(owned).hexdigest()
    intent = {
        "candidate_sha256": expected_digest,
        "job_id": job_root.name,
        "target": str(target),
    }
    (job_root / DELIVERY_INTENT_NAME).write_bytes(canonical_json_bytes(intent))
    staging = staging_path_for(target, job_root.name, expected_digest)
    staging.write_bytes(owned)
    owned_backup = delivery / "owned-backup"
    competitor = delivery / "must-survive.txt"
    competitor.write_bytes(b"survive")
    from academic_pdf_en_zh_reader.job import cleanup as cleanup_module

    real_sha256 = cleanup_module.hashlib.sha256

    class RacingDigest:
        def __init__(self) -> None:
            self._digest = real_sha256()

        def update(self, data: bytes) -> None:
            self._digest.update(data)

        def hexdigest(self) -> str:
            digest = self._digest.hexdigest()
            try:
                staging.rename(owned_backup)
                competitor.rename(staging)
            except OSError:
                pass
            return digest

    def racing_sha256(data: bytes = b""):
        return real_sha256(data) if data else RacingDigest()

    monkeypatch.setattr(cleanup_module.hashlib, "sha256", racing_sha256)

    result = cleanup_after_job(managed_root, job_root, outcome="failure", now=NOW)

    surviving_path = competitor if competitor.exists() else staging
    assert surviving_path.read_bytes() == b"survive"
    if os.name == "nt":
        assert result.code == "CLEANUP_OK"
        assert result.cleaned is True
    else:
        assert result.code == "CLEANUP_UNSAFE_PATH"
        assert result.cleaned is False


def test_relative_or_parent_traversal_paths_are_rejected(tmp_path: Path) -> None:
    relative = Path("managed-jobs")
    result = cleanup_after_job(
        relative,
        relative / "job-001",
        outcome="failure",
        now=NOW,
    )
    assert result.code == "CLEANUP_INVALID_SCOPE"

    managed_root, job_root = _managed_job(tmp_path, "job-003")
    unresolved = managed_root / "nested" / ".." / job_root.name
    result = cleanup_after_job(
        managed_root,
        unresolved,
        outcome="failure",
        now=NOW,
    )
    assert result.code == "CLEANUP_INVALID_SCOPE"
    assert job_root.exists()


def test_sweeper_skips_unmanaged_and_symlink_entries(tmp_path: Path) -> None:
    managed_root, job_root = _managed_job(tmp_path)
    retained = cleanup_after_job(
        managed_root,
        job_root,
        outcome="failure",
        retention_mode="resume",
        ttl_seconds=1,
        now=NOW,
    )
    assert retained.code == "CLEANUP_RETAINED"

    unmanaged = managed_root / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "keep").write_text("keep", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    link = managed_root / "linked"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        link = None

    swept = sweep_expired_jobs(managed_root, now=NOW + timedelta(seconds=1))

    assert swept.cleaned_jobs == 1
    assert swept.skipped_jobs >= 1
    assert unmanaged.exists()
    assert external.exists()
    if link is not None:
        assert os.path.lexists(link)
