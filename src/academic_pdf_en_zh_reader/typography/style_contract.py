# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Versioned, document-wide typography sizes derived from English body text."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

STYLE_CONTRACT_VERSION = 2
AUXILIARY_MIN_SIZE_MPT = 7_000
BODY_LINE_HEIGHT_NUMERATOR = 130
BODY_LINE_HEIGHT_DENOMINATOR = 100


@dataclass(frozen=True)
class FontSizeSample:
    """One English body span size and its visible character contribution."""

    size_mpt: int
    character_count: int


@dataclass(frozen=True)
class RoleStyle:
    """Fixed font role, point size, and line height for one semantic role."""

    font_role: str
    size_mpt: int
    line_height_mpt: int

    @property
    def size_pt(self) -> float:
        return self.size_mpt / 1_000

    @property
    def line_height_pt(self) -> float:
        return self.line_height_mpt / 1_000


@dataclass(frozen=True)
class TypographyStyleContract:
    """Immutable role styles that cannot drift by page or text block."""

    version: int
    body_source_size_mpt: int
    styles: tuple[tuple[str, RoleStyle], ...]

    def style_for(self, role: str) -> RoleStyle:
        for candidate_role, style in self.styles:
            if candidate_role == role:
                return style
        raise ValueError(f"unsupported typography role: {role}")

    def ambiguity_size_mpt(self, containing_role: str) -> int:
        """Keep the red ambiguity label exactly at its translation role size."""

        return self.style_for(containing_role).size_mpt


def _rounded_ratio(value: int, numerator: int, denominator: int = 100) -> int:
    return (value * numerator + denominator // 2) // denominator


def detect_body_size_mpt(samples: Sequence[FontSizeSample]) -> int:
    """Return robustly-filtered, character-weighted English body size mode."""

    if not samples or any(
        type(sample.size_mpt) is not int
        or type(sample.character_count) is not int
        or sample.size_mpt <= 0
        or sample.character_count <= 0
        for sample in samples
    ):
        raise ValueError("body size evidence must contain positive integer values")

    sizes = [sample.size_mpt for sample in samples]
    center = float(median(sizes))
    absolute_deviations = [abs(size - center) for size in sizes]
    mad = float(median(absolute_deviations))
    tolerance = max(500.0, center * 0.15, mad * 3.0)
    filtered = [
        sample for sample in samples if abs(sample.size_mpt - center) <= tolerance
    ]
    if not filtered:
        raise ValueError("body size evidence has no sample after robust filtering")

    weights: defaultdict[int, int] = defaultdict(int)
    for sample in filtered:
        weights[sample.size_mpt] += sample.character_count
    return min(
        weights,
        key=lambda size: (-weights[size], abs(size - center), size),
    )


def _style(font_role: str, size_mpt: int) -> RoleStyle:
    return RoleStyle(
        font_role=font_role,
        size_mpt=size_mpt,
        line_height_mpt=_rounded_ratio(
            size_mpt,
            BODY_LINE_HEIGHT_NUMERATOR,
            BODY_LINE_HEIGHT_DENOMINATOR,
        ),
    )


def build_style_contract(
    body_samples: Sequence[FontSizeSample],
) -> TypographyStyleContract:
    """Build the v1 calibrated role map from English body evidence once."""

    body = 10_000
    title = _rounded_ratio(body, 160)
    heading = _rounded_ratio(body, 120)
    caption = max(_rounded_ratio(body, 92), AUXILIARY_MIN_SIZE_MPT)
    auxiliary = 9_000
    body_style = _style("body", body)
    styles = (
        ("body", body_style),
        ("abstract", body_style),
        ("keywords", body_style),
        ("title", _style("heading", title)),
        ("heading", _style("heading", heading)),
        ("figure-caption", _style("body", caption)),
        ("table-caption", _style("body", caption)),
        ("footnote", _style("body", caption)),
        ("auxiliary", _style("body", auxiliary)),
    )
    return TypographyStyleContract(
        version=STYLE_CONTRACT_VERSION,
        body_source_size_mpt=body,
        styles=styles,
    )
