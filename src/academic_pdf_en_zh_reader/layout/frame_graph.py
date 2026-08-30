# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build a mirrored, immutable flow graph without solving final positions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

from reportlab.pdfbase import pdfmetrics

from academic_pdf_en_zh_reader.extraction.unit_mapping import (
    UnitMappingError,
    validate_unit_mapping,
)
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.band_geometry import (
    FlowItemMeasure,
    initial_unsplit_band_content_height,
    lines_height_mpt,
)
from academic_pdf_en_zh_reader.layout.unit_parts import (
    UnitPartError,
    initial_unit_part,
    legal_composite_line_breaks,
    legal_line_breaks,
    measure_auxiliary_lines,
    measure_composite_target_lines,
    measure_target_lines,
    style_descriptor,
)
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    TranslationValidationError,
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    TypographyStyleContract,
)

_CAPTION_ROLES = frozenset({"figure-caption", "table-caption"})
_BREAK_KINDS = ["between-unit", "sentence", "line"]
_CONTINUATION_HEADER_TEXT = "译文续页"
_CONTINUATION_HEADER_GAP_AFTER_MPT = 3_000


class FrameGraphError(ValueError):
    """Raised when frozen inputs cannot produce one trustworthy flow graph."""


@dataclass(frozen=True, slots=True)
class FrameGraphConfig:
    """Small versioned set of fixed right-panel spacing values."""

    version: int = 1
    horizontal_padding_mpt: int = 4_000
    vertical_padding_mpt: int = 4_000
    block_gap_mpt: int = 4_000
    figure_note_gap_mpt: int = 3_000

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(type(value) is not int for value in values.values()):
            raise FrameGraphError("frame graph configuration must use integers")
        if self.version < 1 or any(
            value < 0 for key, value in values.items() if key != "version"
        ):
            raise FrameGraphError("frame graph configuration is out of range")


DEFAULT_FRAME_GRAPH_CONFIG = FrameGraphConfig()


@dataclass(frozen=True, slots=True)
class _BlockLocation:
    page_number: int
    crop_box_mpt: tuple[int, int, int, int]
    band_index: int
    column_index: int
    column_count: int
    block: dict[str, Any]


def _mpt_ceil(value_pt: float) -> int:
    return int(
        (Decimal(str(value_pt)) * 1000).to_integral_value(rounding=ROUND_CEILING)
    )


def _continuation_header(
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    config: FrameGraphConfig,
) -> dict[str, object]:
    """Freeze one exact, graph-owned continuation heading and its reserve."""

    style = style_contract.style_for("auxiliary")
    maximum_width = (
        A3_LANDSCAPE_WIDTH_MPT - A4_WIDTH_MPT - 2 * config.horizontal_padding_mpt
    )
    lines = measure_target_lines(
        _CONTINUATION_HEADER_TEXT,
        maximum_width_mpt=maximum_width,
        resolver=resolver,
        style=style,
        semantic_role="auxiliary",
        style_contract_version=style_contract.version,
    )
    if len(lines) != 1 or lines[0]["text"] != _CONTINUATION_HEADER_TEXT:
        raise FrameGraphError("continuation heading must fit one exact line")
    line = lines[0]
    cumulative_pt = 0.0
    previous_mpt = 0
    runs: list[dict[str, object]] = []
    for run_index, raw_run in enumerate(line["runs"]):
        cumulative_pt += pdfmetrics.stringWidth(
            str(raw_run["text"]),
            str(raw_run["font_name"]),
            style.size_pt,
        )
        cumulative_mpt = _mpt_ceil(cumulative_pt)
        run_width = cumulative_mpt - previous_mpt
        if run_width <= 0:
            raise FrameGraphError("continuation heading run has no visible width")
        runs.append(
            {
                "run_index": run_index,
                "text": raw_run["text"],
                "font_role": raw_run["font_role"],
                "font_name": raw_run["font_name"],
                "x_offset_mpt": previous_mpt,
                "width_mpt": run_width,
            }
        )
        previous_mpt = cumulative_mpt
    if previous_mpt != line["width_mpt"]:
        raise FrameGraphError("continuation heading width cannot be reproduced")
    header: dict[str, object] = {
        "contract_version": "1.0.0",
        "text": _CONTINUATION_HEADER_TEXT,
        "style": style_descriptor(
            semantic_role="auxiliary",
            style=style,
            style_contract_version=style_contract.version,
        ),
        "runs": runs,
        "width_mpt": line["width_mpt"],
        "line_height_mpt": line["line_height_mpt"],
        "ascent_mpt": line["ascent_mpt"],
        "descent_mpt": line["descent_mpt"],
        "top_inset_mpt": config.vertical_padding_mpt,
        "gap_after_mpt": _CONTINUATION_HEADER_GAP_AFTER_MPT,
        "reserve_height_mpt": (
            config.vertical_padding_mpt
            + int(line["line_height_mpt"])
            + _CONTINUATION_HEADER_GAP_AFTER_MPT
        ),
        "color_token": "muted_gray",
        "color_hex": "#666666",
        "horizontal_alignment": "right",
    }
    header["header_hash"] = sha256_canonical(header)
    return header


def _style_payload(style_contract: TypographyStyleContract) -> dict[str, object]:
    return {
        "version": style_contract.version,
        "body_source_size_mpt": style_contract.body_source_size_mpt,
        "styles": [
            {
                "role": role,
                "font_role": style.font_role,
                "size_mpt": style.size_mpt,
                "line_height_mpt": style.line_height_mpt,
            }
            for role, style in style_contract.styles
        ],
    }


def _normalise_notes(
    notes: Mapping[str, Sequence[str]] | None,
    units: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    if notes is None:
        return {}
    unit_roles = {
        unit["id"]: unit["role"]
        for unit in units["units"]  # type: ignore[index]
    }
    result: dict[str, tuple[str, ...]] = {}
    for unit_id in sorted(notes):
        values = notes[unit_id]
        if unit_roles.get(unit_id) not in _CAPTION_ROLES:
            raise FrameGraphError(
                "approved figure notes must target a figure or table caption"
            )
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise FrameGraphError("approved figure notes must be an ordered sequence")
        frozen = tuple(values)
        if not frozen or any(
            not isinstance(value, str) or not value.strip() for value in frozen
        ):
            raise FrameGraphError("approved figure note text must be non-empty")
        result[unit_id] = frozen
    return result


def _input_hash(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    style_contract: TypographyStyleContract,
    config: FrameGraphConfig,
    notes: Mapping[str, Sequence[str]],
    resolver: FontRunResolver,
    annotation_binding: Mapping[str, object] | None,
) -> str:
    payload: dict[str, object] = {
        "hash_contract_version": (
            "2.0.0" if annotation_binding is not None else "1.0.0"
        ),
        "source": source,
        "units": units,
        "translation": translation,
        "style_contract": _style_payload(style_contract),
        "config": asdict(config),
        "font_fingerprint": [
            {
                "role": role,
                "reportlab_name": reportlab_name,
                "sha256": sha256,
            }
            for role, reportlab_name, sha256 in resolver.font_fingerprint
        ],
    }
    if annotation_binding is None:
        payload["approved_figure_notes"] = {
            unit_id: list(values) for unit_id, values in notes.items()
        }
    else:
        payload["annotation_binding"] = dict(annotation_binding)
    return sha256_canonical(payload)


def _largest_remainder_ratios(widths: Sequence[int]) -> tuple[int, ...]:
    total = sum(widths)
    if total <= 0:
        raise FrameGraphError("source column widths must be positive")
    ratios = [width * 1_000_000 // total for width in widths]
    remainder = 1_000_000 - sum(ratios)
    order = sorted(
        range(len(widths)),
        key=lambda index: (-(widths[index] * 1_000_000 % total), index),
    )
    for index in order[:remainder]:
        ratios[index] += 1
    return tuple(ratios)


def _frame_id(band_id: str, column_id: str, kind: str) -> str:
    suffix = "native" if kind == "native" else "continuation-template"
    return f"fg:{band_id}:{column_id}:{suffix}"


def _source_indexes(
    source: Mapping[str, object],
) -> tuple[
    dict[str, _BlockLocation],
    dict[str, dict[str, Any]],
]:
    locations: dict[str, _BlockLocation] = {}
    page_by_band: dict[str, dict[str, Any]] = {}
    for page in source["pages"]:  # type: ignore[index]
        crop = tuple(page["crop_box_mpt"])
        for band_index, band in enumerate(page["bands"]):
            page_by_band[band["id"]] = page
            column_index = {
                column["id"]: index for index, column in enumerate(band["columns"])
            }
            for block in page["blocks"]:
                if block["band_id"] != band["id"]:
                    continue
                locations[block["id"]] = _BlockLocation(
                    page_number=page["page_number"],
                    crop_box_mpt=crop,  # type: ignore[arg-type]
                    band_index=band_index,
                    column_index=column_index[block["column_id"]],
                    column_count=len(band["columns"]),
                    block=block,
                )
    return locations, page_by_band


def _translated_x(source_x: int, crop_left: int) -> int:
    return A4_WIDTH_MPT + source_x - crop_left


def _translated_y(source_y: int, crop_bottom: int) -> int:
    return source_y - crop_bottom


def _build_pages(
    source: Mapping[str, object],
    config: FrameGraphConfig,
) -> tuple[
    list[dict[str, object]],
    dict[tuple[int, str, str], str],
    dict[str, dict[str, object]],
    dict[str, str],
]:
    pages: list[dict[str, object]] = []
    native_by_source: dict[tuple[int, str, str], str] = {}
    frame_by_id: dict[str, dict[str, object]] = {}
    candidate_by_native: dict[str, str] = {}
    for page in source["pages"]:  # type: ignore[index]
        crop_left, crop_bottom, _crop_right, _crop_top = page["crop_box_mpt"]
        native_frames: list[dict[str, object]] = []
        candidate_frames: list[dict[str, object]] = []
        bands: list[dict[str, object]] = []
        for band_index, band in enumerate(page["bands"]):
            widths = [
                column["x_right_mpt"] - column["x_left_mpt"]
                for column in band["columns"]
            ]
            ratios = _largest_remainder_ratios(widths)
            native_ids: list[str] = []
            candidate_ids: list[str] = []
            for column_index, (column, ratio) in enumerate(
                zip(band["columns"], ratios, strict=True)
            ):
                x_left = _translated_x(column["x_left_mpt"], crop_left)
                raw_right = _translated_x(column["x_right_mpt"], crop_left)
                x_right = min(A3_LANDSCAPE_WIDTH_MPT, raw_right)
                y_bottom = _translated_y(band["y_bottom_mpt"], crop_bottom)
                y_top = _translated_y(band["y_top_mpt"], crop_bottom)
                if (
                    x_left < A4_WIDTH_MPT
                    or x_right > A3_LANDSCAPE_WIDTH_MPT
                    or y_bottom < 0
                    or y_top > A3_LANDSCAPE_HEIGHT_MPT
                    or x_right - x_left <= config.horizontal_padding_mpt * 2
                    or y_bottom >= y_top
                ):
                    raise FrameGraphError(
                        "mirrored source frame is outside the A3 panel"
                    )
                native_id = _frame_id(band["id"], column["id"], "native")
                candidate_id = _frame_id(
                    band["id"], column["id"], "continuation-template"
                )
                common: dict[str, object] = {
                    "bbox_mpt": [x_left, y_bottom, x_right, y_top],
                    "text_left_mpt": x_left + config.horizontal_padding_mpt,
                    "text_right_mpt": x_right - config.horizontal_padding_mpt,
                    "source_page_number": page["page_number"],
                    "source_band_id": band["id"],
                    "source_column_id": column["id"],
                    "band_index": band_index,
                    "column_index": column_index,
                    "column_count": len(band["columns"]),
                    "width_ratio_ppm": ratio,
                }
                native = {
                    "id": native_id,
                    "kind": "native",
                    "activation": "always",
                    **common,
                }
                candidate = {
                    "id": candidate_id,
                    "kind": "continuation-template",
                    "activation": "candidate",
                    **common,
                }
                native_frames.append(native)
                candidate_frames.append(candidate)
                native_ids.append(native_id)
                candidate_ids.append(candidate_id)
                native_by_source[(page["page_number"], band["id"], column["id"])] = (
                    native_id
                )
                frame_by_id[native_id] = native
                frame_by_id[candidate_id] = candidate
                candidate_by_native[native_id] = candidate_id
            bands.append(
                {
                    "id": f"fg:{band['id']}:band",
                    "source_band_id": band["id"],
                    "band_index": band_index,
                    "preferred_top_offset_mpt": (
                        A3_LANDSCAPE_HEIGHT_MPT
                        - _translated_y(band["y_top_mpt"], crop_bottom)
                    ),
                    "preferred_bottom_offset_mpt": (
                        A3_LANDSCAPE_HEIGHT_MPT
                        - _translated_y(band["y_bottom_mpt"], crop_bottom)
                    ),
                    "initial_unsplit_content_height_mpt": 1,
                    "height_basis": "initial-unsplit-ordinary-flow",
                    "native_frame_ids": native_ids,
                    "continuation_template_frame_ids": candidate_ids,
                    "initial_unsplit_content_item_ids": [],
                }
            )
        pages.append(
            {
                "page_number": page["page_number"],
                "page_height_mpt": A3_LANDSCAPE_HEIGHT_MPT,
                "bands": bands,
                "frames": [*native_frames, *candidate_frames],
            }
        )
    return pages, native_by_source, frame_by_id, candidate_by_native


def _frame_transition(left: Mapping[str, object], right: Mapping[str, object]) -> str:
    if left["source_page_number"] != right["source_page_number"]:
        return "next-source-page"
    if left["source_band_id"] != right["source_band_id"]:
        return "next-band"
    return "next-column"


def _build_edges(
    pages: Sequence[Mapping[str, object]],
    candidate_by_native: Mapping[str, str],
    frame_by_id: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    native = [
        frame
        for page in pages
        for frame in page["frames"]  # type: ignore[index]
        if frame["kind"] == "native"
    ]
    raw: list[tuple[str, str, str, str]] = []
    for left, right in zip(native, native[1:], strict=False):
        raw.append(
            (
                left["id"],
                right["id"],
                _frame_transition(left, right),
                "always",
            )
        )
    for page_index, page in enumerate(pages):
        page_native = [
            frame
            for frame in page["frames"]
            if frame["kind"] == "native"  # type: ignore[index]
        ]
        page_candidates = [
            frame
            for frame in page["frames"]  # type: ignore[index]
            if frame["kind"] == "continuation-template"
        ]
        for source_frame in page_native:
            raw.append(
                (
                    source_frame["id"],
                    candidate_by_native[source_frame["id"]],
                    "enter-continuation",
                    "candidate",
                )
            )
        for left, right in zip(page_candidates, page_candidates[1:], strict=False):
            raw.append(
                (
                    left["id"],
                    right["id"],
                    _frame_transition(left, right),
                    "candidate",
                )
            )
        if page_index + 1 < len(pages):
            next_native = next(
                frame
                for frame in pages[page_index + 1]["frames"]  # type: ignore[index]
                if frame["kind"] == "native"
            )
            raw.append(
                (
                    page_candidates[-1]["id"],
                    next_native["id"],
                    "leave-continuation",
                    "candidate",
                )
            )
    return [
        {
            "id": f"fg-edge-{order:05d}",
            "from_frame_id": source_id,
            "to_frame_id": target_id,
            "order": order,
            "transition": transition,
            "activation": activation,
            "allowed_break_kinds": list(_BREAK_KINDS),
        }
        for order, (source_id, target_id, transition, activation) in enumerate(raw)
    ]


def _source_anchor_candidates(
    fragment_locations: Sequence[_BlockLocation],
) -> list[dict[str, object]]:
    """Freeze the earliest visible unit fragment on every repeated source page."""

    by_page: dict[int, _BlockLocation] = {}
    for location in fragment_locations:
        current = by_page.get(location.page_number)
        if current is None or int(location.block["reading_order"]) < int(
            current.block["reading_order"]
        ):
            by_page[location.page_number] = location
    candidates: list[dict[str, object]] = []
    for page_number in sorted(by_page):
        location = by_page[page_number]
        crop_left, crop_bottom, _crop_right, _crop_top = location.crop_box_mpt
        raw_first_line = location.block["first_line_bbox_mpt"]
        first_line = [
            int(raw_first_line[0]) - crop_left,
            int(raw_first_line[1]) - crop_bottom,
            int(raw_first_line[2]) - crop_left,
            int(raw_first_line[3]) - crop_bottom,
        ]
        visual_center = (int(first_line[1]) + int(first_line[3])) // 2
        candidates.append(
            {
                "kind": "leader" if location.column_count == 1 else "soft-y",
                "source_block_id": location.block["id"],
                "source_page_number": page_number,
                "source_band_id": location.block["band_id"],
                "source_visual_center_offset_mpt": (
                    A3_LANDSCAPE_HEIGHT_MPT - visual_center
                ),
                "source_first_line_bbox_mpt": first_line,
                "source_endpoint_mpt": [int(first_line[2]), visual_center],
            }
        )
    return candidates


def _build_flows(
    units: Mapping[str, object],
    translation: Mapping[str, object],
    locations: Mapping[str, _BlockLocation],
    native_by_source: Mapping[tuple[int, str, str], str],
    frame_by_id: Mapping[str, Mapping[str, object]],
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    notes: Mapping[str, Sequence[str]],
    annotation_items: Sequence[Mapping[str, object]] | None,
    config: FrameGraphConfig,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, list[FlowItemMeasure]],
]:
    translated_by_id = {
        item["unit_id"]: item
        for item in translation["units"]  # type: ignore[index]
    }
    flows: list[dict[str, object]] = []
    parts: list[dict[str, object]] = []
    note_flows: list[dict[str, object]] = []
    auxiliary_flows: list[dict[str, object]] = []
    items_by_frame: dict[str, list[FlowItemMeasure]] = defaultdict(list)
    labels_by_unit: dict[str, list[dict[str, object]]] = defaultdict(list)
    styled_by_unit: dict[str, list[dict[str, object]]] = defaultdict(list)
    auxiliary_by_unit: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    if annotation_items is not None:
        for item in annotation_items:
            kind = item["kind"]
            if kind in {"dark-red-highlight", "bright-red-ambiguity"}:
                styled_by_unit[str(item["unit_id"])].append(
                    {
                        "annotation_id": item["id"],
                        "kind": kind,
                        "target_start": item["target_start"],
                        "target_end": item["target_end"],
                        "color_token": (
                            "dark_red" if kind == "dark-red-highlight" else "bright_red"
                        ),
                        "underline": kind == "bright-red-ambiguity",
                    }
                )
            if kind == "bright-red-ambiguity" and item["content"] is not None:
                labels_by_unit[str(item["unit_id"])].append(
                    {
                        "annotation_id": item["id"],
                        "target_offset": item["target_end"],
                        "text": item["content"],
                    }
                )
            elif kind in {"dark-orange-teaching", "figure-table-reading"}:
                auxiliary_by_unit[str(item["unit_id"])].append(item)
    for unit in units["units"]:  # type: ignore[index]
        fragment_locations = [locations[item["block_id"]] for item in unit["fragments"]]
        allowed: list[str] = []
        for location in fragment_locations:
            block = location.block
            frame_id = native_by_source[
                (location.page_number, block["band_id"], block["column_id"])
            ]
            if not allowed or allowed[-1] != frame_id:
                allowed.append(frame_id)
        home_frame_id = allowed[0]
        maximum_width = min(
            int(frame_by_id[frame_id]["text_right_mpt"])
            - int(frame_by_id[frame_id]["text_left_mpt"])
            for frame_id in allowed
        )
        translated = translated_by_id[unit["id"]]
        chinese_text = translated["chinese_text"]
        style = style_contract.style_for(unit["role"])
        if annotation_items is None:
            lines = measure_target_lines(
                chinese_text,
                maximum_width_mpt=maximum_width,
                resolver=resolver,
                style=style,
                semantic_role=unit["role"],
                style_contract_version=style_contract.version,
            )
            composite_segments: tuple[dict[str, object], ...] | None = None
        else:
            lines, composite_segments = measure_composite_target_lines(
                chinese_text,
                labels_by_unit.get(str(unit["id"]), ()),
                maximum_width_mpt=maximum_width,
                resolver=resolver,
                style=style,
                semantic_role=unit["role"],
                style_contract_version=style_contract.version,
            )
        style_record = style_descriptor(
            semantic_role=unit["role"],
            style=style,
            style_contract_version=style_contract.version,
        )
        first = fragment_locations[0]
        first_line = first.block["first_line_bbox_mpt"]
        source_visual_center = (
            first_line[1] + first_line[3]
        ) // 2 - first.crop_box_mpt[1]
        anchor_candidates = _source_anchor_candidates(fragment_locations)
        first_candidate = anchor_candidates[0]
        flow: dict[str, object] = {
            "unit_id": unit["id"],
            "role": unit["role"],
            "home_frame_id": home_frame_id,
            "allowed_native_frame_ids": allowed,
            "continuation_owner_page_number": fragment_locations[-1].page_number,
            "source_fragment_start": 0,
            "source_fragment_end": len(fragment_locations),
            "line_count": len(lines),
            "style": style_record,
            "lines": list(lines),
            "anchor": {
                "kind": "leader" if first.column_count == 1 else "soft-y",
                "source_block_id": first.block["id"],
                "source_page_number": first.page_number,
                "source_visual_center_offset_mpt": (
                    A3_LANDSCAPE_HEIGHT_MPT - source_visual_center
                ),
                "initial_part_index": 0,
            },
        }
        if annotation_items is None:
            flow["line_sequence_hash"] = sha256_canonical(
                {"style": style_record, "lines": lines}
            )
            flow["legal_breaks"] = list(legal_line_breaks(chinese_text, lines))
        else:
            assert composite_segments is not None
            flow["base_target_length"] = len(chinese_text)
            flow["composite_length"] = int(composite_segments[-1]["composite_end"])
            flow["composite_segments"] = list(composite_segments)
            flow["styled_spans"] = styled_by_unit.get(str(unit["id"]), [])
            flow["source_anchor_candidates"] = anchor_candidates
            flow["anchor"].update(  # type: ignore[union-attr]
                {
                    "source_band_id": first_candidate["source_band_id"],
                    "source_first_line_bbox_mpt": first_candidate[
                        "source_first_line_bbox_mpt"
                    ],
                    "source_endpoint_mpt": first_candidate["source_endpoint_mpt"],
                }
            )
            flow["line_sequence_hash"] = sha256_canonical(
                {
                    "line_sequence_contract_version": "2.0.0",
                    "style": style_record,
                    "composite_segments": composite_segments,
                    "lines": lines,
                }
            )
            flow["legal_breaks"] = list(
                legal_composite_line_breaks(
                    chinese_text,
                    lines,
                    composite_segments,
                )
            )
        flows.append(flow)
        parts.append(
            initial_unit_part(
                unit_id=unit["id"],
                home_frame_id=home_frame_id,
                line_count=len(lines),
                source_fragment_count=len(fragment_locations),
            )
        )
        existing = items_by_frame[home_frame_id]
        existing.append(
            FlowItemMeasure(
                id=f"unit:{unit['id']}",
                height_mpt=lines_height_mpt(lines),
                gap_before_mpt=config.block_gap_mpt if existing else 0,
            )
        )
        auxiliary_style = style_contract.style_for("auxiliary")
        note_style = style_descriptor(
            semantic_role="auxiliary",
            style=auxiliary_style,
            style_contract_version=style_contract.version,
        )
        for note_index, note in enumerate(notes.get(unit["id"], ())):
            note_id = f"note:{unit['id']}:{note_index:03d}"
            note_lines = measure_target_lines(
                note,
                maximum_width_mpt=maximum_width,
                resolver=resolver,
                style=auxiliary_style,
                semantic_role="auxiliary",
                style_contract_version=style_contract.version,
            )
            note_flows.append(
                {
                    "id": note_id,
                    "unit_id": unit["id"],
                    "frame_id": home_frame_id,
                    "note_index": note_index,
                    "line_count": len(note_lines),
                    "style": note_style,
                    "line_sequence_hash": sha256_canonical(
                        {"style": note_style, "lines": note_lines}
                    ),
                    "lines": list(note_lines),
                }
            )
            existing.append(
                FlowItemMeasure(
                    id=note_id,
                    height_mpt=lines_height_mpt(note_lines),
                    gap_before_mpt=config.figure_note_gap_mpt,
                )
            )
        if annotation_items is not None:
            for auxiliary_index, item in enumerate(
                auxiliary_by_unit.get(str(unit["id"]), ())
            ):
                auxiliary_id = f"aux:{item['id']}"
                auxiliary_lines, auxiliary_segments = measure_auxiliary_lines(
                    str(item["content"]),
                    annotation_id=str(item["id"]),
                    target_offset=int(item["target_end"]),
                    maximum_width_mpt=maximum_width,
                    resolver=resolver,
                    style=auxiliary_style,
                    style_contract_version=style_contract.version,
                )
                auxiliary_flow: dict[str, object] = {
                    "id": auxiliary_id,
                    "annotation_id": item["id"],
                    "annotation_kind": item["kind"],
                    "unit_id": unit["id"],
                    "order_after_unit": auxiliary_index,
                    "attachment": item["attachment"],
                    "home_frame_id": home_frame_id,
                    "allowed_native_frame_ids": list(allowed),
                    "continuation_owner_page_number": fragment_locations[
                        -1
                    ].page_number,
                    "parent_target_length": len(chinese_text),
                    "attachment_target_start": item["target_start"],
                    "attachment_target_end": item["target_end"],
                    "composite_length": len(str(item["content"])),
                    "composite_segments": list(auxiliary_segments),
                    "line_count": len(auxiliary_lines),
                    "style": note_style,
                    "line_sequence_hash": sha256_canonical(
                        {
                            "line_sequence_contract_version": "2.0.0",
                            "style": note_style,
                            "composite_segments": auxiliary_segments,
                            "lines": auxiliary_lines,
                        }
                    ),
                    "lines": list(auxiliary_lines),
                }
                auxiliary_flows.append(auxiliary_flow)
                existing.append(
                    FlowItemMeasure(
                        id=auxiliary_id,
                        height_mpt=lines_height_mpt(auxiliary_lines),
                        gap_before_mpt=config.figure_note_gap_mpt,
                    )
                )
    return flows, parts, note_flows, auxiliary_flows, items_by_frame


def _complete_band_geometry(
    source: Mapping[str, object],
    pages: list[dict[str, object]],
    items_by_frame: Mapping[str, Sequence[FlowItemMeasure]],
    config: FrameGraphConfig,
    *,
    annotated: bool,
) -> None:
    source_pages = {
        page["page_number"]: page
        for page in source["pages"]  # type: ignore[index]
    }
    for output_page in pages:
        source_page = source_pages[output_page["page_number"]]
        source_band_by_id = {band["id"]: band for band in source_page["bands"]}
        for band_record in output_page["bands"]:  # type: ignore[index]
            band_id = band_record["source_band_id"]
            source_band = source_band_by_id[band_id]
            required_roles = [
                block["role"]
                for block in source_page["blocks"]
                if block["band_id"] == source_band["id"]
                and block["translation_policy"] == "required"
            ]
            graphic_only = (
                bool(required_roles) and set(required_roles) <= _CAPTION_ROLES
            )
            height, content_ids = initial_unsplit_band_content_height(
                band_record["native_frame_ids"],
                items_by_frame,
                vertical_padding_mpt=config.vertical_padding_mpt,
            )
            band_record["initial_unsplit_content_height_mpt"] = height
            band_record["height_basis"] = (
                (
                    "initial-unsplit-caption-plus-frozen-annotation-auxiliary"
                    if annotated
                    else "initial-unsplit-caption-plus-approved-figure-notes"
                )
                if graphic_only
                else "initial-unsplit-ordinary-flow"
            )
            band_record["initial_unsplit_content_item_ids"] = list(content_ids)


def _validated_inputs(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
) -> None:
    try:
        validate_artifact("source", source)
        validate_artifact("units", units)
        validate_unit_mapping(source, units)
        validate_translation_artifact(units, translation)
    except (
        SchemaValidationError,
        UnitMappingError,
        TranslationValidationError,
    ) as exc:
        raise FrameGraphError(
            "frame graph inputs violate their frozen contracts"
        ) from exc
    for page in source["pages"]:  # type: ignore[index]
        crop = page["crop_box_mpt"]
        # Extraction freezes already-rotated displayed coordinates.  A raw
        # 90/270-degree box would therefore fail this portrait A4 boundary.
        if (
            abs(int(crop[2]) - int(crop[0]) - A4_WIDTH_MPT) > 1_000
            or abs(int(crop[3]) - int(crop[1]) - A3_LANDSCAPE_HEIGHT_MPT) > 1_000
        ):
            raise FrameGraphError(
                "source crop must use normalized displayed A4 coordinates"
            )


def _construct(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    approved_figure_notes: Mapping[str, Sequence[str]] | None,
    annotation_binding: Mapping[str, object] | None,
    annotation_items: Sequence[Mapping[str, object]] | None,
    config: FrameGraphConfig,
) -> dict[str, object]:
    _validated_inputs(source, units, translation)
    if annotation_binding is not None and approved_figure_notes is not None:
        raise FrameGraphError(
            "annotated frame graphs cannot mix legacy approved figure notes"
        )
    if (annotation_binding is None) != (annotation_items is None):
        raise FrameGraphError("annotation binding and items must be supplied together")
    notes = _normalise_notes(approved_figure_notes, units)
    locations, _page_by_band = _source_indexes(source)
    pages, native_by_source, frame_by_id, candidate_by_native = _build_pages(
        source, config
    )
    try:
        continuation_header = _continuation_header(
            style_contract=style_contract,
            resolver=resolver,
            config=config,
        )
        flows, parts, note_flows, auxiliary_flows, items_by_frame = _build_flows(
            units,
            translation,
            locations,
            native_by_source,
            frame_by_id,
            style_contract,
            resolver,
            notes,
            annotation_items,
            config,
        )
    except (KeyError, UnitPartError, ValueError) as exc:
        raise FrameGraphError(
            "target flow cannot be measured deterministically"
        ) from exc
    _complete_band_geometry(
        source,
        pages,
        items_by_frame,
        config,
        annotated=annotation_binding is not None,
    )
    artifact: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "frame-graph",
        "frame_graph_input_hash": _input_hash(
            source,
            units,
            translation,
            style_contract,
            config,
            notes,
            resolver,
            annotation_binding,
        ),
        "font_fingerprint": [
            {
                "role": role,
                "reportlab_name": reportlab_name,
                "sha256": sha256,
            }
            for role, reportlab_name, sha256 in resolver.font_fingerprint
        ],
        "right_panel_bbox_mpt": [
            A4_WIDTH_MPT,
            0,
            A3_LANDSCAPE_WIDTH_MPT,
            A3_LANDSCAPE_HEIGHT_MPT,
        ],
        "flow_spacing": {
            "config_version": config.version,
            "horizontal_padding_mpt": config.horizontal_padding_mpt,
            "vertical_padding_mpt": config.vertical_padding_mpt,
            "block_gap_mpt": config.block_gap_mpt,
            "figure_note_gap_mpt": config.figure_note_gap_mpt,
        },
        "continuation_header": continuation_header,
        "pages": pages,
        "edges": _build_edges(pages, candidate_by_native, frame_by_id),
        "unit_flows": flows,
        "unit_parts": parts,
        "figure_note_flows": note_flows,
    }
    if annotation_binding is not None:
        artifact.update(
            {
                "line_mapping_version": 2,
                "annotation_binding": dict(annotation_binding),
                "auxiliary_flows": auxiliary_flows,
            }
        )
    return artifact


def _build_annotated_frame_graph(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    annotation_binding: Mapping[str, object],
    annotation_items: Sequence[Mapping[str, object]],
    config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
) -> dict[str, object]:
    """Internal v2 constructor used only after annotation-parent validation."""

    artifact = _construct(
        source,
        units,
        translation,
        style_contract=style_contract,
        resolver=resolver,
        approved_figure_notes=None,
        annotation_binding=annotation_binding,
        annotation_items=annotation_items,
        config=config,
    )
    try:
        validate_artifact("frame-graph", artifact)
    except SchemaValidationError as exc:
        raise FrameGraphError(
            "constructed annotated frame graph violates its schema"
        ) from exc
    return artifact


def build_frame_graph(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    approved_figure_notes: Mapping[str, Sequence[str]] | None = None,
    config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
) -> dict[str, object]:
    """Build and schema-check the unsolved Task 13 flow graph."""

    artifact = _construct(
        source,
        units,
        translation,
        style_contract=style_contract,
        resolver=resolver,
        approved_figure_notes=approved_figure_notes,
        annotation_binding=None,
        annotation_items=None,
        config=config,
    )
    try:
        validate_artifact("frame-graph", artifact)
    except SchemaValidationError as exc:
        raise FrameGraphError("constructed frame graph violates its schema") from exc
    return artifact


def validate_frame_graph_against_inputs(
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    artifact: Mapping[str, object],
    *,
    style_contract: TypographyStyleContract,
    resolver: FontRunResolver,
    approved_figure_notes: Mapping[str, Sequence[str]] | None = None,
    config: FrameGraphConfig = DEFAULT_FRAME_GRAPH_CONFIG,
) -> None:
    """Parent-side exact recomputation rejects every derived-field tamper."""

    try:
        validate_artifact("frame-graph", artifact)
    except SchemaValidationError as exc:
        raise FrameGraphError("frame graph artifact is structurally invalid") from exc
    expected = _construct(
        source,
        units,
        translation,
        style_contract=style_contract,
        resolver=resolver,
        approved_figure_notes=approved_figure_notes,
        annotation_binding=None,
        annotation_items=None,
        config=config,
    )
    if canonical_json_bytes(artifact) != canonical_json_bytes(expected):
        raise FrameGraphError("frame graph differs from parent-recomputed content")
