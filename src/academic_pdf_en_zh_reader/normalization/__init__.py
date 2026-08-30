# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic vector normalization into an A4 portrait canvas."""

from .core import (
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
    NORMALIZATION_POLICY_VERSION,
    NormalizationError,
    NormalizationPlan,
    NormalizationResult,
    normalize_pdf_bytes,
    plan_displayed_crop,
)

__all__ = [
    "A4_HEIGHT_MPT",
    "A4_WIDTH_MPT",
    "NORMALIZATION_POLICY_VERSION",
    "NormalizationError",
    "NormalizationPlan",
    "NormalizationResult",
    "normalize_pdf_bytes",
    "plan_displayed_crop",
]
