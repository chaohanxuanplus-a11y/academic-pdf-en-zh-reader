# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Audit the exact source-block coverage of merged semantic units."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


class UnitMappingError(ValueError):
    """Raised when unit fragments do not exactly cover translatable blocks."""


def ordered_source_blocks(
    source: Mapping[str, object],
) -> tuple[tuple[int, dict[str, Any]], ...]:
    """Return every source block in its globally declared reading order."""

    records: list[tuple[int, dict[str, Any]]] = []
    pages = source.get("pages")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
        raise UnitMappingError("source pages must be a sequence")
    for page in pages:
        if not isinstance(page, Mapping) or type(page.get("page_number")) is not int:
            raise UnitMappingError("source page is invalid")
        blocks = page.get("blocks")
        if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
            raise UnitMappingError("source blocks must be a sequence")
        for block in blocks:
            if not isinstance(block, dict):
                raise UnitMappingError("source block is invalid")
            records.append((page["page_number"], block))

    records.sort(key=lambda record: record[1]["reading_order"])
    orders = [block["reading_order"] for _page, block in records]
    if len(orders) != len(set(orders)):
        raise UnitMappingError("source reading_order values must be globally unique")
    return tuple(records)


def fragment_for_block(
    page_number: int,
    block: Mapping[str, object],
) -> dict[str, object]:
    """Build the only valid complete fragment mapping for one source block."""

    return {
        "page_number": page_number,
        "block_id": block["id"],
        "source_char_start": block["source_char_start"],
        "source_char_end": block["source_char_end"],
    }


def joined_source_text(blocks: Sequence[Mapping[str, object]]) -> str:
    """Join complete source blocks without preserving PDF-native breaks."""

    text = ""
    for block in blocks:
        next_text = str(block["text"]).lstrip()
        if not text:
            text = next_text
        elif text.rstrip().endswith("-"):
            text = text.rstrip()[:-1] + next_text
        else:
            text = f"{text.rstrip()} {next_text}"
    return text


def validate_unit_mapping(
    source: Mapping[str, object],
    artifact: Mapping[str, object],
) -> None:
    """Require every translatable block once and every excluded block zero times."""

    records = ordered_source_blocks(source)
    source_by_id = {block["id"]: (page, block) for page, block in records}
    required = {
        block["id"]
        for _page, block in records
        if block["translation_policy"] == "required"
    }
    excluded = set(source_by_id) - required
    counts: Counter[str] = Counter()
    previous_order = -1

    units = artifact.get("units")
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise UnitMappingError("units must be a sequence")
    for unit in units:
        if not isinstance(unit, Mapping):
            raise UnitMappingError("unit is invalid")
        fragments = unit.get("fragments")
        if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
            raise UnitMappingError("unit fragments must be a sequence")
        if not fragments:
            raise UnitMappingError("unit fragments must not be empty")
        mapped_blocks: list[dict[str, Any]] = []
        for fragment in fragments:
            if not isinstance(fragment, Mapping):
                raise UnitMappingError("unit fragment is invalid")
            block_id = fragment.get("block_id")
            if block_id in excluded:
                raise UnitMappingError("excluded source block must not be mapped")
            record = source_by_id.get(block_id)
            if record is None:
                raise UnitMappingError(
                    "unit fragment references an unknown source block"
                )
            page_number, block = record
            if dict(fragment) != fragment_for_block(page_number, block):
                raise UnitMappingError(
                    "unit fragment does not map the complete source block"
                )
            if unit.get("role") != block["role"]:
                raise UnitMappingError("unit role disagrees with its source block")
            mapped_blocks.append(block)
            counts[block_id] += 1
            if counts[block_id] > 1:
                raise UnitMappingError("required source block is mapped more than once")
            reading_order = block["reading_order"]
            if reading_order <= previous_order:
                raise UnitMappingError("unit fragments are not in source reading order")
            previous_order = reading_order

        first = mapped_blocks[0]
        if unit.get("id") != first["id"]:
            raise UnitMappingError("unit id must equal its first source block id")
        if unit.get("reading_order") != first["reading_order"]:
            raise UnitMappingError(
                "unit reading order must equal its first source block reading order"
            )
        if unit.get("source_text") != joined_source_text(mapped_blocks):
            raise UnitMappingError("unit source text differs from its source blocks")
        if unit.get("confidence_ppm") != min(
            block["confidence_ppm"] for block in mapped_blocks
        ):
            raise UnitMappingError("unit confidence differs from its source blocks")

    missing = required - set(counts)
    if missing:
        raise UnitMappingError("missing required source block")
