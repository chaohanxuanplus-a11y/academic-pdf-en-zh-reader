# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.corrections.database import (
    CorrectionDraft,
    CorrectionError,
    CorrectionStore,
)


def _private_for_tests(_path: Path, _is_directory: bool) -> None:
    return None


def _annotation(key: str = "a" * 64) -> dict[str, object]:
    return {
        "kind": "bright-red-ambiguity",
        "underline": True,
        "ambiguity_key_id": key,
    }


def _draft(
    *,
    key: str = "a" * 64,
    expression: str = "associated with",
    chinese: str = "与……相关",
) -> CorrectionDraft:
    return CorrectionDraft(
        english_expression=expression,
        preferred_chinese=chinese,
        ambiguity_key_id=key,
        domain="biomedicine",
        source_part_of_speech="verb phrase",
        source_syntax="past-participle predicate with preposition",
        target_grammar_function="predicate",
        core_collocation="exposure associated with outcome",
        semantic_tags=("association", "observational evidence"),
    )


@pytest.fixture
def store(tmp_path: Path) -> CorrectionStore:
    return CorrectionStore(
        tmp_path / "personal-corrections.sqlite3",
        permission_hardener=_private_for_tests,
    )


@pytest.mark.parametrize(
    ("authorized", "annotation", "source", "code"),
    [
        (False, _annotation(), "user-explicit", "AUTHORIZATION_REQUIRED"),
        (True, {"kind": "dark-red-highlight"}, "user-explicit", "INVALID_ANNOTATION"),
        (True, _annotation(), "model-candidate", "USER_CORRECTION_REQUIRED"),
        (True, _annotation(), "ordinary-feedback", "USER_CORRECTION_REQUIRED"),
    ],
)
def test_only_explicit_user_correction_of_bright_red_ambiguity_can_write(
    store: CorrectionStore,
    authorized: bool,
    annotation: dict[str, object],
    source: str,
    code: str,
) -> None:
    with pytest.raises(CorrectionError) as caught:
        store.record_authorized_correction(
            _draft(),
            source_annotation=annotation,
            correction_source=source,
            authorized=authorized,
        )

    assert caught.value.code == code
    assert store.database_path.exists() is False


def test_authorized_record_is_created_and_exact_reconfirmation_is_versioned(
    store: CorrectionStore,
) -> None:
    first = store.record_authorized_correction(
        _draft(),
        source_annotation=_annotation(),
        correction_source="user-explicit",
        authorized=True,
    )
    second = store.record_authorized_correction(
        _draft(),
        source_annotation=_annotation(),
        correction_source="user-explicit",
        authorized=True,
    )

    assert first.id == second.id
    assert second.version == 2
    assert len(store.list_records()) == 1
    assert store.schema_version() == 1


def test_annotation_key_must_match_the_recorded_ambiguity(
    store: CorrectionStore,
) -> None:
    with pytest.raises(CorrectionError) as caught:
        store.record_authorized_correction(
            _draft(key="b" * 64),
            source_annotation=_annotation("a" * 64),
            correction_source="user-explicit",
            authorized=True,
        )

    assert caught.value.code == "INVALID_ANNOTATION"
    assert store.database_path.exists() is False


def test_empty_user_correction_is_not_a_writable_preference(
    store: CorrectionStore,
) -> None:
    with pytest.raises(CorrectionError) as caught:
        store.record_authorized_correction(
            _draft(chinese="  "),
            source_annotation=_annotation(),
            correction_source="user-explicit",
            authorized=True,
        )

    assert caught.value.code == "INVALID_INPUT"
    assert store.database_path.exists() is False


def test_permission_failure_is_fail_closed_before_database_creation(
    tmp_path: Path,
) -> None:
    def deny(_path: Path, _is_directory: bool) -> None:
        raise PermissionError("sensitive operating-system detail")

    store = CorrectionStore(
        tmp_path / "private" / "personal-corrections.sqlite3",
        permission_hardener=deny,
    )
    with pytest.raises(CorrectionError) as caught:
        store.record_authorized_correction(
            _draft(),
            source_annotation=_annotation(),
            correction_source="user-explicit",
            authorized=True,
        )

    assert caught.value.code == "STORAGE_PERMISSION_DENIED"
    assert store.database_path.exists() is False
    assert "sensitive" not in str(caught.value)


def test_sql_injection_payload_is_data_not_sql(store: CorrectionStore) -> None:
    payload = "robust'); DROP TABLE corrections; --"
    record = store.record_authorized_correction(
        _draft(expression=payload),
        source_annotation=_annotation(),
        correction_source="user-explicit",
        authorized=True,
    )

    assert record.english_expression == payload
    assert store.get_record(record.id).english_expression == payload
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM corrections").fetchone() == (1,)


def test_concurrent_writers_use_transactions_and_deterministic_listing(
    store: CorrectionStore,
) -> None:
    def write(index: int) -> str:
        key = f"{index:064x}"
        return store.record_authorized_correction(
            _draft(key=key, expression=f"term {index}"),
            source_annotation=_annotation(key),
            correction_source="user-explicit",
            authorized=True,
        ).id

    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(write, range(24)))

    assert len(set(identifiers)) == 24
    rows = store.list_records()
    assert [row.normalized_english for row in rows] == sorted(
        row.normalized_english for row in rows
    )


def test_independent_store_instances_coordinate_through_sqlite(
    tmp_path: Path,
) -> None:
    database = tmp_path / "personal-corrections.sqlite3"
    stores = [
        CorrectionStore(database, permission_hardener=_private_for_tests)
        for _ in range(8)
    ]

    def write(index: int) -> str:
        key = f"{index + 100:064x}"
        return (
            stores[index % len(stores)]
            .record_authorized_correction(
                _draft(key=key, expression=f"shared term {index}"),
                source_annotation=_annotation(key),
                correction_source="user-explicit",
                authorized=True,
            )
            .id
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(write, range(16)))

    assert len(set(identifiers)) == 16
    assert len(stores[0].list_records()) == 16
