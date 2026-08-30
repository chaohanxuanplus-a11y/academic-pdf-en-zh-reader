# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.qa.fonts import (
    FontQaError,
    validate_draw_run_fonts,
    validate_embedded_fonts,
    validate_glyph_coverage,
)
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry


def test_planned_fonts_are_embedded_with_tounicode_and_bound_to_draws(
    composed_qa_fixture: dict[str, object],
) -> None:
    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    manifest = composed_qa_fixture["render_manifest"]
    plan = composed_qa_fixture["overlay_plan"]

    assert validate_embedded_fonts(reader, manifest)["font_count"] >= 1
    assert validate_draw_run_fonts(reader, manifest, plan)["draw_run_count"] >= 1
    assert validate_glyph_coverage(manifest, plan)["character_count"] >= 1


def test_native_source_text_is_allowed_when_page_has_no_planned_draw_runs(
    composed_qa_fixture: dict[str, object],
) -> None:
    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    manifest = deepcopy(composed_qa_fixture["render_manifest"])
    plan = deepcopy(composed_qa_fixture["overlay_plan"])
    for page in plan["pages"]:
        page["draw_runs"] = []
    for usage in manifest["font_usages"]:
        usage["draw_run_count"] = 0

    assert validate_draw_run_fonts(reader, manifest, plan) == {"draw_run_count": 0}


def test_long_multisubset_text_object_counts_as_one_planned_draw_run() -> None:
    registry = load_font_registry()
    body = registry.face("body")
    heading = registry.face("heading")
    text = "".join(
        chr(codepoint)
        for codepoint in sorted(body.codepoints)
        if 0x4E00 <= codepoint <= 0x9FFF
    )[:300]
    assert len(text) == 300

    output = BytesIO()
    canvas = Canvas(output, pagesize=(841.89, 595.276), invariant=1)
    canvas.setFont(heading.reportlab_name, 11.956)
    canvas.drawString(10, 520, "标题")
    canvas.setFont(body.reportlab_name, 9.963)
    canvas.drawString(10, 500, text)
    canvas.save()
    reader = PdfReader(BytesIO(output.getvalue()), strict=True)
    manifest = {
        "font_fingerprint": [
            {"role": face.role, "reportlab_name": face.reportlab_name}
            for face in registry.faces
        ],
        "font_usages": [{"draw_run_count": 2}],
    }
    plan = {
        "pages": [
            {
                "draw_runs": [
                    {"font_role": "heading", "size_mpt": 11956},
                    {"font_role": "body", "size_mpt": 9963},
                ]
            }
        ]
    }

    assert validate_draw_run_fonts(reader, manifest, plan) == {"draw_run_count": 2}


def test_nonempty_draw_run_suffix_and_manifest_total_remain_exact(
    composed_qa_fixture: dict[str, object],
) -> None:
    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    manifest = composed_qa_fixture["render_manifest"]
    plan = deepcopy(composed_qa_fixture["overlay_plan"])
    first_page_with_runs = next(page for page in plan["pages"] if page["draw_runs"])
    first_page_with_runs["draw_runs"][0]["size_mpt"] += 1

    with pytest.raises(FontQaError, match="FONT_DRAW_BINDING_INVALID"):
        validate_draw_run_fonts(reader, manifest, plan)

    mismatched_manifest = deepcopy(manifest)
    mismatched_manifest["font_usages"][0]["draw_run_count"] += 1
    with pytest.raises(FontQaError, match="FONT_DRAW_BINDING_INVALID"):
        validate_draw_run_fonts(
            reader,
            mismatched_manifest,
            composed_qa_fixture["overlay_plan"],
        )


def test_missing_tounicode_is_a_hard_failure(
    composed_qa_fixture: dict[str, object],
) -> None:
    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    for page in reader.pages:
        for font_ref in page["/Resources"]["/Font"].get_object().values():
            font = font_ref.get_object()
            if "/ToUnicode" in font:
                del font["/ToUnicode"]
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    output = BytesIO()
    writer.write(output)
    tampered = PdfReader(BytesIO(output.getvalue()), strict=True)

    with pytest.raises(FontQaError, match="FONT_EMBEDDING_INVALID"):
        validate_embedded_fonts(tampered, composed_qa_fixture["render_manifest"])
