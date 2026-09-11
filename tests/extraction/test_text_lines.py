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


@pytest.mark.parametrize("marker", ["-1", "2+", "3/2"])
def test_inline_numeric_superscript_group_preserves_prose_order(
    tmp_path: Path, marker: str
) -> None:
    path = tmp_path / "numeric-suffix.pdf"
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    prefix = "The unit is cm"
    canvas.setFont("Helvetica", 10)
    canvas.drawString(72, 700, prefix)
    x = 72 + canvas.stringWidth(prefix, "Helvetica", 10)
    canvas.setFont("Helvetica", 6)
    canvas.drawString(x, 705, marker)
    x += canvas.stringWidth(marker, "Helvetica", 6)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(x, 700, ".")
    canvas.save()
    lines = build_text_lines(extract_page_objects(path).pages)[0].lines
    assert [line.text for line in lines] == [prefix + marker + "."]


@pytest.mark.parametrize(
    "prefix,marker,suffix",
    [
        ("Zn(OH)", "2", ", and oxide."),
        ("The D", "max", " value."),
        ("Zn 2p", "3/2", " spectra."),
        ("ZnCl", "2,", " and water."),
    ],
)
def test_lowered_suffix_group_stays_in_the_prose_line(
    tmp_path: Path, prefix: str, marker: str, suffix: str
) -> None:
    path = tmp_path / "lowered-suffix.pdf"
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(72, 700, prefix)
    x = 72 + canvas.stringWidth(prefix, "Helvetica", 10)
    canvas.setFont("Helvetica", 6)
    canvas.drawString(x, 698, marker)
    x += canvas.stringWidth(marker, "Helvetica", 6)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(x, 700, suffix)
    canvas.save()
    lines = build_text_lines(extract_page_objects(path).pages)[0].lines
    assert [line.text for line in lines] == [prefix + marker + suffix]


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


def test_bold_caption_continuation_can_start_uppercase_left_of_drawing() -> None:
    def line(i: int, text: str, top: int, right: int) -> TextLine:
        return TextLine(
            f"l{i}",
            1,
            text,
            (72000, top - 9000, right, top),
            (f"c{i}",),
            ("Helvetica-Bold",),
            (None,),
            9000,
        )

    lines = (
        line(1, "Fig. 1. Complete description.", 480000, 300000),
        line(2, "The inset shows the measured profile.", 469000, 310000),
        line(3, "(f) SR.", 458000, 95000),
    )
    graphic = GraphicRegion(
        "g1", 1, "figure", (110000, 500000, 320000, 650000), "enclosed-vector-drawing"
    )
    assert _captions(1, lines, (graphic,))[0].line_ids == ("l1", "l2", "l3")


def test_numbered_equation_fragments_are_excluded_but_explanation_is_retained() -> None:
    from academic_pdf_en_zh_reader.extraction.blocks import _mark_equation_lines

    def line(
        i: int, text: str, box: tuple[int, int, int, int], font: str = "Helvetica"
    ) -> TextLine:
        return TextLine(f"l{i}", 1, text, box, (f"c{i}",), (font,), (None,), 10000)

    lines = (
        line(
            1, "The response is calculated as follows:", (72000, 700000, 290000, 710000)
        ),
        line(2, "R", (85000, 673000, 92000, 683000)),
        line(3, "=", (96000, 674000, 103000, 684000), "Symbol"),
        line(4, "a + b", (110000, 678000, 155000, 688000)),
        line(5, "(1)", (278000, 673000, 290000, 683000)),
        line(
            6, "where a and b are measured quantities.", (72000, 650000, 290000, 660000)
        ),
        line(7, "The", (72000, 630000, 89000, 640000)),
    )
    result = _mark_equation_lines(lines)
    assert all(item.exclusion_kind == "equation" for item in result[1:5])
    assert all(item.coverage_eligible for item in (result[0], result[5], result[6]))


def test_inline_display_equation_keeps_its_prose_prefix_separate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inline-equation.pdf"
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica", 10)
    prefix = "The result is expressed as "
    c.drawString(72, 700, prefix)
    x = 72 + c.stringWidth(prefix, "Helvetica", 10)
    c.setFont("Courier", 10)
    c.drawString(x, 700, "CR")
    c.drawString(x + 15, 697, "= a + b")
    c.save()
    lines = build_text_lines(extract_page_objects(path).pages)[0].lines
    assert any(line.text == prefix.strip() for line in lines)
    assert any(line.text == "CR" for line in lines)


def test_inline_statistic_with_its_own_operator_is_not_split(tmp_path: Path) -> None:
    path = tmp_path / "inline-statistic.pdf"
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica", 10)
    prefix = "The measured correlation was "
    c.drawString(72, 700, prefix)
    x = 72 + c.stringWidth(prefix, "Helvetica", 10)
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(x, 700, "r")
    x += c.stringWidth("r", "Helvetica-Oblique", 10)
    c.setFont("Helvetica", 10)
    c.drawString(x, 700, " = 0.25.")
    c.save()
    lines = build_text_lines(extract_page_objects(path).pages)[0].lines
    assert [line.text for line in lines] == [prefix + "r = 0.25."]


def test_parent_validates_coincident_subscript_and_superscript_order(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.extraction import extract_document
    from academic_pdf_en_zh_reader.extraction.api import _validate_page_items
    from academic_pdf_en_zh_reader.extraction.revalidate import (
        validate_derived_semantics,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    path = tmp_path / "paired-scripts.pdf"
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica", 10)
    c.drawString(72, 700, "CO")
    x = 72 + c.stringWidth("CO", "Helvetica", 10)
    c.setFont("Helvetica", 6)
    c.drawString(x, 705, "2")
    c.drawString(x, 698, "3")
    c.save()
    page = extract_document(path)["pages"][0]
    assert page["lines"][0]["text"] == "CO32"
    _validate_page_items(
        page, 1, set(), crop_box=page["crop_box_mpt"], limits=WorkerLimits()
    )
    validate_derived_semantics([page])


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
