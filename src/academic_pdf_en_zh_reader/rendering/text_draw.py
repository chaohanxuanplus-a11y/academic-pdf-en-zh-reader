# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Freeze and consume exact text paint runs without ever re-breaking text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, Decimal
from typing import Protocol

from reportlab.pdfbase import pdfmetrics

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.branding import validate_brand_block
from academic_pdf_en_zh_reader.rendering.contracts import (
    FrozenContinuationLabel,
    OverlayPlanError,
)

UNDERLINE_STYLE_VERSION = 1
UNDERLINE_OFFSET_MPT = -1_000
UNDERLINE_THICKNESS_MPT = 600


class FrozenTextCanvas(Protocol):
    """The exact small ReportLab surface used by the plan consumer."""

    def setFillColor(self, color: str) -> None:  # noqa: N802
        ...

    def setStrokeColor(self, color: str) -> None:  # noqa: N802
        ...

    def setLineWidth(self, width: float) -> None:  # noqa: N802
        ...

    def setFont(self, name: str, size: float) -> None:  # noqa: N802
        ...

    def drawString(  # noqa: N802
        self,
        x: float,
        y: float,
        text: str,
    ) -> None: ...

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> None: ...


def _mpt_ceil(value_pt: float) -> int:
    return int(
        (Decimal(str(value_pt)) * 1000).to_integral_value(rounding=ROUND_CEILING)
    )


def _composite_text(flow: Mapping[str, object]) -> str:
    segments = flow.get("composite_segments")
    if not isinstance(segments, list) or not segments:
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "annotated flow has no composite segments",
        )
    return "".join(str(segment["text"]) for segment in segments)


def _visible_range(
    text: str,
    start: int,
    end: int,
    visible: str,
) -> tuple[int, int]:
    raw = text[start:end]
    if raw.strip() != visible:
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "line text does not match its whitespace-complete composite range",
        )
    leading = len(raw) - len(raw.lstrip())
    visible_start = start + leading
    visible_end = visible_start + len(visible)
    if text[visible_start:visible_end] != visible:
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "visible line cannot be recovered from the frozen range",
        )
    return visible_start, visible_end


def _target_boundary_to_composite(
    segments: Sequence[Mapping[str, object]],
    target_offset: int,
) -> tuple[int, ...]:
    result: list[int] = []
    for segment in segments:
        if segment["kind"] != "target":
            continue
        target_start = int(segment["target_start"])
        target_end = int(segment["target_end"])
        if target_start <= target_offset <= target_end:
            result.append(
                int(segment["composite_start"]) + target_offset - target_start
            )
    return tuple(result)


def _segment_at(
    segments: Sequence[Mapping[str, object]],
    start: int,
    end: int,
) -> Mapping[str, object]:
    matches = [
        segment
        for segment in segments
        if int(segment["composite_start"]) <= start
        and end <= int(segment["composite_end"])
    ]
    if len(matches) != 1:
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "draw interval is not bound to exactly one composite segment",
        )
    return matches[0]


def _font_at(
    fonts: Sequence[tuple[int, int, Mapping[str, object]]],
    start: int,
    end: int,
) -> Mapping[str, object]:
    matches = [font for left, right, font in fonts if left <= start and end <= right]
    if len(matches) != 1:
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "draw interval is not bound to exactly one frozen font run",
        )
    return matches[0]


def _paint_for_interval(
    *,
    content_kind: str,
    segment: Mapping[str, object],
    target_start: int,
    target_end: int,
    styled_spans: Sequence[Mapping[str, object]],
) -> tuple[str, tuple[str, ...]]:
    if content_kind == "auxiliary":
        return "dark_orange", ()
    if segment["kind"] == "ambiguity-label":
        return "bright_red", ()
    dark = [
        span
        for span in styled_spans
        if span["kind"] == "dark-red-highlight"
        and int(span["target_start"]) <= target_start
        and target_end <= int(span["target_end"])
    ]
    bright_ids = tuple(
        str(span["annotation_id"])
        for span in styled_spans
        if span["kind"] == "bright-red-ambiguity"
        and int(span["target_start"]) <= target_start
        and target_end <= int(span["target_end"])
    )
    return ("dark_red" if dark else "body"), bright_ids


def _font_intervals(
    line: Mapping[str, object],
    visible_start: int,
) -> tuple[tuple[int, int, Mapping[str, object]], ...]:
    cursor = visible_start
    result: list[tuple[int, int, Mapping[str, object]]] = []
    for run in line["runs"]:  # type: ignore[index]
        text = str(run["text"])
        result.append((cursor, cursor + len(text), run))
        cursor += len(text)
    if cursor != visible_start + len(str(line["text"])):
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "font runs do not cover the visible line",
        )
    return tuple(result)


def _line_atoms(
    *,
    line: Mapping[str, object],
    flow: Mapping[str, object],
    composite_text: str,
) -> tuple[
    tuple[int, int],
    tuple[
        tuple[
            int,
            int,
            Mapping[str, object],
            Mapping[str, object],
            int,
            int,
        ],
        ...,
    ],
]:
    raw_start = int(line["composite_start"])
    raw_end = int(line["composite_end"])
    visible_start, visible_end = _visible_range(
        composite_text,
        raw_start,
        raw_end,
        str(line["text"]),
    )
    segments = flow["composite_segments"]  # type: ignore[index]
    styled = flow.get("styled_spans", [])
    fonts = _font_intervals(line, visible_start)
    boundaries = {visible_start, visible_end}
    for left, right, _font in fonts:
        boundaries.update((left, right))
    for segment in segments:
        boundaries.update(
            (
                max(visible_start, int(segment["composite_start"])),
                min(visible_end, int(segment["composite_end"])),
            )
        )
    for span in styled:
        for target_boundary in (
            int(span["target_start"]),
            int(span["target_end"]),
        ):
            boundaries.update(
                value
                for value in _target_boundary_to_composite(
                    segments,
                    target_boundary,
                )
                if visible_start <= value <= visible_end
            )
    ordered = sorted(
        value for value in boundaries if visible_start <= value <= visible_end
    )
    atoms: list[
        tuple[
            int,
            int,
            Mapping[str, object],
            Mapping[str, object],
            int,
            int,
        ]
    ] = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        if start == end:
            continue
        segment = _segment_at(segments, start, end)
        font = _font_at(fonts, start, end)
        if segment["kind"] == "target":
            target_start = int(segment["target_start"]) + (
                start - int(segment["composite_start"])
            )
            target_end = target_start + end - start
        else:
            target_start = target_end = int(segment["target_offset"])
        atoms.append((start, end, segment, font, target_start, target_end))
    if not atoms or atoms[0][0] != visible_start or atoms[-1][1] != visible_end:
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "atomic draw intervals do not cover the visible line",
        )
    return (visible_start, visible_end), tuple(atoms)


def _measure_atom_widths(
    atoms: Sequence[
        tuple[
            int,
            int,
            Mapping[str, object],
            Mapping[str, object],
            int,
            int,
        ]
    ],
    *,
    composite_text: str,
    size_mpt: int,
    frozen_line_width_mpt: int,
) -> tuple[int, ...]:
    cumulative = 0.0
    previous_mpt = 0
    widths: list[int] = []
    for start, end, _segment, font, _target_start, _target_end in atoms:
        font_name = str(font["font_name"])
        try:
            pdfmetrics.getFont(font_name)
        except KeyError as exc:
            raise OverlayPlanError(
                "FONT_NOT_REGISTERED",
                f"frozen font is not registered: {font_name}",
            ) from exc
        cumulative += pdfmetrics.stringWidth(
            composite_text[start:end],
            font_name,
            size_mpt / 1000,
        )
        cumulative_mpt = _mpt_ceil(cumulative)
        widths.append(cumulative_mpt - previous_mpt)
        previous_mpt = cumulative_mpt
    if previous_mpt != frozen_line_width_mpt or any(width <= 0 for width in widths):
        raise OverlayPlanError(
            "FROZEN_TEXT_MISMATCH",
            "frozen line width cannot be reproduced from its exact font runs",
        )
    return tuple(widths)


def _line_binding(
    *,
    block: Mapping[str, object],
    line: Mapping[str, object],
    visible_range: tuple[int, int],
) -> dict[str, object]:
    binding: dict[str, object] = {
        "content_id": block["content_id"],
        "unit_id": block["unit_id"],
        "content_kind": block["content_kind"],
        "part_index": block["part_index"],
        "line_index": line["index"],
        "target_start": line["target_start"],
        "target_end": line["target_end"],
        "composite_start": line["composite_start"],
        "composite_end": line["composite_end"],
        "visible_composite_start": visible_range[0],
        "visible_composite_end": visible_range[1],
        "synthetic_annotation_ids": list(line["synthetic_annotation_ids"]),
        "line_box_hash": line["line_box_hash"],
    }
    binding["line_binding_hash"] = sha256_canonical(binding)
    return binding


def freeze_page_text(
    page: Mapping[str, object],
    *,
    flows_by_content: Mapping[str, Mapping[str, object]],
    colors: Mapping[str, str],
    start_draw_order: int = 0,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """Map exact layout lines to immutable paint runs and underline segments."""

    bindings: list[dict[str, object]] = []
    draw_runs: list[dict[str, object]] = []
    underlines: list[dict[str, object]] = []
    draw_order = start_draw_order
    for block in page["blocks"]:  # type: ignore[index]
        content_id = str(block["content_id"])
        flow = flows_by_content.get(content_id)
        if flow is None:
            raise OverlayPlanError(
                "FROZEN_TEXT_MISMATCH",
                "layout content has no frozen flow",
            )
        composite = _composite_text(flow)
        styled = flow.get("styled_spans", [])
        style = block["style"]
        size_mpt = int(style["size_mpt"])
        for line in block["lines"]:
            visible, atoms = _line_atoms(
                line=line,
                flow=flow,
                composite_text=composite,
            )
            bindings.append(
                _line_binding(block=block, line=line, visible_range=visible)
            )
            widths = _measure_atom_widths(
                atoms,
                composite_text=composite,
                size_mpt=size_mpt,
                frozen_line_width_mpt=int(line["width_mpt"]),
            )
            cursor = int(line["x_mpt"])
            atomic_underlines: list[dict[str, object]] = []
            for atom, width in zip(atoms, widths, strict=True):
                start, end, segment, font, target_start, target_end = atom
                color_token, bright_ids = _paint_for_interval(
                    content_kind=str(block["content_kind"]),
                    segment=segment,
                    target_start=target_start,
                    target_end=target_end,
                    styled_spans=styled,
                )
                text = composite[start:end]
                draw: dict[str, object] = {
                    "draw_run_id": (
                        f"draw:p{int(page['page_number']):05d}:{draw_order:07d}"
                    ),
                    "draw_order": draw_order,
                    "content_id": content_id,
                    "unit_id": block["unit_id"],
                    "content_kind": block["content_kind"],
                    "part_index": block["part_index"],
                    "line_index": line["index"],
                    "style_id": style["style_id"],
                    "font_role": font["font_role"],
                    "font_name": font["font_name"],
                    "text": text,
                    "size_mpt": size_mpt,
                    "color_token": color_token,
                    "color_hex": colors[color_token],
                    "target_start": target_start,
                    "target_end": target_end,
                    "composite_start": start,
                    "composite_end": end,
                    "x_mpt": cursor,
                    "baseline_y_mpt": line["baseline_y_mpt"],
                    "width_mpt": width,
                    "bbox_mpt": [
                        cursor,
                        int(line["baseline_y_mpt"]) + int(line["descent_mpt"]),
                        cursor + width,
                        int(line["baseline_y_mpt"]) + int(line["ascent_mpt"]),
                    ],
                }
                draw["draw_run_hash"] = sha256_canonical(draw)
                draw_runs.append(draw)
                for annotation_id in bright_ids:
                    y = int(line["baseline_y_mpt"]) + UNDERLINE_OFFSET_MPT
                    atomic_underlines.append(
                        {
                            "annotation_id": annotation_id,
                            "content_id": content_id,
                            "unit_id": block["unit_id"],
                            "part_index": block["part_index"],
                            "line_index": line["index"],
                            "target_start": target_start,
                            "target_end": target_end,
                            "x_start_mpt": cursor,
                            "x_end_mpt": cursor + width,
                            "y_mpt": y,
                        }
                    )
                cursor += width
                draw_order += 1
            if cursor != int(line["x_mpt"]) + int(line["width_mpt"]):
                raise OverlayPlanError(
                    "FROZEN_TEXT_MISMATCH",
                    "draw runs do not reproduce the frozen line width",
                )
            for raw in atomic_underlines:
                underline: dict[str, object] = {
                    "underline_id": (
                        f"underline:p{int(page['page_number']):05d}:"
                        f"{len(underlines):07d}"
                    ),
                    **raw,
                    "color_token": "bright_red",
                    "color_hex": colors["bright_red"],
                    "thickness_mpt": UNDERLINE_THICKNESS_MPT,
                    "style_version": UNDERLINE_STYLE_VERSION,
                    "bbox_mpt": [
                        raw["x_start_mpt"],
                        int(raw["y_mpt"]) - (UNDERLINE_THICKNESS_MPT + 1) // 2,
                        raw["x_end_mpt"],
                        int(raw["y_mpt"]) + UNDERLINE_THICKNESS_MPT // 2,
                    ],
                }
                underline["underline_hash"] = sha256_canonical(underline)
                underlines.append(underline)
    return bindings, draw_runs, underlines


def _validate_frozen_page(page_plan: Mapping[str, object]) -> None:
    required = {
        "page_number",
        "source_page_number",
        "page_kind",
        "continuation_index",
        "continuation_label",
        "brand_block",
        "source_obstacle_count",
        "source_obstacles_hash",
        "line_bindings",
        "draw_runs",
        "underlines",
        "leader_routes",
        "page_plan_hash",
    }
    if set(page_plan) != required:
        raise OverlayPlanError("PLAN_TAMPERED", "page plan is incomplete")
    expected_page_hash = sha256_canonical(
        {key: value for key, value in page_plan.items() if key != "page_plan_hash"}
    )
    if page_plan["page_plan_hash"] != expected_page_hash:
        raise OverlayPlanError("PLAN_TAMPERED", "page plan hash is invalid")
    draw_runs = page_plan["draw_runs"]
    underlines = page_plan["underlines"]
    if not isinstance(draw_runs, list) or not isinstance(underlines, list):
        raise OverlayPlanError("PLAN_TAMPERED", "page drawing lists are invalid")
    line_bindings = page_plan["line_bindings"]
    leader_routes = page_plan["leader_routes"]
    if not isinstance(line_bindings, list) or not isinstance(leader_routes, list):
        raise OverlayPlanError("PLAN_TAMPERED", "page plan lists are invalid")
    raw_label = page_plan["continuation_label"]
    brand_block = validate_brand_block(page_plan["brand_block"])
    if brand_block is not None and page_plan["page_kind"] != "native":
        raise OverlayPlanError("PLAN_TAMPERED", "brand block page kind is invalid")
    if page_plan["page_kind"] == "native":
        if raw_label is not None or page_plan["continuation_index"] != 0:
            raise OverlayPlanError("PLAN_TAMPERED", "native page label is invalid")
    elif page_plan["page_kind"] == "continuation":
        if not isinstance(raw_label, Mapping):
            raise OverlayPlanError(
                "PLAN_TAMPERED",
                "continuation page label is not frozen",
            )
        try:
            label = FrozenContinuationLabel.from_mapping(raw_label)
        except OverlayPlanError as exc:
            raise OverlayPlanError(
                "PLAN_TAMPERED",
                "continuation label is invalid",
            ) from exc
        if (
            label.source_page_number != page_plan["source_page_number"]
            or label.continuation_index != page_plan["continuation_index"]
        ):
            raise OverlayPlanError(
                "PLAN_TAMPERED",
                "continuation label page binding is invalid",
            )
    else:
        raise OverlayPlanError("PLAN_TAMPERED", "page kind is invalid")
    for binding in line_bindings:
        if not isinstance(binding, Mapping) or binding.get(
            "line_binding_hash"
        ) != sha256_canonical(
            {key: value for key, value in binding.items() if key != "line_binding_hash"}
        ):
            raise OverlayPlanError("PLAN_TAMPERED", "line binding is invalid")
    run_ids: set[str] = set()
    for expected_order, run in enumerate(draw_runs):
        bbox = run.get("bbox_mpt") if isinstance(run, Mapping) else None
        if (
            not isinstance(run, Mapping)
            or run.get("draw_order") != expected_order
            or not isinstance(run.get("draw_run_id"), str)
            or not run["draw_run_id"]
            or run["draw_run_id"] in run_ids
            or not isinstance(run.get("text"), str)
            or not run["text"]
            or run.get("font_role") not in {"body", "heading", "symbols"}
            or not isinstance(run.get("font_name"), str)
            or not run["font_name"]
            or run.get("color_token")
            not in {
                "body",
                "dark_red",
                "dark_orange",
                "bright_red",
                "muted_gray",
            }
            or run.get("color_hex")
            != {
                "body": "#111111",
                "dark_red": "#7F1D1D",
                "dark_orange": "#A84F08",
                "bright_red": "#D00000",
                "muted_gray": "#666666",
            }.get(run.get("color_token"))
            or type(run.get("size_mpt")) is not int
            or int(run["size_mpt"]) <= 0
            or type(run.get("width_mpt")) is not int
            or int(run["width_mpt"]) <= 0
            or any(
                type(run.get(key)) is not int
                for key in (
                    "target_start",
                    "target_end",
                    "composite_start",
                    "composite_end",
                    "x_mpt",
                    "baseline_y_mpt",
                )
            )
            or int(run["target_end"]) < int(run["target_start"])
            or int(run["composite_end"]) - int(run["composite_start"])
            != len(str(run["text"]))
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(type(value) is not int for value in bbox)
            or bbox[0] != run["x_mpt"]
            or bbox[2] != int(run["x_mpt"]) + int(run["width_mpt"])
            or bbox[2] <= bbox[0]
            or bbox[3] <= bbox[1]
            or not bbox[1] <= int(run["baseline_y_mpt"]) <= bbox[3]
            or run.get("draw_run_hash")
            != sha256_canonical(
                {key: value for key, value in run.items() if key != "draw_run_hash"}
            )
        ):
            raise OverlayPlanError("PLAN_TAMPERED", "draw run is invalid")
        run_ids.add(str(run["draw_run_id"]))
    for underline in underlines:
        bbox = underline.get("bbox_mpt") if isinstance(underline, Mapping) else None
        if (
            not isinstance(underline, Mapping)
            or type(underline.get("x_start_mpt")) is not int
            or type(underline.get("x_end_mpt")) is not int
            or int(underline["x_end_mpt"]) <= int(underline["x_start_mpt"])
            or type(underline.get("y_mpt")) is not int
            or type(underline.get("thickness_mpt")) is not int
            or int(underline["thickness_mpt"]) <= 0
            or underline.get("color_token") != "bright_red"
            or underline.get("color_hex") != "#D00000"
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(type(value) is not int for value in bbox)
            or bbox[0] != underline["x_start_mpt"]
            or bbox[2] != underline["x_end_mpt"]
            or bbox[3] <= bbox[1]
            or underline.get("underline_hash")
            != sha256_canonical(
                {
                    key: value
                    for key, value in underline.items()
                    if key != "underline_hash"
                }
            )
        ):
            raise OverlayPlanError("PLAN_TAMPERED", "underline is invalid")
    for route in leader_routes:
        if not isinstance(route, Mapping) or route.get(
            "route_hash"
        ) != sha256_canonical(
            {key: value for key, value in route.items() if key != "route_hash"}
        ):
            raise OverlayPlanError("PLAN_TAMPERED", "leader route is invalid")
    brand_runs = [run for run in draw_runs if run.get("content_kind") == "brand"]
    if (brand_block is None and brand_runs) or (
        brand_block is not None
        and len(brand_runs) != int(brand_block["text_run_count"])
    ):
        raise OverlayPlanError("PLAN_TAMPERED", "brand text binding is invalid")


def _draw_validated_page(
    canvas: FrozenTextCanvas, page_plan: Mapping[str, object]
) -> None:
    """Internal draw step used only after whole-plan parent validation."""

    _validate_frozen_page(page_plan)
    for run in page_plan["draw_runs"]:  # type: ignore[index]
        canvas.setFillColor(str(run["color_hex"]))
        canvas.setFont(str(run["font_name"]), int(run["size_mpt"]) / 1000)
        canvas.drawString(
            int(run["x_mpt"]) / 1000,
            int(run["baseline_y_mpt"]) / 1000,
            str(run["text"]),
        )
    for underline in page_plan["underlines"]:  # type: ignore[index]
        canvas.setStrokeColor(str(underline["color_hex"]))
        canvas.setLineWidth(int(underline["thickness_mpt"]) / 1000)
        canvas.line(
            int(underline["x_start_mpt"]) / 1000,
            int(underline["y_mpt"]) / 1000,
            int(underline["x_end_mpt"]) / 1000,
            int(underline["y_mpt"]) / 1000,
        )


__all__ = [
    "UNDERLINE_OFFSET_MPT",
    "UNDERLINE_STYLE_VERSION",
    "UNDERLINE_THICKNESS_MPT",
    "freeze_page_text",
]
