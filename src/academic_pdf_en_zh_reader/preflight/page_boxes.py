# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed page-box inspection for child-side preflight."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pypdf._page import PageObject

_PAGE_BOX_KEYS = ("/CropBox", "/BleedBox", "/TrimBox", "/ArtBox")


class PageBoxError(ValueError):
    """A stable, user-reportable page geometry rejection."""

    def __init__(self, code: str, details: str) -> None:
        super().__init__(details)
        self.code = code
        self.details = details


@dataclass(frozen=True, slots=True)
class PageBoxFacts:
    """Validated page geometry represented in integer milli-points."""

    width_mpt: int
    height_mpt: int
    media_box_mpt: tuple[int, int, int, int]
    crop_box_mpt: tuple[int, int, int, int]
    rotation_degrees: int


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PageBoxError("INVALID_PAGE_BOX", f"{field} is not numeric") from exc
    if not number.is_finite():
        raise PageBoxError("INVALID_PAGE_BOX", f"{field} is not finite")
    return number


def _mpt(value: Any, *, field: str) -> int:
    return int(
        (_decimal(value, field=field) * 1000).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _box(page: PageObject, key: str) -> tuple[int, int, int, int] | None:
    raw = page.get(key)
    if raw is None:
        return None
    try:
        resolved = raw.get_object()
        if isinstance(resolved, (str, bytes)) or len(resolved) != 4:
            raise TypeError
        values = tuple(_mpt(item, field=key[1:]) for item in resolved)
    except PageBoxError:
        raise
    except Exception as exc:
        raise PageBoxError(
            "INVALID_PAGE_BOX", f"{key[1:]} is not a four-number rectangle"
        ) from exc
    if values[2] <= values[0] or values[3] <= values[1]:
        raise PageBoxError("INVALID_PAGE_BOX", f"{key[1:]} does not have positive area")
    return values  # type: ignore[return-value]


def _contained(
    child: tuple[int, int, int, int], parent: tuple[int, int, int, int]
) -> bool:
    return (
        child[0] >= parent[0]
        and child[1] >= parent[1]
        and child[2] <= parent[2]
        and child[3] <= parent[3]
    )


def _rotation(page: PageObject) -> int:
    raw = _decimal(page.get("/Rotate", 0), field="Rotate")
    if raw != raw.to_integral_value() or int(raw) % 90:
        raise PageBoxError(
            "INVALID_PAGE_ROTATION", "Rotate must be an integer multiple of 90"
        )
    return int(raw) % 360


def inspect_page_boxes(page: PageObject) -> PageBoxFacts:
    """Validate finite boxes, unit scale, rotation, and containment.

    Page size is recorded but is not a rejection condition.  A later isolated
    normalization stage fits the displayed CropBox into a fixed A4 canvas.
    """

    user_unit = _decimal(page.get("/UserUnit", 1), field="UserUnit")
    if user_unit != 1:
        raise PageBoxError(
            "UNSUPPORTED_USER_UNIT", "UserUnit must be exactly 1 for 1:1 output"
        )

    media = _box(page, "/MediaBox")
    if media is None:
        raise PageBoxError("INVALID_PAGE_BOX", "MediaBox is missing")
    resolved_boxes: dict[str, tuple[int, int, int, int]] = {}
    for key in _PAGE_BOX_KEYS:
        value = _box(page, key)
        if value is None:
            continue
        if not _contained(value, media):
            raise PageBoxError(
                "PAGE_BOX_OUTSIDE_MEDIA", f"{key[1:]} is outside MediaBox"
            )
        resolved_boxes[key] = value

    crop = resolved_boxes.get("/CropBox", media)
    rotation = _rotation(page)
    raw_width = crop[2] - crop[0]
    raw_height = crop[3] - crop[1]
    if rotation in {90, 270}:
        width, height = raw_height, raw_width
    else:
        width, height = raw_width, raw_height
    return PageBoxFacts(
        width_mpt=width,
        height_mpt=height,
        media_box_mpt=media,
        crop_box_mpt=crop,
        rotation_degrees=rotation,
    )
