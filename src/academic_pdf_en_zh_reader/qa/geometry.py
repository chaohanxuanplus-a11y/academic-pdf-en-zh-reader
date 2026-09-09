# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic geometry gates for A3 composition and frozen layout."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from pypdf import PdfReader

from academic_pdf_en_zh_reader.qa.page_contract import (
    source_manifest_pages,
    source_plan_pages,
)
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
)

type Box = tuple[int, int, int, int]


class GeometryQaError(ValueError):
    """A stable geometry QA failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _box(value: object, *, code: str) -> Box:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 4
        or any(type(item) is not int for item in value)
    ):
        raise GeometryQaError(code)
    result = tuple(int(item) for item in value)
    if result[0] >= result[2] or result[1] >= result[3]:
        raise GeometryQaError(code)
    return result  # type: ignore[return-value]


def _contains(outer: Box, inner: Box) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _intersects(left: Box, right: Box) -> bool:
    return (
        left[0] < right[2]
        and right[0] < left[2]
        and left[1] < right[3]
        and right[1] < left[3]
    )


def _mpt(value: object) -> int:
    return round(float(value) * 1000)


def validate_a3_pages(
    reader: PdfReader, render_manifest: Mapping[str, object]
) -> dict[str, int]:
    """Require every real output page and manifest entry to be exact A3 landscape."""

    pages = render_manifest.get("pages")
    if (
        reader.is_encrypted
        or not isinstance(pages, list)
        or not pages
        or len(reader.pages) != len(pages)
    ):
        raise GeometryQaError("GEOMETRY_A3_INVALID")
    for page_number, (page, mapping) in enumerate(
        zip(reader.pages, pages, strict=True), start=1
    ):
        try:
            media = (
                _mpt(page.mediabox.left),
                _mpt(page.mediabox.bottom),
                _mpt(page.mediabox.right),
                _mpt(page.mediabox.top),
            )
            crop = (
                _mpt(page.cropbox.left),
                _mpt(page.cropbox.bottom),
                _mpt(page.cropbox.right),
                _mpt(page.cropbox.top),
            )
            valid = (
                media == (0, 0, A3_LANDSCAPE_WIDTH_MPT, A3_LANDSCAPE_HEIGHT_MPT)
                and crop == media
                and int(page.get("/Rotate", 0)) % 360 == 0
                and float(page.get("/UserUnit", 1)) == 1.0
                and mapping["output_page_number"] == page_number
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GeometryQaError("GEOMETRY_A3_INVALID") from exc
        if not valid:
            raise GeometryQaError("GEOMETRY_A3_INVALID")
    return {"page_count": len(pages)}


def validate_source_left_one_to_one(
    source: Mapping[str, object], render_manifest: Mapping[str, object]
) -> dict[str, int]:
    """Verify the manifest's source placement is translation-only at exact 1:1."""

    try:
        sources = {int(page["page_number"]): page for page in source["pages"]}  # type: ignore[index]
        pages = source_manifest_pages(render_manifest["pages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryQaError("GEOMETRY_SOURCE_PLACEMENT_INVALID") from exc
    if not isinstance(pages, list) or not pages:
        raise GeometryQaError("GEOMETRY_SOURCE_PLACEMENT_INVALID")
    for mapping in pages:
        try:
            source_page = sources[int(mapping["source_page_number"])]
            normalized = _box(
                mapping["source_normalized_visible_box_mpt"],
                code="GEOMETRY_SOURCE_PLACEMENT_INVALID",
            )
            width = normalized[2] - normalized[0]
            height = normalized[3] - normalized[1]
            valid = (
                mapping["source_crop_box_mpt"] == source_page["crop_box_mpt"]
                and abs(width - A4_WIDTH_MPT) <= 1_000
                and abs(height - A4_HEIGHT_MPT) <= 1_000
                and mapping["source_transform_mpt"]
                == [1000, 0, 0, 1000, -normalized[0], -normalized[1]]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GeometryQaError("GEOMETRY_SOURCE_PLACEMENT_INVALID") from exc
        if not valid:
            raise GeometryQaError("GEOMETRY_SOURCE_PLACEMENT_INVALID")
    return {"source_placement_count": len(pages)}


def _largest_remainder_ratios(widths: list[int]) -> list[int]:
    total = sum(widths)
    if total <= 0:
        raise GeometryQaError("GEOMETRY_MIRROR_INVALID")
    ratios = [width * 1_000_000 // total for width in widths]
    remainder = 1_000_000 - sum(ratios)
    order = sorted(
        range(len(widths)),
        key=lambda index: (-(widths[index] * 1_000_000 % total), index),
    )
    for index in order[:remainder]:
        ratios[index] += 1
    return ratios


def validate_mirrored_frames(
    source: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
) -> dict[str, int]:
    """Recompute every mirrored column and its fixed translation left edge."""

    try:
        padding = int(frame_graph["flow_spacing"]["horizontal_padding_mpt"])  # type: ignore[index]
        graph_frames = {
            str(frame["id"]): frame
            for page in frame_graph["pages"]  # type: ignore[index]
            for frame in page["frames"]
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryQaError("GEOMETRY_MIRROR_INVALID") from exc
    expected: dict[tuple[int, str, str, str], dict[str, object]] = {}
    for source_page in source["pages"]:  # type: ignore[index]
        crop_left, crop_bottom, _crop_right, _crop_top = source_page["crop_box_mpt"]
        for band_index, band in enumerate(source_page["bands"]):
            columns = band["columns"]
            widths = [
                int(column["x_right_mpt"]) - int(column["x_left_mpt"])
                for column in columns
            ]
            ratios = _largest_remainder_ratios(widths)
            for column_index, (column, ratio) in enumerate(
                zip(columns, ratios, strict=True)
            ):
                left = A4_WIDTH_MPT + int(column["x_left_mpt"]) - int(crop_left)
                right = min(
                    A3_LANDSCAPE_WIDTH_MPT,
                    A4_WIDTH_MPT + int(column["x_right_mpt"]) - int(crop_left),
                )
                bottom = int(band["y_bottom_mpt"]) - int(crop_bottom)
                top = int(band["y_top_mpt"]) - int(crop_bottom)
                for kind in ("native", "continuation-template"):
                    expected[
                        (
                            int(source_page["page_number"]),
                            str(band["id"]),
                            str(column["id"]),
                            kind,
                        )
                    ] = {
                        "bbox_mpt": [left, bottom, right, top],
                        "text_left_mpt": left + padding,
                        "text_right_mpt": right - padding,
                        "band_index": band_index,
                        "column_index": column_index,
                        "column_count": len(columns),
                        "width_ratio_ppm": ratio,
                    }
    seen: set[tuple[int, str, str, str]] = set()
    for frame in graph_frames.values():
        key = (
            int(frame["source_page_number"]),
            str(frame["source_band_id"]),
            str(frame["source_column_id"]),
            str(frame["kind"]),
        )
        contract = expected.get(key)
        if contract is None or any(
            frame.get(name) != value for name, value in contract.items()
        ):
            raise GeometryQaError("GEOMETRY_MIRROR_INVALID")
        seen.add(key)
    if seen != set(expected):
        raise GeometryQaError("GEOMETRY_MIRROR_INVALID")

    layout_count = 0
    for page in layout["pages"]:  # type: ignore[index]
        for frame in page["frames"]:
            template = graph_frames.get(str(frame["template_frame_id"]))
            if (
                template is None
                or frame["text_left_mpt"] != template["text_left_mpt"]
                or frame["text_right_mpt"] != template["text_right_mpt"]
                or frame["bbox_mpt"][0] != template["bbox_mpt"][0]
                or frame["bbox_mpt"][2] != template["bbox_mpt"][2]
                or frame["column_index"] != template["column_index"]
                or frame["column_count"] != template["column_count"]
                or frame["width_ratio_ppm"] != template["width_ratio_ppm"]
            ):
                raise GeometryQaError("GEOMETRY_MIRROR_INVALID")
            layout_count += 1
    return {"frame_count": layout_count}


def validate_bounds_and_overlap(
    layout: Mapping[str, object], overlay_plan: Mapping[str, object]
) -> dict[str, int]:
    """Require all translated geometry to stay in its frame with no block overlap."""

    page_box = (0, 0, A3_LANDSCAPE_WIDTH_MPT, A3_LANDSCAPE_HEIGHT_MPT)
    right_box = (A4_WIDTH_MPT, 0, A3_LANDSCAPE_WIDTH_MPT, A3_LANDSCAPE_HEIGHT_MPT)
    try:
        plan_pages = source_plan_pages(layout, overlay_plan)
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryQaError("GEOMETRY_BOUNDS_INVALID") from exc
    layout_pages = layout.get("pages")
    if (
        not isinstance(plan_pages, list)
        or not isinstance(layout_pages, list)
        or len(plan_pages) != len(layout_pages)
    ):
        raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
    block_count = 0
    for layout_page, plan_page in zip(layout_pages, plan_pages, strict=True):
        frames = {str(frame["id"]): frame for frame in layout_page["frames"]}
        blocks_by_frame: defaultdict[str, list[Box]] = defaultdict(list)
        blocks: dict[tuple[str, int], Mapping[str, object]] = {}
        for block in layout_page["blocks"]:
            frame = frames.get(str(block["frame_id"]))
            if frame is None:
                raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            frame_box = _box(frame["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID")
            block_box = _box(block["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID")
            if not _contains(right_box, frame_box) or not _contains(
                frame_box, block_box
            ):
                raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            for line in block["lines"]:
                line_box = (
                    int(line["x_mpt"]),
                    int(line["baseline_y_mpt"]) + int(line["descent_mpt"]),
                    int(line["x_mpt"]) + int(line["width_mpt"]),
                    int(line["baseline_y_mpt"]) + int(line["ascent_mpt"]),
                )
                if line["x_mpt"] != frame["text_left_mpt"] or not _contains(
                    frame_box, line_box
                ):
                    raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            key = (str(block["content_id"]), int(block["part_index"]))
            if key in blocks:
                raise GeometryQaError("GEOMETRY_OVERLAP_INVALID")
            blocks[key] = block
            blocks_by_frame[str(block["frame_id"])].append(block_box)
            block_count += 1
        for boxes in blocks_by_frame.values():
            ordered = sorted(
                boxes, key=lambda item: (item[1], item[3], item[0], item[2])
            )
            if any(
                _intersects(left, right)
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                raise GeometryQaError("GEOMETRY_OVERLAP_INVALID")
        runs_by_line: defaultdict[tuple[str, int, int], list[Box]] = defaultdict(list)
        brand_block = plan_page.get("brand_block")
        brand_box = (
            None
            if brand_block is None
            else _box(brand_block["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID")
        )
        if brand_box is not None and not _contains(right_box, brand_box):
            raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
        for run in plan_page["draw_runs"]:
            run_box = _box(run["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID")
            if not _contains(right_box, run_box):
                raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            if run["content_kind"] == "brand":
                if brand_box is None or not _contains(brand_box, run_box):
                    raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            elif run["content_kind"] != "continuation-label":
                block = blocks.get((str(run["content_id"]), int(run["part_index"])))
                if block is None or not _contains(
                    _box(block["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID"), run_box
                ):
                    raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            runs_by_line[
                (
                    str(run["content_id"]),
                    int(run["part_index"]),
                    int(run["line_index"]),
                )
            ].append(run_box)
        for boxes in runs_by_line.values():
            ordered = sorted(boxes)
            if any(
                _intersects(left, right)
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                raise GeometryQaError("GEOMETRY_OVERLAP_INVALID")
        for underline in plan_page["underlines"]:
            if not _contains(
                right_box,
                _box(underline["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID"),
            ):
                raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
        for route in plan_page["leader_routes"]:
            route_box = _box(route["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID")
            if not _contains(page_box, route_box):
                raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
    if len(overlay_plan["pages"]) > len(plan_pages):
        page = overlay_plan["pages"][-1]
        from academic_pdf_en_zh_reader.rendering.branding import load_brand_manifest

        manifest, _asset, _manifest_hash = load_brand_manifest()
        margin = int(manifest["layout"]["page_margin_mpt"])
        safe_box = (margin, margin, page_box[2] - margin, page_box[3] - margin)
        brand_box = _box(
            page["brand_block"]["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID"
        )
        image_box = _box(
            page["brand_block"]["image_bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID"
        )
        if not _contains(safe_box, brand_box) or not _contains(brand_box, image_box):
            raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
        boxes = [image_box]
        for run in page["draw_runs"]:
            run_box = _box(run["bbox_mpt"], code="GEOMETRY_BOUNDS_INVALID")
            if not _contains(brand_box, run_box):
                raise GeometryQaError("GEOMETRY_BOUNDS_INVALID")
            if any(_intersects(run_box, previous) for previous in boxes):
                raise GeometryQaError("GEOMETRY_OVERLAP_INVALID")
            boxes.append(run_box)
    return {"block_count": block_count}


def validate_leader_policy(
    source: Mapping[str, object],
    layout: Mapping[str, object],
    overlay_plan: Mapping[str, object],
) -> dict[str, int]:
    """Forbid leaders for multi-column soft alignment and bind every legal route."""

    column_counts = {
        (int(page["page_number"]), str(band["id"])): len(band["columns"])
        for page in source["pages"]  # type: ignore[index]
        for band in page["bands"]
    }
    route_count = 0
    try:
        plan_pages = source_plan_pages(layout, overlay_plan)
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryQaError("GEOMETRY_LEADER_INVALID") from exc
    for page, plan_page in zip(layout["pages"], plan_pages, strict=True):  # type: ignore[index]
        routes = {str(route["unit_id"]): route for route in plan_page["leader_routes"]}
        expected: set[str] = set()
        for block in page["blocks"]:
            selected = block.get("selected_anchor")
            if selected is None:
                continue
            columns = column_counts.get(
                (int(selected["source_page_number"]), str(selected["source_band_id"]))
            )
            if columns is None:
                raise GeometryQaError("GEOMETRY_LEADER_INVALID")
            if selected["kind"] == "soft-y":
                if columns <= 1 or str(block["unit_id"]) in routes:
                    raise GeometryQaError("GEOMETRY_LEADER_INVALID")
                continue
            if selected["kind"] != "leader" or columns != 1:
                raise GeometryQaError("GEOMETRY_LEADER_INVALID")
            unit_id = str(block["unit_id"])
            route = routes.get(unit_id)
            if (
                route is None
                or route["points_mpt"][0] != selected["source_endpoint_mpt"]
            ):
                raise GeometryQaError("GEOMETRY_LEADER_INVALID")
            expected.add(unit_id)
        if expected != set(routes):
            raise GeometryQaError("GEOMETRY_LEADER_INVALID")
        route_count += len(routes)
    return {"leader_count": route_count}


def validate_continuation_and_sizes(
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    overlay_plan: Mapping[str, object],
) -> dict[str, int]:
    """Keep continuation labels and every role at its frozen document-wide size."""

    flow_styles: dict[str, Mapping[str, object]] = {}
    for key in ("unit_flows", "figure_note_flows", "auxiliary_flows"):
        for flow in frame_graph.get(key, []):
            identifier = flow.get("unit_id") if key == "unit_flows" else flow.get("id")
            flow_styles[str(identifier)] = flow["style"]
    header = frame_graph.get("continuation_header")
    continuation_count = 0
    try:
        plan_pages = source_plan_pages(layout, overlay_plan)
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryQaError("GEOMETRY_CONTINUATION_INVALID") from exc
    for page, plan_page in zip(layout["pages"], plan_pages, strict=True):  # type: ignore[index]
        continuation = page["page_kind"] == "continuation"
        if continuation != (page["continuation_label"] is not None) or continuation != (
            plan_page["continuation_label"] is not None
        ):
            raise GeometryQaError("GEOMETRY_CONTINUATION_INVALID")
        if continuation:
            continuation_count += 1
            if (
                not isinstance(header, Mapping)
                or page["continuation_label"]["header_hash"] != header["header_hash"]
            ):
                raise GeometryQaError("GEOMETRY_CONTINUATION_INVALID")
        block_styles: dict[tuple[str, int], Mapping[str, object]] = {}
        for block in page["blocks"]:
            expected = flow_styles.get(str(block["content_id"]))
            if expected is None or block["style"] != expected:
                raise GeometryQaError("GEOMETRY_FIXED_SIZE_INVALID")
            block_styles[(str(block["content_id"]), int(block["part_index"]))] = (
                expected
            )
        for run in plan_page["draw_runs"]:
            if run["content_kind"] == "continuation-label":
                if (
                    not isinstance(header, Mapping)
                    or run["size_mpt"] != header["style"]["size_mpt"]
                ):
                    raise GeometryQaError("GEOMETRY_FIXED_SIZE_INVALID")
            elif run["content_kind"] == "brand":
                continue
            else:
                style = block_styles.get(
                    (str(run["content_id"]), int(run["part_index"]))
                )
                if style is None or run["size_mpt"] != style["size_mpt"]:
                    raise GeometryQaError("GEOMETRY_FIXED_SIZE_INVALID")
    return {"continuation_page_count": continuation_count}


__all__ = [
    "GeometryQaError",
    "validate_a3_pages",
    "validate_bounds_and_overlap",
    "validate_continuation_and_sizes",
    "validate_leader_policy",
    "validate_mirrored_frames",
    "validate_source_left_one_to_one",
]
