# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import SENTINEL_NAME, create_managed_job
from academic_pdf_en_zh_reader.job.hashing import (
    sha256_bytes,
    sha256_canonical,
    stable_source_id,
)
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    load_job_state,
    write_immutable_artifact,
    write_immutable_bytes,
    write_job_state,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _explicit_in_process_worker_test_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep cross-platform unit fixtures explicit; production has no fallback."""

    from academic_pdf_en_zh_reader.orchestration import finish as finish_module
    from academic_pdf_en_zh_reader.qa.persist import validate_and_persist_qa
    from academic_pdf_en_zh_reader.rendering.compose import compose_bilingual_pdf

    monkeypatch.setattr(
        finish_module,
        "render_bilingual_pdf_in_worker",
        compose_bilingual_pdf,
    )
    monkeypatch.setattr(
        finish_module,
        "validate_qa_in_worker",
        validate_and_persist_qa,
    )


@dataclass(frozen=True)
class FinishFixture:
    managed_root: Path
    job_root: Path
    job_id: str
    source_pdf: Path
    translation_json: Path
    review_json: Path
    semantic_candidates_json: Path
    output_pdf: Path


def _advance(state, stage: JobStage, artifacts: dict[str, str]):
    return advance_job(
        state,
        stage,
        artifacts,
        expected_previous_state_hash=state_hash(state),
    )


def _write_source_pdf(path: Path, *, size_pt: float = 9.3) -> str:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setTitle("CC0 synthetic finish-job fixture")
    canvas.setFont("Helvetica", size_pt)
    canvas.drawString(72, 720, "Key term.")
    canvas.save()
    return sha256_bytes(path.read_bytes())


def _parents(
    source_sha256: str,
    *,
    normalized_pdf_sha256: str | None = None,
    source_font_size_mpt: int | None = 9_300,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    normalized_pdf_sha256 = normalized_pdf_sha256 or source_sha256
    source_text = "Key term."
    unit_id = stable_source_id(
        page_number=1,
        reading_order=0,
        role="body",
        source_char_start=0,
        source_char_end=len(source_text),
    )
    block: dict[str, object] = {
        "id": unit_id,
        "role": "body",
        "translation_policy": "required",
        "band_id": "body-band",
        "column_id": "body-column",
        "reading_order": 0,
        "source_char_start": 0,
        "source_char_end": len(source_text),
        "text": source_text,
        "bbox_mpt": [72_000, 715_000, 120_000, 727_000],
        "first_line_bbox_mpt": [72_000, 715_000, 120_000, 727_000],
        "confidence_ppm": 1_000_000,
    }
    if source_font_size_mpt is not None:
        block["source_font_size_mpt"] = source_font_size_mpt
    source: dict[str, object] = {
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
                        "id": "body-band",
                        "y_top_mpt": 760_000,
                        "y_bottom_mpt": 200_000,
                        "columns": [
                            {
                                "id": "body-column",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 555_276,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": [block],
            }
        ],
    }
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "units": [
            {
                "id": unit_id,
                "role": "body",
                "reading_order": 0,
                "source_text": source_text,
                "confidence_ppm": 1_000_000,
                "fragments": [
                    {
                        "page_number": 1,
                        "block_id": unit_id,
                        "source_char_start": 0,
                        "source_char_end": len(source_text),
                    }
                ],
            }
        ],
    }
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": unit_id,
                "chinese_text": "关键术语。",
                "spans": [],
                "terminology": [],
            }
        ],
    }
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": [unit_id],
        "issues": [],
        "final_status": "passed",
    }
    semantic: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "semantic-candidates",
        "units_hash": sha256_canonical(units),
        "translation_hash": sha256_canonical(translation),
        "review_hash": sha256_canonical(review),
        "red_candidates": [],
        "ambiguity_occurrences": [],
        "teaching_candidates": [],
        "figure_candidates": [],
    }
    return source, units, translation, review, semantic


def _write_external_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _finish_fixture(
    tmp_path: Path,
    *,
    job_id: str,
    source_font_size_mpt: int | None = 9_300,
) -> FinishFixture:
    source_pdf = tmp_path / f"{job_id}.source.pdf"
    _write_source_pdf(source_pdf)
    normalized_pdf_bytes = source_pdf.read_bytes()
    source_pdf.write_bytes(normalized_pdf_bytes + b"\n")
    source_sha256 = sha256_bytes(source_pdf.read_bytes())
    normalized_pdf_sha256 = sha256_bytes(normalized_pdf_bytes)
    source, units, translation, review, semantic = _parents(
        source_sha256,
        normalized_pdf_sha256=normalized_pdf_sha256,
        source_font_size_mpt=source_font_size_mpt,
    )

    managed_root = tmp_path / f"{job_id}.managed"
    managed_root.mkdir()
    job_root = create_managed_job(managed_root, job_id)
    preflight = {
        "schema_version": "1.0.0",
        "artifact_kind": "preflight",
        "source_sha256": source_sha256,
        "passed": True,
        "error_codes": [],
        "warnings": [],
        "checks": [{"id": "page-geometry", "hard_gate": True, "passed": True}],
        "pages": [
            {
                "page_number": 1,
                "width_mpt": 595_276,
                "height_mpt": 841_890,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "extractable_character_count": 8,
            }
        ],
    }
    preflight_hash = write_immutable_artifact(
        job_root / "preflight.json", preflight, "preflight"
    )
    normalized_pdf_hash = write_immutable_bytes(
        job_root / "normalized-source.pdf", normalized_pdf_bytes
    )
    normalization = {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": "1.0.0",
        "source_sha256": source_sha256,
        "preflight_sha256": preflight_hash,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "normalized_pdf_bytes": len(normalized_pdf_bytes),
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
    normalization_hash = write_immutable_artifact(
        job_root / "normalization.json", normalization, "normalization"
    )
    source_hash = write_immutable_artifact(job_root / "source.json", source, "source")
    units_hash = write_immutable_artifact(job_root / "units.json", units, "units")

    state = create_job(
        job_id=job_id,
        source_sha256=source_sha256,
        translation_revision=1,
    )
    state = _advance(
        state,
        JobStage.PREFLIGHTED,
        {
            "preflight": preflight_hash,
            "normalization": normalization_hash,
            "normalized-pdf": normalized_pdf_hash,
        },
    )
    state = _advance(
        state,
        JobStage.EXTRACTED,
        {"source": source_hash, "units": units_hash},
    )
    write_job_state(job_root / "job-state.json", state)

    agent_root = tmp_path / f"{job_id}.agent-inputs"
    agent_root.mkdir()
    translation_json = agent_root / "translation.json"
    review_json = agent_root / "review.json"
    semantic_json = agent_root / "semantic-candidates.json"
    _write_external_json(translation_json, translation)
    _write_external_json(review_json, review)
    _write_external_json(semantic_json, semantic)

    delivery_root = tmp_path / f"{job_id}.delivery"
    delivery_root.mkdir()
    return FinishFixture(
        managed_root=managed_root,
        job_root=job_root,
        job_id=job_id,
        source_pdf=source_pdf,
        translation_json=translation_json,
        review_json=review_json,
        semantic_candidates_json=semantic_json,
        output_pdf=delivery_root / "paper.bilingual-a3.zh-CN.pdf",
    )


def _finish(fixture: FinishFixture, **kwargs: object):
    from academic_pdf_en_zh_reader.orchestration.finish import finish_managed_job

    return finish_managed_job(
        managed_root=fixture.managed_root,
        job_id=fixture.job_id,
        source_pdf=fixture.source_pdf,
        translation_json=fixture.translation_json,
        review_json=fixture.review_json,
        semantic_candidates_json=fixture.semantic_candidates_json,
        output_pdf=fixture.output_pdf,
        **kwargs,
    )


@pytest.mark.parametrize(
    "field",
    ("translation_json", "review_json", "semantic_candidates_json"),
)
def test_finish_rejects_every_agent_input_inside_the_job_root(
    tmp_path: Path,
    field: str,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(tmp_path, job_id=f"finish-inside-{field[:3]}")
    inside = fixture.job_root / f"agent-{field}.json"
    inside.write_bytes(getattr(fixture, field).read_bytes())
    values = dict(fixture.__dict__)
    values[field] = inside
    unsafe = FinishFixture(**values)

    with pytest.raises(FinishJobError) as captured:
        _finish(unsafe)

    assert captured.value.code == "AGENT_INPUT_UNSAFE"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_rejects_symlink_and_oversized_agent_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    oversized = _finish_fixture(tmp_path, job_id="finish-agent-size")
    monkeypatch.setattr(finish_module, "MAX_AGENT_JSON_BYTES", 8)
    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(oversized)
    assert captured.value.code == "AGENT_INPUT_TOO_LARGE"
    assert not oversized.job_root.exists()

    linked = _finish_fixture(tmp_path, job_id="finish-agent-link")
    symlink = linked.translation_json.with_name("translation-link.json")
    try:
        symlink.symlink_to(linked.translation_json)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    values = dict(linked.__dict__)
    values["translation_json"] = symlink
    linked_input = FinishFixture(**values)
    monkeypatch.setattr(finish_module, "MAX_AGENT_JSON_BYTES", 128 * 1024 * 1024)
    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(linked_input)
    assert captured.value.code == "AGENT_INPUT_UNSAFE"
    assert not linked.job_root.exists()


def test_finish_rejects_non_finite_json_constants_before_schema_validation(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(tmp_path, job_id="finish-json-constant")
    fixture.translation_json.write_text(
        '{"artifact_kind":"translation","translation_revision":NaN}',
        encoding="utf-8",
    )

    with pytest.raises(FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "AGENT_INPUT_INVALID"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


@pytest.mark.parametrize(
    "field",
    ("translation_json", "review_json", "semantic_candidates_json"),
)
def test_finish_rejects_duplicate_keys_in_every_agent_input(
    tmp_path: Path,
    field: str,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(tmp_path, job_id=f"finish-duplicate-{field[:3]}")
    path = getattr(fixture, field)
    original = path.read_text(encoding="utf-8")
    path.write_text(
        '{\n  "schema_version": "0.0.0",' + original.lstrip()[1:],
        encoding="utf-8",
    )

    with pytest.raises(FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "AGENT_INPUT_INVALID"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


@pytest.mark.parametrize("bad_parent", ("translation", "review", "semantic"))
def test_finish_rejects_parent_hash_or_agent_identity_mismatch(
    tmp_path: Path,
    bad_parent: str,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(tmp_path, job_id=f"finish-parent-{bad_parent}")
    if bad_parent == "translation":
        value = json.loads(fixture.translation_json.read_text(encoding="utf-8"))
        value["units_hash"] = "f" * 64
        _write_external_json(fixture.translation_json, value)
        expected = "TRANSLATION_INVALID"
    elif bad_parent == "review":
        value = json.loads(fixture.review_json.read_text(encoding="utf-8"))
        value["reviewer_id"] = value["translator_id"]
        _write_external_json(fixture.review_json, value)
        expected = "INDEPENDENT_REVIEW_REQUIRED"
    else:
        value = json.loads(fixture.semantic_candidates_json.read_text(encoding="utf-8"))
        value["review_hash"] = "e" * 64
        _write_external_json(fixture.semantic_candidates_json, value)
        expected = "SEMANTIC_CANDIDATES_PARENT_MISMATCH"

    with pytest.raises(FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == expected
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_builds_typography_only_from_bound_body_or_abstract_evidence() -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import (
        FinishJobError,
        _style_contract_from_source,
    )

    source, *_rest = _parents("a" * 64, source_font_size_mpt=9_300)
    assert _style_contract_from_source(source).body_source_size_mpt == 9_300

    abstract = deepcopy(source)
    block = abstract["pages"][0]["blocks"][0]
    block["role"] = "abstract"
    block["source_font_size_mpt"] = 8_700
    assert _style_contract_from_source(abstract).body_source_size_mpt == 8_700

    missing, *_rest = _parents("a" * 64, source_font_size_mpt=None)
    with pytest.raises(FinishJobError) as captured:
        _style_contract_from_source(missing)
    assert captured.value.code == "TYPOGRAPHY_EVIDENCE_INVALID"

    abstract["pages"][0]["blocks"][0]["translation_policy"] = "excluded"
    with pytest.raises(FinishJobError) as captured:
        _style_contract_from_source(abstract)
    assert captured.value.code == "TYPOGRAPHY_EVIDENCE_MISSING"


def test_finish_rejects_valid_source_bytes_that_no_longer_match_extracted_ledger(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(tmp_path, job_id="finish-source-tamper")
    source_path = fixture.job_root / "source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["pages"][0]["blocks"][0]["source_font_size_mpt"] = 8_900
    source_path.write_bytes(canonical_json_bytes(source))

    with pytest.raises(FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "SOURCE_ARTIFACT_MISMATCH"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_accepts_only_an_extracted_ledger(tmp_path: Path) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(tmp_path, job_id="finish-wrong-stage")
    state_path = fixture.job_root / "job-state.json"
    state = load_job_state(state_path)
    translated = _advance(
        state,
        JobStage.TRANSLATED,
        {"translation": "2" * 64},
    )
    write_job_state(
        state_path,
        translated,
        expected_previous_state_hash=state_hash(state),
    )

    with pytest.raises(FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "JOB_NOT_EXTRACTED"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_advances_every_stage_and_uses_non_hardcoded_typography(
    tmp_path: Path,
) -> None:
    fixture = _finish_fixture(tmp_path, job_id="finish-debug-success")

    result = _finish(fixture, retain_debug=True, ttl_seconds=60)

    assert result == {
        "status": "ok",
        "code": "FINISH_OK",
        "notices": ["DISCLAIMER_PAGE_APPENDED"],
    }
    state = load_job_state(fixture.job_root / "job-state.json")
    assert [record.stage for record in state.history] == list(JobStage)
    source = json.loads((fixture.job_root / "source.json").read_text(encoding="utf-8"))
    receipt = json.loads(
        (fixture.job_root / "finalization-receipt.json").read_text(encoding="utf-8")
    )
    from academic_pdf_en_zh_reader.job.finalize import _style_contract_payload
    from academic_pdf_en_zh_reader.orchestration.finish import (
        _style_contract_from_source,
    )

    expected_style = _style_contract_payload(_style_contract_from_source(source))
    assert expected_style["body_source_size_mpt"] == 9_300
    assert receipt["policy_hashes"]["style-contract"] == sha256_canonical(
        expected_style
    )
    manifest = json.loads(
        (fixture.job_root / "render-manifest.json").read_text(encoding="utf-8")
    )
    qa = json.loads((fixture.job_root / "qa.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (fixture.job_root / "provenance.json").read_text(encoding="utf-8")
    )
    assert state.source_sha256 != source["normalized_pdf_sha256"]
    assert manifest["source_sha256"] == state.source_sha256
    assert manifest["normalized_pdf_sha256"] == source["normalized_pdf_sha256"]
    assert qa["source_pdf_sha256"] == state.source_sha256
    assert qa["normalized_pdf_sha256"] == source["normalized_pdf_sha256"]
    assert provenance["source_sha256"] == state.source_sha256
    assert provenance["normalized_pdf_sha256"] == source["normalized_pdf_sha256"]
    assert fixture.output_pdf.is_file()
    assert list(fixture.output_pdf.parent.iterdir()) == [fixture.output_pdf]


def test_finish_cleans_candidate_when_rendered_state_cas_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    fixture = _finish_fixture(tmp_path, job_id="finish-render-cas")
    real_commit = finish_module._commit_stage
    composed = False
    real_compose = finish_module.render_bilingual_pdf_in_worker

    def compose(*args: object, **kwargs: object):
        nonlocal composed
        result = real_compose(*args, **kwargs)
        composed = True
        return result

    def commit(state, target_stage: JobStage, artifacts, state_path: Path):
        if target_stage is JobStage.RENDERED:
            raise finish_module.FinishJobError("STATE_CAS_FAILED", "render")
        return real_commit(state, target_stage, artifacts, state_path)

    monkeypatch.setattr(finish_module, "render_bilingual_pdf_in_worker", compose)
    monkeypatch.setattr(finish_module, "_commit_stage", commit)

    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert composed is True
    assert captured.value.code == "STATE_CAS_FAILED"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_never_exposes_an_untrusted_render_exception_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    fixture = _finish_fixture(tmp_path, job_id="finish-render-code")

    class LeakyRenderError(RuntimeError):
        code = "ATTACKER_CHOSEN_CODE"

    def fail_render(**_kwargs: object) -> None:
        raise LeakyRenderError("paper text")

    monkeypatch.setattr(
        finish_module,
        "render_bilingual_pdf_in_worker",
        fail_render,
    )
    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "RENDER_FAILED"
    assert "ATTACKER_CHOSEN_CODE" not in str(captured.value)
    assert "paper text" not in str(captured.value)
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_qa_failure_publishes_no_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module
    from academic_pdf_en_zh_reader.qa.persist import QaCommitResult

    fixture = _finish_fixture(tmp_path, job_id="finish-qa-failure")
    monkeypatch.setattr(
        finish_module,
        "validate_qa_in_worker",
        lambda **_kwargs: QaCommitResult(
            code="QA_FAILED",
            passed=False,
            qa_hash=None,
            provenance_hash=None,
            validated_state_hash=None,
        ),
    )

    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "QA_FAILED"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_preserves_qa_sandbox_contract_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module
    from academic_pdf_en_zh_reader.qa.persist import QaCommitError

    fixture = _finish_fixture(tmp_path, job_id="finish-qa-sandbox-contract")

    def fail_qa(**_kwargs: object) -> None:
        raise QaCommitError("SANDBOX_CONTRACT_UNVERIFIED")

    monkeypatch.setattr(finish_module, "validate_qa_in_worker", fail_qa)

    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "SANDBOX_CONTRACT_UNVERIFIED"
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


def test_finish_never_exposes_an_untrusted_qa_exception_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    fixture = _finish_fixture(tmp_path, job_id="finish-qa-code")

    class LeakyQaError(RuntimeError):
        code = "ATTACKER_CHOSEN_CODE"

    def fail_qa(**_kwargs: object) -> None:
        raise LeakyQaError("paper text")

    monkeypatch.setattr(finish_module, "validate_qa_in_worker", fail_qa)

    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "QA_COMMIT_FAILED"
    assert "ATTACKER_CHOSEN_CODE" not in str(captured.value)
    assert "paper text" not in str(captured.value)
    assert not fixture.job_root.exists()
    assert not fixture.output_pdf.exists()


@pytest.mark.parametrize(
    ("retain_debug", "retained"),
    ((False, False), (True, True)),
)
def test_finish_failure_cleanup_boundary_requires_explicit_debug_retention(
    tmp_path: Path,
    retain_debug: bool,
    retained: bool,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.finish import FinishJobError

    fixture = _finish_fixture(
        tmp_path,
        job_id=f"finish-failure-retain-{int(retain_debug)}",
    )
    translation = json.loads(fixture.translation_json.read_text(encoding="utf-8"))
    translation["units_hash"] = "f" * 64
    _write_external_json(fixture.translation_json, translation)

    with pytest.raises(FinishJobError) as captured:
        _finish(
            fixture,
            retain_debug=retain_debug,
            **({"ttl_seconds": 60} if retain_debug else {}),
        )

    assert captured.value.code == "TRANSLATION_INVALID"
    assert fixture.job_root.exists() is retained
    if retained:
        sentinel = json.loads(
            (fixture.job_root / SENTINEL_NAME).read_text(encoding="utf-8")
        )
        assert sentinel["retention_mode"] == "debug"
    assert not fixture.output_pdf.exists()


@pytest.mark.parametrize(
    ("retain_debug", "retained"),
    ((False, False), (True, True)),
)
def test_finish_cancel_cleanup_boundary_requires_explicit_debug_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain_debug: bool,
    retained: bool,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    fixture = _finish_fixture(
        tmp_path,
        job_id=f"finish-cancel-retain-{int(retain_debug)}",
    )

    def cancel(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(finish_module, "validate_translation_artifact", cancel)
    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(
            fixture,
            retain_debug=retain_debug,
            **({"ttl_seconds": 60} if retain_debug else {}),
        )

    assert captured.value.code == "FINISH_CANCELLED"
    assert fixture.job_root.exists() is retained
    if retained:
        sentinel = json.loads(
            (fixture.job_root / SENTINEL_NAME).read_text(encoding="utf-8")
        )
        assert sentinel["retention_mode"] == "debug"
    assert not fixture.output_pdf.exists()


def test_finish_maps_cleanup_io_exception_to_content_free_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    fixture = _finish_fixture(tmp_path, job_id="finish-cleanup-exception")
    translation = json.loads(fixture.translation_json.read_text(encoding="utf-8"))
    translation["units_hash"] = "f" * 64
    _write_external_json(fixture.translation_json, translation)

    def fail_cleanup(*_args: object, **_kwargs: object):
        raise OSError("private-path/paper.pdf")

    monkeypatch.setattr(finish_module, "cleanup_after_job", fail_cleanup)
    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "CLEANUP_FAILED"
    assert captured.value.stage == "cleanup"
    assert "private-path" not in str(captured.value)


def test_delivery_state_failure_may_leave_published_pdf_but_is_never_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.job import deliver as deliver_module
    from academic_pdf_en_zh_reader.orchestration import finish as finish_module

    fixture = _finish_fixture(tmp_path, job_id="finish-delivery-state-failure")
    real_write_state = deliver_module.write_job_state

    def fail_finalized_state(path, state, **kwargs):
        if state.stage is JobStage.FINALIZED:
            raise OSError("final state unavailable")
        return real_write_state(path, state, **kwargs)

    monkeypatch.setattr(deliver_module, "write_job_state", fail_finalized_state)

    with pytest.raises(finish_module.FinishJobError) as captured:
        _finish(fixture)

    assert captured.value.code == "DELIVERY_STATE_FAILED"
    assert fixture.output_pdf.is_file()
    assert not fixture.job_root.exists()


def test_finish_default_success_delivers_only_one_pdf_and_cleans_job(
    tmp_path: Path,
) -> None:
    fixture = _finish_fixture(tmp_path, job_id="finish-default-success")

    result = _finish(fixture)

    assert result == {
        "status": "ok",
        "code": "FINISH_OK",
        "notices": ["DISCLAIMER_PAGE_APPENDED"],
    }
    assert fixture.output_pdf.is_file()
    assert list(fixture.output_pdf.parent.iterdir()) == [fixture.output_pdf]
    assert not fixture.job_root.exists()


def _wrapper_globals() -> dict[str, object]:
    return runpy.run_path(
        str(ROOT / "scripts" / "finish_job.py"),
        run_name="finish_job_contract_test",
    )


def _wrapper_arguments() -> list[str]:
    return [
        "--managed-root",
        "private-managed",
        "--job-id",
        "private-job",
        "--source-pdf",
        "private-paper.pdf",
        "--translation-json",
        "private-translation.json",
        "--review-json",
        "private-review.json",
        "--semantic-candidates-json",
        "private-semantic.json",
        "--output-pdf",
        "private-output.pdf",
    ]


def test_finish_job_wrapper_success_is_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _wrapper_globals()
    main = module["main"]
    main.__globals__["finish_managed_job"] = lambda **_kwargs: {
        "status": "ok",
        "code": "FINISH_OK",
    }

    assert main(_wrapper_arguments()) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_finish_job_wrapper_reports_appended_disclaimer_without_private_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _wrapper_globals()
    main = module["main"]
    main.__globals__["finish_managed_job"] = lambda **_kwargs: {
        "status": "ok",
        "code": "FINISH_OK",
        "notices": ["DISCLAIMER_PAGE_APPENDED"],
    }

    assert main(_wrapper_arguments()) == 0
    captured = capsys.readouterr()
    assert captured.out == "原末页无足够安全空间，已在文件末尾追加责任声明页。\n"
    assert captured.err == ""
    assert "private-" not in captured.out
    assert "FINISH_OK" not in captured.out


def test_finish_job_wrapper_does_not_echo_unknown_notice_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _wrapper_globals()
    main = module["main"]
    main.__globals__["finish_managed_job"] = lambda **_kwargs: {
        "status": "ok",
        "code": "FINISH_OK",
        "notices": ["private-paper-data"],
    }

    assert main(_wrapper_arguments()) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_finish_job_wrapper_hides_cleanup_cause_and_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _wrapper_globals()
    main = module["main"]
    error_type = main.__globals__["FinishJobError"]

    def fail(**_kwargs: object) -> None:
        try:
            raise OSError("private-path/paper.pdf")
        except OSError as cause:
            raise error_type("CLEANUP_FAILED", "cleanup") from cause

    main.__globals__["finish_managed_job"] = fail
    assert main(_wrapper_arguments()) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "FINISH_ERROR cleanup CLEANUP_FAILED\n"
    assert "private-path" not in captured.err
    assert "Traceback" not in captured.err


def test_finish_job_wrapper_help_is_content_free_and_complete() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/finish_job.py", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0
    for option in (
        "--managed-root",
        "--job-id",
        "--source-pdf",
        "--translation-json",
        "--review-json",
        "--semantic-candidates-json",
        "--output-pdf",
        "--retain-debug",
        "--ttl-seconds",
    ):
        assert option in result.stdout
    assert "Key term" not in result.stdout + result.stderr
