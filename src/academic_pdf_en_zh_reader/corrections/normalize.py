# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Pure normalization for minimized personal-correction records."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable

_SHA256 = re.compile(r"[0-9a-f]{64}")


class NormalizationError(ValueError):
    """Raised without echoing rejected personal or paper text."""


def compact_text(
    value: object,
    *,
    maximum: int,
    casefold: bool = False,
) -> str:
    """Return bounded NFC text with whitespace collapsed."""

    if not isinstance(value, str):
        raise NormalizationError("text field is invalid")
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split())
    if casefold:
        normalized = normalized.casefold()
    if not normalized or len(normalized) > maximum:
        raise NormalizationError("text field is invalid")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise NormalizationError("text field is invalid")
    return normalized


def canonical_english(value: object) -> str:
    return compact_text(value, maximum=240)


def normalized_english(value: object) -> str:
    return compact_text(value, maximum=240, casefold=True)


def preferred_chinese(value: object) -> str:
    return compact_text(value, maximum=240)


def normalized_label(value: object, *, maximum: int = 160) -> str:
    return compact_text(value, maximum=maximum, casefold=True)


def normalized_tags(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise NormalizationError("semantic tags are invalid")
    tags = {compact_text(value, maximum=48, casefold=True) for value in values}
    if not tags or len(tags) > 8:
        raise NormalizationError("semantic tags are invalid")
    return tuple(sorted(tags))


def sha256_identifier(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise NormalizationError("identifier is invalid")
    return value


def context_fingerprint(
    *,
    domain: str,
    source_part_of_speech: str,
    source_syntax: str,
    target_grammar_function: str,
    core_collocation: str,
    semantic_tags: tuple[str, ...],
) -> str:
    """Hash only the small structured context needed for later comparison."""

    payload = {
        "core_collocation": core_collocation,
        "domain": domain,
        "semantic_tags": list(semantic_tags),
        "source_part_of_speech": source_part_of_speech,
        "source_syntax": source_syntax,
        "target_grammar_function": target_grammar_function,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_record_id(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "NormalizationError",
    "canonical_english",
    "compact_text",
    "context_fingerprint",
    "normalized_english",
    "normalized_label",
    "normalized_tags",
    "preferred_chinese",
    "sha256_identifier",
    "stable_record_id",
]
