# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Solve complete deterministic right-panel layout from a frozen FrameGraph."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.layout.continuation_dp import (
    ContinuationPlan,
    FlowDpComplexityError,
    FlowDpInfeasible,
    FlowDpLimits,
    FlowPlacement,
    FlowPlan,
    FlowSpec,
    FrameSlot,
    PageSlotTemplates,
    instantiate_slots,
    solve_continuation_distributions,
    solve_fixed_slots,
)
from academic_pdf_en_zh_reader.layout.isotonic import (
    FrozenBounds,
    IsotonicComplexityError,
    IsotonicInfeasibleError,
    IsotonicItem,
    solve_bounded_l1_isotonic,
)
from academic_pdf_en_zh_reader.layout.window_search import (
    WindowSearchComplexityError,
    enumerate_release_windows,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LINE_FIELDS_V1 = (
    "index",
    "target_start",
    "target_end",
    "text",
    "style_id",
    "width_mpt",
    "line_height_mpt",
    "ascent_mpt",
    "descent_mpt",
    "runs",
    "line_box_hash",
)
_LINE_FIELDS_V2 = (
    *_LINE_FIELDS_V1[:-1],
    "composite_start",
    "composite_end",
    "synthetic_annotation_ids",
    "line_box_hash",
)


class LayoutSolverError(ValueError):
    """Base class for deterministic layout failures."""


class LayoutInfeasibleError(LayoutSolverError):
    """Raised only after all finite legal placements are exhausted."""


class LayoutComplexityError(LayoutSolverError):
    """Stable complexity failure; never replaced by a greedy degradation."""

    code = "LAYOUT_COMPLEXITY_LIMIT"

    def __init__(
        self,
        *,
        limit_name: str,
        observed: int,
        maximum: int,
    ) -> None:
        self.limit_name = limit_name
        self.observed = observed
        self.maximum = maximum
        super().__init__(f"{limit_name} observed {observed} exceeds {maximum}")


@dataclass(frozen=True, slots=True)
class LayoutLimits:
    """Versioned finite solver policy; flow spacing comes from FrameGraph."""

    version: int = 1
    band_gap_mpt: int = 4_000
    min_lines_before_break: int = 2
    min_lines_after_break: int = 2
    heading_with_next_lines: int = 2
    max_window_radius: int = 512
    max_window_attempts: int = 8_192
    max_isotonic_items: int = 512
    max_breakpoints_per_unit: int = 512
    max_dp_states: int = 100_000
    max_dp_transitions: int = 500_000
    max_continuation_distributions: int = 4_096
    max_continuation_pages_per_source_page: int = 32
    max_total_continuation_pages: int = 128

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(type(value) is not int for value in values.values()):
            raise LayoutSolverError("layout limits must contain integers")
        nonnegative = {"band_gap_mpt"}
        if self.version != 1 or any(
            value < 0 if name in nonnegative else value < 1
            for name, value in values.items()
            if name != "version"
        ):
            raise LayoutSolverError("layout limits are out of range")


DEFAULT_LAYOUT_LIMITS = LayoutLimits()


@dataclass(frozen=True, slots=True)
class _ContentRecord:
    spec: FlowSpec
    style: Mapping[str, object]
    line_sequence_hash: str
    anchor: Mapping[str, object] | None
    anchor_candidates: tuple[Mapping[str, object], ...]
    content_kind: str


def _line_fields(frame_graph: Mapping[str, object]) -> tuple[str, ...]:
    return (
        _LINE_FIELDS_V2
        if frame_graph.get("line_mapping_version") == 2
        else _LINE_FIELDS_V1
    )


@dataclass(frozen=True, slots=True)
class _BandPlacement:
    top_mpt: int
    height_mpt: int
    diagnostic_height_mpt: int


@dataclass(frozen=True, slots=True)
class _BlockPlacement:
    preferred_top_mpt: int
    solved_top_mpt: int


@dataclass(frozen=True, slots=True)
class _FlowSpacing:
    config_version: int
    horizontal_padding_mpt: int
    vertical_padding_mpt: int
    block_gap_mpt: int
    figure_note_gap_mpt: int


def _flow_spacing(frame_graph: Mapping[str, object]) -> _FlowSpacing:
    raw = frame_graph["flow_spacing"]
    version = int(raw["config_version"])  # type: ignore[index]
    if version != 1:
        raise LayoutSolverError(
            f"unsupported flow_spacing config_version {version}; expected 1"
        )
    return _FlowSpacing(
        config_version=version,
        horizontal_padding_mpt=int(raw["horizontal_padding_mpt"]),  # type: ignore[index]
        vertical_padding_mpt=int(raw["vertical_padding_mpt"]),  # type: ignore[index]
        block_gap_mpt=int(raw["block_gap_mpt"]),  # type: ignore[index]
        figure_note_gap_mpt=int(raw["figure_note_gap_mpt"]),  # type: ignore[index]
    )


def _continuation_label(
    header: Mapping[str, object],
    *,
    source_page_number: int,
    continuation_index: int,
    page_height_mpt: int,
    right_panel_right_mpt: int,
    horizontal_padding_mpt: int,
) -> dict[str, object]:
    """Freeze the exact page-specific placement of the graph-owned header."""

    x_mpt = right_panel_right_mpt - horizontal_padding_mpt - int(header["width_mpt"])
    baseline_y_mpt = (
        page_height_mpt - int(header["top_inset_mpt"]) - int(header["ascent_mpt"])
    )
    label: dict[str, object] = {
        "contract_version": header["contract_version"],
        "header_hash": header["header_hash"],
        "source_page_number": source_page_number,
        "continuation_index": continuation_index,
        "text": header["text"],
        "style": dict(header["style"]),  # type: ignore[arg-type]
        "runs": [dict(run) for run in header["runs"]],  # type: ignore[union-attr]
        "width_mpt": header["width_mpt"],
        "line_height_mpt": header["line_height_mpt"],
        "ascent_mpt": header["ascent_mpt"],
        "descent_mpt": header["descent_mpt"],
        "color_token": header["color_token"],
        "color_hex": header["color_hex"],
        "x_mpt": x_mpt,
        "baseline_y_mpt": baseline_y_mpt,
        "bbox_mpt": [
            x_mpt,
            baseline_y_mpt + int(header["descent_mpt"]),
            x_mpt + int(header["width_mpt"]),
            baseline_y_mpt + int(header["ascent_mpt"]),
        ],
    }
    label["label_hash"] = sha256_canonical(label)
    return label


def _complexity(exc: object) -> LayoutComplexityError:
    return LayoutComplexityError(
        limit_name=str(getattr(exc, "limit_name", "layout_complexity")),
        observed=int(getattr(exc, "observed", 0)),
        maximum=int(getattr(exc, "maximum", 0)),
    )


def _validate_limits(limits: LayoutLimits) -> None:
    if not isinstance(limits, LayoutLimits):
        raise LayoutSolverError("limits must be a LayoutLimits value")


def _content_records(
    frame_graph: Mapping[str, object],
    limits: LayoutLimits,
    spacing: _FlowSpacing,
) -> tuple[tuple[FlowSpec, ...], dict[str, _ContentRecord]]:
    annotated = frame_graph.get("line_mapping_version") == 2
    note_by_unit: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for note in frame_graph["figure_note_flows"]:  # type: ignore[index]
        note_by_unit[str(note["unit_id"])].append(note)
    for notes in note_by_unit.values():
        notes.sort(key=lambda item: int(item["note_index"]))

    flows: list[FlowSpec] = []
    records: dict[str, _ContentRecord] = {}
    for raw in frame_graph["unit_flows"]:  # type: ignore[index]
        unit_id = str(raw["unit_id"])
        breaks = tuple(
            (int(item["after_line"]), str(item["kind"])) for item in raw["legal_breaks"]
        )
        if len(breaks) > limits.max_breakpoints_per_unit:
            raise LayoutComplexityError(
                limit_name="max_breakpoints_per_unit",
                observed=len(breaks),
                maximum=limits.max_breakpoints_per_unit,
            )
        spec = FlowSpec(
            content_id=unit_id,
            unit_id=unit_id,
            role=str(raw["role"]),
            lines=tuple(raw["lines"]),
            legal_breaks=breaks,
            allowed_native_frame_ids=tuple(raw["allowed_native_frame_ids"]),
            continuation_owner_page_number=int(raw["continuation_owner_page_number"]),
            gap_before_mpt=spacing.block_gap_mpt,
        )
        flows.append(spec)
        records[spec.content_id] = _ContentRecord(
            spec=spec,
            style=raw["style"],
            line_sequence_hash=str(raw["line_sequence_hash"]),
            anchor=raw["anchor"],
            anchor_candidates=tuple(raw.get("source_anchor_candidates", ())),
            content_kind="unit",
        )
        attached: Sequence[Mapping[str, object]]
        if annotated:
            attached = sorted(
                (
                    item
                    for item in frame_graph["auxiliary_flows"]  # type: ignore[index]
                    if item["unit_id"] == unit_id
                ),
                key=lambda item: int(item["order_after_unit"]),
            )
        else:
            attached = note_by_unit.get(unit_id, ())
        for note in attached:
            note_id = str(note["id"])
            note_spec = FlowSpec(
                content_id=note_id,
                unit_id=unit_id,
                role="auxiliary",
                lines=tuple(note["lines"]),
                legal_breaks=(),
                allowed_native_frame_ids=(
                    tuple(str(value) for value in note["allowed_native_frame_ids"])
                    if annotated
                    else (str(note["frame_id"]),)
                ),
                continuation_owner_page_number=int(
                    note.get(
                        "continuation_owner_page_number",
                        spec.continuation_owner_page_number,
                    )
                ),
                gap_before_mpt=spacing.figure_note_gap_mpt,
                is_note=True,
            )
            flows.append(note_spec)
            records[note_id] = _ContentRecord(
                spec=note_spec,
                style=note["style"],
                line_sequence_hash=str(note["line_sequence_hash"]),
                anchor=None,
                anchor_candidates=(),
                content_kind="auxiliary" if annotated else "figure-note",
            )
    if not flows:
        raise LayoutSolverError("frame graph contains no target flows")
    return tuple(flows), records


def _page_templates(
    frame_graph: Mapping[str, object],
    spacing: _FlowSpacing,
) -> tuple[PageSlotTemplates, ...]:
    pages: list[PageSlotTemplates] = []
    continuation_reserve = int(
        frame_graph["continuation_header"]["reserve_height_mpt"]  # type: ignore[index]
    )
    for page in frame_graph["pages"]:  # type: ignore[index]
        page_height = int(page["page_height_mpt"])
        native_capacity = page_height - 2 * spacing.vertical_padding_mpt
        continuation_capacity = (
            page_height - continuation_reserve - 2 * spacing.vertical_padding_mpt
        )
        if native_capacity <= 0 or continuation_capacity <= 0:
            raise LayoutInfeasibleError(
                "page cannot contain the configured padding and continuation header"
            )
        frame_by_id = {str(frame["id"]): frame for frame in page["frames"]}
        native_slots: list[FrameSlot] = []
        candidates: list[FrameSlot] = []
        for band in page["bands"]:
            native_ids = band["native_frame_ids"]
            candidate_ids = band["continuation_template_frame_ids"]
            for native_id, candidate_id in zip(native_ids, candidate_ids, strict=True):
                native = frame_by_id[str(native_id)]
                candidate = frame_by_id[str(candidate_id)]

                def slot(
                    raw: Mapping[str, object],
                    *,
                    instance_id: str,
                    template_id: str,
                    native_frame_id: str,
                    page_kind: str,
                    capacity_mpt: int,
                    page_height_mpt: int,
                    page_top_reserve_mpt: int,
                ) -> FrameSlot:
                    return FrameSlot(
                        instance_id=instance_id,
                        template_frame_id=template_id,
                        native_frame_id=native_frame_id,
                        source_page_number=int(raw["source_page_number"]),
                        page_kind=page_kind,
                        continuation_index=0,
                        capacity_mpt=capacity_mpt,
                        page_height_mpt=page_height_mpt,
                        page_top_reserve_mpt=page_top_reserve_mpt,
                        bbox_mpt=tuple(raw["bbox_mpt"]),  # type: ignore[arg-type]
                        text_left_mpt=int(raw["text_left_mpt"]),
                        text_right_mpt=int(raw["text_right_mpt"]),
                        source_band_id=str(raw["source_band_id"]),
                        source_column_id=str(raw["source_column_id"]),
                        band_index=int(raw["band_index"]),
                        column_index=int(raw["column_index"]),
                        column_count=int(raw["column_count"]),
                        width_ratio_ppm=int(raw["width_ratio_ppm"]),
                    )

                native_slots.append(
                    slot(
                        native,
                        instance_id=str(native_id),
                        template_id=str(native_id),
                        native_frame_id=str(native_id),
                        page_kind="native",
                        capacity_mpt=native_capacity,
                        page_height_mpt=page_height,
                        page_top_reserve_mpt=0,
                    )
                )
                candidates.append(
                    slot(
                        candidate,
                        instance_id=str(candidate_id),
                        template_id=str(candidate_id),
                        native_frame_id=str(native_id),
                        page_kind="continuation",
                        capacity_mpt=continuation_capacity,
                        page_height_mpt=page_height,
                        page_top_reserve_mpt=continuation_reserve,
                    )
                )
        pages.append(
            PageSlotTemplates(
                source_page_number=int(page["page_number"]),
                native_slots=tuple(native_slots),
                continuation_templates=tuple(candidates),
            )
        )
    return tuple(pages)


def _dp_limits(limits: LayoutLimits, spacing: _FlowSpacing) -> FlowDpLimits:
    return FlowDpLimits(
        max_dp_states=limits.max_dp_states,
        max_dp_transitions=limits.max_dp_transitions,
        max_continuation_distributions=limits.max_continuation_distributions,
        max_continuation_pages_per_source_page=(
            limits.max_continuation_pages_per_source_page
        ),
        max_total_continuation_pages=limits.max_total_continuation_pages,
        min_lines_before_break=limits.min_lines_before_break,
        min_lines_after_break=limits.min_lines_after_break,
        heading_with_next_lines=limits.heading_with_next_lines,
        vertical_padding_mpt=spacing.vertical_padding_mpt,
        band_gap_mpt=limits.band_gap_mpt,
    )


def _first_violation(
    tops: Sequence[int],
    heights: Sequence[int],
    gaps: Sequence[int],
    *,
    frame_top: int,
    frame_bottom: int,
) -> int | None:
    if tops[0] < frame_top:
        return 0
    for index in range(1, len(tops)):
        if tops[index] < tops[index - 1] + heights[index - 1] + gaps[index - 1]:
            return index
    if tops[-1] + heights[-1] > frame_bottom:
        return len(tops) - 1
    return None


def _solve_chain(
    *,
    chain_id: str,
    scope_kind: str,
    preferred_tops: Sequence[int],
    heights: Sequence[int],
    gaps: Sequence[int],
    weights: Sequence[int],
    frame_top: int,
    frame_bottom: int,
    limits: LayoutLimits,
    attempts: list[dict[str, object]],
) -> tuple[int, ...]:
    tops = list(preferred_tops)
    if not tops:
        return ()
    if len(tops) != len(heights) or len(tops) != len(gaps):
        raise LayoutSolverError("chain geometry arrays disagree")
    if (
        _first_violation(
            tops,
            heights,
            gaps,
            frame_top=frame_top,
            frame_bottom=frame_bottom,
        )
        is None
    ):
        attempts.append(
            {
                "attempt_index": len(attempts),
                "scope_kind": scope_kind,
                "chain_id": chain_id,
                "release_stage": "current",
                "window_start": 0,
                "window_end": 1,
                "covers_whole_scope": len(tops) == 1,
                "frame_top_mpt": frame_top,
                "frame_bottom_mpt": frame_bottom,
                "result": "accepted",
                "minimum_d_mpt": 0,
                "weighted_l1_cost": 0,
                "frozen_before_hash": sha256_canonical({"outside": tops[1:]}),
                "frozen_after_hash": sha256_canonical({"outside": tops[1:]}),
            }
        )
        return tuple(tops)

    while True:
        pivot = _first_violation(
            tops,
            heights,
            gaps,
            frame_top=frame_top,
            frame_bottom=frame_bottom,
        )
        if pivot is None:
            return tuple(tops)
        try:
            windows = enumerate_release_windows(
                pivot_index=pivot,
                item_count=len(tops),
                scope_kind=scope_kind,  # type: ignore[arg-type]
                maximum_local_radius=limits.max_window_radius,
            )
        except WindowSearchComplexityError as exc:
            raise _complexity(exc) from exc
        accepted = False
        for window in windows:
            if len(attempts) + 1 > limits.max_window_attempts:
                raise LayoutComplexityError(
                    limit_name="max_window_attempts",
                    observed=len(attempts) + 1,
                    maximum=limits.max_window_attempts,
                )
            start = window.start_index
            end = window.end_index
            outside = [
                [index, tops[index]]
                for index in range(len(tops))
                if not start <= index < end
            ]
            frozen = FrozenBounds(
                previous_bottom_mpt=(
                    tops[start - 1] + heights[start - 1] if start > 0 else None
                ),
                gap_before_mpt=gaps[start - 1] if start > 0 else 0,
                next_top_mpt=tops[end] if end < len(tops) else None,
                gap_after_mpt=gaps[end - 1] if end < len(tops) else 0,
            )
            try:
                solution = solve_bounded_l1_isotonic(
                    tuple(
                        IsotonicItem(
                            preferred_top_mpt=int(preferred_tops[index]),
                            height_mpt=int(heights[index]),
                            gap_after_mpt=(int(gaps[index]) if index + 1 < end else 0),
                            weight=int(weights[index]),
                        )
                        for index in range(start, end)
                    ),
                    frame_top_mpt=frame_top,
                    frame_bottom_mpt=frame_bottom,
                    frozen=frozen,
                    max_isotonic_items=limits.max_isotonic_items,
                )
            except IsotonicComplexityError as exc:
                raise _complexity(exc) from exc
            except IsotonicInfeasibleError:
                attempts.append(
                    {
                        "attempt_index": len(attempts),
                        "scope_kind": scope_kind,
                        "chain_id": chain_id,
                        "release_stage": window.stage,
                        "window_start": start,
                        "window_end": end,
                        "covers_whole_scope": window.covers_whole_scope,
                        "frame_top_mpt": frame_top,
                        "frame_bottom_mpt": frame_bottom,
                        "result": "rejected",
                        "minimum_d_mpt": None,
                        "weighted_l1_cost": None,
                        "frozen_before_hash": sha256_canonical({"outside": outside}),
                        "frozen_after_hash": sha256_canonical({"outside": outside}),
                    }
                )
                continue
            tops[start:end] = solution.tops_mpt
            outside_after = [
                [index, tops[index]]
                for index in range(len(tops))
                if not start <= index < end
            ]
            attempts.append(
                {
                    "attempt_index": len(attempts),
                    "scope_kind": scope_kind,
                    "chain_id": chain_id,
                    "release_stage": window.stage,
                    "window_start": start,
                    "window_end": end,
                    "covers_whole_scope": window.covers_whole_scope,
                    "frame_top_mpt": frame_top,
                    "frame_bottom_mpt": frame_bottom,
                    "result": "accepted",
                    "minimum_d_mpt": (solution.minimum_max_displacement_mpt),
                    "weighted_l1_cost": solution.weighted_l1_cost,
                    "frozen_before_hash": sha256_canonical({"outside": outside}),
                    "frozen_after_hash": sha256_canonical({"outside": outside_after}),
                }
            )
            accepted = True
            break
        if not accepted:
            raise LayoutInfeasibleError(
                f"no legal position exists for released {scope_kind} chain"
            )


def _page_key(slot: FrameSlot) -> tuple[int, str, int]:
    return (slot.source_page_number, slot.page_kind, slot.continuation_index)


def _placement_height(
    placement: FlowPlacement,
    record: _ContentRecord,
) -> int:
    return sum(
        int(line["line_height_mpt"])
        for line in record.spec.lines[placement.line_start : placement.line_end]
    )


def _selected_page_keys(
    pages: Sequence[PageSlotTemplates],
    continuation_counts: Mapping[int, int],
) -> tuple[tuple[int, str, int], ...]:
    result: list[tuple[int, str, int]] = []
    for page in pages:
        result.append((page.source_page_number, "native", 0))
        result.extend(
            (page.source_page_number, "continuation", index)
            for index in range(
                1,
                continuation_counts.get(page.source_page_number, 0) + 1,
            )
        )
    return tuple(result)


def _band_positions(
    *,
    frame_graph: Mapping[str, object],
    pages: Sequence[PageSlotTemplates],
    slots: Sequence[FrameSlot],
    placements: Sequence[FlowPlacement],
    records: Mapping[str, _ContentRecord],
    continuation_counts: Mapping[int, int],
    limits: LayoutLimits,
    spacing: _FlowSpacing,
    attempts: list[dict[str, object]],
) -> tuple[
    dict[tuple[tuple[int, str, int], str], _BandPlacement],
    list[dict[str, object]],
]:
    graph_page = {
        int(page["page_number"]): page
        for page in frame_graph["pages"]  # type: ignore[index]
    }
    by_slot: dict[int, list[FlowPlacement]] = defaultdict(list)
    for placement in placements:
        by_slot[placement.slot_index].append(placement)
    slot_index_by_id = {slot.instance_id: index for index, slot in enumerate(slots)}
    result: dict[tuple[tuple[int, str, int], str], _BandPlacement] = {}
    traces: list[dict[str, object]] = []
    page_keys = _selected_page_keys(pages, continuation_counts)
    for output_page_number, key in enumerate(page_keys, start=1):
        source_page, page_kind, continuation_index = key
        raw_page = graph_page[source_page]
        page_reserves = {
            slot.page_top_reserve_mpt for slot in slots if _page_key(slot) == key
        }
        if len(page_reserves) != 1:
            raise LayoutSolverError("output page has inconsistent top reserves")
        page_top_reserve = next(iter(page_reserves))
        band_heights: list[int] = []
        preferred: list[int] = []
        diagnostics: list[int] = []
        band_ids: list[str] = []
        for band in raw_page["bands"]:
            band_id = str(band["source_band_id"])
            column_heights: list[int] = []
            for slot in slots:
                if _page_key(slot) != key or slot.source_band_id != band_id:
                    continue
                index = slot_index_by_id[slot.instance_id]
                used = 0
                for placement in by_slot.get(index, ()):
                    record = records[placement.content_id]
                    if used:
                        used += record.spec.gap_before_mpt
                    used += _placement_height(placement, record)
                column_heights.append(used)
            content = max(column_heights, default=0)
            band_heights.append(max(1, content + 2 * spacing.vertical_padding_mpt))
            preferred.append(int(band["preferred_top_offset_mpt"]))
            diagnostics.append(int(band["initial_unsplit_content_height_mpt"]))
            band_ids.append(band_id)
        gaps = [limits.band_gap_mpt] * len(band_heights)
        if gaps:
            gaps[-1] = 0
        solved = _solve_chain(
            chain_id=(f"page:{source_page}:{page_kind}:{continuation_index}:bands"),
            scope_kind="band",
            preferred_tops=preferred,
            heights=band_heights,
            gaps=gaps,
            weights=[1] * len(band_heights),
            frame_top=page_top_reserve,
            frame_bottom=int(raw_page["page_height_mpt"]),
            limits=limits,
            attempts=attempts,
        )
        for band_id, top, height, diagnostic in zip(
            band_ids, solved, band_heights, diagnostics, strict=True
        ):
            result[(key, band_id)] = _BandPlacement(
                top_mpt=top,
                height_mpt=height,
                diagnostic_height_mpt=diagnostic,
            )
            traces.append(
                {
                    "output_page_number": output_page_number,
                    "source_page_number": source_page,
                    "continuation_index": continuation_index,
                    "source_band_id": band_id,
                    "scope_kind": "band",
                    "diagnostic_initial_unsplit_height_mpt": diagnostic,
                    "actual_content_height_mpt": height,
                }
            )
    return result, traces


def _selected_anchors(
    *,
    slots: Sequence[FrameSlot],
    placements: Sequence[FlowPlacement],
    records: Mapping[str, _ContentRecord],
) -> dict[int, Mapping[str, object]]:
    """Select the visible source-page anchor for each actual first unit part."""

    selected: dict[int, Mapping[str, object]] = {}
    for placement_index, placement in enumerate(placements):
        record = records[placement.content_id]
        if record.content_kind != "unit" or placement.line_start != 0:
            continue
        slot = slots[placement.slot_index]
        if record.anchor_candidates:
            matches = [
                candidate
                for candidate in record.anchor_candidates
                if int(candidate["source_page_number"]) == slot.source_page_number
            ]
            if len(matches) != 1:
                raise LayoutInfeasibleError(
                    "actual first unit part has no unique visible source anchor"
                )
            selected[placement_index] = matches[0]
        elif (
            record.anchor is not None
            and slot.page_kind == "native"
            and slot.source_page_number == int(record.anchor["source_page_number"])
        ):
            selected[placement_index] = record.anchor
    return selected


def _block_positions(
    *,
    slots: Sequence[FrameSlot],
    placements: Sequence[FlowPlacement],
    records: Mapping[str, _ContentRecord],
    bands: Mapping[tuple[tuple[int, str, int], str], _BandPlacement],
    limits: LayoutLimits,
    spacing: _FlowSpacing,
    attempts: list[dict[str, object]],
    selected_anchors: Mapping[int, Mapping[str, object]],
) -> dict[int, _BlockPlacement]:
    by_slot: dict[int, list[tuple[int, FlowPlacement]]] = defaultdict(list)
    for placement_index, placement in enumerate(placements):
        by_slot[placement.slot_index].append((placement_index, placement))
    positions: dict[int, _BlockPlacement] = {}
    for slot_index in sorted(by_slot):
        slot = slots[slot_index]
        items = by_slot[slot_index]
        band = bands[(_page_key(slot), slot.source_band_id)]
        frame_top = band.top_mpt + spacing.vertical_padding_mpt
        frame_bottom = band.top_mpt + band.height_mpt - spacing.vertical_padding_mpt
        heights: list[int] = []
        gaps: list[int] = []
        preferred: list[int] = []
        weights: list[int] = []
        natural = frame_top
        for item_index, (placement_index, placement) in enumerate(items):
            record = records[placement.content_id]
            height = _placement_height(placement, record)
            heights.append(height)
            if item_index + 1 < len(items):
                following = records[items[item_index + 1][1].content_id]
                gaps.append(following.spec.gap_before_mpt)
            else:
                gaps.append(0)
            anchor = selected_anchors.get(placement_index)
            if anchor is not None:
                first_line = record.spec.lines[placement.line_start]
                preferred.append(
                    int(anchor["source_visual_center_offset_mpt"])
                    - (int(first_line["ascent_mpt"]) - int(first_line["descent_mpt"]))
                    // 2
                )
                weights.append(2)
            else:
                preferred.append(natural)
                weights.append(1)
            natural += height + gaps[-1]
        solved = _solve_chain(
            chain_id=slot.instance_id,
            scope_kind="column",
            preferred_tops=preferred,
            heights=heights,
            gaps=gaps,
            weights=weights,
            frame_top=frame_top,
            frame_bottom=frame_bottom,
            limits=limits,
            attempts=attempts,
        )
        for item_index, ((placement_index, _placement), top) in enumerate(
            zip(items, solved, strict=True)
        ):
            positions[placement_index] = _BlockPlacement(
                preferred_top_mpt=preferred[item_index],
                solved_top_mpt=top,
            )
    return positions


def _output_pages(
    *,
    frame_graph: Mapping[str, object],
    pages: Sequence[PageSlotTemplates],
    slots: Sequence[FrameSlot],
    plan: FlowPlan,
    records: Mapping[str, _ContentRecord],
    continuation_counts: Mapping[int, int],
    band_positions: Mapping[tuple[tuple[int, str, int], str], _BandPlacement],
    block_positions: Mapping[int, _BlockPlacement],
    selected_anchors: Mapping[int, Mapping[str, object]],
) -> list[dict[str, object]]:
    page_keys = _selected_page_keys(pages, continuation_counts)
    output_number = {key: index for index, key in enumerate(page_keys, start=1)}
    graph_pages = {
        int(page["page_number"]): page
        for page in frame_graph["pages"]  # type: ignore[index]
    }
    continuation_header = frame_graph["continuation_header"]
    horizontal_padding = int(
        frame_graph["flow_spacing"]["horizontal_padding_mpt"]  # type: ignore[index]
    )
    right_panel = frame_graph["right_panel_bbox_mpt"]
    annotated = frame_graph.get("line_mapping_version") == 2
    line_fields = _line_fields(frame_graph)
    output: dict[tuple[int, str, int], dict[str, object]] = {}
    for key in page_keys:
        source_page, page_kind, continuation_index = key
        raw_page = graph_pages[source_page]
        page_height = int(raw_page["page_height_mpt"])
        page_slots = [slot for slot in slots if _page_key(slot) == key]
        solved_bands: list[dict[str, object]] = []
        solved_frames: list[dict[str, object]] = []
        for raw_band in raw_page["bands"]:
            band_id = str(raw_band["source_band_id"])
            band = band_positions[(key, band_id)]
            band_frame_ids = [
                slot.instance_id
                for slot in page_slots
                if slot.source_band_id == band_id
            ]
            solved_bands.append(
                {
                    "id": (
                        f"layout:page:{output_number[key]:04d}:"
                        f"band:{int(raw_band['band_index']):04d}"
                    ),
                    "source_band_id": band_id,
                    "band_index": int(raw_band["band_index"]),
                    "preferred_top_offset_mpt": int(
                        raw_band["preferred_top_offset_mpt"]
                    ),
                    "solved_top_offset_mpt": band.top_mpt,
                    "height_mpt": band.height_mpt,
                    "bbox_mpt": [
                        int(right_panel[0]),
                        page_height - band.top_mpt - band.height_mpt,
                        int(right_panel[2]),
                        page_height - band.top_mpt,
                    ],
                    "frame_ids": band_frame_ids,
                }
            )
            for slot in page_slots:
                if slot.source_band_id != band_id:
                    continue
                solved_frames.append(
                    {
                        "id": slot.instance_id,
                        "template_frame_id": slot.template_frame_id,
                        "native_frame_id": slot.native_frame_id,
                        "source_page_number": slot.source_page_number,
                        "source_band_id": slot.source_band_id,
                        "source_column_id": slot.source_column_id,
                        "band_index": slot.band_index,
                        "column_index": slot.column_index,
                        "column_count": slot.column_count,
                        "width_ratio_ppm": slot.width_ratio_ppm,
                        "bbox_mpt": [
                            slot.bbox_mpt[0],
                            page_height - band.top_mpt - band.height_mpt,
                            slot.bbox_mpt[2],
                            page_height - band.top_mpt,
                        ],
                        "text_left_mpt": slot.text_left_mpt,
                        "text_right_mpt": slot.text_right_mpt,
                    }
                )
        output[key] = {
            "page_number": output_number[key],
            "source_page_number": key[0],
            "page_kind": key[1],
            "continuation_index": key[2],
            "continuation_label": (
                None
                if page_kind == "native"
                else _continuation_label(
                    continuation_header,  # type: ignore[arg-type]
                    source_page_number=source_page,
                    continuation_index=continuation_index,
                    page_height_mpt=page_height,
                    right_panel_right_mpt=int(right_panel[2]),
                    horizontal_padding_mpt=horizontal_padding,
                )
            ),
            "page_height_mpt": page_height,
            "utilization_basis_points": 0,
            "bands": solved_bands,
            "frames": solved_frames,
            "blocks": [],
            "dashed_leaders": [],
        }

    part_index: dict[str, int] = defaultdict(int)
    used_by_page: dict[tuple[int, str, int], int] = defaultdict(int)
    capacity_by_page: dict[tuple[int, str, int], int] = defaultdict(int)
    for slot in slots:
        capacity_by_page[_page_key(slot)] += slot.capacity_mpt

    for placement_index, placement in enumerate(plan.placements):
        slot = slots[placement.slot_index]
        key = _page_key(slot)
        record = records[placement.content_id]
        position = block_positions[placement_index]
        top = position.solved_top_mpt
        height = _placement_height(placement, record)
        page_height = slot.page_height_mpt
        selected_lines = record.spec.lines[placement.line_start : placement.line_end]
        line_output: list[dict[str, object]] = []
        cursor = top
        for line in selected_lines:
            copied = {field: line[field] for field in line_fields}
            copied["x_mpt"] = slot.text_left_mpt
            copied["baseline_y_mpt"] = page_height - cursor - int(line["ascent_mpt"])
            line_output.append(copied)
            cursor += int(line["line_height_mpt"])
        current_part = part_index[placement.content_id]
        part_index[placement.content_id] += 1
        max_width = max(int(line["width_mpt"]) for line in selected_lines)
        block = {
            "id": f"layout:{placement.content_id}:part:{current_part:04d}",
            "content_id": placement.content_id,
            "content_kind": record.content_kind,
            "unit_id": placement.unit_id,
            "part_index": current_part,
            "frame_id": slot.instance_id,
            "template_frame_id": slot.template_frame_id,
            "line_start": placement.line_start,
            "line_end": placement.line_end,
            "line_sequence_hash": record.line_sequence_hash,
            "style": dict(record.style),
            "preferred_top_offset_mpt": position.preferred_top_mpt,
            "solved_top_offset_mpt": top,
            "creates_anchor": placement_index in selected_anchors,
            "bbox_mpt": [
                slot.text_left_mpt,
                page_height - top - height,
                min(slot.text_right_mpt, slot.text_left_mpt + max_width),
                page_height - top,
            ],
            "lines": line_output,
        }
        if annotated:
            selected_anchor = selected_anchors.get(placement_index)
            block["selected_anchor"] = (
                dict(selected_anchor) if selected_anchor is not None else None
            )
        output[key]["blocks"].append(block)  # type: ignore[index]
        used_by_page[key] += height

    for key, page in output.items():
        capacity = capacity_by_page[key]
        page["utilization_basis_points"] = (
            min(10_000, used_by_page[key] * 10_000 // capacity) if capacity else 0
        )
    return [output[key] for key in page_keys]


def validate_layout_against_frame_graph(
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
) -> None:
    """Rebind every output line and placement to the frozen Task 13 graph."""

    try:
        validate_artifact("frame-graph", frame_graph)
        validate_artifact("layout", layout)
    except SchemaValidationError as exc:
        raise LayoutSolverError("layout or frame graph schema is invalid") from exc
    annotated = frame_graph.get("line_mapping_version") == 2
    if layout.get("line_mapping_version") != (2 if annotated else None):
        raise LayoutSolverError("layout line mapping version differs from FrameGraph")
    line_fields = _line_fields(frame_graph)
    if (
        layout["frame_graph_input_hash"] != frame_graph["frame_graph_input_hash"]
        or layout["frame_graph_hash"] != sha256_canonical(frame_graph)
        or layout["right_panel_bbox_mpt"] != frame_graph["right_panel_bbox_mpt"]
        or layout["font_fingerprint"] != frame_graph["font_fingerprint"]
        or layout["flow_spacing"] != frame_graph["flow_spacing"]
    ):
        raise LayoutSolverError("layout is bound to a different FrameGraph artifact")

    graph_pages = {
        int(page["page_number"]): page
        for page in frame_graph["pages"]  # type: ignore[index]
    }
    continuation_header = frame_graph["continuation_header"]
    horizontal_padding = int(
        frame_graph["flow_spacing"]["horizontal_padding_mpt"]  # type: ignore[index]
    )
    raw_frames: dict[str, Mapping[str, object]] = {}
    for graph_page in graph_pages.values():
        page_frames = {str(frame["id"]): frame for frame in graph_page["frames"]}
        raw_frames.update(page_frames)

    notes_by_unit: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for note in frame_graph["figure_note_flows"]:  # type: ignore[index]
        notes_by_unit[str(note["unit_id"])].append(note)
    for notes in notes_by_unit.values():
        notes.sort(key=lambda note: int(note["note_index"]))
    auxiliary_by_unit: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for auxiliary in frame_graph.get("auxiliary_flows", []):
        auxiliary_by_unit[str(auxiliary["unit_id"])].append(auxiliary)
    for auxiliary in auxiliary_by_unit.values():
        auxiliary.sort(key=lambda item: int(item["order_after_unit"]))
    raw_by_content: dict[str, Mapping[str, object]] = {}
    for flow in frame_graph["unit_flows"]:  # type: ignore[index]
        unit_id = str(flow["unit_id"])
        raw_by_content[unit_id] = flow
        for note in notes_by_unit.get(unit_id, ()):
            raw_by_content[str(note["id"])] = note
        for auxiliary in auxiliary_by_unit.get(unit_id, ()):
            raw_by_content[str(auxiliary["id"])] = auxiliary

    seen_lines: dict[str, list[int]] = defaultdict(list)
    seen_parts: dict[str, list[int]] = defaultdict(list)
    content_rank = {identifier: rank for rank, identifier in enumerate(raw_by_content)}
    observed_ranks: list[int] = []
    for page in layout["pages"]:  # type: ignore[index]
        source_page_number = int(page["source_page_number"])
        graph_page = graph_pages.get(source_page_number)
        if graph_page is None:
            raise LayoutSolverError("layout references an unknown source page")
        expected_label = (
            None
            if page["page_kind"] == "native"
            else _continuation_label(
                continuation_header,  # type: ignore[arg-type]
                source_page_number=source_page_number,
                continuation_index=int(page["continuation_index"]),
                page_height_mpt=int(page["page_height_mpt"]),
                right_panel_right_mpt=int(frame_graph["right_panel_bbox_mpt"][2]),  # type: ignore[index]
                horizontal_padding_mpt=horizontal_padding,
            )
        )
        if page["continuation_label"] != expected_label:
            raise LayoutSolverError(
                "layout continuation label differs from its FrameGraph header"
            )
        graph_bands = list(graph_page["bands"])
        if len(page["bands"]) != len(graph_bands):
            raise LayoutSolverError("layout changed the mirrored band count")
        for solved_band, graph_band in zip(page["bands"], graph_bands, strict=True):
            if (
                solved_band["source_band_id"] != graph_band["source_band_id"]
                or solved_band["band_index"] != graph_band["band_index"]
                or solved_band["preferred_top_offset_mpt"]
                != graph_band["preferred_top_offset_mpt"]
            ):
                raise LayoutSolverError("layout changed mirrored band identity")
            if page["page_kind"] == "continuation" and int(
                solved_band["solved_top_offset_mpt"]
            ) < int(continuation_header["reserve_height_mpt"]):
                raise LayoutSolverError(
                    "continuation band intrudes into the reserved header"
                )

        expected_template_ids: list[str] = []
        expected_native_ids: list[str] = []
        for graph_band in graph_bands:
            native_ids = [str(value) for value in graph_band["native_frame_ids"]]
            candidate_ids = [
                str(value) for value in graph_band["continuation_template_frame_ids"]
            ]
            if page["page_kind"] == "native":
                expected_template_ids.extend(native_ids)
            else:
                expected_template_ids.extend(candidate_ids)
            expected_native_ids.extend(native_ids)
        if [frame["template_frame_id"] for frame in page["frames"]] != (
            expected_template_ids
        ):
            raise LayoutSolverError("layout changed mirrored frame order")

        frame_by_id: dict[str, Mapping[str, object]] = {}
        for frame, expected_template, expected_native in zip(
            page["frames"],
            expected_template_ids,
            expected_native_ids,
            strict=True,
        ):
            template = raw_frames[expected_template]
            expected_instance = (
                expected_template
                if page["page_kind"] == "native"
                else (f"{expected_template}:copy:{int(page['continuation_index']):04d}")
            )
            frozen_fields = (
                "source_page_number",
                "source_band_id",
                "source_column_id",
                "band_index",
                "column_index",
                "column_count",
                "width_ratio_ppm",
                "text_left_mpt",
                "text_right_mpt",
            )
            if (
                frame["id"] != expected_instance
                or frame["native_frame_id"] != expected_native
                or any(frame[field] != template[field] for field in frozen_fields)
                or frame["bbox_mpt"][0] != template["bbox_mpt"][0]
                or frame["bbox_mpt"][2] != template["bbox_mpt"][2]
            ):
                raise LayoutSolverError("layout changed frozen mirrored frame geometry")
            frame_by_id[str(frame["id"])] = frame

        blocks_by_frame: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for block in page["blocks"]:
            content_id = str(block["content_id"])
            raw = raw_by_content.get(content_id)
            if raw is None:
                raise LayoutSolverError("layout contains unknown content")
            if (
                block["style"] != raw["style"]
                or block["line_sequence_hash"] != raw["line_sequence_hash"]
            ):
                raise LayoutSolverError("layout changed a frozen style or line hash")
            frame = frame_by_id.get(str(block["frame_id"]))
            if frame is None:
                raise LayoutSolverError("layout block is outside the mirrored frames")
            is_note = "note_index" in raw
            is_auxiliary = "annotation_id" in raw
            if is_note:
                allowed_native = (str(raw["frame_id"]),)
                expected_kind = "figure-note"
            elif is_auxiliary:
                allowed_native = tuple(
                    str(value) for value in raw["allowed_native_frame_ids"]
                )
                expected_kind = "auxiliary"
            else:
                allowed_native = tuple(
                    str(value) for value in raw["allowed_native_frame_ids"]
                )
                expected_kind = "unit"
            if (
                frame["native_frame_id"] not in allowed_native
                or block["content_kind"] != expected_kind
                or block["unit_id"] != raw["unit_id"]
                or (
                    page["page_kind"] == "continuation"
                    and source_page_number
                    != int(
                        raw.get(
                            "continuation_owner_page_number",
                            source_page_number,
                        )
                    )
                )
            ):
                raise LayoutSolverError("layout block violates its allowed frame set")
            source_lines = raw["lines"][block["line_start"] : block["line_end"]]
            if len(source_lines) != len(block["lines"]):
                raise LayoutSolverError("layout line range and output lines disagree")
            for source_line, output_line in zip(
                source_lines, block["lines"], strict=True
            ):
                if any(
                    output_line[field] != source_line[field] for field in line_fields
                ):
                    raise LayoutSolverError("layout changed a frozen line box")
                seen_lines[content_id].append(int(output_line["index"]))
            seen_parts[content_id].append(int(block["part_index"]))
            if annotated:
                expected_selected_anchor = None
                if not is_note and not is_auxiliary and int(block["part_index"]) == 0:
                    matching = [
                        candidate
                        for candidate in raw["source_anchor_candidates"]
                        if int(candidate["source_page_number"]) == source_page_number
                    ]
                    if len(matching) != 1:
                        raise LayoutSolverError(
                            "first unit part has no unique source anchor candidate"
                        )
                    expected_selected_anchor = matching[0]
                expected_anchor = expected_selected_anchor is not None
                if block.get("selected_anchor") != expected_selected_anchor:
                    raise LayoutSolverError(
                        "layout selected anchor differs from FrameGraph candidate"
                    )
            else:
                expected_anchor = (
                    not is_note
                    and int(block["part_index"]) == 0
                    and page["page_kind"] == "native"
                    and source_page_number == int(raw["anchor"]["source_page_number"])
                )
            if block["creates_anchor"] != expected_anchor:
                raise LayoutSolverError(
                    "layout anchor creation is not bound to FrameGraph"
                )
            blocks_by_frame[str(block["frame_id"])].append(block)
            observed_ranks.append(content_rank[content_id])

        vertical_padding = int(frame_graph["flow_spacing"]["vertical_padding_mpt"])
        block_gap = int(frame_graph["flow_spacing"]["block_gap_mpt"])
        note_gap = int(frame_graph["flow_spacing"]["figure_note_gap_mpt"])
        band_by_source = {str(band["source_band_id"]): band for band in page["bands"]}
        flow_height_by_frame: dict[str, int] = {}
        for frame_id, frame in frame_by_id.items():
            blocks = blocks_by_frame.get(frame_id, [])
            natural = (
                int(
                    band_by_source[str(frame["source_band_id"])][
                        "solved_top_offset_mpt"
                    ]
                )
                + vertical_padding
            )
            used = 0
            for index, block in enumerate(blocks):
                raw = raw_by_content[str(block["content_id"])]
                height = sum(int(line["line_height_mpt"]) for line in block["lines"])
                expected_anchor = bool(block["creates_anchor"])
                if expected_anchor:
                    first_line = block["lines"][0]
                    anchor = block["selected_anchor"] if annotated else raw["anchor"]
                    expected_preferred = (
                        int(anchor["source_visual_center_offset_mpt"])
                        - (
                            int(first_line["ascent_mpt"])
                            - int(first_line["descent_mpt"])
                        )
                        // 2
                    )
                else:
                    expected_preferred = natural
                if int(block["preferred_top_offset_mpt"]) != expected_preferred:
                    raise LayoutSolverError(
                        "layout preferred block position is invalid"
                    )
                if index:
                    gap = (
                        note_gap
                        if "note_index" in raw or "annotation_id" in raw
                        else block_gap
                    )
                    used += gap
                    previous = blocks[index - 1]
                    previous_bottom = int(previous["solved_top_offset_mpt"]) + sum(
                        int(line["line_height_mpt"]) for line in previous["lines"]
                    )
                    if int(block["solved_top_offset_mpt"]) < previous_bottom + gap:
                        raise LayoutSolverError("layout blocks violate frozen spacing")
                used += height
                next_gap = 0
                if index + 1 < len(blocks):
                    following_raw = raw_by_content[str(blocks[index + 1]["content_id"])]
                    next_gap = (
                        note_gap
                        if "note_index" in following_raw
                        or "annotation_id" in following_raw
                        else block_gap
                    )
                natural += height + next_gap
            flow_height_by_frame[frame_id] = used

        for band in page["bands"]:
            heights = [flow_height_by_frame[frame_id] for frame_id in band["frame_ids"]]
            expected_height = max(
                1,
                max(heights, default=0) + 2 * vertical_padding,
            )
            if int(band["height_mpt"]) != expected_height:
                raise LayoutSolverError(
                    "layout band height was not recomputed from final legal flow"
                )
    if observed_ranks != sorted(observed_ranks):
        raise LayoutSolverError("layout content order differs from FrameGraph order")
    for content_id, raw in raw_by_content.items():
        if seen_lines[content_id] != list(range(len(raw["lines"]))):
            raise LayoutSolverError(
                "layout loses, duplicates, or reorders target lines"
            )
        if seen_parts[content_id] != list(range(len(seen_parts[content_id]))):
            raise LayoutSolverError("layout part indexes are not contiguous")


def solve_layout(
    frame_graph: Mapping[str, object],
    *,
    expected_solver_input_hash: str | None = None,
    limits: LayoutLimits = DEFAULT_LAYOUT_LIMITS,
) -> dict[str, object]:
    """Return a complete immutable layout or one stable explicit failure."""

    _validate_limits(limits)
    try:
        validate_artifact("frame-graph", frame_graph)
    except SchemaValidationError as exc:
        raise LayoutSolverError("frame graph is structurally invalid") from exc

    spacing = _flow_spacing(frame_graph)
    solver_policy = asdict(limits)
    frame_graph_hash = sha256_canonical(frame_graph)
    computed_solver_input_hash = sha256_canonical(
        {
            "solver_contract_version": "1.0.0",
            "frame_graph_input_hash": frame_graph["frame_graph_input_hash"],
            "frame_graph_hash": frame_graph_hash,
            "flow_spacing": asdict(spacing),
            "solver_policy": solver_policy,
        }
    )
    if expected_solver_input_hash is not None:
        if not isinstance(expected_solver_input_hash, str) or not _SHA256.fullmatch(
            expected_solver_input_hash
        ):
            raise LayoutSolverError(
                "expected_solver_input_hash must be lowercase SHA-256"
            )
        if expected_solver_input_hash != computed_solver_input_hash:
            raise LayoutSolverError(
                "expected_solver_input_hash does not match the frozen inputs"
            )

    flows, records = _content_records(frame_graph, limits, spacing)
    page_templates = _page_templates(frame_graph, spacing)
    dp_limits = _dp_limits(limits, spacing)
    native_slots = instantiate_slots(page_templates, {})
    native_failure: FlowDpInfeasible | None = None
    try:
        plan = solve_fixed_slots(flows, native_slots, limits=dp_limits)
        slots = native_slots
        continuation_counts: dict[int, int] = {}
        native_status = "solved"
        continuation_reason: str | None = None
        distributions_examined = 0
        native_states = plan.states_examined
        native_transitions = plan.transitions_examined
    except FlowDpComplexityError as exc:
        raise _complexity(exc) from exc
    except FlowDpInfeasible as exc:
        native_failure = exc
        try:
            continuation: ContinuationPlan = solve_continuation_distributions(
                flows,
                page_templates,
                limits=dp_limits,
            )
        except FlowDpComplexityError as complexity:
            raise _complexity(complexity) from complexity
        except FlowDpInfeasible as infeasible:
            raise LayoutInfeasibleError(
                "native and continuation frame graphs are both infeasible"
            ) from infeasible
        plan = continuation.flow_plan
        slots = continuation.slots
        continuation_counts = dict(continuation.continuation_counts)
        native_status = "exhausted"
        continuation_reason = "native-band-flow-exhausted"
        distributions_examined = continuation.distributions_examined
        native_states = exc.states
        native_transitions = exc.transitions

    window_attempts: list[dict[str, object]] = []
    bands, band_traces = _band_positions(
        frame_graph=frame_graph,
        pages=page_templates,
        slots=slots,
        placements=plan.placements,
        records=records,
        continuation_counts=continuation_counts,
        limits=limits,
        spacing=spacing,
        attempts=window_attempts,
    )
    selected_anchors = _selected_anchors(
        slots=slots,
        placements=plan.placements,
        records=records,
    )
    block_positions = _block_positions(
        slots=slots,
        placements=plan.placements,
        records=records,
        bands=bands,
        limits=limits,
        spacing=spacing,
        attempts=window_attempts,
        selected_anchors=selected_anchors,
    )
    pages = _output_pages(
        frame_graph=frame_graph,
        pages=page_templates,
        slots=slots,
        plan=plan,
        records=records,
        continuation_counts=continuation_counts,
        band_positions=bands,
        block_positions=block_positions,
        selected_anchors=selected_anchors,
    )
    artifact: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "layout",
        "solver_contract_version": limits.version,
        "solver_input_hash": computed_solver_input_hash,
        "frame_graph_input_hash": frame_graph["frame_graph_input_hash"],
        "frame_graph_hash": frame_graph_hash,
        "right_panel_bbox_mpt": frame_graph["right_panel_bbox_mpt"],
        "font_fingerprint": frame_graph["font_fingerprint"],
        "flow_spacing": asdict(spacing),
        "solver_policy": solver_policy,
        "solver_trace": {
            "native_status": native_status,
            "native_window_and_band_search_exhausted": native_failure is not None,
            "native_exhaustion_reason": (
                "native-fixed-slot-and-band-height-state-space-exhausted"
                if native_failure is not None
                else None
            ),
            "continuation_reason": continuation_reason,
            "continuation_page_count": sum(continuation_counts.values()),
            "split_count": plan.split_count,
            "break_quality_cost": plan.break_quality_cost,
            "band_height_objective_mpt": list(plan.geometry_height_vector_mpt),
            "native_dp_states": native_states,
            "native_dp_transitions": native_transitions,
            "selected_dp_states": plan.states_examined,
            "selected_dp_transitions": plan.transitions_examined,
            "continuation_distributions_examined": distributions_examined,
            "window_attempts": window_attempts,
            "band_heights": band_traces,
        },
        "pages": pages,
    }
    if frame_graph.get("line_mapping_version") == 2:
        artifact["line_mapping_version"] = 2
    try:
        validate_artifact("layout", artifact)
    except SchemaValidationError as exc:
        raise LayoutSolverError("constructed layout violates its schema") from exc
    validate_layout_against_frame_graph(frame_graph, artifact)
    return artifact
