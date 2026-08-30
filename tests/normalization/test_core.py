# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from hashlib import sha256
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.normalization.core import (
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
    NormalizationError,
    normalize_pdf_bytes,
    plan_displayed_crop,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


def _source_pdf(
    *,
    width_pt: float,
    height_pt: float,
    crop_pt: tuple[float, float, float, float] | None = None,
    rotation: int = 0,
    active: bool = False,
) -> bytes:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(width_pt, height_pt), invariant=1)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(40, 60, "VECTOR_MARKER")
    canvas.rect(30, 40, 180, 80, stroke=1, fill=0)
    canvas.showPage()
    canvas.save()

    reader = PdfReader(BytesIO(stream.getvalue()), strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    page = writer.pages[0]
    if crop_pt is not None:
        page.cropbox.lower_left = (crop_pt[0], crop_pt[1])
        page.cropbox.upper_right = (crop_pt[2], crop_pt[3])
    if rotation:
        page[NameObject("/Rotate")] = NumberObject(rotation)
    if active:
        writer.root_object[NameObject("/OpenAction")] = DictionaryObject()
        writer.root_object[NameObject("/Names")] = DictionaryObject()
        writer.root_object[NameObject("/AcroForm")] = DictionaryObject()
        page[NameObject("/AA")] = DictionaryObject()
        page[NameObject("/Annots")] = ArrayObject([DictionaryObject()])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _preflight(
    source: bytes,
    *,
    media_box_mpt: list[int],
    crop_box_mpt: list[int],
    rotation: int,
) -> dict[str, object]:
    raw_width = crop_box_mpt[2] - crop_box_mpt[0]
    raw_height = crop_box_mpt[3] - crop_box_mpt[1]
    width, height = (
        (raw_height, raw_width) if rotation in {90, 270} else (raw_width, raw_height)
    )
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "preflight",
        "source_sha256": sha256(source).hexdigest(),
        "passed": True,
        "pages": [
            {
                "page_number": 1,
                "width_mpt": width,
                "height_mpt": height,
                "media_box_mpt": media_box_mpt,
                "crop_box_mpt": crop_box_mpt,
                "rotation_degrees": rotation,
                "extractable_character_count": 13,
            }
        ],
        "limits": {
            "file_bytes": {"observed": len(source), "maximum": 100 * 1024 * 1024}
        },
    }


def _normalize(
    source: bytes, preflight: dict[str, object], *, maximum: int = 16 * 1024 * 1024
):
    return normalize_pdf_bytes(
        source,
        preflight=preflight,
        preflight_sha256=sha256_canonical(preflight),
        max_output_bytes=maximum,
    )


def test_short_axes_stay_one_to_one_and_pad_with_remainder_right_top() -> None:
    plan = plan_displayed_crop(595_000, 794_000)

    assert plan.scale_ppm == 1_000_000
    assert plan.scaled_width_mpt == 595_000
    assert plan.scaled_height_mpt == 794_000
    assert (plan.padding_left_mpt, plan.padding_right_mpt) == (138, 138)
    assert (plan.padding_bottom_mpt, plan.padding_top_mpt) == (23_945, 23_945)
    assert plan.normalized_content_box_mpt == (138, 23_945, 595_138, 817_945)


def test_odd_padding_remainder_goes_to_right_and_top() -> None:
    plan = plan_displayed_crop(A4_WIDTH_MPT - 1, A4_HEIGHT_MPT - 1)

    assert plan.scale_ppm == 1_000_000
    assert (plan.padding_left_mpt, plan.padding_right_mpt) == (0, 1)
    assert (plan.padding_bottom_mpt, plan.padding_top_mpt) == (0, 1)


def test_any_oversized_axis_uses_one_uniform_floor_scale_without_upscaling() -> None:
    width = 700_000
    height = 700_000
    plan = plan_displayed_crop(width, height)
    expected = min(
        1_000_000,
        A4_WIDTH_MPT * 1_000_000 // width,
        A4_HEIGHT_MPT * 1_000_000 // height,
    )

    assert plan.scale_ppm == expected
    assert plan.scaled_width_mpt == (width * expected + 500_000) // 1_000_000
    assert plan.scaled_height_mpt == (height * expected + 500_000) // 1_000_000
    assert plan.scaled_width_mpt <= A4_WIDTH_MPT
    assert plan.scaled_height_mpt <= A4_HEIGHT_MPT
    assert plan.padding_left_mpt + plan.padding_right_mpt == (
        A4_WIDTH_MPT - plan.scaled_width_mpt
    )
    assert plan.padding_bottom_mpt + plan.padding_top_mpt == (
        A4_HEIGHT_MPT - plan.scaled_height_mpt
    )


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (0, 1),
        (1, 0),
        (-1, 1),
        (True, 1),
        (1, False),
        (2_147_483_648, 1),
    ],
)
def test_plan_rejects_invalid_or_unrepresentable_dimensions(
    width: object, height: object
) -> None:
    with pytest.raises(NormalizationError):
        plan_displayed_crop(width, height)  # type: ignore[arg-type]


def test_normalize_short_journal_page_is_a4_vector_fresh_and_deterministic() -> None:
    source = _source_pdf(width_pt=595, height_pt=794, active=True)
    preflight = _preflight(
        source,
        media_box_mpt=[0, 0, 595_000, 794_000],
        crop_box_mpt=[0, 0, 595_000, 794_000],
        rotation=0,
    )

    first = _normalize(source, preflight)
    second = _normalize(source, preflight)

    assert first.pdf_bytes == second.pdf_bytes
    assert first.artifact == second.artifact
    validate_artifact("normalization", first.artifact)
    assert first.artifact == {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": "1.0.0",
        "source_sha256": sha256(source).hexdigest(),
        "preflight_sha256": sha256_canonical(preflight),
        "normalized_pdf_sha256": sha256(first.pdf_bytes).hexdigest(),
        "normalized_pdf_bytes": len(first.pdf_bytes),
        "pages": [
            {
                "page_number": 1,
                "source_media_box_mpt": [0, 0, 595_000, 794_000],
                "source_crop_box_mpt": [0, 0, 595_000, 794_000],
                "source_rotation_degrees": 0,
                "displayed_width_mpt": 595_000,
                "displayed_height_mpt": 794_000,
                "scale_ppm": 1_000_000,
                "scaled_width_mpt": 595_000,
                "scaled_height_mpt": 794_000,
                "padding_left_mpt": 138,
                "padding_bottom_mpt": 23_945,
                "padding_right_mpt": 138,
                "padding_top_mpt": 23_945,
                "normalized_content_box_mpt": [138, 23_945, 595_138, 817_945],
            }
        ],
    }

    reader = PdfReader(BytesIO(first.pdf_bytes), strict=True)
    assert not reader.is_encrypted
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert round(float(page.mediabox.width) * 1000) == A4_WIDTH_MPT
    assert round(float(page.mediabox.height) * 1000) == A4_HEIGHT_MPT
    assert tuple(page.cropbox) == tuple(page.mediabox)
    assert int(page.get("/Rotate", 0)) % 360 == 0
    assert "VECTOR_MARKER" in (page.extract_text() or "")
    assert "/Annots" not in page
    assert "/AA" not in page
    assert not {
        "/OpenAction",
        "/AA",
        "/Names",
        "/AcroForm",
        "/Collection",
        "/Perms",
    }.intersection(reader.root_object)
    resources = page.get("/Resources", {}).get_object()
    xobjects = resources.get("/XObject")
    if xobjects is not None:
        assert all(
            str(item.get_object().get("/Subtype")) != "/Image"
            for item in xobjects.get_object().values()
        )


def test_rotated_nonzero_crop_uses_displayed_crop_axes() -> None:
    source = _source_pdf(
        width_pt=500,
        height_pt=700,
        crop_pt=(20, 30, 420, 630),
        rotation=90,
    )
    preflight = _preflight(
        source,
        media_box_mpt=[0, 0, 500_000, 700_000],
        crop_box_mpt=[20_000, 30_000, 420_000, 630_000],
        rotation=90,
    )

    result = _normalize(source, preflight)
    page = result.artifact["pages"][0]

    assert page["displayed_width_mpt"] == 600_000
    assert page["displayed_height_mpt"] == 400_000
    assert page["scale_ppm"] == A4_WIDTH_MPT * 1_000_000 // 600_000
    output = PdfReader(BytesIO(result.pdf_bytes), strict=True).pages[0]
    assert round(float(output.mediabox.width) * 1000) == A4_WIDTH_MPT
    assert round(float(output.mediabox.height) * 1000) == A4_HEIGHT_MPT
    assert int(output.get("/Rotate", 0)) % 360 == 0


def test_hashes_page_geometry_and_output_bound_fail_closed() -> None:
    source = _source_pdf(width_pt=595, height_pt=794)
    preflight = _preflight(
        source,
        media_box_mpt=[0, 0, 595_000, 794_000],
        crop_box_mpt=[0, 0, 595_000, 794_000],
        rotation=0,
    )

    with pytest.raises(NormalizationError) as mismatch:
        normalize_pdf_bytes(
            source + b"\n",
            preflight=preflight,
            preflight_sha256=sha256_canonical(preflight),
            max_output_bytes=16 * 1024 * 1024,
        )
    assert mismatch.value.code == "NORMALIZATION_SOURCE_MISMATCH"

    with pytest.raises(NormalizationError) as preflight_mismatch:
        normalize_pdf_bytes(
            source,
            preflight=preflight,
            preflight_sha256="0" * 64,
            max_output_bytes=16 * 1024 * 1024,
        )
    assert preflight_mismatch.value.code == "NORMALIZATION_PREFLIGHT_MISMATCH"

    tampered = {**preflight, "pages": [{**preflight["pages"][0], "width_mpt": 1}]}
    with pytest.raises(NormalizationError) as geometry:
        _normalize(source, tampered)
    assert geometry.value.code == "NORMALIZATION_GEOMETRY_MISMATCH"

    with pytest.raises(NormalizationError) as oversized:
        _normalize(source, preflight, maximum=1)
    assert oversized.value.code == "NORMALIZATION_OUTPUT_LIMIT_EXCEEDED"
