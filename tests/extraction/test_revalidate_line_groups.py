# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from academic_pdf_en_zh_reader.extraction.blocks import build_basic_blocks
from academic_pdf_en_zh_reader.extraction.page_objects import (
    CharacterObject,
    PageObjects,
)
from academic_pdf_en_zh_reader.extraction.revalidate import (
    validate_derived_semantics,
)
from academic_pdf_en_zh_reader.extraction.text_lines import build_text_lines


def _character(
    ordinal: int,
    text: str,
    bbox_mpt: tuple[int, int, int, int],
) -> CharacterObject:
    return CharacterObject(
        id=f"p0001-char-{ordinal:06d}",
        page_number=1,
        text=text,
        bbox_mpt=bbox_mpt,
        font_name="Synthetic-Regular",
        font_size_mpt=10_000,
        fill_color=None,
        stroke_color=None,
        upright=True,
    )


def _page(*characters: CharacterObject) -> dict[str, object]:
    objects = PageObjects(
        page_number=1,
        media_box_mpt=(0, 0, 200_000, 300_000),
        crop_box_mpt=(0, 0, 200_000, 300_000),
        rotation_degrees=0,
        chars=tuple(characters),
        rectangles=(),
        curves=(),
        images=(),
    )
    rebuilt = build_basic_blocks((objects,), build_text_lines((objects,))).pages[0]
    page = asdict(objects)
    derived = asdict(rebuilt)
    page.update(
        {
            "lines": derived["lines"],
            "graphic_regions": derived["graphic_regions"],
            "captions": derived["captions"],
            "references": derived["references"],
        }
    )
    return json.loads(json.dumps(page))


def _line(
    identifier: str,
    text: str,
    bbox_mpt: list[int],
    character_ids: list[str],
) -> dict[str, object]:
    return {
        "id": identifier,
        "page_number": 1,
        "text": text,
        "bbox_mpt": bbox_mpt,
        "character_ids": character_ids,
        "font_names": ["Synthetic-Regular"],
        "fill_colors": [None],
        "max_font_size_mpt": 10_000,
        "confidence_ppm": 1_000_000,
        "body_eligible": True,
        "coverage_eligible": True,
        "exclusion_kind": None,
        "container_kind": None,
        "container_id": None,
    }


def test_parent_rebuild_rejects_forged_split_of_one_raw_baseline() -> None:
    page = _page(
        _character(1, "A", (10_000, 100_000, 15_000, 110_000)),
        _character(2, "B", (15_500, 100_000, 20_500, 110_000)),
    )
    validate_derived_semantics([page])
    page["lines"] = [
        _line(
            "p0001-line-00001",
            "A",
            [10_000, 100_000, 15_000, 110_000],
            ["p0001-char-000001"],
        ),
        _line(
            "p0001-line-00002",
            "B",
            [15_500, 100_000, 20_500, 110_000],
            ["p0001-char-000002"],
        ),
    ]

    with pytest.raises(ValueError, match="base lines differ"):
        validate_derived_semantics([page])


def test_parent_rebuild_rejects_forged_merge_across_wide_gutter() -> None:
    page = _page(
        _character(1, "A", (10_000, 100_000, 15_000, 110_000)),
        _character(2, "B", (80_000, 100_000, 85_000, 110_000)),
    )
    validate_derived_semantics([page])
    page["lines"] = [
        _line(
            "p0001-line-00001",
            "A B",
            [10_000, 100_000, 85_000, 110_000],
            ["p0001-char-000001", "p0001-char-000002"],
        )
    ]

    with pytest.raises(ValueError, match="base lines differ"):
        validate_derived_semantics([page])
