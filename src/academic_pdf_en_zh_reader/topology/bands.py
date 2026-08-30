# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Infer stable horizontal bands and fixed columns from extraction geometry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from academic_pdf_en_zh_reader.topology.projection import (
    BoxMpt,
    OccupiedBox,
    axis_gaps,
    gutter_persistence_ppm,
    occupied_boxes,
    union_box,
)
from academic_pdf_en_zh_reader.topology.xy_cut import vertical_xy_cut

_MINIMUM_GUTTER_MPT = 12_000
_MINIMUM_HORIZONTAL_GAP_MPT = 12_000
_MAX_COLUMNS = 3
_DOCUMENT_TEMPLATE_MIN_SCORE_PPM = 800_000


@dataclass(frozen=True)
class ColumnGeometry:
    id: str
    index: int
    x_left_mpt: int
    x_right_mpt: int
    width_ratio_ppm: int
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GutterGeometry:
    x_left_mpt: int
    x_right_mpt: int
    width_mpt: int
    persistence_ppm: int
    evidence: str


@dataclass(frozen=True)
class BandGeometry:
    id: str
    index: int
    y_top_mpt: int
    y_bottom_mpt: int
    columns: tuple[ColumnGeometry, ...]
    gutters: tuple[GutterGeometry, ...]
    score_ppm: int
    evidence: tuple[str, ...]
    object_ids: tuple[str, ...]


@dataclass(frozen=True)
class PageBands:
    page_number: int
    crop_box_mpt: BoxMpt
    bands: tuple[BandGeometry, ...]
    score_ppm: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _HorizontalGroup:
    boxes: tuple[OccupiedBox, ...]
    wide_barrier: bool
    label: int | None
    inferred_by_adjacency: bool = False

    @property
    def top(self) -> int:
        return max(item.bbox_mpt[3] for item in self.boxes)

    @property
    def bottom(self) -> int:
        return min(item.bbox_mpt[1] for item in self.boxes)


def _crop_box(page: Mapping[str, object]) -> BoxMpt:
    raw = page.get("crop_box_mpt")
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 4
        or not all(type(value) is int for value in raw)
    ):
        raise ValueError("page crop box must contain four integer milli-points")
    crop = tuple(raw)
    if crop[0] >= crop[2] or crop[1] >= crop[3]:
        raise ValueError("page crop box must have positive area")
    return crop  # type: ignore[return-value]


def _horizontal_groups(
    boxes: tuple[OccupiedBox, ...],
    minimum_gap_mpt: int,
) -> list[tuple[OccupiedBox, ...]]:
    gaps = axis_gaps(boxes, axis="y", minimum_mpt=minimum_gap_mpt)
    if not gaps:
        return [boxes]
    boundaries = sorted(gaps)
    groups: list[list[OccupiedBox]] = [[] for _ in range(len(boundaries) + 1)]
    for box in boxes:
        midpoint = (box.bbox_mpt[1] + box.bbox_mpt[3]) // 2
        index = sum(midpoint >= gap[1] for gap in boundaries)
        groups[index].append(box)
    return [
        tuple(
            sorted(
                group,
                key=lambda item: (-item.bbox_mpt[3], item.bbox_mpt[0], item.id),
            )
        )
        for group in reversed(groups)
        if group
    ]


def _cluster_anchors(
    boxes: Sequence[OccupiedBox],
    *,
    tolerance_mpt: int,
    minimum_separation_mpt: int,
) -> tuple[int, ...]:
    values = sorted((item.bbox_mpt[0], item.id) for item in boxes)
    if not values:
        return ()
    clusters: list[list[int]] = []
    for value, _identifier in values:
        if not clusters or value - clusters[-1][-1] > tolerance_mpt:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    candidates = [min(cluster) for cluster in clusters]
    anchors: list[int] = []
    for candidate in candidates:
        if not anchors or candidate - anchors[-1] >= minimum_separation_mpt:
            anchors.append(candidate)
    return tuple(anchors)


def _nearest_anchor(box: OccupiedBox, anchors: tuple[int, ...]) -> int:
    return min(
        range(len(anchors)),
        key=lambda index: (abs(box.bbox_mpt[0] - anchors[index]), index),
    )


def _document_anchor(
    box: OccupiedBox,
    anchors: tuple[int, ...],
    *,
    tolerance_mpt: int | None,
) -> int:
    if tolerance_mpt is None:
        return _nearest_anchor(box, anchors)
    index = 0
    for candidate in range(1, len(anchors)):
        if box.bbox_mpt[0] >= anchors[candidate] - tolerance_mpt:
            index = candidate
    return index


def _gap_between(first: _HorizontalGroup, second: _HorizontalGroup) -> int:
    if first.bottom >= second.top:
        return first.bottom - second.top
    if second.bottom >= first.top:
        return second.bottom - first.top
    return 0


def _resolve_unknown_labels(
    groups: list[_HorizontalGroup],
    *,
    inherit_gap_mpt: int,
) -> list[_HorizontalGroup]:
    resolved = list(groups)
    index = 0
    while index < len(resolved):
        if resolved[index].label is not None:
            index += 1
            continue
        end = index
        while end + 1 < len(resolved) and resolved[end + 1].label is None:
            end += 1
        previous = resolved[index - 1] if index > 0 else None
        following = resolved[end + 1] if end + 1 < len(resolved) else None
        label = 1
        if (
            previous is not None
            and following is not None
            and previous.label == following.label
            and previous.label is not None
            and previous.label > 1
        ):
            label = previous.label
        elif previous is not None and previous.label is not None and previous.label > 1:
            if _gap_between(previous, resolved[index]) <= inherit_gap_mpt:
                label = previous.label
        elif (
            following is not None
            and following.label is not None
            and following.label > 1
            and _gap_between(resolved[end], following) <= inherit_gap_mpt
        ):
            label = following.label
        for replacement in range(index, end + 1):
            group = resolved[replacement]
            resolved[replacement] = _HorizontalGroup(
                boxes=group.boxes,
                wide_barrier=group.wide_barrier,
                label=label,
                inferred_by_adjacency=True,
            )
        index = end + 1
    return resolved


def _merge_labelled_groups(
    groups: list[_HorizontalGroup],
) -> list[tuple[int, tuple[OccupiedBox, ...], bool, bool]]:
    merged: list[tuple[int, list[OccupiedBox], bool, bool]] = []
    for group in groups:
        assert group.label is not None
        if merged and merged[-1][0] == group.label:
            merged[-1][1].extend(group.boxes)
            merged[-1] = (
                merged[-1][0],
                merged[-1][1],
                merged[-1][2] or group.wide_barrier,
                merged[-1][3] or group.inferred_by_adjacency,
            )
        else:
            merged.append(
                (
                    group.label,
                    list(group.boxes),
                    group.wide_barrier,
                    group.inferred_by_adjacency,
                )
            )
    return [
        (
            label,
            tuple(
                sorted(
                    members,
                    key=lambda item: (
                        -item.bbox_mpt[3],
                        item.bbox_mpt[0],
                        item.id,
                    ),
                )
            ),
            barrier,
            inferred,
        )
        for label, members, barrier, inferred in merged
    ]


def _ratios(widths: list[int]) -> list[int]:
    total = sum(widths)
    if total <= 0:
        raise ValueError("column widths must be positive")
    base = [width * 1_000_000 // total for width in widths]
    remainder = 1_000_000 - sum(base)
    order = sorted(
        range(len(widths)),
        key=lambda index: (-(widths[index] * 1_000_000 % total), index),
    )
    for index in order[:remainder]:
        base[index] += 1
    return base


def _single_column(
    page_number: int,
    band_index: int,
    boxes: tuple[OccupiedBox, ...],
    crop: BoxMpt,
) -> tuple[ColumnGeometry, ...]:
    content = union_box(boxes)
    symmetric_right = crop[2] - max(0, content[0] - crop[0])
    right = max(content[2], symmetric_right)
    return (
        ColumnGeometry(
            id=f"p{page_number:04d}-band-{band_index:03d}-col-001",
            index=1,
            x_left_mpt=content[0],
            x_right_mpt=right,
            width_ratio_ppm=1_000_000,
            evidence_ids=tuple(item.id for item in boxes),
        ),
    )


def _multicolumn_geometry(
    page_number: int,
    band_index: int,
    boxes: tuple[OccupiedBox, ...],
    crop: BoxMpt,
    column_count: int,
    global_anchors: tuple[int, ...],
    minimum_gutter_mpt: int,
    document_anchor_tolerance_mpt: int | None,
) -> tuple[tuple[ColumnGeometry, ...], tuple[GutterGeometry, ...], int]:
    if len(global_anchors) < column_count:
        cut = vertical_xy_cut(boxes, minimum_gutter_mpt=minimum_gutter_mpt)
        anchors = tuple(region.bbox_mpt[0] for region in cut.regions)
    else:
        anchors = global_anchors[:column_count]
    if len(anchors) != column_count:
        return _single_column(page_number, band_index, boxes, crop), (), 300_000

    members: list[list[OccupiedBox]] = [[] for _ in anchors]
    for box in boxes:
        members[
            _document_anchor(
                box,
                anchors,
                tolerance_mpt=document_anchor_tolerance_mpt,
            )
        ].append(box)
    if any(not group for group in members):
        return _single_column(page_number, band_index, boxes, crop), (), 300_000
    lefts = [min(item.bbox_mpt[0] for item in group) for group in members]
    content_right = max(item.bbox_mpt[2] for item in boxes)
    symmetric_right = crop[2] - max(0, lefts[0] - crop[0])
    outer_right = max(content_right, symmetric_right)
    desired_gutter = max(
        minimum_gutter_mpt,
        (outer_right - lefts[0]) * 35 // 1_000,
    )
    rights: list[int] = []
    gutter_widths: list[int] = []
    for index in range(column_count - 1):
        occupied_right = max(item.bbox_mpt[2] for item in members[index])
        available = lefts[index + 1] - occupied_right
        if available < minimum_gutter_mpt:
            return _single_column(page_number, band_index, boxes, crop), (), 300_000
        gutter_width = min(available, desired_gutter)
        rights.append(lefts[index + 1] - gutter_width)
        gutter_widths.append(gutter_width)
    rights.append(outer_right)
    widths = [right - left for left, right in zip(lefts, rights, strict=True)]
    ratios = _ratios(widths)
    columns = tuple(
        ColumnGeometry(
            id=f"p{page_number:04d}-band-{band_index:03d}-col-{index + 1:03d}",
            index=index + 1,
            x_left_mpt=lefts[index],
            x_right_mpt=rights[index],
            width_ratio_ppm=ratios[index],
            evidence_ids=tuple(item.id for item in members[index]),
        )
        for index in range(column_count)
    )
    band_box = union_box(boxes)
    gutters = tuple(
        GutterGeometry(
            x_left_mpt=columns[index].x_right_mpt,
            x_right_mpt=columns[index + 1].x_left_mpt,
            width_mpt=gutter_widths[index],
            persistence_ppm=gutter_persistence_ppm(
                boxes,
                gutter=(
                    columns[index].x_right_mpt,
                    columns[index + 1].x_left_mpt,
                ),
                y_bottom_mpt=band_box[1],
                y_top_mpt=band_box[3],
            ),
            evidence=(
                f"empty-x:{columns[index].x_right_mpt}-{columns[index + 1].x_left_mpt}"
            ),
        )
        for index in range(column_count - 1)
    )
    minimum_support = min(len(group) for group in members)
    if minimum_support < 2:
        score = 680_000
    else:
        score = min(990_000, 850_000 + min(120_000, minimum_support * 20_000))
    return columns, gutters, score


def _detect_page_bands(
    page: Mapping[str, object],
    *,
    document_anchors: tuple[int, ...] | None = None,
) -> PageBands:
    """Detect a page's observable 1-3 column band geometry.

    This pure stage intentionally returns evidence and a score only. The later
    confidence stage owns the fail-closed ``NEEDS_TOPOLOGY_REVIEW`` decision.
    """

    page_number = page.get("page_number")
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page number must be a positive integer")
    crop = _crop_box(page)
    boxes = occupied_boxes(page)
    if not boxes:
        column = ColumnGeometry(
            id=f"p{page_number:04d}-band-001-col-001",
            index=1,
            x_left_mpt=crop[0],
            x_right_mpt=crop[2],
            width_ratio_ppm=1_000_000,
            evidence_ids=(),
        )
        band = BandGeometry(
            id=f"p{page_number:04d}-band-001",
            index=1,
            y_top_mpt=crop[3],
            y_bottom_mpt=crop[1],
            columns=(column,),
            gutters=(),
            score_ppm=0,
            evidence=("no-occupancy",),
            object_ids=(),
        )
        return PageBands(
            page_number=page_number,
            crop_box_mpt=crop,
            bands=(band,),
            score_ppm=0,
            evidence=("no-occupancy",),
        )

    crop_width = crop[2] - crop[0]
    crop_height = crop[3] - crop[1]
    minimum_gutter = max(_MINIMUM_GUTTER_MPT, crop_width * 2 // 100)
    minimum_horizontal_gap = max(
        _MINIMUM_HORIZONTAL_GAP_MPT,
        crop_height // 100,
    )
    content = union_box(boxes)
    wide_threshold = (content[2] - content[0]) * 52 // 100
    nonwide = tuple(
        item for item in boxes if item.bbox_mpt[2] - item.bbox_mpt[0] < wide_threshold
    )
    anchor_source = nonwide or boxes
    anchor_tolerance = max(12_000, crop_width * 25 // 1_000)
    anchors = document_anchors or _cluster_anchors(
        anchor_source,
        tolerance_mpt=anchor_tolerance,
        minimum_separation_mpt=max(60_000, crop_width // 10),
    )
    global_count = max(1, len(anchors))

    raw_groups = _horizontal_groups(boxes, minimum_horizontal_gap)
    groups: list[_HorizontalGroup] = []
    observed_column_count = global_count
    for members in raw_groups:
        barrier = any(
            item.bbox_mpt[2] - item.bbox_mpt[0] >= wide_threshold for item in members
        )
        cut = vertical_xy_cut(members, minimum_gutter_mpt=minimum_gutter)
        observed_column_count = max(observed_column_count, len(cut.regions))
        cut_count = min(_MAX_COLUMNS, len(cut.regions))
        active_anchors = {
            _document_anchor(
                item,
                anchors,
                tolerance_mpt=(anchor_tolerance if document_anchors else None),
            )
            for item in members
            if anchors and item.bbox_mpt[2] - item.bbox_mpt[0] < wide_threshold
        }
        label: int | None
        if barrier and global_count > 1:
            label = 1
        elif cut_count > 1:
            label = cut_count
        elif len(active_anchors) > 1:
            label = min(_MAX_COLUMNS, max(active_anchors) + 1)
        elif global_count == 1:
            label = 1
        else:
            label = None
        groups.append(
            _HorizontalGroup(
                boxes=members,
                wide_barrier=barrier and global_count > 1,
                label=label,
            )
        )

    typical_font = sorted(
        item.font_size_mpt for item in boxes if item.font_size_mpt > 0
    )
    median_font = typical_font[len(typical_font) // 2] if typical_font else 10_000
    groups = _resolve_unknown_labels(
        groups,
        inherit_gap_mpt=max(30_000, median_font * 3),
    )
    merged = _merge_labelled_groups(groups)

    bands: list[BandGeometry] = []
    for band_index, (
        column_count,
        members,
        has_barrier,
        inferred_by_adjacency,
    ) in enumerate(
        merged,
        start=1,
    ):
        band_box = union_box(members)
        if column_count == 1:
            columns = _single_column(
                page_number,
                band_index,
                members,
                crop,
            )
            gutters: tuple[GutterGeometry, ...] = ()
            if global_count == 1:
                if len(members) == 1:
                    score = 650_000
                elif len(members) == 2:
                    score = 760_000
                else:
                    score = 950_000
                evidence = (
                    "single-column-occupancy",
                    f"support-objects:{len(members)}",
                )
            elif has_barrier:
                score = 920_000
                evidence = ("wide-barrier", f"support-objects:{len(members)}")
            else:
                score = 780_000
                evidence = (
                    "single-column-by-adjacency",
                    f"support-objects:{len(members)}",
                )
        else:
            columns, gutters, score = _multicolumn_geometry(
                page_number,
                band_index,
                members,
                crop,
                column_count,
                anchors,
                minimum_gutter,
                anchor_tolerance if document_anchors else None,
            )
            column_count = len(columns)
            evidence = (
                f"sustained-gutters:{len(gutters)}",
                f"column-count:{column_count}",
                f"support-min:{min(len(item.evidence_ids) for item in columns)}",
            )
            if inferred_by_adjacency:
                if document_anchors:
                    evidence = (*evidence, "document-supported-adjacency")
                else:
                    score = min(score, 780_000)
                    evidence = (*evidence, "adjacency-inference")
            if observed_column_count > _MAX_COLUMNS:
                score = min(score, 300_000)
                evidence = (
                    *evidence,
                    f"unsupported-column-count:{observed_column_count}",
                )
        bands.append(
            BandGeometry(
                id=f"p{page_number:04d}-band-{band_index:03d}",
                index=band_index,
                y_top_mpt=band_box[3],
                y_bottom_mpt=band_box[1],
                columns=columns,
                gutters=gutters,
                score_ppm=score,
                evidence=evidence,
                object_ids=tuple(item.id for item in members),
            )
        )
    page_score = min(item.score_ppm for item in bands)
    page_evidence = [
        f"occupancy-objects:{len(boxes)}",
        f"anchor-count:{global_count}",
        f"band-count:{len(bands)}",
    ]
    if document_anchors:
        page_evidence.append(f"document-column-template:{len(document_anchors)}")
    if observed_column_count > _MAX_COLUMNS:
        page_score = min(page_score, 300_000)
        page_evidence.append(f"unsupported-column-count:{observed_column_count}")
    return PageBands(
        page_number=page_number,
        crop_box_mpt=crop,
        bands=tuple(bands),
        score_ppm=page_score,
        evidence=tuple(page_evidence),
    )


def detect_page_bands(page: Mapping[str, object]) -> PageBands:
    """Detect one page without borrowing layout evidence from other pages."""

    return _detect_page_bands(page)


def _document_column_template(
    detected_pages: Sequence[PageBands],
) -> tuple[int, ...] | None:
    candidates: dict[int, list[tuple[int, ...]]] = {}
    for page in detected_pages:
        supported = [
            band
            for band in page.bands
            if band.score_ppm >= _DOCUMENT_TEMPLATE_MIN_SCORE_PPM
            and 1 < len(band.columns) <= _MAX_COLUMNS
        ]
        counts = {len(band.columns) for band in supported}
        if len(counts) != 1:
            continue
        representative = max(
            supported,
            key=lambda band: (len(band.object_ids), band.score_ppm, -band.index),
        )
        candidates.setdefault(len(representative.columns), []).append(
            tuple(column.x_left_mpt for column in representative.columns)
        )
    ranked = sorted(candidates.items(), key=lambda item: (-len(item[1]), item[0]))
    if len(ranked) == 0 or len(ranked[0][1]) < 2:
        return None
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return None
    _column_count, anchors = ranked[0]
    crop_width = detected_pages[0].crop_box_mpt[2] - detected_pages[0].crop_box_mpt[0]
    tolerance = max(12_000, crop_width * 25 // 1_000)
    if any(
        max(values) - min(values) > tolerance for values in zip(*anchors, strict=True)
    ):
        return None
    return tuple(min(values) for values in zip(*anchors, strict=True))


def detect_document_bands(
    extraction: Mapping[str, object],
) -> tuple[PageBands, ...]:
    """Detect every page in a JSON-safe extraction artifact or payload."""

    pages = extraction.get("pages")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or not pages:
        raise ValueError("extraction must contain at least one page")
    if not all(isinstance(page, Mapping) for page in pages):
        raise ValueError("every extraction page must be an object")
    first_pass = tuple(detect_page_bands(page) for page in pages)
    if [page.page_number for page in first_pass] != list(range(1, len(first_pass) + 1)):
        raise ValueError("extraction page numbers must be contiguous")
    document_anchors = _document_column_template(first_pass)
    if document_anchors is None:
        return first_pass
    result: list[PageBands] = []
    for page, detected in zip(pages, first_pass, strict=True):
        if detected.score_ppm >= _DOCUMENT_TEMPLATE_MIN_SCORE_PPM:
            result.append(detected)
            continue
        retried = _detect_page_bands(page, document_anchors=document_anchors)
        has_multicolumn_support = any(len(band.columns) > 1 for band in retried.bands)
        result.append(
            retried
            if has_multicolumn_support and retried.score_ppm > detected.score_ppm
            else detected
        )
    return tuple(result)
