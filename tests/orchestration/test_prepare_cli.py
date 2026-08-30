# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from academic_pdf_en_zh_reader import cli
from academic_pdf_en_zh_reader.orchestration.prepare import PrepareJobError


def test_prepare_cli_success_emits_only_hash_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "prepare_managed_job",
        lambda **_kwargs: {
            "job_id": "safe-job-001",
            "stage": "extracted",
            "source_sha256": "a" * 64,
            "artifact_hashes": {
                "preflight": "b" * 64,
                "normalization": "1" * 64,
                "normalized-pdf": "2" * 64,
                "extraction": "c" * 64,
                "source": "d" * 64,
                "units": "e" * 64,
            },
            "job_state_hash": "f" * 64,
        },
    )

    exit_code = cli.main(
        [
            "prepare-job",
            "--managed-root",
            "C:/private/managed",
            "--job-id",
            "safe-job-001",
            "--source-pdf",
            "C:/private/secret-paper.pdf",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["result"]["stage"] == "extracted"
    assert "secret-paper" not in captured.out
    assert "C:/private" not in captured.out


def test_prepare_cli_cancel_hides_cause_and_paths(monkeypatch, capsys) -> None:
    def cancel(**_kwargs: object):
        try:
            raise KeyboardInterrupt("C:/private/secret-paper.pdf")
        except KeyboardInterrupt as cause:
            raise PrepareJobError(
                "PREPARE_CANCELLED",
                "preflight",
                job_id="safe-job-002",
            ) from cause

    monkeypatch.setattr(cli, "prepare_managed_job", cancel)

    exit_code = cli.main(
        [
            "prepare-job",
            "--managed-root",
            "C:/private/managed",
            "--job-id",
            "safe-job-002",
            "--source-pdf",
            "C:/private/secret-paper.pdf",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code != 0
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["error"] == {
        "code": "PREPARE_CANCELLED",
        "job_id": "safe-job-002",
        "stage": "preflight",
    }
    assert "secret-paper" not in captured.err
    assert "KeyboardInterrupt" not in captured.err
    assert "Traceback" not in captured.err
