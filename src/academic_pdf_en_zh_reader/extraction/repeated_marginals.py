# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Mark repeated marginal text and watermarks without deleting source text."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import replace
from statistics import median

from academic_pdf_en_zh_reader.extraction.page_objects import PageObjects
from academic_pdf_en_zh_reader.extraction.text_lines import PageTextLines, TextLine

_PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?(?:\d+|[ivxlcdm]+)(?:\s+of\s+(?:\d+|[ivxlcdm]+))?$",
    re.IGNORECASE | re.ASCII,
)


def _signature(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.casefold().split()))


def _is_light(line: TextLine) -> bool:
    numeric_components = [
        component
        for color in line.fill_colors
        if color is not None
        for component in color
        if isinstance(component, int)
    ]
    return (
        bool(numeric_components)
        and sum(numeric_components) / len(numeric_components) >= 600_000
    )


def mark_repeated_marginals(
    line_pages: tuple[PageTextLines, ...],
    pages: tuple[PageObjects, ...],
) -> tuple[PageTextLines, ...]:
    """Explicitly exclude repeated headers, footers, page numbers, and watermarks."""

    if [page.page_number for page in line_pages] != [
        page.page_number for page in pages
    ]:
        raise ValueError("page objects and text lines must have matching pages")
    if len(pages) < 2:
        return line_pages

    page_by_number = {page.page_number: page for page in pages}
    occurrences: dict[str, list[TextLine]] = defaultdict(list)
    all_lines = [line for page in line_pages for line in page.lines]
    for line in all_lines:
        occurrences[_signature(line.text)].append(line)
    threshold = max(2, math.ceil(len(pages) * 0.6))
    parity_totals = {
        parity: sum(page.page_number % 2 == parity for page in pages)
        for parity in (0, 1)
    }
    repeated: set[str] = set()
    for signature, lines in occurrences.items():
        page_numbers = {line.page_number for line in lines}
        repeats_globally = len(page_numbers) >= threshold
        repeats_by_parity = len(pages) >= 4 and any(
            parity_totals[parity] >= 2
            and sum(number % 2 == parity for number in page_numbers)
            >= max(2, math.ceil(parity_totals[parity] * 0.6))
            for parity in (0, 1)
        )
        if repeats_globally or repeats_by_parity:
            repeated.add(signature)
    typical_font = int(median(line.max_font_size_mpt for line in all_lines))

    def classify(line: TextLine) -> str | None:
        page = page_by_number[line.page_number]
        _x0, page_y0, _x1, page_y1 = page.crop_box_mpt
        height = page_y1 - page_y0
        if line.bbox_mpt[1] <= page_y0 + height * 12 // 100 and _PAGE_NUMBER.fullmatch(
            " ".join(line.text.split())
        ):
            return "page-number"
        if _signature(line.text) not in repeated:
            return None
        if line.bbox_mpt[3] >= page_y1 - height * 12 // 100:
            return "repeated-header"
        if line.bbox_mpt[1] <= page_y0 + height * 12 // 100:
            return "repeated-footer"
        if line.max_font_size_mpt >= typical_font * 3 // 2 or _is_light(line):
            return "watermark"
        return None

    return tuple(
        PageTextLines(
            page_number=line_page.page_number,
            lines=tuple(
                replace(
                    line,
                    body_eligible=False,
                    coverage_eligible=False,
                    exclusion_kind=kind,
                )
                if (kind := classify(line)) is not None
                else line
                for line in line_page.lines
            ),
        )
        for line_page in line_pages
    )
