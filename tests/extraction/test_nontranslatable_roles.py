# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.extraction.unit_mapping import (
    UnitMappingError,
    validate_unit_mapping,
)
from academic_pdf_en_zh_reader.extraction.unit_merge import (
    UnitMergeStatus,
    build_semantic_units,
)
from academic_pdf_en_zh_reader.job.hashing import stable_source_id
from academic_pdf_en_zh_reader.schema.validate import validate_artifact

SHA = "c" * 64
EXPLICIT_EXCLUSIONS = (
    "author",
    "affiliation",
    "header",
    "footer",
    "footnote",
    "endnote",
    "acknowledgements",
    "reference-entry",
    "equation",
    "variable",
    "code",
    "chemical-formula",
    "pure-data",
)


def _source() -> dict[str, object]:
    blocks: list[dict[str, object]] = []
    cursor = 0
    for reading_order, role in enumerate((*EXPLICIT_EXCLUSIONS, "body")):
        text = "Required paragraph." if role == "body" else f"Excluded {role}"
        end = cursor + len(text)
        blocks.append(
            {
                "id": stable_source_id(
                    page_number=1,
                    reading_order=reading_order,
                    role=role,
                    source_char_start=cursor,
                    source_char_end=end,
                ),
                "role": role,
                "translation_policy": "required" if role == "body" else "excluded",
                "band_id": "p1-band-0",
                "column_id": "p1-col-0",
                "reading_order": reading_order,
                "source_char_start": cursor,
                "source_char_end": end,
                "text": text,
                "bbox_mpt": [40_000, 500_000, 300_000, 520_000],
                "first_line_bbox_mpt": [40_000, 500_000, 300_000, 520_000],
                "confidence_ppm": 970_000,
            }
        )
        cursor = end
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": SHA,
        "normalized_pdf_sha256": "b" * 64,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "p1-band-0",
                        "y_top_mpt": 800_000,
                        "y_bottom_mpt": 40_000,
                        "columns": [
                            {
                                "id": "p1-col-0",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 555_276,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": blocks,
            }
        ],
    }


def test_all_explicit_nontranslatable_roles_are_excluded_without_omissions() -> None:
    source = _source()
    validate_artifact("source", source)

    outcome = build_semantic_units(source)

    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    validate_artifact("units", outcome.artifact)
    assert [unit["role"] for unit in outcome.artifact["units"]] == ["body"]
    validate_unit_mapping(source, outcome.artifact)


def test_mapping_rejects_missing_or_duplicate_required_source_fragments() -> None:
    source = _source()
    outcome = build_semantic_units(source)
    assert outcome.artifact is not None

    missing = deepcopy(outcome.artifact)
    missing["units"] = []
    with pytest.raises(UnitMappingError, match="missing required source block"):
        validate_unit_mapping(source, missing)

    duplicate = deepcopy(outcome.artifact)
    duplicate["units"].append(deepcopy(duplicate["units"][0]))
    duplicate["units"][1]["id"] = "duplicate-unit"
    duplicate["units"][1]["reading_order"] += 1
    with pytest.raises(UnitMappingError, match="mapped more than once"):
        validate_unit_mapping(source, duplicate)


def test_mapping_rejects_an_excluded_source_fragment() -> None:
    source = _source()
    outcome = build_semantic_units(source)
    assert outcome.artifact is not None
    invalid = deepcopy(outcome.artifact)
    excluded = source["pages"][0]["blocks"][0]
    invalid["units"][0]["fragments"].append(
        {
            "page_number": 1,
            "block_id": excluded["id"],
            "source_char_start": excluded["source_char_start"],
            "source_char_end": excluded["source_char_end"],
        }
    )

    with pytest.raises(UnitMappingError, match="excluded source block"):
        validate_unit_mapping(source, invalid)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "invented-unit-id", "id"),
        ("reading_order", 999, "reading order"),
        ("source_text", "Invented source text.", "source text"),
        ("confidence_ppm", 1, "confidence"),
    ],
)
def test_mapping_rejects_tampered_unit_derived_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    source = _source()
    outcome = build_semantic_units(source)
    assert outcome.artifact is not None
    invalid = deepcopy(outcome.artifact)
    invalid["units"][0][field] = value

    with pytest.raises(UnitMappingError, match=message):
        validate_unit_mapping(source, invalid)
