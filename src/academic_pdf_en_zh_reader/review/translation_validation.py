# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Validate translation coverage without interpreting paper text as instructions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)


class TranslationValidationError(ValueError):
    """Raised when a translation artifact violates its structural contract."""


def _ordered_positive_ranges(
    ranges: Sequence[Mapping[str, object]],
    *,
    start_key: str,
    end_key: str,
    limit: int,
    label: str,
) -> None:
    previous_end = 0
    for index, item in enumerate(ranges):
        start = item[start_key]
        end = item[end_key]
        if (
            type(start) is not int
            or type(end) is not int
            or start < 0
            or end <= start
            or end > limit
            or (index > 0 and start < previous_end)
        ):
            raise TranslationValidationError(
                f"translation {label} must be positive, ordered, and in bounds"
            )
        previous_end = end


def _validate_spans(
    source_text: str,
    target_text: str,
    spans: Sequence[Mapping[str, object]],
) -> None:
    _ordered_positive_ranges(
        spans,
        start_key="source_start",
        end_key="source_end",
        limit=len(source_text),
        label="spans",
    )
    _ordered_positive_ranges(
        spans,
        start_key="target_start",
        end_key="target_end",
        limit=len(target_text),
        label="spans",
    )


def _validate_terminology(
    source_text: str,
    target_text: str,
    terms: Sequence[Mapping[str, object]],
) -> None:
    _ordered_positive_ranges(
        terms,
        start_key="source_start",
        end_key="source_end",
        limit=len(source_text),
        label="terminology",
    )
    _ordered_positive_ranges(
        terms,
        start_key="target_start",
        end_key="target_end",
        limit=len(target_text),
        label="terminology",
    )
    for term in terms:
        source = term["source"]
        target = term["target"]
        if not isinstance(source, str) or not source.strip():
            raise TranslationValidationError(
                "translation terminology must be non-empty"
            )
        if not isinstance(target, str) or not target.strip():
            raise TranslationValidationError(
                "translation terminology must be non-empty"
            )
        if source_text[term["source_start"] : term["source_end"]] != source:
            raise TranslationValidationError(
                "translation terminology must match its source slice"
            )
        if target_text[term["target_start"] : term["target_end"]] != target:
            raise TranslationValidationError(
                "translation terminology must match its target slice"
            )


def validate_translation_artifact(
    units: Mapping[str, object],
    translation: Mapping[str, object],
) -> None:
    """Validate exact unit coverage and bounded annotations as inert JSON data."""

    try:
        validate_artifact("units", units)
        validate_artifact("translation", translation)
    except SchemaValidationError as exc:
        raise TranslationValidationError("translation input schema is invalid") from exc

    if translation["units_hash"] != sha256_canonical(units):
        raise TranslationValidationError(
            "translation units_hash does not match the units artifact"
        )
    translator_id = translation["translator_id"]
    if not isinstance(translator_id, str) or not translator_id.strip():
        raise TranslationValidationError("translation translator_id must be non-empty")

    source_units = units["units"]
    translated_units = translation["units"]
    assert isinstance(source_units, list)
    assert isinstance(translated_units, list)
    expected_ids = [unit["id"] for unit in source_units]
    actual_ids = [unit["unit_id"] for unit in translated_units]
    if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
        raise TranslationValidationError(
            "translation unit_id values must match units exactly once and in order"
        )

    for source_unit, translated_unit in zip(
        source_units, translated_units, strict=True
    ):
        source_text = source_unit["source_text"]
        target_text = translated_unit["chinese_text"]
        if not isinstance(target_text, str) or not target_text.strip():
            raise TranslationValidationError("translation text must be non-empty")
        spans = translated_unit["spans"]
        terms = translated_unit["terminology"]
        assert isinstance(source_text, str)
        assert isinstance(spans, list)
        assert isinstance(terms, list)
        _validate_spans(source_text, target_text, spans)
        _validate_terminology(source_text, target_text, terms)
