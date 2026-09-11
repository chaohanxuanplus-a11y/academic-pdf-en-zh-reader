# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic vector-page and right-panel overlay primitives."""

from academic_pdf_en_zh_reader.rendering.compose import (
    CompositionError,
    CompositionResult,
    compose_bilingual_pdf,
)
from academic_pdf_en_zh_reader.rendering.contracts import (
    OverlayPlanError,
    OverlayPlanLimits,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan

__all__ = [
    "CompositionError",
    "CompositionResult",
    "OverlayPlanError",
    "OverlayPlanLimits",
    "build_overlay_plan",
    "compose_bilingual_pdf",
]
