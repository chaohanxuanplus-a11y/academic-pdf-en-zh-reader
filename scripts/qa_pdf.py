# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Run diagnostic QA or commit production QA without printing document content."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_pdf_en_zh_reader.job.storage import (  # noqa: E402
    write_immutable_artifact,
)
from academic_pdf_en_zh_reader.qa.api import run_mechanical_qa  # noqa: E402
from academic_pdf_en_zh_reader.qa.persist import (  # noqa: E402
    validate_and_persist_qa,
)

_MAX_JSON_BYTES = 256 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a frozen bilingual PDF")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--diagnostic",
        action="store_true",
        help="write a standalone QA report without changing job state",
    )
    mode.add_argument(
        "--persist",
        action="store_true",
        help="commit canonical QA/provenance and CAS the job to VALIDATED",
    )
    parser.add_argument("--source-pdf", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--source-json", type=Path, required=True)
    parser.add_argument("--units-json", type=Path, required=True)
    parser.add_argument("--translation-json", type=Path, required=True)
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--annotations-json", type=Path, required=True)
    parser.add_argument("--frame-graph-json", type=Path, required=True)
    parser.add_argument("--layout-json", type=Path, required=True)
    parser.add_argument("--finalization-receipt-json", type=Path, required=True)
    parser.add_argument("--policy-inputs-json", type=Path, required=True)
    parser.add_argument("--overlay-plan-json", type=Path, required=True)
    parser.add_argument("--render-manifest-json", type=Path, required=True)
    parser.add_argument("--expected-render-manifest-hash", required=True)
    parser.add_argument("--qa-out", type=Path)
    parser.add_argument("--job-root", type=Path)
    parser.add_argument("--expected-rendered-state-hash")
    parser.add_argument("--font-manifest", type=Path)
    return parser


def _mapping(path: Path) -> Mapping[str, object]:
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > _MAX_JSON_BYTES
        ):
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("QA_INPUT_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("QA_INPUT_INVALID")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.diagnostic:
            if (
                args.qa_out is None
                or args.job_root is not None
                or args.expected_rendered_state_hash is not None
            ):
                raise ValueError("QA_MODE_ARGUMENTS_INVALID")
        elif (
            args.qa_out is not None
            or args.job_root is None
            or args.expected_rendered_state_hash is None
        ):
            raise ValueError("QA_MODE_ARGUMENTS_INVALID")
        kwargs: dict[str, object] = {
            "source_pdf_path": args.source_pdf,
            "output_pdf_path": args.output_pdf,
            "source": _mapping(args.source_json),
            "units": _mapping(args.units_json),
            "translation": _mapping(args.translation_json),
            "review": _mapping(args.review_json),
            "annotations": _mapping(args.annotations_json),
            "frame_graph": _mapping(args.frame_graph_json),
            "layout": _mapping(args.layout_json),
            "finalization_receipt": _mapping(args.finalization_receipt_json),
            "policy_inputs": _mapping(args.policy_inputs_json),
            "overlay_plan": _mapping(args.overlay_plan_json),
            "render_manifest": _mapping(args.render_manifest_json),
            "expected_render_manifest_hash": args.expected_render_manifest_hash,
        }
        if args.font_manifest is not None:
            kwargs["font_manifest_path"] = args.font_manifest
        if args.diagnostic:
            qa = run_mechanical_qa(**kwargs)  # type: ignore[arg-type]
            write_immutable_artifact(args.qa_out, qa, "qa")
        else:
            result = validate_and_persist_qa(
                job_root=args.job_root,
                expected_rendered_state_hash=args.expected_rendered_state_hash,
                **kwargs,  # type: ignore[arg-type]
            )
    except Exception:
        print("QA_ERROR")
        return 2

    if args.diagnostic:
        prefix = str(qa["render_manifest_hash"])[:12]
        if qa["passed"]:
            print(f"QA_PASS {prefix}")
            return 0
        print(f"QA_FAILED {prefix}")
        return 1

    if result.passed:
        if result.code not in {"QA_VALIDATED", "QA_ALREADY_VALIDATED"} or not (
            result.qa_hash
        ):
            print("QA_ERROR")
            return 2
        print(f"{result.code} {result.qa_hash[:12]}")
        return 0
    if result.code != "QA_FAILED":
        print("QA_ERROR")
        return 2
    print(f"QA_FAILED {args.expected_render_manifest_hash[:12]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
