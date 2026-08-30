# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable

from academic_pdf_en_zh_reader.layout.solver import solve_layout


def test_english_cross_column_fragments_do_not_force_a_chinese_split(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {
                "unit_id": "cross-column",
                "line_count": 3,
                "allowed_frame_indexes": (0, 1),
                "legal_breaks": [(2, "line")],
            }
        ],
        frame_count=2,
    )

    layout = solve_layout(graph)
    blocks = layout["pages"][0]["blocks"]

    assert len(blocks) == 1
    assert blocks[0]["frame_id"] == "native-0"
    assert (blocks[0]["line_start"], blocks[0]["line_end"]) == (0, 3)


def test_chinese_splits_only_at_a_legal_target_line_when_space_requires_it(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {
                "unit_id": "cross-column",
                "line_count": 5,
                "allowed_frame_indexes": (0, 1),
                "legal_breaks": [(2, "sentence")],
            }
        ],
        frame_count=2,
    )

    layout = solve_layout(graph)
    blocks = layout["pages"][0]["blocks"]

    observed = [
        (block["frame_id"], block["line_start"], block["line_end"]) for block in blocks
    ]
    assert observed == [
        ("native-0", 0, 2),
        ("native-1", 2, 5),
    ]
    assert [line["index"] for block in blocks for line in block["lines"]] == list(
        range(5)
    )


def test_equal_quality_breaks_minimize_multicolumn_band_height_before_stable_id(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {
                "unit_id": "balanced",
                "line_count": 6,
                "allowed_frame_indexes": (0, 1),
                "legal_breaks": [(2, "sentence"), (3, "sentence")],
            }
        ],
        frame_count=2,
    )

    layout = solve_layout(graph)
    blocks = layout["pages"][0]["blocks"]

    assert [(block["line_start"], block["line_end"]) for block in blocks] == [
        (0, 3),
        (3, 6),
    ]
    assert layout["solver_trace"]["band_height_objective_mpt"] == [68_000]


def test_equal_quality_and_geometry_breaks_use_the_earliest_stable_line_id(
    frame_graph_factory: Callable[..., dict[str, object]],
) -> None:
    graph = frame_graph_factory(
        [
            {
                "unit_id": "stable",
                "line_count": 5,
                "allowed_frame_indexes": (0, 1),
                "legal_breaks": [(2, "sentence"), (3, "sentence")],
            }
        ],
        frame_count=2,
    )

    layout = solve_layout(graph)

    assert [
        (block["line_start"], block["line_end"])
        for block in layout["pages"][0]["blocks"]
    ] == [(0, 2), (2, 5)]
