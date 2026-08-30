# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.corrections.commands import execute_command
from academic_pdf_en_zh_reader.corrections.database import (
    CorrectionDraft,
    CorrectionError,
    CorrectionStore,
    default_database_path,
)


def _private_for_tests(_path: Path, _is_directory: bool) -> None:
    return None


def _write_one(store: CorrectionStore) -> None:
    key = "a" * 64
    store.record_authorized_correction(
        CorrectionDraft(
            english_expression="associated with",
            preferred_chinese="与……相关",
            ambiguity_key_id=key,
            domain="biomedicine",
            source_part_of_speech="verb phrase",
            source_syntax="past-participle predicate",
            target_grammar_function="predicate",
            core_collocation="exposure associated with outcome",
            semantic_tags=("association",),
        ),
        source_annotation={
            "kind": "bright-red-ambiguity",
            "underline": True,
            "ambiguity_key_id": key,
        },
        correction_source="user-explicit",
        authorized=True,
    )


def test_import_has_no_filesystem_side_effect(tmp_path: Path) -> None:
    data_root = tmp_path / "never-created"
    environment = os.environ.copy()
    environment["XDG_DATA_HOME"] = str(data_root)
    environment["LOCALAPPDATA"] = str(data_root)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import academic_pdf_en_zh_reader.corrections.database",
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0
    assert data_root.exists() is False


def test_default_path_is_in_a_user_data_root_not_the_working_directory(
    tmp_path: Path,
) -> None:
    expected_root = tmp_path / "user-data"
    result = default_database_path(
        environment={"XDG_DATA_HOME": str(expected_root)},
        platform="linux",
        home=tmp_path / "home",
    )

    assert result.is_relative_to(expected_root)
    assert result.name.endswith(".corrections.sqlite3")
    assert result.is_relative_to(Path.cwd()) is False


def test_database_schema_contains_only_minimized_fields(tmp_path: Path) -> None:
    store = CorrectionStore(
        tmp_path / "personal-corrections.sqlite3",
        permission_hardener=_private_for_tests,
    )
    _write_one(store)

    with sqlite3.connect(store.database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(corrections)").fetchall()
        }

    assert columns == {
        "id",
        "english_expression",
        "normalized_english",
        "preferred_chinese",
        "ambiguity_key_id",
        "domain",
        "source_part_of_speech",
        "source_syntax",
        "target_grammar_function",
        "context_fingerprint",
        "core_collocation",
        "semantic_tags_json",
        "evidence_code",
        "status",
        "version",
        "created_at",
        "last_confirmed_at",
    }
    assert columns.isdisjoint({"paper_text", "paper_title", "author", "source_path"})


def test_unsupported_schema_version_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "personal-corrections.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    store = CorrectionStore(database, permission_hardener=_private_for_tests)

    with pytest.raises(CorrectionError) as caught:
        store.list_records()

    assert caught.value.code == "SCHEMA_VERSION_UNSUPPORTED"


def test_explicit_database_path_inside_current_job_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CorrectionError) as caught:
        CorrectionStore(
            tmp_path / "job" / "personal-corrections.sqlite3",
            permission_hardener=_private_for_tests,
        )

    assert caught.value.code == "INVALID_STORAGE_LOCATION"
    assert (tmp_path / "job").exists() is False


def test_stable_json_error_does_not_echo_sensitive_input(tmp_path: Path) -> None:
    store = CorrectionStore(
        tmp_path / "personal-corrections.sqlite3",
        permission_hardener=_private_for_tests,
    )
    secret = "C:/private/paper-by-Dr-Example.pdf"
    result = execute_command(
        store,
        "add",
        {
            "english_expression": secret,
            "preferred_chinese": "秘密译法",
        },
        authorized=False,
    )
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)

    assert result == {
        "ok": False,
        "error": {
            "code": "AUTHORIZATION_REQUIRED",
            "message": "explicit authorization is required",
        },
    }
    assert secret not in encoded
    assert "秘密译法" not in encoded


def test_unknown_payload_fields_are_rejected_without_being_stored(
    tmp_path: Path,
) -> None:
    store = CorrectionStore(
        tmp_path / "personal-corrections.sqlite3",
        permission_hardener=_private_for_tests,
    )
    result = execute_command(
        store,
        "add",
        {
            "paper_text": "entire unpublished paper",
            "author": "Dr Example",
            "source_path": "C:/private/paper.pdf",
        },
        authorized=True,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"  # type: ignore[index]
    assert store.database_path.exists() is False


def test_cli_error_is_json_and_never_echoes_rejected_text(tmp_path: Path) -> None:
    secret = "unpublished-sensitive-translation"
    payload = json.dumps({"preferred_chinese": secret}, ensure_ascii=False)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/corrections.py",
            "--database",
            str(tmp_path / "personal-corrections.sqlite3"),
            "add",
        ],
        check=False,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        input=payload,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["error"]["code"] == "AUTHORIZATION_REQUIRED"
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_cli_argument_errors_also_use_the_stable_json_envelope() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/corrections.py", "not-a-command"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "error": {"code": "INVALID_INPUT", "message": "input is invalid"},
        "ok": False,
    }
    assert completed.stderr == ""
