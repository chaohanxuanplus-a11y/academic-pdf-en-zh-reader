# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Finish one extracted bilingual PDF job without exposing document content."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_pdf_en_zh_reader.orchestration.finish import (  # noqa: E402
    FinishJobError,
    finish_managed_job,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--translation-json", type=Path, required=True)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--semantic-candidates-json", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument(
        "--retain-debug",
        action="store_true",
        help="retain private job artifacts for at most 24 hours",
    )
    parser.add_argument("--ttl-seconds", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.ttl_seconds is not None and not arguments.retain_debug:
        parser.error("--ttl-seconds requires --retain-debug")
    try:
        result = finish_managed_job(
            managed_root=arguments.managed_root,
            job_id=arguments.job_id,
            source_pdf=arguments.source_pdf,
            translation_json=arguments.translation_json,
            review_json=arguments.review_json,
            semantic_candidates_json=arguments.semantic_candidates_json,
            output_pdf=arguments.output_pdf,
            retain_debug=arguments.retain_debug,
            ttl_seconds=arguments.ttl_seconds,
        )
    except FinishJobError as exc:
        print(f"FINISH_ERROR {exc.stage} {exc.code}", file=sys.stderr)
        if getattr(exc, "failure_codes", ()):
            print("QA_FAILURE " + ",".join(exc.failure_codes), file=sys.stderr)
        if getattr(exc, "recovery_action", None):
            print(
                f"RECOVER {exc.recovery_action}; "
                "reuse extracted checkpoint and unchanged translation",
                file=sys.stderr,
            )
        return 2
    if result.get("added_pages", 0):
        print(
            "精简非主要补充后，完整译文、核心补充与责任声明"
            f"仍需增加 {result['added_pages']} 页。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
