# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Freeze deterministic collision-checked gray dashed leader routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.contracts import (
    LeaderReflowRequired,
    OverlayPlanError,
)

LEADER_STYLE_VERSION = 1
LEADER_COLOR_HEX = "#9A9A9A"
LEADER_DASH_MPT = (4_000, 3_000)
LEADER_WIDTH_MPT = 1_000
LEADER_CLEARANCE_MPT = 1_500
LEADER_CORNER_RADIUS_MPT = 3_000
_LANE_OFFSETS_MPT = tuple(4_000 + index * 1_000 for index in range(32))
_MAX_DIRECT_REQUESTS = 4_096
_DEFAULT_MAX_COLLISION_CHECKS = 5_000_000


@dataclass(frozen=True, slots=True)
class Obstacle:
    """One exact bbox that a leader may not enter."""

    obstacle_id: str
    kind: str
    bbox_mpt: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class LeaderRequest:
    """Already-rebound endpoints for one actual first unit part."""

    leader_id: str
    unit_id: str
    source_block_id: str
    source_endpoint_mpt: tuple[int, int]
    target_endpoint_mpt: tuple[int, int]
    target_obstacle_ids: tuple[str, ...]


@dataclass(slots=True)
class _CollisionBudget:
    maximum: int
    used: int = 0

    def consume(self) -> None:
        self.used += 1
        if self.used > self.maximum:
            raise OverlayPlanError(
                "PLAN_COMPLEXITY_LIMIT",
                "leader collision-check budget exceeded",
            )


def _box(value: Sequence[int], *, label: str) -> tuple[int, int, int, int]:
    if (
        isinstance(value, (str, bytes))
        or len(value) != 4
        or any(type(item) is not int for item in value)
    ):
        raise OverlayPlanError("LEADER_INPUT_INVALID", f"{label} is invalid")
    box = tuple(int(item) for item in value)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise OverlayPlanError("LEADER_INPUT_INVALID", f"{label} is not positive")
    return box


def _point(value: Sequence[int], *, label: str) -> tuple[int, int]:
    if (
        isinstance(value, (str, bytes))
        or len(value) != 2
        or any(type(item) is not int for item in value)
    ):
        raise OverlayPlanError("LEADER_INPUT_INVALID", f"{label} is invalid")
    return int(value[0]), int(value[1])


def _normalise_obstacle(value: Obstacle | Mapping[str, object]) -> Obstacle:
    if isinstance(value, Obstacle):
        raw = value
    elif isinstance(value, Mapping):
        identifier = value.get("obstacle_id", value.get("id"))
        kind = value.get("kind")
        bbox = value.get("bbox_mpt")
        if (
            not isinstance(identifier, str)
            or not identifier
            or not isinstance(kind, str)
            or not kind
            or not isinstance(bbox, Sequence)
        ):
            raise OverlayPlanError("LEADER_INPUT_INVALID", "obstacle is invalid")
        raw = Obstacle(identifier, kind, _box(bbox, label="obstacle bbox"))
    else:
        raise OverlayPlanError("LEADER_INPUT_INVALID", "obstacle is invalid")
    if not raw.obstacle_id or not raw.kind:
        raise OverlayPlanError("LEADER_INPUT_INVALID", "obstacle is invalid")
    return Obstacle(
        raw.obstacle_id,
        raw.kind,
        _box(raw.bbox_mpt, label="obstacle bbox"),
    )


def _segment_box(
    start: tuple[int, int],
    end: tuple[int, int],
    padding: int,
) -> tuple[int, int, int, int]:
    return (
        min(start[0], end[0]) - padding,
        min(start[1], end[1]) - padding,
        max(start[0], end[0]) + padding,
        max(start[1], end[1]) + padding,
    )


def _boxes_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def _route_segments(
    points: Sequence[tuple[int, int]],
) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
    return tuple(zip(points, points[1:], strict=False))


def _route_hits_obstacles(
    points: Sequence[tuple[int, int]],
    obstacles: Sequence[Obstacle],
    ignored_ids: frozenset[str],
    budget: _CollisionBudget,
) -> bool:
    for start, end in _route_segments(points):
        segment = _segment_box(start, end, LEADER_CLEARANCE_MPT)
        for obstacle in obstacles:
            if obstacle.obstacle_id in ignored_ids:
                continue
            budget.consume()
            if _boxes_overlap(segment, obstacle.bbox_mpt):
                return True
    return False


def _routes_conflict(
    points: Sequence[tuple[int, int]],
    frozen_points: Sequence[Sequence[int]],
    budget: _CollisionBudget,
) -> bool:
    second = tuple((int(point[0]), int(point[1])) for point in frozen_points)
    padding = LEADER_CLEARANCE_MPT // 2
    for first_start, first_end in _route_segments(points):
        first_box = _segment_box(first_start, first_end, padding)
        for second_start, second_end in _route_segments(second):
            budget.consume()
            if _boxes_overlap(
                first_box,
                _segment_box(second_start, second_end, padding),
            ):
                return True
    return False


def _corner_radius(points: Sequence[tuple[int, int]]) -> int:
    lengths = [
        abs(end[0] - start[0]) + abs(end[1] - start[1])
        for start, end in _route_segments(points)
    ]
    return min(LEADER_CORNER_RADIUS_MPT, *(length // 2 for length in lengths))


def _frozen_route(
    request: LeaderRequest,
    points: Sequence[tuple[int, int]],
    *,
    lane_index: int | None,
) -> dict[str, object]:
    half_width = (LEADER_WIDTH_MPT + 1) // 2
    route: dict[str, object] = {
        "leader_id": request.leader_id,
        "unit_id": request.unit_id,
        "source_block_id": request.source_block_id,
        "route_kind": ("horizontal" if lane_index is None else "rounded-three-segment"),
        "lane_index": lane_index,
        "points_mpt": [list(point) for point in points],
        "corner_radius_mpt": 0 if lane_index is None else _corner_radius(points),
        "dash_mpt": list(LEADER_DASH_MPT),
        "width_mpt": LEADER_WIDTH_MPT,
        "clearance_mpt": LEADER_CLEARANCE_MPT,
        "color_hex": LEADER_COLOR_HEX,
        "style_version": LEADER_STYLE_VERSION,
        "bbox_mpt": [
            min(point[0] for point in points) - half_width,
            min(point[1] for point in points) - half_width,
            max(point[0] for point in points) + half_width,
            max(point[1] for point in points) + half_width,
        ],
    }
    route["route_hash"] = sha256_canonical(route)
    return route


def freeze_leader_routes(
    requests: Sequence[LeaderRequest],
    *,
    source_obstacles: Sequence[Obstacle | Mapping[str, object]],
    right_obstacles: Sequence[Obstacle | Mapping[str, object]],
    page_bbox_mpt: Sequence[int],
    right_panel_left_mpt: int,
    max_collision_checks: int = _DEFAULT_MAX_COLLISION_CHECKS,
) -> list[dict[str, object]]:
    """Return exact finite routes or fail before any ReportLab canvas exists."""

    page_box = _box(page_bbox_mpt, label="page bbox")
    if (
        type(right_panel_left_mpt) is not int
        or not page_box[0] < right_panel_left_mpt < page_box[2]
        or isinstance(requests, (str, bytes))
        or len(requests) > _MAX_DIRECT_REQUESTS
        or type(max_collision_checks) is not int
        or max_collision_checks < 1
    ):
        raise OverlayPlanError("LEADER_INPUT_INVALID", "leader request set is invalid")
    collision_budget = _CollisionBudget(max_collision_checks)
    obstacles = tuple(
        sorted(
            (
                *(_normalise_obstacle(value) for value in source_obstacles),
                *(_normalise_obstacle(value) for value in right_obstacles),
            ),
            key=lambda item: (item.obstacle_id, item.kind, item.bbox_mpt),
        )
    )
    identifiers = [obstacle.obstacle_id for obstacle in obstacles]
    if len(identifiers) != len(set(identifiers)):
        raise OverlayPlanError("LEADER_INPUT_INVALID", "obstacle IDs are not unique")

    validated: list[tuple[LeaderRequest, tuple[int, int], tuple[int, int]]] = []
    for request in requests:
        if (
            not isinstance(request, LeaderRequest)
            or not request.leader_id
            or not request.unit_id
            or not request.source_block_id
            or any(not identifier for identifier in request.target_obstacle_ids)
        ):
            raise OverlayPlanError("LEADER_INPUT_INVALID", "leader request is invalid")
        source = _point(request.source_endpoint_mpt, label="source endpoint")
        target = _point(request.target_endpoint_mpt, label="target endpoint")
        if (
            not page_box[0] <= source[0] < right_panel_left_mpt
            or not right_panel_left_mpt <= target[0] <= page_box[2]
            or not page_box[1] <= source[1] <= page_box[3]
            or not page_box[1] <= target[1] <= page_box[3]
            or source[0] >= target[0]
        ):
            raise OverlayPlanError(
                "LEADER_INPUT_INVALID",
                "leader endpoints are outside their panels",
            )
        validated.append((request, source, target))

    if len({item[0].leader_id for item in validated}) != len(validated):
        raise OverlayPlanError("LEADER_INPUT_INVALID", "leader IDs are not unique")
    # Freeze visually from top to bottom.  Identifier order is unrelated to
    # geometry (for example r12 sorts before r2) and can greedily occupy a lane
    # that an enclosing higher route must cross even when a finite nesting exists.
    ordered = sorted(
        validated,
        key=lambda item: (-item[2][1], -item[1][1], item[0].leader_id),
    )
    frozen: list[dict[str, object]] = []
    for request, source, target in ordered:
        ignored = frozenset((request.source_block_id, *request.target_obstacle_ids))
        candidates: list[tuple[int | None, tuple[tuple[int, int], ...]]] = []
        if source[1] == target[1]:
            candidates.append((None, (source, target)))
        else:
            for lane_index, offset in enumerate(_LANE_OFFSETS_MPT):
                lane_x = right_panel_left_mpt + offset
                if lane_x + LEADER_CLEARANCE_MPT >= target[0]:
                    continue
                candidates.append(
                    (
                        lane_index,
                        (
                            source,
                            (lane_x, source[1]),
                            (lane_x, target[1]),
                            target,
                        ),
                    )
                )
        chosen: dict[str, object] | None = None
        for lane_index, points in candidates:
            if _route_hits_obstacles(points, obstacles, ignored, collision_budget):
                continue
            if any(
                _routes_conflict(points, route["points_mpt"], collision_budget)
                for route in frozen
            ):
                continue
            if lane_index is not None and _corner_radius(points) <= 0:
                continue
            chosen = _frozen_route(request, points, lane_index=lane_index)
            break
        if chosen is None:
            raise LeaderReflowRequired(
                f"no collision-free finite route for {request.leader_id}"
            )
        frozen.append(chosen)
    return frozen


__all__ = [
    "LEADER_CLEARANCE_MPT",
    "LEADER_COLOR_HEX",
    "LEADER_DASH_MPT",
    "LEADER_STYLE_VERSION",
    "LEADER_WIDTH_MPT",
    "LeaderRequest",
    "Obstacle",
    "freeze_leader_routes",
]
