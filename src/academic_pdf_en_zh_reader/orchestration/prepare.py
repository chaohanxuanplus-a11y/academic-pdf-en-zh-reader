# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Create one managed job and freeze its validated extraction artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from academic_pdf_en_zh_reader.extraction.api import (
    _expected_normalized_pages,
    _read_valid_normalization,
    _read_valid_preflight,
    _validate_extraction_artifact,
    extract_untrusted_pdf,
)
from academic_pdf_en_zh_reader.extraction.unit_merge import (
    UnitMergeStatus,
    build_semantic_units,
)
from academic_pdf_en_zh_reader.job.cleanup import (
    cleanup_after_job,
    create_managed_job,
)
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    write_immutable_artifact,
    write_job_state,
)
from academic_pdf_en_zh_reader.normalization.api import (
    NORMALIZED_PDF_NAME,
    normalize_untrusted_pdf,
)
from academic_pdf_en_zh_reader.preflight.api import preflight_untrusted_pdf
from academic_pdf_en_zh_reader.security.input_copy import read_bounded_regular_file
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.topology.confidence import build_topology
from academic_pdf_en_zh_reader.topology.contracts import TopologyStatus

_STABLE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class PrepareJobError(RuntimeError):
    """A content-free, stable reason why managed preparation stopped."""

    def __init__(self, code: str, stage: str, *, job_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.job_id = job_id


def _outcome_hash(
    outcome: Mapping[str, object],
    *,
    artifact_name: str,
    failure_code: str,
    stage: str,
    job_id: str,
) -> str:
    if outcome.get("status") != "ok":
        error = outcome.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        stable_code = (
            code
            if isinstance(code, str) and _STABLE_CODE.fullmatch(code)
            else failure_code
        )
        raise PrepareJobError(stable_code, stage, job_id=job_id)
    artifact = outcome.get("artifact")
    digest = artifact.get("sha256") if isinstance(artifact, Mapping) else None
    name = artifact.get("name") if isinstance(artifact, Mapping) else None
    if (
        name != artifact_name
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise PrepareJobError(failure_code, stage, job_id=job_id)
    return digest


def _normalization_hashes(
    outcome: Mapping[str, object], *, stage: str, job_id: str
) -> tuple[str, str]:
    if outcome.get("status") != "ok":
        error = outcome.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        stable_code = (
            code
            if isinstance(code, str) and _STABLE_CODE.fullmatch(code)
            else "NORMALIZATION_FAILED"
        )
        raise PrepareJobError(stable_code, stage, job_id=job_id)
    artifacts = outcome.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "normalization",
        "normalized-pdf",
    }:
        raise PrepareJobError("NORMALIZATION_ARTIFACT_INVALID", stage, job_id=job_id)

    def digest(name: str) -> str:
        record = artifacts.get(name)
        value = record.get("sha256") if isinstance(record, Mapping) else None
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise PrepareJobError(
                "NORMALIZATION_ARTIFACT_INVALID", stage, job_id=job_id
            )
        return value

    return digest("normalization"), digest("normalized-pdf")


def _read_bound_extraction(
    job_root: Path,
    *,
    limits: WorkerLimits,
    expected_preflight_hash: str,
    expected_normalization_hash: str,
    expected_normalized_pdf_hash: str,
    expected_extraction_hash: str,
    job_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        preflight, _preflight_bytes, preflight_hash = _read_valid_preflight(
            job_root,
            limits=limits,
        )
        if preflight_hash != expected_preflight_hash:
            raise ValueError("preflight hash differs")
    except Exception as exc:
        raise PrepareJobError(
            "PREFLIGHT_ARTIFACT_INVALID",
            "freeze",
            job_id=job_id,
        ) from exc

    try:
        normalization, _normalization_bytes, normalization_hash = (
            _read_valid_normalization(
                job_root,
                preflight=preflight,
                preflight_sha256=preflight_hash,
                limits=limits,
            )
        )
        normalized_pdf = read_bounded_regular_file(
            job_root / NORMALIZED_PDF_NAME,
            max_bytes=limits.max_normalized_pdf_bytes,
        )
        if (
            normalization_hash != expected_normalization_hash
            or normalized_pdf.sha256 != expected_normalized_pdf_hash
            or normalization["normalized_pdf_sha256"] != normalized_pdf.sha256
            or normalization["normalized_pdf_bytes"] != len(normalized_pdf.data)
        ):
            raise ValueError("normalization identity differs")
    except Exception as exc:
        raise PrepareJobError(
            "NORMALIZATION_ARTIFACT_INVALID",
            "freeze",
            job_id=job_id,
        ) from exc

    try:
        bounded = read_bounded_regular_file(
            job_root / "extraction.json",
            max_bytes=limits.max_extraction_artifact_bytes,
        )
        if bounded.sha256 != expected_extraction_hash:
            raise ValueError("extraction hash differs")
        extraction = _validate_extraction_artifact(
            bounded.data,
            source_sha256=preflight["source_sha256"],
            normalized_pdf_sha256=normalized_pdf.sha256,
            preflight_sha256=preflight_hash,
            normalization_sha256=normalization_hash,
            expected_pages=_expected_normalized_pages(preflight, normalization),
            limits=limits,
        )
    except Exception as exc:
        raise PrepareJobError(
            "EXTRACTION_ARTIFACT_INVALID",
            "freeze",
            job_id=job_id,
        ) from exc
    return preflight, extraction


def _freeze_extracted_state(
    job_root: Path,
    *,
    job_id: str,
    source_sha256: str,
    preflight_hash: str,
    normalization_hash: str,
    normalized_pdf_hash: str,
    source_hash: str,
    units_hash: str,
) -> str:
    state_path = job_root / "job-state.json"
    initialized = create_job(
        job_id=job_id,
        source_sha256=source_sha256,
        translation_revision=1,
    )
    initialized_hash = write_job_state(state_path, initialized)
    if initialized_hash != state_hash(initialized):
        raise ValueError("initialized state hash differs")

    preflighted = advance_job(
        initialized,
        JobStage.PREFLIGHTED,
        {
            "preflight": preflight_hash,
            "normalization": normalization_hash,
            "normalized-pdf": normalized_pdf_hash,
        },
        expected_previous_state_hash=initialized_hash,
    )
    preflighted_hash = write_job_state(
        state_path,
        preflighted,
        expected_previous_state_hash=initialized_hash,
    )
    if preflighted_hash != state_hash(preflighted):
        raise ValueError("preflighted state hash differs")

    extracted = advance_job(
        preflighted,
        JobStage.EXTRACTED,
        {"source": source_hash, "units": units_hash},
        expected_previous_state_hash=preflighted_hash,
    )
    extracted_hash = write_job_state(
        state_path,
        extracted,
        expected_previous_state_hash=preflighted_hash,
    )
    if extracted_hash != state_hash(extracted):
        raise ValueError("extracted state hash differs")
    return extracted_hash


def prepare_managed_job(
    *,
    managed_root: str | Path,
    job_id: str,
    source_pdf: str | Path,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    """Create and freeze one managed job through the real extraction pipeline."""

    job_root: Path | None = None
    stage = "create"
    cleanup_outcome = "failure"
    try:
        try:
            job_root = create_managed_job(managed_root, job_id)
        except FileExistsError as exc:
            raise PrepareJobError("JOB_EXISTS", stage, job_id=job_id) from exc
        except ValueError as exc:
            raise PrepareJobError("INPUT_REJECTED", stage) from exc
        except OSError as exc:
            raise PrepareJobError("JOB_CREATE_FAILED", stage) from exc

        stage = "preflight"
        preflight_outcome = preflight_untrusted_pdf(source_pdf, job_root, limits=limits)
        preflight_hash = _outcome_hash(
            preflight_outcome,
            artifact_name="preflight.json",
            failure_code="PREFLIGHT_ARTIFACT_INVALID",
            stage=stage,
            job_id=job_id,
        )

        stage = "normalization"
        normalization_outcome = normalize_untrusted_pdf(
            source_pdf, job_root, limits=limits
        )
        normalization_hash, normalized_pdf_hash = _normalization_hashes(
            normalization_outcome,
            stage=stage,
            job_id=job_id,
        )

        stage = "extraction"
        extraction_outcome = extract_untrusted_pdf(
            job_root / NORMALIZED_PDF_NAME,
            job_root,
            limits=limits,
        )
        extraction_hash = _outcome_hash(
            extraction_outcome,
            artifact_name="extraction.json",
            failure_code="EXTRACTION_ARTIFACT_INVALID",
            stage=stage,
            job_id=job_id,
        )

        stage = "freeze"
        preflight, extraction = _read_bound_extraction(
            job_root,
            limits=limits,
            expected_preflight_hash=preflight_hash,
            expected_normalization_hash=normalization_hash,
            expected_normalized_pdf_hash=normalized_pdf_hash,
            expected_extraction_hash=extraction_hash,
            job_id=job_id,
        )

        stage = "topology"
        topology = build_topology(extraction)
        if topology.status is not TopologyStatus.OK or topology.source is None:
            raise PrepareJobError(
                "NEEDS_TOPOLOGY_REVIEW",
                stage,
                job_id=job_id,
            )
        source = topology.source

        stage = "units"
        unit_outcome = build_semantic_units(source)
        if (
            unit_outcome.status is not UnitMergeStatus.OK
            or unit_outcome.artifact is None
        ):
            raise PrepareJobError("NEEDS_UNIT_REVIEW", stage, job_id=job_id)
        units = unit_outcome.artifact

        stage = "commit"
        source_hash = write_immutable_artifact(
            job_root / "source.json",
            source,
            "source",
        )
        units_hash = write_immutable_artifact(
            job_root / "units.json",
            units,
            "units",
        )

        stage = "state"
        job_state_hash = _freeze_extracted_state(
            job_root,
            job_id=job_id,
            source_sha256=str(preflight["source_sha256"]),
            preflight_hash=preflight_hash,
            normalization_hash=normalization_hash,
            normalized_pdf_hash=normalized_pdf_hash,
            source_hash=source_hash,
            units_hash=units_hash,
        )
        return {
            "job_id": job_id,
            "stage": JobStage.EXTRACTED.value,
            "source_sha256": preflight["source_sha256"],
            "artifact_hashes": {
                "preflight": preflight_hash,
                "normalization": normalization_hash,
                "normalized-pdf": normalized_pdf_hash,
                "extraction": extraction_hash,
                "source": source_hash,
                "units": units_hash,
            },
            "job_state_hash": job_state_hash,
        }
    except KeyboardInterrupt as exc:
        cleanup_outcome = "cancel"
        failure = PrepareJobError("PREPARE_CANCELLED", stage, job_id=job_id)
        failure.__cause__ = exc
    except PrepareJobError as error:
        failure = error
    except Exception as exc:
        failure = PrepareJobError("PREPARE_FAILED", stage, job_id=job_id)
        failure.__cause__ = exc

    if job_root is not None:
        try:
            cleanup = cleanup_after_job(
                managed_root,
                job_root,
                outcome=cleanup_outcome,
            )
        except Exception as exc:
            raise PrepareJobError(
                "PREPARE_CLEANUP_FAILED",
                "cleanup",
                job_id=job_id,
            ) from exc
        if not cleanup.cleaned:
            raise PrepareJobError(
                "PREPARE_CLEANUP_FAILED",
                "cleanup",
                job_id=job_id,
            ) from failure
    raise failure


__all__ = ["PrepareJobError", "prepare_managed_job"]
