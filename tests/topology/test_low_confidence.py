# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.extraction import extract_document
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.topology.confidence import build_topology
from academic_pdf_en_zh_reader.topology.contracts import TopologyStatus
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"
SOURCE_SHA256 = "a" * 64
NORMALIZED_PDF_SHA256 = "b" * 64


def _extracted(fixture_id: str, tmp_path: Path) -> dict[str, object]:
    pdf_path = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", pdf_path)
    document = extract_document(pdf_path)
    document["source_sha256"] = SOURCE_SHA256
    document["normalized_pdf_sha256"] = NORMALIZED_PDF_SHA256
    return document


def _line(
    identifier: str,
    text: str,
    bbox_mpt: list[int],
    *,
    font_size_mpt: int = 8_500,
    confidence_ppm: int = 1_000_000,
) -> dict[str, object]:
    return {
        "id": identifier,
        "page_number": 1,
        "text": text,
        "bbox_mpt": bbox_mpt,
        "character_ids": [identifier.replace("line", "char")],
        "font_names": ["Synthetic-Regular"],
        "fill_colors": [None],
        "max_font_size_mpt": font_size_mpt,
        "confidence_ppm": confidence_ppm,
        "body_eligible": True,
        "coverage_eligible": True,
        "exclusion_kind": None,
        "container_kind": None,
        "container_id": None,
    }


def _document(lines: list[dict[str, object]]) -> dict[str, object]:
    return {
        "format_version": "1.0.0",
        "source_sha256": SOURCE_SHA256,
        "normalized_pdf_sha256": NORMALIZED_PDF_SHA256,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 300_000, 400_000],
                "crop_box_mpt": [0, 0, 300_000, 400_000],
                "rotation_degrees": 0,
                "chars": [],
                "rectangles": [],
                "curves": [],
                "images": [],
                "lines": lines,
                "graphic_regions": [],
                "captions": [],
                "references": [],
            }
        ],
    }


@pytest.mark.parametrize(
    "fixture_id",
    ["single-column", "two-column", "three-column", "first-page-mixed"],
)
def test_high_confidence_fixtures_produce_valid_source(
    fixture_id: str,
    tmp_path: Path,
) -> None:
    outcome = build_topology(_extracted(fixture_id, tmp_path))

    assert outcome.status is TopologyStatus.OK
    assert outcome.source is not None
    validate_artifact("source", outcome.source)
    assert outcome.source["source_sha256"] == SOURCE_SHA256
    assert outcome.source["normalized_pdf_sha256"] == NORMALIZED_PDF_SHA256
    blocks = [block for page in outcome.source["pages"] for block in page["blocks"]]
    assert [block["reading_order"] for block in blocks] == list(range(len(blocks)))
    for page in outcome.source["pages"]:
        ranges = [
            (block["source_char_start"], block["source_char_end"])
            for block in page["blocks"]
        ]
        assert all(
            first_end <= second_start
            for (_first_start, first_end), (second_start, _second_end) in zip(
                ranges, ranges[1:], strict=False
            )
        )


def test_sparse_low_score_page_fails_closed() -> None:
    document = _document(
        [
            _line(
                "p0001-line-00001",
                "Only sparse text.",
                [20_000, 200_000, 120_000, 210_000],
            )
        ]
    )

    outcome = build_topology(document)

    assert outcome.status is TopologyStatus.NEEDS_TOPOLOGY_REVIEW
    assert outcome.source is None


def test_more_than_three_columns_fails_closed_instead_of_becoming_three() -> None:
    lines = [
        _line(
            f"p0001-line-{index:05d}",
            f"Column {index}",
            [x0, 220_000, x0 + 45_000, 230_000],
        )
        for index, x0 in enumerate((20_000, 90_000, 160_000, 230_000), start=1)
    ] + [
        _line(
            f"p0001-line-{index + 4:05d}",
            f"Second {index}",
            [x0, 190_000, x0 + 45_000, 200_000],
        )
        for index, x0 in enumerate((20_000, 90_000, 160_000, 230_000), start=1)
    ]

    outcome = build_topology(_document(lines))

    assert outcome.status is TopologyStatus.NEEDS_TOPOLOGY_REVIEW
    assert outcome.source is None


def test_adjacency_inference_fails_closed() -> None:
    lines = [
        _line("p0001-line-00001", "Top left", [20_000, 300_000, 120_000, 310_000]),
        _line("p0001-line-00002", "Top right", [180_000, 300_000, 280_000, 310_000]),
        _line("p0001-line-00003", "Ambiguous", [20_000, 250_000, 100_000, 260_000]),
        _line("p0001-line-00004", "Bottom left", [20_000, 200_000, 120_000, 210_000]),
        _line("p0001-line-00005", "Bottom right", [180_000, 200_000, 280_000, 210_000]),
    ]

    outcome = build_topology(_document(lines))

    assert outcome.status is TopologyStatus.NEEDS_TOPOLOGY_REVIEW
    assert outcome.source is None


def test_required_block_below_threshold_fails_closed() -> None:
    lines = [
        _line(
            "p0001-line-00001",
            "Synthetic title",
            [20_000, 350_000, 180_000, 365_000],
            font_size_mpt=15_000,
        ),
        _line(
            "p0001-line-00002",
            "1. Results",
            [20_000, 300_000, 100_000, 310_000],
            font_size_mpt=10_000,
        ),
        _line(
            "p0001-line-00003",
            "Low confidence required body.",
            [20_000, 260_000, 180_000, 270_000],
            confidence_ppm=799_999,
        ),
        _line(
            "p0001-line-00004",
            "Ordinary body support.",
            [20_000, 230_000, 160_000, 240_000],
        ),
    ]

    outcome = build_topology(_document(lines))

    assert outcome.status is TopologyStatus.NEEDS_TOPOLOGY_REVIEW
    assert outcome.source is None


def test_truly_blank_page_is_a_valid_empty_source_page() -> None:
    outcome = build_topology(_document([]))

    assert outcome.status is TopologyStatus.OK
    assert outcome.source is not None
    validate_artifact("source", outcome.source)
    assert outcome.source["pages"][0]["blocks"] == []
    assert outcome.source["pages"][0]["graphic_nodes"] == []


@pytest.mark.parametrize(
    ("kind", "evidence", "caption_text"),
    [
        ("figure", "embedded-image", "Figure 1. Synthetic evidence."),
        (
            "table",
            "captioned-three-rule-table",
            "Table 1. Synthetic evidence.",
        ),
    ],
)
def test_graphic_confidence_comes_from_extraction_evidence(
    kind: str,
    evidence: str,
    caption_text: str,
) -> None:
    document = _document(
        [
            _line(
                "p0001-line-00001",
                "Synthetic figure page",
                [20_000, 350_000, 200_000, 365_000],
                font_size_mpt=15_000,
            ),
            _line(
                "p0001-line-00002",
                "1. Result",
                [20_000, 320_000, 100_000, 330_000],
                font_size_mpt=10_000,
            ),
            _line(
                "p0001-line-00003",
                caption_text,
                [20_000, 80_000, 180_000, 90_000],
            ),
            _line(
                "p0001-line-00004",
                "Ordinary support text.",
                [20_000, 50_000, 150_000, 60_000],
            ),
        ]
    )
    page = document["pages"][0]
    graphic_id = f"p0001-{kind}-0001"
    page["graphic_regions"] = [
        {
            "id": graphic_id,
            "page_number": 1,
            "kind": kind,
            "bbox_mpt": [20_000, 100_000, 180_000, 280_000],
            "evidence": evidence,
        }
    ]
    page["captions"] = [
        {
            "id": f"p0001-{kind}-caption-0001",
            "page_number": 1,
            "kind": kind,
            "number": 1,
            "text": caption_text,
            "line_ids": ["p0001-line-00003"],
            "target_id": graphic_id,
        }
    ]

    outcome = build_topology(document)

    assert outcome.status is TopologyStatus.OK
    assert outcome.source is not None
    [graphic] = outcome.source["pages"][0]["graphic_nodes"]
    assert graphic["confidence_ppm"] == 950_000
