# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Manage the current user's private ambiguity-correction evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_pdf_en_zh_reader.corrections.commands import (  # noqa: E402
    execute_command,
    export_corrections,
)
from academic_pdf_en_zh_reader.corrections.database import (  # noqa: E402
    CorrectionError,
    CorrectionStore,
    error_message,
)
from academic_pdf_en_zh_reader.corrections.retrieve import (  # noqa: E402
    RetrievalContext,
    retrieve_suggestions,
)

_EXIT_CODES = {
    "AUTHORIZATION_REQUIRED": 2,
    "INVALID_INPUT": 2,
    "INVALID_ANNOTATION": 3,
    "USER_CORRECTION_REQUIRED": 3,
    "RECORD_NOT_FOUND": 4,
    "STORAGE_PERMISSION_DENIED": 5,
    "INVALID_STORAGE_LOCATION": 5,
    "DATABASE_ERROR": 6,
    "SCHEMA_VERSION_UNSUPPORTED": 6,
}


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise CorrectionError("INVALID_INPUT")


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(description="Manage private correction evidence.")
    parser.add_argument("--database", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser(
        "add",
        help="read one correction JSON object from stdin",
    )
    add.add_argument("--authorized", action="store_true")

    listing = subparsers.add_parser("list")
    listing.add_argument("--include-revoked", action="store_true")

    for name in ("revoke", "restore", "delete"):
        mutation = subparsers.add_parser(name)
        mutation.add_argument("record_id")
        mutation.add_argument("--authorized", action="store_true")

    clear = subparsers.add_parser("clear")
    clear.add_argument("--authorized", action="store_true")

    export = subparsers.add_parser("export")
    export.add_argument("destination", type=Path)
    export.add_argument("--authorized", action="store_true")
    export.add_argument("--overwrite", action="store_true")

    suggest = subparsers.add_parser(
        "suggest",
        help="read one minimized retrieval context JSON object from stdin",
    )
    suggest.set_defaults(authorized=False)
    return parser


def _stdin_object() -> Mapping[str, object]:
    value = json.load(sys.stdin)
    if not isinstance(value, Mapping):
        raise ValueError("JSON input must be an object")
    return value


def _suggest(store: CorrectionStore, payload: Mapping[str, object]):
    allowed = {
        "core_collocation",
        "domain",
        "english_expression",
        "related_expressions",
        "semantic_tags",
        "source_part_of_speech",
        "source_syntax",
        "target_grammar_function",
    }
    if set(payload) != allowed:
        raise CorrectionError("INVALID_INPUT")
    related = payload["related_expressions"]
    tags = payload["semantic_tags"]
    if not isinstance(related, (list, tuple)) or not isinstance(tags, (list, tuple)):
        raise CorrectionError("INVALID_INPUT")
    context = RetrievalContext(
        english_expression=payload["english_expression"],  # type: ignore[arg-type]
        domain=payload["domain"],  # type: ignore[arg-type]
        source_part_of_speech=payload["source_part_of_speech"],  # type: ignore[arg-type]
        source_syntax=payload["source_syntax"],  # type: ignore[arg-type]
        target_grammar_function=payload["target_grammar_function"],  # type: ignore[arg-type]
        core_collocation=payload["core_collocation"],  # type: ignore[arg-type]
        semantic_tags=tuple(tags),  # type: ignore[arg-type]
        related_expressions=tuple(related),  # type: ignore[arg-type]
    )
    return {
        "ok": True,
        "result": [item.to_dict() for item in retrieve_suggestions(store, context)],
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        store = CorrectionStore(arguments.database)
        command = arguments.command
        if command == "add":
            outcome = execute_command(
                store,
                "add",
                _stdin_object(),
                authorized=arguments.authorized,
            )
        elif command == "list":
            outcome = execute_command(
                store,
                "list",
                {"include_revoked": arguments.include_revoked},
            )
        elif command in {"revoke", "restore", "delete"}:
            outcome = execute_command(
                store,
                command,
                {"record_id": arguments.record_id},
                authorized=arguments.authorized,
            )
        elif command == "clear":
            outcome = execute_command(
                store,
                "clear",
                authorized=arguments.authorized,
            )
        elif command == "export":
            result = export_corrections(
                store,
                arguments.destination,
                authorized=arguments.authorized,
                overwrite=arguments.overwrite,
            )
            outcome = {"ok": True, "result": result}
        else:
            outcome = _suggest(store, _stdin_object())
    except CorrectionError as exc:
        outcome = {
            "ok": False,
            "error": {"code": exc.code, "message": error_message(exc.code)},
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        outcome = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": error_message("INVALID_INPUT"),
            },
        }
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    if outcome.get("ok") is True:
        return 0
    error = outcome.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    return _EXIT_CODES.get(str(code), 6)


if __name__ == "__main__":
    raise SystemExit(main())
