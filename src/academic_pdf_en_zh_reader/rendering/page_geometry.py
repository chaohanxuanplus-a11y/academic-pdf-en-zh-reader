# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed ISO page geometry for vector PDF composition."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pypdf import PageObject

A4_WIDTH_MPT = 595_276
A4_HEIGHT_MPT = 841_890
A3_LANDSCAPE_WIDTH_MPT = 1_190_551
A3_LANDSCAPE_HEIGHT_MPT = 841_890
PAGE_BOX_TOLERANCE_MPT = 1_000


class PageGeometryError(ValueError):
    """Raised when a page cannot be placed at 1:1 scale in the A4 panel."""


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """Validated page boxes expressed only as integer milli-points."""

    media_box_mpt: tuple[int, int, int, int]
    visible_box_mpt: tuple[int, int, int, int]
    rotation_degrees: int
    displayed_width_mpt: int
    displayed_height_mpt: int


def displayed_crop_relative_boxes(
    geometry: PageGeometry,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Return the extraction contract's displayed, CropBox-relative page boxes."""

    def displayed_axis_box(
        box: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        if geometry.rotation_degrees in {90, 270}:
            return box[1], box[0], box[3], box[2]
        return box

    displayed_media = displayed_axis_box(geometry.media_box_mpt)
    displayed_crop = displayed_axis_box(geometry.visible_box_mpt)
    media_height = displayed_media[3] - displayed_media[1]

    def top_box(
        box: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        return (
            box[0],
            media_height - box[3],
            box[2],
            media_height - box[1],
        )

    media_top = top_box(displayed_media)
    crop_top = top_box(displayed_crop)
    relative_media = (
        media_top[0] - crop_top[0],
        crop_top[3] - media_top[3],
        media_top[2] - crop_top[0],
        crop_top[3] - media_top[1],
    )
    relative_crop = (
        0,
        0,
        crop_top[2] - crop_top[0],
        crop_top[3] - crop_top[1],
    )
    return relative_media, relative_crop


def _to_decimal(value: Any, *, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PageGeometryError(f"{field} must contain finite numbers") from exc
    if not number.is_finite():
        raise PageGeometryError(f"{field} must contain finite numbers")
    return number


def _to_millipoints(value: Any, *, field: str) -> int:
    points = _to_decimal(value, field=field)
    return int((points * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _read_box(page: PageObject, key: str) -> tuple[int, int, int, int]:
    raw = page.get(key)
    if raw is None:
        raise PageGeometryError(f"{key[1:]} is missing")
    try:
        box = raw.get_object()
        if isinstance(box, (str, bytes)) or len(box) != 4:
            raise TypeError
        values = tuple(_to_millipoints(value, field=key[1:]) for value in box)
    except PageGeometryError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PageGeometryError(f"{key[1:]} must be a four-number rectangle") from exc

    left, bottom, right, top = values
    if right <= left or top <= bottom:
        raise PageGeometryError(f"{key[1:]} must have positive width and height")
    return values


def _read_rotation(page: PageObject) -> int:
    raw_rotation = page.get("/Rotate", 0)
    rotation = _to_decimal(raw_rotation, field="Rotate")
    if rotation != rotation.to_integral_value():
        raise PageGeometryError("Rotate must be an integer multiple of 90 degrees")
    normalized = int(rotation) % 360
    if normalized not in {0, 90, 180, 270}:
        raise PageGeometryError("Rotate must be an integer multiple of 90 degrees")
    return normalized


def _validate_user_unit(page: PageObject) -> None:
    user_unit = _to_decimal(page.get("/UserUnit", 1), field="UserUnit")
    if user_unit != 1:
        raise PageGeometryError("UserUnit must be exactly 1 for 1:1 composition")


def inspect_a4_page_geometry(page: PageObject) -> PageGeometry:
    """Validate and describe one visible A4 portrait page.

    The visible page is the CropBox when present and otherwise the MediaBox.
    Orthogonal page rotation is interpreted, but no scaling is authorized.
    """

    _validate_user_unit(page)
    media_box = _read_box(page, "/MediaBox")
    visible_box = (
        _read_box(page, "/CropBox") if page.get("/CropBox") is not None else media_box
    )

    media_left, media_bottom, media_right, media_top = media_box
    crop_left, crop_bottom, crop_right, crop_top = visible_box
    if (
        crop_left < media_left
        or crop_bottom < media_bottom
        or crop_right > media_right
        or crop_top > media_top
    ):
        raise PageGeometryError("CropBox must be contained within MediaBox")

    rotation = _read_rotation(page)
    raw_width = crop_right - crop_left
    raw_height = crop_top - crop_bottom
    if rotation in {90, 270}:
        displayed_width, displayed_height = raw_height, raw_width
    else:
        displayed_width, displayed_height = raw_width, raw_height

    if (
        abs(displayed_width - A4_WIDTH_MPT) > PAGE_BOX_TOLERANCE_MPT
        or abs(displayed_height - A4_HEIGHT_MPT) > PAGE_BOX_TOLERANCE_MPT
    ):
        raise PageGeometryError(
            "visible page is not A4 portrait within the 1-point tolerance: "
            f"{displayed_width} x {displayed_height} mpt"
        )

    return PageGeometry(
        media_box_mpt=media_box,
        visible_box_mpt=visible_box,
        rotation_degrees=rotation,
        displayed_width_mpt=displayed_width,
        displayed_height_mpt=displayed_height,
    )
