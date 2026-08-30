# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.extraction import extract_document
from academic_pdf_en_zh_reader.topology.bands import (
    BandGeometry,
    ColumnGeometry,
    PageBands,
    detect_document_bands,
)
from academic_pdf_en_zh_reader.topology.roles import (
    classify_document_lines,
    classify_page_lines,
)
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"


def _page_and_bands(fixture_id: str, tmp_path: Path):
    pdf_path = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", pdf_path)
    document = extract_document(pdf_path)
    return document["pages"][0], detect_document_bands(document)[0]


def _by_role(blocks, role: str):
    return [block for block in blocks if block.role == role]


def _single_column_bands(page_number: int) -> PageBands:
    column = ColumnGeometry(
        id=f"p{page_number:04d}-band-001-col-001",
        index=0,
        x_left_mpt=20_000,
        x_right_mpt=580_000,
        width_ratio_ppm=1_000_000,
        evidence_ids=(),
    )
    band = BandGeometry(
        id=f"p{page_number:04d}-band-001",
        index=0,
        y_top_mpt=800_000,
        y_bottom_mpt=40_000,
        columns=(column,),
        gutters=(),
        score_ppm=1_000_000,
        evidence=("synthetic-test",),
        object_ids=(),
    )
    return PageBands(
        page_number=page_number,
        crop_box_mpt=(0, 0, 600_000, 840_000),
        bands=(band,),
        score_ppm=1_000_000,
        evidence=("synthetic-test",),
    )


def _two_column_bands(page_number: int) -> PageBands:
    columns = (
        ColumnGeometry(
            id=f"p{page_number:04d}-band-001-col-001",
            index=0,
            x_left_mpt=20_000,
            x_right_mpt=290_000,
            width_ratio_ppm=490_909,
            evidence_ids=(),
        ),
        ColumnGeometry(
            id=f"p{page_number:04d}-band-001-col-002",
            index=1,
            x_left_mpt=310_000,
            x_right_mpt=580_000,
            width_ratio_ppm=490_909,
            evidence_ids=(),
        ),
    )
    band = BandGeometry(
        id=f"p{page_number:04d}-band-001",
        index=0,
        y_top_mpt=800_000,
        y_bottom_mpt=40_000,
        columns=columns,
        gutters=((290_000, 310_000),),
        score_ppm=1_000_000,
        evidence=("synthetic-test",),
        object_ids=(),
    )
    return PageBands(
        page_number=page_number,
        crop_box_mpt=(0, 0, 600_000, 840_000),
        bands=(band,),
        score_ppm=1_000_000,
        evidence=("synthetic-test",),
    )


def _real_shape_line(
    identifier: str,
    text: str,
    *,
    y_top_mpt: int,
    font_size_mpt: int,
    bold: bool = False,
    italic: bool = False,
) -> dict[str, object]:
    font = "Journal-Bold" if bold else "Journal-Italic" if italic else "Journal-Roman"
    return {
        "id": identifier,
        "page_number": 1,
        "text": text,
        "bbox_mpt": [40_000, y_top_mpt - font_size_mpt, 560_000, y_top_mpt],
        "character_ids": [f"{identifier}-char"],
        "font_names": [font],
        "fill_colors": [[0, 0, 0]],
        "max_font_size_mpt": font_size_mpt,
        "confidence_ppm": 1_000_000,
        "body_eligible": True,
        "coverage_eligible": True,
        "exclusion_kind": None,
        "container_kind": None,
        "container_id": None,
    }


def test_single_column_classifies_and_merges_academic_roles(tmp_path: Path) -> None:
    page, page_bands = _page_and_bands("single-column", tmp_path)

    blocks = classify_page_lines(page, page_bands)

    assert [block.text for block in _by_role(blocks, "title")] == [
        "A Synthetic Study of Stable Paper Sensor Calibration"
    ]
    assert [block.text for block in _by_role(blocks, "author")] == [
        "A. Example and B. Example"
    ]
    assert [block.text for block in _by_role(blocks, "abstract")] == [
        "We evaluate a fully synthetic paper sensor under fixed laboratory conditions. "
        "The invented measurements support deterministic layout tests only."
    ]
    assert [block.text for block in _by_role(blocks, "keywords")] == [
        "Keywords: synthetic fixture; calibration; deterministic PDF"
    ]
    assert [block.text for block in _by_role(blocks, "heading")] == [
        "Abstract",
        "1. Methods",
        "2. Conclusion",
    ]
    assert len(_by_role(blocks, "body")) == 2
    assert len(_by_role(blocks, "body")[0].line_ids) == 3
    assert all(
        block.translation_policy == "required"
        for block in blocks
        if block.role in {"title", "abstract", "keywords", "heading", "body"}
    )


def test_mixed_page_keeps_body_merges_within_one_column(tmp_path: Path) -> None:
    page, page_bands = _page_and_bands("first-page-mixed", tmp_path)

    blocks = classify_page_lines(page, page_bands)

    bodies = _by_role(blocks, "body")
    assert len(bodies) == 2
    assert {block.column_id for block in bodies} == {
        "p0001-band-002-col-001",
        "p0001-band-002-col-002",
    }
    assert all(len(block.line_ids) == 3 for block in bodies)
    assert {block.band_id for block in bodies} == {"p0001-band-002"}
    assert [block.text for block in _by_role(blocks, "abstract")] == [
        "The upper band spans the page while the lower band uses two columns. "
        "This invented layout provides an explicit transition for topology tests."
    ]


def test_tiny_adjacent_glyph_overlap_does_not_split_one_body_block(
    tmp_path: Path,
) -> None:
    page, page_bands = _page_and_bands("single-column", tmp_path)
    initial = classify_page_lines(page, page_bands)
    first_body = _by_role(initial, "body")[0]
    first_id, second_id = first_body.line_ids[:2]
    by_id = {line["id"]: line for line in page["lines"]}
    first_box = by_id[first_id]["bbox_mpt"]
    second_box = by_id[second_id]["bbox_mpt"]
    second_height = second_box[3] - second_box[1]
    second_box[3] = first_box[1] + 170
    second_box[1] = second_box[3] - second_height

    blocks = classify_page_lines(page, page_bands)

    assert any(
        first_id in block.line_ids and second_id in block.line_ids
        for block in _by_role(blocks, "body")
    )


def test_indented_body_line_starts_a_new_block_despite_tiny_overlap(
    tmp_path: Path,
) -> None:
    page, page_bands = _page_and_bands("single-column", tmp_path)
    initial = classify_page_lines(page, page_bands)
    first_body = _by_role(initial, "body")[0]
    first_id, second_id = first_body.line_ids[:2]
    by_id = {line["id"]: line for line in page["lines"]}
    first_box = by_id[first_id]["bbox_mpt"]
    second_box = by_id[second_id]["bbox_mpt"]
    second_height = second_box[3] - second_box[1]
    second_box[3] = first_box[1] + 170
    second_box[1] = second_box[3] - second_height
    second_box[0] += 12_000

    blocks = classify_page_lines(page, page_bands)
    bodies = _by_role(blocks, "body")

    assert any(first_id in block.line_ids for block in bodies)
    assert any(second_id in block.line_ids for block in bodies)
    assert not any(
        first_id in block.line_ids and second_id in block.line_ids for block in bodies
    )


def test_figure_table_relations_and_internal_text_are_explicit(tmp_path: Path) -> None:
    page, page_bands = _page_and_bands("figures-and-tables", tmp_path)
    figure = next(
        graphic for graphic in page["graphic_regions"] if graphic["kind"] == "figure"
    )
    figure_text = {
        "id": "p0001-line-90000",
        "page_number": 1,
        "text": "invented axis label",
        "bbox_mpt": [70_000, 500_000, 130_000, 508_000],
        "character_ids": ["p0001-char-900000"],
        "font_names": ["Synthetic-Regular"],
        "fill_colors": [[0, 0, 0]],
        "max_font_size_mpt": 8_000,
        "confidence_ppm": 400_000,
        "body_eligible": False,
        "coverage_eligible": False,
        "exclusion_kind": None,
        "container_kind": "figure",
        "container_id": figure["id"],
    }
    page["lines"].append(figure_text)

    blocks = classify_page_lines(page, page_bands)

    figure_caption = _by_role(blocks, "figure-caption")
    table_caption = _by_role(blocks, "table-caption")
    assert len(figure_caption) == len(table_caption) == 1
    assert len(figure_caption[0].line_ids) == 2
    assert len(table_caption[0].line_ids) == 2
    assert figure_caption[0].target_graphic_id == "p0001-figure-0001"
    assert table_caption[0].target_graphic_id == "p0001-table-0002"
    assert all(
        block.translation_policy == "required"
        for block in [*figure_caption, *table_caption]
    )

    table_cells = _by_role(blocks, "table-cell")
    assert {block.text for block in table_cells} == {
        "Group",
        "Mean",
        "Range",
        "A",
        "4.2",
        "3-5",
    }
    assert all(block.target_graphic_id == "p0001-table-0002" for block in table_cells)
    assert [block.text for block in _by_role(blocks, "figure-text")] == [
        "invented axis label"
    ]
    assert all(
        block.translation_policy == "excluded"
        for block in [*table_cells, *_by_role(blocks, "figure-text")]
    )


@pytest.mark.parametrize(
    ("exclusion_kind", "expected_role"),
    [
        ("repeated-header", "header"),
        ("repeated-footer", "footer"),
        ("page-number", "page-number"),
        ("watermark", "watermark"),
        ("footnote", "footnote"),
    ],
)
def test_extraction_exclusion_kind_has_priority(
    exclusion_kind: str,
    expected_role: str,
) -> None:
    line = {
        "id": "p0001-line-00001",
        "page_number": 1,
        "text": "1. Looks like a heading",
        "bbox_mpt": [20_000, 360_000, 180_000, 370_000],
        "max_font_size_mpt": 20_000,
        "confidence_ppm": 1_000_000,
        "body_eligible": False,
        "coverage_eligible": False,
        "exclusion_kind": exclusion_kind,
        "container_kind": None,
        "container_id": None,
    }
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 200_000, 400_000],
        "lines": [line],
        "graphic_regions": [],
    }
    page_bands = detect_document_bands({"pages": [page]})[0]

    [block] = classify_page_lines(page, page_bands)

    assert block.role == expected_role
    assert block.translation_policy == "excluded"


def test_shuffled_input_produces_identical_stable_blocks(tmp_path: Path) -> None:
    page, page_bands = _page_and_bands("figures-and-tables", tmp_path)
    shuffled = deepcopy(page)
    for key in ("lines", "captions", "graphic_regions"):
        shuffled[key] = list(reversed(shuffled[key]))

    first = classify_page_lines(page, page_bands)
    second = classify_page_lines(shuffled, page_bands)

    assert [asdict(block) for block in first] == [asdict(block) for block in second]
    assert [block.source_ordinal for block in first] == list(range(1, len(first) + 1))
    assert len({block.id for block in first}) == len(first)
    assert all(block.first_line_bbox_mpt for block in first)


def test_synthetic_front_matter_and_wrapped_heading_roles_are_separated() -> None:
    lines = [
        _real_shape_line(
            "journal",
            "Journal of Imaginary Interface Studies 42 (2099) 101–115",
            y_top_mpt=780_000,
            font_size_mpt=8_000,
        ),
        _real_shape_line(
            "article-type",
            "Review",
            y_top_mpt=750_000,
            font_size_mpt=12_000,
        ),
        _real_shape_line(
            "title",
            "Synthetic interface response to imaginary materials",
            y_top_mpt=720_000,
            font_size_mpt=17_000,
        ),
        _real_shape_line(
            "author-marker",
            "a,b,∗,1",
            y_top_mpt=695_000,
            font_size_mpt=10_000,
        ),
        _real_shape_line(
            "authors",
            "Avery Example, Bailey Sample, Casey Fiction",
            y_top_mpt=682_000,
            font_size_mpt=13_000,
        ),
        _real_shape_line(
            "affiliation-marker",
            "a",
            y_top_mpt=665_000,
            font_size_mpt=6_000,
        ),
        _real_shape_line(
            "affiliation",
            "Department of Imaginary Materials, Example University",
            y_top_mpt=655_000,
            font_size_mpt=8_000,
            italic=True,
        ),
        _real_shape_line(
            "abstract-label",
            "Abstract",
            y_top_mpt=620_000,
            font_size_mpt=9_000,
            bold=True,
        ),
        _real_shape_line(
            "abstract-text",
            "A synthetic overview of an imaginary interface response is presented.",
            y_top_mpt=605_000,
            font_size_mpt=9_000,
        ),
        _real_shape_line(
            "keywords",
            "Keywords: synthetic interface; example particles",
            y_top_mpt=580_000,
            font_size_mpt=8_000,
        ),
        _real_shape_line(
            "heading-first",
            "1. Introduction: synthetic response following",
            y_top_mpt=545_000,
            font_size_mpt=10_000,
            bold=True,
        ),
        _real_shape_line(
            "heading-continuation",
            "imaginary material placement",
            y_top_mpt=533_000,
            font_size_mpt=10_000,
            bold=True,
        ),
        _real_shape_line(
            "body",
            "The perspective of this review originates from a fictional dataset.",
            y_top_mpt=510_000,
            font_size_mpt=10_000,
        ),
        _real_shape_line(
            "footnote-marker",
            "∗",
            y_top_mpt=160_000,
            font_size_mpt=6_000,
        ),
        _real_shape_line(
            "corresponding",
            "Corresponding author at: Example University.",
            y_top_mpt=150_000,
            font_size_mpt=8_000,
        ),
        _real_shape_line(
            "email",
            "E-mail address: corresponding@example.invalid.",
            y_top_mpt=140_000,
            font_size_mpt=8_000,
        ),
        _real_shape_line(
            "equal-contribution",
            "1 All authors contributed equally to the preparation of this review.",
            y_top_mpt=130_000,
            font_size_mpt=8_000,
        ),
        _real_shape_line(
            "issn",
            "0000-0000/$ – synthetic front matter © 2099 Fictional Press Ltd.",
            y_top_mpt=105_000,
            font_size_mpt=8_000,
        ),
        _real_shape_line(
            "doi",
            "doi:10.0000/j.synthetic.2099.001",
            y_top_mpt=95_000,
            font_size_mpt=8_000,
        ),
    ]
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 600_000, 840_000],
        "lines": lines,
        "captions": [],
        "graphic_regions": [],
    }

    blocks = classify_page_lines(page, _single_column_bands(1))

    assert [block.text for block in _by_role(blocks, "title")] == [
        "Synthetic interface response to imaginary materials"
    ]
    assert {block.text for block in _by_role(blocks, "bibliographic-metadata")} == {
        "Journal of Imaginary Interface Studies 42 (2099) 101–115",
        "Review",
        "0000-0000/$ – synthetic front matter © 2099 Fictional Press Ltd.",
        "doi:10.0000/j.synthetic.2099.001",
    }
    assert [block.text for block in _by_role(blocks, "author")] == [
        "a,b,∗,1 Avery Example, Bailey Sample, Casey Fiction"
    ]
    assert [block.text for block in _by_role(blocks, "affiliation")] == [
        "a Department of Imaginary Materials, Example University"
    ]
    assert [block.text for block in _by_role(blocks, "heading")] == [
        "Abstract",
        "1. Introduction: synthetic response following imaginary material placement",
    ]
    assert {block.text for block in _by_role(blocks, "footnote")} == {
        "∗",
        "Corresponding author at: Example University.",
        "E-mail address: corresponding@example.invalid.",
        "1 All authors contributed equally to the preparation of this review.",
    }
    assert all(
        block.translation_policy == "excluded"
        for block in blocks
        if block.role in {"author", "affiliation", "bibliographic-metadata", "footnote"}
    )


@pytest.mark.parametrize(
    ("first", "continuation"),
    [
        ("2. Monocytes, macrophages, and foreign body giant", "cells"),
        (
            "3. Cross-talk between macrophages/FBGCS and",
            "inflammatory/wound healing cells",
        ),
    ],
)
def test_bold_numbered_heading_continuation_is_one_complete_heading(
    first: str,
    continuation: str,
) -> None:
    page = {
        "page_number": 2,
        "crop_box_mpt": [0, 0, 600_000, 840_000],
        "lines": [
            {
                **_real_shape_line(
                    "heading-first",
                    first,
                    y_top_mpt=500_000,
                    font_size_mpt=10_000,
                    bold=True,
                ),
                "page_number": 2,
            },
            {
                **_real_shape_line(
                    "heading-second",
                    continuation,
                    y_top_mpt=488_000,
                    font_size_mpt=10_000,
                    bold=True,
                ),
                "page_number": 2,
            },
            {
                **_real_shape_line(
                    "body",
                    "Ordinary paragraph text follows the section heading.",
                    y_top_mpt=465_000,
                    font_size_mpt=10_000,
                ),
                "page_number": 2,
            },
        ],
        "captions": [],
        "graphic_regions": [],
    }

    blocks = classify_page_lines(page, _single_column_bands(2))

    assert [block.text for block in _by_role(blocks, "heading")] == [
        f"{first} {continuation}"
    ]
    assert [block.text for block in _by_role(blocks, "body")] == [
        "Ordinary paragraph text follows the section heading."
    ]


def test_parenthesized_citation_number_is_body_not_a_numbered_heading() -> None:
    page = {
        "page_number": 9,
        "crop_box_mpt": [0, 0, 600_000, 840_000],
        "lines": [
            {
                **_real_shape_line(
                    "tlr4",
                    "4 (TLR4) [129] and can stimulate macrophages to become classical.",
                    y_top_mpt=500_000,
                    font_size_mpt=10_000,
                ),
                "page_number": 9,
            }
        ],
        "captions": [],
        "graphic_regions": [],
    }

    [block] = classify_page_lines(page, _single_column_bands(9))

    assert block.role == "body"


def test_reference_state_excludes_all_following_entries_across_pages() -> None:
    pages = []
    for page_number, texts in (
        (
            11,
            (
                "Concluding body paragraph.",
                "References",
                "[1] First citation.",
                "2004. p. 237–46.",
            ),
        ),
        (12, ("continued citation text.", "[2] Second citation.")),
    ):
        lines = []
        for index, text in enumerate(texts):
            line = _real_shape_line(
                f"p{page_number}-line-{index}",
                text,
                y_top_mpt=700_000 - index * 30_000,
                font_size_mpt=12_000 if text == "References" else 8_000,
                bold=text == "References",
            )
            line["page_number"] = page_number
            lines.append(line)
        pages.append(
            {
                "page_number": page_number,
                "crop_box_mpt": [0, 0, 600_000, 840_000],
                "lines": lines,
                "captions": [],
                "graphic_regions": [],
            }
        )

    first, second = classify_document_lines(
        pages,
        (_single_column_bands(11), _single_column_bands(12)),
    )

    assert [(block.text, block.role) for block in first] == [
        ("Concluding body paragraph.", "body"),
        ("References", "heading"),
        ("[1] First citation.", "reference-entry"),
        ("2004. p. 237–46.", "reference-entry"),
    ]
    assert [(block.text, block.role) for block in second] == [
        ("continued citation text.", "reference-entry"),
        ("[2] Second citation.", "reference-entry"),
    ]
    assert all(
        block.translation_policy == "excluded"
        for block in (*first, *second)
        if block.role == "reference-entry"
    )


def test_acknowledgements_are_excluded_before_a_preserved_references_heading() -> None:
    rows = (
        (
            "conclusion",
            "Concluding body paragraph.",
            [32_000, 160_000, 284_000, 168_000],
            8_000,
            False,
        ),
        (
            "ack-heading",
            "Acknowledgements",
            [32_000, 133_000, 116_000, 143_000],
            10_000,
            True,
        ),
        (
            "ack-body",
            "We acknowledge the support from the National Institute of Health.",
            [45_000, 109_000, 284_000, 117_000],
            8_000,
            False,
        ),
        (
            "references-heading",
            "References",
            [302_000, 743_000, 349_000, 753_000],
            10_000,
            True,
        ),
        (
            "reference-entry",
            "[1] First citation.",
            [302_000, 719_000, 560_000, 727_000],
            8_000,
            False,
        ),
    )
    lines = []
    for identifier, text, bbox, size, bold in rows:
        line = _real_shape_line(
            identifier,
            text,
            y_top_mpt=bbox[3],
            font_size_mpt=size,
            bold=bold,
        )
        line["page_number"] = 11
        line["bbox_mpt"] = bbox
        lines.append(line)
    page = {
        "page_number": 11,
        "crop_box_mpt": [0, 0, 600_000, 840_000],
        "lines": lines,
        "captions": [],
        "graphic_regions": [],
    }

    [blocks] = classify_document_lines([page], (_two_column_bands(11),))

    assert [(block.text, block.role, block.translation_policy) for block in blocks] == [
        ("Concluding body paragraph.", "body", "required"),
        ("Acknowledgements", "acknowledgements", "excluded"),
        (
            "We acknowledge the support from the National Institute of Health.",
            "acknowledgements",
            "excluded",
        ),
        ("References", "heading", "required"),
        ("[1] First citation.", "reference-entry", "excluded"),
    ]
