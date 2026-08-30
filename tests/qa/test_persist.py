# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import academic_pdf_en_zh_reader.qa.persist as persist_module
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    load_job_state,
    write_immutable_artifact,
    write_job_state,
)
from academic_pdf_en_zh_reader.qa.api import run_mechanical_qa
from academic_pdf_en_zh_reader.qa.persist import (
    QaCommitError,
    validate_and_persist_qa,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


def _rendered_state(inputs: dict[str, object], *, pdf_hash: str | None = None):
    state = create_job(
        job_id="job-qa-001",
        source_sha256=inputs["source"]["source_sha256"],
        translation_revision=1,
    )
    artifacts = {
        JobStage.PREFLIGHTED: {
            "preflight": "1" * 64,
            "normalization": "2" * 64,
            "normalized-pdf": inputs["source"]["normalized_pdf_sha256"],
        },
        JobStage.EXTRACTED: {
            "source": sha256_canonical(inputs["source"]),
            "units": sha256_canonical(inputs["units"]),
        },
        JobStage.TRANSLATED: {"translation": sha256_canonical(inputs["translation"])},
        JobStage.INDEPENDENTLY_REVIEWED: {"review": sha256_canonical(inputs["review"])},
        JobStage.ANNOTATED: {"annotations": sha256_canonical(inputs["annotations"])},
        JobStage.LAID_OUT: {
            "frame-graph": sha256_canonical(inputs["frame_graph"]),
            "layout": sha256_canonical(inputs["layout"]),
            "finalization-receipt": sha256_canonical(inputs["finalization_receipt"]),
        },
        JobStage.RENDERED: {
            "render-manifest": inputs["expected_render_manifest_hash"],
            "pdf": pdf_hash or inputs["render_manifest"]["output_pdf_sha256"],
        },
    }
    for stage in list(JobStage)[1 : list(JobStage).index(JobStage.RENDERED) + 1]:
        state = advance_job(
            state,
            stage,
            artifacts[stage],
            expected_previous_state_hash=state_hash(state),
        )
    return state


def _install_rendered_state(inputs: dict[str, object]) -> tuple[Path, str]:
    job_root = Path(inputs["output_pdf_path"]).parent
    state = _rendered_state(inputs)
    write_job_state(job_root / "job-state.json", state)
    return job_root, state_hash(state)


def test_partial_artifacts_recover_and_state_cas_is_last(
    composed_qa_fixture: dict[str, object], monkeypatch
) -> None:
    job_root, rendered_hash = _install_rendered_state(composed_qa_fixture)
    real_write_state = persist_module.write_job_state

    def fail_state(*_args, **_kwargs):
        raise OSError("injected state failure")

    monkeypatch.setattr(persist_module, "write_job_state", fail_state)
    with pytest.raises(QaCommitError, match="QA_STATE_CAS_FAILED"):
        validate_and_persist_qa(
            job_root=job_root,
            expected_rendered_state_hash=rendered_hash,
            **composed_qa_fixture,
        )

    qa_path = job_root / "qa.json"
    provenance_path = job_root / "provenance.json"
    assert qa_path.is_file() and provenance_path.is_file()
    assert load_job_state(job_root / "job-state.json").stage is JobStage.RENDERED
    persisted_qa = json.loads(qa_path.read_text(encoding="utf-8"))
    persisted_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    validate_artifact("qa", persisted_qa)
    validate_artifact("provenance", persisted_provenance)
    assert (
        persisted_qa["source_pdf_sha256"]
        == composed_qa_fixture["source"]["source_sha256"]
    )
    assert (
        persisted_qa["normalized_pdf_sha256"]
        == composed_qa_fixture["source"]["normalized_pdf_sha256"]
    )
    assert persisted_provenance["source_sha256"] == persisted_qa["source_pdf_sha256"]
    assert (
        persisted_provenance["normalized_pdf_sha256"]
        == persisted_qa["normalized_pdf_sha256"]
    )
    provenance_text = json.dumps(persisted_provenance, ensure_ascii=False)
    assert str(job_root) not in provenance_text
    assert "Key scientific term in context." not in provenance_text
    assert composed_qa_fixture["translation"]["units"][0]["chinese_text"] not in (
        provenance_text
    )

    monkeypatch.setattr(persist_module, "write_job_state", real_write_state)
    monkeypatch.setattr(
        persist_module,
        "run_mechanical_qa",
        lambda **_kwargs: persisted_qa,
    )
    result = validate_and_persist_qa(
        job_root=job_root,
        expected_rendered_state_hash=rendered_hash,
        **composed_qa_fixture,
    )
    assert result.code == "QA_VALIDATED"
    validated = load_job_state(job_root / "job-state.json")
    assert validated.stage is JobStage.VALIDATED
    assert validated.artifact_hashes["qa"] == result.qa_hash
    assert validated.artifact_hashes["provenance"] == result.provenance_hash

    replay = validate_and_persist_qa(
        job_root=job_root,
        expected_rendered_state_hash=rendered_hash,
        **composed_qa_fixture,
    )
    assert replay.code == "QA_ALREADY_VALIDATED"
    assert replay.validated_state_hash == state_hash(validated)


def test_qa_only_partial_commit_is_idempotently_resumed(
    composed_qa_fixture: dict[str, object], monkeypatch
) -> None:
    job_root, rendered_hash = _install_rendered_state(composed_qa_fixture)
    passed_qa = run_mechanical_qa(**composed_qa_fixture)
    real_commit = persist_module._write_or_verify_same

    def fail_provenance(path, value, schema_name, *, root):
        if schema_name == "provenance":
            raise QaCommitError("INJECTED_PROVENANCE_FAILURE")
        return real_commit(path, value, schema_name, root=root)

    monkeypatch.setattr(persist_module, "_write_or_verify_same", fail_provenance)
    with pytest.raises(QaCommitError, match="INJECTED_PROVENANCE_FAILURE"):
        validate_and_persist_qa(
            job_root=job_root,
            expected_rendered_state_hash=rendered_hash,
            **composed_qa_fixture,
        )
    assert (job_root / "qa.json").is_file()
    assert not (job_root / "provenance.json").exists()
    assert load_job_state(job_root / "job-state.json").stage is JobStage.RENDERED

    monkeypatch.setattr(persist_module, "_write_or_verify_same", real_commit)
    monkeypatch.setattr(
        persist_module,
        "run_mechanical_qa",
        lambda **_kwargs: passed_qa,
    )
    result = validate_and_persist_qa(
        job_root=job_root,
        expected_rendered_state_hash=rendered_hash,
        **composed_qa_fixture,
    )
    assert result.code == "QA_VALIDATED"
    assert (job_root / "provenance.json").is_file()


def test_different_existing_qa_artifact_is_rejected(
    composed_qa_fixture: dict[str, object], monkeypatch
) -> None:
    job_root, rendered_hash = _install_rendered_state(composed_qa_fixture)
    passed_qa = run_mechanical_qa(**composed_qa_fixture)
    conflicting_qa = deepcopy(passed_qa)
    conflicting_qa["qa_config_hash"] = "f" * 64
    if conflicting_qa["qa_config_hash"] == passed_qa["qa_config_hash"]:
        conflicting_qa["qa_config_hash"] = "e" * 64
    write_immutable_artifact(job_root / "qa.json", conflicting_qa, "qa")
    monkeypatch.setattr(
        persist_module,
        "run_mechanical_qa",
        lambda **_kwargs: passed_qa,
    )

    with pytest.raises(QaCommitError, match="QA_ARTIFACT_CONFLICT"):
        validate_and_persist_qa(
            job_root=job_root,
            expected_rendered_state_hash=rendered_hash,
            **composed_qa_fixture,
        )
    assert not (job_root / "provenance.json").exists()
    assert load_job_state(job_root / "job-state.json").stage is JobStage.RENDERED


def test_failed_qa_writes_no_passed_artifacts_and_does_not_advance(
    composed_qa_fixture: dict[str, object], monkeypatch
) -> None:
    job_root, rendered_hash = _install_rendered_state(composed_qa_fixture)
    failed = deepcopy(run_mechanical_qa(**composed_qa_fixture))
    failed["passed"] = False
    failed["checks"][-1]["passed"] = False
    failed["checks"][-1]["details"] = "RASTER_PAGE_SANITY_FAILED"
    validate_artifact("qa", failed)
    monkeypatch.setattr(
        persist_module,
        "run_mechanical_qa",
        lambda **_kwargs: failed,
    )

    result = validate_and_persist_qa(
        job_root=job_root,
        expected_rendered_state_hash=rendered_hash,
        **composed_qa_fixture,
    )

    assert result.code == "QA_FAILED"
    assert not (job_root / "qa.json").exists()
    assert not (job_root / "provenance.json").exists()
    assert load_job_state(job_root / "job-state.json").stage is JobStage.RENDERED


def test_rendered_ledger_pdf_binding_is_checked_before_qa(
    composed_qa_fixture: dict[str, object], monkeypatch
) -> None:
    job_root = Path(composed_qa_fixture["output_pdf_path"]).parent
    state = _rendered_state(composed_qa_fixture, pdf_hash="f" * 64)
    write_job_state(job_root / "job-state.json", state)
    called = False

    def should_not_run(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(persist_module, "run_mechanical_qa", should_not_run)
    with pytest.raises(QaCommitError, match="QA_STATE_BINDING_MISMATCH"):
        validate_and_persist_qa(
            job_root=job_root,
            expected_rendered_state_hash=state_hash(state),
            **composed_qa_fixture,
        )
    assert called is False


def test_normalized_pdf_ledger_binding_is_checked_before_qa(
    composed_qa_fixture: dict[str, object], monkeypatch
) -> None:
    job_root = Path(composed_qa_fixture["output_pdf_path"]).parent
    inputs = deepcopy(composed_qa_fixture)
    inputs["source"]["normalized_pdf_sha256"] = "f" * 64
    state = _rendered_state(inputs)
    write_job_state(job_root / "job-state.json", state)
    called = False

    def should_not_run(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(persist_module, "run_mechanical_qa", should_not_run)
    with pytest.raises(QaCommitError, match="QA_STATE_BINDING_MISMATCH"):
        validate_and_persist_qa(
            job_root=job_root,
            expected_rendered_state_hash=state_hash(state),
            **composed_qa_fixture,
        )
    assert called is False
