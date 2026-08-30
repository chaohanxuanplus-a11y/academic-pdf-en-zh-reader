# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from academic_pdf_en_zh_reader.topology.reading_order import (
    ReadingOrderError,
    build_reading_order,
)


@dataclass(frozen=True)
class Block:
    id: str
    page_number: int
    band_index: int
    column_index: int
    bbox_mpt: tuple[int, int, int, int]
    source_ordinal: int


def _block(
    identifier: str,
    *,
    page: int = 1,
    band: int = 0,
    column: int = 0,
    box: tuple[int, int, int, int] = (10_000, 700_000, 90_000, 710_000),
    ordinal: int = 0,
) -> Block:
    return Block(identifier, page, band, column, box, ordinal)


def _ids(blocks: tuple[Block, ...]) -> tuple[str, ...]:
    return tuple(block.id for block in blocks)


def _edge_pairs(result) -> tuple[tuple[str, str], ...]:
    return tuple((edge.before_id, edge.after_id) for edge in result.edges)


def _assert_acyclic(node_ids: tuple[str, ...], edges) -> None:
    outgoing = {identifier: [] for identifier in node_ids}
    indegree = dict.fromkeys(node_ids, 0)
    for edge in edges:
        outgoing[edge.before_id].append(edge.after_id)
        indegree[edge.after_id] += 1

    ready = [identifier for identifier in node_ids if indegree[identifier] == 0]
    visited: list[str] = []
    while ready:
        identifier = ready.pop()
        visited.append(identifier)
        for target in outgoing[identifier]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    assert len(visited) == len(node_ids)


def test_orders_pages_bands_columns_then_blocks_top_to_bottom() -> None:
    blocks = (
        _block("p2", page=2),
        _block("band-2", band=1),
        _block("left-low", box=(10_000, 600_000, 90_000, 610_000)),
        _block(
            "right-high",
            column=1,
            box=(120_000, 680_000, 200_000, 690_000),
        ),
        _block("left-high", box=(10_000, 680_000, 90_000, 690_000)),
    )

    result = build_reading_order(blocks)

    assert _ids(result.ordered_blocks) == (
        "left-high",
        "left-low",
        "right-high",
        "band-2",
        "p2",
    )
    assert _edge_pairs(result) == (
        ("left-high", "left-low"),
        ("left-low", "right-high"),
        ("right-high", "band-2"),
        ("band-2", "p2"),
    )


def test_three_columns_finish_left_to_right() -> None:
    blocks = (
        _block("c3-high", column=2, box=(220_000, 680_000, 290_000, 690_000)),
        _block("c1-low", column=0, box=(10_000, 600_000, 90_000, 610_000)),
        _block("c2-low", column=1, box=(120_000, 600_000, 190_000, 610_000)),
        _block("c1-high", column=0, box=(10_000, 680_000, 90_000, 690_000)),
        _block("c2-high", column=1, box=(120_000, 680_000, 190_000, 690_000)),
    )

    result = build_reading_order(blocks)

    assert _ids(result.ordered_blocks) == (
        "c1-high",
        "c1-low",
        "c2-high",
        "c2-low",
        "c3-high",
    )


def test_shuffle_does_not_change_order_or_edges() -> None:
    blocks = [
        _block("a", box=(10_000, 680_000, 90_000, 690_000), ordinal=3),
        _block("b", box=(10_000, 600_000, 90_000, 610_000), ordinal=2),
        _block("c", column=1, box=(120_000, 680_000, 190_000, 690_000)),
        _block("d", band=1),
    ]
    expected = build_reading_order(blocks)

    random.Random(29).shuffle(blocks)
    actual = build_reading_order(blocks)

    assert _ids(actual.ordered_blocks) == _ids(expected.ordered_blocks)
    assert actual.edges == expected.edges


def test_identical_geometry_uses_source_ordinal_then_identifier() -> None:
    box = (10_000, 680_000, 90_000, 690_000)
    blocks = (
        _block("z", box=box, ordinal=5),
        _block("b", box=box, ordinal=2),
        _block("a", box=box, ordinal=2),
    )

    result = build_reading_order(blocks)

    assert _ids(result.ordered_blocks) == ("a", "b", "z")
    _assert_acyclic(_ids(result.ordered_blocks), result.edges)


def test_empty_and_singleton_inputs_are_valid_edgeless_dags() -> None:
    empty = build_reading_order(())
    singleton = build_reading_order((_block("only"),))

    assert empty.ordered_blocks == ()
    assert empty.edges == ()
    assert _ids(singleton.ordered_blocks) == ("only",)
    assert singleton.edges == ()


def test_chain_edges_form_an_acyclic_graph_covering_the_order() -> None:
    blocks = tuple(
        _block(
            f"block-{index}",
            column=index % 3,
            box=(
                index * 10_000,
                700_000 - index * 1_000,
                index * 10_000 + 5_000,
                705_000 - index * 1_000,
            ),
            ordinal=index,
        )
        for index in range(12)
    )

    result = build_reading_order(blocks)
    node_ids = _ids(result.ordered_blocks)

    assert len(result.edges) == len(node_ids) - 1
    assert _edge_pairs(result) == tuple(zip(node_ids, node_ids[1:], strict=False))
    _assert_acyclic(node_ids, result.edges)


def test_duplicate_identifier_is_rejected() -> None:
    with pytest.raises(ReadingOrderError, match="unique"):
        build_reading_order((_block("same"), _block("same", ordinal=2)))


@pytest.mark.parametrize(
    "block",
    [
        _block("zero-width", box=(10, 20, 10, 30)),
        _block("zero-height", box=(10, 20, 30, 20)),
        _block("reversed-x", box=(30, 20, 10, 40)),
        _block("reversed-y", box=(10, 40, 30, 20)),
        _block("bool-coordinate", box=(10, 20, 30, True)),
    ],
)
def test_invalid_geometry_is_rejected(block: Block) -> None:
    with pytest.raises(ReadingOrderError, match="bbox_mpt"):
        build_reading_order((block,))


@pytest.mark.parametrize(
    "block",
    [
        _block("page-zero", page=0),
        _block("band-negative", band=-1),
        _block("column-negative", column=-1),
        _block("ordinal-negative", ordinal=-1),
    ],
)
def test_invalid_topology_indices_are_rejected(block: Block) -> None:
    with pytest.raises(ReadingOrderError, match="index|page_number|source_ordinal"):
        build_reading_order((block,))
