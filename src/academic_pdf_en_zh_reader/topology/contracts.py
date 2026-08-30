# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Cross-reference and fail-closed outcome contracts for page topology."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

TRANSLATABLE_ROLES = frozenset(
    {
        "title",
        "abstract",
        "keywords",
        "heading",
        "body",
        "figure-caption",
        "table-caption",
    }
)
EXCLUDED_ROLES = frozenset(
    {
        "author",
        "affiliation",
        "bibliographic-metadata",
        "header",
        "footer",
        "page-number",
        "watermark",
        "footnote",
        "endnote",
        "acknowledgements",
        "equation",
        "variable",
        "code",
        "chemical-formula",
        "pure-data",
        "reference-entry",
        "table-cell",
        "figure-text",
    }
)


class TopologyContractError(ValueError):
    """Raised when topology data contradicts its structural contract."""


class TopologyStatus(StrEnum):
    """Only the two outcomes allowed at the G3 boundary."""

    OK = "OK"
    NEEDS_TOPOLOGY_REVIEW = "NEEDS_TOPOLOGY_REVIEW"


@dataclass(frozen=True)
class TopologyOutcome:
    """A successful source artifact or an explicit fail-closed review stop."""

    status: TopologyStatus
    source: dict[str, object] | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, TopologyStatus):
            raise TopologyContractError("topology outcome status is invalid")
        if self.status is TopologyStatus.OK:
            if self.source is None:
                raise TopologyContractError("OK topology outcome requires source")
            from academic_pdf_en_zh_reader.schema.validate import (
                SchemaValidationError,
                validate_artifact,
            )

            try:
                validate_artifact("source", self.source)
            except SchemaValidationError as exc:
                raise TopologyContractError(
                    "OK topology outcome requires valid source"
                ) from exc
        elif self.source is not None:
            raise TopologyContractError("NEEDS_TOPOLOGY_REVIEW must not carry source")


def _contains(outer: list[int], inner: list[int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _positive(box: list[int]) -> bool:
    return box[0] < box[2] and box[1] < box[3]


def _claim_identifier(identifier: str, seen: set[str]) -> None:
    if identifier in seen:
        raise TopologyContractError("source topology identifiers must be unique")
    seen.add(identifier)


def _validate_bands(page: dict[str, Any], seen: set[str]) -> dict[str, set[str]]:
    crop = page["crop_box_mpt"]
    columns_by_band: dict[str, set[str]] = {}
    previous_bottom: int | None = None
    for band in page["bands"]:
        band_id = band["id"]
        _claim_identifier(band_id, seen)
        top = band["y_top_mpt"]
        bottom = band["y_bottom_mpt"]
        if top <= bottom or bottom < crop[1] or top > crop[3]:
            raise TopologyContractError("band geometry is outside the crop box")
        if previous_bottom is not None and top > previous_bottom:
            raise TopologyContractError("bands must not overlap in top-down order")
        previous_bottom = bottom

        column_ids: set[str] = set()
        previous_right: int | None = None
        for column in band["columns"]:
            column_id = column["id"]
            _claim_identifier(column_id, seen)
            column_ids.add(column_id)
            left = column["x_left_mpt"]
            right = column["x_right_mpt"]
            if left >= right or left < crop[0] or right > crop[2]:
                raise TopologyContractError("column geometry is outside the crop box")
            if previous_right is not None and left < previous_right:
                raise TopologyContractError(
                    "columns must not overlap in left-to-right order"
                )
            previous_right = right
        columns_by_band[band_id] = column_ids
    return columns_by_band


def _validate_graphics(page: dict[str, Any], seen: set[str]) -> dict[str, str]:
    crop = page["crop_box_mpt"]
    kinds: dict[str, str] = {}
    for graphic in page["graphic_nodes"]:
        identifier = graphic["id"]
        _claim_identifier(identifier, seen)
        if not _positive(graphic["bbox_mpt"]) or not _contains(
            crop, graphic["bbox_mpt"]
        ):
            raise TopologyContractError("graphic box is outside the crop box")
        kinds[identifier] = graphic["kind"]
    return kinds


def _validate_blocks(
    page: dict[str, Any],
    columns_by_band: dict[str, set[str]],
    graphic_kinds: dict[str, str],
    seen: set[str],
) -> None:
    crop = page["crop_box_mpt"]
    band_by_id = {band["id"]: band for band in page["bands"]}
    previous_end: int | None = None
    for block in page["blocks"]:
        _claim_identifier(block["id"], seen)
        role = block["role"]
        expected_policy = "required" if role in TRANSLATABLE_ROLES else "excluded"
        if block["translation_policy"] != expected_policy:
            raise TopologyContractError("block role and translation policy disagree")

        band_id = block["band_id"]
        column_id = block["column_id"]
        band = band_by_id.get(band_id)
        if band is None:
            raise TopologyContractError("block references an unknown band")
        if column_id not in columns_by_band[band_id]:
            raise TopologyContractError("block references a column outside its band")
        column = next(item for item in band["columns"] if item["id"] == column_id)
        frame = [
            column["x_left_mpt"],
            band["y_bottom_mpt"],
            column["x_right_mpt"],
            band["y_top_mpt"],
        ]
        if not _positive(block["bbox_mpt"]) or not _contains(crop, block["bbox_mpt"]):
            raise TopologyContractError("block box is outside the page crop geometry")
        if role in TRANSLATABLE_ROLES and not _contains(frame, block["bbox_mpt"]):
            raise TopologyContractError(
                "translatable block is outside its band or column geometry"
            )
        if not _positive(block["first_line_bbox_mpt"]) or not _contains(
            block["bbox_mpt"], block["first_line_bbox_mpt"]
        ):
            raise TopologyContractError(
                "first-line box must be contained by its block box"
            )

        start = block["source_char_start"]
        end = block["source_char_end"]
        if end <= start or (previous_end is not None and start < previous_end):
            raise TopologyContractError(
                "source block character ranges must be positive and monotonic"
            )
        if end - start != len(block["text"]):
            raise TopologyContractError(
                "source block text length must match its character range"
            )
        previous_end = end

        target = block.get("target_graphic_id")
        expected_kind = {
            "figure-caption": "figure",
            "figure-text": "figure",
            "table-caption": "table",
            "table-cell": "table",
        }.get(role)
        if expected_kind is None:
            if target is not None:
                raise TopologyContractError(
                    "target_graphic_id is not allowed for this block role"
                )
        elif target is None or graphic_kinds.get(target) != expected_kind:
            raise TopologyContractError(
                "caption or graphic text block has no matching graphic target"
            )


def validate_source_topology(source: dict[str, Any]) -> None:
    """Validate source relationships JSON Schema cannot express locally."""

    seen: set[str] = set()
    for page in source["pages"]:
        media = page["media_box_mpt"]
        crop = page["crop_box_mpt"]
        if not _contains(media, crop):
            raise TopologyContractError("crop box must be inside the media box")
        columns_by_band = _validate_bands(page, seen)
        graphic_kinds = _validate_graphics(page, seen)
        _validate_blocks(page, columns_by_band, graphic_kinds, seen)
