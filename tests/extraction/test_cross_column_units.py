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

SHA = "a" * 64


def test_parenthetical_infix_continues_across_a_column_boundary() -> None:
    outcome = build_semantic_units(
        _source("The forms were rounded", "(type one), and angular (type two).")
    )
    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    assert len(outcome.artifact["units"]) == 1
    assert outcome.artifact["units"][0]["source_text"] == (
        "The forms were rounded (type one), and angular (type two)."
    )


def test_unclosed_parenthetical_is_still_ambiguous() -> None:
    outcome = build_semantic_units(_source("The forms were rounded", "(type one"))
    assert outcome.status is UnitMergeStatus.NEEDS_UNIT_REVIEW


def test_chemical_formula_is_a_lexical_continuation_not_a_new_heading() -> None:
    outcome = build_semantic_units(
        _source("The concentration was changed by adding", "NaCl to the solution.")
    )
    assert outcome.status is UnitMergeStatus.OK
    assert len(outcome.artifact["units"]) == 1


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


def test_floating_caption_does_not_split_a_hyphenated_body_unit() -> None:
    source = _source("The response showed per-", "sistence over time.")
    page = source["pages"][0]
    second = page["blocks"][1]
    caption = dict(second)
    caption.update(
        id="caption", role="figure-caption", text="Figure 1. Response.", reading_order=1
    )
    caption["source_char_end"] = caption["source_char_start"] + len(caption["text"])
    second["reading_order"] = 2
    second["source_char_start"] = caption["source_char_end"]
    second["source_char_end"] = second["source_char_start"] + len(second["text"])
    second["id"] = stable_source_id(
        page_number=1,
        reading_order=2,
        role="body",
        source_char_start=second["source_char_start"],
        source_char_end=second["source_char_end"],
    )
    caption["id"] = stable_source_id(
        page_number=1,
        reading_order=1,
        role="figure-caption",
        source_char_start=caption["source_char_start"],
        source_char_end=caption["source_char_end"],
    )
    caption["target_graphic_id"] = "graphic"
    page["graphic_nodes"] = [
        {
            "id": "graphic",
            "kind": "figure",
            "bbox_mpt": [315000, 700000, 555000, 750000],
            "confidence_ppm": 990000,
        }
    ]
    page["blocks"] = [page["blocks"][0], caption, second]
    outcome = build_semantic_units(source)
    assert outcome.status is UnitMergeStatus.OK
    assert len(outcome.artifact["units"]) == 2
    assert (
        outcome.artifact["units"][0]["source_text"]
        == "The response showed persistence over time."
    )
    assert outcome.artifact["units"][1]["role"] == "figure-caption"
    tampered_source = deepcopy(source)
    tampered_source["pages"][0]["blocks"][1]["role"] = "heading"
    tampered = deepcopy(outcome.artifact)
    tampered["units"][1]["role"] = "heading"
    with pytest.raises(UnitMappingError, match="skips required non-caption"):
        validate_unit_mapping(tampered_source, tampered)


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
