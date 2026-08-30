# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.extraction.unit_merge import (
    UnitMergeStatus,
    build_semantic_units,
)
from academic_pdf_en_zh_reader.job.hashing import stable_source_id
from academic_pdf_en_zh_reader.schema.validate import validate_artifact

SHA = "a" * 64


def _block(
    *,
    reading_order: int,
    text: str,
    column_id: str,
    bbox_mpt: list[int],
    source_char_start: int,
) -> dict[str, object]:
    source_char_end = source_char_start + len(text)
    return {
        "id": stable_source_id(
            page_number=1,
            reading_order=reading_order,
            role="body",
            source_char_start=source_char_start,
            source_char_end=source_char_end,
        ),
        "role": "body",
        "translation_policy": "required",
        "band_id": "p1-band-0",
        "column_id": column_id,
        "reading_order": reading_order,
        "source_char_start": source_char_start,
        "source_char_end": source_char_end,
        "text": text,
        "bbox_mpt": bbox_mpt,
        "first_line_bbox_mpt": bbox_mpt,
        "confidence_ppm": 990_000,
    }


def _source(
    first_text: str,
    second_text: str,
    *,
    second_x0_mpt: int = 315_000,
) -> dict[str, object]:
    first = _block(
        reading_order=0,
        text=first_text,
        column_id="p1-col-0",
        bbox_mpt=[40_000, 45_000, 280_000, 75_000],
        source_char_start=0,
    )
    second = _block(
        reading_order=1,
        text=second_text,
        column_id="p1-col-1",
        bbox_mpt=[second_x0_mpt, 765_000, 555_000, 795_000],
        source_char_start=int(first["source_char_end"]),
    )
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
                                "x_right_mpt": 280_000,
                            },
                            {
                                "id": "p1-col-1",
                                "x_left_mpt": 315_000,
                                "x_right_mpt": 555_276,
                            },
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": [first, second],
            }
        ],
    }


def test_hyphenated_word_merges_across_columns_with_complete_fragments() -> None:
    source = _source("The observed microstruc-", "ture remained stable.")
    validate_artifact("source", source)

    outcome = build_semantic_units(source)

    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    validate_artifact("units", outcome.artifact)
    assert outcome.artifact["normalized_pdf_sha256"] == source["normalized_pdf_sha256"]
    assert len(outcome.artifact["units"]) == 1
    unit = outcome.artifact["units"][0]
    assert unit["source_text"] == "The observed microstructure remained stable."
    assert [fragment["block_id"] for fragment in unit["fragments"]] == [
        block["id"] for block in source["pages"][0]["blocks"]
    ]


def test_explicit_hyphenated_word_merges_when_left_column_ends_early() -> None:
    source = _source("The response showed per-", "sistence over time.")
    first = source["pages"][0]["blocks"][0]
    first["bbox_mpt"] = [40_000, 180_000, 280_000, 210_000]
    first["first_line_bbox_mpt"] = list(first["bbox_mpt"])

    outcome = build_semantic_units(source)

    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    assert len(outcome.artifact["units"]) == 1
    assert outcome.artifact["units"][0]["source_text"] == (
        "The response showed persistence over time."
    )


def test_sentence_end_indentation_list_and_quote_are_new_units() -> None:
    cases = (
        ("The first result was stable.", "The second analysis continued.", 315_000),
        ("The analysis further showed", "A new paragraph begins here.", 330_000),
        ("The analysis further showed", "1. Sensitivity analysis followed.", 315_000),
        ("The analysis further showed", '"Quoted evidence begins here."', 315_000),
    )
    for first, second, second_x0 in cases:
        outcome = build_semantic_units(_source(first, second, second_x0_mpt=second_x0))
        assert outcome.status is UnitMergeStatus.OK
        assert outcome.artifact is not None
        assert len(outcome.artifact["units"]) == 2


def test_uppercase_continuation_without_boundary_evidence_needs_review() -> None:
    source = _source("The analysis involved", "Bayesian models and priors.")

    outcome = build_semantic_units(source)

    assert outcome.status is UnitMergeStatus.NEEDS_UNIT_REVIEW
    assert outcome.artifact is None
    assert len(outcome.issues) == 1
    assert outcome.issues[0].left_block_id == source["pages"][0]["blocks"][0]["id"]
    assert outcome.issues[0].right_block_id == source["pages"][0]["blocks"][1]["id"]


def test_uppercase_continuation_after_coordinating_conjunction_merges() -> None:
    outcome = build_semantic_units(
        _source(
            "Macrophages respond to chemokines and",
            "T lymphocyte-derived cytokines.",
        )
    )

    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    assert len(outcome.artifact["units"]) == 1
    assert outcome.artifact["units"][0]["source_text"] == (
        "Macrophages respond to chemokines and T lymphocyte-derived cytokines."
    )
