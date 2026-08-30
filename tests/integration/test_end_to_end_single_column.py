# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from academic_pdf_en_zh_reader.job.cleanup import create_managed_job
from academic_pdf_en_zh_reader.job.deliver import deliver_validated_pdf
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    load_job_state,
    write_job_state,
)
from academic_pdf_en_zh_reader.qa.persist import validate_and_persist_qa
from academic_pdf_en_zh_reader.rendering.compose import compose_bilingual_pdf

from .conftest import NOW, build_case


def _advance(state, stage: JobStage, artifacts: dict[str, str]):
    return advance_job(
        state,
        stage,
        artifacts,
        expected_previous_state_hash=state_hash(state),
    )


def _rendered_state(
    *,
    case,
    candidate_hash: str,
    manifest_hash: str,
):
    state = create_job(
        job_id="job-single-column-001",
        source_sha256=str(case.source["source_sha256"]),
        translation_revision=1,
    )
    stages = (
        (
            JobStage.PREFLIGHTED,
            {
                "preflight": "1" * 64,
                "normalization": "2" * 64,
                "normalized-pdf": str(case.source["normalized_pdf_sha256"]),
            },
        ),
        (
            JobStage.EXTRACTED,
            {
                "source": sha256_canonical(case.source),
                "units": sha256_canonical(case.units),
            },
        ),
        (JobStage.TRANSLATED, {"translation": sha256_canonical(case.translation)}),
        (
            JobStage.INDEPENDENTLY_REVIEWED,
            {"review": sha256_canonical(case.review)},
        ),
        (
            JobStage.ANNOTATED,
            {"annotations": sha256_canonical(case.annotations)},
        ),
        (
            JobStage.LAID_OUT,
            {
                "frame-graph": sha256_canonical(case.frame_graph),
                "layout": sha256_canonical(case.layout),
                "finalization-receipt": sha256_canonical(case.receipt),
            },
        ),
        (
            JobStage.RENDERED,
            {"render-manifest": manifest_hash, "pdf": candidate_hash},
        ),
    )
    for stage, artifacts in stages:
        state = _advance(state, stage, artifacts)
    return state


def test_single_column_full_chain_delivers_only_the_validated_pdf(
    tmp_path: Path,
) -> None:
    case = build_case(tmp_path, "single-column")
    managed_root = tmp_path / "managed-jobs"
    managed_root.mkdir()
    job_root = create_managed_job(managed_root, "job-single-column-001", now=NOW)
    candidate_path = job_root / "candidate.pdf"
    manifest_path = job_root / "render-manifest.json"
    composition = compose_bilingual_pdf(
        source_pdf_path=case.source_pdf_path,
        source=case.source,
        units=case.units,
        translation=case.translation,
        review=case.review,
        annotations=case.annotations,
        frame_graph=case.frame_graph,
        layout=case.layout,
        finalization_receipt=case.receipt,
        policy_inputs=case.policy_inputs,
        overlay_plan=case.overlay_plan,
        expected_finalization_receipt_hash=sha256_canonical(case.receipt),
        expected_overlay_plan_hash=str(case.overlay_plan["overlay_plan_hash"]),
        job_root=job_root,
        output_pdf_path=candidate_path,
        render_manifest_path=manifest_path,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rendered_state = _rendered_state(
        case=case,
        candidate_hash=composition.output_pdf_sha256,
        manifest_hash=composition.render_manifest_hash,
    )
    state_path = job_root / "job-state.json"
    write_job_state(state_path, rendered_state)
    rendered_state_hash = state_hash(rendered_state)

    qa_commit = validate_and_persist_qa(
        job_root=job_root,
        expected_rendered_state_hash=rendered_state_hash,
        source_pdf_path=case.source_pdf_path,
        output_pdf_path=candidate_path,
        source=case.source,
        units=case.units,
        translation=case.translation,
        review=case.review,
        annotations=case.annotations,
        frame_graph=case.frame_graph,
        layout=case.layout,
        finalization_receipt=case.receipt,
        policy_inputs=case.policy_inputs,
        overlay_plan=case.overlay_plan,
        render_manifest=manifest,
        expected_render_manifest_hash=sha256_canonical(manifest),
    )
    assert qa_commit.code == "QA_VALIDATED"
    assert qa_commit.passed is True
    validated_state = load_job_state(state_path)
    assert validated_state.stage is JobStage.VALIDATED
    assert state_hash(validated_state) == qa_commit.validated_state_hash

    qa_path = job_root / "qa.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    assert qa["passed"] is True
    assert qa["output_pdf_sha256"] == composition.output_pdf_sha256
    assert (job_root / "provenance.json").is_file()
    candidate_bytes = candidate_path.read_bytes()

    delivery_dir = tmp_path / "delivery"
    delivery_dir.mkdir()
    final_path = delivery_dir / "paper.bilingual-a3.zh-CN.pdf"
    result = deliver_validated_pdf(
        managed_root=managed_root,
        job_root=job_root,
        state_path=state_path,
        source_path=case.source_pdf_path,
        candidate_path=candidate_path,
        render_manifest_path=manifest_path,
        qa_path=qa_path,
        output_path=final_path,
        now=NOW,
    )

    assert (result.status, result.code) == ("ok", "DELIVERY_OK")
    assert result.delivered is True
    assert result.output_sha256 == sha256_bytes(candidate_bytes)
    assert final_path.read_bytes() == candidate_bytes
    assert list(delivery_dir.iterdir()) == [final_path]
    assert not job_root.exists()
