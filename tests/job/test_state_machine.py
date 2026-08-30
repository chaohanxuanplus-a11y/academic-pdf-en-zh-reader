# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    JobStateError,
    _build_rerun,
    advance_job,
    create_job,
    state_hash,
)

SHA_A = "a" * 64

STAGE_ARTIFACTS = {
    JobStage.PREFLIGHTED: {
        "preflight": "1" * 64,
        "normalization": "e" * 64,
        "normalized-pdf": "f" * 64,
    },
    JobStage.EXTRACTED: {"source": "2" * 64, "units": "3" * 64},
    JobStage.TRANSLATED: {"translation": "4" * 64},
    JobStage.INDEPENDENTLY_REVIEWED: {"review": "5" * 64},
    JobStage.ANNOTATED: {"annotations": "6" * 64},
    JobStage.LAID_OUT: {
        "frame-graph": "7" * 64,
        "layout": "8" * 64,
        "finalization-receipt": "e" * 64,
    },
    JobStage.RENDERED: {"render-manifest": "9" * 64, "pdf": "a" * 64},
    JobStage.VALIDATED: {"qa": "b" * 64, "provenance": "c" * 64},
    JobStage.FINALIZED: {"final-pdf": "d" * 64},
}


def _advance(state, target: JobStage):
    return advance_job(
        state,
        target,
        STAGE_ARTIFACTS[target],
        expected_previous_state_hash=state_hash(state),
    )


def test_state_advances_once_through_the_only_legal_order() -> None:
    state = create_job(job_id="job-001", source_sha256=SHA_A, translation_revision=1)
    assert state.stage is JobStage.INITIALIZED

    for target in list(JobStage)[1:]:
        previous_hash = state_hash(state)
        state = _advance(state, target)
        assert state.stage is target
        assert state.history[-1].previous_state_hash == previous_hash

    assert [record.stage for record in state.history] == list(JobStage)


def test_state_rejects_skip_repeat_wrong_hash_and_wrong_artifact_set() -> None:
    state = create_job(job_id="job-001", source_sha256=SHA_A, translation_revision=1)

    with pytest.raises(JobStateError, match="next stage"):
        _advance(state, JobStage.EXTRACTED)

    state = _advance(state, JobStage.PREFLIGHTED)
    with pytest.raises(JobStateError, match="next stage"):
        _advance(state, JobStage.PREFLIGHTED)

    with pytest.raises(JobStateError, match="hash"):
        advance_job(
            state,
            JobStage.EXTRACTED,
            STAGE_ARTIFACTS[JobStage.EXTRACTED],
            expected_previous_state_hash="0" * 64,
        )

    with pytest.raises(JobStateError, match="artifact"):
        advance_job(
            state,
            JobStage.EXTRACTED,
            {"source": "2" * 64},
            expected_previous_state_hash=state_hash(state),
        )


@pytest.mark.parametrize(
    "artifacts",
    [
        {"preflight": "1" * 64},
        {"preflight": "1" * 64, "normalization": "e" * 64},
        {"preflight": "1" * 64, "normalized-pdf": "f" * 64},
        {
            "preflight": "1" * 64,
            "normalization": "e" * 64,
            "normalized-pdf": "f" * 64,
            "unexpected": "0" * 64,
        },
    ],
)
def test_preflighted_stage_requires_the_complete_normalized_source_binding(
    artifacts: dict[str, str],
) -> None:
    state = create_job(job_id="job-001", source_sha256=SHA_A, translation_revision=1)

    with pytest.raises(JobStateError, match="artifact"):
        advance_job(
            state,
            JobStage.PREFLIGHTED,
            artifacts,
            expected_previous_state_hash=state_hash(state),
        )


def test_rerun_reuses_the_complete_normalized_extraction_chain() -> None:
    artifact_hashes = {
        **STAGE_ARTIFACTS[JobStage.PREFLIGHTED],
        **STAGE_ARTIFACTS[JobStage.EXTRACTED],
    }

    rerun = _build_rerun(
        previous_job_id="job-001",
        source_sha256=SHA_A,
        translation_revision=1,
        artifact_hashes=artifact_hashes,
        new_job_id="job-002",
    )

    assert rerun.stage is JobStage.EXTRACTED
    assert rerun.reused_from_job_id == "job-001"
    assert rerun.artifact_hashes == artifact_hashes
    assert all(record.reused for record in rerun.history[1:])

    incomplete = dict(artifact_hashes)
    incomplete.pop("normalized-pdf")
    with pytest.raises(JobStateError, match="verified extraction artifacts"):
        _build_rerun(
            previous_job_id="job-001",
            source_sha256=SHA_A,
            translation_revision=1,
            artifact_hashes=incomplete,
            new_job_id="job-003",
        )


def test_laid_out_stage_requires_the_finalization_receipt() -> None:
    state = create_job(job_id="job-001", source_sha256=SHA_A, translation_revision=1)
    for stage in (
        JobStage.PREFLIGHTED,
        JobStage.EXTRACTED,
        JobStage.TRANSLATED,
        JobStage.INDEPENDENTLY_REVIEWED,
        JobStage.ANNOTATED,
    ):
        state = _advance(state, stage)

    with pytest.raises(JobStateError, match="artifact"):
        advance_job(
            state,
            JobStage.LAID_OUT,
            {"frame-graph": "7" * 64, "layout": "8" * 64},
            expected_previous_state_hash=state_hash(state),
        )

    laid_out = _advance(state, JobStage.LAID_OUT)
    legacy = deepcopy(laid_out.to_dict())
    legacy["history"][-1]["artifact_hashes"].pop("finalization-receipt")
    with pytest.raises(JobStateError, match="artifact"):
        type(laid_out).from_dict(legacy)


def test_state_and_history_are_not_mutated_by_advance() -> None:
    original = create_job(job_id="job-001", source_sha256=SHA_A, translation_revision=1)
    advanced = _advance(original, JobStage.PREFLIGHTED)
    assert original.stage is JobStage.INITIALIZED
    assert len(original.history) == 1
    assert len(advanced.history) == 2


@pytest.mark.parametrize("revision", [True, 1.5, "1"])
def test_revision_must_be_an_integer(revision: object) -> None:
    with pytest.raises(JobStateError, match="positive integer"):
        create_job(
            job_id="job-001",
            source_sha256=SHA_A,
            translation_revision=revision,
        )
