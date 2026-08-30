# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import create_managed_job, staging_path_for
from academic_pdf_en_zh_reader.job.deliver import deliver_validated_pdf
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import write_job_state

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
CANDIDATE_BYTES = b"%PDF-1.7\n% deterministic candidate\n%%EOF\n"


def _advance(state, stage: JobStage, artifacts: dict[str, str]):
    return advance_job(
        state,
        stage,
        artifacts,
        expected_previous_state_hash=state_hash(state),
    )


def _validated_state(*, candidate_sha256: str, manifest_sha256: str, qa_sha256: str):
    state = create_job(job_id="job-001", source_sha256="0" * 64, translation_revision=1)
    stages = [
        (
            JobStage.PREFLIGHTED,
            {
                "preflight": "1" * 64,
                "normalization": "a" * 64,
                "normalized-pdf": "b" * 64,
            },
        ),
        (JobStage.EXTRACTED, {"source": "2" * 64, "units": "3" * 64}),
        (JobStage.TRANSLATED, {"translation": "4" * 64}),
        (JobStage.INDEPENDENTLY_REVIEWED, {"review": "5" * 64}),
        (JobStage.ANNOTATED, {"annotations": "6" * 64}),
        (
            JobStage.LAID_OUT,
            {
                "frame-graph": "7" * 64,
                "layout": "8" * 64,
                "finalization-receipt": "e" * 64,
            },
        ),
        (
            JobStage.RENDERED,
            {"render-manifest": manifest_sha256, "pdf": candidate_sha256},
        ),
        (
            JobStage.VALIDATED,
            {"qa": qa_sha256, "provenance": "9" * 64},
        ),
    ]
    for stage, artifacts in stages:
        state = _advance(state, stage, artifacts)
    return state


def _fixture(tmp_path: Path) -> dict[str, Path]:
    managed_root = tmp_path / "managed-jobs"
    managed_root.mkdir()
    job_root = create_managed_job(managed_root, "job-001", now=NOW)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source bytes")
    candidate = job_root / "candidate.pdf"
    candidate.write_bytes(CANDIDATE_BYTES)
    candidate_sha256 = sha256_bytes(CANDIDATE_BYTES)

    manifest = {
        "artifact_kind": "render-manifest",
        "output_pdf_sha256": candidate_sha256,
    }
    manifest_path = job_root / "render-manifest.json"
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha256 = sha256_bytes(manifest_bytes)

    qa = {
        "artifact_kind": "qa",
        "output_pdf_sha256": candidate_sha256,
        "passed": True,
        "render_manifest_hash": manifest_sha256,
    }
    qa_path = job_root / "qa.json"
    qa_bytes = canonical_json_bytes(qa)
    qa_path.write_bytes(qa_bytes)
    qa_sha256 = sha256_bytes(qa_bytes)

    state_path = job_root / "job-state.json"
    write_job_state(
        state_path,
        _validated_state(
            candidate_sha256=candidate_sha256,
            manifest_sha256=manifest_sha256,
            qa_sha256=qa_sha256,
        ),
    )
    output_dir = tmp_path / "delivery"
    output_dir.mkdir()
    output = output_dir / "paper.bilingual-a3.zh-CN.pdf"
    return {
        "managed_root": managed_root,
        "job_root": job_root,
        "source": source,
        "candidate": candidate,
        "manifest": manifest_path,
        "qa": qa_path,
        "state": state_path,
        "output": output,
    }


def _deliver(paths: dict[str, Path], **overrides):
    arguments = {
        "managed_root": paths["managed_root"],
        "job_root": paths["job_root"],
        "state_path": paths["state"],
        "source_path": paths["source"],
        "candidate_path": paths["candidate"],
        "render_manifest_path": paths["manifest"],
        "qa_path": paths["qa"],
        "output_path": paths["output"],
        "now": NOW,
    }
    arguments.update(overrides)
    return deliver_validated_pdf(**arguments)


def test_success_is_exact_atomic_and_cleans_private_job(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    result = _deliver(paths)

    assert result.status == "ok"
    assert result.code == "DELIVERY_OK"
    assert result.delivered is True
    assert result.output_sha256 == sha256_bytes(CANDIDATE_BYTES)
    assert paths["output"].read_bytes() == CANDIDATE_BYTES
    assert not paths["job_root"].exists()
    assert list(paths["output"].parent.iterdir()) == [paths["output"]]
    if hasattr(paths["output"].stat(), "st_file_attributes"):
        attributes = paths["output"].stat().st_file_attributes
        assert attributes & 0x2 == 0
        assert attributes & 0x100 == 0


def test_success_can_explicitly_retain_debug_state_for_at_most_24_hours(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)

    result = _deliver(paths, retention_mode="debug", ttl_seconds=3600)

    assert result.status == "ok"
    assert result.cleanup_code == "CLEANUP_RETAINED"
    assert paths["job_root"].exists()
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert state["stage"] == "finalized"
    assert state["history"][-1]["artifact_hashes"] == {
        "final-pdf": sha256_bytes(CANDIDATE_BYTES)
    }


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    old = b"pre-existing user file"
    paths["output"].write_bytes(old)

    result = _deliver(paths)

    assert result.status == "error"
    assert result.code == "OUTPUT_EXISTS"
    assert result.delivered is False
    assert paths["output"].read_bytes() == old
    assert not paths["job_root"].exists()


def test_competing_creator_between_check_and_publish_is_not_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    real_publish = deliver_module._atomic_publish
    competitor = b"race winner"

    def race(staging: Path, output: Path) -> None:
        output.write_bytes(competitor)
        real_publish(staging, output)

    monkeypatch.setattr(deliver_module, "_atomic_publish", race)

    result = _deliver(paths)

    assert result.code == "OUTPUT_EXISTS"
    assert result.delivered is False
    assert paths["output"].read_bytes() == competitor


@pytest.mark.parametrize(
    "conflict", ["source-output", "candidate-output", "source-candidate"]
)
def test_source_candidate_and_final_path_conflicts_fail_closed(
    tmp_path: Path, conflict: str
) -> None:
    paths = _fixture(tmp_path)
    overrides: dict[str, Path] = {}
    if conflict == "source-output":
        overrides["output_path"] = paths["source"]
    elif conflict == "candidate-output":
        overrides["output_path"] = paths["candidate"]
    else:
        overrides["source_path"] = paths["candidate"]

    result = _deliver(paths, **overrides)

    assert result.code == "OUTPUT_PATH_CONFLICT"
    assert result.delivered is False


def test_only_validated_state_may_deliver(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    state["stage"] = "rendered"
    state["history"] = state["history"][:-1]
    paths["state"].write_bytes(canonical_json_bytes(state))

    result = _deliver(paths)

    assert result.code == "DELIVERY_NOT_VALIDATED"
    assert not paths["output"].exists()


@pytest.mark.parametrize("mutation", ["candidate", "manifest", "qa"])
def test_candidate_manifest_qa_and_job_hashes_are_revalidated(
    tmp_path: Path, mutation: str
) -> None:
    paths = _fixture(tmp_path)
    if mutation == "candidate":
        paths["candidate"].write_bytes(CANDIDATE_BYTES + b"tampered")
    elif mutation == "manifest":
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["output_pdf_sha256"] = "f" * 64
        paths["manifest"].write_bytes(canonical_json_bytes(manifest))
    else:
        qa = json.loads(paths["qa"].read_text(encoding="utf-8"))
        qa["output_pdf_sha256"] = "e" * 64
        paths["qa"].write_bytes(canonical_json_bytes(qa))

    result = _deliver(paths)

    assert result.code == "DELIVERY_BINDING_MISMATCH"
    assert result.delivered is False
    assert not paths["output"].exists()


def test_legacy_candidate_pdf_sha256_field_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    candidate_sha256 = sha256_bytes(CANDIDATE_BYTES)
    manifest_sha256 = sha256_bytes(paths["manifest"].read_bytes())
    legacy_qa = {
        "artifact_kind": "qa",
        "candidate_pdf_sha256": candidate_sha256,
        "passed": True,
        "render_manifest_hash": manifest_sha256,
    }
    legacy_qa_bytes = canonical_json_bytes(legacy_qa)
    paths["qa"].write_bytes(legacy_qa_bytes)
    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    state["history"][-1]["artifact_hashes"]["qa"] = sha256_bytes(legacy_qa_bytes)
    paths["state"].write_bytes(canonical_json_bytes(state))

    result = _deliver(paths)

    assert result.code == "DELIVERY_BINDING_MISMATCH"
    assert result.delivered is False
    assert not paths["output"].exists()


def test_failed_qa_never_delivers(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    qa = json.loads(paths["qa"].read_text(encoding="utf-8"))
    qa["passed"] = False
    qa_bytes = canonical_json_bytes(qa)
    paths["qa"].write_bytes(qa_bytes)

    result = _deliver(paths)

    assert result.code in {"DELIVERY_NOT_VALIDATED", "DELIVERY_BINDING_MISMATCH"}
    assert not paths["output"].exists()


def test_noncanonical_manifest_or_qa_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = _deliver(paths)

    assert result.code == "DELIVERY_BINDING_MISMATCH"
    assert not paths["output"].exists()


def test_stage_failure_leaves_no_final_or_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    def fail_copy(*_args, **_kwargs):
        raise OSError("injected staging failure")

    monkeypatch.setattr(deliver_module, "_copy_candidate_to_staging", fail_copy)

    result = _deliver(paths)

    assert result.code == "DELIVERY_STAGE_FAILED"
    assert result.delivered is False
    assert not paths["output"].exists()
    assert list(paths["output"].parent.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows v1 handle semantics")
def test_stream_failure_deletes_only_the_locked_owned_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    digest = sha256_bytes(CANDIDATE_BYTES)
    staging = staging_path_for(paths["output"], "job-001", digest)
    owned_backup = paths["output"].parent / "owned-backup"
    competitor = paths["output"].parent / "must-survive.txt"
    competitor.write_bytes(b"survive")
    real_open = deliver_module._open_staging_exclusive

    class FailingStream:
        def __init__(self, stream) -> None:
            self._stream = stream

        def write(self, data: bytes) -> int:
            self._stream.write(data[:1])
            try:
                staging.rename(owned_backup)
                competitor.rename(staging)
            except OSError:
                pass
            raise OSError("injected streaming failure")

        def __getattr__(self, name: str):
            return getattr(self._stream, name)

    @contextmanager
    def failing_open(path: Path):
        with real_open(path) as stream:
            yield FailingStream(stream)

    monkeypatch.setattr(deliver_module, "_open_staging_exclusive", failing_open)

    result = _deliver(paths)

    assert result.code == "DELIVERY_STAGE_FAILED"
    assert result.delivered is False
    assert competitor.read_bytes() == b"survive"
    assert not staging.exists()
    assert not owned_backup.exists()
    assert not paths["output"].exists()


def test_cancel_during_staging_cleans_the_job_and_never_delivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(deliver_module, "_copy_candidate_to_staging", cancel)

    result = _deliver(paths)

    assert result.code == "DELIVERY_CANCELLED"
    assert result.delivered is False
    assert not paths["output"].exists()
    assert not paths["job_root"].exists()


def test_commit_failure_leaves_no_final_and_removes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    def fail_publish(_staging: Path, _output: Path) -> None:
        raise deliver_module.AtomicPublishError("DELIVERY_COMMIT_FAILED")

    monkeypatch.setattr(deliver_module, "_atomic_publish", fail_publish)

    result = _deliver(paths)

    assert result.code == "DELIVERY_COMMIT_FAILED"
    assert not paths["output"].exists()
    assert list(paths["output"].parent.iterdir()) == []


def test_state_failure_after_publish_reports_the_file_as_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    def fail_state_write(*_args, **_kwargs) -> None:
        raise OSError("injected state write failure")

    monkeypatch.setattr(deliver_module, "write_job_state", fail_state_write)

    result = _deliver(paths)

    assert result.code == "DELIVERY_STATE_FAILED"
    assert result.delivered is True
    assert result.output_sha256 == sha256_bytes(CANDIDATE_BYTES)
    assert paths["output"].read_bytes() == CANDIDATE_BYTES
    assert not paths["job_root"].exists()


def test_cleanup_failure_after_publish_never_hides_delivery_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module
    from academic_pdf_en_zh_reader.job.cleanup import CleanupResult

    def fail_cleanup(*_args, **_kwargs) -> CleanupResult:
        return CleanupResult("CLEANUP_IO_FAILED", False)

    monkeypatch.setattr(deliver_module, "cleanup_after_job", fail_cleanup)

    result = _deliver(paths)

    assert result.code == "CLEANUP_FAILED"
    assert result.delivered is True
    assert result.output_sha256 == sha256_bytes(CANDIDATE_BYTES)
    assert result.cleanup_code == "CLEANUP_IO_FAILED"
    assert paths["output"].read_bytes() == CANDIDATE_BYTES


def test_preexisting_staging_file_is_not_deleted_or_replaced(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    digest = sha256_bytes(CANDIDATE_BYTES)
    staging = staging_path_for(paths["output"], "job-001", digest)
    old = b"unrelated pre-existing staging name"
    staging.write_bytes(old)

    result = _deliver(paths)

    assert result.code == "DELIVERY_STAGE_FAILED"
    assert result.delivered is False
    assert staging.read_bytes() == old
    assert not paths["output"].exists()


def test_delivery_finally_never_unlinks_a_staging_path_swapped_after_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import cleanup as cleanup_module
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    digest = sha256_bytes(CANDIDATE_BYTES)
    staging = staging_path_for(paths["output"], "job-001", digest)
    owned_backup = paths["output"].parent / "owned-backup"
    competitor = paths["output"].parent / "must-survive.txt"
    competitor.write_bytes(b"survive")
    real_check = deliver_module._is_reparse_or_symlink
    real_hash_handle = (
        cleanup_module._hash_windows_handle
        if os.name == "nt"
        else cleanup_module._hash_posix_descriptor
    )

    def publish_without_consuming_staging(source: Path, output: Path) -> None:
        os.link(source, output)

    def legacy_race(path: Path) -> bool:
        if path == staging and paths["output"].exists() and competitor.exists():
            staging.rename(owned_backup)
            competitor.rename(staging)
        return real_check(path)

    def racing_hash_handle(handle: int) -> str:
        try:
            staging.rename(owned_backup)
            competitor.rename(staging)
        except OSError:
            pass
        return real_hash_handle(handle)

    monkeypatch.setattr(
        deliver_module, "_atomic_publish", publish_without_consuming_staging
    )
    monkeypatch.setattr(deliver_module, "_is_reparse_or_symlink", legacy_race)
    monkeypatch.setattr(
        cleanup_module,
        "_hash_windows_handle" if os.name == "nt" else "_hash_posix_descriptor",
        racing_hash_handle,
    )

    result = _deliver(paths)

    assert result.delivered is True
    assert paths["output"].read_bytes() == CANDIDATE_BYTES
    if os.name == "nt":
        assert result.status == "ok"
    else:
        assert result.status == "error"
        assert result.code == "CLEANUP_FAILED"
    surviving_path = competitor if competitor.exists() else staging
    assert surviving_path.read_bytes() == b"survive"


def test_delivery_requires_the_managed_job_sentinel(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job.cleanup import SENTINEL_NAME

    (paths["job_root"] / SENTINEL_NAME).unlink()

    result = _deliver(paths, retention_mode="debug", ttl_seconds=60)

    assert result.code == "DELIVERY_BINDING_MISMATCH"
    assert not paths["output"].exists()


def test_output_directory_and_artifacts_must_be_safe_real_paths(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    missing_parent = tmp_path / "missing" / paths["output"].name

    result = _deliver(paths, output_path=missing_parent)

    assert result.code == "OUTPUT_PATH_UNSAFE"
    assert not missing_parent.exists()


def test_delivery_intent_is_minimal_and_private_during_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    from academic_pdf_en_zh_reader.job import deliver as deliver_module

    observed: dict[str, object] = {}
    real_copy = deliver_module._copy_candidate_to_staging

    def inspect_intent(candidate: Path, staging: Path, expected_hash: str) -> None:
        intent = json.loads(
            (paths["job_root"] / deliver_module.DELIVERY_INTENT_NAME).read_text(
                encoding="utf-8"
            )
        )
        observed.update(intent)
        real_copy(candidate, staging, expected_hash)

    monkeypatch.setattr(deliver_module, "_copy_candidate_to_staging", inspect_intent)

    result = _deliver(paths, retention_mode="debug", ttl_seconds=60)

    assert result.status == "ok"
    assert set(observed) == {"candidate_sha256", "job_id", "target"}
    assert observed["job_id"] == "job-001"
    assert observed["candidate_sha256"] == sha256_bytes(CANDIDATE_BYTES)
    assert observed["target"] == str(paths["output"].resolve())


def test_binding_errors_do_not_echo_document_bytes(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    secret = "SECRET_PAPER_SENTENCE"
    paths["candidate"].write_bytes(CANDIDATE_BYTES + secret.encode())

    result = _deliver(paths, retention_mode="debug", ttl_seconds=60)

    rendered = repr(result)
    assert secret not in rendered
    assert paths["source"].name not in rendered
