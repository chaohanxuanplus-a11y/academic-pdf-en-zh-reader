# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Keep an extracted checkpoint while isolated completion attempts advance."""

from pathlib import Path
from uuid import uuid4

from academic_pdf_en_zh_reader.job.cleanup import cleanup_after_job, create_managed_job
from academic_pdf_en_zh_reader.job.storage import (
    create_verified_rerun,
    load_job_state,
    write_immutable_bytes,
    write_job_state,
)
from academic_pdf_en_zh_reader.security.input_copy import read_bounded_regular_file
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS

EXTRACTION_FILES = {
    "preflight": "preflight.json",
    "normalization": "normalization.json",
    "normalized-pdf": "normalized-source.pdf",
    "source": "source.json",
    "units": "units.json",
}


def create_finish_attempt(managed_root, checkpoint, *, translation_revision=None):
    """Copy only verified extraction bytes; never rewind a mutable ledger."""
    checkpoint = Path(checkpoint)
    state = load_job_state(checkpoint / "job-state.json")
    attempt_id = "attempt-" + uuid4().hex
    attempt_state = create_verified_rerun(
        state=state,
        preflight_path=checkpoint / "preflight.json",
        normalization_path=checkpoint / "normalization.json",
        normalized_pdf_path=checkpoint / "normalized-source.pdf",
        source_path=checkpoint / "source.json",
        units_path=checkpoint / "units.json",
        new_job_id=attempt_id,
        next_translation_revision=state.translation_revision
        if translation_revision is None
        else translation_revision,
    )
    attempt = create_managed_job(managed_root, attempt_id)
    try:
        for key, name in EXTRACTION_FILES.items():
            limit = (
                DEFAULT_LIMITS.max_normalized_pdf_bytes
                if key == "normalized-pdf"
                else 128 * 1024 * 1024
            )
            snapshot = read_bounded_regular_file(checkpoint / name, max_bytes=limit)
            if snapshot.sha256 != attempt_state.artifact_hashes[key]:
                raise ValueError("checkpoint changed during copying")
            write_immutable_bytes(attempt / name, snapshot.data)
        write_job_state(attempt / "job-state.json", attempt_state)
        return attempt
    except BaseException:
        cleanup_after_job(managed_root, attempt, outcome="failure")
        raise


def recovery_action(code):
    """Stable local actions, independent of document-supplied text."""
    if code in {
        "TRANSLATION_INVALID",
        "MECHANICAL_SEMANTIC_MISMATCH",
        "REVIEW_REQUIRED",
    }:
        return "repair-affected-units"
    if code in {
        "FINALIZATION_FAILED",
        "OVERLAY_PLAN_FAILED",
        "APPROVED_FONT_CHAIN_INVALID",
    }:
        return "repair-layout-or-fonts"
    if code in {
        "SEMANTIC_CANDIDATES_INVALID",
        "CORE_VOCABULARY_MISSING",
        "CORE_FIGURE_READING_MISSING",
        "SEMANTIC_CANDIDATES_SCHEMA_INVALID",
    }:
        return "repair-supplementary-content"
    if code in {"QA_FAILED", "QA_COMMIT_FAILED"}:
        return "inspect-local-qa-and-repair"
    if code == "RENDER_FAILED":
        return "verify-compatible-runtime"
    if code in {"OUTPUT_EXISTS", "RENDER_OUTPUT_EXISTS"}:
        return "use-new-output-path"
    return None
