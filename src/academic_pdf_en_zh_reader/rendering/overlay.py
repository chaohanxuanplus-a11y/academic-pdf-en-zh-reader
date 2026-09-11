# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Render an already parent-validated overlay plan as vector PDF pages."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.branding import (
    load_brand_manifest,
    validate_brand_block,
)
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.rendering.text_draw import (
    _draw_validated_page,
    _validate_frozen_page,
)


class OverlayRenderError(ValueError):
    """Raised when a supposedly validated plan cannot be drawn exactly."""


def _draw_brand_image(canvas: Canvas, value: object) -> None:
    block = validate_brand_block(value)
    if block is None:
        return
    _manifest, asset_path, _manifest_hash = load_brand_manifest()
    box = block["image_bbox_mpt"]
    canvas.saveState()
    canvas.drawImage(
        ImageReader(str(asset_path)),
        int(box[0]) / 1000,
        int(box[1]) / 1000,
        width=(int(box[2]) - int(box[0])) / 1000,
        height=(int(box[3]) - int(box[1])) / 1000,
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )
    canvas.restoreState()


def _validate_plan_for_drawing(plan: Mapping[str, object]) -> None:
    if (
        plan.get("overlay_plan_version") != 2
        or plan.get("artifact_kind") != "overlay-plan"
        or plan.get("overlay_plan_hash")
        != sha256_canonical(
            {key: value for key, value in plan.items() if key != "overlay_plan_hash"}
        )
        or not isinstance(plan.get("pages"), list)
        or not plan["pages"]
    ):
        raise OverlayRenderError("overlay plan root is invalid")
    pages = plan["pages"]
    for expected_page, page in enumerate(pages, start=1):
        if not isinstance(page, Mapping) or page.get("page_number") != expected_page:
            raise OverlayRenderError("overlay plan page order is invalid")
        _validate_frozen_page(page)
    branding = plan.get("branding")
    branded_pages = [page["page_number"] for page in pages if page.get("brand_block")]
    if (
        not isinstance(branding, Mapping)
        or branded_pages != [len(pages)]
        or branding.get("rendered_page_number") != len(pages)
    ):
        raise OverlayRenderError("final disclaimer page binding is invalid")


def _render_validated_overlay_pdf(plan: Mapping[str, object]) -> bytes:
    """Render a plan already proven equal to a trusted parent recomputation."""

    _validate_plan_for_drawing(plan)
    output = BytesIO()
    canvas = Canvas(
        output,
        pagesize=(
            A3_LANDSCAPE_WIDTH_MPT / 1000,
            A3_LANDSCAPE_HEIGHT_MPT / 1000,
        ),
        invariant=1,
        pageCompression=1,
    )
    canvas.setAuthor("")
    canvas.setTitle("Academic bilingual overlay")
    canvas.setSubject("Frozen right-panel vector overlay")
    canvas.setCreator("academic-pdf-en-zh-reader")
    for page in plan["pages"]:
        _draw_brand_image(canvas, page["brand_block"])
        _draw_validated_page(canvas, page)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


__all__ = ["OverlayRenderError"]
