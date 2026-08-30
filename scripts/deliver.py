# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Atomically publish one fully validated PDF without logging document data."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_EXIT_CODES = {
    "DELIVERY_NOT_VALIDATED": 2,
    "DELIVERY_BINDING_MISMATCH": 3,
    "OUTPUT_PATH_CONFLICT": 4,
    "OUTPUT_PATH_UNSAFE": 4,
    "OUTPUT_EXISTS": 5,
    "ATOMIC_DELIVERY_UNAVAILABLE": 6,
    "DELIVERY_STAGE_FAILED": 7,
    "DELIVERY_COMMIT_FAILED": 8,
    "DELIVERY_VERIFY_FAILED": 9,
    "DELIVERY_STATE_FAILED": 10,
    "DELIVERY_CANCELLED": 10,
    "RETENTION_POLICY_INVALID": 11,
    "CLEANUP_FAILED": 12,
}


def deliver_validated_pdf(*args, **kwargs):
    from academic_pdf_en_zh_reader.job.deliver import (
        deliver_validated_pdf as run,
    )

    return run(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a validated PDF with no-clobber atomic delivery."
    )
    parser.add_argument("managed_root", type=Path)
    parser.add_argument("job_root", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("render_manifest", type=Path)
    parser.add_argument("qa", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--retain", choices=("resume", "debug"))
    parser.add_argument("--ttl-seconds", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = deliver_validated_pdf(
        managed_root=arguments.managed_root,
        job_root=arguments.job_root,
        state_path=arguments.state,
        source_path=arguments.source,
        candidate_path=arguments.candidate,
        render_manifest_path=arguments.render_manifest,
        qa_path=arguments.qa,
        output_path=arguments.output,
        retention_mode=arguments.retain,
        ttl_seconds=arguments.ttl_seconds,
    )
    print(
        json.dumps(
            {
                "cleanup_code": result.cleanup_code,
                "code": result.code,
                "delivered": result.delivered,
                "output_sha256": result.output_sha256,
                "status": result.status,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result.status == "ok" else _EXIT_CODES.get(result.code, 13)


if __name__ == "__main__":
    raise SystemExit(main())
