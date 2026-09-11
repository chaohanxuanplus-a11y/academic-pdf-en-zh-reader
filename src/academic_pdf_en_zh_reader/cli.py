# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed stage commands for the bilingual academic-PDF pipeline."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.job.state import JobStage, JobState, JobStateError
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    load_job_state,
    write_immutable_artifact,
)
from academic_pdf_en_zh_reader.layout.solver import (
    LayoutSolverError,
    solve_layout,
    validate_layout_against_frame_graph,
)
from academic_pdf_en_zh_reader.orchestration.prepare import (
    PrepareJobError,
    prepare_managed_job,
)
from academic_pdf_en_zh_reader.review.review_validation import (
    ReviewGateResult,
    ReviewValidationError,
    validate_review,
)
from academic_pdf_en_zh_reader.review.semantic_checks import (
    unresolved_mechanical_issues,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    TranslationValidationError,
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CliStageError(ValueError):
    """One stable, content-free reason a stage command stopped."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CliStageError("INPUT_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise CliStageError("INPUT_JSON_INVALID")
    return value


def _job_state(path: Path) -> JobState:
    try:
        return load_job_state(path)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        JobStateError,
        KeyError,
        SchemaValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise CliStageError("JOB_STATE_INVALID") from exc


def _verify_stage_ledger(
    state: JobState,
    expected_stage: JobStage,
    artifacts: Mapping[str, Mapping[str, object]],
) -> None:
    if not isinstance(state, JobState) or state.stage is not expected_stage:
        raise CliStageError("JOB_STAGE_INVALID")
    ledger = state.artifact_hashes
    try:
        for name, artifact in artifacts.items():
            if ledger.get(name) != sha256_canonical(artifact):
                raise CliStageError("JOB_PARENT_MISMATCH")
        units = artifacts["units"]
        translation = artifacts["translation"]
        if (
            units.get("source_sha256") != state.source_sha256
            or translation.get("translation_revision") != state.translation_revision
        ):
            raise CliStageError("JOB_PARENT_MISMATCH")
    except CliStageError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CliStageError("JOB_PARENT_MISMATCH") from exc


def _validate_reviewed_chain(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
) -> ReviewGateResult:
    try:
        validate_translation_artifact(units, translation)
    except TranslationValidationError as exc:
        raise CliStageError("TRANSLATION_INVALID") from exc
    try:
        mechanical_issues = unresolved_mechanical_issues(units, translation, review)
    except TranslationValidationError as exc:
        raise CliStageError("TRANSLATION_INVALID") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise CliStageError("REVIEW_REQUIRED") from exc
    if mechanical_issues:
        raise CliStageError("MECHANICAL_SEMANTIC_MISMATCH")
    try:
        return validate_review(translation, review)
    except ReviewValidationError as exc:
        raise CliStageError("REVIEW_REQUIRED") from exc


def validate_translation_stage(
    state: JobState,
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
) -> dict[str, object]:
    """Validate one translated-stage candidate review without writing artifacts."""

    _verify_stage_ledger(
        state,
        JobStage.TRANSLATED,
        {"units": units, "translation": translation},
    )
    gate = _validate_reviewed_chain(units, translation, review)
    return {
        "translation_hash": gate.translation_hash,
        "review_hash": sha256_canonical(review),
        "reviewed_unit_count": len(gate.reviewed_unit_ids),
        "unresolved_ambiguity_count": len(gate.unresolved_ambiguity_keys),
        "mechanical_issue_count": 0,
    }


def solve_layout_stage(
    state: JobState,
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    *,
    expected_solver_input_hash: str,
) -> dict[str, object]:
    """Solve one already frozen annotated graph at the exact annotated stage."""

    _verify_stage_ledger(
        state,
        JobStage.ANNOTATED,
        {
            "units": units,
            "translation": translation,
            "review": review,
            "annotations": annotations,
        },
    )
    _validate_reviewed_chain(units, translation, review)
    if not isinstance(expected_solver_input_hash, str) or not _SHA256.fullmatch(
        expected_solver_input_hash
    ):
        raise CliStageError("LAYOUT_EXPECTED_HASH_INVALID")
    try:
        validate_artifact("annotations", annotations)
        validate_artifact("frame-graph", frame_graph)
        if (
            annotations.get("units_hash") != sha256_canonical(units)
            or annotations.get("translation_hash") != sha256_canonical(translation)
            or annotations.get("review_hash") != sha256_canonical(review)
        ):
            raise CliStageError("LAYOUT_PARENT_MISMATCH")
        binding = frame_graph.get("annotation_binding")
        if not isinstance(binding, Mapping) or (
            binding.get("kind") != "final"
            or binding.get("parent_hash") != sha256_canonical(annotations)
            or binding.get("selection_hash") != annotations.get("selection_hash")
        ):
            raise CliStageError("LAYOUT_PARENT_MISMATCH")
        layout = solve_layout(
            frame_graph,
            expected_solver_input_hash=expected_solver_input_hash,
        )
        validate_layout_against_frame_graph(frame_graph, layout)
        return layout
    except CliStageError:
        raise
    except (KeyError, LayoutSolverError, SchemaValidationError, TypeError) as exc:
        raise CliStageError("LAYOUT_SOLVE_FAILED") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare-job",
        help="create one managed EXTRACTED job from an untrusted academic PDF",
    )
    prepare.add_argument("--managed-root", type=Path, required=True)
    prepare.add_argument("--job-id", required=True)
    prepare.add_argument("--source-pdf", type=Path, required=True)

    translation = commands.add_parser(
        "validate-translation",
        help="validate a translated-stage translation and independent review",
    )
    for name in ("job-state", "units", "translation", "review"):
        translation.add_argument(f"--{name}", type=Path, required=True)

    layout = commands.add_parser(
        "solve-layout",
        help="solve one trusted annotated FrameGraph without rendering",
    )
    for name in (
        "job-state",
        "units",
        "translation",
        "review",
        "annotations",
        "frame-graph",
    ):
        layout.add_argument(f"--{name}", type=Path, required=True)
    layout.add_argument("--expected-solver-input-hash", required=True)
    layout.add_argument("--output", type=Path, required=True)
    return parser


def _emit(value: Mapping[str, object], *, stream) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=stream,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "prepare-job":
            result = prepare_managed_job(
                managed_root=arguments.managed_root,
                job_id=arguments.job_id,
                source_pdf=arguments.source_pdf,
            )
        else:
            state = _job_state(arguments.job_state)
            units = _json_object(arguments.units)
            translation = _json_object(arguments.translation)
            review = _json_object(arguments.review)
            if arguments.command == "validate-translation":
                result = validate_translation_stage(state, units, translation, review)
            else:
                annotations = _json_object(arguments.annotations)
                frame_graph = _json_object(arguments.frame_graph)
                layout = solve_layout_stage(
                    state,
                    units,
                    translation,
                    review,
                    annotations,
                    frame_graph,
                    expected_solver_input_hash=arguments.expected_solver_input_hash,
                )
                try:
                    layout_hash = write_immutable_artifact(
                        arguments.output,
                        layout,
                        "layout",
                    )
                except ArtifactExistsError as exc:
                    raise CliStageError("OUTPUT_EXISTS") from exc
                except (OSError, SchemaValidationError, TypeError, ValueError) as exc:
                    raise CliStageError("OUTPUT_WRITE_FAILED") from exc
                trace = layout["solver_trace"]
                result = {
                    "layout_hash": layout_hash,
                    "solver_input_hash": layout["solver_input_hash"],
                    "page_count": len(layout["pages"]),
                    "continuation_page_count": trace["continuation_page_count"],
                }
    except PrepareJobError as exc:
        error: dict[str, object] = {
            "code": exc.code,
            "stage": exc.stage,
        }
        if exc.job_id is not None:
            error["job_id"] = exc.job_id
        _emit(
            {"status": "error", "command": "prepare-job", "error": error},
            stream=sys.stderr,
        )
        return 2
    except CliStageError as exc:
        _emit(
            {"status": "error", "error": {"code": exc.code}},
            stream=sys.stderr,
        )
        return 2
    _emit(
        {"status": "ok", "command": arguments.command, "result": result},
        stream=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CliStageError",
    "main",
    "solve_layout_stage",
    "validate_translation_stage",
]
