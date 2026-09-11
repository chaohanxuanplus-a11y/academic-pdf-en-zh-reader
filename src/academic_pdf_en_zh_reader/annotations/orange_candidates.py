# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Immutable candidates for optional terminology and expression teaching notes."""

from __future__ import annotations

from dataclasses import dataclass

from academic_pdf_en_zh_reader.annotations.validation import AnnotationValidationError

TEACHING_SEPARATOR = " — "


@dataclass(frozen=True, order=True)
class TeachingOccurrence:
    unit_id: str
    source_start: int
    source_end: int
    target_start: int
    target_end: int


@dataclass(frozen=True)
class TeachingCandidate:
    key: str
    english_original: str
    chinese_meaning: str
    occurrences: tuple[TeachingOccurrence, ...]
    value_priority: int
    essential: bool = True


def _one_line(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\n" in value
        or "\r" in value
    ):
        raise AnnotationValidationError(f"{label} must be non-empty one-line text")
    return value


def format_teaching_content(english_original: str, chinese_meaning: str) -> str:
    """Return the only permitted ordinary teaching-note text shape."""

    english = _one_line(english_original, label="English original")
    chinese = _one_line(chinese_meaning, label="Chinese meaning")
    return f"{english}{TEACHING_SEPARATOR}{chinese}"


__all__ = [
    "TEACHING_SEPARATOR",
    "TeachingCandidate",
    "TeachingOccurrence",
    "format_teaching_content",
]
