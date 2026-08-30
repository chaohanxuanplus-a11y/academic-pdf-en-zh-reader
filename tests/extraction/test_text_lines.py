# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.extraction.blocks import (
    GraphicRegion,
    _captions,
    build_basic_blocks,
)
from academic_pdf_en_zh_reader.extraction.page_objects import extract_page_objects
from academic_pdf_en_zh_reader.extraction.text_lines import TextLine, build_text_lines
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"


def _extract(fixture_id: str, tmp_path: Path):
    pdf_path = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", pdf_path)
    pages = extract_page_objects(pdf_path).pages
    return pages, build_text_lines(pages)


def test_lines_preserve_column_separation_and_source_text(tmp_path: Path) -> None:
    _pages, line_pages = _extract("first-page-mixed", tmp_path)
    lines = line_pages[0].lines
    texts = [line.text for line in lines]

    assert "1. Left-column method" in texts
    assert "2. Right-column result" in texts
    assert all(
        not ("Left-column method" in text and "Right-column result" in text)
        for text in texts
    )
    assert all(type(value) is int for line in lines for value in line.bbox_mpt)
    assert len({line.id for line in lines}) == len(lines)


def test_same_baseline_text_across_a_narrow_gutter_stays_in_separate_lines(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "narrow-gutter.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(72, 700, "Left")
    canvas.drawString(101, 700, "Right")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    texts = [line.text for line in build_text_lines(pages)[0].lines]

    assert texts == ["Left", "Right"]


def test_same_baseline_words_with_a_normal_space_form_one_line(tmp_path: Path) -> None:
    pdf_path = tmp_path / "normal-word-gap.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.setFont("Helvetica", 10)
    first = "have"
    second = "recently"
    canvas.drawString(72, 700, first)
    second_x = 72 + canvas.stringWidth(first, "Helvetica", 10) + 8
    canvas.drawString(second_x, 700, second)
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    texts = [line.text for line in build_text_lines(pages)[0].lines]

    assert texts == ["have recently"]


def test_raised_registered_mark_is_restored_as_an_inline_suffix(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "registered-suffix.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    base_x = 72
    base_y = 700
    base_font = 10
    mark_font = 7.5
    product = "PEU Pellethane-2363-80A"
    suffix = "was investigated."
    canvas.setFont("Helvetica", base_font)
    canvas.drawString(base_x, base_y, product)
    mark_x = base_x + canvas.stringWidth(product, "Helvetica", base_font)
    canvas.setFont("Helvetica", mark_font)
    canvas.drawString(mark_x, base_y + 4, "®")
    suffix_x = mark_x + canvas.stringWidth("®", "Helvetica", mark_font) + 2
    canvas.setFont("Helvetica", base_font)
    canvas.drawString(suffix_x, base_y, suffix)
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    texts = [line.text for line in build_text_lines(pages)[0].lines]

    assert texts == ["PEU Pellethane-2363-80A® was investigated."]


def test_raised_registered_mark_without_a_left_neighbor_stays_separate(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "unattached-registered-mark.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(72, 704, "®")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(100, 700, "ordinary text")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    texts = [line.text for line in build_text_lines(pages)[0].lines]

    assert texts == ["®", "ordinary text"]


def test_lowered_digit_between_baseline_letters_is_restored_as_subscript_text(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "subscript-infix.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    base_x = 72
    base_y = 700
    base_font = 10
    mark_font = 7.5
    canvas.setFont("Helvetica", base_font)
    canvas.drawString(base_x, base_y, "CX")
    mark_x = base_x + canvas.stringWidth("CX", "Helvetica", base_font)
    canvas.setFont("Helvetica", mark_font)
    canvas.drawString(mark_x, base_y - 2, "3")
    suffix_x = mark_x + canvas.stringWidth("3", "Helvetica", mark_font) + 0.5
    canvas.setFont("Helvetica", base_font)
    canvas.drawString(suffix_x, base_y, "C chemokine")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    texts = [line.text for line in build_text_lines(pages)[0].lines]

    assert texts == ["CX3C chemokine"]


def test_raised_letter_table_note_marker_is_not_treated_as_an_inline_symbol(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "table-note-marker.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.setFont("Helvetica", 10)
    caption = "Table 1 Molecular mediators"
    canvas.drawString(72, 700, caption)
    marker_x = 72 + canvas.stringWidth(caption, "Helvetica", 10)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(marker_x, 704, "a")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    texts = [line.text for line in build_text_lines(pages)[0].lines]

    assert texts == ["a", "Table 1 Molecular mediators"]


def test_strong_figure_table_caption_and_reference_relationships_are_stable(
    tmp_path: Path,
) -> None:
    pages, line_pages = _extract("figures-and-tables", tmp_path)

    first = build_basic_blocks(pages, line_pages)
    second = build_basic_blocks(pages, line_pages)

    assert first == second
    page = first.pages[0]
    assert {region.kind for region in page.graphic_regions} == {"figure", "table"}
    captions = {caption.kind: caption for caption in page.captions}
    assert captions["figure"].text.startswith("Figure 1.")
    assert captions["table"].text.startswith("Table 1.")
    assert captions["figure"].target_id in {
        region.id for region in page.graphic_regions if region.kind == "figure"
    }
    assert captions["table"].target_id in {
        region.id for region in page.graphic_regions if region.kind == "table"
    }
    # The fixture body does not literally cite either object. Strong-evidence
    # extraction must not turn nearby prose into a guessed reference.
    assert page.references == ()
    caption_line_ids = {
        line_id for caption in page.captions for line_id in caption.line_ids
    }
    caption_lines = [line for line in page.lines if line.id in caption_line_ids]
    assert caption_lines
    assert all(line.body_eligible is False for line in caption_lines)
    assert all(line.coverage_eligible is True for line in caption_lines)


def test_multiline_caption_keeps_all_adjacent_lines_in_source_order() -> None:
    def line(ordinal: int, text: str, top: int) -> TextLine:
        return TextLine(
            id=f"p0001-line-{ordinal:05d}",
            page_number=1,
            text=text,
            bbox_mpt=(72_000, top - 9_000, 300_000, top),
            character_ids=(f"p0001-char-{ordinal:06d}",),
            font_names=("Helvetica",),
            fill_colors=(None,),
            max_font_size_mpt=10_000,
        )

    lines = (
        line(1, "Figure 1. First caption line", 480_000),
        line(2, "second caption line", 471_000),
        line(3, "third caption line", 462_000),
        line(4, "fourth caption line", 453_000),
    )
    region = GraphicRegion(
        id="p0001-figure-0001",
        page_number=1,
        kind="figure",
        bbox_mpt=(72_000, 500_000, 300_000, 650_000),
        evidence="enclosed-vector-drawing",
    )

    captions = _captions(1, lines, (region,))

    assert len(captions) == 1
    assert captions[0].line_ids == tuple(line.id for line in lines)
    assert captions[0].text == " ".join(line.text for line in lines)


def test_multiline_caption_does_not_consume_ambiguous_following_body_line() -> None:
    def line(ordinal: int, text: str, top: int) -> TextLine:
        return TextLine(
            id=f"p0001-line-{ordinal:05d}",
            page_number=1,
            text=text,
            bbox_mpt=(72_000, top - 9_000, 300_000, top),
            character_ids=(f"p0001-char-{ordinal:06d}",),
            font_names=("Helvetica",),
            fill_colors=(None,),
            max_font_size_mpt=10_000,
        )

    lines = (
        line(1, "Figure 1. First caption line", 480_000),
        line(2, "second caption line", 471_000),
        line(3, "third caption line", 462_000),
        line(4, "This paragraph is ordinary body prose.", 453_000),
    )
    region = GraphicRegion(
        id="p0001-figure-0001",
        page_number=1,
        kind="figure",
        bbox_mpt=(72_000, 500_000, 300_000, 650_000),
        evidence="enclosed-vector-drawing",
    )

    captions = _captions(1, lines, (region,))

    assert len(captions) == 1
    assert captions[0].line_ids == tuple(line.id for line in lines[:3])
    assert lines[3].id not in captions[0].line_ids


def test_table_internal_text_is_retained_but_never_body_eligible(
    tmp_path: Path,
) -> None:
    pages, line_pages = _extract("figures-and-tables", tmp_path)
    result = build_basic_blocks(pages, line_pages)
    lines = result.pages[0].lines

    internal = [line for line in lines if "Group" in line.text or "Mean" in line.text]
    assert internal
    assert all(line.container_kind == "table" for line in internal)
    assert all(line.body_eligible is False for line in internal)
    assert all(line.confidence_ppm < 500_000 for line in internal)


def test_literal_body_reference_links_to_numbered_caption_target(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "explicit-reference.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.rect(72, 500, 180, 120)
    canvas.line(84, 512, 84, 600)
    canvas.line(84, 512, 230, 512)
    canvas.line(84, 512, 220, 580)
    canvas.drawString(72, 470, "Figure 1. Explicit synthetic plot.")
    canvas.drawString(72, 420, "As shown in Figure 1, this link is explicit.")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    result = build_basic_blocks(pages, build_text_lines(pages))

    assert len(result.pages[0].references) == 1
    reference = result.pages[0].references[0]
    assert reference.label == "Figure 1"
    assert reference.target_id == result.pages[0].captions[0].target_id


@pytest.mark.parametrize(
    ("reference_page", "figure_page"),
    [(1, 2), (2, 1)],
)
def test_literal_reference_links_across_pages_in_both_directions(
    tmp_path: Path,
    reference_page: int,
    figure_page: int,
) -> None:
    pdf_path = tmp_path / f"cross-page-{reference_page}-{figure_page}.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    for page_number in (1, 2):
        if page_number == figure_page:
            canvas.rect(72, 500, 180, 120)
            canvas.line(84, 512, 84, 600)
            canvas.line(84, 512, 230, 512)
            canvas.drawString(72, 470, "Figure 1. Cross-page synthetic plot.")
        if page_number == reference_page:
            canvas.drawString(72, 400, "As shown in Figure 1, the link is explicit.")
        canvas.showPage()
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    result = build_basic_blocks(pages, build_text_lines(pages))

    target = result.pages[figure_page - 1].captions[0].target_id
    references = result.pages[reference_page - 1].references
    assert len(references) == 1
    assert references[0].target_id == target


def test_distant_caption_like_text_is_not_guessed_as_a_figure_relation(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "distant-caption.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.rect(72, 600, 180, 120)
    canvas.line(84, 612, 84, 700)
    canvas.line(84, 612, 230, 612)
    canvas.drawString(72, 100, "Figure 1. Too far from the drawing.")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    result = build_basic_blocks(pages, build_text_lines(pages))

    assert len(result.pages[0].graphic_regions) == 1
    assert result.pages[0].captions == ()


def test_figure_number_inside_a_drawing_is_not_promoted_to_caption(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "internal-figure-label.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.rect(72, 500, 180, 120)
    canvas.line(84, 512, 84, 600)
    canvas.line(84, 512, 230, 512)
    canvas.drawString(100, 550, "Figure 1 internal label")
    canvas.save()
    pages = extract_page_objects(pdf_path).pages

    result = build_basic_blocks(pages, build_text_lines(pages))

    internal = next(line for line in result.pages[0].lines if line.container_id)
    assert internal.body_eligible is False
    assert result.pages[0].captions == ()
