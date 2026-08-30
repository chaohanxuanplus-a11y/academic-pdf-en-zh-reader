# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
    PageGeometryError,
)
from academic_pdf_en_zh_reader.rendering.vector_compose import (
    compose_source_pages_to_a3,
)


def _register_test_font() -> str:
    name = "G1ComposeNotoSerifSC"
    if name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path("assets/fonts/NotoSerifSC-Regular.ttf")
        pdfmetrics.registerFont(TTFont(name, font_path))
    return name


def _draw_complex_page(canvas: Canvas, label: str) -> None:
    font_name = _register_test_font()
    canvas.setFont(font_name, 16)
    canvas.drawString(54, A4_HEIGHT_MPT / 1000 - 72, label)
    canvas.setLineWidth(1.25)
    canvas.line(54, 700, 420, 650)

    canvas.saveState()
    canvas.setFillAlpha(0.45)
    canvas.setFillColorRGB(0.8, 0.1, 0.1)
    canvas.rect(72, 560, 140, 55, fill=1, stroke=0)
    canvas.restoreState()

    canvas.beginForm("vector_badge", 0, 0, 72, 24)
    canvas.setFillColorRGB(0.1, 0.2, 0.7)
    canvas.circle(12, 12, 10, fill=1, stroke=0)
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.line(24, 12, 68, 12)
    canvas.endForm()
    canvas.saveState()
    canvas.translate(72, 480)
    canvas.doForm("vector_badge")
    canvas.restoreState()

    image = Image.new("RGB", (8, 8), (30, 160, 90))
    canvas.drawImage(ImageReader(image), 72, 390, 32, 32, mask="auto")


def _make_active_source(path: Path) -> None:
    raw = BytesIO()
    canvas = Canvas(
        raw,
        pagesize=(A4_WIDTH_MPT / 1000, A4_HEIGHT_MPT / 1000),
        invariant=1,
        pageCompression=0,
    )
    _draw_complex_page(canvas, "Selectable vector source page")
    canvas.showPage()
    canvas.setPageSize((A4_HEIGHT_MPT / 1000, A4_WIDTH_MPT / 1000))
    canvas.setFont(_register_test_font(), 14)
    canvas.drawString(72, 72, "Rotated vector source page")
    canvas.showPage()
    canvas.setPageSize((A4_WIDTH_MPT / 1000, A4_HEIGHT_MPT / 1000))
    canvas.setFont(_register_test_font(), 14)
    canvas.drawString(72, 720, "Tolerated CropBox source page")
    canvas.save()

    reader = PdfReader(BytesIO(raw.getvalue()))
    writer = PdfWriter()
    for source_page in reader.pages:
        writer.add_page(source_page)

    writer.pages[1][NameObject("/Rotate")] = NumberObject(90)
    writer.pages[2].cropbox = ArrayObject(
        [
            FloatObject(0.25),
            FloatObject(0.25),
            FloatObject(A4_WIDTH_MPT / 1000 - 0.25),
            FloatObject(A4_HEIGHT_MPT / 1000 - 0.25),
        ]
    )

    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Rect"): ArrayObject(
                [FloatObject(20), FloatObject(20), FloatObject(40), FloatObject(40)]
            ),
            NameObject("/Contents"): TextStringObject("must not be copied"),
        }
    )
    writer.pages[0][NameObject("/Annots")] = ArrayObject([annotation])

    javascript = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('must not run')"),
        }
    )
    writer.root_object[NameObject("/OpenAction")] = javascript
    writer.root_object[NameObject("/AA")] = DictionaryObject(
        {NameObject("/WC"): javascript}
    )
    writer.root_object[NameObject("/AcroForm")] = DictionaryObject(
        {NameObject("/Fields"): ArrayObject()}
    )
    writer.add_attachment("private.txt", b"must not be copied")
    writer.write(path)


def _walk_xobjects(page) -> tuple[set[str], Counter[tuple[int, int, str]]]:
    subtypes: set[str] = set()
    images: Counter[tuple[int, int, str]] = Counter()
    visited: set[tuple[int, int] | int] = set()

    def visit_resources(resources) -> None:
        resources = resources.get_object()
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return
        for reference in xobjects.get_object().values():
            key: tuple[int, int] | int
            if hasattr(reference, "idnum"):
                key = (reference.idnum, reference.generation)
            else:
                key = id(reference)
            if key in visited:
                continue
            visited.add(key)
            xobject = reference.get_object()
            subtype = str(xobject.get("/Subtype"))
            subtypes.add(subtype)
            if subtype == "/Image":
                images[
                    (
                        int(xobject["/Width"]),
                        int(xobject["/Height"]),
                        sha256(xobject.get_data()).hexdigest(),
                    )
                ] += 1
            elif subtype == "/Form" and "/Resources" in xobject:
                visit_resources(xobject["/Resources"])

    visit_resources(page["/Resources"])
    return subtypes, images


def _has_embedded_font(page) -> bool:
    resources = page["/Resources"].get_object()
    for font_reference in resources.get("/Font", {}).get_object().values():
        font = font_reference.get_object()
        descriptors = []
        if "/FontDescriptor" in font:
            descriptors.append(font["/FontDescriptor"].get_object())
        if "/DescendantFonts" in font:
            for descendant in font["/DescendantFonts"].get_object():
                descendant = descendant.get_object()
                if "/FontDescriptor" in descendant:
                    descriptors.append(descendant["/FontDescriptor"].get_object())
        if any(
            key in descriptor
            for descriptor in descriptors
            for key in ("/FontFile", "/FontFile2", "/FontFile3")
        ):
            return True
    return False


def test_compose_preserves_visible_resources_and_strips_active_content(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "active-source.pdf"
    output_path = tmp_path / "a3-output.pdf"
    repeated_output_path = tmp_path / "a3-output-repeated.pdf"
    _make_active_source(source_path)

    compose_source_pages_to_a3(source_path, output_path)
    compose_source_pages_to_a3(source_path, repeated_output_path)

    assert output_path.read_bytes() == repeated_output_path.read_bytes()

    source = PdfReader(source_path)
    output = PdfReader(output_path)
    assert len(output.pages) == 3
    for page in output.pages:
        assert round(float(page.mediabox.width) * 1000) == A3_LANDSCAPE_WIDTH_MPT
        assert round(float(page.mediabox.height) * 1000) == A3_LANDSCAPE_HEIGHT_MPT
        assert "/Annots" not in page or not page["/Annots"]

    extracted = "\n".join(page.extract_text() or "" for page in output.pages)
    assert "Selectable vector source page" in extracted
    assert "Rotated vector source page" in extracted
    assert "Tolerated CropBox source page" in extracted

    source_subtypes, source_images = _walk_xobjects(source.pages[0])
    output_subtypes, output_images = _walk_xobjects(output.pages[0])
    assert {"/Form", "/Image"} <= source_subtypes
    assert source_subtypes <= output_subtypes
    assert output_images == source_images
    assert "/ExtGState" in output.pages[0]["/Resources"]
    assert _has_embedded_font(source.pages[0])
    assert _has_embedded_font(output.pages[0])

    first_cm = next(
        operands
        for operands, operator in output.pages[0].get_contents().operations
        if operator == b"cm"
    )
    assert [float(value) for value in first_cm] == [1, 0, 0, 1, 0, 0]

    forbidden_root_keys = {"/AA", "/AcroForm", "/Names", "/OpenAction"}
    assert forbidden_root_keys.isdisjoint(output.root_object)
    output_bytes = output_path.read_bytes()
    for forbidden_token in (
        b"/Annot",
        b"/EmbeddedFile",
        b"/Filespec",
        b"/JavaScript",
        b"/OpenAction",
        b"/AcroForm",
        b"must not be copied",
    ):
        assert forbidden_token not in output_bytes


def test_invalid_later_page_fails_before_creating_output(tmp_path: Path) -> None:
    source_path = tmp_path / "mixed-page-sizes.pdf"
    output_path = tmp_path / "must-not-exist.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=A4_WIDTH_MPT / 1000, height=A4_HEIGHT_MPT / 1000)
    writer.add_blank_page(width=612, height=792)
    writer.write(source_path)

    with pytest.raises(PageGeometryError, match="A4"):
        compose_source_pages_to_a3(source_path, output_path)

    assert not output_path.exists()
