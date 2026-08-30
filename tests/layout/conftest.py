# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical


def _line(
    *,
    index: int,
    start: int,
    height_mpt: int,
    style_id: str,
    font_role: str,
    font_name: str,
) -> dict[str, object]:
    text = f"译{index}"
    payload: dict[str, object] = {
        "index": index,
        "target_start": start,
        "target_end": start + len(text),
        "text": text,
        "style_id": style_id,
        "width_mpt": 20_000,
        "line_height_mpt": height_mpt,
        "ascent_mpt": max(1, height_mpt * 4 // 5),
        "descent_mpt": min(0, -(height_mpt // 5)),
        "runs": [
            {
                "font_role": font_role,
                "font_name": font_name,
                "text": text,
            }
        ],
    }
    payload["line_box_hash"] = sha256_canonical(
        {"line_box_contract_version": "1.0.0", **payload}
    )
    return payload


@pytest.fixture
def frame_graph_factory() -> Callable[..., dict[str, object]]:
    def build(
        flow_specs: Sequence[dict[str, Any]],
        *,
        frame_count: int = 1,
        diagnostic_height_mpt: int = 1,
        page_height_mpt: int = 100_000,
    ) -> dict[str, object]:
        native_ids = [f"native-{index}" for index in range(frame_count)]
        candidate_ids = [f"candidate-{index}" for index in range(frame_count)]
        ratios = [1_000_000 // frame_count] * frame_count
        ratios[0] += 1_000_000 - sum(ratios)
        frames: list[dict[str, object]] = []
        width = 200_000 // frame_count
        for index, (native_id, candidate_id) in enumerate(
            zip(native_ids, candidate_ids, strict=True)
        ):
            x_left = 100_000 + index * width
            common = {
                "bbox_mpt": [x_left, 10_000, x_left + width, 90_000],
                "text_left_mpt": x_left + 4_000,
                "text_right_mpt": x_left + width - 4_000,
                "source_page_number": 1,
                "source_band_id": "band-0",
                "source_column_id": f"column-{index}",
                "band_index": 0,
                "column_index": index,
                "column_count": frame_count,
                "width_ratio_ppm": ratios[index],
            }
            frames.extend(
                [
                    {
                        "id": native_id,
                        "kind": "native",
                        "activation": "always",
                        **common,
                    },
                    {
                        "id": candidate_id,
                        "kind": "continuation-template",
                        "activation": "candidate",
                        **common,
                    },
                ]
            )

        edges: list[dict[str, object]] = []

        def edge(source: str, target: str, transition: str, activation: str) -> None:
            edges.append(
                {
                    "id": f"edge-{len(edges):04d}",
                    "from_frame_id": source,
                    "to_frame_id": target,
                    "order": len(edges),
                    "transition": transition,
                    "activation": activation,
                    "allowed_break_kinds": ["between-unit", "sentence", "line"],
                }
            )

        for left, right in zip(native_ids, native_ids[1:], strict=False):
            edge(left, right, "next-column", "always")
        for native_id, candidate_id in zip(native_ids, candidate_ids, strict=True):
            edge(native_id, candidate_id, "enter-continuation", "candidate")
        for left, right in zip(candidate_ids, candidate_ids[1:], strict=False):
            edge(left, right, "next-column", "candidate")

        font_names = {
            "body": "APR-body-aaaaaaaaaaaaaaaa",
            "heading": "APR-heading-bbbbbbbbbbbbbbbb",
            "symbols": "APR-symbols-cccccccccccccccc",
        }
        header_style = {
            "style_id": "typography-v1:auxiliary:body:8000:12000",
            "semantic_role": "auxiliary",
            "font_role": "body",
            "size_mpt": 8_000,
            "line_height_mpt": 12_000,
        }
        header_runs = [
            {
                "run_index": 0,
                "text": "译文续页",
                "font_role": "body",
                "font_name": font_names["body"],
                "x_offset_mpt": 0,
                "width_mpt": 32_000,
            }
        ]
        continuation_header = {
            "contract_version": "1.0.0",
            "text": "译文续页",
            "style": header_style,
            "runs": header_runs,
            "width_mpt": 32_000,
            "line_height_mpt": 12_000,
            "ascent_mpt": 7_000,
            "descent_mpt": -1_000,
            "top_inset_mpt": 4_000,
            "gap_after_mpt": 3_000,
            "reserve_height_mpt": 19_000,
            "color_token": "muted_gray",
            "color_hex": "#666666",
            "horizontal_alignment": "right",
        }
        continuation_header["header_hash"] = sha256_canonical(continuation_header)
        flows: list[dict[str, object]] = []
        parts: list[dict[str, object]] = []
        content_ids: list[str] = []
        for flow_index, spec in enumerate(flow_specs):
            unit_id = str(spec.get("unit_id", f"unit-{flow_index}"))
            role = str(spec.get("role", "body"))
            line_count = int(spec.get("line_count", 1))
            line_height = int(spec.get("line_height_mpt", 20_000))
            allowed_indexes = tuple(spec.get("allowed_frame_indexes", (0,)))
            allowed = [native_ids[index] for index in allowed_indexes]
            font_role = "heading" if role in {"title", "heading"} else "body"
            style_id = f"typography-v1:{role}:{font_role}:10000:{line_height}"
            style = {
                "style_id": style_id,
                "semantic_role": role,
                "font_role": font_role,
                "size_mpt": 10_000,
                "line_height_mpt": line_height,
            }
            lines: list[dict[str, object]] = []
            cursor = 0
            for line_index in range(line_count):
                line = _line(
                    index=line_index,
                    start=cursor,
                    height_mpt=line_height,
                    style_id=style_id,
                    font_role=font_role,
                    font_name=font_names[font_role],
                )
                cursor = int(line["target_end"])
                lines.append(line)
            raw_breaks = spec.get("legal_breaks")
            if raw_breaks is None:
                legal_breaks = [
                    {"after_line": index, "kind": "line"}
                    for index in range(1, line_count)
                ]
            else:
                legal_breaks = [
                    {"after_line": int(index), "kind": str(kind)}
                    for index, kind in raw_breaks
                ]
            anchor_kind = "leader" if frame_count == 1 else "soft-y"
            flow = {
                "unit_id": unit_id,
                "role": role,
                "home_frame_id": allowed[0],
                "allowed_native_frame_ids": allowed,
                "continuation_owner_page_number": 1,
                "source_fragment_start": 0,
                "source_fragment_end": len(allowed),
                "line_count": line_count,
                "style": style,
                "line_sequence_hash": sha256_canonical(
                    {"style": style, "lines": lines}
                ),
                "lines": lines,
                "legal_breaks": legal_breaks,
                "anchor": {
                    "kind": anchor_kind,
                    "source_block_id": f"source-{unit_id}",
                    "source_page_number": 1,
                    "source_visual_center_offset_mpt": int(
                        spec.get("anchor_offset_mpt", 35_000)
                    ),
                    "initial_part_index": 0,
                },
            }
            flows.append(flow)
            parts.append(
                {
                    "id": f"{unit_id}:part:0000",
                    "unit_id": unit_id,
                    "part_index": 0,
                    "frame_id": allowed[0],
                    "line_start": 0,
                    "line_end": line_count,
                    "source_fragment_start": 0,
                    "source_fragment_end": len(allowed),
                    "is_first_part": True,
                    "creates_anchor": True,
                }
            )
            content_ids.append(f"unit:{unit_id}")

        return {
            "schema_version": "1.0.0",
            "artifact_kind": "frame-graph",
            "frame_graph_input_hash": "a" * 64,
            "right_panel_bbox_mpt": [100_000, 0, 300_000, page_height_mpt],
            "font_fingerprint": [
                {"role": role, "reportlab_name": font_names[role], "sha256": char * 64}
                for role, char in (("body", "a"), ("heading", "b"), ("symbols", "c"))
            ],
            "flow_spacing": {
                "config_version": 1,
                "horizontal_padding_mpt": 4_000,
                "vertical_padding_mpt": 4_000,
                "block_gap_mpt": 4_000,
                "figure_note_gap_mpt": 3_000,
            },
            "continuation_header": continuation_header,
            "pages": [
                {
                    "page_number": 1,
                    "page_height_mpt": page_height_mpt,
                    "bands": [
                        {
                            "id": "fg:band-0:band",
                            "source_band_id": "band-0",
                            "band_index": 0,
                            "preferred_top_offset_mpt": 10_000,
                            "preferred_bottom_offset_mpt": 90_000,
                            "initial_unsplit_content_height_mpt": diagnostic_height_mpt,
                            "height_basis": "initial-unsplit-ordinary-flow",
                            "native_frame_ids": native_ids,
                            "continuation_template_frame_ids": candidate_ids,
                            "initial_unsplit_content_item_ids": content_ids,
                        }
                    ],
                    "frames": frames,
                }
            ],
            "edges": edges,
            "unit_flows": flows,
            "unit_parts": parts,
            "figure_note_flows": [],
        }

    return build
