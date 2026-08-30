# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Run geometry extraction without logging source paths or PDF content."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_EXIT_CODES = {
    "INPUT_REJECTED": 2,
    "SANDBOX_UNAVAILABLE": 3,
    "SANDBOX_CONTRACT_UNVERIFIED": 3,
    "WORKER_LIMIT_EXCEEDED": 4,
    "WORKER_FAILED": 5,
    "PREFLIGHT_ARTIFACT_INVALID": 6,
    "EXTRACTION_ARTIFACT_INVALID": 7,
    "ARTIFACT_EXISTS": 8,
    "SCANNED_PDF_UNSUPPORTED": 9,
}


def extract_untrusted_pdf(*args, **kwargs):
    from academic_pdf_en_zh_reader.extraction.api import extract_untrusted_pdf as run

    return run(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract one preflighted PDF in the audited Windows sandbox."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("job_root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    outcome = extract_untrusted_pdf(arguments.source, arguments.job_root)
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    if outcome.get("status") == "ok":
        return 0
    error = outcome.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return _EXIT_CODES.get(str(code), 5)


if __name__ == "__main__":
    raise SystemExit(main())
