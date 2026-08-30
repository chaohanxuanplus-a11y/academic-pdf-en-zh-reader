# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic fixed-size CJK typography primitives."""

from academic_pdf_en_zh_reader.typography.cjk_breaker import break_text
from academic_pdf_en_zh_reader.typography.font_registry import (
    FontRegistry,
    load_font_registry,
)
from academic_pdf_en_zh_reader.typography.font_runs import (
    FontRunResolver,
    ResolvedText,
)
from academic_pdf_en_zh_reader.typography.measure import LineBox, draw_line
from academic_pdf_en_zh_reader.typography.style_contract import (
    TypographyStyleContract,
    build_style_contract,
)

__all__ = [
    "FontRegistry",
    "FontRunResolver",
    "LineBox",
    "ResolvedText",
    "TypographyStyleContract",
    "break_text",
    "build_style_contract",
    "draw_line",
    "load_font_registry",
]
