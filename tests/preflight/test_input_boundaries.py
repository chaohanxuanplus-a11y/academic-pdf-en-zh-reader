# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    EncodedStreamObject,
    FloatObject,
    NameObject,
    NumberObject,
)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.preflight.checks import preflight_safe_copy
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"


def _fixture(tmp_path: Path, fixture_id: str = "single-column") -> Path:
    output = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", output)
    return output


def _rewrite(path: Path, mutate) -> Path:
    reader = PdfReader(path, strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    mutate(writer)
    output = path.with_name(f"mutated-{path.name}")
    with output.open("wb") as stream:
        writer.write(stream)
    return output


def _text_pdf(path: Path, text: str) -> Path:
    canvas = Canvas(str(path), pagesize=A4, pageCompression=0)
    text_object = canvas.beginText(72, 760)
    text_object.setFont("Helvetica", 10)
    for _ in range(12):
        text_object.textLine(text)
    canvas.drawText(text_object)
    canvas.save()
    return path


def _single_text_page(path: Path, text: str) -> Path:
    canvas = Canvas(str(path), pagesize=A4, pageCompression=0)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(72, 760, text)
    canvas.save()
    return path


@pytest.mark.parametrize(
    "fixture_id",
    [
        "active-content",
        "cross-column-paragraph",
        "cross-page-paragraph",
        "figures-and-tables",
        "first-page-mixed",
        "long-translation",
        "single-column",
        "three-column",
        "two-column",
    ],
)
def test_all_cc0_layout_fixtures_pass_and_match_the_preflight_schema(
    tmp_path: Path, fixture_id: str
) -> None:
    result = preflight_safe_copy(_fixture(tmp_path, fixture_id))

    validate_artifact("preflight", result)
    assert result["passed"] is True
    assert result["error_codes"] == []
    assert all(page["extractable_character_count"] > 0 for page in result["pages"])


def test_encryption_and_malformed_input_fail_before_content_extraction(
    tmp_path: Path,
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=A4[0], height=A4[1])
    writer.encrypt("fixture-password")
    encrypted = tmp_path / "encrypted.pdf"
    with encrypted.open("wb") as stream:
        writer.write(stream)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not a valid cross-reference table\n%%EOF\n")

    encrypted_result = preflight_safe_copy(encrypted)
    broken_result = preflight_safe_copy(broken)

    assert encrypted_result["passed"] is False
    assert encrypted_result["error_codes"] == ["PDF_ENCRYPTED"]
    assert "fixture-password" not in str(encrypted_result)
    assert encrypted.name not in str(encrypted_result)
    assert broken_result["passed"] is False
    assert "PDF_PARSE_ERROR" in broken_result["error_codes"]
    assert "cross-reference" not in str(broken_result)


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        (
            lambda writer: writer.pages[0].__setitem__(
                NameObject("/CropBox"),
                ArrayObject(
                    [
                        FloatObject(-1),
                        FloatObject(0),
                        FloatObject(595),
                        FloatObject(841),
                    ]
                ),
            ),
            "PAGE_BOX_OUTSIDE_MEDIA",
        ),
        (
            lambda writer: writer.pages[0].__setitem__(
                NameObject("/CropBox"),
                ArrayObject(
                    [
                        FloatObject(10),
                        FloatObject(10),
                        FloatObject(10),
                        FloatObject(100),
                    ]
                ),
            ),
            "INVALID_PAGE_BOX",
        ),
        (
            lambda writer: writer.pages[0].__setitem__(
                NameObject("/UserUnit"), FloatObject(2)
            ),
            "UNSUPPORTED_USER_UNIT",
        ),
        (
            lambda writer: writer.pages[0].__setitem__(
                NameObject("/Rotate"), NumberObject(45)
            ),
            "INVALID_PAGE_ROTATION",
        ),
        (
            lambda writer: writer.pages[0].__setitem__(
                NameObject("/BleedBox"),
                ArrayObject(
                    [
                        FloatObject(-1),
                        FloatObject(0),
                        FloatObject(595),
                        FloatObject(841),
                    ]
                ),
            ),
            "PAGE_BOX_OUTSIDE_MEDIA",
        ),
    ],
)
def test_invalid_geometry_fails_closed(
    tmp_path: Path, mutation, error_code: str
) -> None:
    source = _fixture(tmp_path)
    result = preflight_safe_copy(_rewrite(source, mutation))

    assert result["passed"] is False
    assert error_code in result["error_codes"]


def test_valid_non_a4_page_size_is_recorded_for_later_normalization(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)

    def letter_page(writer: PdfWriter) -> None:
        box = ArrayObject(
            [FloatObject(0), FloatObject(0), FloatObject(612), FloatObject(792)]
        )
        writer.pages[0][NameObject("/MediaBox")] = box
        writer.pages[0][NameObject("/CropBox")] = ArrayObject(list(box))

    result = preflight_safe_copy(_rewrite(source, letter_page))

    assert result["passed"] is True
    assert result["error_codes"] == []
    assert result["pages"][0]["width_mpt"] == 612_000
    assert result["pages"][0]["height_mpt"] == 792_000


def test_rotation_must_be_consistent_across_the_document(tmp_path: Path) -> None:
    source = _fixture(tmp_path, "cross-page-paragraph")

    def mutate(writer: PdfWriter) -> None:
        writer.pages[1][NameObject("/Rotate")] = NumberObject(180)

    result = preflight_safe_copy(_rewrite(source, mutate))

    assert result["passed"] is False
    assert "INCONSISTENT_PAGE_ROTATION" in result["error_codes"]


def test_scan_like_page_and_non_english_text_fail_with_distinct_codes(
    tmp_path: Path,
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=A4[0], height=A4[1])
    scan_like = tmp_path / "scan-like.pdf"
    with scan_like.open("wb") as stream:
        writer.write(stream)
    digits_only = _text_pdf(
        tmp_path / "digits-only.pdf", "1234 5678 9012 3456 7890 -- ++ =="
    )

    scan_result = preflight_safe_copy(scan_like)
    digits_result = preflight_safe_copy(digits_only)

    assert "TEXT_LAYER_INSUFFICIENT" in scan_result["error_codes"]
    assert "ENGLISH_TEXT_INSUFFICIENT" in digits_result["error_codes"]


def test_text_threshold_is_document_wide_and_allows_a_blank_figure_page(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)

    def add_blank_page(writer: PdfWriter) -> None:
        writer.add_blank_page(width=A4[0], height=A4[1])

    result = preflight_safe_copy(_rewrite(source, add_blank_page))

    assert result["passed"] is True
    assert [page["extractable_character_count"] for page in result["pages"]][-1] == 0


def test_document_text_budget_scales_with_reachable_page_count(tmp_path: Path) -> None:
    source = _single_text_page(
        tmp_path / "one-text-page.pdf",
        "English methods demonstrate robust causal evidence in clinical trials.",
    )
    assert preflight_safe_copy(source)["passed"] is True

    def add_many_blank_pages(writer: PdfWriter) -> None:
        for _ in range(20):
            writer.add_blank_page(width=A4[0], height=A4[1])

    sparse_document = _rewrite(source, add_many_blank_pages)
    result = preflight_safe_copy(sparse_document)

    assert result["passed"] is False
    assert "TEXT_LAYER_INSUFFICIENT" in result["error_codes"]
    assert len(result["pages"]) == 21


def test_page_tree_is_walked_with_limits_before_pypdf_page_flattening(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)

    def add_page_and_lie_about_count(writer: PdfWriter) -> None:
        writer.add_blank_page(width=A4[0], height=A4[1])
        page_tree = writer.root_object["/Pages"].get_object()
        page_tree[NameObject("/Count")] = NumberObject(1)

    mutated = _rewrite(source, add_page_and_lie_about_count)
    from academic_pdf_en_zh_reader.preflight.checks import PreflightLimits

    result = preflight_safe_copy(mutated, limits=PreflightLimits(max_pages=1))

    assert result["passed"] is False
    assert "PAGE_COUNT_LIMIT_EXCEEDED" in result["error_codes"]


def test_extract_text_failure_is_sanitized_and_decoded_caches_are_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fixture(tmp_path)
    captured: list[EncodedStreamObject] = []

    def fail_after_caching(self: PageObject, *args, **kwargs):
        raw = self["/Contents"].get_object()
        assert isinstance(raw, EncodedStreamObject)
        raw.decoded_self = DecodedStreamObject()
        captured.append(raw)
        raise RuntimeError("SECRET-CONTROL-STRING")

    monkeypatch.setattr(PageObject, "extract_text", fail_after_caching)

    result = preflight_safe_copy(source)

    assert result["passed"] is False
    assert "TEXT_EXTRACTION_ERROR" in result["error_codes"]
    assert "SECRET-CONTROL-STRING" not in str(result)
    assert captured and all(stream.decoded_self is None for stream in captured)
