# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.extraction.unit_merge import (
    UnitMergeStatus,
    build_semantic_units,
)
from academic_pdf_en_zh_reader.job.hashing import stable_source_id
from academic_pdf_en_zh_reader.schema.validate import validate_artifact

SHA = "b" * 64


def _block(
    *,
    page_number: int,
    reading_order: int,
    role: str,
    policy: str,
    text: str,
    bbox_mpt: list[int],
    source_char_start: int,
) -> dict[str, object]:
    source_char_end = source_char_start + len(text)
    return {
        "id": stable_source_id(
            page_number=page_number,
            reading_order=reading_order,
            role=role,
            source_char_start=source_char_start,
            source_char_end=source_char_end,
        ),
        "role": role,
        "translation_policy": policy,
        "band_id": f"p{page_number}-band-0",
        "column_id": f"p{page_number}-col-0",
        "reading_order": reading_order,
        "source_char_start": source_char_start,
        "source_char_end": source_char_end,
        "text": text,
        "bbox_mpt": bbox_mpt,
        "first_line_bbox_mpt": bbox_mpt,
        "confidence_ppm": 980_000,
    }


def _page(page_number: int, blocks: list[dict[str, object]]) -> dict[str, object]:
    return {
        "page_number": page_number,
        "media_box_mpt": [0, 0, 595_276, 841_890],
        "crop_box_mpt": [0, 0, 595_276, 841_890],
        "rotation_degrees": 0,
        "bands": [
            {
                "id": f"p{page_number}-band-0",
                "y_top_mpt": 800_000,
                "y_bottom_mpt": 40_000,
                "columns": [
                    {
                        "id": f"p{page_number}-col-0",
                        "x_left_mpt": 40_000,
                        "x_right_mpt": 555_276,
                    }
                ],
            }
        ],
        "graphic_nodes": [],
        "blocks": blocks,
    }


def _cross_page_source(
    second_text: str = "that the effect is robust.",
) -> dict[str, object]:
    page_one_body = _block(
        page_number=1,
        reading_order=0,
        role="body",
        policy="required",
        text="These findings indicate",
        bbox_mpt=[40_000, 45_000, 555_000, 75_000],
        source_char_start=0,
    )
    footer = _block(
        page_number=1,
        reading_order=1,
        role="footer",
        policy="excluded",
        text="Journal footer",
        bbox_mpt=[200_000, 10_000, 350_000, 25_000],
        source_char_start=int(page_one_body["source_char_end"]),
    )
    header = _block(
        page_number=2,
        reading_order=2,
        role="header",
        policy="excluded",
        text="Journal header",
        bbox_mpt=[200_000, 815_000, 350_000, 830_000],
        source_char_start=0,
    )
    page_two_body = _block(
        page_number=2,
        reading_order=3,
        role="body",
        policy="required",
        text=second_text,
        bbox_mpt=[40_000, 765_000, 555_000, 795_000],
        source_char_start=int(header["source_char_end"]),
    )
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": SHA,
        "normalized_pdf_sha256": "b" * 64,
        "pages": [
            _page(1, [page_one_body, footer]),
            _page(2, [header, page_two_body]),
        ],
    }


def test_body_merges_across_pages_while_headers_and_footers_are_skipped() -> None:
    source = _cross_page_source()
    validate_artifact("source", source)

    outcome = build_semantic_units(source)

    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    validate_artifact("units", outcome.artifact)
    unit = outcome.artifact["units"][0]
    assert unit["source_text"] == "These findings indicate that the effect is robust."
    assert [fragment["page_number"] for fragment in unit["fragments"]] == [1, 2]
    assert {fragment["block_id"] for fragment in unit["fragments"]}.isdisjoint(
        {
            source["pages"][0]["blocks"][1]["id"],
            source["pages"][1]["blocks"][0]["id"],
        }
    )


def test_units_do_not_encode_english_source_break_constraints() -> None:
    outcome = build_semantic_units(_cross_page_source())

    assert outcome.artifact is not None
    unit = outcome.artifact["units"][0]
    assert set(unit) == {
        "id",
        "role",
        "reading_order",
        "source_text",
        "confidence_ppm",
        "fragments",
    }
    assert all(
        set(fragment)
        == {"page_number", "block_id", "source_char_start", "source_char_end"}
        for fragment in unit["fragments"]
    )


def test_same_column_blocks_away_from_a_page_edge_are_not_merged() -> None:
    first = _block(
        page_number=1,
        reading_order=0,
        role="body",
        policy="required",
        text="One paragraph continues",
        bbox_mpt=[40_000, 600_000, 555_000, 630_000],
        source_char_start=0,
    )
    second = _block(
        page_number=1,
        reading_order=1,
        role="body",
        policy="required",
        text="with lowercase words.",
        bbox_mpt=[40_000, 540_000, 555_000, 570_000],
        source_char_start=int(first["source_char_end"]),
    )
    source = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": SHA,
        "normalized_pdf_sha256": "b" * 64,
        "pages": [_page(1, [first, second])],
    }

    outcome = build_semantic_units(source)

    assert outcome.status is UnitMergeStatus.OK
    assert outcome.artifact is not None
    assert len(outcome.artifact["units"]) == 2


def test_uppercase_cross_page_continuation_needs_review() -> None:
    outcome = build_semantic_units(
        _cross_page_source("Bayesian models were then fitted.")
    )

    assert outcome.status is UnitMergeStatus.NEEDS_UNIT_REVIEW
    assert outcome.artifact is None
