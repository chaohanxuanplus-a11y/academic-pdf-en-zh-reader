# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.extraction import extract_document


def test_caption_binds_multipanel_axes_and_outside_labels(tmp_path: Path) -> None:
    path = tmp_path / "composite.pdf"
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Helvetica", 10)
    c.drawString(72, 740, "This complete prose sentence stays outside the graphic.")
    c.drawString(72, 682, "(kg per volume).")
    for x in (90, 310):
        c.rect(x, 530, 160, 140)
        c.line(x, 530, x + 160, 630)
        c.line(x, 540, x + 140, 650)
        c.setFont("Helvetica", 8)
        c.drawString(x, 500, "Time / h")
        c.drawString(x - 35, 605, "Y")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(72, 485, "Fig. 1. Two synthetic panels with external axis labels.")
    c.save()
    page = extract_document(path)["pages"][0]
    assert len(page["captions"]) == 1
    region = next(
        g
        for g in page["graphic_regions"]
        if g["id"] == page["captions"][0]["target_id"]
    )
    assert region["bbox_mpt"][0] <= 55_000
    assert region["bbox_mpt"][2] >= 470_000
    for line in page["lines"]:
        if line["text"] in {"Time / h", "Y"}:
            assert line["container_id"] == region["id"]
            assert not line["coverage_eligible"]
        if line["text"].startswith("This complete"):
            assert line["coverage_eligible"]
            assert line["container_id"] is None
        if line["text"] == "(kg per volume).":
            assert line["coverage_eligible"]
            assert line["container_id"] is None


def test_distant_caption_does_not_absorb_intervening_prose(tmp_path: Path) -> None:
    path = tmp_path / "separated.pdf"
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.rect(90, 600, 350, 100)
    c.line(100, 610, 400, 680)
    c.line(110, 610, 400, 660)
    c.setFont("Helvetica", 10)
    c.drawString(
        72, 500, "This ordinary paragraph separates the drawing from the label."
    )
    c.setFont("Helvetica-Bold", 9)
    c.drawString(72, 475, "Fig. 1. This is not adjacent to any drawing.")
    c.save()
    page = extract_document(path)["pages"][0]
    assert not page["captions"]
    assert all(
        g["evidence"] != "caption-bounded-composite" for g in page["graphic_regions"]
    )
