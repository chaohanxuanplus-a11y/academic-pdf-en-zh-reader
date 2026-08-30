# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.review.review_validation import make_ambiguity_key

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "agent_artifacts.py"
MAX_AGENT_ARTIFACT_BYTES = 128 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\n\Z")


def _run(
    *arguments: object,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(value) for value in arguments)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _translation() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": "a" * 64,
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [],
    }


def _review() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": "b" * 64,
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": [],
        "issues": [],
        "final_status": "passed",
    }


def _semantic_candidates() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "semantic-candidates",
        "units_hash": "a" * 64,
        "translation_hash": "b" * 64,
        "review_hash": "c" * 64,
        "red_candidates": [],
        "ambiguity_occurrences": [],
        "teaching_candidates": [],
        "figure_candidates": [],
    }


def _ambiguity_input() -> dict[str, object]:
    return {
        "english_expression": "associated with",
        "syntactic_structure": "past participle predicate with preposition",
        "candidate_meanings": [
            "statistically related",
            "mechanistically connected",
        ],
        "disciplinary_context": "observational biomedical cohort",
        "ambiguity_reason": (
            "The local evidence does not distinguish association from mechanism."
        ),
    }


def test_direct_wrapper_help_lists_both_fixed_subcommands() -> None:
    completed = _run("--help")

    assert completed.returncode == 0, completed.stderr
    assert "canonical-hash" in completed.stdout
    assert "ambiguity-key" in completed.stdout


@pytest.mark.parametrize(
    ("schema_name", "artifact"),
    [
        ("translation", _translation()),
        ("review", _review()),
        ("semantic-candidates", _semantic_candidates()),
    ],
)
def test_canonical_hash_supports_only_the_three_agent_artifact_schemas(
    tmp_path: Path,
    schema_name: str,
    artifact: dict[str, object],
) -> None:
    path = tmp_path / f"{schema_name}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    completed = _run("canonical-hash", "--schema", schema_name, "--input", path)

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout == f"{sha256_canonical(artifact)}\n"


def test_canonical_hash_is_independent_of_json_whitespace_and_key_order(
    tmp_path: Path,
) -> None:
    artifact = _translation()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(artifact, indent=4), encoding="utf-8")
    second.write_text(
        json.dumps(dict(reversed(list(artifact.items()))), separators=(",", ":")),
        encoding="utf-8",
    )

    first_run = _run("canonical-hash", "--schema", "translation", "--input", first)
    second_run = _run("canonical-hash", "--schema", "translation", "--input", second)

    assert first_run.returncode == second_run.returncode == 0
    assert first_run.stdout == second_run.stdout
    assert SHA256.fullmatch(first_run.stdout)


def test_canonical_hash_rejects_symlink_input_without_path_or_trace(
    tmp_path: Path,
) -> None:
    target = tmp_path / "secret-paper.json"
    target.write_text(json.dumps(_translation()), encoding="utf-8")
    link = tmp_path / "linked-agent-input.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"creating symlinks is unavailable: {error}")

    completed = _run("canonical-hash", "--schema", "translation", "--input", link)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "INPUT_UNSAFE\n"


def test_canonical_hash_rejects_input_inside_project_root() -> None:
    completed = _run(
        "canonical-hash",
        "--schema",
        "translation",
        "--input",
        ROOT / "pyproject.toml",
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "PATH_FORBIDDEN\n"


def test_canonical_hash_rejects_relative_input_path(tmp_path: Path) -> None:
    path = tmp_path / "relative-agent-input.json"
    path.write_text(json.dumps(_translation()), encoding="utf-8")

    completed = _run(
        "canonical-hash",
        "--schema",
        "translation",
        "--input",
        path.name,
        cwd=tmp_path,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "PATH_FORBIDDEN\n"


def test_canonical_hash_rejects_oversize_input_before_reading_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversize-secret-paper.json"
    with path.open("wb") as stream:
        stream.truncate(MAX_AGENT_ARTIFACT_BYTES + 1)

    completed = _run("canonical-hash", "--schema", "translation", "--input", path)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "INPUT_TOO_LARGE\n"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_canonical_hash_rejects_nonfinite_json_with_a_stable_error(
    tmp_path: Path,
    constant: str,
) -> None:
    path = tmp_path / "secret-paper.json"
    path.write_text(f'{{"untrusted":{constant}}}', encoding="utf-8")

    completed = _run("canonical-hash", "--schema", "translation", "--input", path)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "INPUT_INVALID\n"
    assert "secret-paper" not in completed.stderr
    assert "Traceback" not in completed.stderr


def test_canonical_hash_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-key.json"
    path.write_text(
        '{"artifact_kind":"translation","artifact_kind":"review"}',
        encoding="utf-8",
    )

    completed = _run("canonical-hash", "--schema", "translation", "--input", path)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "INPUT_INVALID\n"


def test_canonical_hash_schema_failure_emits_only_a_stable_code(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secret-paper.json"
    path.write_text(json.dumps(_translation()), encoding="utf-8")

    completed = _run("canonical-hash", "--schema", "review", "--input", path)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "SCHEMA_INVALID\n"


def test_ambiguity_key_writes_canonical_no_clobber_output_deterministically(
    tmp_path: Path,
) -> None:
    value = _ambiguity_input()
    first_input = tmp_path / "first.json"
    second_input = tmp_path / "second.json"
    first_output = tmp_path / "first-key.json"
    second_output = tmp_path / "second-key.json"
    first_input.write_text(json.dumps(value, indent=2), encoding="utf-8")
    second_input.write_text(
        json.dumps(dict(reversed(list(value.items()))), separators=(",", ":")),
        encoding="utf-8",
    )

    first = _run("ambiguity-key", "--input", first_input, "--output", first_output)
    second = _run("ambiguity-key", "--input", second_input, "--output", second_output)
    expected = make_ambiguity_key(**value)  # type: ignore[arg-type]
    expected_bytes = canonical_json_bytes(expected)

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout == f"{sha256_bytes(expected_bytes)}\n"
    assert first_output.read_bytes() == second_output.read_bytes() == expected_bytes

    before = first_output.read_bytes()
    repeated = _run("ambiguity-key", "--input", first_input, "--output", first_output)
    assert repeated.returncode == 2
    assert repeated.stdout == ""
    assert repeated.stderr == "OUTPUT_EXISTS\n"
    assert first_output.read_bytes() == before


def test_ambiguity_key_rejects_json_path_or_command_fields_as_data(
    tmp_path: Path,
) -> None:
    value = _ambiguity_input()
    value["output_path"] = str(tmp_path / "attacker-selected.json")
    value["command"] = "run something"
    path = tmp_path / "secret-ambiguity.json"
    output = tmp_path / "should-not-exist.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    completed = _run("ambiguity-key", "--input", path, "--output", output)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "AMBIGUITY_INPUT_INVALID\n"
    assert not output.exists()


def test_ambiguity_key_rejects_output_inside_project_root(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ambiguity.json"
    path.write_text(json.dumps(_ambiguity_input()), encoding="utf-8")
    protected = ROOT / "tests" / "skill" / "test_agent_artifacts.py"
    before = protected.read_bytes()

    completed = _run("ambiguity-key", "--input", path, "--output", protected)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "PATH_FORBIDDEN\n"
    assert protected.read_bytes() == before


def test_ambiguity_key_rejects_relative_output_path(tmp_path: Path) -> None:
    path = tmp_path / "ambiguity.json"
    path.write_text(json.dumps(_ambiguity_input()), encoding="utf-8")
    output = tmp_path / "relative-agent-output.json"

    completed = _run(
        "ambiguity-key",
        "--input",
        path,
        "--output",
        output.name,
        cwd=tmp_path,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "PATH_FORBIDDEN\n"
    assert not output.exists()
