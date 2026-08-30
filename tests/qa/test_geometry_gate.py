# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest
from pypdf import PdfReader

from academic_pdf_en_zh_reader.qa.geometry import (
    GeometryQaError,
    validate_bounds_and_overlap,
    validate_mirrored_frames,
)


def test_mirrored_frame_x_coordinates_are_exact(
    composed_qa_fixture: dict[str, object],
) -> None:
    validate_mirrored_frames(
        composed_qa_fixture["source"],
        composed_qa_fixture["frame_graph"],
        composed_qa_fixture["layout"],
    )

    graph = deepcopy(composed_qa_fixture["frame_graph"])
    graph["pages"][0]["frames"][0]["text_left_mpt"] += 1
    with pytest.raises(GeometryQaError, match="GEOMETRY_MIRROR_INVALID"):
        validate_mirrored_frames(
            composed_qa_fixture["source"], graph, composed_qa_fixture["layout"]
        )


def test_block_or_glyph_outside_frame_is_rejected(
    composed_qa_fixture: dict[str, object],
) -> None:
    layout = deepcopy(composed_qa_fixture["layout"])
    layout["pages"][0]["blocks"][0]["bbox_mpt"][2] = 1_190_552

    with pytest.raises(GeometryQaError, match="GEOMETRY_BOUNDS_INVALID"):
        validate_bounds_and_overlap(
            layout,
            composed_qa_fixture["overlay_plan"],
        )


def test_final_pdf_page_boxes_are_a3(
    composed_qa_fixture: dict[str, object],
) -> None:
    from academic_pdf_en_zh_reader.qa.geometry import validate_a3_pages

    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    evidence = validate_a3_pages(reader, composed_qa_fixture["render_manifest"])
    assert evidence["page_count"] == len(reader.pages)
