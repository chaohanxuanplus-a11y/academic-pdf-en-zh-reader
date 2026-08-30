# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.qa.raster_compare import (
    RasterPolicy,
    audit_full_page_rasters,
)


def test_pdfium_renders_every_page_at_fixed_144_dpi(
    composed_qa_fixture: dict[str, object],
) -> None:
    audit = audit_full_page_rasters(
        composed_qa_fixture["source_pdf_path"],
        composed_qa_fixture["output_pdf_path"],
        composed_qa_fixture["render_manifest"],
        composed_qa_fixture["overlay_plan"],
        policy=RasterPolicy(),
    )

    assert audit.page_count == len(composed_qa_fixture["render_manifest"]["pages"])
    assert audit.rasterized_page_count == audit.page_count
    assert audit.left_equivalent is True
    assert audit.pages_sane is True


def test_pdfium_does_not_sample_away_continuation_pages(
    composed_qa_continuation_fixture: dict[str, object],
) -> None:
    audit = audit_full_page_rasters(
        composed_qa_continuation_fixture["source_pdf_path"],
        composed_qa_continuation_fixture["output_pdf_path"],
        composed_qa_continuation_fixture["render_manifest"],
        composed_qa_continuation_fixture["overlay_plan"],
        policy=RasterPolicy(),
    )

    assert audit.page_count > 1
    assert audit.rasterized_page_count == audit.page_count
    assert audit.left_equivalent is True
