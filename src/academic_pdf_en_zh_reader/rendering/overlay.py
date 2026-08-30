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


def _point(value: object) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(item) is not int for item in value)
    ):
        raise OverlayRenderError("leader point is invalid")
    return int(value[0]), int(value[1])


def _route(value: object) -> tuple[tuple[tuple[int, int], ...], int]:
    required = {
        "leader_id",
        "unit_id",
        "source_block_id",
        "route_kind",
        "lane_index",
        "points_mpt",
        "corner_radius_mpt",
        "dash_mpt",
        "width_mpt",
        "clearance_mpt",
        "color_hex",
        "style_version",
        "bbox_mpt",
        "route_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise OverlayRenderError("leader route fields are not exact")
    points = tuple(_point(point) for point in value["points_mpt"])
    radius = value["corner_radius_mpt"]
    dash = value["dash_mpt"]
    if (
        value["route_hash"]
        != sha256_canonical(
            {key: child for key, child in value.items() if key != "route_hash"}
        )
        or value["route_kind"] not in {"horizontal", "rounded-three-segment"}
        or type(radius) is not int
        or radius < 0
        or not isinstance(dash, list)
        or not dash
        or any(type(item) is not int or item <= 0 for item in dash)
        or type(value["width_mpt"]) is not int
        or value["width_mpt"] <= 0
        or value["color_hex"] != "#9A9A9A"
        or (
            value["route_kind"] == "horizontal"
            and (len(points) != 2 or radius != 0 or points[0][1] != points[1][1])
        )
        or (
            value["route_kind"] == "rounded-three-segment"
            and (len(points) != 4 or radius <= 0)
        )
    ):
        raise OverlayRenderError("leader route values are invalid")
    for start, end in zip(points, points[1:], strict=False):
        if start == end or (start[0] != end[0] and start[1] != end[1]):
            raise OverlayRenderError(
                "leader segments must be non-empty orthogonal lines"
            )
    return points, radius


def _before_after(
    previous: tuple[int, int],
    corner: tuple[int, int],
    following: tuple[int, int],
    radius: int,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]:
    incoming = (
        0 if corner[0] == previous[0] else (1 if corner[0] > previous[0] else -1),
        0 if corner[1] == previous[1] else (1 if corner[1] > previous[1] else -1),
    )
    outgoing = (
        0 if following[0] == corner[0] else (1 if following[0] > corner[0] else -1),
        0 if following[1] == corner[1] else (1 if following[1] > corner[1] else -1),
    )
    before = (corner[0] - incoming[0] * radius, corner[1] - incoming[1] * radius)
    after = (corner[0] + outgoing[0] * radius, corner[1] + outgoing[1] * radius)
    # Cubic approximation of one quarter circle, derived only from frozen radius.
    control_distance = radius * 552_285 // 1_000_000
    first_control = (
        before[0] + incoming[0] * control_distance,
        before[1] + incoming[1] * control_distance,
    )
    second_control = (
        after[0] - outgoing[0] * control_distance,
        after[1] - outgoing[1] * control_distance,
    )
    return before, first_control, second_control, after


def _draw_route(canvas: Canvas, route: Mapping[str, object]) -> None:
    points, radius = _route(route)
    canvas.saveState()
    canvas.setStrokeColor(str(route["color_hex"]))
    canvas.setLineWidth(int(route["width_mpt"]) / 1000)
    canvas.setDash([int(value) / 1000 for value in route["dash_mpt"]], 0)
    canvas.setLineCap(1)
    canvas.setLineJoin(1)
    path = canvas.beginPath()
    path.moveTo(points[0][0] / 1000, points[0][1] / 1000)
    if len(points) == 2:
        path.lineTo(points[1][0] / 1000, points[1][1] / 1000)
    else:
        for index in (1, 2):
            before, control1, control2, after = _before_after(
                points[index - 1],
                points[index],
                points[index + 1],
                radius,
            )
            path.lineTo(before[0] / 1000, before[1] / 1000)
            path.curveTo(
                control1[0] / 1000,
                control1[1] / 1000,
                control2[0] / 1000,
                control2[1] / 1000,
                after[0] / 1000,
                after[1] / 1000,
            )
        path.lineTo(points[-1][0] / 1000, points[-1][1] / 1000)
    canvas.drawPath(path, stroke=1, fill=0)
    canvas.restoreState()


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
        plan.get("overlay_plan_version") != 1
        or plan.get("artifact_kind") != "overlay-plan"
        or plan.get("overlay_plan_hash")
        != sha256_canonical(
            {key: value for key, value in plan.items() if key != "overlay_plan_hash"}
        )
        or not isinstance(plan.get("pages"), list)
        or not plan["pages"]
    ):
        raise OverlayRenderError("overlay plan root is invalid")
    for expected_page, page in enumerate(plan["pages"], start=1):
        if not isinstance(page, Mapping) or page.get("page_number") != expected_page:
            raise OverlayRenderError("overlay plan page order is invalid")
        _validate_frozen_page(page)
        for route in page["leader_routes"]:
            _route(route)


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
        for route in page["leader_routes"]:
            _draw_route(canvas, route)
        _draw_validated_page(canvas, page)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


__all__ = ["OverlayRenderError"]
