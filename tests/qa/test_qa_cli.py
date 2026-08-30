# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from academic_pdf_en_zh_reader.qa.persist import QaCommitResult


def _load_cli():
    script = Path(__file__).resolve().parents[2] / "scripts" / "qa_pdf.py"
    spec = importlib.util.spec_from_file_location("qa_pdf_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_wrapper_help_loads_repository_package() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "qa_pdf.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0
    assert "--diagnostic" in completed.stdout
    assert "--persist" in completed.stdout


def _argv(*, mode: str = "diagnostic") -> list[str]:
    values = {
        "source-pdf": "secret-source.pdf",
        "output-pdf": "secret-output.pdf",
        "source-json": "source.json",
        "units-json": "units.json",
        "translation-json": "translation.json",
        "review-json": "review.json",
        "annotations-json": "annotations.json",
        "frame-graph-json": "frame-graph.json",
        "layout-json": "layout.json",
        "finalization-receipt-json": "receipt.json",
        "policy-inputs-json": "policies.json",
        "overlay-plan-json": "overlay-plan.json",
        "render-manifest-json": "render-manifest.json",
        "expected-render-manifest-hash": "a" * 64,
    }
    argv = [part for key, value in values.items() for part in (f"--{key}", value)]
    if mode == "diagnostic":
        return ["--diagnostic", *argv, "--qa-out", "qa.json"]
    return [
        "--persist",
        *argv,
        "--job-root",
        "job-root",
        "--expected-rendered-state-hash",
        "d" * 64,
    ]


def test_cli_prints_only_status_and_hash_prefix(capsys, monkeypatch) -> None:
    cli = _load_cli()
    monkeypatch.setattr(cli, "_mapping", lambda _path: {})
    monkeypatch.setattr(
        cli,
        "run_mechanical_qa",
        lambda **_kwargs: {
            "passed": True,
            "render_manifest_hash": "b" * 64,
        },
    )
    monkeypatch.setattr(cli, "write_immutable_artifact", lambda *_args: "c" * 64)

    assert cli.main(_argv()) == 0
    captured = capsys.readouterr()
    assert captured.out == f"QA_PASS {'b' * 12}\n"
    assert "secret" not in captured.out + captured.err


def test_cli_failure_never_echoes_paths_or_exception_text(capsys, monkeypatch) -> None:
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "_mapping",
        lambda _path: (_ for _ in ()).throw(ValueError("secret paper text")),
    )

    assert cli.main(_argv()) == 2
    captured = capsys.readouterr()
    assert captured.out == "QA_ERROR\n"
    assert "secret" not in captured.out + captured.err


def test_cli_persist_mode_uses_stateful_commit_without_arbitrary_qa_output(
    capsys, monkeypatch
) -> None:
    cli = _load_cli()
    monkeypatch.setattr(cli, "_mapping", lambda _path: {})
    called: dict[str, object] = {}

    def persist(**kwargs):
        called.update(kwargs)
        return QaCommitResult(
            code="QA_VALIDATED",
            passed=True,
            qa_hash="e" * 64,
            provenance_hash="f" * 64,
            validated_state_hash="a" * 64,
        )

    monkeypatch.setattr(cli, "validate_and_persist_qa", persist)
    monkeypatch.setattr(
        cli,
        "run_mechanical_qa",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pure QA called")),
    )
    monkeypatch.setattr(
        cli,
        "write_immutable_artifact",
        lambda *_args: (_ for _ in ()).throw(AssertionError("arbitrary write called")),
    )

    assert cli.main(_argv(mode="persist")) == 0
    captured = capsys.readouterr()
    assert captured.out == f"QA_VALIDATED {'e' * 12}\n"
    assert called["job_root"] == Path("job-root")
    assert called["expected_rendered_state_hash"] == "d" * 64
    assert "secret" not in captured.out + captured.err
