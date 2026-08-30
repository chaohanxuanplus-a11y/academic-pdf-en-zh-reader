# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.corrections.commands import export_corrections
from academic_pdf_en_zh_reader.corrections.database import (
    CorrectionDraft,
    CorrectionError,
    CorrectionStore,
)


def _private_for_tests(_path: Path, _is_directory: bool) -> None:
    return None


@pytest.fixture
def populated_store(tmp_path: Path) -> tuple[CorrectionStore, str]:
    store = CorrectionStore(
        tmp_path / "personal-corrections.sqlite3",
        permission_hardener=_private_for_tests,
    )
    key = "a" * 64
    record = store.record_authorized_correction(
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
    return store, record.id


@pytest.mark.parametrize("operation", ["revoke", "restore", "delete", "clear"])
def test_mutating_lifecycle_commands_require_explicit_authorization(
    populated_store: tuple[CorrectionStore, str], operation: str
) -> None:
    store, record_id = populated_store
    method = getattr(store, operation)
    arguments = () if operation == "clear" else (record_id,)

    with pytest.raises(CorrectionError) as caught:
        method(*arguments, authorized=False)

    assert caught.value.code == "AUTHORIZATION_REQUIRED"
    assert len(store.list_records(include_revoked=True)) == 1


def test_revoke_and_restore_are_reversible(
    populated_store: tuple[CorrectionStore, str],
) -> None:
    store, record_id = populated_store

    revoked = store.revoke(record_id, authorized=True)
    assert revoked.status == "revoked"
    assert store.list_records() == ()
    assert store.list_records(include_revoked=True)[0].status == "revoked"

    restored = store.restore(record_id, authorized=True)
    assert restored.status == "active"
    assert [item.id for item in store.list_records()] == [record_id]


def test_delete_is_permanent_and_clear_removes_all_records(
    populated_store: tuple[CorrectionStore, str],
) -> None:
    store, record_id = populated_store
    store.delete(record_id, authorized=True)
    assert store.list_records(include_revoked=True) == ()

    with pytest.raises(CorrectionError) as caught:
        store.delete(record_id, authorized=True)
    assert caught.value.code == "RECORD_NOT_FOUND"


def test_privacy_safe_export_is_deterministic_and_hardened(
    populated_store: tuple[CorrectionStore, str], tmp_path: Path
) -> None:
    store, _ = populated_store
    export_path = tmp_path / "private-export.json"

    first = export_corrections(
        store,
        export_path,
        authorized=True,
        permission_hardener=_private_for_tests,
    )
    first_bytes = export_path.read_bytes()
    export_path.write_bytes(b"stale export that must be explicitly replaced")
    second = export_corrections(
        store,
        export_path,
        authorized=True,
        overwrite=True,
        permission_hardener=_private_for_tests,
    )

    assert first == second
    assert export_path.read_bytes() == first_bytes
    assert "database_path" not in first
    assert "paper" not in first
    assert "author" not in first
    assert "source_path" not in first


def test_export_requires_authorization_and_does_not_leave_partial_file(
    populated_store: tuple[CorrectionStore, str], tmp_path: Path
) -> None:
    store, _ = populated_store
    export_path = tmp_path / "private-export.json"

    with pytest.raises(CorrectionError) as caught:
        export_corrections(
            store,
            export_path,
            authorized=False,
            permission_hardener=_private_for_tests,
        )

    assert caught.value.code == "AUTHORIZATION_REQUIRED"
    assert export_path.exists() is False


def test_export_without_overwrite_never_clobbers_a_competing_creator(
    populated_store: tuple[CorrectionStore, str], tmp_path: Path
) -> None:
    store, _ = populated_store
    export_path = tmp_path / "private-export.json"
    competitor = b"race winner"

    def create_competing_target(_path: Path, _is_directory: bool) -> None:
        export_path.write_bytes(competitor)

    with pytest.raises(CorrectionError) as caught:
        export_corrections(
            store,
            export_path,
            authorized=True,
            permission_hardener=create_competing_target,
        )

    assert caught.value.code == "INVALID_INPUT"
    assert export_path.read_bytes() == competitor
    assert list(tmp_path.glob(".private-export.json.*.tmp")) == []


def test_export_permission_failure_does_not_leave_a_product(
    populated_store: tuple[CorrectionStore, str], tmp_path: Path
) -> None:
    store, _ = populated_store
    export_path = tmp_path / "private-export.json"

    def deny(_path: Path, _is_directory: bool) -> None:
        raise PermissionError("private operating-system detail")

    with pytest.raises(CorrectionError) as caught:
        export_corrections(
            store,
            export_path,
            authorized=True,
            permission_hardener=deny,
        )

    assert caught.value.code == "STORAGE_PERMISSION_DENIED"
    assert export_path.exists() is False
