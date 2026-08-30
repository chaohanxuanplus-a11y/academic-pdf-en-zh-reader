# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Stable reading order and its minimal directed acyclic graph."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class ReadingOrderError(ValueError):
    """Raised when a block cannot participate in a valid reading order."""


class ReadingOrderBlock(Protocol):
    """The structural fields required to order one block."""

    id: str
    page_number: int
    band_index: int
    column_index: int
    bbox_mpt: tuple[int, int, int, int]
    source_ordinal: int


@dataclass(frozen=True, slots=True)
class ReadingOrderEdge:
    """One immediate precedence relation in the reading-order DAG."""

    before_id: str
    after_id: str


@dataclass(frozen=True, slots=True)
class ReadingOrderResult[BlockT: ReadingOrderBlock]:
    """The stable total order and its minimal chain of DAG edges."""

    ordered_blocks: tuple[BlockT, ...]
    edges: tuple[ReadingOrderEdge, ...]


def _index(value: object, *, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ReadingOrderError(f"{name} must be an integer at least {minimum}")
    return value


def _box(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ReadingOrderError("bbox_mpt must contain four integer coordinates")
    if any(type(coordinate) is not int for coordinate in value):
        raise ReadingOrderError("bbox_mpt must contain four integer coordinates")
    x0, y0, x1, y1 = value
    if x0 >= x1 or y0 >= y1:
        raise ReadingOrderError("bbox_mpt must have positive width and height")
    return x0, y0, x1, y1


def _sort_key(block: ReadingOrderBlock) -> tuple[object, ...]:
    identifier = block.id
    if not isinstance(identifier, str) or not identifier:
        raise ReadingOrderError("block id must be a non-empty string")
    page = _index(block.page_number, name="page_number", minimum=1)
    band = _index(block.band_index, name="band_index", minimum=0)
    column = _index(block.column_index, name="column_index", minimum=0)
    ordinal = _index(block.source_ordinal, name="source_ordinal", minimum=0)
    x0, y0, x1, y1 = _box(block.bbox_mpt)
    return page, band, column, -y1, -y0, x0, x1, ordinal, identifier


def build_reading_order[BlockT: ReadingOrderBlock](
    blocks: Iterable[BlockT],
) -> ReadingOrderResult[BlockT]:
    """Order blocks and connect each item to its immediate successor.

    The resulting chain is the smallest edge set that preserves the complete
    deterministic order, and a chain cannot contain a directed cycle.
    """

    keyed: list[tuple[tuple[object, ...], BlockT]] = []
    seen: set[str] = set()
    for block in blocks:
        key = _sort_key(block)
        identifier = block.id
        if identifier in seen:
            raise ReadingOrderError("block identifiers must be unique")
        seen.add(identifier)
        keyed.append((key, block))

    ordered = tuple(block for _key, block in sorted(keyed, key=lambda item: item[0]))
    identifiers = tuple(block.id for block in ordered)
    edges = tuple(
        ReadingOrderEdge(before_id, after_id)
        for before_id, after_id in zip(identifiers, identifiers[1:], strict=False)
    )
    return ReadingOrderResult(ordered_blocks=ordered, edges=edges)
