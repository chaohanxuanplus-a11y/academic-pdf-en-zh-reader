# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_pdf_help_runs_as_a_direct_script() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compose_pdf.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--source-pdf" in completed.stdout
    assert "--expected-finalization-receipt-hash" in completed.stdout


def test_prepare_job_help_runs_as_a_direct_script() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prepare_job.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--managed-root" in completed.stdout
    assert "--job-id" in completed.stdout
    assert "--source-pdf" in completed.stdout


def test_finish_job_help_runs_as_a_direct_script() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "finish_job.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    for option in (
        "--managed-root",
        "--job-id",
        "--source-pdf",
        "--translation-json",
        "--review-json",
        "--semantic-candidates-json",
        "--output-pdf",
    ):
        assert option in completed.stdout
