# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
)
from academic_pdf_en_zh_reader.job.hashing import (
    layout_input_hash,
    sha256_canonical,
    stable_source_id,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def test_canonical_json_is_utf8_nfc_sorted_and_preserves_array_order() -> None:
    left = {"z": [3, 1, 2], "e\u0301": "Cafe\u0301", "a": {"b": True}}
    right = {"a": {"b": True}, "é": "Café", "z": [3, 1, 2]}

    encoded = canonical_json_bytes(left)

    assert encoded == canonical_json_bytes(right)
    assert encoded.decode("utf-8") == '{"a":{"b":true},"z":[3,1,2],"é":"Café"}'
    assert canonical_json_bytes({"z": [2, 1]}) != canonical_json_bytes({"z": [1, 2]})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(CanonicalJsonError, match="finite"):
        canonical_json_bytes({"nested": [value]})


def test_canonical_json_rejects_non_string_keys_and_nfc_key_collisions() -> None:
    with pytest.raises(CanonicalJsonError, match="string"):
        canonical_json_bytes({1: "not allowed"})
    with pytest.raises(CanonicalJsonError, match="collision"):
        canonical_json_bytes({"é": 1, "e\u0301": 2})


def _layout_inputs() -> dict[str, object]:
    return {
        "source": {"schema_version": "1.0.0", "value": "source"},
        "units": {"schema_version": "1.0.0", "value": "units"},
        "translation": {"schema_version": "1.0.0", "value": "translation"},
        "review": {"schema_version": "1.0.0", "value": "review"},
        "annotations": {"schema_version": "1.0.0", "value": "annotations"},
        "frame_graph": {"schema_version": "1.0.0", "value": "frames"},
        "config": {"body_size_mpt": 9000},
        "code_version": "0.1.0.dev0",
        "dependency_lock": b"locked dependency graph\n",
        "font_manifest": {"regular.ttf": SHA_A, "semibold.ttf": SHA_B},
        "runtime_fingerprint": {
            "python": "3.12.11",
            "unicode": "15.1.0",
            "worker_runtime": SHA_A,
        },
    }


def test_layout_hash_covers_every_frozen_input() -> None:
    baseline_inputs = _layout_inputs()
    baseline = layout_input_hash(**baseline_inputs)
    assert len(baseline) == 64

    for key in baseline_inputs:
        changed = _layout_inputs()
        if key == "dependency_lock":
            changed[key] = b"different lock\n"
        elif key == "code_version":
            changed[key] = "0.1.0.dev1"
        else:
            changed[key] = {"changed": key}
        assert layout_input_hash(**changed) != baseline, key


def test_hashing_is_stable_under_key_order_and_unicode_normalization() -> None:
    assert sha256_canonical({"b": "e\u0301", "a": 1}) == sha256_canonical(
        {"a": 1, "b": "é"}
    )


def test_stable_source_id_uses_only_source_location_and_role() -> None:
    first = stable_source_id(
        page_number=2,
        reading_order=7,
        role="body",
        source_char_start=100,
        source_char_end=180,
    )
    second = stable_source_id(
        page_number=2,
        reading_order=7,
        role="body",
        source_char_start=100,
        source_char_end=180,
    )
    assert first == second == "p2-r7-body-100-180"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_number", True),
        ("page_number", 1.0),
        ("reading_order", False),
        ("reading_order", "7"),
        ("source_char_start", 1.5),
        ("source_char_end", True),
    ],
)
def test_stable_source_id_rejects_non_integer_location_values(
    field: str, value: object
) -> None:
    arguments = {
        "page_number": 2,
        "reading_order": 7,
        "role": "body",
        "source_char_start": 100,
        "source_char_end": 180,
    }
    arguments[field] = value
    with pytest.raises(ValueError, match="integer"):
        stable_source_id(**arguments)
