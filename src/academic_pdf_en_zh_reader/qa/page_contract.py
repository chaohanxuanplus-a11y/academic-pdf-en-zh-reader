# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Separate left-panel source identity from independent Chinese pagination."""

from collections.abc import Mapping

SOURCE_FIELDS = (
    "source_page_number",
    "source_crop_box_mpt",
    "source_normalized_visible_box_mpt",
    "source_rotation_degrees",
    "source_transform_mpt",
)


def source_manifest_pages(pages):
    if not isinstance(pages, list) or not pages:
        raise ValueError("invalid manifest pages")
    native, extra = [], 0
    for index, page in enumerate(pages, 1):
        if not isinstance(page, Mapping) or page["output_page_number"] != index:
            raise ValueError("invalid manifest page order")
        if page["page_kind"] == "native":
            if (
                extra
                or page["source_page_number"] != index
                or page["continuation_index"] != 0
            ):
                raise ValueError("invalid original page order")
            native.append(page)
        elif page["page_kind"] == "continuation":
            extra += 1
            if (
                any(page.get(field) is not None for field in SOURCE_FIELDS)
                or page["continuation_index"] != extra
            ):
                raise ValueError("additional page has a fabricated source")
        else:
            raise ValueError("invalid page kind")
        if page["continuation_label_present"]:
            raise ValueError("continuous reading has no continuation label")
    if not native:
        raise ValueError("missing original pages")
    return native


def source_plan_pages(layout, plan):
    pages = plan.get("pages")
    if not isinstance(pages, list) or len(pages) != len(layout["pages"]):
        raise ValueError("plan page count mismatch")
    for lp, pp in zip(layout["pages"], pages, strict=True):
        if any(
            lp[k] != pp[k]
            for k in (
                "page_number",
                "source_page_number",
                "page_kind",
                "continuation_index",
                "continuation_label",
            )
        ):
            raise ValueError("plan target binding mismatch")
    if [p["page_number"] for p in pages if p.get("brand_block")] != [len(pages)]:
        raise ValueError("statement must appear once on the final page")
    return pages
