# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_cli():
    script = Path(__file__).resolve().parents[2] / "scripts" / "preflight.py"
    spec = importlib.util.spec_from_file_location("preflight_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_emits_only_structured_summary(capsys, monkeypatch) -> None:
    cli = _load_cli()
    secret = Path("C:/private/research/secret-paper.pdf")
    monkeypatch.setattr(
        cli,
        "preflight_untrusted_pdf",
        lambda *_args, **_kw: {
            "status": "ok",
            "artifact": {"name": "preflight.json", "sha256": "a" * 64},
            "summary": {"page_count": 3, "warning_count": 1},
        },
    )

    exit_code = cli.main([str(secret), "C:/private/job"])
    output = capsys.readouterr()
    parsed = json.loads(output.out)

    assert exit_code == 0
    assert parsed["summary"] == {"page_count": 3, "warning_count": 1}
    assert "secret-paper" not in output.out
    assert output.err == ""


def test_cli_failure_uses_stable_exit_code_without_trace(capsys, monkeypatch) -> None:
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "preflight_untrusted_pdf",
        lambda *_args, **_kw: {
            "status": "error",
            "error": {
                "code": "SANDBOX_UNAVAILABLE",
                "message": "The required isolated PDF worker is unavailable.",
            },
        },
    )

    exit_code = cli.main(["secret.pdf", "private-job"])
    output = capsys.readouterr()

    assert exit_code == 3
    assert json.loads(output.out)["error"]["code"] == "SANDBOX_UNAVAILABLE"
    assert "Traceback" not in output.out + output.err
    assert "secret.pdf" not in output.out + output.err
