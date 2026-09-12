# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Measure a continuous reading stream, independently of source geometry."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from academic_pdf_en_zh_reader.extraction.unit_mapping import validate_unit_mapping
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.unit_parts import (
    measure_auxiliary_lines,
    measure_composite_target_lines,
    style_descriptor,
)
from academic_pdf_en_zh_reader.rendering.branding import (
    freeze_brand_block,
    load_brand_manifest,
)
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


class FrameGraphError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FrameGraphConfig:
    version: int = 3
    horizontal_padding_mpt: int = 32_000
    vertical_padding_mpt: int = 32_000
    column_gap_mpt: int = 16_000
    block_gap_mpt: int = 3_500
    figure_note_gap_mpt: int = 0

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in asdict(self).values()):
            raise FrameGraphError("invalid spacing")
        if (
            self.version != 3
            or 2 * self.horizontal_padding_mpt + self.column_gap_mpt >= A4_WIDTH_MPT
        ):
            raise FrameGraphError("invalid column geometry")


DEFAULT_FRAME_GRAPH_CONFIG = FrameGraphConfig()


def _construct(
    source,
    units,
    translation,
    *,
    style_contract,
    resolver,
    annotation_binding=None,
    annotation_items=(),
    config=DEFAULT_FRAME_GRAPH_CONFIG,
):
    validate_unit_mapping(source, units)
    validate_translation_artifact(units, translation)
    translated = {u["unit_id"]: u for u in translation["units"]}
    locations = {
        b["id"]: p["page_number"] for p in source["pages"] for b in p["blocks"]
    }
    full_width = A4_WIDTH_MPT - 2 * config.horizontal_padding_mpt
    column_width = (full_width - config.column_gap_mpt) // 2
    labels, styles, notes = defaultdict(list), defaultdict(list), defaultdict(list)
    for item in annotation_items:
        kind, uid = item["kind"], item["unit_id"]
        if kind in {"dark-red-highlight", "bright-red-ambiguity"}:
            styles[uid].append(
                {
                    "annotation_id": item["id"],
                    "kind": kind,
                    "target_start": item["target_start"],
                    "target_end": item["target_end"],
                    "color_token": "dark_red"
                    if kind == "dark-red-highlight"
                    else "bright_red",
                    "underline": kind == "bright-red-ambiguity",
                }
            )
            if kind == "bright-red-ambiguity" and item["content"]:
                labels[uid].append(
                    {
                        "annotation_id": item["id"],
                        "target_offset": item["target_end"],
                        "text": item["content"],
                    }
                )
        else:
            notes[uid].append(item)
    unit_flows, auxiliary_flows, order = [], [], []
    for unit in units["units"]:
        uid, role = unit["id"], unit["role"]
        text = translated[uid]["chinese_text"]
        columns = 1 if role in {"title", "abstract", "keywords"} else 2
        width = full_width if columns == 1 else column_width
        style = style_contract.style_for(role)
        lines, segments = measure_composite_target_lines(
            text,
            labels[uid],
            maximum_width_mpt=width,
            resolver=resolver,
            style=style,
            semantic_role=role,
            style_contract_version=style_contract.version,
        )
        source_pages = sorted({locations[f["block_id"]] for f in unit["fragments"]})
        flow = {
            "unit_id": uid,
            "role": role,
            "column_count": columns,
            "source_page_numbers": source_pages,
            "base_target_length": len(text),
            "style": style_descriptor(
                semantic_role=role,
                style=style,
                style_contract_version=style_contract.version,
            ),
            "lines": list(lines),
            "line_count": len(lines),
            "composite_segments": list(segments),
            "composite_length": segments[-1]["composite_end"],
            "styled_spans": styles[uid],
        }
        flow["line_sequence_hash"] = sha256_canonical(
            {"style": flow["style"], "lines": lines, "composite_segments": segments}
        )
        unit_flows.append(flow)
        order.append(uid)
        for item in notes[uid]:
            aux_style = style_contract.style_for("auxiliary")
            aux_lines, aux_segments = measure_auxiliary_lines(
                item["content"],
                annotation_id=item["id"],
                target_offset=len(text),
                maximum_width_mpt=width,
                resolver=resolver,
                style=aux_style,
                style_contract_version=style_contract.version,
            )
            aux = {
                "id": "aux:" + item["id"],
                "unit_id": uid,
                "role": "auxiliary",
                "annotation_id": item["id"],
                "annotation_kind": item["kind"],
                "column_count": columns,
                "source_page_numbers": source_pages,
                "parent_target_length": len(text),
                "attachment": "below-translation",
                "style": style_descriptor(
                    semantic_role="auxiliary",
                    style=aux_style,
                    style_contract_version=style_contract.version,
                ),
                "lines": list(aux_lines),
                "line_count": len(aux_lines),
                "composite_segments": list(aux_segments),
                "composite_length": len(item["content"]),
                "styled_spans": [],
            }
            aux["line_sequence_hash"] = sha256_canonical(
                {
                    "style": aux["style"],
                    "lines": aux_lines,
                    "composite_segments": aux_segments,
                }
            )
            auxiliary_flows.append(aux)
            order.append(aux["id"])
    manifest, _, manifest_hash = load_brand_manifest()
    if manifest["layout"]["page_margin_mpt"] != config.vertical_padding_mpt:
        raise FrameGraphError("statement and reading flow must share a safe margin")
    warning = freeze_brand_block(
        output_page_number=1,
        start_draw_order=0,
        manifest=manifest,
        manifest_sha256=manifest_hash,
    )
    if warning is None:
        raise FrameGraphError("responsibility statement cannot fit")
    warning_height = warning[0]["bbox_mpt"][3] - warning[0]["bbox_mpt"][1]
    body_pages = {
        locations[f["block_id"]] for u in units["units"] for f in u["fragments"]
    }
    body_pages.update(p["page_number"] for p in source["pages"] if p["graphic_nodes"])
    graph = {
        "schema_version": "2.0.0",
        "artifact_kind": "frame-graph",
        "source_hash": sha256_canonical(source),
        "line_mapping_version": 2,
        "frame_graph_input_hash": sha256_canonical(
            {
                "source": source,
                "units": units,
                "translation": translation,
                "style": asdict(style_contract),
                "config": asdict(config),
                "annotations": annotation_binding,
                "items": list(annotation_items),
                "brand_manifest_hash": manifest_hash,
            }
        ),
        "font_fingerprint": [
            {"role": a, "reportlab_name": b, "sha256": c}
            for a, b, c in resolver.font_fingerprint
        ],
        "right_panel_bbox_mpt": [
            A4_WIDTH_MPT,
            0,
            A3_LANDSCAPE_WIDTH_MPT,
            A3_LANDSCAPE_HEIGHT_MPT,
        ],
        "flow_spacing": asdict(config),
        "source_page_count": len(source["pages"]),
        "body_page_budget": max(1, len(body_pages)),
        "warning_height_mpt": warning_height,
        "brand_manifest_hash": manifest_hash,
        "unit_flows": unit_flows,
        "auxiliary_flows": auxiliary_flows,
        "flow_order": order,
        "annotation_binding": annotation_binding,
    }
    validate_artifact("frame-graph", graph)
    return graph


def _build_annotated_frame_graph(source, units, translation, **kwargs):
    return _construct(source, units, translation, **kwargs)


def build_frame_graph(
    source,
    units,
    translation,
    *,
    style_contract,
    resolver,
    config=DEFAULT_FRAME_GRAPH_CONFIG,
    approved_figure_notes=None,
):
    if approved_figure_notes:
        raise FrameGraphError("supply notes through the annotation artifact")
    return _construct(
        source,
        units,
        translation,
        style_contract=style_contract,
        resolver=resolver,
        config=config,
    )


def validate_frame_graph_against_inputs(source, units, translation, artifact, **kwargs):
    expected = build_frame_graph(source, units, translation, **kwargs)
    if artifact != expected:
        raise FrameGraphError("frame graph differs from current inputs")
