# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.qa.image_fingerprints import (
    page_image_fingerprints,
)
from academic_pdf_en_zh_reader.qa.pdf_structure import (
    PdfStructureQaError,
    validate_no_new_page_rasters,
)


def _png(color: tuple[int, int, int], *, size: tuple[int, int] = (8, 8)) -> bytes:
    payload = BytesIO()
    Image.new("RGB", size, color).save(payload, format="PNG")
    return payload.getvalue()


def _pdf_pages(
    images: list[tuple[bytes, tuple[float, float, float, float]] | None],
) -> bytes:
    payload = BytesIO()
    canvas = Canvas(payload, pagesize=(100, 100), pageCompression=0)
    for page_number, image in enumerate(images, start=1):
        canvas.drawString(5, 90, f"page {page_number}")
        if image is not None:
            image_bytes, (x, y, width, height) = image
            canvas.drawImage(
                ImageReader(BytesIO(image_bytes)),
                x,
                y,
                width,
                height,
            )
        canvas.showPage()
    canvas.save()
    return payload.getvalue()


def _merge(source_pdf: bytes, overlay_pdf: bytes) -> bytes:
    source = PdfReader(BytesIO(source_pdf), strict=True)
    overlay = PdfReader(BytesIO(overlay_pdf), strict=True)
    assert len(source.pages) == len(overlay.pages)
    writer = PdfWriter()
    for source_page, overlay_page in zip(source.pages, overlay.pages, strict=True):
        source_page.merge_page(overlay_page, expand=False)
        writer.add_page(source_page)
    payload = BytesIO()
    writer.write(payload)
    return payload.getvalue()


def _declared_fingerprints(pdf: bytes, page_number: int) -> list[str]:
    reader = PdfReader(BytesIO(pdf), strict=True)
    counter = page_image_fingerprints(reader.pages[page_number - 1])
    return sorted(counter.elements())


def _manifest(
    source_page_numbers: list[int],
    declared_by_page: list[list[str]],
) -> dict[str, object]:
    return {
        "pages": [
            {
                "output_page_number": output_page_number,
                "source_page_number": source_page_number,
                "page_kind": "native",
                "continuation_index": 0,
                "continuation_label_present": False,
                "overlay_image_fingerprints": declared,
            }
            for output_page_number, (source_page_number, declared) in enumerate(
                zip(source_page_numbers, declared_by_page, strict=True),
                start=1,
            )
        ]
    }


def test_exact_declared_overlay_images_are_admitted() -> None:
    source_pdf = _pdf_pages([(_png((0, 128, 0)), (5, 5, 20, 20))])
    overlay_pdf = _pdf_pages([(_png((0, 0, 255)), (60, 60, 15, 15))])
    output_pdf = _merge(source_pdf, overlay_pdf)
    manifest = _manifest([1], [_declared_fingerprints(overlay_pdf, 1)])

    evidence = validate_no_new_page_rasters(
        PdfReader(BytesIO(source_pdf), strict=True),
        PdfReader(BytesIO(output_pdf), strict=True),
        manifest,
    )

    assert evidence == {"image_count": 2}


@pytest.mark.parametrize(
    "declared",
    (
        None,
        "a" * 64,
        ["A" * 64],
        ["a" * 63],
        ["b" * 64, "a" * 64],
    ),
)
def test_malformed_or_unsorted_declarations_are_rejected(declared: object) -> None:
    source_pdf = _pdf_pages([None])
    manifest_page: dict[str, object] = {
        "output_page_number": 1,
        "source_page_number": 1,
        "page_kind": "native",
        "continuation_index": 0,
        "continuation_label_present": False,
    }
    if declared is not None:
        manifest_page["overlay_image_fingerprints"] = declared

    with pytest.raises(PdfStructureQaError, match="PDF_RASTER_SUBSTITUTION"):
        validate_no_new_page_rasters(
            PdfReader(BytesIO(source_pdf), strict=True),
            PdfReader(BytesIO(source_pdf), strict=True),
            {"pages": [manifest_page]},
        )


def test_undeclared_extra_image_is_rejected() -> None:
    source_pdf = _pdf_pages([None])
    overlay_pdf = _pdf_pages([(_png((255, 0, 0)), (10, 10, 20, 20))])
    output_pdf = _merge(source_pdf, overlay_pdf)

    with pytest.raises(PdfStructureQaError, match="PDF_RASTER_SUBSTITUTION"):
        validate_no_new_page_rasters(
            PdfReader(BytesIO(source_pdf), strict=True),
            PdfReader(BytesIO(output_pdf), strict=True),
            _manifest([1], [[]]),
        )


def test_overlay_image_declared_on_the_wrong_page_is_rejected() -> None:
    source_pdf = _pdf_pages([None, None])
    overlay_pdf = _pdf_pages([(_png((255, 0, 0)), (10, 10, 20, 20)), None])
    output_pdf = _merge(source_pdf, overlay_pdf)
    brand = _declared_fingerprints(overlay_pdf, 1)

    with pytest.raises(PdfStructureQaError, match="PDF_RASTER_SUBSTITUTION"):
        validate_no_new_page_rasters(
            PdfReader(BytesIO(source_pdf), strict=True),
            PdfReader(BytesIO(output_pdf), strict=True),
            _manifest([1, 2], [[], brand]),
        )


def test_overlay_image_byte_tampering_is_rejected() -> None:
    source_pdf = _pdf_pages([None])
    declared_overlay = _pdf_pages([(_png((255, 0, 0)), (10, 10, 20, 20))])
    tampered_overlay = _pdf_pages([(_png((0, 0, 255)), (10, 10, 20, 20))])
    output_pdf = _merge(source_pdf, tampered_overlay)

    with pytest.raises(PdfStructureQaError, match="PDF_RASTER_SUBSTITUTION"):
        validate_no_new_page_rasters(
            PdfReader(BytesIO(source_pdf), strict=True),
            PdfReader(BytesIO(output_pdf), strict=True),
            _manifest([1], [_declared_fingerprints(declared_overlay, 1)]),
        )


def test_unlisted_full_page_raster_replacement_is_rejected() -> None:
    source_pdf = _pdf_pages([None])
    replacement_pdf = _pdf_pages(
        [(_png((255, 255, 255), size=(100, 100)), (0, 0, 100, 100))]
    )

    with pytest.raises(PdfStructureQaError, match="PDF_RASTER_SUBSTITUTION"):
        validate_no_new_page_rasters(
            PdfReader(BytesIO(source_pdf), strict=True),
            PdfReader(BytesIO(replacement_pdf), strict=True),
            _manifest([1], [[]]),
        )
