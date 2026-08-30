# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""A strict, immutable, forward-only job state machine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class JobStateError(ValueError):
    """Raised when a job transition would violate the state contract."""


class JobStage(StrEnum):
    INITIALIZED = "initialized"
    PREFLIGHTED = "preflighted"
    EXTRACTED = "extracted"
    TRANSLATED = "translated"
    INDEPENDENTLY_REVIEWED = "independently_reviewed"
    ANNOTATED = "annotated"
    LAID_OUT = "laid_out"
    RENDERED = "rendered"
    VALIDATED = "validated"
    FINALIZED = "finalized"


def _build_rerun(
    *,
    previous_job_id: str,
    source_sha256: str,
    translation_revision: int,
    artifact_hashes: dict[str, str],
    new_job_id: str,
) -> JobState:
    """Build rerun state after the storage layer has verified reusable bytes."""

    if new_job_id == previous_job_id:
        raise JobStateError("a rerun requires a new job_id")
    if set(artifact_hashes) != {
        "preflight",
        "normalization",
        "normalized-pdf",
        "source",
        "units",
    }:
        raise JobStateError("rerun requires exactly the verified extraction artifacts")

    state = create_job(
        job_id=new_job_id,
        source_sha256=source_sha256,
        translation_revision=translation_revision + 1,
    )
    state = JobState(
        job_id=state.job_id,
        source_sha256=state.source_sha256,
        translation_revision=state.translation_revision,
        stage=state.stage,
        history=state.history,
        reused_from_job_id=previous_job_id,
    )
    state = _advance(
        state,
        JobStage.PREFLIGHTED,
        {
            "preflight": artifact_hashes["preflight"],
            "normalization": artifact_hashes["normalization"],
            "normalized-pdf": artifact_hashes["normalized-pdf"],
        },
        expected_previous_state_hash=state_hash(state),
        reused=True,
    )
    return _advance(
        state,
        JobStage.EXTRACTED,
        {"source": artifact_hashes["source"], "units": artifact_hashes["units"]},
        expected_previous_state_hash=state_hash(state),
        reused=True,
    )


_REQUIRED_ARTIFACTS = {
    JobStage.PREFLIGHTED: frozenset({"preflight", "normalization", "normalized-pdf"}),
    JobStage.EXTRACTED: frozenset({"source", "units"}),
    JobStage.TRANSLATED: frozenset({"translation"}),
    JobStage.INDEPENDENTLY_REVIEWED: frozenset({"review"}),
    JobStage.ANNOTATED: frozenset({"annotations"}),
    JobStage.LAID_OUT: frozenset({"frame-graph", "layout", "finalization-receipt"}),
    JobStage.RENDERED: frozenset({"render-manifest", "pdf"}),
    JobStage.VALIDATED: frozenset({"qa", "provenance"}),
    JobStage.FINALIZED: frozenset({"final-pdf"}),
}


@dataclass(frozen=True, slots=True)
class StageRecord:
    stage: JobStage
    previous_state_hash: str | None
    artifacts: tuple[tuple[str, str], ...]
    reused: bool = False

    @property
    def artifact_hashes(self) -> dict[str, str]:
        return dict(self.artifacts)

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "previous_state_hash": self.previous_state_hash,
            "artifact_hashes": dict(self.artifacts),
            "reused": self.reused,
        }


@dataclass(frozen=True, slots=True)
class JobState:
    job_id: str
    source_sha256: str
    translation_revision: int
    stage: JobStage
    history: tuple[StageRecord, ...]
    reused_from_job_id: str | None = None

    @property
    def artifact_hashes(self) -> dict[str, str]:
        flattened: dict[str, str] = {}
        for record in self.history:
            flattened.update(record.artifacts)
        return flattened

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_kind": "job-state",
            "job_id": self.job_id,
            "source_sha256": self.source_sha256,
            "translation_revision": self.translation_revision,
            "stage": self.stage.value,
            "reused_from_job_id": self.reused_from_job_id,
            "history": [record.to_dict() for record in self.history],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> JobState:
        history = tuple(
            StageRecord(
                stage=JobStage(record["stage"]),
                previous_state_hash=record["previous_state_hash"],
                artifacts=tuple(sorted(record["artifact_hashes"].items())),
                reused=record["reused"],
            )
            for record in value["history"]
        )
        state = cls(
            job_id=value["job_id"],
            source_sha256=value["source_sha256"],
            translation_revision=value["translation_revision"],
            stage=JobStage(value["stage"]),
            history=history,
            reused_from_job_id=value["reused_from_job_id"],
        )
        _validate_history(state)
        return state


def _validate_hash(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise JobStateError(f"{field} must be a lowercase SHA-256 digest")


def _normalize_artifacts(
    stage: JobStage, artifact_hashes: dict[str, str]
) -> tuple[tuple[str, str], ...]:
    required = _REQUIRED_ARTIFACTS[stage]
    if set(artifact_hashes) != required:
        raise JobStateError(
            f"artifact set for {stage.value} must be exactly {sorted(required)}"
        )
    for name, digest in artifact_hashes.items():
        _validate_hash(digest, field=f"artifact {name!r}")
    return tuple(sorted(artifact_hashes.items()))


def _validate_history(state: JobState) -> None:
    if not state.job_id:
        raise JobStateError("job_id must be a non-empty string")
    _validate_hash(state.source_sha256, field="source_sha256")
    if (
        isinstance(state.translation_revision, bool)
        or not isinstance(state.translation_revision, int)
        or state.translation_revision < 1
    ):
        raise JobStateError("translation_revision must be a positive integer")
    if not state.history or state.history[0].stage is not JobStage.INITIALIZED:
        raise JobStateError("history must start at initialized")
    expected_stages = tuple(JobStage)[: len(state.history)]
    if tuple(record.stage for record in state.history) != expected_stages:
        raise JobStateError("history contains a skipped or repeated stage")
    if state.history[-1].stage is not state.stage:
        raise JobStateError("current stage does not match history")
    initial = state.history[0]
    if initial.previous_state_hash is not None or initial.artifacts or initial.reused:
        raise JobStateError("initialized history record is invalid")

    for index, record in enumerate(state.history[1:], start=1):
        normalized = _normalize_artifacts(record.stage, record.artifact_hashes)
        if record.artifacts != normalized:
            raise JobStateError("history artifact hashes are not canonical")
        prior = JobState(
            job_id=state.job_id,
            source_sha256=state.source_sha256,
            translation_revision=state.translation_revision,
            stage=state.history[index - 1].stage,
            history=state.history[:index],
            reused_from_job_id=state.reused_from_job_id,
        )
        if record.previous_state_hash != state_hash(prior):
            raise JobStateError("history previous state hash does not match")

    reused_records = [record for record in state.history if record.reused]
    if state.reused_from_job_id is None and reused_records:
        raise JobStateError("reused history requires reused_from_job_id")
    if state.reused_from_job_id is not None:
        if state.reused_from_job_id == state.job_id:
            raise JobStateError("reused_from_job_id must differ from job_id")
        allowed = {JobStage.PREFLIGHTED, JobStage.EXTRACTED}
        stages_present = {record.stage for record in state.history}
        expected_reused = allowed & stages_present
        if {record.stage for record in reused_records} != expected_reused:
            raise JobStateError("rerun must reuse exactly preflighted and extracted")


def create_job(
    *, job_id: str, source_sha256: str, translation_revision: int
) -> JobState:
    """Create a new initialized job with explicit identity and revision."""

    if not job_id or not isinstance(job_id, str):
        raise JobStateError("job_id must be a non-empty string")
    _validate_hash(source_sha256, field="source_sha256")
    if (
        isinstance(translation_revision, bool)
        or not isinstance(translation_revision, int)
        or translation_revision < 1
    ):
        raise JobStateError("translation_revision must be a positive integer")
    initial = StageRecord(
        stage=JobStage.INITIALIZED,
        previous_state_hash=None,
        artifacts=(),
    )
    return JobState(
        job_id=job_id,
        source_sha256=source_sha256,
        translation_revision=translation_revision,
        stage=JobStage.INITIALIZED,
        history=(initial,),
    )


def state_hash(state: JobState) -> str:
    """Hash a complete immutable state snapshot."""

    return sha256_canonical(state.to_dict())


def _advance(
    state: JobState,
    target_stage: JobStage,
    artifact_hashes: dict[str, str],
    *,
    expected_previous_state_hash: str,
    reused: bool,
) -> JobState:
    _validate_history(state)
    actual_previous_hash = state_hash(state)
    if expected_previous_state_hash != actual_previous_hash:
        raise JobStateError("previous state hash does not match")
    stages = list(JobStage)
    current_index = stages.index(state.stage)
    if (
        current_index + 1 >= len(stages)
        or stages[current_index + 1] is not target_stage
    ):
        raise JobStateError("target must be the single next stage")
    artifacts = _normalize_artifacts(target_stage, artifact_hashes)
    record = StageRecord(
        stage=target_stage,
        previous_state_hash=actual_previous_hash,
        artifacts=artifacts,
        reused=reused,
    )
    return JobState(
        job_id=state.job_id,
        source_sha256=state.source_sha256,
        translation_revision=state.translation_revision,
        stage=target_stage,
        history=(*state.history, record),
        reused_from_job_id=state.reused_from_job_id,
    )


def advance_job(
    state: JobState,
    target_stage: JobStage,
    artifact_hashes: dict[str, str],
    *,
    expected_previous_state_hash: str,
) -> JobState:
    """Advance exactly one stage after compare-and-swap hash verification."""

    return _advance(
        state,
        target_stage,
        artifact_hashes,
        expected_previous_state_hash=expected_previous_state_hash,
        reused=False,
    )
