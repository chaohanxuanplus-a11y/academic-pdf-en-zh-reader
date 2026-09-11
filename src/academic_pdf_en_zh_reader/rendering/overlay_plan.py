# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Freeze vector text and the budgeted final responsibility statement."""

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.solver import validate_layout_against_frame_graph
from academic_pdf_en_zh_reader.rendering.branding import (
    freeze_brand_block,
    load_brand_manifest,
)
from academic_pdf_en_zh_reader.rendering.contracts import (
    DEFAULT_OVERLAY_PLAN_LIMITS,
    OverlayPlanError,
)
from academic_pdf_en_zh_reader.rendering.text_draw import (
    UNDERLINE_OFFSET_MPT,
    UNDERLINE_STYLE_VERSION,
    UNDERLINE_THICKNESS_MPT,
    freeze_page_text,
)

OVERLAY_PLAN_VERSION = 2
_COLORS = {
    "body": "#111111",
    "dark_red": "#7F1D1D",
    "dark_orange": "#A84F08",
    "bright_red": "#D00000",
    "muted_gray": "#666666",
}


def build_overlay_plan(
    source, frame_graph, layout, annotations, *, limits=DEFAULT_OVERLAY_PLAN_LIMITS
):
    if frame_graph.get("source_hash") != sha256_canonical(source):
        raise OverlayPlanError(
            "SOURCE_PARENT_MISMATCH", "source differs from measured graph"
        )
    try:
        validate_layout_against_frame_graph(frame_graph, layout)
    except ValueError as exc:
        raise OverlayPlanError(
            "LAYOUT_PARENT_MISMATCH", "layout differs from measured graph"
        ) from exc
    if frame_graph["annotation_binding"] != {
        "kind": "final",
        "parent_hash": sha256_canonical(annotations),
        "selection_hash": annotations["selection_hash"],
    }:
        raise OverlayPlanError(
            "ANNOTATION_PARENT_MISMATCH", "annotations differ from measured input"
        )
    manifest, _, manifest_hash = load_brand_manifest()
    if manifest_hash != frame_graph["brand_manifest_hash"]:
        raise OverlayPlanError(
            "BRAND_PARENT_MISMATCH", "statement changed after pagination"
        )
    if len(layout["pages"]) > limits.max_pages:
        raise OverlayPlanError("PLAN_COMPLEXITY_LIMIT", "too many pages")
    flows = {f["unit_id"]: f for f in frame_graph["unit_flows"]}
    flows.update({f["id"]: f for f in frame_graph["auxiliary_flows"]})
    pages, draw_count, char_count = [], 0, 0
    for page in layout["pages"]:
        bindings, runs, underlines = freeze_page_text(
            page, flows_by_content=flows, colors=_COLORS
        )
        brand = None
        if page["warning_region_mpt"] is not None:
            frozen = freeze_brand_block(
                output_page_number=page["page_number"],
                start_draw_order=len(runs),
                manifest=manifest,
                manifest_sha256=manifest_hash,
                available_y_mpt=tuple(page["warning_region_mpt"]),
            )
            if frozen is None:
                raise OverlayPlanError(
                    "BRAND_LAYOUT_INVALID",
                    "statement does not fit its measured reservation",
                )
            brand, brand_bindings, brand_runs = frozen
            bindings.extend(brand_bindings)
            runs.extend(brand_runs)
        draw_count += len(runs)
        char_count += sum(len(r["text"]) for r in runs)
        if draw_count > limits.max_draw_runs or char_count > limits.max_characters:
            raise OverlayPlanError("PLAN_COMPLEXITY_LIMIT", "drawing limits exceeded")
        record = {
            "page_number": page["page_number"],
            "source_page_number": page["source_page_number"],
            "page_kind": page["page_kind"],
            "continuation_index": page["continuation_index"],
            "continuation_label": None,
            "brand_block": brand,
            "line_bindings": bindings,
            "draw_runs": runs,
            "underlines": underlines,
        }
        record["page_plan_hash"] = sha256_canonical(record)
        pages.append(record)
    plan = {
        "overlay_plan_version": OVERLAY_PLAN_VERSION,
        "artifact_kind": "overlay-plan",
        "source_hash": sha256_canonical(source),
        "frame_graph_hash": sha256_canonical(frame_graph),
        "layout_hash": sha256_canonical(layout),
        "annotations_hash": sha256_canonical(annotations),
        "annotation_selection_hash": annotations["selection_hash"],
        "font_fingerprint": frame_graph["font_fingerprint"],
        "render_style": {
            "version": 2,
            "colors": _COLORS,
            "underline": {
                "version": UNDERLINE_STYLE_VERSION,
                "offset_mpt": UNDERLINE_OFFSET_MPT,
                "thickness_mpt": UNDERLINE_THICKNESS_MPT,
            },
        },
        "branding": {
            "manifest_sha256": manifest_hash,
            "asset_sha256": manifest["image"]["sha256"],
            "policy_version": manifest["layout"]["policy_version"],
            "rendered_page_number": len(pages),
        },
        "pages": pages,
    }
    plan["overlay_plan_hash"] = sha256_canonical(plan)
    return plan
