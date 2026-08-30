# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Private, transactional SQLite storage for authorized ambiguity corrections."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from academic_pdf_en_zh_reader.corrections.normalize import (
    NormalizationError,
    canonical_english,
    compact_text,
    context_fingerprint,
    normalized_english,
    normalized_label,
    normalized_tags,
    preferred_chinese,
    sha256_identifier,
    stable_record_id,
)

SCHEMA_VERSION = 1
EVIDENCE_CODE = "user-resolved-bright-red-ambiguity"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SQL_TIMEOUT_SECONDS = 30.0
_INITIALIZATION_LOCKS: dict[Path, threading.Lock] = {}
_INITIALIZATION_LOCKS_GUARD = threading.Lock()

PermissionHardener = Callable[[Path, bool], None]
Clock = Callable[[], str]

_ERROR_MESSAGES = {
    "AUTHORIZATION_REQUIRED": "explicit authorization is required",
    "DATABASE_ERROR": "personal correction storage failed",
    "INVALID_ANNOTATION": "source annotation is not an eligible ambiguity",
    "INVALID_INPUT": "input is invalid",
    "INVALID_STORAGE_LOCATION": "personal correction storage location is invalid",
    "RECORD_NOT_FOUND": "personal correction record was not found",
    "SCHEMA_VERSION_UNSUPPORTED": "personal correction schema is unsupported",
    "STORAGE_PERMISSION_DENIED": "private storage permissions could not be enforced",
    "USER_CORRECTION_REQUIRED": "an explicit user correction is required",
}


class CorrectionError(RuntimeError):
    """A stable, non-sensitive storage error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


@dataclass(frozen=True)
class CorrectionDraft:
    english_expression: str
    preferred_chinese: str
    ambiguity_key_id: str
    domain: str
    source_part_of_speech: str
    source_syntax: str
    target_grammar_function: str
    core_collocation: str
    semantic_tags: tuple[str, ...]


@dataclass(frozen=True)
class CorrectionRecord:
    id: str
    english_expression: str
    normalized_english: str
    preferred_chinese: str
    ambiguity_key_id: str
    domain: str
    source_part_of_speech: str
    source_syntax: str
    target_grammar_function: str
    context_fingerprint: str
    core_collocation: str
    semantic_tags: tuple[str, ...]
    evidence_code: str
    status: str
    version: int
    created_at: str
    last_confirmed_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ambiguity_key_id": self.ambiguity_key_id,
            "context_fingerprint": self.context_fingerprint,
            "core_collocation": self.core_collocation,
            "created_at": self.created_at,
            "domain": self.domain,
            "english_expression": self.english_expression,
            "evidence_code": self.evidence_code,
            "id": self.id,
            "last_confirmed_at": self.last_confirmed_at,
            "normalized_english": self.normalized_english,
            "preferred_chinese": self.preferred_chinese,
            "semantic_tags": list(self.semantic_tags),
            "source_part_of_speech": self.source_part_of_speech,
            "source_syntax": self.source_syntax,
            "status": self.status,
            "target_grammar_function": self.target_grammar_function,
            "version": self.version,
        }


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def default_database_path(
    *,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a user-scoped path without creating it."""

    env = os.environ if environment is None else environment
    current_platform = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else Path(home)
    if current_platform == "win32":
        local_appdata = env.get("LOCALAPPDATA")
        root = Path(local_appdata) if local_appdata else user_home / "AppData" / "Local"
    else:
        xdg_data = env.get("XDG_DATA_HOME")
        root = Path(xdg_data) if xdg_data else user_home / ".local" / "share"
    return root / "academic-pdf-en-zh-reader" / "personal.corrections.sqlite3"


def _windows_current_sid() -> str:
    completed = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise PermissionError("current Windows identity is unavailable")
    try:
        row = next(csv.reader([completed.stdout.strip()]))
    except (csv.Error, StopIteration) as exc:
        raise PermissionError("current Windows identity is unavailable") from exc
    if len(row) < 2 or not row[1].startswith("S-"):
        raise PermissionError("current Windows identity is unavailable")
    return row[1]


def harden_private_path(path: Path, is_directory: bool) -> None:
    """Best-effort current-user-only permissions; any uncertainty fails closed."""

    if os.name != "nt":
        path.chmod(0o700 if is_directory else 0o600)
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError("private Unix mode was not applied")
        return

    sid = _windows_current_sid()
    inheritance = "(OI)(CI)F" if is_directory else "F"
    commands = (
        ["icacls", str(path), "/grant:r", f"*{sid}:{inheritance}"],
        ["icacls", str(path), "/inheritance:r"],
        [
            "icacls",
            str(path),
            "/remove:g",
            "*S-1-1-0",
            "*S-1-5-11",
            "*S-1-5-32-545",
        ],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=10,
        )
        if completed.returncode != 0:
            raise PermissionError("private Windows ACL was not applied")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _inside_git_checkout(path: Path) -> bool:
    return any((ancestor / ".git").exists() for ancestor in path.parents)


def _initialization_lock(path: Path) -> threading.Lock:
    with _INITIALIZATION_LOCKS_GUARD:
        return _INITIALIZATION_LOCKS.setdefault(path, threading.Lock())


class CorrectionStore:
    """A lazily-created personal store. Construction and import never write."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        permission_hardener: PermissionHardener = harden_private_path,
        clock: Clock = _utc_now,
        forbidden_roots: Iterable[Path | str] = (),
    ) -> None:
        explicit_path = database_path is not None
        selected = (
            default_database_path() if database_path is None else Path(database_path)
        )
        self.database_path = selected.expanduser().resolve(strict=False)
        roots = (_REPOSITORY_ROOT, *(Path(root).resolve() for root in forbidden_roots))
        inside_forbidden = any(_inside(self.database_path, root) for root in roots)
        inside_current_job = explicit_path and _inside(
            self.database_path,
            Path.cwd().resolve(),
        )
        if (
            inside_forbidden
            or inside_current_job
            or _inside_git_checkout(self.database_path)
        ):
            raise CorrectionError("INVALID_STORAGE_LOCATION")
        self._permission_hardener = permission_hardener
        self._clock = clock
        self._initialization_lock = _initialization_lock(self.database_path)
        self._ready = False

    def _harden(self, path: Path, is_directory: bool) -> None:
        try:
            self._permission_hardener(path, is_directory)
        except Exception as exc:
            raise CorrectionError("STORAGE_PERMISSION_DENIED") from exc

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._initialization_lock:
            if self._ready:
                return
            parent = self.database_path.parent
            created_parent = not parent.exists()
            try:
                parent.mkdir(parents=True, exist_ok=True)
                self._harden(parent, True)
            except (OSError, CorrectionError) as exc:
                if created_parent:
                    with suppress(OSError):
                        parent.rmdir()
                if isinstance(exc, CorrectionError):
                    raise
                raise CorrectionError("STORAGE_PERMISSION_DENIED") from exc

            created_database = not self.database_path.exists()
            try:
                with sqlite3.connect(
                    self.database_path,
                    timeout=_SQL_TIMEOUT_SECONDS,
                ) as connection:
                    self._harden(self.database_path, False)
                    connection.execute("PRAGMA busy_timeout = 30000")
                    user_version = connection.execute("PRAGMA user_version").fetchone()[
                        0
                    ]
                    if user_version not in {0, SCHEMA_VERSION}:
                        raise CorrectionError("SCHEMA_VERSION_UNSUPPORTED")
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = FULL")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS metadata ("
                        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                    )
                    existing = connection.execute(
                        "SELECT value FROM metadata WHERE key = ?",
                        ("schema_version",),
                    ).fetchone()
                    if existing is not None and existing[0] != str(SCHEMA_VERSION):
                        raise CorrectionError("SCHEMA_VERSION_UNSUPPORTED")
                    connection.execute(
                        "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
                        ("schema_version", str(SCHEMA_VERSION)),
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS corrections (
                            id TEXT PRIMARY KEY,
                            english_expression TEXT NOT NULL,
                            normalized_english TEXT NOT NULL,
                            preferred_chinese TEXT NOT NULL,
                            ambiguity_key_id TEXT NOT NULL,
                            domain TEXT NOT NULL,
                            source_part_of_speech TEXT NOT NULL,
                            source_syntax TEXT NOT NULL,
                            target_grammar_function TEXT NOT NULL,
                            context_fingerprint TEXT NOT NULL,
                            core_collocation TEXT NOT NULL,
                            semantic_tags_json TEXT NOT NULL,
                            evidence_code TEXT NOT NULL,
                            status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
                            version INTEGER NOT NULL CHECK(version > 0),
                            created_at TEXT NOT NULL,
                            last_confirmed_at TEXT NOT NULL,
                            UNIQUE(
                                ambiguity_key_id,
                                normalized_english,
                                preferred_chinese,
                                context_fingerprint
                            )
                        )
                        """
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS corrections_lookup "
                        "ON corrections(normalized_english, status, id)"
                    )
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._ready = True
            except CorrectionError:
                if created_database:
                    self._remove_new_database_files()
                raise
            except (OSError, sqlite3.Error) as exc:
                if created_database:
                    self._remove_new_database_files()
                raise CorrectionError("DATABASE_ERROR") from exc

    def _remove_new_database_files(self) -> None:
        for path in (
            self.database_path,
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
        ):
            with suppress(OSError):
                path.unlink(missing_ok=True)

    def _connect(self) -> sqlite3.Connection:
        self._ensure_ready()
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=_SQL_TIMEOUT_SECONDS,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error as exc:
            raise CorrectionError("DATABASE_ERROR") from exc

    @contextmanager
    def _transaction(self) -> Any:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except CorrectionError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise CorrectionError("DATABASE_ERROR") from exc
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> CorrectionRecord:
        return CorrectionRecord(
            id=row["id"],
            english_expression=row["english_expression"],
            normalized_english=row["normalized_english"],
            preferred_chinese=row["preferred_chinese"],
            ambiguity_key_id=row["ambiguity_key_id"],
            domain=row["domain"],
            source_part_of_speech=row["source_part_of_speech"],
            source_syntax=row["source_syntax"],
            target_grammar_function=row["target_grammar_function"],
            context_fingerprint=row["context_fingerprint"],
            core_collocation=row["core_collocation"],
            semantic_tags=tuple(json.loads(row["semantic_tags_json"])),
            evidence_code=row["evidence_code"],
            status=row["status"],
            version=row["version"],
            created_at=row["created_at"],
            last_confirmed_at=row["last_confirmed_at"],
        )

    @staticmethod
    def _require_authorized(authorized: bool) -> None:
        if authorized is not True:
            raise CorrectionError("AUTHORIZATION_REQUIRED")

    @staticmethod
    def _validate_source(
        source_annotation: Mapping[str, object],
        ambiguity_key_id: str,
        correction_source: str,
    ) -> None:
        if correction_source != "user-explicit":
            raise CorrectionError("USER_CORRECTION_REQUIRED")
        if (
            source_annotation.get("kind") != "bright-red-ambiguity"
            or source_annotation.get("underline") is not True
            or source_annotation.get("ambiguity_key_id") != ambiguity_key_id
        ):
            raise CorrectionError("INVALID_ANNOTATION")

    @staticmethod
    def _normalize_draft(draft: CorrectionDraft) -> dict[str, object]:
        try:
            english = canonical_english(draft.english_expression)
            normalized = normalized_english(draft.english_expression)
            preferred = preferred_chinese(draft.preferred_chinese)
            key = sha256_identifier(draft.ambiguity_key_id)
            domain = normalized_label(draft.domain, maximum=80)
            part_of_speech = normalized_label(
                draft.source_part_of_speech,
                maximum=80,
            )
            source_syntax = normalized_label(draft.source_syntax, maximum=160)
            target_function = normalized_label(
                draft.target_grammar_function,
                maximum=80,
            )
            collocation = compact_text(
                draft.core_collocation,
                maximum=120,
                casefold=True,
            )
            tags = normalized_tags(draft.semantic_tags)
        except NormalizationError as exc:
            raise CorrectionError("INVALID_INPUT") from exc
        fingerprint = context_fingerprint(
            domain=domain,
            source_part_of_speech=part_of_speech,
            source_syntax=source_syntax,
            target_grammar_function=target_function,
            core_collocation=collocation,
            semantic_tags=tags,
        )
        identity = {
            "ambiguity_key_id": key,
            "context_fingerprint": fingerprint,
            "normalized_english": normalized,
            "preferred_chinese": preferred,
        }
        return {
            "id": stable_record_id(identity),
            "english_expression": english,
            "normalized_english": normalized,
            "preferred_chinese": preferred,
            "ambiguity_key_id": key,
            "domain": domain,
            "source_part_of_speech": part_of_speech,
            "source_syntax": source_syntax,
            "target_grammar_function": target_function,
            "context_fingerprint": fingerprint,
            "core_collocation": collocation,
            "semantic_tags": tags,
        }

    def record_authorized_correction(
        self,
        draft: CorrectionDraft,
        *,
        source_annotation: Mapping[str, object],
        correction_source: str,
        authorized: bool,
    ) -> CorrectionRecord:
        self._require_authorized(authorized)
        normalized = self._normalize_draft(draft)
        self._validate_source(
            source_annotation,
            str(normalized["ambiguity_key_id"]),
            correction_source,
        )
        now = self._clock()
        tags_json = json.dumps(
            normalized["semantic_tags"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO corrections (
                    id, english_expression, normalized_english, preferred_chinese,
                    ambiguity_key_id, domain, source_part_of_speech, source_syntax,
                    target_grammar_function, context_fingerprint, core_collocation,
                    semantic_tags_json, evidence_code, status, version, created_at,
                    last_confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = 'active',
                    version = corrections.version + 1,
                    last_confirmed_at = excluded.last_confirmed_at
                """,
                (
                    normalized["id"],
                    normalized["english_expression"],
                    normalized["normalized_english"],
                    normalized["preferred_chinese"],
                    normalized["ambiguity_key_id"],
                    normalized["domain"],
                    normalized["source_part_of_speech"],
                    normalized["source_syntax"],
                    normalized["target_grammar_function"],
                    normalized["context_fingerprint"],
                    normalized["core_collocation"],
                    tags_json,
                    EVIDENCE_CODE,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM corrections WHERE id = ?",
                (normalized["id"],),
            ).fetchone()
        assert row is not None
        return self._record(row)

    def schema_version(self) -> int:
        connection = self._connect()
        try:
            metadata = connection.execute(
                "SELECT value FROM metadata WHERE key = ?",
                ("schema_version",),
            ).fetchone()
            pragma = connection.execute("PRAGMA user_version").fetchone()
        except sqlite3.Error as exc:
            raise CorrectionError("DATABASE_ERROR") from exc
        finally:
            connection.close()
        if metadata is None or pragma is None or metadata[0] != str(pragma[0]):
            raise CorrectionError("SCHEMA_VERSION_UNSUPPORTED")
        return int(metadata[0])

    def get_record(self, record_id: str) -> CorrectionRecord:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM corrections WHERE id = ?",
                (record_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise CorrectionError("DATABASE_ERROR") from exc
        finally:
            connection.close()
        if row is None:
            raise CorrectionError("RECORD_NOT_FOUND")
        return self._record(row)

    def list_records(
        self,
        *,
        include_revoked: bool = False,
    ) -> tuple[CorrectionRecord, ...]:
        connection = self._connect()
        query = "SELECT * FROM corrections"
        parameters: tuple[object, ...] = ()
        if not include_revoked:
            query += " WHERE status = ?"
            parameters = ("active",)
        query += " ORDER BY normalized_english, domain, id"
        try:
            rows = connection.execute(query, parameters).fetchall()
        except sqlite3.Error as exc:
            raise CorrectionError("DATABASE_ERROR") from exc
        finally:
            connection.close()
        return tuple(self._record(row) for row in rows)

    def find_active_by_expressions(
        self,
        expressions: Iterable[str],
    ) -> tuple[CorrectionRecord, ...]:
        normalized = tuple(sorted({normalized_english(value) for value in expressions}))
        if not normalized:
            return ()
        placeholders = ",".join("?" for _ in normalized)
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM corrections WHERE status = ? "
                f"AND normalized_english IN ({placeholders}) "
                "ORDER BY normalized_english, domain, id",
                ("active", *normalized),
            ).fetchall()
        except (NormalizationError, sqlite3.Error) as exc:
            raise CorrectionError("INVALID_INPUT") from exc
        finally:
            connection.close()
        return tuple(self._record(row) for row in rows)

    def _set_status(
        self,
        record_id: str,
        status: str,
        *,
        authorized: bool,
    ) -> CorrectionRecord:
        self._require_authorized(authorized)
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE corrections SET status = ?, version = version + 1 WHERE id = ?",
                (status, record_id),
            )
            if cursor.rowcount != 1:
                raise CorrectionError("RECORD_NOT_FOUND")
            row = connection.execute(
                "SELECT * FROM corrections WHERE id = ?",
                (record_id,),
            ).fetchone()
        assert row is not None
        return self._record(row)

    def revoke(self, record_id: str, *, authorized: bool) -> CorrectionRecord:
        return self._set_status(record_id, "revoked", authorized=authorized)

    def restore(self, record_id: str, *, authorized: bool) -> CorrectionRecord:
        return self._set_status(record_id, "active", authorized=authorized)

    def delete(self, record_id: str, *, authorized: bool) -> None:
        self._require_authorized(authorized)
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM corrections WHERE id = ?",
                (record_id,),
            )
            if cursor.rowcount != 1:
                raise CorrectionError("RECORD_NOT_FOUND")

    def clear(self, *, authorized: bool) -> int:
        self._require_authorized(authorized)
        with self._transaction() as connection:
            count = connection.execute("SELECT count(*) FROM corrections").fetchone()[0]
            connection.execute("DELETE FROM corrections")
        return int(count)


def error_message(code: str) -> str:
    return _ERROR_MESSAGES[code]


__all__ = [
    "CorrectionDraft",
    "CorrectionError",
    "CorrectionRecord",
    "CorrectionStore",
    "EVIDENCE_CODE",
    "PermissionHardener",
    "SCHEMA_VERSION",
    "default_database_path",
    "error_message",
    "harden_private_path",
]
