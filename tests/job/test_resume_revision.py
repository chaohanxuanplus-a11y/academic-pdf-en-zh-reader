# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    JobStateError,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    ConcurrentStateError,
    create_verified_rerun,
    load_job_state,
    write_immutable_artifact,
    write_immutable_bytes,
    write_job_state,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
NORMALIZED_PDF_BYTES = b"%PDF-1.7\n%%EOF\n"
NORMALIZED_PDF_SHA = hashlib.sha256(NORMALIZED_PDF_BYTES).hexdigest()


def _advance(state, target: JobStage, artifact_hashes: dict[str, str]):
    return advance_job(
        state,
        target,
        artifact_hashes,
        expected_previous_state_hash=state_hash(state),
    )


def _extracted_job(
    *,
    preflight_hash: str = "1" * 64,
    normalization_hash: str = "4" * 64,
    normalized_pdf_hash: str = NORMALIZED_PDF_SHA,
    source_hash: str = "2" * 64,
    units_hash: str = "3" * 64,
):
    state = create_job(job_id="job-001", source_sha256=SHA_A, translation_revision=2)
    state = _advance(
        state,
        JobStage.PREFLIGHTED,
        {
            "preflight": preflight_hash,
            "normalization": normalization_hash,
            "normalized-pdf": normalized_pdf_hash,
        },
    )
    return _advance(
        state,
        JobStage.EXTRACTED,
        {"source": source_hash, "units": units_hash},
    )


def _preflight(*, passed: bool = True, source_sha256: str = SHA_A):
    if passed:
        return {
            "schema_version": "1.0.0",
            "artifact_kind": "preflight",
            "source_sha256": source_sha256,
            "passed": True,
            "checks": [{"id": "page-tree", "hard_gate": True, "passed": True}],
            "pages": [
                {
                    "page_number": 1,
                    "width_mpt": 595_276,
                    "height_mpt": 841_890,
                    "media_box_mpt": [0, 0, 595_276, 841_890],
                    "crop_box_mpt": [0, 0, 595_276, 841_890],
                    "rotation_degrees": 0,
                    "extractable_character_count": 1200,
                }
            ],
        }
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "preflight",
        "source_sha256": source_sha256,
        "passed": False,
        "error_codes": ["TEXT_LAYER_MISSING"],
        "checks": [
            {
                "id": "text-layer",
                "hard_gate": True,
                "passed": False,
                "error_code": "TEXT_LAYER_MISSING",
            }
        ],
        "pages": [],
    }


def _normalization(
    *,
    preflight_sha256: str,
    source_sha256: str = SHA_A,
    normalized_pdf_sha256: str = NORMALIZED_PDF_SHA,
):
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": "1.0.0",
        "source_sha256": source_sha256,
        "preflight_sha256": preflight_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "normalized_pdf_bytes": len(NORMALIZED_PDF_BYTES),
        "pages": [
            {
                "page_number": 1,
                "source_media_box_mpt": [0, 0, 595_276, 841_890],
                "source_crop_box_mpt": [0, 0, 595_276, 841_890],
                "source_rotation_degrees": 0,
                "displayed_width_mpt": 595_276,
                "displayed_height_mpt": 841_890,
                "scale_ppm": 1_000_000,
                "scaled_width_mpt": 595_276,
                "scaled_height_mpt": 841_890,
                "padding_left_mpt": 0,
                "padding_bottom_mpt": 0,
                "padding_right_mpt": 0,
                "padding_top_mpt": 0,
                "normalized_content_box_mpt": [0, 0, 595_276, 841_890],
            }
        ],
    }


def _source(
    *,
    source_sha256: str = SHA_A,
    normalized_pdf_sha256: str = NORMALIZED_PDF_SHA,
):
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "p0001-band-0001",
                        "y_top_mpt": 841_890,
                        "y_bottom_mpt": 0,
                        "columns": [
                            {
                                "id": "p0001-column-0001",
                                "x_left_mpt": 0,
                                "x_right_mpt": 595_276,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": [],
            }
        ],
    }


def _units(
    *,
    source_sha256: str = SHA_A,
    normalized_pdf_sha256: str = NORMALIZED_PDF_SHA,
):
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "units": [],
    }


def _write_reusable_set(
    tmp_path,
    *,
    preflight=None,
    normalization=None,
    source=None,
    units=None,
):
    preflight_path = tmp_path / "preflight.json"
    normalization_path = tmp_path / "normalization.json"
    normalized_pdf_path = tmp_path / "normalized-source.pdf"
    source_path = tmp_path / "source.json"
    units_path = tmp_path / "units.json"
    preflight_hash = write_immutable_artifact(
        preflight_path, preflight or _preflight(), "preflight"
    )
    normalized_pdf_hash = write_immutable_bytes(
        normalized_pdf_path, NORMALIZED_PDF_BYTES
    )
    normalization_hash = write_immutable_artifact(
        normalization_path,
        normalization or _normalization(preflight_sha256=preflight_hash),
        "normalization",
    )
    hashes = {
        "preflight": preflight_hash,
        "normalization": normalization_hash,
        "normalized-pdf": normalized_pdf_hash,
        "source": write_immutable_artifact(source_path, source or _source(), "source"),
        "units": write_immutable_artifact(units_path, units or _units(), "units"),
    }
    state = _extracted_job(
        preflight_hash=hashes["preflight"],
        normalization_hash=hashes["normalization"],
        normalized_pdf_hash=hashes["normalized-pdf"],
        source_hash=hashes["source"],
        units_hash=hashes["units"],
    )
    return (
        state,
        preflight_path,
        normalization_path,
        normalized_pdf_path,
        source_path,
        units_path,
    )


def _create_rerun(
    state,
    preflight_path,
    normalization_path,
    normalized_pdf_path,
    source_path,
    units_path,
    *,
    new_job_id,
):
    return create_verified_rerun(
        state=state,
        preflight_path=preflight_path,
        normalization_path=normalization_path,
        normalized_pdf_path=normalized_pdf_path,
        source_path=source_path,
        units_path=units_path,
        new_job_id=new_job_id,
    )


def _crash_holding_lock(destination: str, ready) -> None:
    from academic_pdf_en_zh_reader.job.storage import _exclusive_lock

    with _exclusive_lock(Path(destination)):
        ready.set()
        os._exit(23)


def test_new_rerun_gets_new_identity_and_reuses_only_verified_extraction(
    tmp_path,
) -> None:
    previous, *paths = _write_reusable_set(tmp_path)
    previous = _advance(previous, JobStage.TRANSLATED, {"translation": "4" * 64})
    rerun = _create_rerun(
        previous,
        *paths,
        new_job_id="job-002",
    )

    assert rerun.job_id == "job-002"
    assert rerun.translation_revision == 3
    assert rerun.stage is JobStage.EXTRACTED
    assert rerun.reused_from_job_id == "job-001"
    assert [record.stage for record in rerun.history] == [
        JobStage.INITIALIZED,
        JobStage.PREFLIGHTED,
        JobStage.EXTRACTED,
    ]
    assert set(rerun.artifact_hashes) == {
        "preflight",
        "normalization",
        "normalized-pdf",
        "source",
        "units",
    }
    assert "translation" not in rerun.artifact_hashes
    assert all(record.reused for record in rerun.history[1:])


def test_rerun_rejects_same_job_id_and_unverified_inputs(tmp_path) -> None:
    previous, *paths = _write_reusable_set(tmp_path)
    with pytest.raises(JobStateError, match="new job_id"):
        _create_rerun(
            previous,
            *paths,
            new_job_id=previous.job_id,
        )

    initialized = create_job(
        job_id="job-empty", source_sha256=SHA_A, translation_revision=1
    )
    with pytest.raises(JobStateError, match="extracted"):
        _create_rerun(
            initialized,
            *paths,
            new_job_id="job-002",
        )


def test_reuse_verification_rejects_tamper_fake_hash_and_bad_schema(tmp_path) -> None:
    tamper_dir = tmp_path / "tamper"
    state, preflight_path, *paths = _write_reusable_set(tamper_dir)
    preflight_path.write_bytes(preflight_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="canonical|hash"):
        _create_rerun(
            state,
            preflight_path,
            *paths,
            new_job_id="job-002",
        )

    fake_dir = tmp_path / "fake"
    _, *paths = _write_reusable_set(fake_dir)
    fake_state = _extracted_job()
    with pytest.raises(ValueError, match="hash"):
        _create_rerun(
            fake_state,
            *paths,
            new_job_id="job-002",
        )

    schema_dir = tmp_path / "schema"
    state, preflight_path, *paths = _write_reusable_set(schema_dir)
    bad_bytes = b'{"artifact_kind":"preflight","schema_version":"1.0.0"}'
    preflight_path.write_bytes(bad_bytes)
    bad_state = _extracted_job(
        preflight_hash=hashlib.sha256(bad_bytes).hexdigest(),
        source_hash=state.artifact_hashes["source"],
        units_hash=state.artifact_hashes["units"],
    )
    with pytest.raises(ValueError, match="preflight"):
        _create_rerun(
            bad_state,
            preflight_path,
            *paths,
            new_job_id="job-002",
        )


def test_reuse_verification_rejects_source_mismatch_and_failed_preflight(
    tmp_path,
) -> None:
    mismatch_dir = tmp_path / "mismatch"
    state, *paths = _write_reusable_set(
        mismatch_dir, source=_source(source_sha256=SHA_B)
    )
    with pytest.raises(ValueError, match="source_sha256"):
        _create_rerun(
            state,
            *paths,
            new_job_id="job-002",
        )

    failed_dir = tmp_path / "failed"
    state, *paths = _write_reusable_set(failed_dir, preflight=_preflight(passed=False))
    with pytest.raises(ValueError, match="passed"):
        _create_rerun(
            state,
            *paths,
            new_job_id="job-002",
        )


def test_artifact_write_is_validated_atomic_and_immutable(tmp_path) -> None:
    artifact_path = tmp_path / "preflight.json"
    artifact = _preflight()

    digest = write_immutable_artifact(artifact_path, artifact, "preflight")
    original_bytes = artifact_path.read_bytes()
    assert len(digest) == 64
    assert json.loads(original_bytes)["artifact_kind"] == "preflight"

    with pytest.raises(ArtifactExistsError):
        write_immutable_artifact(artifact_path, artifact, "preflight")
    assert artifact_path.read_bytes() == original_bytes

    invalid_path = tmp_path / "invalid.json"
    invalid = {**artifact, "passed": "yes"}
    with pytest.raises(ValueError):
        write_immutable_artifact(invalid_path, invalid, "preflight")
    assert not invalid_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_job_state_write_uses_compare_and_swap_and_resumes(tmp_path) -> None:
    state_path = tmp_path / "job-state.json"
    initialized = create_job(
        job_id="job-001", source_sha256=SHA_A, translation_revision=1
    )
    write_job_state(state_path, initialized)
    persisted_hash = state_hash(initialized)

    preflighted = _advance(
        initialized,
        JobStage.PREFLIGHTED,
        {
            "preflight": "1" * 64,
            "normalization": "4" * 64,
            "normalized-pdf": SHA_B,
        },
    )
    write_job_state(
        state_path,
        preflighted,
        expected_previous_state_hash=persisted_hash,
    )
    assert load_job_state(state_path) == preflighted

    extracted = _advance(
        preflighted,
        JobStage.EXTRACTED,
        {"source": "2" * 64, "units": "3" * 64},
    )
    with pytest.raises(ConcurrentStateError, match="hash"):
        write_job_state(
            state_path,
            extracted,
            expected_previous_state_hash="0" * 64,
        )
    assert load_job_state(state_path) == preflighted


def test_kernel_lock_is_released_when_the_owner_process_crashes(tmp_path) -> None:
    destination = tmp_path / "after-crash.json"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_crash_holding_lock,
        args=(str(destination), ready),
    )
    process.start()
    try:
        assert ready.wait(10), "child did not acquire the file lock"
        process.join(10)
        assert not process.is_alive(), "crashing child did not exit"
        assert process.exitcode == 23
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)

    digest = write_immutable_artifact(destination, _preflight(), "preflight")
    assert len(digest) == 64
