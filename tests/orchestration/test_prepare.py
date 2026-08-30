# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.extraction import extract_document
from academic_pdf_en_zh_reader.extraction.unit_merge import (
    UnitMergeIssue,
    UnitMergeOutcome,
    UnitMergeStatus,
)
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import SENTINEL_NAME, create_managed_job
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.job.state import JobStage, state_hash
from academic_pdf_en_zh_reader.job.storage import load_job_state
from academic_pdf_en_zh_reader.normalization.core import normalize_pdf_bytes
from academic_pdf_en_zh_reader.preflight.checks import preflight_safe_copy
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy
from academic_pdf_en_zh_reader.topology.contracts import (
    TopologyOutcome,
    TopologyStatus,
)


def _write_authored_synthetic_pdf(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setTitle("CC0 synthetic prepare-job fixture")
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(72, 780, "A Synthetic Study of Stable Sensor Calibration")
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(72, 744, "Abstract")
    canvas.setFont("Helvetica", 10)
    lines = (
        "This project-authored paper contains only synthetic observations.",
        "It evaluates three fabricated samples with fixed values of 3, 5, and 7.",
        "No sentence describes a person, publication, or real experiment.",
        "The fixture exists solely to verify deterministic extraction and topology.",
        "Methods",
        "Each synthetic sample follows the same controlled calibration procedure.",
        "The stable sequence preserves facts, quantities, and reading order.",
        "Conclusion",
        "The invented measurements support this software test and nothing else.",
    )
    y = 718
    for line in lines:
        canvas.drawString(72, y, line)
        y -= 24
    canvas.save()


@pytest.fixture
def synthetic_worker_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use real parent APIs and deterministic cores, mocking only the OS worker."""

    from academic_pdf_en_zh_reader.extraction import api as extraction_api
    from academic_pdf_en_zh_reader.normalization import api as normalization_api
    from academic_pdf_en_zh_reader.preflight import api as preflight_api
    from academic_pdf_en_zh_reader.security.windows_worker import (
        _extraction_counts,
        _preflight_limits_for_worker,
    )

    source = tmp_path / "synthetic-paper.pdf"
    _write_authored_synthetic_pdf(source)

    def run_os_worker(request, workspace: Path, *, limits):
        input_path = Path(workspace) / request.input_path
        provenance = {"appcontainer_cleanup_verified": True}
        if request.operation == "preflight":
            preflight = preflight_safe_copy(
                input_path,
                limits=_preflight_limits_for_worker(limits),
            )
            return SimpleNamespace(
                artifact_bytes=canonical_json_bytes(preflight),
                provenance=provenance,
            )
        if request.operation == "normalize":
            preflight = json.loads((Path(workspace) / "preflight.json").read_bytes())
            result = normalize_pdf_bytes(
                input_path.read_bytes(),
                preflight=preflight,
                preflight_sha256=request.parameters["preflight_sha256"],
                max_output_bytes=limits.max_normalized_pdf_bytes,
            )
            return SimpleNamespace(
                normalized_pdf_bytes=result.pdf_bytes,
                normalization_artifact_bytes=canonical_json_bytes(result.artifact),
                provenance=provenance,
            )
        if request.operation == "extract":
            pages = extract_document(input_path)["pages"]
            extraction = {
                "schema_version": "1.0.0",
                "artifact_kind": "extraction",
                "source_sha256": request.parameters["source_sha256"],
                "normalized_pdf_sha256": request.parameters["normalized_pdf_sha256"],
                "preflight_sha256": request.parameters["preflight_sha256"],
                "normalization_sha256": request.parameters["normalization_sha256"],
                "format_version": "1.1.0",
                "counts": _extraction_counts(pages),
                "pages": pages,
            }
            return SimpleNamespace(
                artifact_bytes=canonical_json_bytes(extraction),
                provenance=provenance,
            )
        raise AssertionError(f"unexpected worker operation: {request.operation}")

    monkeypatch.setattr(preflight_api, "_run_platform_worker", run_os_worker)
    monkeypatch.setattr(normalization_api, "_run_platform_worker", run_os_worker)
    monkeypatch.setattr(extraction_api, "_run_platform_worker", run_os_worker)
    return source


def test_prepare_managed_job_runs_real_chain_and_binds_extracted_ledger(
    tmp_path: Path,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.prepare import prepare_managed_job

    managed_root = tmp_path / "managed"
    managed_root.mkdir()

    result = prepare_managed_job(
        managed_root=managed_root,
        job_id="prepare-success-001",
        source_pdf=synthetic_worker_chain,
    )

    job_root = managed_root / "prepare-success-001"
    state = load_job_state(job_root / "job-state.json")
    preflight = (job_root / "preflight.json").read_bytes()
    normalization = (job_root / "normalization.json").read_bytes()
    normalized_pdf = (job_root / "normalized-source.pdf").read_bytes()
    extraction = (job_root / "extraction.json").read_bytes()
    source = (job_root / "source.json").read_bytes()
    units = (job_root / "units.json").read_bytes()

    assert (job_root / SENTINEL_NAME).is_file()
    assert state.stage is JobStage.EXTRACTED
    assert state.artifact_hashes == {
        "preflight": sha256_bytes(preflight),
        "normalization": sha256_bytes(normalization),
        "normalized-pdf": sha256_bytes(normalized_pdf),
        "source": sha256_bytes(source),
        "units": sha256_bytes(units),
    }
    assert state.history[1].artifact_hashes == {
        "preflight": sha256_bytes(preflight),
        "normalization": sha256_bytes(normalization),
        "normalized-pdf": sha256_bytes(normalized_pdf),
    }
    assert result == {
        "job_id": "prepare-success-001",
        "stage": "extracted",
        "source_sha256": state.source_sha256,
        "artifact_hashes": {
            "preflight": sha256_bytes(preflight),
            "normalization": sha256_bytes(normalization),
            "normalized-pdf": sha256_bytes(normalized_pdf),
            "extraction": sha256_bytes(extraction),
            "source": sha256_bytes(source),
            "units": sha256_bytes(units),
        },
        "job_state_hash": state_hash(state),
    }
    assert sha256_canonical(state.to_dict()) == result["job_state_hash"]
    source_artifact = json.loads(source)
    units_artifact = json.loads(units)
    extraction_artifact = json.loads(extraction)
    validate_artifact("source", source_artifact)
    validate_artifact("units", units_artifact)
    assert source_artifact["source_sha256"] == sha256_bytes(
        synthetic_worker_chain.read_bytes()
    )
    assert units_artifact["source_sha256"] == source_artifact["source_sha256"]
    assert source_artifact["normalized_pdf_sha256"] == sha256_bytes(normalized_pdf)
    assert (
        units_artifact["normalized_pdf_sha256"]
        == source_artifact["normalized_pdf_sha256"]
    )
    assert extraction_artifact["source_sha256"] == source_artifact["source_sha256"]
    assert (
        extraction_artifact["normalized_pdf_sha256"]
        == source_artifact["normalized_pdf_sha256"]
    )
    assert extraction_artifact["preflight_sha256"] == sha256_bytes(preflight)
    assert extraction_artifact["normalization_sha256"] == sha256_bytes(normalization)
    blocks = [block for page in source_artifact["pages"] for block in page["blocks"]]
    assert blocks
    assert all(
        type(block["source_font_size_mpt"]) is int and block["source_font_size_mpt"] > 0
        for block in blocks
    )


def test_source_font_size_tamper_breaks_extracted_ledger_binding(
    tmp_path: Path,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration.prepare import prepare_managed_job

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    prepare_managed_job(
        managed_root=managed_root,
        job_id="prepare-font-binding-001",
        source_pdf=synthetic_worker_chain,
    )
    job_root = managed_root / "prepare-font-binding-001"
    state = load_job_state(job_root / "job-state.json")
    source = json.loads((job_root / "source.json").read_bytes())
    tampered = deepcopy(source)
    block = tampered["pages"][0]["blocks"][0]
    block["source_font_size_mpt"] += 1

    validate_artifact("source", tampered)
    assert sha256_canonical(tampered) != state.artifact_hashes["source"]


@pytest.mark.parametrize(
    ("artifact_name", "expected_code"),
    (
        ("preflight.json", "PREFLIGHT_ARTIFACT_INVALID"),
        ("normalization.json", "NORMALIZATION_ARTIFACT_INVALID"),
        ("normalized-source.pdf", "NORMALIZATION_ARTIFACT_INVALID"),
        ("extraction.json", "EXTRACTION_ARTIFACT_INVALID"),
    ),
)
def test_prepare_revalidates_tampered_frozen_chain_and_cleans_managed_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_worker_chain: Path,
    artifact_name: str,
    expected_code: str,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    real_extract = prepare_module.extract_untrusted_pdf

    def extract_then_tamper(source_pdf: Path, job_root: Path, **kwargs: object):
        outcome = real_extract(source_pdf, job_root, **kwargs)
        (Path(job_root) / artifact_name).write_bytes(b"{}")
        return outcome

    monkeypatch.setattr(prepare_module, "extract_untrusted_pdf", extract_then_tamper)

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-tamper-001",
            source_pdf=synthetic_worker_chain,
        )

    assert captured.value.code == expected_code
    assert captured.value.stage == "freeze"
    assert not (managed_root / "prepare-tamper-001").exists()


def test_prepare_cleans_on_topology_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    monkeypatch.setattr(
        prepare_module,
        "build_topology",
        lambda _extraction: TopologyOutcome(
            status=TopologyStatus.NEEDS_TOPOLOGY_REVIEW,
            source=None,
        ),
    )

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-topology-review-001",
            source_pdf=synthetic_worker_chain,
        )

    assert captured.value.code == "NEEDS_TOPOLOGY_REVIEW"
    assert captured.value.stage == "topology"
    assert not (managed_root / "prepare-topology-review-001").exists()


def test_prepare_cleans_on_unit_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    monkeypatch.setattr(
        prepare_module,
        "build_semantic_units",
        lambda _source: UnitMergeOutcome(
            status=UnitMergeStatus.NEEDS_UNIT_REVIEW,
            artifact=None,
            issues=(UnitMergeIssue("left", "right", "ambiguous boundary"),),
        ),
    )

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-unit-review-001",
            source_pdf=synthetic_worker_chain,
        )

    assert captured.value.code == "NEEDS_UNIT_REVIEW"
    assert captured.value.stage == "units"
    assert not (managed_root / "prepare-unit-review-001").exists()


def test_prepare_does_not_clobber_or_clean_an_existing_job(
    tmp_path: Path,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    existing = create_managed_job(managed_root, "prepare-existing-001")
    marker = existing / "keep.txt"
    marker.write_text("owned by an earlier run", encoding="utf-8")

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-existing-001",
            source_pdf=synthetic_worker_chain,
        )

    assert captured.value.code == "JOB_EXISTS"
    assert captured.value.stage == "create"
    assert marker.read_text(encoding="utf-8") == "owned by an earlier run"


def test_prepare_requires_an_existing_managed_root(tmp_path: Path) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    missing_root = tmp_path / "missing-managed-root"

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=missing_root,
            job_id="prepare-missing-root-001",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "INPUT_REJECTED"
    assert captured.value.stage == "create"
    assert not missing_root.exists()


@pytest.mark.parametrize("job_id", ["job.", "job ", "CON", "nul.txt"])
def test_prepare_rejects_noncanonical_windows_job_aliases_before_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()

    def must_not_start_preflight(*_args: object, **_kwargs: object):
        raise AssertionError("invalid job ID reached preflight")

    monkeypatch.setattr(
        prepare_module,
        "preflight_untrusted_pdf",
        must_not_start_preflight,
    )

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id=job_id,
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "INPUT_REJECTED"
    assert captured.value.stage == "create"
    assert list(managed_root.iterdir()) == []


def test_noncanonical_alias_cannot_collide_with_or_clean_a_canonical_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    existing = create_managed_job(managed_root, "canonical-job")
    marker = existing / "keep.txt"
    marker.write_text("owned by the canonical job", encoding="utf-8")

    def must_not_start_preflight(*_args: object, **_kwargs: object):
        raise AssertionError("noncanonical alias reached preflight")

    monkeypatch.setattr(
        prepare_module,
        "preflight_untrusted_pdf",
        must_not_start_preflight,
    )

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="canonical-job.",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "INPUT_REJECTED"
    assert captured.value.stage == "create"
    assert marker.read_text(encoding="utf-8") == "owned by the canonical job"


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive collision")
def test_prepare_case_alias_collision_preserves_existing_job(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    existing = create_managed_job(managed_root, "CaseJob")
    marker = existing / "keep.txt"
    marker.write_text("existing", encoding="utf-8")

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="casejob",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "JOB_EXISTS"
    assert marker.read_text(encoding="utf-8") == "existing"


def test_prepare_interrupt_during_sentinel_write_removes_own_partial_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    real_open = Path.open

    def interrupt_sentinel(path: Path, *args: object, **kwargs: object):
        if path.name == SENTINEL_NAME:
            raise KeyboardInterrupt
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", interrupt_sentinel)

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-create-cancel-001",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "PREPARE_CANCELLED"
    assert captured.value.stage == "create"
    assert not (managed_root / "prepare-create-cancel-001").exists()


def test_interrupted_create_does_not_remove_a_replacement_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    job_root = managed_root / "prepare-create-race-001"
    moved_root = managed_root / "moved-original"
    real_open = Path.open

    def replace_then_interrupt(path: Path, *args: object, **kwargs: object):
        if path.name == SENTINEL_NAME:
            job_root.rename(moved_root)
            job_root.mkdir()
            (job_root / "keep.txt").write_text("replacement", encoding="utf-8")
            raise KeyboardInterrupt
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_then_interrupt)

    with pytest.raises(prepare_module.PrepareJobError):
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-create-race-001",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert (job_root / "keep.txt").read_text(encoding="utf-8") == "replacement"
    assert moved_root.is_dir()


def test_prepare_keyboard_interrupt_uses_cancel_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    observed: dict[str, object] = {}
    real_cleanup = prepare_module.cleanup_after_job

    def cancel_at_preflight(*_args: object, **_kwargs: object):
        raise KeyboardInterrupt

    def capture_cleanup(*args: object, **kwargs: object):
        observed["outcome"] = kwargs["outcome"]
        return real_cleanup(*args, **kwargs)

    monkeypatch.setattr(
        prepare_module,
        "preflight_untrusted_pdf",
        cancel_at_preflight,
    )
    monkeypatch.setattr(prepare_module, "cleanup_after_job", capture_cleanup)

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-cancel-001",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "PREPARE_CANCELLED"
    assert captured.value.stage == "preflight"
    assert observed == {"outcome": "cancel"}
    assert not (managed_root / "prepare-cancel-001").exists()


def test_prepare_preflight_interrupt_cleans_private_safe_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module
    from academic_pdf_en_zh_reader.preflight import api as preflight_api

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    safe_root = tmp_path / "preflight-safe-copy"
    safe_root.mkdir()
    copied = safe_root / "input.pdf"
    copied.write_bytes(b"synthetic private input")
    safe_copy = SafeInputCopy(
        safe_root,
        copied,
        hashlib.sha256(copied.read_bytes()).hexdigest(),
        copied.stat().st_size,
    )

    monkeypatch.setattr(
        preflight_api,
        "copy_untrusted_input",
        lambda *_args, **_kwargs: safe_copy,
    )

    def interrupt_worker(*_args: object, **_kwargs: object):
        raise KeyboardInterrupt

    monkeypatch.setattr(preflight_api, "_run_platform_worker", interrupt_worker)

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-preflight-cancel-001",
            source_pdf=tmp_path / "private-source.pdf",
        )

    assert captured.value.code == "PREPARE_CANCELLED"
    assert captured.value.stage == "preflight"
    assert not safe_root.exists()
    assert not (managed_root / "prepare-preflight-cancel-001").exists()


def test_prepare_extraction_interrupt_cleans_private_safe_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api as extraction_api
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()

    def interrupt_worker(*_args: object, **_kwargs: object):
        raise KeyboardInterrupt

    monkeypatch.setattr(extraction_api, "_run_platform_worker", interrupt_worker)

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-extraction-cancel-001",
            source_pdf=synthetic_worker_chain,
        )

    assert captured.value.code == "PREPARE_CANCELLED"
    assert captured.value.stage == "extraction"
    assert list(tmp_path.glob("safe-copy-*")) == []
    assert not (managed_root / "prepare-extraction-cancel-001").exists()


def test_prepare_state_commit_failure_cleans_all_partial_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_worker_chain: Path,
) -> None:
    from academic_pdf_en_zh_reader.orchestration import prepare as prepare_module

    managed_root = tmp_path / "managed"
    managed_root.mkdir()

    def fail_state_commit(*_args: object, **_kwargs: object):
        raise OSError("private path must not escape")

    monkeypatch.setattr(prepare_module, "write_job_state", fail_state_commit)

    with pytest.raises(prepare_module.PrepareJobError) as captured:
        prepare_module.prepare_managed_job(
            managed_root=managed_root,
            job_id="prepare-state-failure-001",
            source_pdf=synthetic_worker_chain,
        )

    assert captured.value.code == "PREPARE_FAILED"
    assert captured.value.stage == "state"
    assert not (managed_root / "prepare-state-failure-001").exists()
