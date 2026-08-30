# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""The project's small, explicit canonical JSON profile."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping


class CanonicalJsonError(ValueError):
    """Raised for values that cannot enter a deterministic JSON artifact."""


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalJsonError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError("JSON object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise CanonicalJsonError("Unicode normalization caused a key collision")
            normalized[normalized_key] = _normalize(child)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(child) for child in value]
    raise CanonicalJsonError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize as UTF-8 NFC JSON with sorted keys and preserved array order."""

    normalized = _normalize(value)
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CanonicalJsonError("value cannot be encoded as canonical JSON") from exc
