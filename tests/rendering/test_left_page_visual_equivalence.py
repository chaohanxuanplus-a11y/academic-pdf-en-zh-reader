# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_WIDTH_MPT,
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.rendering.vector_compose import (
    compose_source_pages_to_a3,
)

RENDER_SCALE = 2
MAX_PIXEL_DELTA = 8
MAX_DIFFERENT_PIXEL_RATIO = 0.001


def _make_visual_source(path: Path) -> None:
    font_name = "G1VisualNotoSerifSC"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path("assets/fonts/NotoSerifSC-Regular.ttf")
        pdfmetrics.registerFont(TTFont(font_name, font_path))

    canvas = Canvas(
        str(path),
        pagesize=(A4_WIDTH_MPT / 1000, A4_HEIGHT_MPT / 1000),
        invariant=1,
        pageCompression=0,
    )
    canvas.setFont(font_name, 18)
    canvas.drawString(50, 760, "Pixel-equivalent selectable text")
    canvas.setLineWidth(2)
    canvas.line(50, 720, 500, 620)
    canvas.saveState()
    canvas.setFillAlpha(0.5)
    canvas.setFillColorRGB(0.1, 0.4, 0.8)
    canvas.rect(70, 520, 180, 70, fill=1, stroke=0)
    canvas.restoreState()
    canvas.beginForm("visual_form", 0, 0, 80, 30)
    canvas.setFillColorRGB(0.9, 0.2, 0.1)
    canvas.circle(15, 15, 12, fill=1, stroke=0)
    canvas.line(30, 15, 75, 15)
    canvas.endForm()
    canvas.saveState()
    canvas.translate(70, 430)
    canvas.doForm("visual_form")
    canvas.restoreState()
    canvas.drawImage(
        ImageReader(Image.new("RGB", (8, 8), (20, 150, 80))),
        70,
        340,
        40,
        40,
    )
    canvas.save()


def _render_first_page(data: bytes, *, right_crop_pt: float = 0) -> Image.Image:
    document = pdfium.PdfDocument(data)
    try:
        page = document[0]
        try:
            bitmap = page.render(
                scale=RENDER_SCALE,
                crop=(0, 0, right_crop_pt, 0),
                grayscale=True,
                draw_annots=False,
                may_draw_forms=False,
            )
            return bitmap.to_pil().copy()
        finally:
            page.close()
    finally:
        document.close()


def test_output_left_half_is_pixel_equivalent_to_source(tmp_path: Path) -> None:
    source_path = tmp_path / "visual-source.pdf"
    output = BytesIO()
    _make_visual_source(source_path)

    compose_source_pages_to_a3(source_path, output)

    source_image = _render_first_page(source_path.read_bytes())
    output_image = _render_first_page(
        output.getvalue(),
        right_crop_pt=(A3_LANDSCAPE_WIDTH_MPT - A4_WIDTH_MPT) / 1000,
    )
    assert output_image.size == source_image.size

    difference = ImageChops.difference(source_image, output_image)
    extrema = difference.getextrema()
    max_delta = (
        extrema[1]
        if isinstance(extrema[0], int)
        else max(channel[1] for channel in extrema)
    )
    histogram = difference.convert("L").histogram()
    different_pixels = sum(histogram[1:])
    total_pixels = source_image.width * source_image.height

    assert max_delta <= MAX_PIXEL_DELTA
    assert different_pixels / total_pixels <= MAX_DIFFERENT_PIXEL_RATIO
