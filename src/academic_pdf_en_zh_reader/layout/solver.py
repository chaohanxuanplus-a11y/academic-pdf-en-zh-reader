# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Linear pagination of measured prose and attached notes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


class LayoutSolverError(ValueError):
    pass


class LayoutInfeasibleError(LayoutSolverError):
    pass


class LayoutComplexityError(LayoutSolverError):
    code = "LAYOUT_COMPLEXITY_LIMIT"


@dataclass(frozen=True, slots=True)
class LayoutLimits:
    version: int = 2
    max_pages: int = 2_000
    max_lines: int = 1_000_000
    heading_with_next_lines: int = 2

    def __post_init__(self):
        if self.version != 2 or any(
            type(v) is not int or v < 1 for v in asdict(self).values()
        ):
            raise LayoutSolverError("invalid layout limits")


DEFAULT_LAYOUT_LIMITS = LayoutLimits()


def _paginate(graph, limits, *, warning_page=None):
    config = graph["flow_spacing"]
    margin_x, margin_y = (
        config["horizontal_padding_mpt"],
        config["vertical_padding_mpt"],
    )
    width = (A4_WIDTH_MPT - 2 * margin_x - config["column_gap_mpt"]) // 2
    page_height = A3_LANDSCAPE_HEIGHT_MPT
    left = A4_WIDTH_MPT + margin_x
    source_count = graph["source_page_count"]
    flows = {f["unit_id"]: f for f in graph["unit_flows"]}
    flows.update({f["id"]: f for f in graph["auxiliary_flows"]})
    if sum(f["line_count"] for f in flows.values()) > limits.max_lines:
        raise LayoutComplexityError("too many measured lines")
    pages, part_index = [], defaultdict(int)
    column, top, start_top, current_columns = 0, margin_y, margin_y, 1

    def new_page():
        if len(pages) >= limits.max_pages:
            raise LayoutComplexityError("too many output pages")
        number = len(pages) + 1
        pages.append(
            {
                "page_number": number,
                "source_page_number": number if number <= source_count else None,
                "page_kind": "native" if number <= source_count else "continuation",
                "continuation_index": max(0, number - source_count),
                "continuation_label": None,
                "frames": [],
                "blocks": [],
                "warning_region_mpt": None,
            }
        )

    new_page()

    def next_column():
        nonlocal column, top, start_top
        if current_columns == 2 and column == 0:
            column, top = 1, start_top
        else:
            new_page()
            column, top, start_top = 0, margin_y, margin_y

    previous_uid = None
    for content_id in graph["flow_order"]:
        flow = flows[content_id]
        content_kind = "unit" if content_id == flow["unit_id"] else "auxiliary"
        columns = flow["column_count"]
        if columns != current_columns:
            # A spanning item starts below everything already placed on the page.
            top = max(
                [page_height - b["bbox_mpt"][1] for b in pages[-1]["blocks"]] + [top]
            )
            start_top, column, current_columns = top, 0, columns
        if previous_uid is not None and previous_uid != flow["unit_id"]:
            top += config["block_gap_mpt"]
        previous_uid = flow["unit_id"]
        lines, cursor = flow["lines"], 0
        while cursor < len(lines):
            bottom = margin_y
            if len(pages) == warning_page:
                bottom += graph["warning_height_mpt"] + config["block_gap_mpt"]
            if cursor == 0 and flow["role"] == "heading":
                reserve = (
                    sum(line["line_height_mpt"] for line in lines)
                    + limits.heading_with_next_lines * 13_000
                    + config["block_gap_mpt"]
                )
                if (
                    reserve <= page_height - margin_y - bottom
                    and top + reserve > page_height - bottom
                ):
                    next_column()
                    continue
            capacity = page_height - bottom - top
            end, height = cursor, 0
            while (
                end < len(lines) and height + lines[end]["line_height_mpt"] <= capacity
            ):
                height += lines[end]["line_height_mpt"]
                end += 1
            if end == cursor:
                if top == margin_y:
                    raise LayoutInfeasibleError("one line exceeds a full column")
                next_column()
                continue
            x = (
                left + (width + config["column_gap_mpt"]) * column
                if columns == 2
                else left
            )
            max_width = width if columns == 2 else A4_WIDTH_MPT - 2 * margin_x
            frame_id = f"p{len(pages)}:frame:{len(pages[-1]['frames'])}"
            frame = {
                "id": frame_id,
                "column_index": column,
                "column_count": columns,
                "bbox_mpt": [x, bottom, x + max_width, page_height - top],
                "text_left_mpt": x,
                "text_right_mpt": x + max_width,
            }
            pages[-1]["frames"].append(frame)
            placed_lines, y = [], top
            for line in lines[cursor:end]:
                placed_lines.append(
                    {
                        **line,
                        "x_mpt": x,
                        "baseline_y_mpt": page_height - y - line["ascent_mpt"],
                    }
                )
                y += line["line_height_mpt"]
            part = part_index[content_id]
            part_index[content_id] += 1
            pages[-1]["blocks"].append(
                {
                    "id": f"layout:{content_id}:part:{part:04d}",
                    "content_id": content_id,
                    "content_kind": content_kind,
                    "unit_id": flow["unit_id"],
                    "part_index": part,
                    "frame_id": frame_id,
                    "line_start": cursor,
                    "line_end": end,
                    "line_sequence_hash": flow["line_sequence_hash"],
                    "style": flow["style"],
                    "target_page_number": len(pages),
                    "source_page_numbers": flow["source_page_numbers"],
                    "column_count": columns,
                    "column_index": column,
                    "bbox_mpt": [
                        x,
                        page_height - top - height,
                        x + max(line["width_mpt"] for line in lines[cursor:end]),
                        page_height - top,
                    ],
                    "lines": placed_lines,
                }
            )
            top += height
            cursor = end
            if cursor < len(lines):
                next_column()
    # Original pages are retained even when the Chinese stream ends earlier.
    while len(pages) < source_count:
        new_page()
    while warning_page is not None and len(pages) < warning_page:
        new_page()
    return pages


def _pages_with_warning(graph, limits):
    pages = _paginate(graph, limits)
    final_page = len(pages)
    pages = _paginate(graph, limits, warning_page=final_page)
    if len(pages) > final_page:
        # Content fits the unreserved pages; one additional page is sufficient.
        pages = _paginate(graph, limits, warning_page=final_page + 1)
    config = graph["flow_spacing"]
    warning_top = min(
        [b["bbox_mpt"][1] for b in pages[-1]["blocks"]]
        + [A3_LANDSCAPE_HEIGHT_MPT - config["vertical_padding_mpt"]]
    )
    if pages[-1]["blocks"]:
        warning_top -= config["block_gap_mpt"]
    pages[-1]["warning_region_mpt"] = [
        warning_top - graph["warning_height_mpt"],
        warning_top,
    ]
    return pages


def solve_layout(
    frame_graph, *, limits=DEFAULT_LAYOUT_LIMITS, expected_solver_input_hash=None
):
    validate_artifact("frame-graph", frame_graph)
    pages = _pages_with_warning(frame_graph, limits)
    # Reference-only source pages do not inflate the Chinese space budget.
    compact_pages = (
        pages
        if frame_graph["source_page_count"] == 1
        else _pages_with_warning({**frame_graph, "source_page_count": 1}, limits)
    )
    count = len(compact_pages)
    result = {
        "schema_version": "2.0.0",
        "artifact_kind": "layout",
        "solver_contract_version": 2,
        "line_mapping_version": 2,
        "solver_input_hash": sha256_canonical(
            {"graph": frame_graph, "limits": asdict(limits)}
        ),
        "frame_graph_input_hash": frame_graph["frame_graph_input_hash"],
        "frame_graph_hash": sha256_canonical(frame_graph),
        "right_panel_bbox_mpt": frame_graph["right_panel_bbox_mpt"],
        "font_fingerprint": frame_graph["font_fingerprint"],
        "flow_spacing": frame_graph["flow_spacing"],
        "solver_policy": asdict(limits),
        "solver_trace": {
            "continuation_page_count": len(pages) - frame_graph["source_page_count"],
            "chinese_page_count": count,
            "body_page_budget": frame_graph["body_page_budget"],
            "budget_exceeded": count > frame_graph["body_page_budget"],
        },
        "pages": pages,
    }
    validate_artifact("layout", result)
    if (
        expected_solver_input_hash is not None
        and expected_solver_input_hash != result["solver_input_hash"]
    ):
        raise LayoutSolverError("expected solver input hash does not match")
    return result


def validate_layout_against_frame_graph(frame_graph, layout):
    validate_artifact("layout", layout)
    expected = solve_layout(frame_graph, limits=LayoutLimits(**layout["solver_policy"]))
    if layout != expected:
        raise LayoutSolverError("layout differs from measured continuous flow")
