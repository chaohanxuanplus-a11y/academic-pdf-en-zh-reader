# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Strict separation of source-backed pages from one final disclosure page."""

from __future__ import annotations

from collections.abc import Mapping

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

SOURCE_FIELDS = (
    "source_page_number",
    "source_crop_box_mpt",
    "source_normalized_visible_box_mpt",
    "source_rotation_degrees",
    "source_transform_mpt",
)


def source_manifest_pages(pages: object) -> list:
    if not isinstance(pages, list) or not pages:
        raise ValueError("invalid manifest pages")
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            raise ValueError("invalid manifest page")
        if page.get("page_kind") != "disclaimer":
            continue
        if (
            index != len(pages) - 1
            or index == 0
            or page.get("output_page_number") != index + 1
            or any(
                field not in page or page[field] is not None for field in SOURCE_FIELDS
            )
            or type(page.get("continuation_index")) is not int
            or page["continuation_index"] != 0
            or page.get("continuation_label_present") is not False
        ):
            raise ValueError("invalid final disclaimer mapping")
        return pages[:-1]
    return pages


def source_plan_pages(layout: Mapping, plan: Mapping) -> list:
    layout_pages = layout.get("pages")
    pages = plan.get("pages")
    if not isinstance(layout_pages, list) or not isinstance(pages, list):
        raise ValueError("invalid plan pages")
    count = len(layout_pages)
    if not count or len(pages) not in (count, count + 1):
        raise ValueError("invalid plan page count")
    source_pages = pages[:count]
    for layout_page, plan_page in zip(layout_pages, source_pages, strict=True):
        if plan_page.get("page_kind") not in {"native", "continuation"} or any(
            plan_page.get(field) != layout_page.get(field)
            for field in (
                "page_number",
                "source_page_number",
                "page_kind",
                "continuation_index",
            )
        ):
            raise ValueError("source-backed plan page mismatch")
    appended = plan.get("branding", {}).get("appended_page_number")
    if len(pages) == count:
        if appended is not None:
            raise ValueError("missing appended disclaimer")
        return source_pages
    page = pages[-1]
    if (
        page.get("page_kind") != "disclaimer"
        or page.get("page_number") != count + 1
        or appended != count + 1
        or "source_page_number" not in page
        or page["source_page_number"] is not None
        or type(page.get("continuation_index")) is not int
        or page["continuation_index"] != 0
        or page.get("continuation_label") is not None
        or not isinstance(page.get("brand_block"), Mapping)
        or not page["brand_block"]
        or type(page.get("source_obstacle_count")) is not int
        or page.get("source_obstacle_count") != 0
        or page.get("source_obstacles_hash") != sha256_canonical([])
        or page.get("underlines") != []
        or page.get("leader_routes") != []
        or any(
            not isinstance(page.get(field), list)
            or not page[field]
            or any(item.get("content_kind") != "brand" for item in page[field])
            for field in ("draw_runs", "line_bindings")
        )
    ):
        raise ValueError("invalid final disclaimer plan")
    if any(page.get("brand_block") is not None for page in source_pages):
        raise ValueError("duplicate disclaimer branding")
    return source_pages
