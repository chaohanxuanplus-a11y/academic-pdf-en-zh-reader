# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Stable hashes and source-derived identifiers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

_ROLE_PATTERN = re.compile(r"[a-z][a-z0-9_-]*\Z")


def sha256_canonical(value: object) -> str:
    """Return SHA-256 over the project canonical JSON representation."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for opaque bytes."""

    return hashlib.sha256(value).hexdigest()


def stable_source_id(
    *,
    page_number: int,
    reading_order: int,
    role: str,
    source_char_start: int,
    source_char_end: int,
) -> str:
    """Build an ID solely from stable source position and semantic role."""

    location_values = (
        page_number,
        reading_order,
        source_char_start,
        source_char_end,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in location_values
    ):
        raise ValueError("source location values must be integers")
    if page_number < 1 or reading_order < 0:
        raise ValueError("page_number and reading_order are out of range")
    if source_char_start < 0 or source_char_end <= source_char_start:
        raise ValueError("source character range is invalid")
    if not isinstance(role, str) or not _ROLE_PATTERN.fullmatch(role):
        raise ValueError("role must be a lowercase stable identifier")
    return (
        f"p{page_number}-r{reading_order}-{role}-{source_char_start}-{source_char_end}"
    )


def layout_input_hash(
    *,
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    config: Mapping[str, object],
    code_version: str,
    dependency_lock: bytes,
    font_manifest: Mapping[str, object],
    runtime_fingerprint: Mapping[str, object],
) -> str:
    """Hash every frozen input that can influence deterministic layout."""

    if not isinstance(dependency_lock, bytes):
        raise TypeError("dependency_lock must be the exact lock-file bytes")
    payload = {
        "hash_contract_version": "1.0.0",
        "artifacts": {
            "source": source,
            "units": units,
            "translation": translation,
            "review": review,
            "annotations": annotations,
            "frame-graph": frame_graph,
        },
        "config": config,
        "code_version": code_version,
        "dependency_lock_sha256": sha256_bytes(dependency_lock),
        "font_manifest": font_manifest,
        "runtime_fingerprint": runtime_fingerprint,
    }
    return sha256_canonical(payload)
