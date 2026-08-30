# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.extraction import extract_document
from academic_pdf_en_zh_reader.topology.bands import (
    detect_document_bands,
    detect_page_bands,
)
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"


def _extracted(fixture_id: str, tmp_path: Path) -> dict[str, object]:
    pdf_path = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", pdf_path)
    return extract_document(pdf_path)


@pytest.mark.parametrize(
    ("fixture_id", "expected_columns"),
    [
        ("single-column", [1]),
        ("two-column", [1, 2]),
        ("three-column", [1, 3]),
        ("first-page-mixed", [1, 2]),
    ],
)
def test_cc0_fixtures_recover_observable_band_topology(
    fixture_id: str,
    expected_columns: list[int],
    tmp_path: Path,
) -> None:
    document = _extracted(fixture_id, tmp_path)

    page = detect_document_bands(document)[0]

    assert [len(band.columns) for band in page.bands] == expected_columns
    assert all(band.y_top_mpt > band.y_bottom_mpt for band in page.bands)
    assert all(band.score_ppm >= 800_000 for band in page.bands)
    assert page.score_ppm == min(band.score_ppm for band in page.bands)


@pytest.mark.parametrize(
    ("fixture_id", "expected_lefts_mpt"),
    [
        ("two-column", [51_024, 308_976]),
        ("three-column", [51_024, 221_102, 391_181]),
        ("first-page-mixed", [51_024, 308_976]),
    ],
)
def test_multicolumn_band_has_fixed_left_edges_ratios_and_gutters(
    fixture_id: str,
    expected_lefts_mpt: list[int],
    tmp_path: Path,
) -> None:
    page = detect_document_bands(_extracted(fixture_id, tmp_path))[0]
    band = page.bands[-1]

    assert [column.x_left_mpt for column in band.columns] == expected_lefts_mpt
    assert sum(column.width_ratio_ppm for column in band.columns) == 1_000_000
    assert (
        max(column.width_ratio_ppm for column in band.columns)
        - min(column.width_ratio_ppm for column in band.columns)
        <= 70_000
    )
    assert len(band.gutters) == len(band.columns) - 1
    for left, gutter, right in zip(
        band.columns[:-1],
        band.gutters,
        band.columns[1:],
        strict=True,
    ):
        assert left.x_right_mpt == gutter.x_left_mpt
        assert gutter.x_right_mpt == right.x_left_mpt
        assert gutter.width_mpt >= 12_000
        assert gutter.persistence_ppm == 1_000_000


def test_shuffled_extraction_collections_produce_identical_geometry(
    tmp_path: Path,
) -> None:
    document = _extracted("first-page-mixed", tmp_path)
    page = document["pages"][0]
    shuffled = deepcopy(page)
    for name in ("lines", "graphic_regions", "rectangles", "curves", "images"):
        shuffled[name] = list(reversed(shuffled[name]))

    assert asdict(detect_page_bands(page)) == asdict(detect_page_bands(shuffled))


def _line(
    identifier: str,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> dict[str, object]:
    return {
        "id": identifier,
        "bbox_mpt": [x0, y0, x1, y1],
        "coverage_eligible": True,
        "body_eligible": True,
        "max_font_size_mpt": 10_000,
    }


def test_full_width_barrier_splits_two_multicolumn_bands() -> None:
    lines = [
        _line("top-left", 40_000, 710_000, 240_000, 720_000),
        _line("top-right", 320_000, 710_000, 520_000, 720_000),
        _line("barrier", 40_000, 650_000, 520_000, 670_000),
        _line("bottom-left", 40_000, 590_000, 240_000, 600_000),
        _line("bottom-right", 320_000, 590_000, 520_000, 600_000),
    ]
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 595_276, 841_890],
        "lines": lines,
        "graphic_regions": [],
    }

    result = detect_page_bands(page)

    assert [len(band.columns) for band in result.bands] == [2, 1, 2]
    assert "wide-barrier" in result.bands[1].evidence


def test_full_width_horizontal_rule_is_a_barrier_without_reopening_pdf() -> None:
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 595_276, 841_890],
        "lines": [
            _line("top-left", 40_000, 710_000, 240_000, 720_000),
            _line("top-right", 320_000, 710_000, 520_000, 720_000),
            _line("bottom-left", 40_000, 590_000, 240_000, 600_000),
            _line("bottom-right", 320_000, 590_000, 520_000, 600_000),
        ],
        "graphic_regions": [],
        "curves": [
            {
                "id": "horizontal-rule",
                "source_kind": "line",
                "bbox_mpt": [40_000, 650_000, 520_000, 650_000],
                "line_width_mpt": 1_000,
            }
        ],
    }

    result = detect_page_bands(page)

    assert [len(band.columns) for band in result.bands] == [2, 1, 2]
    assert "horizontal-rule" in result.bands[1].object_ids


def test_topology_code_does_not_require_pdf_or_fixture_metadata() -> None:
    page = {
        "page_number": 4,
        "crop_box_mpt": [0, 0, 300_000, 400_000],
        "lines": [
            _line("left", 20_000, 200_000, 120_000, 210_000),
            _line("right", 180_000, 200_000, 280_000, 210_000),
        ],
        "graphic_regions": [],
        # The algorithm must neither need nor interpret these values.
        "metadata": {"FixtureTruth": {"column_count": 1}},
    }

    result = detect_page_bands(page)

    assert len(result.bands[0].columns) == 2


@pytest.mark.parametrize(
    "lines",
    [
        [_line("only-line", 20_000, 200_000, 280_000, 210_000)],
        [
            _line("only-left", 20_000, 200_000, 120_000, 210_000),
            _line("only-right", 180_000, 200_000, 280_000, 210_000),
        ],
    ],
)
def test_sparse_geometry_exposes_low_score_instead_of_claiming_certainty(
    lines: list[dict[str, object]],
) -> None:
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 300_000, 400_000],
        "lines": lines,
        "graphic_regions": [],
    }

    result = detect_page_bands(page)

    assert result.score_ppm < 800_000
    assert any("support" in item for item in result.bands[0].evidence)


def test_more_than_three_columns_is_explainably_outside_this_gate() -> None:
    lines = [
        _line(f"c{column}-a", x0, 200_000, x0 + 50_000, 210_000)
        for column, x0 in enumerate((20_000, 100_000, 180_000, 260_000), start=1)
    ] + [
        _line(f"c{column}-b", x0, 180_000, x0 + 50_000, 190_000)
        for column, x0 in enumerate((20_000, 100_000, 180_000, 260_000), start=1)
    ]
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 330_000, 400_000],
        "lines": lines,
        "graphic_regions": [],
    }

    result = detect_page_bands(page)

    assert result.score_ppm < 500_000
    assert "unsupported-column-count:4" in result.evidence


def _two_column_page(
    page_number: int,
    *,
    noisy_indents: bool = False,
) -> dict[str, object]:
    lines: list[dict[str, object]] = []
    left_starts = (20_000, 90_000) if noisy_indents else (20_000,)
    right_starts = (180_000, 250_000) if noisy_indents else (180_000,)
    for row in range(8):
        y0 = 300_000 - row * 12_000
        left = left_starts[row % len(left_starts)]
        right = right_starts[row % len(right_starts)]
        lines.extend(
            (
                _line(f"p{page_number}-left-{row}", left, y0, 120_000, y0 + 10_000),
                _line(
                    f"p{page_number}-right-{row}",
                    right,
                    y0,
                    280_000,
                    y0 + 10_000,
                ),
            )
        )
    return {
        "page_number": page_number,
        "crop_box_mpt": [0, 0, 300_000, 400_000],
        "lines": lines,
        "graphic_regions": [],
    }


def test_repeated_document_columns_override_page_local_indent_anchors() -> None:
    noisy_page = _two_column_page(3, noisy_indents=True)
    document = {
        "pages": [
            _two_column_page(1),
            _two_column_page(2),
            noisy_page,
        ]
    }

    local = detect_page_bands(noisy_page)
    detected = detect_document_bands(document)

    assert local.score_ppm < 500_000
    assert "unsupported-column-count:4" in local.evidence
    assert [len(band.columns) for band in detected[2].bands] == [2]
    assert detected[2].score_ppm >= 800_000
    assert "document-column-template:2" in detected[2].evidence


def test_document_column_template_does_not_upgrade_sparse_page() -> None:
    sparse = {
        "page_number": 3,
        "crop_box_mpt": [0, 0, 300_000, 400_000],
        "lines": [_line("sparse", 20_000, 200_000, 120_000, 210_000)],
        "graphic_regions": [],
    }

    detected = detect_document_bands(
        {"pages": [_two_column_page(1), _two_column_page(2), sparse]}
    )

    assert detected[2].score_ppm < 800_000


def test_repeated_four_column_document_still_fails_closed() -> None:
    def four_column_page(page_number: int) -> dict[str, object]:
        lines = [
            _line(
                f"p{page_number}-c{column}-r{row}",
                x0,
                300_000 - row * 12_000,
                x0 + 45_000,
                310_000 - row * 12_000,
            )
            for row in range(4)
            for column, x0 in enumerate((20_000, 90_000, 160_000, 230_000), start=1)
        ]
        return {
            "page_number": page_number,
            "crop_box_mpt": [0, 0, 300_000, 400_000],
            "lines": lines,
            "graphic_regions": [],
        }

    detected = detect_document_bands(
        {"pages": [four_column_page(1), four_column_page(2)]}
    )

    assert all(page.score_ppm < 500_000 for page in detected)
    assert all("unsupported-column-count:4" in page.evidence for page in detected)

    mixed = detect_document_bands(
        {
            "pages": [
                _two_column_page(1),
                _two_column_page(2),
                four_column_page(3),
            ]
        }
    )

    assert mixed[2].score_ppm < 500_000
    assert "unsupported-column-count:4" in mixed[2].evidence


def test_ambiguous_short_row_between_multicolumn_rows_is_not_high_confidence() -> None:
    page = {
        "page_number": 1,
        "crop_box_mpt": [0, 0, 595_276, 841_890],
        "lines": [
            _line("top-left", 40_000, 710_000, 240_000, 720_000),
            _line("top-right", 320_000, 710_000, 520_000, 720_000),
            _line("ambiguous-short", 40_000, 650_000, 140_000, 660_000),
            _line("bottom-left", 40_000, 590_000, 240_000, 600_000),
            _line("bottom-right", 320_000, 590_000, 520_000, 600_000),
        ],
        "graphic_regions": [],
    }

    result = detect_page_bands(page)

    assert result.score_ppm < 800_000
    assert "adjacency-inference" in result.bands[0].evidence
