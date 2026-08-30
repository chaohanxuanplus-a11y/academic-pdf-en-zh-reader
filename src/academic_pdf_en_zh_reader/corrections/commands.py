# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Stable JSON command boundary for the private correction store."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from academic_pdf_en_zh_reader.atomic_file import atomic_publish_no_clobber
from academic_pdf_en_zh_reader.corrections.database import (
    CorrectionDraft,
    CorrectionError,
    CorrectionStore,
    PermissionHardener,
    error_message,
    harden_private_path,
)

_ADD_FIELDS = {
    "ambiguity_key_id",
    "core_collocation",
    "correction_source",
    "domain",
    "english_expression",
    "preferred_chinese",
    "semantic_tags",
    "source_annotation",
    "source_part_of_speech",
    "source_syntax",
    "target_grammar_function",
}


def _error(code: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": {"code": code, "message": error_message(code)},
    }


def _success(result: object) -> dict[str, object]:
    return {"ok": True, "result": result}


def _authorization_required(authorized: bool) -> None:
    if authorized is not True:
        raise CorrectionError("AUTHORIZATION_REQUIRED")


def _add(store: CorrectionStore, payload: Mapping[str, object], authorized: bool):
    _authorization_required(authorized)
    if set(payload) != _ADD_FIELDS:
        raise CorrectionError("INVALID_INPUT")
    annotation = payload["source_annotation"]
    tags = payload["semantic_tags"]
    if not isinstance(annotation, Mapping) or not isinstance(tags, (list, tuple)):
        raise CorrectionError("INVALID_INPUT")
    draft = CorrectionDraft(
        english_expression=payload["english_expression"],  # type: ignore[arg-type]
        preferred_chinese=payload["preferred_chinese"],  # type: ignore[arg-type]
        ambiguity_key_id=payload["ambiguity_key_id"],  # type: ignore[arg-type]
        domain=payload["domain"],  # type: ignore[arg-type]
        source_part_of_speech=payload["source_part_of_speech"],  # type: ignore[arg-type]
        source_syntax=payload["source_syntax"],  # type: ignore[arg-type]
        target_grammar_function=payload["target_grammar_function"],  # type: ignore[arg-type]
        core_collocation=payload["core_collocation"],  # type: ignore[arg-type]
        semantic_tags=tuple(tags),  # type: ignore[arg-type]
    )
    return store.record_authorized_correction(
        draft,
        source_annotation=annotation,
        correction_source=payload["correction_source"],  # type: ignore[arg-type]
        authorized=True,
    ).to_dict()


def execute_command(
    store: CorrectionStore,
    command: str,
    payload: Mapping[str, object] | None = None,
    *,
    authorized: bool = False,
) -> dict[str, object]:
    """Execute without logging or echoing rejected personal text."""

    values: Mapping[str, object] = {} if payload is None else payload
    try:
        if command == "add":
            result = _add(store, values, authorized)
        elif command == "list":
            if set(values) - {"include_revoked"}:
                raise CorrectionError("INVALID_INPUT")
            include_revoked = values.get("include_revoked", False)
            if type(include_revoked) is not bool:
                raise CorrectionError("INVALID_INPUT")
            result = [
                record.to_dict()
                for record in store.list_records(include_revoked=include_revoked)
            ]
        elif command in {"revoke", "restore", "delete"}:
            _authorization_required(authorized)
            if set(values) != {"record_id"} or not isinstance(values["record_id"], str):
                raise CorrectionError("INVALID_INPUT")
            record_id = values["record_id"]
            if command == "delete":
                store.delete(record_id, authorized=True)
                result = {"deleted": True, "record_id": record_id}
            else:
                record = getattr(store, command)(record_id, authorized=True)
                result = record.to_dict()
        elif command == "clear":
            _authorization_required(authorized)
            if values:
                raise CorrectionError("INVALID_INPUT")
            result = {"deleted_count": store.clear(authorized=True)}
        else:
            raise CorrectionError("INVALID_INPUT")
        return _success(result)
    except CorrectionError as exc:
        return _error(exc.code)
    except (KeyError, TypeError, ValueError):
        return _error("INVALID_INPUT")


def export_payload(store: CorrectionStore) -> dict[str, object]:
    return {
        "export_kind": "personal-corrections",
        "schema_version": store.schema_version(),
        "records": [
            record.to_dict() for record in store.list_records(include_revoked=True)
        ],
    }


def export_corrections(
    store: CorrectionStore,
    destination: Path | str,
    *,
    authorized: bool,
    overwrite: bool = False,
    permission_hardener: PermissionHardener = harden_private_path,
) -> dict[str, object]:
    """Atomically write only minimized records to an explicitly chosen file."""

    _authorization_required(authorized)
    target = Path(destination).expanduser().resolve(strict=False)
    if target.exists() and not overwrite:
        raise CorrectionError("INVALID_INPUT")
    payload = export_payload(store)
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            permission_hardener(temporary, False)
        except Exception as exc:
            raise CorrectionError("STORAGE_PERMISSION_DENIED") from exc
        if overwrite:
            os.replace(temporary, target)
        else:
            try:
                atomic_publish_no_clobber(temporary, target)
            except FileExistsError as exc:
                raise CorrectionError("INVALID_INPUT") from exc
        return payload
    except CorrectionError:
        raise
    except (OSError, PermissionError) as exc:
        raise CorrectionError("STORAGE_PERMISSION_DENIED") from exc
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


__all__ = [
    "execute_command",
    "export_corrections",
    "export_payload",
]
