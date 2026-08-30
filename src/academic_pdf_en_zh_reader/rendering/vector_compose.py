# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Place visible A4 source pages as vector content on A3 left panels."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import NameObject, RectangleObject

from .page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
    PageGeometry,
    inspect_a4_page_geometry,
)

type PdfInput = str | Path | BinaryIO
type PdfOutput = str | Path | BinaryIO


class VectorCompositionError(ValueError):
    """Raised when a source document cannot be safely vector-composed."""


def _prepare_page(
    staging_writer: PdfWriter,
    source_page: PageObject,
    geometry: PageGeometry,
) -> tuple[PageObject, tuple[float, float]]:
    page = staging_writer.add_page(source_page, excluded_keys=("/Annots",))

    if geometry.rotation_degrees:
        page.transfer_rotation_to_content()

    normalized = inspect_a4_page_geometry(page)
    left, bottom, right, top = normalized.visible_box_mpt

    # A tolerated oversized box is clipped, never scaled, at the fixed A4 panel.
    clipped_right = min(right, left + A4_WIDTH_MPT)
    clipped_top = min(top, bottom + A4_HEIGHT_MPT)
    page.cropbox = RectangleObject(
        (
            left / 1000,
            bottom / 1000,
            clipped_right / 1000,
            clipped_top / 1000,
        )
    )
    return page, (-left / 1000, -bottom / 1000)


def compose_source_pages_to_a3(source_pdf: PdfInput, output_pdf: PdfOutput) -> None:
    """Write a fresh inactive A3 document containing vector source left panels.

    Every source page is validated and prepared before output is opened. The
    destination therefore remains absent when a later page fails validation.
    """

    reader = PdfReader(source_pdf, strict=True)
    if reader.is_encrypted:
        raise VectorCompositionError("encrypted PDFs cannot be vector-composed")
    if not reader.pages:
        raise VectorCompositionError("source PDF must contain at least one page")

    geometries = [inspect_a4_page_geometry(page) for page in reader.pages]
    staging_writer = PdfWriter()
    prepared_pages = [
        _prepare_page(staging_writer, page, geometry)
        for page, geometry in zip(reader.pages, geometries, strict=True)
    ]

    writer = PdfWriter()
    for source_page, (translate_x, translate_y) in prepared_pages:
        destination = writer.add_blank_page(
            width=A3_LANDSCAPE_WIDTH_MPT / 1000,
            height=A3_LANDSCAPE_HEIGHT_MPT / 1000,
        )
        destination.merge_transformed_page(
            source_page,
            Transformation().translate(translate_x, translate_y),
            expand=False,
        )
        destination.pop(NameObject("/Annots"), None)

    writer.write(output_pdf)
