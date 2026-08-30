# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Finite exact flow DP for native frames and candidate continuation copies."""

from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace


class FlowDpError(ValueError):
    """Base class for deterministic flow-planning failures."""


class FlowDpInfeasible(FlowDpError):
    """Raised after the finite state graph has been exhausted."""

    def __init__(self, *, states: int, transitions: int) -> None:
        self.states = states
        self.transitions = transitions
        super().__init__("no legal complete flow exists in the supplied frames")


class FlowDpComplexityError(FlowDpError):
    """Raised instead of replacing exact search with a greedy fallback."""

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
class FlowDpLimits:
    """Finite versioned limits passed explicitly by the layout solver."""

    max_dp_states: int
    max_dp_transitions: int
    max_continuation_distributions: int
    max_continuation_pages_per_source_page: int
    max_total_continuation_pages: int
    min_lines_before_break: int
    min_lines_after_break: int
    heading_with_next_lines: int
    vertical_padding_mpt: int
    band_gap_mpt: int


@dataclass(frozen=True, slots=True)
class FlowSpec:
    """One frozen line sequence in global semantic reading order."""

    content_id: str
    unit_id: str
    role: str
    lines: tuple[Mapping[str, object], ...]
    legal_breaks: tuple[tuple[int, str], ...]
    allowed_native_frame_ids: tuple[str, ...]
    continuation_owner_page_number: int
    gap_before_mpt: int
    is_note: bool = False


@dataclass(frozen=True, slots=True)
class FrameSlot:
    """One native frame or one instantiated continuation-template frame."""

    instance_id: str
    template_frame_id: str
    native_frame_id: str
    source_page_number: int
    page_kind: str
    continuation_index: int
    capacity_mpt: int
    page_height_mpt: int
    page_top_reserve_mpt: int
    bbox_mpt: tuple[int, int, int, int]
    text_left_mpt: int
    text_right_mpt: int
    source_band_id: str
    source_column_id: str
    band_index: int
    column_index: int
    column_count: int
    width_ratio_ppm: int


@dataclass(frozen=True, slots=True)
class PageSlotTemplates:
    """Native slots and one reusable continuation topology for a source page."""

    source_page_number: int
    native_slots: tuple[FrameSlot, ...]
    continuation_templates: tuple[FrameSlot, ...]


@dataclass(frozen=True, slots=True)
class FlowPlacement:
    """One contiguous target-line range assigned to one concrete frame slot."""

    content_id: str
    unit_id: str
    role: str
    line_start: int
    line_end: int
    slot_index: int
    break_kind_after: str | None


@dataclass(frozen=True, slots=True)
class FlowPlan:
    """Lexicographically optimal plan for one fixed concrete slot sequence."""

    placements: tuple[FlowPlacement, ...]
    split_count: int
    break_quality_cost: int
    geometry_height_vector_mpt: tuple[int, ...]
    stable_path: tuple[tuple[str, str, int, str], ...]
    states_examined: int
    transitions_examined: int


@dataclass(frozen=True, slots=True)
class ContinuationPlan:
    """Best exact plan after a globally minimal continuation distribution."""

    flow_plan: FlowPlan
    slots: tuple[FrameSlot, ...]
    continuation_counts: tuple[tuple[int, int], ...]
    distributions_examined: int

    @property
    def continuation_page_count(self) -> int:
        return sum(count for _page, count in self.continuation_counts)


@dataclass(frozen=True, slots=True)
class _PathRecord:
    placements: tuple[FlowPlacement, ...]
    stable_path: tuple[tuple[str, str, int, str], ...]


def _transition_guard(counter: list[int], maximum: int) -> None:
    counter[0] += 1
    if counter[0] > maximum:
        raise FlowDpComplexityError(
            limit_name="max_dp_transitions",
            observed=counter[0],
            maximum=maximum,
        )


def _state_guard(current_count: int, maximum: int) -> None:
    if current_count > maximum:
        raise FlowDpComplexityError(
            limit_name="max_dp_states",
            observed=current_count,
            maximum=maximum,
        )


def _is_allowed(flow: FlowSpec, slot: FrameSlot) -> bool:
    if slot.native_frame_id not in flow.allowed_native_frame_ids:
        return False
    return slot.page_kind == "native" or (
        slot.source_page_number == flow.continuation_owner_page_number
    )


def _line_prefix(flow: FlowSpec) -> tuple[int, ...]:
    prefix = [0]
    for line in flow.lines:
        height = line.get("line_height_mpt")
        if type(height) is not int or height <= 0:
            raise FlowDpError("flow line heights must be positive integers")
        prefix.append(prefix[-1] + height)
    return tuple(prefix)


def _candidate_ends(
    flow: FlowSpec,
    *,
    line_start: int,
    pending_min_lines: int,
    limits: FlowDpLimits,
) -> tuple[tuple[int, str | None], ...]:
    line_count = len(flow.lines)
    if flow.role == "heading":
        return ((line_count, None),) if line_start == 0 else ()
    break_by_line = dict(flow.legal_breaks)
    candidates: list[tuple[int, str | None]] = [(line_count, None)]
    for end in sorted(break_by_line):
        if not line_start < end < line_count:
            continue
        segment_lines = end - line_start
        remaining_lines = line_count - end
        if (
            segment_lines < limits.min_lines_before_break
            or remaining_lines < limits.min_lines_after_break
            or (line_start > 0 and segment_lines < limits.min_lines_after_break)
            or segment_lines < pending_min_lines
        ):
            continue
        candidates.append((end, break_by_line[end]))
    return tuple(candidates)


def _page_key(slot: FrameSlot) -> tuple[int, str, int]:
    return (slot.source_page_number, slot.page_kind, slot.continuation_index)


def _current_band_fits(
    slot: FrameSlot,
    *,
    used_height: int,
    band_max_height: int,
    page_used_height: int,
    limits: FlowDpLimits,
) -> bool:
    gap = limits.band_gap_mpt if slot.band_index > 0 else 0
    band_height = max(
        1,
        max(used_height, band_max_height) + 2 * limits.vertical_padding_mpt,
    )
    prospective = page_used_height + gap + band_height
    return slot.page_top_reserve_mpt + prospective <= slot.page_height_mpt


def _advance_slot(
    slots: tuple[FrameSlot, ...],
    *,
    slot_index: int,
    used_height: int,
    band_max_height: int,
    page_used_height: int,
    limits: FlowDpLimits,
) -> tuple[int, int, int, int, int | None] | None:
    if slot_index + 1 >= len(slots):
        return None
    current = slots[slot_index]
    following = slots[slot_index + 1]
    current_band_max = max(used_height, band_max_height)
    same_page = _page_key(current) == _page_key(following)
    if same_page and current.page_top_reserve_mpt != following.page_top_reserve_mpt:
        raise FlowDpError("one output page must use one top reserve")
    same_band = same_page and current.source_band_id == following.source_band_id
    if same_band:
        return (slot_index + 1, 0, current_band_max, page_used_height, None)

    gap = limits.band_gap_mpt if current.band_index > 0 else 0
    completed_band_height = max(
        1,
        current_band_max + 2 * limits.vertical_padding_mpt,
    )
    completed_page_height = page_used_height + gap + completed_band_height
    if current.page_top_reserve_mpt + completed_page_height > current.page_height_mpt:
        return None
    if same_page:
        return (
            slot_index + 1,
            0,
            0,
            completed_page_height,
            completed_band_height,
        )
    return (slot_index + 1, 0, 0, 0, completed_band_height)


def _tail_geometry(
    slots: tuple[FrameSlot, ...],
    *,
    slot_index: int,
    used_height: int,
    band_max_height: int,
    page_used_height: int,
    completed_band_heights: tuple[int, ...],
    limits: FlowDpLimits,
) -> tuple[int, ...] | None:
    index = slot_index
    used = used_height
    band_max = band_max_height
    page_used = page_used_height
    geometry = completed_band_heights
    while index + 1 < len(slots):
        advanced = _advance_slot(
            slots,
            slot_index=index,
            used_height=used,
            band_max_height=band_max,
            page_used_height=page_used,
            limits=limits,
        )
        if advanced is None:
            return None
        index, used, band_max, page_used, completed = advanced
        if completed is not None:
            geometry += (completed,)
    if not _current_band_fits(
        slots[index],
        used_height=used,
        band_max_height=band_max,
        page_used_height=page_used,
        limits=limits,
    ):
        return None
    return geometry + (max(1, max(used, band_max) + 2 * limits.vertical_padding_mpt),)


def solve_fixed_slots(
    flows: Sequence[FlowSpec],
    slots: Sequence[FrameSlot],
    *,
    limits: FlowDpLimits,
) -> FlowPlan:
    """Solve one finite ordered frame sequence without activating new pages."""

    if not flows or not slots:
        raise FlowDpError("flow DP needs non-empty flows and slots")
    flow_tuple = tuple(flows)
    slot_tuple = tuple(slots)
    prefixes = tuple(_line_prefix(flow) for flow in flow_tuple)
    break_quality = {"sentence": 1, "line": 2}

    # State contains only facts that can change future transitions.  For one
    # slot index, every completed-band prefix has the same length, so the
    # lexicographically best prefix belongs in the path label instead.
    # state = item, line, slot, column flow, band max, page bands, heading lines
    start_state = (0, 0, 0, 0, 0, 0, 0)
    start_cost: tuple[
        int,
        int,
        tuple[int, ...],
        tuple[tuple[str, str, int, str], ...],
    ] = (0, 0, (), ())
    heap: list[
        tuple[
            int,
            int,
            tuple[int, ...],
            tuple[tuple[str, str, int, str], ...],
            tuple[int, int, int, int, int, int, int],
        ]
    ] = [(*start_cost, start_state)]
    best = {start_state: start_cost}
    records = {start_state: _PathRecord(placements=(), stable_path=())}
    transitions = [0]
    states_examined = 0
    best_complete: (
        tuple[
            tuple[
                int,
                int,
                tuple[int, ...],
                tuple[tuple[str, str, int, str], ...],
            ],
            _PathRecord,
        ]
        | None
    ) = None

    def offer(
        state: tuple[int, int, int, int, int, int, int],
        cost: tuple[
            int,
            int,
            tuple[int, ...],
            tuple[tuple[str, str, int, str], ...],
        ],
        record: _PathRecord,
    ) -> None:
        previous = best.get(state)
        if previous is not None and previous <= cost:
            return
        is_new = previous is None
        best[state] = cost
        records[state] = record
        if is_new:
            _state_guard(len(best), limits.max_dp_states)
        heapq.heappush(heap, (*cost, state))

    while heap:
        splits, quality, geometry_prefix, stable_path, state = heapq.heappop(heap)
        cost = (splits, quality, geometry_prefix, stable_path)
        if best.get(state) != cost:
            continue
        if best_complete is not None and (splits, quality) > best_complete[0][:2]:
            break
        states_examined += 1
        (
            item_index,
            line_start,
            slot_index,
            used_height,
            band_max_height,
            page_used_height,
            pending_min,
        ) = state
        if item_index == len(flow_tuple):
            geometry = _tail_geometry(
                slot_tuple,
                slot_index=slot_index,
                used_height=used_height,
                band_max_height=band_max_height,
                page_used_height=page_used_height,
                completed_band_heights=geometry_prefix,
                limits=limits,
            )
            if geometry is None:
                continue
            record = records[state]
            objective = (splits, quality, geometry, stable_path)
            if best_complete is None or objective < best_complete[0]:
                best_complete = (objective, record)
            continue
        if slot_index >= len(slot_tuple):
            continue

        flow = flow_tuple[item_index]
        slot = slot_tuple[slot_index]
        record = records[state]

        if pending_min == 0:
            _transition_guard(transitions, limits.max_dp_transitions)
            advanced = _advance_slot(
                slot_tuple,
                slot_index=slot_index,
                used_height=used_height,
                band_max_height=band_max_height,
                page_used_height=page_used_height,
                limits=limits,
            )
            if advanced is not None:
                (
                    next_slot,
                    next_used,
                    next_band_max,
                    next_page_used,
                    completed,
                ) = advanced
                next_geometry = geometry_prefix + (
                    (completed,) if completed is not None else ()
                )
                offer(
                    (
                        item_index,
                        line_start,
                        next_slot,
                        next_used,
                        next_band_max,
                        next_page_used,
                        0,
                    ),
                    (splits, quality, next_geometry, stable_path),
                    record,
                )

        if not _is_allowed(flow, slot):
            continue
        for line_end, break_kind in _candidate_ends(
            flow,
            line_start=line_start,
            pending_min_lines=pending_min,
            limits=limits,
        ):
            _transition_guard(transitions, limits.max_dp_transitions)
            segment_lines = line_end - line_start
            if segment_lines < pending_min:
                continue
            gap = 0 if used_height == 0 else flow.gap_before_mpt
            segment_height = (
                prefixes[item_index][line_end] - prefixes[item_index][line_start]
            )
            new_used = used_height + gap + segment_height
            if new_used > slot.capacity_mpt or not _current_band_fits(
                slot,
                used_height=new_used,
                band_max_height=band_max_height,
                page_used_height=page_used_height,
                limits=limits,
            ):
                continue
            placement = FlowPlacement(
                content_id=flow.content_id,
                unit_id=flow.unit_id,
                role=flow.role,
                line_start=line_start,
                line_end=line_end,
                slot_index=slot_index,
                break_kind_after=break_kind,
            )
            decision = (
                flow.unit_id,
                flow.content_id,
                line_end,
                slot.instance_id,
            )
            next_record = _PathRecord(
                placements=record.placements + (placement,),
                stable_path=stable_path + (decision,),
            )
            if line_end < len(flow.lines):
                assert break_kind is not None
                advanced = _advance_slot(
                    slot_tuple,
                    slot_index=slot_index,
                    used_height=new_used,
                    band_max_height=band_max_height,
                    page_used_height=page_used_height,
                    limits=limits,
                )
                if advanced is not None:
                    (
                        next_slot,
                        next_used,
                        next_band_max,
                        next_page_used,
                        completed,
                    ) = advanced
                    next_geometry = geometry_prefix + (
                        (completed,) if completed is not None else ()
                    )
                    offer(
                        (
                            item_index,
                            line_end,
                            next_slot,
                            next_used,
                            next_band_max,
                            next_page_used,
                            0,
                        ),
                        (
                            splits + 1,
                            quality + break_quality[break_kind],
                            next_geometry,
                            next_record.stable_path,
                        ),
                        next_record,
                    )
                continue

            next_pending = 0
            if flow.role == "heading" and item_index + 1 < len(flow_tuple):
                next_pending = min(
                    limits.heading_with_next_lines,
                    len(flow_tuple[item_index + 1].lines),
                )
            offer(
                (
                    item_index + 1,
                    0,
                    slot_index,
                    new_used,
                    band_max_height,
                    page_used_height,
                    next_pending,
                ),
                (splits, quality, geometry_prefix, next_record.stable_path),
                next_record,
            )

    if best_complete is None:
        raise FlowDpInfeasible(
            states=states_examined,
            transitions=transitions[0],
        )
    objective, record = best_complete
    splits, quality, geometry, stable_path = objective
    return FlowPlan(
        placements=record.placements,
        split_count=splits,
        break_quality_cost=quality,
        geometry_height_vector_mpt=geometry,
        stable_path=stable_path,
        states_examined=states_examined,
        transitions_examined=transitions[0],
    )


def instantiate_slots(
    pages: Sequence[PageSlotTemplates],
    continuation_counts: Mapping[int, int],
) -> tuple[FrameSlot, ...]:
    """Instantiate candidate template copies immediately after their source page."""

    slots: list[FrameSlot] = []
    for page in pages:
        slots.extend(page.native_slots)
        count = continuation_counts.get(page.source_page_number, 0)
        for copy_index in range(1, count + 1):
            for template in page.continuation_templates:
                slots.append(
                    replace(
                        template,
                        instance_id=(
                            f"{template.template_frame_id}:copy:{copy_index:04d}"
                        ),
                        page_kind="continuation",
                        continuation_index=copy_index,
                    )
                )
    return tuple(slots)


def solve_continuation_distributions(
    flows: Sequence[FlowSpec],
    pages: Sequence[PageSlotTemplates],
    *,
    limits: FlowDpLimits,
) -> ContinuationPlan:
    """Find pages→splits→quality→band-height→stable-ID optimum."""

    owner_pages = tuple(sorted({flow.continuation_owner_page_number for flow in flows}))
    if not owner_pages:
        raise FlowDpError("continuation search needs at least one owner page")
    page_set = {page.source_page_number for page in pages}
    if any(owner not in page_set for owner in owner_pages):
        raise FlowDpError("a continuation owner has no page template")
    semantic_page_max = {
        owner: sum(
            1 + (len(flow.legal_breaks) if flow.role != "heading" else 0)
            for flow in flows
            if flow.continuation_owner_page_number == owner
        )
        for owner in owner_pages
    }

    zero = tuple(0 for _owner in owner_pages)
    heap: list[tuple[int, tuple[int, ...]]] = []
    seen = {zero}
    for index in range(len(owner_pages)):
        vector = list(zero)
        vector[index] = 1
        frozen = tuple(vector)
        seen.add(frozen)
        heapq.heappush(heap, (1, frozen))

    examined = 0
    best: (
        tuple[
            tuple[
                int,
                int,
                int,
                tuple[int, ...],
                tuple[tuple[str, str, int, str], ...],
                tuple[int, ...],
            ],
            FlowPlan,
            tuple[FrameSlot, ...],
            tuple[int, ...],
        ]
        | None
    ) = None
    truncated_limit: tuple[str, int, int] | None = None

    while heap:
        total_pages, vector = heapq.heappop(heap)
        if best is not None and total_pages > best[0][0]:
            break
        examined += 1
        if examined > limits.max_continuation_distributions:
            raise FlowDpComplexityError(
                limit_name="max_continuation_distributions",
                observed=examined,
                maximum=limits.max_continuation_distributions,
            )
        counts = dict(zip(owner_pages, vector, strict=True))
        slots = instantiate_slots(pages, counts)
        try:
            plan = solve_fixed_slots(flows, slots, limits=limits)
        except FlowDpInfeasible:
            plan = None
        if plan is not None:
            objective = (
                total_pages,
                plan.split_count,
                plan.break_quality_cost,
                plan.geometry_height_vector_mpt,
                plan.stable_path,
                vector,
            )
            if best is None or objective < best[0]:
                best = (objective, plan, slots, vector)
            continue

        for index, count in enumerate(vector):
            new_total = total_pages + 1
            owner = owner_pages[index]
            if count >= semantic_page_max[owner]:
                continue
            if count >= limits.max_continuation_pages_per_source_page:
                truncated_limit = (
                    "max_continuation_pages_per_source_page",
                    count + 1,
                    limits.max_continuation_pages_per_source_page,
                )
                continue
            if new_total > limits.max_total_continuation_pages:
                truncated_limit = (
                    "max_total_continuation_pages",
                    new_total,
                    limits.max_total_continuation_pages,
                )
                continue
            expanded = list(vector)
            expanded[index] += 1
            candidate = tuple(expanded)
            if candidate in seen:
                continue
            seen.add(candidate)
            heapq.heappush(heap, (new_total, candidate))

    if best is None:
        if truncated_limit is not None:
            name, observed, maximum = truncated_limit
            raise FlowDpComplexityError(
                limit_name=name,
                observed=observed,
                maximum=maximum,
            )
        raise FlowDpInfeasible(states=0, transitions=0)
    _objective, plan, slots, vector = best
    return ContinuationPlan(
        flow_plan=plan,
        slots=slots,
        continuation_counts=tuple(zip(owner_pages, vector, strict=True)),
        distributions_examined=examined,
    )
