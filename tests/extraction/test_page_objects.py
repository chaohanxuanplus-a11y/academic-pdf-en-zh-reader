# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.extraction import (
    ScannedPdfUnsupportedError,
    extract_document,
)
from academic_pdf_en_zh_reader.extraction.page_objects import extract_page_objects
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"
FIGURE_SPEC = SPECS / "figures-and-tables.json"


def _fixture(tmp_path: Path) -> Path:
    path = tmp_path / "figures-and-tables.pdf"
    generate_fixture(FIGURE_SPEC, path)
    return path


def test_extracts_page_box_chars_fonts_colors_and_vector_objects_once(
    tmp_path: Path,
) -> None:
    result = extract_page_objects(_fixture(tmp_path))

    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.page_number == 1
    assert page.media_box_mpt == (0, 0, 595276, 841890)
    assert page.crop_box_mpt == page.media_box_mpt
    assert page.chars
    assert page.rectangles
    assert page.curves
    assert all(character.font_name for character in page.chars)
    assert all(character.fill_color is not None for character in page.chars)

    all_boxes = [
        page.media_box_mpt,
        page.crop_box_mpt,
        *(item.bbox_mpt for item in page.chars),
        *(item.bbox_mpt for item in page.rectangles),
        *(item.bbox_mpt for item in page.curves),
    ]
    assert all(type(coordinate) is int for box in all_boxes for coordinate in box)


def test_objects_outside_equal_media_and_crop_boxes_are_not_extracted(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "clipped-vector.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.line(-40, -10, A4[0] + 40, -10)
    canvas.line(72, 100, 144, 100)
    canvas.save()

    page = extract_page_objects(pdf_path).pages[0]

    assert len(page.curves) == 1
    assert page.curves[0].bbox_mpt == (72_000, 100_000, 144_000, 100_000)


def test_extracts_embedded_images_with_bottom_left_geometry(tmp_path: Path) -> None:
    image = Image.new("RGB", (2, 3), (150, 25, 20))
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    pdf_path = tmp_path / "image.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.drawImage(ImageReader(image_bytes), 72, 144, width=36, height=54)
    canvas.save()

    page = extract_page_objects(pdf_path).pages[0]

    assert len(page.images) == 1
    assert page.images[0].bbox_mpt == (72000, 144000, 108000, 198000)
    assert page.images[0].pixel_width == 2
    assert page.images[0].pixel_height == 3


def test_full_page_scan_with_substantial_text_overlay_is_rejected(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (60, 84), (235, 235, 235))
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    pdf_path = tmp_path / "ocr-overlay.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.drawImage(
        ImageReader(image_bytes),
        0,
        0,
        width=A4[0],
        height=A4[1],
    )
    for ordinal in range(20):
        canvas.drawString(
            72,
            720 - ordinal * 12,
            f"OCR overlay line {ordinal:02d} contains ordinary academic prose.",
        )
    canvas.save()

    with pytest.raises(ScannedPdfUnsupportedError):
        extract_document(pdf_path)


def test_full_page_image_with_only_a_short_label_is_not_called_a_scan(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (60, 84), (235, 235, 235))
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    pdf_path = tmp_path / "full-page-illustration.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.drawImage(
        ImageReader(image_bytes),
        0,
        0,
        width=A4[0],
        height=A4[1],
    )
    canvas.drawString(72, 40, "Figure 1. Full-page illustration.")
    canvas.save()

    assert extract_document(pdf_path)["pages"]


def test_rotation_and_cropbox_use_displayed_media_bottom_left_coordinates(
    tmp_path: Path,
) -> None:
    base = tmp_path / "landscape-base.pdf"
    transformed = tmp_path / "rotated-crop.pdf"
    media_width = A4[1] + 20
    media_height = A4[0] + 20
    canvas = Canvas(str(base), pagesize=(media_width, media_height), invariant=1)
    canvas.drawString(100, 200, "Rotation probe")
    canvas.save()
    reader = PdfReader(base)
    source_page = reader.pages[0]
    source_page.cropbox = RectangleObject([10, 10, 10 + A4[1], 10 + A4[0]])
    source_page.rotate(90)
    writer = PdfWriter()
    writer.add_page(source_page)
    with transformed.open("wb") as stream:
        writer.write(stream)

    page = extract_page_objects(transformed).pages[0]

    assert page.rotation_degrees == 90
    assert page.media_box_mpt == (-10000, -10000, 605276, 851890)
    assert page.crop_box_mpt == (0, 0, 595276, 841890)
    assert all(
        page.crop_box_mpt[0] <= coordinate.bbox_mpt[0]
        and coordinate.bbox_mpt[2] <= page.crop_box_mpt[2]
        and page.crop_box_mpt[1] <= coordinate.bbox_mpt[1]
        and coordinate.bbox_mpt[3] <= page.crop_box_mpt[3]
        for coordinate in page.chars
    )


@pytest.mark.parametrize(
    ("rotation", "expected_media", "expected_crop", "expected_first_char"),
    [
        (
            0,
            (-20_000, -40_000, 880_000, 580_000),
            (0, 0, 810_000, 530_000),
            (80_000, 147_516, 86_672, 159_516),
        ),
        (
            90,
            (-40_000, -20_000, 580_000, 880_000),
            (0, 0, 530_000, 810_000),
            (147_516, 773_328, 159_516, 780_000),
        ),
        (
            180,
            (-20_000, -40_000, 880_000, 580_000),
            (0, 0, 810_000, 530_000),
            (773_328, 380_484, 780_000, 392_484),
        ),
        (
            270,
            (-40_000, -20_000, 580_000, 880_000),
            (0, 0, 530_000, 810_000),
            (380_484, 80_000, 392_484, 86_672),
        ),
    ],
)
def test_asymmetric_boxes_and_all_rotations_share_one_visible_coordinate_system(
    tmp_path: Path,
    rotation: int,
    expected_media: tuple[int, int, int, int],
    expected_crop: tuple[int, int, int, int],
    expected_first_char: tuple[int, int, int, int],
) -> None:
    base = tmp_path / f"asymmetric-base-{rotation}.pdf"
    transformed = tmp_path / f"asymmetric-{rotation}.pdf"
    canvas = Canvas(str(base), pagesize=(900, 620), invariant=1)
    canvas.drawString(120, 220, "probe")
    canvas.save()
    source_page = PdfReader(base).pages[0]
    source_page.mediabox = RectangleObject([20, 30, 920, 650])
    source_page.cropbox = RectangleObject([40, 70, 850, 600])
    source_page.rotate(rotation)
    writer = PdfWriter()
    writer.add_page(source_page)
    with transformed.open("wb") as stream:
        writer.write(stream)

    page = extract_page_objects(transformed).pages[0]

    assert page.media_box_mpt == expected_media
    assert page.crop_box_mpt == expected_crop
    probe_character = next(item for item in page.chars if item.text == "p")
    assert probe_character.bbox_mpt == expected_first_char
    assert all(
        0 <= coordinate <= limit
        for item in page.chars
        for coordinate, limit in zip(
            item.bbox_mpt,
            (expected_crop[2], expected_crop[3], expected_crop[2], expected_crop[3]),
            strict=True,
        )
    )


def test_object_order_and_ids_are_byte_stable_across_repeated_extraction(
    tmp_path: Path,
) -> None:
    path = _fixture(tmp_path)

    first = extract_page_objects(path)
    second = extract_page_objects(path)

    assert first == second
    first_page = first.pages[0]
    identifiers = [
        *(item.id for item in first_page.chars),
        *(item.id for item in first_page.rectangles),
        *(item.id for item in first_page.curves),
        *(item.id for item in first_page.images),
    ]
    assert len(identifiers) == len(set(identifiers))


def test_each_pdf_page_cache_is_closed_before_document_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pdfplumber

    pdf_path = tmp_path / "two-pages.pdf"
    canvas = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    canvas.drawString(72, 720, "First page body text")
    canvas.showPage()
    canvas.drawString(72, 720, "Second page body text")
    canvas.save()
    closing_document = False
    directly_closed_pages: list[tuple[str, int]] = []
    original_pdf_close = pdfplumber.pdf.PDF.close
    original_page_close = pdfplumber.page.Page.close

    def tracked_pdf_close(document: object) -> None:
        nonlocal closing_document
        closing_document = True
        try:
            original_pdf_close(document)  # type: ignore[arg-type]
        finally:
            closing_document = False

    def tracked_page_close(page: object) -> None:
        if not closing_document:
            directly_closed_pages.append(  # type: ignore[attr-defined]
                (type(page).__name__, page.page_number)
            )
        original_page_close(page)  # type: ignore[arg-type]

    monkeypatch.setattr(pdfplumber.pdf.PDF, "close", tracked_pdf_close)
    monkeypatch.setattr(pdfplumber.page.Page, "close", tracked_page_close)

    extract_page_objects(pdf_path)

    assert directly_closed_pages == [
        ("CroppedPage", 1),
        ("Page", 1),
        ("CroppedPage", 2),
        ("Page", 2),
    ]


def test_document_metadata_cannot_influence_extracted_page_facts(
    tmp_path: Path,
) -> None:
    original = _fixture(tmp_path)
    metadata_variant = tmp_path / "hostile-metadata.pdf"
    reader = PdfReader(original)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.add_metadata(
        {
            "/FixtureTruth": '{"roles":{"invented":"body"}}',
            "/Title": "Metadata must not be an extraction oracle",
        }
    )
    with metadata_variant.open("wb") as stream:
        writer.write(stream)

    assert extract_page_objects(original) == extract_page_objects(metadata_variant)


def test_public_child_entrypoint_accepts_path_and_returns_json_safe_mapping(
    tmp_path: Path,
) -> None:
    path = _fixture(tmp_path)

    result = extract_document(path)

    assert result["format_version"] == "1.0.0"
    assert result["pages"][0]["page_number"] == 1
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    with pytest.raises(TypeError, match="pathlib.Path"):
        extract_document(str(path))  # type: ignore[arg-type]


def test_fresh_package_import_does_not_load_pdf_parser_dependencies() -> None:
    source_root = ROOT / "src"
    script = (
        f"import sys;sys.path.insert(0,{str(source_root)!r});"
        "import academic_pdf_en_zh_reader.extraction;"
        "assert 'pdfplumber' not in sys.modules;"
        "assert not any(n == 'pdfminer' or n.startswith('pdfminer.') "
        "for n in sys.modules)"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "spec_path",
    [path for path in sorted(SPECS.glob("*.json")) if path.stem != "active-content"],
    ids=lambda path: path.stem,
)
def test_all_safe_cc0_layout_fixtures_extract_deterministically(
    spec_path: Path,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / f"{spec_path.stem}.pdf"
    generate_fixture(spec_path, pdf_path)

    first = extract_document(pdf_path)
    second = extract_document(pdf_path)

    assert first == second
    assert [page["page_number"] for page in first["pages"]] == list(
        range(1, len(first["pages"]) + 1)
    )
