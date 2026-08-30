# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_cli():
    script = Path(__file__).resolve().parents[2] / "scripts" / "extract.py"
    spec = importlib.util.spec_from_file_location("extract_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_emits_only_structured_summary(capsys, monkeypatch) -> None:
    cli = _load_cli()
    secret = Path("C:/private/research/secret-paper.pdf")
    monkeypatch.setattr(
        cli,
        "extract_untrusted_pdf",
        lambda *_args, **_kw: {
            "status": "ok",
            "artifact": {"name": "extraction.json", "sha256": "a" * 64},
            "summary": {"page_count": 3, "line_count": 42},
        },
    )

    exit_code = cli.main([str(secret), "C:/private/job"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(output.out)["summary"]["line_count"] == 42
    assert "secret-paper" not in output.out
    assert output.err == ""


def test_cli_failure_is_stable_and_path_free(capsys, monkeypatch) -> None:
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "extract_untrusted_pdf",
        lambda *_args, **_kw: {
            "status": "error",
            "error": {
                "code": "PREFLIGHT_ARTIFACT_INVALID",
                "message": "The passing preflight artifact is invalid.",
            },
        },
    )

    exit_code = cli.main(["secret.pdf", "private-job"])
    output = capsys.readouterr()

    assert exit_code == 6
    assert "secret.pdf" not in output.out + output.err
    assert "Traceback" not in output.out + output.err


def test_cli_uses_dedicated_exit_code_for_scanned_pdf(capsys, monkeypatch) -> None:
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "extract_untrusted_pdf",
        lambda *_args, **_kw: {
            "status": "error",
            "error": {
                "code": "SCANNED_PDF_UNSUPPORTED",
                "message": "Scanned or OCR-overlay PDFs are not supported.",
            },
        },
    )

    exit_code = cli.main(["secret.pdf", "private-job"])
    output = capsys.readouterr()

    assert exit_code == 9
    assert json.loads(output.out)["error"]["code"] == "SCANNED_PDF_UNSUPPORTED"
    assert "secret.pdf" not in output.out + output.err
