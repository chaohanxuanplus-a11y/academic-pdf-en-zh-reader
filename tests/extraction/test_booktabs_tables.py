# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace

from academic_pdf_en_zh_reader.extraction.blocks import build_basic_blocks
from academic_pdf_en_zh_reader.extraction.page_objects import (
    PageObjects,
    VectorObject,
)
from academic_pdf_en_zh_reader.extraction.text_lines import PageTextLines, TextLine


def _rule(
    identifier: str,
    y_mpt: int,
    x0_mpt: int = 72_000,
    x1_mpt: int = 360_000,
) -> VectorObject:
    return VectorObject(
        id=identifier,
        page_number=1,
        source_kind="line",
        bbox_mpt=(x0_mpt, y_mpt, x1_mpt, y_mpt),
        line_width_mpt=800,
        fill_color=None,
        stroke_color=(0,),
        filled=False,
        stroked=True,
    )


def _line(
    identifier: str,
    text: str,
    box: tuple[int, int, int, int],
    *,
    font_size_mpt: int = 10_000,
) -> TextLine:
    return TextLine(
        id=identifier,
        page_number=1,
        text=text,
        bbox_mpt=box,
        character_ids=(f"{identifier}-char",),
        font_names=("Helvetica",),
        fill_colors=((0,),),
        max_font_size_mpt=font_size_mpt,
    )


def _page(*rules: VectorObject) -> PageObjects:
    return PageObjects(
        page_number=1,
        media_box_mpt=(0, 0, 595_276, 841_890),
        crop_box_mpt=(0, 0, 595_276, 841_890),
        rotation_degrees=0,
        chars=(),
        rectangles=(),
        curves=rules,
        images=(),
    )


def _booktabs_lines(*, include_caption: bool = True) -> tuple[TextLine, ...]:
    lines = [
        _line("header-left", "Group", (80_000, 650_000, 125_000, 660_000)),
        _line("header-right", "Mean", (250_000, 650_000, 275_000, 660_000)),
        _line("cell-left", "Control", (80_000, 605_000, 135_000, 615_000)),
        _line("cell-right", "12.4", (270_000, 605_000, 290_000, 615_000)),
    ]
    if include_caption:
        lines.insert(
            0,
            _line(
                "caption",
                "Table 1. Outcomes by group.",
                (72_000, 700_000, 250_000, 710_000),
            ),
        )
    return tuple(lines)


def test_captioned_three_rule_table_is_detected_and_bound() -> None:
    page = _page(
        _rule("top", 675_000),
        _rule("middle", 635_000, 74_000, 358_000),
        _rule("bottom", 580_000, 73_000, 359_000),
    )
    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=_booktabs_lines()),),
    ).pages[0]

    tables = [region for region in result.graphic_regions if region.kind == "table"]
    assert len(tables) == 1
    assert tables[0].evidence == "captioned-three-rule-table"
    assert len(result.captions) == 1
    assert result.captions[0].text == "Table 1. Outcomes by group."
    assert result.captions[0].target_id == tables[0].id

    caption = next(line for line in result.lines if line.id == "caption")
    cells = [line for line in result.lines if line.id != "caption"]
    assert caption.body_eligible is False
    assert all(line.container_kind == "table" for line in cells)
    assert all(line.body_eligible is False for line in cells)
    assert all(line.coverage_eligible is False for line in cells)


def test_captioned_table_accepts_equivalent_thin_filled_rectangle_rules() -> None:
    rules = tuple(
        replace(
            _rule(name, y),
            source_kind="rect",
            filled=True,
            stroked=False,
            bbox_mpt=(72_000, y - 300, 360_000, y + 300),
        )
        for name, y in (("top", 675_000), ("middle", 635_000), ("bottom", 580_000))
    )
    page = replace(_page(), rectangles=rules)
    result = build_basic_blocks(
        (page,), (PageTextLines(page_number=1, lines=_booktabs_lines()),)
    ).pages[0]
    assert len(result.graphic_regions) == 1
    assert result.graphic_regions[0].kind == "table"
    assert all(
        line.container_kind == "table" for line in result.lines if line.id != "caption"
    )


def test_uncaptioned_filled_rectangle_rules_are_not_guessed_as_a_table() -> None:
    rules = tuple(
        replace(
            _rule(name, y),
            source_kind="rect",
            filled=True,
            bbox_mpt=(72_000, y - 300, 360_000, y + 300),
        )
        for name, y in (("top", 675_000), ("middle", 635_000), ("bottom", 580_000))
    )
    result = build_basic_blocks(
        (replace(_page(), rectangles=rules),),
        (PageTextLines(page_number=1, lines=_booktabs_lines(include_caption=False)),),
    ).pages[0]
    assert not result.graphic_regions


def test_split_rule_segments_and_flat_path_form_one_captioned_table() -> None:
    rules = tuple(
        _rule(f"{row}-{col}", y, left, right)
        for row, y in enumerate((675_000, 635_000))
        for col, (left, right) in enumerate(
            ((72_000, 170_000), (170_000, 260_000), (260_000, 360_000))
        )
    ) + (replace(_rule("bottom", 580_000), source_kind="curve"),)
    result = build_basic_blocks(
        (_page(*rules),), (PageTextLines(page_number=1, lines=_booktabs_lines()),)
    ).pages[0]
    assert len(result.graphic_regions) == 1
    assert result.graphic_regions[0].kind == "table"
    assert len(result.captions) == 1


def test_long_captioned_table_keeps_uppercase_title_out_of_first_table_row() -> None:
    page = _page(
        _rule("top", 700_000),
        _rule("middle", 670_000, 74_000, 358_000),
        _rule("bottom", 420_000, 73_000, 359_000),
    )
    lines = (
        _line("caption", "Table 2", (72_000, 720_000, 120_000, 730_000)),
        _line(
            "title",
            "Molecular mediators involved in macrophage fusion",
            (72_000, 708_000, 330_000, 718_000),
        ),
        _line("header-left", "Parameter", (72_000, 688_000, 125_000, 698_000)),
        _line("header-right", "Mean", (250_000, 688_000, 275_000, 698_000)),
        _line("cell-left", "Control", (72_000, 500_000, 135_000, 510_000)),
        _line("cell-right", "12.4", (250_000, 500_000, 275_000, 510_000)),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]

    tables = [region for region in result.graphic_regions if region.kind == "table"]
    assert len(tables) == 1
    assert tables[0].evidence == "captioned-three-rule-table"
    assert len(result.captions) == 1
    assert result.captions[0].text == (
        "Table 2 Molecular mediators involved in macrophage fusion"
    )
    assert result.captions[0].line_ids == ("caption", "title")

    by_id = {line.id: line for line in result.lines}
    assert by_id["title"].container_id is None
    assert by_id["header-left"].container_kind == "table"
    assert by_id["header-left"].id not in result.captions[0].line_ids


def test_raised_letter_suffix_on_table_title_is_an_excluded_footnote() -> None:
    page = _page(
        _rule("top", 700_000),
        _rule("middle", 670_000, 74_000, 358_000),
        _rule("bottom", 420_000, 73_000, 359_000),
    )
    lines = (
        _line("caption", "Table 1", (72_000, 730_000, 120_000, 740_000)),
        _line(
            "title",
            "Molecular mediators involved in macrophage fusion",
            (72_000, 718_000, 330_000, 728_000),
        ),
        _line(
            "title-marker",
            "a",
            (329_900, 722_000, 333_000, 728_500),
            font_size_mpt=6_000,
        ),
        _line("header-left", "Parameter", (72_000, 688_000, 125_000, 698_000)),
        _line("header-right", "Mean", (250_000, 688_000, 275_000, 698_000)),
        _line("cell-left", "Control", (72_000, 500_000, 135_000, 510_000)),
        _line("cell-right", "12.4", (250_000, 500_000, 275_000, 510_000)),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]
    by_id = {line.id: line for line in result.lines}

    assert result.captions[0].line_ids == ("caption", "title")
    assert by_id["title-marker"].exclusion_kind == "footnote"
    assert by_id["title-marker"].body_eligible is False
    assert by_id["title-marker"].coverage_eligible is False


def test_explicit_adapted_from_note_below_table_is_excluded_with_its_marker() -> None:
    page = _page(
        _rule("top", 700_000),
        _rule("middle", 670_000, 74_000, 358_000),
        _rule("bottom", 420_000, 73_000, 359_000),
    )
    lines = (
        _line("caption", "Table 1. Outcomes.", (72_000, 720_000, 210_000, 730_000)),
        _line("header-left", "Parameter", (72_000, 688_000, 125_000, 698_000)),
        _line("header-right", "Mean", (250_000, 688_000, 275_000, 698_000)),
        _line("cell-left", "Control", (72_000, 500_000, 135_000, 510_000)),
        _line("cell-right", "12.4", (250_000, 500_000, 275_000, 510_000)),
        _line(
            "note-marker",
            "a",
            (76_000, 410_000, 79_000, 416_000),
            font_size_mpt=6_000,
        ),
        _line(
            "note-text",
            "Adapted from Example et al.",
            (82_000, 406_000, 220_000, 414_000),
            font_size_mpt=8_000,
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]
    by_id = {line.id: line for line in result.lines}

    assert by_id["note-marker"].exclusion_kind == "footnote"
    assert by_id["note-text"].exclusion_kind == "footnote"
    assert by_id["note-marker"].body_eligible is False
    assert by_id["note-text"].body_eligible is False


def test_explicit_bold_legend_below_table_is_excluded_without_a_marker() -> None:
    page = _page(
        _rule("top", 700_000),
        _rule("middle", 670_000, 74_000, 358_000),
        _rule("bottom", 420_000, 73_000, 359_000),
    )
    lines = (
        _line("caption", "Table 2. Outcomes.", (72_000, 720_000, 210_000, 730_000)),
        _line("header-left", "Parameter", (72_000, 688_000, 125_000, 698_000)),
        _line("header-right", "Mean", (250_000, 688_000, 275_000, 698_000)),
        _line("cell-left", "Control", (72_000, 500_000, 135_000, 510_000)),
        _line("cell-right", "12.4", (250_000, 500_000, 275_000, 510_000)),
        _line(
            "bold-legend",
            "Bold: measured by ELISA; otherwise: cytokine array.",
            (72_000, 406_000, 330_000, 416_000),
        ),
        _line(
            "note-marker",
            "a",
            (76_000, 399_000, 79_000, 405_000),
            font_size_mpt=6_000,
        ),
        _line(
            "note-text",
            "Adapted from Example et al.",
            (82_000, 395_000, 220_000, 403_000),
            font_size_mpt=8_000,
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]
    by_id = {line.id: line for line in result.lines}
    legend = by_id["bold-legend"]

    assert legend.exclusion_kind == "footnote"
    assert legend.body_eligible is False
    assert legend.coverage_eligible is False
    assert by_id["note-marker"].exclusion_kind == "footnote"
    assert by_id["note-text"].exclusion_kind == "footnote"


def test_bold_findings_prose_below_table_is_not_guessed_as_a_note() -> None:
    page = _page(
        _rule("top", 700_000),
        _rule("middle", 670_000, 74_000, 358_000),
        _rule("bottom", 420_000, 73_000, 359_000),
    )
    lines = (
        _line("caption", "Table 2. Outcomes.", (72_000, 720_000, 210_000, 730_000)),
        _line("header-left", "Parameter", (72_000, 688_000, 125_000, 698_000)),
        _line("header-right", "Mean", (250_000, 688_000, 275_000, 698_000)),
        _line("cell-left", "Control", (72_000, 500_000, 135_000, 510_000)),
        _line("cell-right", "12.4", (250_000, 500_000, 275_000, 510_000)),
        _line(
            "ordinary-prose",
            "Bold findings remain ordinary prose.",
            (72_000, 406_000, 300_000, 416_000),
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]
    prose = next(line for line in result.lines if line.id == "ordinary-prose")

    assert prose.exclusion_kind is None
    assert prose.body_eligible is True


def test_unrelated_small_letter_near_table_is_not_guessed_as_a_footnote() -> None:
    page = _page(
        _rule("top", 700_000),
        _rule("middle", 670_000, 74_000, 358_000),
        _rule("bottom", 420_000, 73_000, 359_000),
    )
    lines = (
        _line("caption", "Table 1. Outcomes.", (72_000, 720_000, 210_000, 730_000)),
        _line("header-left", "Parameter", (72_000, 688_000, 125_000, 698_000)),
        _line("header-right", "Mean", (250_000, 688_000, 275_000, 698_000)),
        _line("cell-left", "Control", (72_000, 500_000, 135_000, 510_000)),
        _line("cell-right", "12.4", (250_000, 500_000, 275_000, 510_000)),
        _line(
            "unrelated-letter",
            "b",
            (180_000, 390_000, 183_000, 396_000),
            font_size_mpt=6_000,
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]
    letter = next(line for line in result.lines if line.id == "unrelated-letter")

    assert letter.exclusion_kind is None
    assert letter.body_eligible is True


def test_three_rules_and_aligned_cells_without_explicit_caption_are_not_table() -> None:
    page = _page(
        _rule("top", 675_000),
        _rule("middle", 635_000),
        _rule("bottom", 580_000),
    )
    result = build_basic_blocks(
        (page,),
        (
            PageTextLines(
                page_number=1,
                lines=_booktabs_lines(include_caption=False),
            ),
        ),
    ).pages[0]

    assert not any(region.kind == "table" for region in result.graphic_regions)
    assert all(line.body_eligible is True for line in result.lines)


def test_caption_and_paragraph_separators_without_column_alignment_are_not_table() -> (
    None
):
    page = _page(
        _rule("separator-1", 675_000),
        _rule("separator-2", 635_000),
        _rule("separator-3", 580_000),
    )
    lines = (
        _line(
            "caption-like",
            "Table 1. Discussion guide.",
            (72_000, 700_000, 250_000, 710_000),
        ),
        _line(
            "paragraph-1",
            "This is one full-width prose line.",
            (80_000, 650_000, 310_000, 660_000),
        ),
        _line(
            "paragraph-2",
            "Another prose line remains ordinary body text.",
            (95_000, 605_000, 350_000, 615_000),
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]

    assert not any(region.kind == "table" for region in result.graphic_regions)
    assert result.captions == ()
    assert all(line.body_eligible is True for line in result.lines)


def test_two_full_width_prose_lines_per_rule_interval_do_not_form_columns() -> None:
    page = _page(
        _rule("separator-1", 675_000),
        _rule("separator-2", 635_000),
        _rule("separator-3", 580_000),
    )
    lines = (
        _line(
            "caption-like",
            "Table 1. Discussion guide.",
            (72_000, 700_000, 250_000, 710_000),
        ),
        _line(
            "upper-prose-1",
            "The first ordinary sentence spans nearly the full measure.",
            (80_000, 654_000, 340_000, 664_000),
        ),
        _line(
            "upper-prose-2",
            "Its shorter continuation remains prose.",
            (80_000, 641_000, 305_000, 651_000),
        ),
        _line(
            "lower-prose-1",
            "The next ordinary sentence is a different full-width length.",
            (80_000, 610_000, 350_000, 620_000),
        ),
        _line(
            "lower-prose-2",
            "This continuation is shorter again.",
            (80_000, 592_000, 290_000, 602_000),
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]

    assert not any(region.kind == "table" for region in result.graphic_regions)
    assert result.captions == ()
    assert all(line.body_eligible is True for line in result.lines)


def test_long_spaced_paragraph_separators_are_not_a_table() -> None:
    page = _page(
        _rule("separator-1", 700_000),
        _rule("separator-2", 670_000),
        _rule("separator-3", 420_000),
    )
    lines = (
        _line(
            "caption-like",
            "Table 3. Discussion guide.",
            (72_000, 720_000, 250_000, 730_000),
        ),
        _line(
            "upper-prose",
            "The first ordinary sentence spans the full measure.",
            (80_000, 682_000, 340_000, 692_000),
        ),
        _line(
            "lower-prose",
            "Another prose line remains ordinary body text.",
            (80_000, 500_000, 350_000, 510_000),
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]

    assert not any(region.kind == "table" for region in result.graphic_regions)
    assert result.captions == ()
    assert all(line.body_eligible is True for line in result.lines)


def test_table_reference_inside_two_column_prose_is_not_a_table_caption() -> None:
    page = _page(
        _rule("separator-1", 700_000),
        _rule("separator-2", 450_000),
        _rule("separator-3", 200_000),
    )
    lines = (
        _line(
            "internal-reference",
            "Table 1 shows the study outcomes discussed below.",
            (80_000, 680_000, 330_000, 690_000),
        ),
        _line("upper-left", "First left column.", (80_000, 600_000, 170_000, 610_000)),
        _line(
            "upper-right",
            "First right column.",
            (250_000, 600_000, 340_000, 610_000),
        ),
        _line("lower-left", "Second left column.", (80_000, 300_000, 175_000, 310_000)),
        _line(
            "lower-right",
            "Second right column.",
            (250_000, 300_000, 345_000, 310_000),
        ),
    )

    result = build_basic_blocks(
        (page,),
        (PageTextLines(page_number=1, lines=lines),),
    ).pages[0]

    assert not any(region.kind == "table" for region in result.graphic_regions)
    assert result.captions == ()
    assert all(line.body_eligible is True for line in result.lines)
