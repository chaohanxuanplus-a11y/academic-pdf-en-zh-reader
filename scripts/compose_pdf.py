# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Compose one trusted finalized artifact chain into an immutable A3 PDF."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_pdf_en_zh_reader.rendering.compose import (  # noqa: E402
    CompositionError,
    compose_bilingual_pdf,
)


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("input JSON is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("input JSON must contain one object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pdf", type=Path, required=True)
    for name in (
        "source",
        "units",
        "translation",
        "review",
        "annotations",
        "frame-graph",
        "layout",
        "receipt",
        "policies",
        "overlay-plan",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-overlay-plan-hash", required=True)
    parser.add_argument("--expected-finalization-receipt-hash", required=True)
    parser.add_argument("--job-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = compose_bilingual_pdf(
            source_pdf_path=arguments.source_pdf,
            source=_json_object(arguments.source),
            units=_json_object(arguments.units),
            translation=_json_object(arguments.translation),
            review=_json_object(arguments.review),
            annotations=_json_object(arguments.annotations),
            frame_graph=_json_object(arguments.frame_graph),
            layout=_json_object(arguments.layout),
            finalization_receipt=_json_object(arguments.receipt),
            policy_inputs=_json_object(arguments.policies),
            overlay_plan=_json_object(arguments.overlay_plan),
            expected_finalization_receipt_hash=(
                arguments.expected_finalization_receipt_hash
            ),
            expected_overlay_plan_hash=arguments.expected_overlay_plan_hash,
            job_root=arguments.job_root,
            output_pdf_path=arguments.output,
            render_manifest_path=arguments.manifest,
        )
    except CompositionError as exc:
        print(f"composition failed: {exc.code}", file=sys.stderr)
        return 2
    except ValueError:
        print("composition failed: INPUT_INVALID", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output_pdf_sha256": result.output_pdf_sha256,
                "render_manifest_hash": result.render_manifest_hash,
                "page_count": result.page_count,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
