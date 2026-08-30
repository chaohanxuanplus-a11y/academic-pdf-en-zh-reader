# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject

from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
    PAGE_BOX_TOLERANCE_MPT,
    PageGeometryError,
    inspect_a4_page_geometry,
)


def _blank_page(width_mpt: int, height_mpt: int):
    writer = PdfWriter()
    return writer.add_blank_page(width=width_mpt / 1000, height=height_mpt / 1000)


def test_iso_geometry_is_integer_millipoints() -> None:
    assert A4_WIDTH_MPT == 595_276
    assert A4_HEIGHT_MPT == 841_890
    assert A3_LANDSCAPE_WIDTH_MPT == 1_190_551
    assert A3_LANDSCAPE_HEIGHT_MPT == 841_890
    assert PAGE_BOX_TOLERANCE_MPT == 1_000

    page = _blank_page(A4_WIDTH_MPT, A4_HEIGHT_MPT)
    geometry = inspect_a4_page_geometry(page)

    assert geometry.media_box_mpt == (0, 0, A4_WIDTH_MPT, A4_HEIGHT_MPT)
    assert geometry.visible_box_mpt == (0, 0, A4_WIDTH_MPT, A4_HEIGHT_MPT)
    assert geometry.rotation_degrees == 0
    assert geometry.displayed_width_mpt == A4_WIDTH_MPT
    assert geometry.displayed_height_mpt == A4_HEIGHT_MPT


def test_tolerated_cropbox_and_orthogonal_rotation_are_interpreted() -> None:
    cropped = _blank_page(A4_WIDTH_MPT, A4_HEIGHT_MPT)
    cropped.cropbox = ArrayObject(
        [
            FloatObject(0.25),
            FloatObject(0.25),
            FloatObject(A4_WIDTH_MPT / 1000 - 0.25),
            FloatObject(A4_HEIGHT_MPT / 1000 - 0.25),
        ]
    )
    cropped_geometry = inspect_a4_page_geometry(cropped)
    assert cropped_geometry.visible_box_mpt == (
        250,
        250,
        A4_WIDTH_MPT - 250,
        A4_HEIGHT_MPT - 250,
    )
    assert cropped_geometry.displayed_width_mpt == A4_WIDTH_MPT - 500
    assert cropped_geometry.displayed_height_mpt == A4_HEIGHT_MPT - 500

    rotated = _blank_page(A4_HEIGHT_MPT, A4_WIDTH_MPT)
    rotated[NameObject("/Rotate")] = NumberObject(90)
    rotated_geometry = inspect_a4_page_geometry(rotated)
    assert rotated_geometry.rotation_degrees == 90
    assert rotated_geometry.displayed_width_mpt == A4_WIDTH_MPT
    assert rotated_geometry.displayed_height_mpt == A4_HEIGHT_MPT


@pytest.mark.parametrize(
    ("width_mpt", "height_mpt"),
    [
        (612_000, 792_000),
        (A4_WIDTH_MPT + PAGE_BOX_TOLERANCE_MPT + 1, A4_HEIGHT_MPT),
        (A4_WIDTH_MPT, A4_HEIGHT_MPT - PAGE_BOX_TOLERANCE_MPT - 1),
    ],
)
def test_significantly_non_a4_pages_fail_closed(
    width_mpt: int, height_mpt: int
) -> None:
    page = _blank_page(width_mpt, height_mpt)

    with pytest.raises(PageGeometryError, match="A4"):
        inspect_a4_page_geometry(page)


def test_uninterpretable_page_boxes_rotation_and_user_unit_fail_closed() -> None:
    zero_width = _blank_page(A4_WIDTH_MPT, A4_HEIGHT_MPT)
    zero_width[NameObject("/CropBox")] = ArrayObject(
        [FloatObject(20), FloatObject(0), FloatObject(20), FloatObject(100)]
    )
    with pytest.raises(PageGeometryError, match="CropBox"):
        inspect_a4_page_geometry(zero_width)

    outside_media = _blank_page(A4_WIDTH_MPT, A4_HEIGHT_MPT)
    outside_media[NameObject("/CropBox")] = ArrayObject(
        [
            FloatObject(-1),
            FloatObject(0),
            FloatObject(A4_WIDTH_MPT / 1000),
            FloatObject(A4_HEIGHT_MPT / 1000),
        ]
    )
    with pytest.raises(PageGeometryError, match="CropBox"):
        inspect_a4_page_geometry(outside_media)

    diagonal_rotation = _blank_page(A4_WIDTH_MPT, A4_HEIGHT_MPT)
    diagonal_rotation[NameObject("/Rotate")] = NumberObject(45)
    with pytest.raises(PageGeometryError, match="Rotate"):
        inspect_a4_page_geometry(diagonal_rotation)

    non_default_user_unit = _blank_page(A4_WIDTH_MPT, A4_HEIGHT_MPT)
    non_default_user_unit[NameObject("/UserUnit")] = FloatObject(2)
    with pytest.raises(PageGeometryError, match="UserUnit"):
        inspect_a4_page_geometry(non_default_user_unit)
