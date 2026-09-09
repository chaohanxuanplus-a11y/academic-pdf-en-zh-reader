# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build the immutable pre-Canvas overlay plan from final frozen artifacts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.solver import (
    LayoutSolverError,
    validate_layout_against_frame_graph,
)
from academic_pdf_en_zh_reader.rendering.branding import (
    freeze_brand_block,
    load_brand_manifest,
    reference_only_regions,
)
from academic_pdf_en_zh_reader.rendering.contracts import (
    DEFAULT_OVERLAY_PLAN_LIMITS,
    FrozenContinuationHeader,
    FrozenContinuationLabel,
    OverlayPlanError,
    OverlayPlanLimits,
)
from academic_pdf_en_zh_reader.rendering.leaders import (
    LEADER_CLEARANCE_MPT,
    LEADER_COLOR_HEX,
    LEADER_DASH_MPT,
    LEADER_STYLE_VERSION,
    LEADER_WIDTH_MPT,
    LeaderRequest,
    Obstacle,
    freeze_leader_routes,
)
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.rendering.text_draw import (
    UNDERLINE_OFFSET_MPT,
    UNDERLINE_STYLE_VERSION,
    UNDERLINE_THICKNESS_MPT,
    freeze_page_text,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)

OVERLAY_PLAN_VERSION = 1
_COLORS = {
    "body": "#111111",
    "dark_red": "#7F1D1D",
    "dark_orange": "#A84F08",
    "bright_red": "#D00000",
    "muted_gray": "#666666",
}


def _complexity(observed: int, maximum: int, label: str) -> None:
    if observed > maximum:
        raise OverlayPlanError(
            "PLAN_COMPLEXITY_LIMIT",
            f"{label} observed {observed} exceeds {maximum}",
        )


def _validate_inputs(
    source: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    annotations: Mapping[str, object],
) -> None:
    raw_pages = layout.get("pages")
    if isinstance(raw_pages, list) and any(
        isinstance(page, Mapping) and type(page.get("continuation_label")) is bool
        for page in raw_pages
    ):
        raise OverlayPlanError(
            "CONTINUATION_LABEL_NOT_FROZEN",
            "layout contains a legacy boolean continuation label",
        )
    try:
        validate_artifact("source", source)
        validate_artifact("annotations", annotations)
    except SchemaValidationError as exc:
        raise OverlayPlanError("ARTIFACT_INVALID", str(exc)) from exc
    try:
        validate_artifact("frame-graph", frame_graph)
        validate_artifact("layout", layout)
    except SchemaValidationError as exc:
        raise OverlayPlanError("LAYOUT_GRAPH_MISMATCH", str(exc)) from exc
    binding = frame_graph.get("annotation_binding")
    if (
        frame_graph.get("line_mapping_version") != 2
        or layout.get("line_mapping_version") != 2
        or not isinstance(binding, Mapping)
        or binding.get("kind") != "final"
    ):
        raise OverlayPlanError(
            "FINAL_ANNOTATIONS_REQUIRED",
            "overlay planning requires a final annotated FrameGraph v2",
        )
    if binding.get("parent_hash") != sha256_canonical(annotations) or binding.get(
        "selection_hash"
    ) != annotations.get("selection_hash"):
        raise OverlayPlanError(
            "ANNOTATION_PARENT_MISMATCH",
            "FrameGraph is not bound to the supplied final annotations",
        )
    try:
        validate_layout_against_frame_graph(frame_graph, layout)
    except LayoutSolverError as exc:
        raise OverlayPlanError("LAYOUT_GRAPH_MISMATCH", str(exc)) from exc


def _flow_indexes(
    frame_graph: Mapping[str, object],
) -> tuple[
    dict[str, Mapping[str, object]],
    dict[str, Mapping[str, object]],
]:
    units = {
        str(flow["unit_id"]): flow
        for flow in frame_graph["unit_flows"]  # type: ignore[index]
    }
    content = dict(units)
    auxiliaries: dict[str, Mapping[str, object]] = {}
    for flow in frame_graph["auxiliary_flows"]:  # type: ignore[index]
        content[str(flow["id"])] = flow
        auxiliaries[str(flow["annotation_id"])] = flow
    return content, auxiliaries


def _validate_annotation_projection(
    frame_graph: Mapping[str, object],
    annotations: Mapping[str, object],
) -> None:
    """Reject a validly-shaped graph whose paint/aux content changed parent."""

    unit_flows = {
        str(flow["unit_id"]): flow
        for flow in frame_graph["unit_flows"]  # type: ignore[index]
    }
    _content, auxiliary_by_annotation = _flow_indexes(frame_graph)
    expected_styled: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    expected_labels: dict[str, tuple[str, int, str]] = {}
    expected_auxiliary: dict[str, Mapping[str, object]] = {}
    for item in annotations["items"]:  # type: ignore[index]
        identifier = str(item["id"])
        kind = str(item["kind"])
        unit_id = str(item["unit_id"])
        if unit_id not in unit_flows:
            raise OverlayPlanError(
                "ANNOTATION_PROJECTION_MISMATCH",
                "annotation targets an unknown frozen unit flow",
            )
        if kind in {"dark-red-highlight", "bright-red-ambiguity"}:
            expected_styled[unit_id].append(
                {
                    "annotation_id": identifier,
                    "kind": kind,
                    "target_start": item["target_start"],
                    "target_end": item["target_end"],
                    "color_token": (
                        "dark_red" if kind == "dark-red-highlight" else "bright_red"
                    ),
                    "underline": kind == "bright-red-ambiguity",
                }
            )
            if kind == "bright-red-ambiguity" and item.get("content") is not None:
                expected_labels[identifier] = (
                    unit_id,
                    int(item["target_end"]),
                    str(item["content"]),
                )
        elif kind in {"dark-orange-teaching", "figure-table-reading"}:
            expected_auxiliary[identifier] = item

    style_order = {"dark-red-highlight": 0, "bright-red-ambiguity": 1}
    for unit_id, flow in unit_flows.items():
        expected = sorted(
            expected_styled.get(unit_id, []),
            key=lambda item: (
                int(item["target_start"]),
                style_order[str(item["kind"])],
                str(item["annotation_id"]),
            ),
        )
        if flow.get("styled_spans") != expected:
            raise OverlayPlanError(
                "ANNOTATION_PROJECTION_MISMATCH",
                "styled spans differ from final annotations",
            )

    observed_labels: dict[str, tuple[str, int, str]] = {}
    for unit_id, flow in unit_flows.items():
        for segment in flow["composite_segments"]:  # type: ignore[index]
            if segment["kind"] != "ambiguity-label":
                continue
            observed_labels[str(segment["annotation_id"])] = (
                unit_id,
                int(segment["target_offset"]),
                str(segment["text"]),
            )
    if observed_labels != expected_labels:
        raise OverlayPlanError(
            "ANNOTATION_PROJECTION_MISMATCH",
            "ambiguity labels differ from final annotations",
        )

    if set(auxiliary_by_annotation) != set(expected_auxiliary):
        raise OverlayPlanError(
            "ANNOTATION_PROJECTION_MISMATCH",
            "auxiliary flows differ from final annotations",
        )
    for annotation_id, item in expected_auxiliary.items():
        flow = auxiliary_by_annotation[annotation_id]
        content = "".join(
            str(segment["text"])
            for segment in flow["composite_segments"]  # type: ignore[index]
        )
        if (
            flow["annotation_kind"] != item["kind"]
            or flow["unit_id"] != item["unit_id"]
            or flow["attachment"] != item["attachment"]
            or flow["attachment_target_start"] != item["target_start"]
            or flow["attachment_target_end"] != item["target_end"]
            or content != item["content"]
        ):
            raise OverlayPlanError(
                "ANNOTATION_PROJECTION_MISMATCH",
                "auxiliary content differs from final annotations",
            )


def _source_indexes(
    source: Mapping[str, object],
) -> tuple[
    dict[int, Mapping[str, object]],
    dict[tuple[int, str], Mapping[str, object]],
    dict[tuple[int, str], Mapping[str, object]],
]:
    pages: dict[int, Mapping[str, object]] = {}
    blocks: dict[tuple[int, str], Mapping[str, object]] = {}
    bands: dict[tuple[int, str], Mapping[str, object]] = {}
    for page in source["pages"]:  # type: ignore[index]
        number = int(page["page_number"])
        pages[number] = page
        for band in page["bands"]:
            bands[(number, str(band["id"]))] = band
        for block in page["blocks"]:
            blocks[(number, str(block["id"]))] = block
    return pages, blocks, bands


def _crop_relative_box(
    raw: Sequence[int],
    crop_box: Sequence[int],
) -> tuple[int, int, int, int]:
    return (
        int(raw[0]) - int(crop_box[0]),
        int(raw[1]) - int(crop_box[1]),
        int(raw[2]) - int(crop_box[0]),
        int(raw[3]) - int(crop_box[1]),
    )


def _source_obstacles(
    page: Mapping[str, object],
) -> tuple[tuple[Obstacle, ...], str]:
    crop = page["crop_box_mpt"]
    obstacles: list[Obstacle] = []
    for block in page["blocks"]:  # type: ignore[index]
        obstacles.append(
            Obstacle(
                str(block["id"]),
                "source-block",
                _crop_relative_box(block["bbox_mpt"], crop),
            )
        )
    for graphic in page["graphic_nodes"]:  # type: ignore[index]
        obstacles.append(
            Obstacle(
                f"graphic:{graphic['id']}",
                f"source-{graphic['kind']}",
                _crop_relative_box(graphic["bbox_mpt"], crop),
            )
        )
    obstacles.sort(key=lambda item: (item.obstacle_id, item.kind, item.bbox_mpt))
    payload = {
        "source_page_number": page["page_number"],
        "obstacles": [
            {
                "obstacle_id": obstacle.obstacle_id,
                "kind": obstacle.kind,
                "bbox_mpt": list(obstacle.bbox_mpt),
            }
            for obstacle in obstacles
        ],
    }
    return tuple(obstacles), sha256_canonical(payload)


def _recomputed_anchor(
    selected: Mapping[str, object],
    *,
    source_pages: Mapping[int, Mapping[str, object]],
    source_blocks: Mapping[tuple[int, str], Mapping[str, object]],
    source_bands: Mapping[tuple[int, str], Mapping[str, object]],
) -> dict[str, object]:
    page_number = int(selected["source_page_number"])
    page = source_pages.get(page_number)
    block = source_blocks.get((page_number, str(selected["source_block_id"])))
    if page is None or block is None:
        raise OverlayPlanError(
            "SOURCE_ANCHOR_MISMATCH",
            "selected anchor has no source block parent",
        )
    band = source_bands.get((page_number, str(block["band_id"])))
    if band is None:
        raise OverlayPlanError(
            "SOURCE_ANCHOR_MISMATCH",
            "selected anchor has no source band parent",
        )
    first_line = _crop_relative_box(
        block["first_line_bbox_mpt"],  # type: ignore[arg-type]
        page["crop_box_mpt"],  # type: ignore[arg-type]
    )
    visual_center = (first_line[1] + first_line[3]) // 2
    return {
        "kind": "leader" if len(band["columns"]) == 1 else "soft-y",
        "source_block_id": block["id"],
        "source_page_number": page_number,
        "source_band_id": block["band_id"],
        "source_visual_center_offset_mpt": (A3_LANDSCAPE_HEIGHT_MPT - visual_center),
        "source_first_line_bbox_mpt": list(first_line),
        "source_endpoint_mpt": [first_line[2], visual_center],
    }


def _right_obstacles(
    draw_runs: Sequence[Mapping[str, object]],
    underlines: Sequence[Mapping[str, object]],
) -> tuple[Obstacle, ...]:
    result = [
        Obstacle(
            str(run["draw_run_id"]),
            "right-glyphs",
            tuple(int(value) for value in run["bbox_mpt"]),
        )
        for run in draw_runs
    ]
    result.extend(
        Obstacle(
            str(underline["underline_id"]),
            "right-underline",
            tuple(int(value) for value in underline["bbox_mpt"]),
        )
        for underline in underlines
    )
    return tuple(result)


def _leader_requests(
    page: Mapping[str, object],
    *,
    draw_runs: Sequence[Mapping[str, object]],
    underlines: Sequence[Mapping[str, object]],
    source_pages: Mapping[int, Mapping[str, object]],
    source_blocks: Mapping[tuple[int, str], Mapping[str, object]],
    source_bands: Mapping[tuple[int, str], Mapping[str, object]],
) -> tuple[LeaderRequest, ...]:
    owned_ids_by_line: dict[tuple[str, int, int], list[str]] = defaultdict(list)
    for run in draw_runs:
        key = (
            str(run["content_id"]),
            int(run["part_index"]),
            int(run["line_index"]),
        )
        owned_ids_by_line[key].append(str(run["draw_run_id"]))
    for underline in underlines:
        key = (
            str(underline["content_id"]),
            int(underline["part_index"]),
            int(underline["line_index"]),
        )
        owned_ids_by_line[key].append(str(underline["underline_id"]))
    requests: list[LeaderRequest] = []
    for block in page["blocks"]:  # type: ignore[index]
        selected = block.get("selected_anchor")
        if selected is None:
            continue
        if int(block["part_index"]) != 0 or not block["creates_anchor"]:
            raise OverlayPlanError(
                "LAYOUT_GRAPH_MISMATCH",
                "only an actual first unit part may select an anchor",
            )
        expected = _recomputed_anchor(
            selected,
            source_pages=source_pages,
            source_blocks=source_blocks,
            source_bands=source_bands,
        )
        if dict(selected) != expected:
            raise OverlayPlanError(
                "SOURCE_ANCHOR_MISMATCH",
                "selected source endpoint differs from its source first-line bbox",
            )
        if selected["kind"] == "soft-y":
            continue
        if selected["kind"] != "leader" or block["content_kind"] != "unit":
            raise OverlayPlanError(
                "LAYOUT_GRAPH_MISMATCH",
                "selected anchor kind is invalid for this content",
            )
        first_line = block["lines"][0]
        target_y = (
            int(first_line["baseline_y_mpt"])
            + (int(first_line["ascent_mpt"]) + int(first_line["descent_mpt"])) // 2
        )
        owned_ids = tuple(
            owned_ids_by_line.get(
                (
                    str(block["content_id"]),
                    int(block["part_index"]),
                    int(first_line["index"]),
                ),
                (),
            )
        )
        requests.append(
            LeaderRequest(
                leader_id=f"leader:{block['unit_id']}",
                unit_id=str(block["unit_id"]),
                source_block_id=str(selected["source_block_id"]),
                source_endpoint_mpt=tuple(selected["source_endpoint_mpt"]),
                target_endpoint_mpt=(int(first_line["x_mpt"]), target_y),
                target_obstacle_ids=owned_ids,
            )
        )
    return tuple(requests)


def _continuation_header(
    frame_graph: Mapping[str, object],
) -> FrozenContinuationHeader | None:
    raw = frame_graph.get("continuation_header")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise OverlayPlanError(
            "CONTINUATION_HEADER_INVALID",
            "FrameGraph continuation header is not an object",
        )
    header = FrozenContinuationHeader.from_mapping(raw)
    font_names = {
        str(face["role"]): str(face["reportlab_name"])
        for face in frame_graph["font_fingerprint"]  # type: ignore[index]
    }
    if any(
        font_names.get(run.font_role) != run.font_name
        or run.font_role not in {header.style.font_role, "symbols"}
        for run in header.runs
    ):
        raise OverlayPlanError(
            "CONTINUATION_HEADER_INVALID",
            "continuation header runs differ from the font fingerprint",
        )
    panel = frame_graph["right_panel_bbox_mpt"]
    padding = int(frame_graph["flow_spacing"]["horizontal_padding_mpt"])
    if header.width_mpt > int(panel[2]) - int(
        panel[0]
    ) - 2 * padding or header.reserve_height_mpt >= int(panel[3]) - int(panel[1]):
        raise OverlayPlanError(
            "CONTINUATION_HEADER_INVALID",
            "continuation header does not fit the frozen right panel",
        )
    return header


def _validate_continuation_label(
    page: Mapping[str, object],
    *,
    header: FrozenContinuationHeader | None,
    frame_graph: Mapping[str, object],
) -> FrozenContinuationLabel | None:
    raw = page["continuation_label"]
    if type(raw) is bool:
        raise OverlayPlanError(
            "CONTINUATION_LABEL_NOT_FROZEN",
            "continuation page has only a legacy boolean label",
        )
    if raw is None:
        if page["page_kind"] == "continuation":
            raise OverlayPlanError(
                "CONTINUATION_LABEL_NOT_FROZEN",
                "continuation page has no exact frozen label",
            )
        return None
    if not isinstance(raw, Mapping):
        raise OverlayPlanError(
            "CONTINUATION_LABEL_NOT_FROZEN",
            "continuation label is not an exact frozen object",
        )
    if page["page_kind"] != "continuation" or header is None:
        raise OverlayPlanError(
            "CONTINUATION_LABEL_PARENT_MISMATCH",
            "continuation label has no matching FrameGraph header parent",
        )
    label = FrozenContinuationLabel.from_mapping(raw)
    expected_x = (
        int(frame_graph["right_panel_bbox_mpt"][2])  # type: ignore[index]
        - int(frame_graph["flow_spacing"]["horizontal_padding_mpt"])  # type: ignore[index]
        - header.width_mpt
    )
    expected_baseline = (
        int(page["page_height_mpt"]) - header.top_inset_mpt - header.ascent_mpt
    )
    if (
        label.header_hash != header.header_hash
        or label.source_page_number != page["source_page_number"]
        or label.continuation_index != page["continuation_index"]
        or label.text != header.text
        or label.style != header.style
        or label.runs != header.runs
        or label.width_mpt != header.width_mpt
        or label.line_height_mpt != header.line_height_mpt
        or label.ascent_mpt != header.ascent_mpt
        or label.descent_mpt != header.descent_mpt
        or label.color_token != header.color_token
        or label.color_hex != header.color_hex
        or label.x_mpt != expected_x
        or label.baseline_y_mpt != expected_baseline
    ):
        raise OverlayPlanError(
            "CONTINUATION_LABEL_PARENT_MISMATCH",
            "continuation label differs from its header/page recomputation",
        )
    return label


def _freeze_continuation_label(
    label: FrozenContinuationLabel | None,
    *,
    output_page_number: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if label is None:
        return [], []
    content_id = (
        f"continuation-label:p{label.source_page_number}:{label.continuation_index}"
    )
    binding: dict[str, object] = {
        "content_id": content_id,
        "unit_id": content_id,
        "content_kind": "continuation-label",
        "part_index": 0,
        "line_index": 0,
        "target_start": 0,
        "target_end": 0,
        "composite_start": 0,
        "composite_end": len(label.text),
        "visible_composite_start": 0,
        "visible_composite_end": len(label.text),
        "synthetic_annotation_ids": ["continuation-header"],
        "line_box_hash": label.label_hash,
    }
    binding["line_binding_hash"] = sha256_canonical(binding)
    draw_runs: list[dict[str, object]] = []
    composite_cursor = 0
    for run in label.runs:
        x_mpt = label.x_mpt + run.x_offset_mpt
        draw: dict[str, object] = {
            "draw_run_id": (f"draw:p{output_page_number:05d}:{run.run_index:07d}"),
            "draw_order": run.run_index,
            "content_id": content_id,
            "unit_id": content_id,
            "content_kind": "continuation-label",
            "part_index": 0,
            "line_index": 0,
            "style_id": label.style.style_id,
            "font_role": run.font_role,
            "font_name": run.font_name,
            "text": run.text,
            "size_mpt": label.style.size_mpt,
            "color_token": label.color_token,
            "color_hex": label.color_hex,
            "target_start": 0,
            "target_end": 0,
            "composite_start": composite_cursor,
            "composite_end": composite_cursor + len(run.text),
            "x_mpt": x_mpt,
            "baseline_y_mpt": label.baseline_y_mpt,
            "width_mpt": run.width_mpt,
            "bbox_mpt": [
                x_mpt,
                label.baseline_y_mpt + label.descent_mpt,
                x_mpt + run.width_mpt,
                label.baseline_y_mpt + label.ascent_mpt,
            ],
        }
        draw["draw_run_hash"] = sha256_canonical(draw)
        draw_runs.append(draw)
        composite_cursor += len(run.text)
    return [binding], draw_runs


def _render_style() -> dict[str, object]:
    return {
        "version": 1,
        "colors": dict(_COLORS),
        "underline": {
            "version": UNDERLINE_STYLE_VERSION,
            "offset_mpt": UNDERLINE_OFFSET_MPT,
            "thickness_mpt": UNDERLINE_THICKNESS_MPT,
        },
        "leader": {
            "version": LEADER_STYLE_VERSION,
            "color_hex": LEADER_COLOR_HEX,
            "dash_mpt": list(LEADER_DASH_MPT),
            "width_mpt": LEADER_WIDTH_MPT,
            "clearance_mpt": LEADER_CLEARANCE_MPT,
        },
    }


def build_overlay_plan(
    source: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    annotations: Mapping[str, object],
    *,
    limits: OverlayPlanLimits = DEFAULT_OVERLAY_PLAN_LIMITS,
) -> dict[str, object]:
    """Freeze the complete text/underline/leader plan or fail before Canvas."""

    if not isinstance(limits, OverlayPlanLimits):
        raise OverlayPlanError("PLAN_COMPLEXITY_LIMIT", "overlay limits are invalid")
    _validate_inputs(source, frame_graph, layout, annotations)
    _validate_annotation_projection(frame_graph, annotations)
    layout_pages = layout["pages"]  # type: ignore[index]
    _complexity(len(layout_pages), limits.max_pages, "pages")
    content_flows, _auxiliary = _flow_indexes(frame_graph)
    source_pages, source_blocks, source_bands = _source_indexes(source)
    continuation_header = _continuation_header(frame_graph)
    brand_manifest, _brand_asset_path, brand_manifest_sha256 = load_brand_manifest()
    brand_layout = brand_manifest["layout"]
    brand_regions = reference_only_regions(
        source,
        layout,
        page_margin_mpt=int(brand_layout["page_margin_mpt"]),
        heading_gap_mpt=int(brand_layout["section_gap_mpt"]),
    )
    branded_page_number = None

    total_lines = 0
    total_characters = 0
    total_draw_runs = 0
    total_source_obstacles = 0
    total_leaders = 0
    total_selected_anchors = 0
    pages: list[dict[str, object]] = []
    for page_index, page in enumerate(layout_pages):
        continuation_label = _validate_continuation_label(
            page,
            header=continuation_header,
            frame_graph=frame_graph,
        )
        _complexity(
            len(page["blocks"]),
            limits.max_blocks_per_page,
            "blocks per page",
        )
        total_selected_anchors += sum(
            block.get("selected_anchor") is not None for block in page["blocks"]
        )
        _complexity(
            total_selected_anchors,
            limits.max_leaders,
            "selected anchors",
        )
        page_lines = [line for block in page["blocks"] for line in block["lines"]]
        total_lines += len(page_lines)
        total_characters += sum(len(str(line["text"])) for line in page_lines)
        _complexity(total_lines, limits.max_lines, "lines")
        _complexity(total_characters, limits.max_characters, "characters")

        source_page = source_pages.get(int(page["source_page_number"]))
        if source_page is None:
            raise OverlayPlanError(
                "SOURCE_ANCHOR_MISMATCH",
                "layout output page has no source page parent",
            )
        source_obstacles, source_obstacles_hash = _source_obstacles(source_page)
        total_source_obstacles += len(source_obstacles)
        _complexity(
            total_source_obstacles,
            limits.max_source_obstacles,
            "source obstacles",
        )
        header_bindings, header_runs = _freeze_continuation_label(
            continuation_label,
            output_page_number=int(page["page_number"]),
        )
        line_bindings, draw_runs, underlines = freeze_page_text(
            page,
            flows_by_content=content_flows,
            colors=_COLORS,
            start_draw_order=len(header_runs),
        )
        line_bindings = [*header_bindings, *line_bindings]
        draw_runs = [*header_runs, *draw_runs]
        brand_block = None
        brand_region = brand_regions.get(int(page["page_number"]))
        if (
            page_index == len(layout_pages) - 1
            and branded_page_number is None
            and brand_region is not None
        ):
            frozen_brand = freeze_brand_block(
                output_page_number=int(page["page_number"]),
                start_draw_order=len(draw_runs),
                manifest=brand_manifest,
                manifest_sha256=brand_manifest_sha256,
                available_y_mpt=brand_region,
            )
            if frozen_brand is not None:
                brand_block, brand_bindings, brand_runs = frozen_brand
                branded_page_number = int(page["page_number"])
                line_bindings.extend(brand_bindings)
                draw_runs.extend(brand_runs)
        total_draw_runs += len(draw_runs)
        _complexity(total_draw_runs, limits.max_draw_runs, "draw runs")
        requests = _leader_requests(
            page,
            draw_runs=draw_runs,
            underlines=underlines,
            source_pages=source_pages,
            source_blocks=source_blocks,
            source_bands=source_bands,
        )
        total_leaders += len(requests)
        _complexity(total_leaders, limits.max_leaders, "leaders")
        routes = freeze_leader_routes(
            requests,
            source_obstacles=source_obstacles,
            right_obstacles=_right_obstacles(draw_runs, underlines),
            page_bbox_mpt=(
                0,
                0,
                A3_LANDSCAPE_WIDTH_MPT,
                A3_LANDSCAPE_HEIGHT_MPT,
            ),
            right_panel_left_mpt=A4_WIDTH_MPT,
            max_collision_checks=limits.max_leader_collision_checks,
        )
        page_plan: dict[str, object] = {
            "page_number": page["page_number"],
            "source_page_number": page["source_page_number"],
            "page_kind": page["page_kind"],
            "continuation_index": page["continuation_index"],
            "continuation_label": (
                None if continuation_label is None else continuation_label.to_mapping()
            ),
            "brand_block": brand_block,
            "source_obstacle_count": len(source_obstacles),
            "source_obstacles_hash": source_obstacles_hash,
            "line_bindings": line_bindings,
            "draw_runs": draw_runs,
            "underlines": underlines,
            "leader_routes": routes,
        }
        page_plan["page_plan_hash"] = sha256_canonical(page_plan)
        pages.append(page_plan)

    appended_page_number = None
    if branded_page_number is None:
        appended_page_number = len(pages) + 1
        _complexity(appended_page_number, limits.max_pages, "pages")
        frozen_brand = freeze_brand_block(
            output_page_number=appended_page_number,
            start_draw_order=0,
            manifest=brand_manifest,
            manifest_sha256=brand_manifest_sha256,
            dedicated_page=True,
        )
        if frozen_brand is None:
            raise OverlayPlanError(
                "BRAND_LAYOUT_INVALID", "disclaimer page does not fit"
            )
        brand_block, brand_bindings, brand_runs = frozen_brand
        _complexity(total_lines + len(brand_bindings), limits.max_lines, "lines")
        _complexity(
            total_characters + sum(len(str(run["text"])) for run in brand_runs),
            limits.max_characters,
            "characters",
        )
        _complexity(
            total_draw_runs + len(brand_runs), limits.max_draw_runs, "draw runs"
        )
        disclaimer_page: dict[str, object] = {
            "page_number": appended_page_number,
            "source_page_number": None,
            "page_kind": "disclaimer",
            "continuation_index": 0,
            "continuation_label": None,
            "brand_block": brand_block,
            "source_obstacle_count": 0,
            "source_obstacles_hash": sha256_canonical([]),
            "line_bindings": brand_bindings,
            "draw_runs": brand_runs,
            "underlines": [],
            "leader_routes": [],
        }
        disclaimer_page["page_plan_hash"] = sha256_canonical(disclaimer_page)
        pages.append(disclaimer_page)
        branded_page_number = appended_page_number

    plan: dict[str, object] = {
        "overlay_plan_version": OVERLAY_PLAN_VERSION,
        "artifact_kind": "overlay-plan",
        "source_hash": sha256_canonical(source),
        "frame_graph_hash": sha256_canonical(frame_graph),
        "layout_hash": sha256_canonical(layout),
        "annotations_hash": sha256_canonical(annotations),
        "annotation_selection_hash": annotations["selection_hash"],
        "continuation_header_hash": (
            None if continuation_header is None else continuation_header.header_hash
        ),
        "font_fingerprint": list(frame_graph["font_fingerprint"]),
        "render_style": _render_style(),
        "branding": {
            "manifest_sha256": brand_manifest_sha256,
            "asset_sha256": brand_manifest["image"]["sha256"],
            "policy_version": brand_manifest["layout"]["policy_version"],
            "rendered_page_number": branded_page_number,
            "appended_page_number": appended_page_number,
        },
        "pages": pages,
    }
    plan["overlay_plan_hash"] = sha256_canonical(plan)
    return plan


__all__ = ["OVERLAY_PLAN_VERSION", "build_overlay_plan"]
