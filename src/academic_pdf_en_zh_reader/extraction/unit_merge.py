# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Merge source blocks only across deterministic column and page breaks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from academic_pdf_en_zh_reader.extraction.unit_mapping import (
    UnitMappingError,
    fragment_for_block,
    joined_source_text,
    ordered_source_blocks,
    validate_unit_mapping,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)

_EDGE_TOLERANCE_MPT = 20_000
_INDENT_TOLERANCE_MPT = 10_000
_LIST_START = re.compile(
    r"(?:[-*\u2022\u25aa\u25e6\u2013\u2014]|\(?\d+[.)]|\(?[A-Za-z][.)])\s+"
)
_QUOTE_START = frozenset({'"', "'", "\u2018", "\u201c"})
_SENTENCE_END = re.compile(r"[.!?][\"'\u2019\u201d)\]]*\Z")
_COORDINATING_CONTINUATION = re.compile(r"\b(?:and|or|nor)\s*\Z", re.IGNORECASE)


class UnitMergeError(ValueError):
    """Raised when a source or completed mapping violates the unit contract."""


class UnitMergeStatus(StrEnum):
    """The only outcomes for deterministic semantic-unit construction."""

    OK = "OK"
    NEEDS_UNIT_REVIEW = "NEEDS_UNIT_REVIEW"


@dataclass(frozen=True)
class UnitMergeIssue:
    """One adjacent source boundary that lacks deterministic evidence."""

    left_block_id: str
    right_block_id: str
    reason: str


@dataclass(frozen=True)
class UnitMergeOutcome:
    """A complete units artifact or an explicit fail-closed review stop."""

    status: UnitMergeStatus
    artifact: dict[str, object] | None
    issues: tuple[UnitMergeIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.status is UnitMergeStatus.OK:
            if self.artifact is None or self.issues:
                raise UnitMergeError("OK unit outcome must contain only an artifact")
        elif self.artifact is not None or not self.issues:
            raise UnitMergeError(
                "NEEDS_UNIT_REVIEW must contain issues and no partial artifact"
            )


@dataclass(frozen=True)
class _LocatedBlock:
    page_number: int
    block: dict[str, Any]
    band: dict[str, Any]
    column_index: int
    column: dict[str, Any]


def _located_blocks(source: Mapping[str, object]) -> tuple[_LocatedBlock, ...]:
    locations: dict[str, _LocatedBlock] = {}
    for page in source["pages"]:  # type: ignore[index]
        bands = {band["id"]: band for band in page["bands"]}
        for block in page["blocks"]:
            band = bands[block["band_id"]]
            column_index = next(
                index
                for index, column in enumerate(band["columns"])
                if column["id"] == block["column_id"]
            )
            locations[block["id"]] = _LocatedBlock(
                page_number=page["page_number"],
                block=block,
                band=band,
                column_index=column_index,
                column=band["columns"][column_index],
            )
    return tuple(
        locations[block["id"]]
        for _page, block in ordered_source_blocks(source)
        if block["translation_policy"] == "required"
    )


def _at_bottom(block: _LocatedBlock) -> bool:
    return (
        block.block["bbox_mpt"][1] - block.band["y_bottom_mpt"] <= _EDGE_TOLERANCE_MPT
    )


def _at_top(block: _LocatedBlock) -> bool:
    return block.band["y_top_mpt"] - block.block["bbox_mpt"][3] <= _EDGE_TOLERANCE_MPT


def _is_break_transition(left: _LocatedBlock, right: _LocatedBlock) -> bool:
    if not (_at_bottom(left) and _at_top(right)):
        return False
    if left.page_number == right.page_number:
        return bool(
            left.block["band_id"] == right.block["band_id"]
            and right.column_index == left.column_index + 1
        )
    return bool(
        right.page_number == left.page_number + 1
        and left.column_index == len(left.band["columns"]) - 1
        and right.column_index == 0
    )


def _is_forced_hyphenated_column_continuation(
    left: _LocatedBlock,
    right: _LocatedBlock,
    left_text: str,
    right_text: str,
) -> bool:
    return bool(
        left.page_number == right.page_number
        and left.block["band_id"] == right.block["band_id"]
        and right.column_index == left.column_index + 1
        and _at_top(right)
        and left_text.endswith("-")
        and right_text
        and right_text[0].islower()
    )


def _is_indented(block: _LocatedBlock) -> bool:
    return (
        block.block["first_line_bbox_mpt"][0] - block.column["x_left_mpt"]
        > _INDENT_TOLERANCE_MPT
    )


def _boundary_decision(left: _LocatedBlock, right: _LocatedBlock) -> str:
    if left.block["role"] != "body" or right.block["role"] != "body":
        return "separate"
    left_text = left.block["text"].rstrip()
    right_text = right.block["text"].lstrip()
    if not (
        _is_break_transition(left, right)
        or _is_forced_hyphenated_column_continuation(
            left,
            right,
            left_text,
            right_text,
        )
    ):
        return "separate"

    if not left_text or not right_text:
        return "review"
    if (
        _SENTENCE_END.search(left_text)
        or _is_indented(right)
        or _LIST_START.match(right_text)
        or right_text[0] in _QUOTE_START
    ):
        return "separate"
    if left_text.endswith("-"):
        return "merge" if right_text[0].islower() else "review"
    if right_text[0].islower() or _COORDINATING_CONTINUATION.search(left_text):
        return "merge"
    return "review"


def _new_unit(block: _LocatedBlock) -> dict[str, object]:
    source = block.block
    return {
        "id": source["id"],
        "role": source["role"],
        "reading_order": source["reading_order"],
        "source_text": source["text"],
        "confidence_ppm": source["confidence_ppm"],
        "fragments": [fragment_for_block(block.page_number, source)],
    }


def _append_fragment(unit: dict[str, object], block: _LocatedBlock) -> None:
    unit["source_text"] = joined_source_text(
        (
            {"text": unit["source_text"]},
            {"text": block.block["text"]},
        )
    )
    unit["confidence_ppm"] = min(
        int(unit["confidence_ppm"]),
        block.block["confidence_ppm"],
    )
    fragments = unit["fragments"]
    assert isinstance(fragments, list)
    fragments.append(fragment_for_block(block.page_number, block.block))


def build_semantic_units(source: Mapping[str, object]) -> UnitMergeOutcome:
    """Build complete units or stop when an eligible break is ambiguous."""

    try:
        validate_artifact("source", source)
        blocks = _located_blocks(source)
    except (SchemaValidationError, UnitMappingError, KeyError, StopIteration) as exc:
        raise UnitMergeError("source is not a valid unit-merge input") from exc

    units: list[dict[str, object]] = []
    issues: list[UnitMergeIssue] = []
    previous: _LocatedBlock | None = None
    for block in blocks:
        decision = (
            "separate" if previous is None else _boundary_decision(previous, block)
        )
        if decision == "merge":
            _append_fragment(units[-1], block)
        else:
            units.append(_new_unit(block))
            if decision == "review" and previous is not None:
                issues.append(
                    UnitMergeIssue(
                        left_block_id=previous.block["id"],
                        right_block_id=block.block["id"],
                        reason="ambiguous continuation at a column or page boundary",
                    )
                )
        previous = block

    if issues:
        return UnitMergeOutcome(
            status=UnitMergeStatus.NEEDS_UNIT_REVIEW,
            artifact=None,
            issues=tuple(issues),
        )

    artifact: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": source["source_sha256"],
        "normalized_pdf_sha256": source["normalized_pdf_sha256"],
        "units": units,
    }
    try:
        validate_artifact("units", artifact)
        validate_unit_mapping(source, artifact)
    except (SchemaValidationError, UnitMappingError) as exc:
        raise UnitMergeError("completed units violate their mapping contract") from exc
    return UnitMergeOutcome(status=UnitMergeStatus.OK, artifact=artifact)
