# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Normalize visible PDF pages to A4 without cropping, stretching, or rasterizing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from typing import Any

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import NameObject

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes

A4_WIDTH_MPT = 595_276
A4_HEIGHT_MPT = 841_890
NORMALIZATION_POLICY_VERSION = "1.0.0"
_SCALE_ONE_PPM = 1_000_000
_MAX_SOURCE_DIMENSION_MPT = 2_147_483_647
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ACTIVE_CATALOG_KEYS = frozenset(
    {
        "/AA",
        "/AcroForm",
        "/Collection",
        "/Names",
        "/OpenAction",
        "/Perms",
    }
)
_ACTIVE_PAGE_KEYS = ("/AA", "/Annots")


class NormalizationError(ValueError):
    """A stable, content-free normalization failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NormalizationPlan:
    """One integer-only displayed-CropBox placement inside A4."""

    displayed_width_mpt: int
    displayed_height_mpt: int
    scale_ppm: int
    scaled_width_mpt: int
    scaled_height_mpt: int
    padding_left_mpt: int
    padding_bottom_mpt: int
    padding_right_mpt: int
    padding_top_mpt: int

    @property
    def normalized_content_box_mpt(self) -> tuple[int, int, int, int]:
        return (
            self.padding_left_mpt,
            self.padding_bottom_mpt,
            self.padding_left_mpt + self.scaled_width_mpt,
            self.padding_bottom_mpt + self.scaled_height_mpt,
        )


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """The bounded normalized PDF bytes and their raw-source-bound receipt."""

    pdf_bytes: bytes
    artifact: dict[str, object]


def _positive_dimension(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_SOURCE_DIMENSION_MPT
    ):
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    return value


def _scaled_dimension(dimension_mpt: int, scale_ppm: int) -> int:
    return (dimension_mpt * scale_ppm + _SCALE_ONE_PPM // 2) // _SCALE_ONE_PPM


def plan_displayed_crop(
    displayed_width_mpt: int,
    displayed_height_mpt: int,
) -> NormalizationPlan:
    """Return the sole legal uniform scale and centered A4 placement.

    A page that already fits is never enlarged. If either displayed CropBox
    axis exceeds A4, one floor-quantized scale is used for both axes. Odd
    integer-millipoint padding is assigned to the right or top.
    """

    width = _positive_dimension(displayed_width_mpt)
    height = _positive_dimension(displayed_height_mpt)
    scale_ppm = min(
        _SCALE_ONE_PPM,
        A4_WIDTH_MPT * _SCALE_ONE_PPM // width,
        A4_HEIGHT_MPT * _SCALE_ONE_PPM // height,
    )
    if scale_ppm < 1:
        raise NormalizationError("NORMALIZATION_SCALE_UNREPRESENTABLE")
    scaled_width = _scaled_dimension(width, scale_ppm)
    scaled_height = _scaled_dimension(height, scale_ppm)
    if (
        scaled_width < 1
        or scaled_height < 1
        or scaled_width > A4_WIDTH_MPT
        or scaled_height > A4_HEIGHT_MPT
    ):
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    horizontal_remainder = A4_WIDTH_MPT - scaled_width
    vertical_remainder = A4_HEIGHT_MPT - scaled_height
    left = horizontal_remainder // 2
    bottom = vertical_remainder // 2
    return NormalizationPlan(
        displayed_width_mpt=width,
        displayed_height_mpt=height,
        scale_ppm=scale_ppm,
        scaled_width_mpt=scaled_width,
        scaled_height_mpt=scaled_height,
        padding_left_mpt=left,
        padding_bottom_mpt=bottom,
        padding_right_mpt=horizontal_remainder - left,
        padding_top_mpt=vertical_remainder - bottom,
    )


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID") from exc
    if not number.is_finite():
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    return number


def _mpt(value: Any) -> int:
    return int((_decimal(value) * 1000).quantize(Decimal("1"), ROUND_HALF_UP))


def _page_box(page: PageObject, key: str) -> tuple[int, int, int, int]:
    raw = page.get(key)
    if raw is None:
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    try:
        box = raw.get_object()
        if isinstance(box, (str, bytes)) or len(box) != 4:
            raise TypeError
        values = tuple(_mpt(item) for item in box)
    except NormalizationError:
        raise
    except Exception as exc:
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID") from exc
    if values[2] <= values[0] or values[3] <= values[1]:
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    return values  # type: ignore[return-value]


def _rotation(page: PageObject) -> int:
    raw = _decimal(page.get("/Rotate", 0))
    if raw != raw.to_integral_value() or int(raw) % 90:
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    return int(raw) % 360


def _raw_geometry(
    page: PageObject,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], int, int, int]:
    if _decimal(page.get("/UserUnit", 1)) != 1:
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    media = _page_box(page, "/MediaBox")
    crop = _page_box(page, "/CropBox") if page.get("/CropBox") is not None else media
    if (
        crop[0] < media[0]
        or crop[1] < media[1]
        or crop[2] > media[2]
        or crop[3] > media[3]
    ):
        raise NormalizationError("NORMALIZATION_GEOMETRY_INVALID")
    rotation = _rotation(page)
    raw_width = crop[2] - crop[0]
    raw_height = crop[3] - crop[1]
    width, height = (
        (raw_height, raw_width) if rotation in {90, 270} else (raw_width, raw_height)
    )
    return media, crop, rotation, width, height


def _integer_box(value: object) -> tuple[int, int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise NormalizationError("NORMALIZATION_PREFLIGHT_INVALID")
    result = tuple(value)
    if result[2] <= result[0] or result[3] <= result[1]:
        raise NormalizationError("NORMALIZATION_PREFLIGHT_INVALID")
    return result  # type: ignore[return-value]


def _validated_preflight_pages(
    preflight: Mapping[str, object],
    source_bytes: bytes,
    preflight_sha256: str,
) -> tuple[str, list[Mapping[str, object]]]:
    if not isinstance(preflight_sha256, str) or not _SHA256.fullmatch(preflight_sha256):
        raise NormalizationError("NORMALIZATION_PREFLIGHT_INVALID")
    try:
        observed_preflight_sha256 = sha256(canonical_json_bytes(preflight)).hexdigest()
    except Exception as exc:
        raise NormalizationError("NORMALIZATION_PREFLIGHT_INVALID") from exc
    if observed_preflight_sha256 != preflight_sha256:
        raise NormalizationError("NORMALIZATION_PREFLIGHT_MISMATCH")
    source_sha256 = preflight.get("source_sha256")
    pages = preflight.get("pages")
    if (
        preflight.get("artifact_kind") != "preflight"
        or preflight.get("passed") is not True
        or not isinstance(source_sha256, str)
        or not _SHA256.fullmatch(source_sha256)
        or not isinstance(pages, list)
        or not pages
        or any(not isinstance(page, Mapping) for page in pages)
    ):
        raise NormalizationError("NORMALIZATION_PREFLIGHT_INVALID")
    if sha256(source_bytes).hexdigest() != source_sha256:
        raise NormalizationError("NORMALIZATION_SOURCE_MISMATCH")
    try:
        observed_bytes = preflight["limits"]["file_bytes"]["observed"]  # type: ignore[index]
    except (KeyError, TypeError):
        observed_bytes = None
    if (
        isinstance(observed_bytes, bool)
        or not isinstance(observed_bytes, int)
        or observed_bytes != len(source_bytes)
    ):
        raise NormalizationError("NORMALIZATION_PREFLIGHT_INVALID")
    return source_sha256, pages  # type: ignore[return-value]


def _validate_preflight_page(
    record: Mapping[str, object],
    *,
    page_number: int,
    media: tuple[int, int, int, int],
    crop: tuple[int, int, int, int],
    rotation: int,
    displayed_width: int,
    displayed_height: int,
) -> None:
    try:
        valid = (
            record.get("page_number") == page_number
            and _integer_box(record.get("media_box_mpt")) == media
            and _integer_box(record.get("crop_box_mpt")) == crop
            and record.get("rotation_degrees") == rotation
            and record.get("width_mpt") == displayed_width
            and record.get("height_mpt") == displayed_height
        )
    except NormalizationError as exc:
        raise NormalizationError("NORMALIZATION_GEOMETRY_MISMATCH") from exc
    if not valid:
        raise NormalizationError("NORMALIZATION_GEOMETRY_MISMATCH")


def _prepare_source_page(
    staging_writer: PdfWriter,
    source_page: PageObject,
    plan: NormalizationPlan,
) -> tuple[PageObject, Transformation]:
    staged = staging_writer.add_page(
        source_page,
        excluded_keys=("/AA", "/Annots"),
    )
    for key in _ACTIVE_PAGE_KEYS:
        staged.pop(NameObject(key), None)
    if _rotation(staged):
        staged.transfer_rotation_to_content()
    staged.pop(NameObject("/Rotate"), None)
    visible = (
        _page_box(staged, "/CropBox")
        if staged.get("/CropBox") is not None
        else _page_box(staged, "/MediaBox")
    )
    width = visible[2] - visible[0]
    height = visible[3] - visible[1]
    if (width, height) != (
        plan.displayed_width_mpt,
        plan.displayed_height_mpt,
    ):
        raise NormalizationError("NORMALIZATION_GEOMETRY_MISMATCH")
    scale = plan.scale_ppm / _SCALE_ONE_PPM
    transform = (
        Transformation()
        .translate(-visible[0] / 1000, -visible[1] / 1000)
        .scale(scale, scale)
        .translate(plan.padding_left_mpt / 1000, plan.padding_bottom_mpt / 1000)
    )
    return staged, transform


def _normalize_pages(
    reader: PdfReader,
    preflight_pages: list[Mapping[str, object]],
) -> tuple[bytes, list[dict[str, object]]]:
    if (
        reader.is_encrypted
        or not reader.pages
        or len(reader.pages) != len(preflight_pages)
    ):
        raise NormalizationError("NORMALIZATION_PDF_INVALID")
    staging_writer = PdfWriter()
    prepared: list[tuple[PageObject, Transformation]] = []
    page_artifacts: list[dict[str, object]] = []
    for page_number, (source_page, preflight_page) in enumerate(
        zip(reader.pages, preflight_pages, strict=True),
        start=1,
    ):
        media, crop, rotation, width, height = _raw_geometry(source_page)
        _validate_preflight_page(
            preflight_page,
            page_number=page_number,
            media=media,
            crop=crop,
            rotation=rotation,
            displayed_width=width,
            displayed_height=height,
        )
        plan = plan_displayed_crop(width, height)
        prepared.append(_prepare_source_page(staging_writer, source_page, plan))
        page_artifacts.append(
            {
                "page_number": page_number,
                "source_media_box_mpt": list(media),
                "source_crop_box_mpt": list(crop),
                "source_rotation_degrees": rotation,
                "displayed_width_mpt": width,
                "displayed_height_mpt": height,
                "scale_ppm": plan.scale_ppm,
                "scaled_width_mpt": plan.scaled_width_mpt,
                "scaled_height_mpt": plan.scaled_height_mpt,
                "padding_left_mpt": plan.padding_left_mpt,
                "padding_bottom_mpt": plan.padding_bottom_mpt,
                "padding_right_mpt": plan.padding_right_mpt,
                "padding_top_mpt": plan.padding_top_mpt,
                "normalized_content_box_mpt": list(plan.normalized_content_box_mpt),
            }
        )

    writer = PdfWriter()
    for source_page, transform in prepared:
        destination = writer.add_blank_page(
            width=A4_WIDTH_MPT / 1000,
            height=A4_HEIGHT_MPT / 1000,
        )
        destination.merge_transformed_page(source_page, transform, expand=False)
        for key in _ACTIVE_PAGE_KEYS:
            destination.pop(NameObject(key), None)
    for key in _ACTIVE_CATALOG_KEYS:
        writer.root_object.pop(NameObject(key), None)
    writer.add_metadata(
        {
            "/Producer": "academic-pdf-en-zh-reader",
            "/Creator": "academic-pdf-en-zh-reader normalization/1",
        }
    )
    output = BytesIO()
    writer.write(output)
    return output.getvalue(), page_artifacts


def _validate_normalized_pdf(encoded: bytes, expected_pages: int) -> None:
    try:
        reader = PdfReader(BytesIO(encoded), strict=True)
        if reader.is_encrypted or len(reader.pages) != expected_pages:
            raise NormalizationError("NORMALIZATION_OUTPUT_INVALID")
        if _ACTIVE_CATALOG_KEYS.intersection(reader.root_object):
            raise NormalizationError("NORMALIZATION_OUTPUT_INVALID")
        for page in reader.pages:
            media = _page_box(page, "/MediaBox")
            crop = (
                _page_box(page, "/CropBox")
                if page.get("/CropBox") is not None
                else media
            )
            if (
                media != (0, 0, A4_WIDTH_MPT, A4_HEIGHT_MPT)
                or crop != media
                or _rotation(page) != 0
                or _decimal(page.get("/UserUnit", 1)) != 1
                or any(key in page for key in _ACTIVE_PAGE_KEYS)
            ):
                raise NormalizationError("NORMALIZATION_OUTPUT_INVALID")
    except NormalizationError:
        raise
    except Exception as exc:
        raise NormalizationError("NORMALIZATION_OUTPUT_INVALID") from exc


def normalize_pdf_bytes(
    source_bytes: bytes,
    *,
    preflight: Mapping[str, object],
    preflight_sha256: str,
    max_output_bytes: int,
) -> NormalizationResult:
    """Return a fresh inactive A4 PDF bound to the original uploaded bytes."""

    if type(source_bytes) is not bytes or not source_bytes:
        raise NormalizationError("NORMALIZATION_INPUT_INVALID")
    if (
        isinstance(max_output_bytes, bool)
        or not isinstance(max_output_bytes, int)
        or max_output_bytes <= 0
    ):
        raise NormalizationError("NORMALIZATION_OUTPUT_LIMIT_INVALID")
    source_sha256, preflight_pages = _validated_preflight_pages(
        preflight,
        source_bytes,
        preflight_sha256,
    )
    try:
        reader = PdfReader(BytesIO(source_bytes), strict=True)
    except Exception as exc:
        raise NormalizationError("NORMALIZATION_PDF_INVALID") from exc
    encoded, pages = _normalize_pages(reader, preflight_pages)
    if len(encoded) > max_output_bytes:
        raise NormalizationError("NORMALIZATION_OUTPUT_LIMIT_EXCEEDED")
    _validate_normalized_pdf(encoded, len(preflight_pages))
    normalized_sha256 = sha256(encoded).hexdigest()
    artifact: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "source_sha256": source_sha256,
        "preflight_sha256": preflight_sha256,
        "normalized_pdf_sha256": normalized_sha256,
        "normalized_pdf_bytes": len(encoded),
        "pages": pages,
    }
    return NormalizationResult(pdf_bytes=encoded, artifact=artifact)


__all__ = [
    "A4_HEIGHT_MPT",
    "A4_WIDTH_MPT",
    "NORMALIZATION_POLICY_VERSION",
    "NormalizationError",
    "NormalizationPlan",
    "NormalizationResult",
    "normalize_pdf_bytes",
    "plan_displayed_crop",
]
