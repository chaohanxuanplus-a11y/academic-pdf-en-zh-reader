# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.cli import (
    CliStageError,
    main,
    solve_layout_stage,
    validate_translation_stage,
)
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)


def test_translation_gate_requires_two_independent_roles(
    reviewed_translation_bundle,
) -> None:
    units, translation, review, state = reviewed_translation_bundle
    review["reviewer_id"] = review["translator_id"]

    with pytest.raises(CliStageError) as raised:
        validate_translation_stage(state, units, translation, review)

    assert raised.value.code == "REVIEW_REQUIRED"


def test_translation_gate_requires_the_exact_translated_stage(
    reviewed_translation_bundle,
) -> None:
    units, translation, review, _state = reviewed_translation_bundle
    initialized = create_job(
        job_id="wrong-stage",
        source_sha256=str(units["source_sha256"]),
        translation_revision=1,
    )

    with pytest.raises(CliStageError) as raised:
        validate_translation_stage(initialized, units, translation, review)

    assert raised.value.code == "JOB_STAGE_INVALID"


def test_cli_failure_is_nonzero_structured_and_path_free(
    tmp_path, capsys, reviewed_translation_bundle
) -> None:
    units, translation, review, state = reviewed_translation_bundle
    review["reviewer_id"] = review["translator_id"]
    paths = {}
    for name, value in (
        ("state", state.to_dict()),
        ("units", units),
        ("translation", translation),
        ("review", review),
    ):
        path = tmp_path / f"private-secret-{name}.json"
        path.write_bytes(canonical_json_bytes(value))
        paths[name] = path

    result = main(
        [
            "validate-translation",
            "--job-state",
            str(paths["state"]),
            "--units",
            str(paths["units"]),
            "--translation",
            str(paths["translation"]),
            "--review",
            str(paths["review"]),
        ]
    )
    captured = capsys.readouterr()

    assert result != 0
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["error"]["code"] == "REVIEW_REQUIRED"
    assert "private-secret" not in captured.err
    assert "Alpha protein" not in captured.err


def test_layout_failure_never_writes_a_partial_output(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "layout.json"

    def fail(*_args, **_kwargs):
        raise CliStageError("LAYOUT_SOLVE_FAILED")

    monkeypatch.setattr(
        "academic_pdf_en_zh_reader.cli._job_state", lambda _path: object()
    )
    monkeypatch.setattr("academic_pdf_en_zh_reader.cli._json_object", lambda _path: {})
    monkeypatch.setattr("academic_pdf_en_zh_reader.cli.solve_layout_stage", fail)
    result = main(
        [
            "solve-layout",
            "--job-state",
            str(tmp_path / "state.json"),
            "--units",
            str(tmp_path / "units.json"),
            "--translation",
            str(tmp_path / "translation.json"),
            "--review",
            str(tmp_path / "review.json"),
            "--annotations",
            str(tmp_path / "annotations.json"),
            "--frame-graph",
            str(tmp_path / "frame-graph.json"),
            "--expected-solver-input-hash",
            "a" * 64,
            "--output",
            str(output),
        ]
    )

    assert result != 0
    assert not output.exists()
    assert json.loads(capsys.readouterr().err)["error"]["code"] == (
        "LAYOUT_SOLVE_FAILED"
    )


def test_translation_parent_tamper_is_rejected(reviewed_translation_bundle) -> None:
    units, translation, review, state = reviewed_translation_bundle
    tampered = deepcopy(translation)
    tampered["units"][0]["chinese_text"] = "被替换的译文。"

    with pytest.raises(CliStageError) as raised:
        validate_translation_stage(state, units, tampered, review)

    assert raised.value.code == "JOB_PARENT_MISMATCH"


def test_layout_stage_consumes_only_the_frozen_annotated_chain(
    monkeypatch, reviewed_translation_bundle
) -> None:
    units, translation, review, state = reviewed_translation_bundle
    annotations = {
        "units_hash": sha256_canonical(units),
        "translation_hash": sha256_canonical(translation),
        "review_hash": sha256_canonical(review),
        "orange_selection_hash": "d" * 64,
        "selection_hash": "f" * 64,
    }
    for stage, hashes in (
        (JobStage.REVIEWED, {"review": sha256_canonical(review)}),
        (JobStage.ANNOTATED, {"annotations": sha256_canonical(annotations)}),
    ):
        state = advance_job(
            state,
            stage,
            hashes,
            expected_previous_state_hash=state_hash(state),
        )
    frame_graph = {
        "annotation_binding": {
            "kind": "final",
            "parent_hash": sha256_canonical(annotations),
            "selection_hash": annotations["selection_hash"],
        }
    }
    expected_layout = {"artifact_kind": "layout"}
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "academic_pdf_en_zh_reader.cli.validate_artifact",
        lambda *_args, **_kwargs: None,
    )

    def solve(_frame_graph, *, expected_solver_input_hash):
        observed["expected_hash"] = expected_solver_input_hash
        return expected_layout

    monkeypatch.setattr("academic_pdf_en_zh_reader.cli.solve_layout", solve)
    monkeypatch.setattr(
        "academic_pdf_en_zh_reader.cli.validate_layout_against_frame_graph",
        lambda *_args, **_kwargs: None,
    )

    result = solve_layout_stage(
        state,
        units,
        translation,
        review,
        annotations,
        frame_graph,
        expected_solver_input_hash="e" * 64,
    )

    assert result is expected_layout
    assert observed["expected_hash"] == "e" * 64
