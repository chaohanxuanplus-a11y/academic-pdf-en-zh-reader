# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
import pypdfium2 as pdfium

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.qa.geometry import validate_bounds_and_overlap
from academic_pdf_en_zh_reader.rendering.compose import compose_bilingual_pdf

from .conftest import build_case


def _notes(source, units, translation):
    figures = []
    terms = []
    for unit in units["units"]:
        uid = unit["id"]
        text = unit["source_text"]
        if unit["role"] in {"figure-caption", "table-caption"}:
            figure_id = next(
                b["target_graphic_id"]
                for p in source["pages"]
                for b in p["blocks"]
                if b["id"] == uid
            )
            content = (
                (
                    "图形解析：图1包含四个固定点，折线先升、后降、再升。图中未标出变量名称、刻度或单位，因此"
                    "只能描述形状，不能计算变化率或判断统计显著性。结合下段正文，这些点仅用于检验题注和图区的"
                    "处理，不构成真实研究趋势。"
                )
                if unit["role"] == "figure-caption"
                else "表格解析：三列分别为分组、均值和范围。A组示例均值为4.2，范围为3—5。其余空白单元格不代表零值。正文明确说明数据为虚构测试值，不能据此推断实验效果、组间差异或样本分布。"  # noqa: E501
            )
            figures.append(
                FigureNoteCandidate(
                    "note-" + uid,
                    figure_id,
                    uid,
                    0,
                    1,
                    content,
                    content,
                    (DirectEvidence(uid, 0, len(text), text),),
                    90,
                    True,
                )
            )
        if unit["role"] == "body":
            quote = "coordinates" if "coordinates" in text else "confidence"
            start = text.index(quote)
            terms.append(
                TeachingCandidate(
                    "term-" + uid,
                    quote,
                    "坐标，用于定位图中数据点"
                    if quote == "coordinates"
                    else "置信程度，此处指提取结果的可靠程度",
                    (TeachingOccurrence(uid, start, start + len(quote), 0, 1),),
                    90,
                    True,
                )
            )
    return figures, terms


def test_reading_sample_has_contextual_figure_notes_and_no_overlap(tmp_path):
    case = build_case(
        tmp_path,
        "figures-and-tables",
        candidate_factory=_notes,
        translation_overrides={
            "p1-title": "带有明确题注的合成图表",
            "p1-c1-section": "1. 合成图形",
            "p1-figure-caption-1": "图1：固定的合成数据点，仅用于测试题注与图形区域的处理。",  # noqa: E501
            "p1-c1-body": "该图不报告真实趋势；其坐标在测试定义中固定。",
            "p1-c2-section": "2. 合成表格",
            "p1-table-caption-1": "表1：用于确定性表格与题注提取测试的虚构数值。",
            "p1-c2-body": "表中数值没有外部来源。置信程度低时，绝不能编造内容。",
        },
    )
    assert len(case.frame_graph["auxiliary_flows"]) == 4
    validate_bounds_and_overlap(case.layout, case.overlay_plan)
    output = tmp_path / "reading-sample.pdf"
    compose_bilingual_pdf(
        source_pdf_path=case.source_pdf_path,
        source=case.source,
        units=case.units,
        translation=case.translation,
        review=case.review,
        annotations=case.annotations,
        frame_graph=case.frame_graph,
        layout=case.layout,
        overlay_plan=case.overlay_plan,
        finalization_receipt=case.receipt,
        policy_inputs=case.policy_inputs,
        output_pdf_path=output,
        job_root=tmp_path,
        expected_finalization_receipt_hash=sha256_canonical(case.receipt),
        expected_overlay_plan_hash=case.overlay_plan["overlay_plan_hash"],
        render_manifest_path=tmp_path / "render-manifest.json",
    )
    document = pdfium.PdfDocument(output)
    try:
        for i in range(len(document)):
            page = document[i]
            bitmap = page.render(scale=1.3)
            try:
                bitmap.to_pil().save(tmp_path / f"reading-sample-{i + 1}.png")
            finally:
                bitmap.close()
                page.close()
    finally:
        document.close()
    assert case.layout["solver_trace"]["continuation_page_count"] == 0
