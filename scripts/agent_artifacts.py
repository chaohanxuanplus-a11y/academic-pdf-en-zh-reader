# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build and hash the two bounded Agent-owned JSON artifact forms."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_pdf_en_zh_reader.job.canonical_json import (  # noqa: E402
    CanonicalJsonError,
    canonical_json_bytes,
)
from academic_pdf_en_zh_reader.job.hashing import (  # noqa: E402
    sha256_bytes,
    sha256_canonical,
)
from academic_pdf_en_zh_reader.job.storage import (  # noqa: E402
    ArtifactExistsError,
    write_immutable_bytes,
)
from academic_pdf_en_zh_reader.review.review_validation import (  # noqa: E402
    ReviewValidationError,
    make_ambiguity_key,
)
from academic_pdf_en_zh_reader.schema.validate import (  # noqa: E402
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.security.input_copy import (  # noqa: E402
    UnsafeInputError,
    read_bounded_regular_file,
)

MAX_AGENT_ARTIFACT_BYTES = 128 * 1024 * 1024
_AGENT_SCHEMAS = ("translation", "review", "semantic-candidates")
_AMBIGUITY_FIELDS = frozenset(
    {
        "english_expression",
        "syntactic_structure",
        "candidate_meanings",
        "disciplinary_context",
        "ambiguity_reason",
    }
)


class AgentArtifactCliError(ValueError):
    """One stable, content-free CLI failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise AgentArtifactCliError("ARGUMENTS_INVALID")


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = child
    return value


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_inside_project(path: Path) -> bool:
    try:
        lexical = _absolute_without_resolving(path)
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise AgentArtifactCliError("PATH_FORBIDDEN") from exc
    return any(
        candidate == ROOT or candidate.is_relative_to(ROOT)
        for candidate in (lexical, resolved)
    )


def _require_external_path(path: Path) -> None:
    if not path.is_absolute():
        raise AgentArtifactCliError("PATH_FORBIDDEN")
    if _is_inside_project(path):
        raise AgentArtifactCliError("PATH_FORBIDDEN")


def _looks_like_ordinary_file(path: Path) -> os.stat_result:
    try:
        information = path.lstat()
    except OSError as exc:
        raise AgentArtifactCliError("INPUT_UNSAFE") from exc
    attributes = getattr(information, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(information.st_mode)
        or attributes & reparse_flag
        or not stat.S_ISREG(information.st_mode)
    ):
        raise AgentArtifactCliError("INPUT_UNSAFE")
    return information


def _load_bounded_json(path: Path) -> dict[str, object]:
    _require_external_path(path)
    information = _looks_like_ordinary_file(path)
    if information.st_size > MAX_AGENT_ARTIFACT_BYTES:
        raise AgentArtifactCliError("INPUT_TOO_LARGE")
    try:
        raw = read_bounded_regular_file(
            path,
            max_bytes=MAX_AGENT_ARTIFACT_BYTES,
        ).data
    except UnsafeInputError as exc:
        raise AgentArtifactCliError("INPUT_UNSAFE") from exc
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AgentArtifactCliError("INPUT_INVALID") from exc
    if not isinstance(value, dict):
        raise AgentArtifactCliError("INPUT_INVALID")
    return value


def _canonical_hash(arguments: argparse.Namespace) -> str:
    value = _load_bounded_json(arguments.input)
    try:
        validate_artifact(arguments.schema, value)
        return sha256_canonical(value)
    except SchemaValidationError as exc:
        raise AgentArtifactCliError("SCHEMA_INVALID") from exc
    except CanonicalJsonError as exc:
        raise AgentArtifactCliError("INPUT_INVALID") from exc


def _ambiguity_key(arguments: argparse.Namespace) -> str:
    _require_external_path(arguments.output)
    value = _load_bounded_json(arguments.input)
    if frozenset(value) != _AMBIGUITY_FIELDS:
        raise AgentArtifactCliError("AMBIGUITY_INPUT_INVALID")
    try:
        ambiguity_key = make_ambiguity_key(
            english_expression=value["english_expression"],
            syntactic_structure=value["syntactic_structure"],
            candidate_meanings=value["candidate_meanings"],
            disciplinary_context=value["disciplinary_context"],
            ambiguity_reason=value["ambiguity_reason"],
        )
        encoded = canonical_json_bytes(ambiguity_key)
    except (ReviewValidationError, CanonicalJsonError, TypeError, ValueError) as exc:
        raise AgentArtifactCliError("AMBIGUITY_INPUT_INVALID") from exc
    try:
        digest = write_immutable_bytes(arguments.output, encoded)
    except ArtifactExistsError as exc:
        raise AgentArtifactCliError("OUTPUT_EXISTS") from exc
    except (OSError, ValueError) as exc:
        raise AgentArtifactCliError("OUTPUT_WRITE_FAILED") from exc
    if digest != sha256_bytes(encoded):
        raise AgentArtifactCliError("OUTPUT_WRITE_FAILED")
    return digest


def _parser() -> argparse.ArgumentParser:
    parser = _StableArgumentParser(
        description="Hash or build bounded Agent JSON artifacts.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    canonical_hash = commands.add_parser(
        "canonical-hash",
        help="Validate one Agent artifact and print its canonical SHA-256.",
        allow_abbrev=False,
    )
    canonical_hash.add_argument("--schema", choices=_AGENT_SCHEMAS, required=True)
    canonical_hash.add_argument("--input", type=Path, required=True)
    canonical_hash.set_defaults(handler=_canonical_hash)

    ambiguity_key = commands.add_parser(
        "ambiguity-key",
        help="Build one canonical ambiguity key without replacing output.",
        allow_abbrev=False,
    )
    ambiguity_key.add_argument("--input", type=Path, required=True)
    ambiguity_key.add_argument("--output", type=Path, required=True)
    ambiguity_key.set_defaults(handler=_ambiguity_key)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        handler: Callable[[argparse.Namespace], str] = arguments.handler
        digest = handler(arguments)
        sys.stdout.write(f"{digest}\n")
        return 0
    except AgentArtifactCliError as exc:
        sys.stderr.write(f"{exc.code}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("CANCELLED\n")
        return 130
    except Exception:
        sys.stderr.write("INTERNAL_ERROR\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
