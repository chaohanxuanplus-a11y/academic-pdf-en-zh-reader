# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.extraction.page_objects import extract_page_objects
from academic_pdf_en_zh_reader.extraction.repeated_marginals import (
    mark_repeated_marginals,
)
from academic_pdf_en_zh_reader.extraction.text_lines import build_text_lines


def _repeated_pdf(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    for page_number in range(1, 4):
        canvas.setFillColor(black)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(54, 815, "SYNTHETIC JOURNAL HEADER")
        canvas.drawString(54, 42, "CC0 SYNTHETIC FOOTER")
        canvas.drawCentredString(A4[0] / 2, 24, f"Page {page_number}")
        canvas.setFillColor(Color(0.75, 0.75, 0.75))
        canvas.setFont("Helvetica", 30)
        canvas.drawCentredString(A4[0] / 2, A4[1] / 2, "DRAFT WATERMARK")
        canvas.setFillColor(black)
        canvas.setFont("Helvetica", 10)
        canvas.drawString(72, 700, f"Unique body text for page {page_number}.")
        canvas.showPage()
    canvas.save()


def test_repeated_headers_footers_page_numbers_and_watermarks_are_explicit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repeated.pdf"
    _repeated_pdf(path)
    page_objects = extract_page_objects(path).pages
    line_pages = build_text_lines(page_objects)

    marked = mark_repeated_marginals(line_pages, page_objects)

    kinds = [
        line.exclusion_kind
        for page in marked
        for line in page.lines
        if line.exclusion_kind is not None
    ]
    assert kinds.count("repeated-header") == 3
    assert kinds.count("repeated-footer") == 3
    assert kinds.count("page-number") == 3
    assert kinds.count("watermark") == 3
    excluded = [
        line
        for page in marked
        for line in page.lines
        if line.exclusion_kind is not None
    ]
    assert all(line.body_eligible is False for line in excluded)
    assert all(line.coverage_eligible is False for line in excluded)


def test_unique_body_lines_are_not_removed_by_marginal_detection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repeated.pdf"
    _repeated_pdf(path)
    page_objects = extract_page_objects(path).pages

    marked = mark_repeated_marginals(build_text_lines(page_objects), page_objects)

    body_lines = [
        line
        for page in marked
        for line in page.lines
        if line.text.startswith("Unique body text")
    ]
    assert len(body_lines) == 3
    assert all(line.exclusion_kind is None for line in body_lines)
    assert all(line.body_eligible is True for line in body_lines)
    assert all(line.coverage_eligible is True for line in body_lines)


def test_alternating_running_heads_and_roman_page_numbers_are_excluded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alternating-marginals.pdf"
    roman_numbers = ("i", "ii", "iii", "iv", "v", "vi")
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    for page_number, roman in enumerate(roman_numbers, start=1):
        canvas.setFont("Helvetica", 8)
        header = "ODD JOURNAL HEADER" if page_number % 2 else "EVEN ARTICLE HEADER"
        canvas.drawString(54, 815, header)
        canvas.drawCentredString(A4[0] / 2, 24, roman)
        canvas.setFont("Helvetica", 10)
        canvas.drawString(72, 700, f"Unique body text on leaf {page_number}.")
        canvas.showPage()
    canvas.save()
    page_objects = extract_page_objects(path).pages

    marked = mark_repeated_marginals(build_text_lines(page_objects), page_objects)

    headers = [line for page in marked for line in page.lines if "HEADER" in line.text]
    page_numbers = [
        line
        for page in marked
        for line in page.lines
        if line.text.casefold() in roman_numbers
    ]
    assert len(headers) == 6
    assert all(line.exclusion_kind == "repeated-header" for line in headers)
    assert len(page_numbers) == 6
    assert all(line.exclusion_kind == "page-number" for line in page_numbers)
