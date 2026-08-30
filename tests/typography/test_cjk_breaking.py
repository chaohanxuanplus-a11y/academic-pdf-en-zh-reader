# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.typography.cjk_breaker import (
    PROHIBITED_LINE_END,
    PROHIBITED_LINE_START,
    SAFE_URL_BREAK_AFTER,
    LineBreakError,
    break_text,
)
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.measure import measure_text
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)


def _resolver() -> FontRunResolver:
    return FontRunResolver(load_font_registry())


def _width(text: str, *, size_pt: float = 10.0) -> float:
    return measure_text(
        _resolver().resolve(text, font_role="body"),
        size_pt=size_pt,
    ).width_pt


def test_cjk_lines_obey_start_and_end_prohibitions() -> None:
    lines = break_text(
        "研究（方法），结果表明：处理有效。",
        max_width_pt=_width("研究（方法"),
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )

    assert len(lines) > 1
    assert all(line.text[0] not in PROHIBITED_LINE_START for line in lines)
    assert all(line.text[-1] not in PROHIBITED_LINE_END for line in lines)
    assert all(line.width_pt <= _width("研究（方法") + 1e-9 for line in lines)


def test_too_narrow_for_a_minimal_legal_punctuation_group_fails_closed() -> None:
    one_han_width = _width("甲")

    for text in ("甲（乙丙", "甲乙，丙"):
        with pytest.raises(LineBreakError, match="legal"):
            break_text(
                text,
                max_width_pt=one_han_width,
                resolver=_resolver(),
                font_role="body",
                size_pt=10.0,
                line_height_pt=17.0,
            )


@pytest.mark.parametrize("text", ["，结果", "结果（"])
def test_impossible_edge_punctuation_fails_closed(text: str) -> None:
    with pytest.raises(LineBreakError, match="punctuation"):
        break_text(
            text,
            max_width_pt=_width(text) + 1.0,
            resolver=_resolver(),
            font_role="body",
            size_pt=10.0,
            line_height_pt=17.0,
        )


def test_fitting_latin_word_moves_intact_to_the_next_line() -> None:
    lines = break_text(
        "甲 polymerization",
        max_width_pt=_width("polymerization"),
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )

    assert any(line.text == "polymerization" for line in lines)


def test_single_overlong_latin_word_uses_stable_emergency_grapheme_breaks() -> None:
    text = "polymerization"
    maximum = _width("poly")
    lines = break_text(
        text,
        max_width_pt=maximum,
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )

    assert len(lines) > 1
    assert "".join(line.text for line in lines) == text
    assert all(line.width_pt <= maximum + 1e-9 for line in lines)


@pytest.mark.parametrize(
    ("text", "fitting_prefix"),
    [
        ("5–10 mg", "5–10"),
        ("p = 0.03", "p = 0"),
        ("[12–14]", "[12"),
    ],
)
def test_overlong_semantic_protected_token_fails_closed(
    text: str,
    fitting_prefix: str,
) -> None:
    with pytest.raises(LineBreakError, match="protected"):
        break_text(
            text,
            max_width_pt=_width(fitting_prefix),
            resolver=_resolver(),
            font_role="body",
            size_pt=10.0,
            line_height_pt=17.0,
        )


def test_punctuation_merge_does_not_turn_a_latin_word_into_unsafe_splits() -> None:
    with pytest.raises(LineBreakError, match="protected"):
        break_text(
            "（polymerization",
            max_width_pt=_width("polymerization"),
            resolver=_resolver(),
            font_role="body",
            size_pt=10.0,
            line_height_pt=17.0,
        )


def test_parenthesized_url_keeps_url_safe_break_contract() -> None:
    with pytest.raises(LineBreakError, match="URL"):
        break_text(
            "（https://examplewithoutasafebreak.example",
            max_width_pt=_width("（http"),
            resolver=_resolver(),
            font_role="body",
            size_pt=10.0,
            line_height_pt=17.0,
        )


def test_explicit_newline_is_a_deterministic_hard_break() -> None:
    lines = break_text(
        "第一行\n第二行",
        max_width_pt=_width("第一行第二行") + 1.0,
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )

    assert tuple(line.text for line in lines) == ("第一行", "第二行")


@pytest.mark.parametrize("separator", (" \n", "  \r\n", " \u2028", " \u2029"))
def test_trailing_spaces_do_not_swallow_an_explicit_line_break(
    separator: str,
) -> None:
    lines = break_text(
        f"第一行{separator}第二行",
        max_width_pt=_width("第一行第二行") + 1.0,
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )

    assert tuple(line.text for line in lines) == ("第一行", "第二行")


def test_units_statistics_and_citations_are_not_split_when_they_fit() -> None:
    lines = break_text(
        "polymerization和e\u0301clair剂量为5–10 mg，"
        "95% CI 6.2–8.8，p = 0.03，见[12–14]。",
        max_width_pt=max(
            _width("polymerization"),
            _width("e\u0301clair"),
            _width("5–10 mg"),
            _width("95% CI 6.2–8.8"),
            _width("p = 0.03"),
            _width("[12–14]"),
        )
        + _width("剂"),
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )
    rendered_lines = tuple(line.text for line in lines)

    for expression in (
        "polymerization",
        "e\u0301clair",
        "5–10 mg",
        "95% CI 6.2–8.8",
        "p = 0.03",
        "[12–14]",
    ):
        assert any(expression in line for line in rendered_lines), rendered_lines


def test_long_url_and_doi_break_only_at_stable_safe_boundaries() -> None:
    text = "https://example.org/path/to/article?doi=10.1234/abcdef.2026.001"
    max_width = _width("https://example.org/path/")
    lines = break_text(
        text,
        max_width_pt=max_width,
        resolver=_resolver(),
        font_role="body",
        size_pt=10.0,
        line_height_pt=17.0,
    )

    assert len(lines) > 1
    assert "".join(line.text for line in lines) == text
    assert all(line.text[-1] in SAFE_URL_BREAK_AFTER for line in lines[:-1])
    assert all(line.width_pt <= max_width + 1e-9 for line in lines)


def test_long_title_wraps_without_shrinking() -> None:
    contract = build_style_contract(
        (FontSizeSample(size_mpt=10_000, character_count=500),)
    )
    title_style = contract.style_for("title")
    lines = break_text(
        "一种用于复杂生物医学证据整合的确定性方法",
        max_width_pt=60.0,
        resolver=_resolver(),
        font_role=title_style.font_role,
        size_pt=title_style.size_pt,
        line_height_pt=title_style.line_height_pt,
    )

    assert len(lines) > 1
    assert {line.size_pt for line in lines} == {title_style.size_pt}
    assert all(line.width_pt <= 60.0 + 1e-9 for line in lines)


@pytest.mark.parametrize("maximum", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_line_width_fails_before_layout(maximum: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        break_text(
            "正文",
            max_width_pt=maximum,
            resolver=_resolver(),
            font_role="body",
            size_pt=10.0,
            line_height_pt=17.0,
        )
