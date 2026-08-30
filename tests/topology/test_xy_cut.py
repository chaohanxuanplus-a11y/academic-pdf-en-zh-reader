# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

from academic_pdf_en_zh_reader.topology.projection import (
    OccupiedBox,
    axis_gaps,
    gutter_persistence_ppm,
    merge_intervals,
)
from academic_pdf_en_zh_reader.topology.xy_cut import (
    recursive_xy_cut,
    vertical_xy_cut,
)

ROOT = Path(__file__).resolve().parents[2]


def _box(identifier: str, x0: int, y0: int, x1: int, y1: int) -> OccupiedBox:
    return OccupiedBox(
        id=identifier,
        kind="line",
        bbox_mpt=(x0, y0, x1, y1),
    )


def test_projection_merges_touching_intervals_and_finds_inner_gaps() -> None:
    assert merge_intervals(((10, 20), (20, 30), (50, 70))) == (
        (10, 30),
        (50, 70),
    )
    boxes = (
        _box("left-a", 10_000, 100_000, 90_000, 110_000),
        _box("left-b", 10_000, 80_000, 100_000, 90_000),
        _box("right-a", 130_000, 100_000, 210_000, 110_000),
        _box("right-b", 130_000, 80_000, 220_000, 90_000),
    )

    assert axis_gaps(boxes, axis="x", minimum_mpt=20_000) == ((100_000, 130_000),)
    assert (
        gutter_persistence_ppm(
            boxes,
            gutter=(100_000, 130_000),
            y_bottom_mpt=80_000,
            y_top_mpt=110_000,
        )
        == 1_000_000
    )


def test_vertical_xy_cut_recovers_three_columns_and_stable_evidence() -> None:
    boxes = [
        _box("c1-a", 20_000, 300_000, 90_000, 310_000),
        _box("c1-b", 20_000, 270_000, 95_000, 280_000),
        _box("c2-a", 120_000, 300_000, 190_000, 310_000),
        _box("c2-b", 120_000, 270_000, 192_000, 280_000),
        _box("c3-a", 220_000, 300_000, 290_000, 310_000),
        _box("c3-b", 220_000, 270_000, 295_000, 280_000),
    ]

    first = vertical_xy_cut(tuple(boxes), minimum_gutter_mpt=20_000)
    random.Random(27).shuffle(boxes)
    second = vertical_xy_cut(tuple(boxes), minimum_gutter_mpt=20_000)

    assert first == second
    assert [region.bbox_mpt[:3:2] for region in first.regions] == [
        (20_000, 95_000),
        (120_000, 192_000),
        (220_000, 295_000),
    ]
    assert [(gap.start_mpt, gap.end_mpt) for gap in first.gutters] == [
        (95_000, 120_000),
        (192_000, 220_000),
    ]
    assert first.score_ppm >= 900_000


def test_object_crossing_candidate_gutter_reduces_persistence() -> None:
    boxes = (
        _box("left", 10_000, 50_000, 90_000, 60_000),
        _box("right", 130_000, 50_000, 210_000, 60_000),
        _box("barrier", 10_000, 80_000, 210_000, 90_000),
    )

    score = gutter_persistence_ppm(
        boxes,
        gutter=(90_000, 130_000),
        y_bottom_mpt=50_000,
        y_top_mpt=90_000,
    )

    assert 0 < score < 1_000_000


def test_recursive_xy_cut_uses_barrier_gap_then_column_gutter() -> None:
    boxes = (
        _box("full-width-title", 10_000, 180_000, 210_000, 190_000),
        _box("left", 10_000, 100_000, 90_000, 110_000),
        _box("right", 130_000, 100_000, 210_000, 110_000),
    )

    tree = recursive_xy_cut(
        boxes,
        minimum_x_gap_mpt=20_000,
        minimum_y_gap_mpt=20_000,
    )

    assert tree.cut_axis == "y"
    assert tree.children[0].object_ids == ("full-width-title",)
    assert tree.children[1].cut_axis == "x"
    assert [child.object_ids for child in tree.children[1].children] == [
        ("left",),
        ("right",),
    ]


def test_fresh_topology_import_does_not_load_a_pdf_parser() -> None:
    script = (
        f"import sys;sys.path.insert(0,{str(ROOT / 'src')!r});"
        "import academic_pdf_en_zh_reader.topology.bands;"
        "assert 'pdfplumber' not in sys.modules;"
        "assert 'pypdf' not in sys.modules;"
        "assert not any(n == 'pdfminer' or n.startswith('pdfminer.') "
        "for n in sys.modules)"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
