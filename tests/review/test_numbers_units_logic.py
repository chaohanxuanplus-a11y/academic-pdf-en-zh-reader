# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.review.semantic_checks import (
    check_mechanical_semantics,
)

SHA = "d" * 64
NORMALIZED_SHA = "e" * 64


def _artifacts(source_text: str, chinese_text: str) -> tuple[dict, dict]:
    units = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": SHA,
        "normalized_pdf_sha256": NORMALIZED_SHA,
        "units": [
            {
                "id": "unit-0",
                "role": "body",
                "reading_order": 0,
                "source_text": source_text,
                "confidence_ppm": 990_000,
                "fragments": [
                    {
                        "page_number": 1,
                        "block_id": "block-0",
                        "source_char_start": 0,
                        "source_char_end": len(source_text),
                    }
                ],
            }
        ],
    }
    translation = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent-1",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": "unit-0",
                "chinese_text": chinese_text,
                "spans": [],
                "terminology": [],
            }
        ],
    }
    return units, translation


def _categories(source_text: str, chinese_text: str) -> set[str]:
    units, translation = _artifacts(source_text, chinese_text)
    return {issue.category for issue in check_mechanical_semantics(units, translation)}


def test_numbers_units_ranges_statistics_references_and_citations_match() -> None:
    source = (
        "At 5–10 mg, mean = 7.5 mg (95% CI 6.2–8.8; p = 0.03; n = 24), "
        "values increased by +2.0% compared with baseline (Fig. 2; Table 3) [4–6]."
    )
    target = (
        "在5–10 mg时，平均值=7.5 mg（95% CI 6.2–8.8；p=0.03；n=24），"
        "数值较基线增加+2.0%（图2；表3）[4–6]。"
    )

    assert check_mechanical_semantics(*_artifacts(source, target)) == ()


def test_missing_numeric_and_reference_markers_return_structured_issues() -> None:
    source = "The 5–10 mg dose gave p < 0.05 in Fig. 2 and Table 3 [4–6]."
    target = "该剂量见图2。"

    issues = check_mechanical_semantics(*_artifacts(source, target))

    categories = {issue.category for issue in issues}
    assert {
        "number",
        "unit",
        "range",
        "statistic",
        "citation",
        "figure-table",
    } <= categories
    assert all(issue.unit_id == "unit-0" for issue in issues)
    assert all(issue.code == "marker_mismatch" for issue in issues)
    assert all(issue.source_markers != issue.target_markers for issue in issues)


def test_reversed_plain_inequality_is_reported() -> None:
    categories = _categories(
        "The dose was > 5 mg.",
        "剂量小于5 mg。",
    )

    assert "inequality" in categories


def test_biomedical_positive_negative_markers_do_not_false_alarm() -> None:
    assert _categories("The test was positive.", "检测结果为阳性。") == set()
    assert _categories("The test was negative.", "检测结果为阴性。") == set()


def test_common_faithful_chinese_markers_do_not_false_alarm() -> None:
    source = (
        "The value increased and was higher because it was associated with age "
        "(Smith, 2020). The dose was 5 \u03bcg at 20 \u00b0C."
    )
    target = (
        "该值上升且高于基线，因为它与年龄有关（Smith，2020）。"
        "剂量为5 \u00b5g，温度为20 \u2103。"
    )

    assert _categories(source, target) == set()


def test_spatial_lower_marker_accepts_faithful_chinese_layout_wording() -> None:
    source = "The upper band spans the page while the lower band uses two columns."
    target = "页面上部区域横跨整页，而下部区域采用两栏布局。"

    assert _categories(source, target) == set()


def test_leading_zero_omission_is_normalized_for_statistics() -> None:
    assert _categories("The result was p < .05.", "结果为p<0.05。") == set()


def test_direction_negation_degree_and_logic_markers_match() -> None:
    source = (
        "The treatment may not increase risk because the marker was slightly "
        "lower, although it was positively associated with age."
    )
    target = "治疗可能不会增加风险，因为该标志物略低，尽管其与年龄呈正相关。"

    assert check_mechanical_semantics(*_artifacts(source, target)) == ()


def test_overlapping_chinese_synonym_patterns_count_as_one_marker() -> None:
    source = "The value was approximately 5 compared with baseline."
    target = "该值大约为5，相较于基线。"

    assert check_mechanical_semantics(*_artifacts(source, target)) == ()


def test_omitted_direction_negation_degree_and_logic_are_reported() -> None:
    source = (
        "The treatment may not increase risk because the marker was slightly "
        "lower, although it was positively associated with age."
    )
    target = "治疗改变风险，标志物与年龄有关。"

    categories = _categories(source, target)

    assert {"direction", "negation", "degree", "logic"} <= categories


def test_no_issue_is_documented_as_mechanical_not_absolute_semantic_proof() -> None:
    units, translation = _artifacts("The value was 5 mg.", "该值为5 mg。")

    issues = check_mechanical_semantics(units, translation)

    assert issues == ()
    assert "does not prove semantic correctness" in check_mechanical_semantics.__doc__


def test_pdf_cid_placeholders_are_not_treated_as_numbers() -> None:
    source = "Albumin, (cid:2) globulin, and other proteins were adsorbed [11–14]."
    target = "白蛋白、γ-球蛋白和其他蛋白质发生吸附[11–14]。"

    assert _categories(source, target) == set()


def test_english_number_word_range_matches_arabic_target_range() -> None:
    source = "The response is important in the first two to four weeks."
    target = "该反应在最初2至4周内很重要。"

    assert _categories(source, target) == set()


def test_english_decade_matches_chinese_century_decade_without_seconds_unit() -> None:
    source = "The material was introduced in the late 1980s."
    target = "该材料于20世纪80年代后期引入。"

    assert _categories(source, target) == set()


def test_chinese_written_century_decade_matches_english_decade() -> None:
    source = "The material was introduced in the late 1980s."
    target = "该材料于二十世纪八十年代后期引入。"

    assert _categories(source, target) == set()


def test_hyphenated_identifiers_do_not_create_negative_numbers_or_ranges() -> None:
    source = (
        "Interleukin-4 (IL-4), interleukin-13 (IL-13), and "
        "Pellethane-2363-80A were investigated."
    )
    target = "研究了白细胞介素-4（IL-4）、白细胞介素-13（IL-13）和Pellethane-2363-80A。"

    assert _categories(source, target) == set()


def test_repeated_citation_occurrences_do_not_duplicate_number_evidence() -> None:
    source = "The value was 5 in both reports [4] [4]."
    target = "两篇报告中的数值均为5[4]。"

    assert _categories(source, target) == set()


def test_chinese_lexical_substrings_do_not_satisfy_semantic_markers() -> None:
    assert "negation" in _categories("It was not stable.", "该材料不同。")
    assert "degree" in _categories("It may work.", "已评估这种可能性。")


def test_chinese_lexical_substrings_do_not_create_extra_semantic_markers() -> None:
    source = "Different materials were compared using a strategy."
    target = "采用一种策略比较不同材料。"

    assert _categories(source, target) == set()


def test_lexical_marker_multiplicity_is_not_a_cross_language_hard_gate() -> None:
    source = "The material may respond and may then recover."
    target = "该材料可能先发生应答，随后恢复。"

    assert _categories(source, target) == set()


def test_alphanumeric_identifier_spacing_and_shorthand_are_not_numeric_data() -> None:
    source = (
        "ERK 1/2 and IL- 13 were measured. Pharmacological inhibition of "
        "MMP- 1, -8, -13, and, -18 did not affect adhesion. Caspase-3 was active."
    )
    target = (
        "测量了ERK1/2和IL-13。药理学抑制MMP-1、-8、-13和-18不影响黏附。"
        "半胱天冬酶-3具有活性。"
    )

    assert _categories(source, target) == set()


def test_cid_greek_subunit_numbers_do_not_compete_with_cardinal_counts() -> None:
    source = "There are three (cid:3)1 integrins and four (cid:3)2 integrins."
    target = "有3种β1整合素和4种β2整合素。"

    assert _categories(source, target) == set()


def test_not_only_correlative_is_not_treated_as_plain_negation() -> None:
    source = "The injury not only initiates inflammation, but also leads to healing."
    target = "损伤不仅启动炎症，还会引起愈合。"

    assert _categories(source, target) == set()


def test_faithful_cross_language_direction_and_logic_synonyms_match() -> None:
    source = (
        "Expression increased with culture time. However, apoptosis occurred, "
        "thus rendering cells inactive, whereas controls remained active."
    )
    target = "表达随培养时间延长。不过，细胞发生凋亡，导致其失去活性，而对照仍有活性。"

    assert _categories(source, target) == set()


def test_proposed_possibility_preserves_suggested_degree() -> None:
    source = "Two mechanisms were suggested as leading to degradation."
    target = "研究者提出两种可能导致降解的机制。"

    assert _categories(source, target) == set()


def test_physical_binding_can_preserve_association_marker() -> None:
    source = "Lymphocytes were associated with macrophages."
    target = "淋巴细胞与巨噬细胞结合。"

    assert _categories(source, target) == set()
