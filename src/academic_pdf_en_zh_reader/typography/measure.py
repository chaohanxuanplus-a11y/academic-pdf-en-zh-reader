# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Measure and draw the same immutable ReportLab font runs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from reportlab.pdfbase import pdfmetrics

from academic_pdf_en_zh_reader.typography.font_runs import ResolvedText


class DrawingCanvas(Protocol):
    """Small ReportLab canvas surface needed by deterministic text drawing."""

    def setFont(self, name: str, size: float) -> None:  # noqa: N802
        ...

    def drawString(  # noqa: N802
        self,
        x: float,
        y: float,
        text: str,
    ) -> None: ...


@dataclass(frozen=True)
class TextMetrics:
    width_pt: float
    ascent_pt: float
    descent_pt: float
    natural_height_pt: float


@dataclass(frozen=True)
class LineBox:
    """A measured line whose exact resolved runs are its drawing input."""

    text: str
    resolved: ResolvedText
    size_pt: float
    width_pt: float
    ascent_pt: float
    descent_pt: float
    natural_height_pt: float
    line_height_pt: float


@dataclass(frozen=True)
class DrawnLine:
    line: LineBox
    start_x_pt: float
    end_x_pt: float
    baseline_y_pt: float
    width_pt: float


def _finite_number(
    value: object,
    *,
    label: str,
    positive: bool,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (positive and value <= 0)
    ):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{label} must be {qualifier}")
    return float(value)


def measure_text(resolved: ResolvedText, *, size_pt: float) -> TextMetrics:
    """Measure only with the ReportLab faces stored in ``resolved``."""

    size_pt = _finite_number(size_pt, label="font size", positive=True)
    if not resolved.runs:
        raise ValueError("resolved text must contain at least one font run")
    widths: list[float] = []
    ascents: list[float] = []
    descents: list[float] = []
    for run in resolved.runs:
        widths.append(pdfmetrics.stringWidth(run.text, run.font_name, size_pt))
        ascent, descent = pdfmetrics.getAscentDescent(run.font_name, size_pt)
        ascents.append(ascent)
        descents.append(descent)
    ascent = max(ascents)
    descent = min(descents)
    if not all(math.isfinite(value) for value in (*widths, ascent, descent)):
        raise ValueError("ReportLab text metrics must be finite")
    return TextMetrics(
        width_pt=sum(widths),
        ascent_pt=ascent,
        descent_pt=descent,
        natural_height_pt=ascent - descent,
    )


def make_line_box(
    resolved: ResolvedText,
    *,
    size_pt: float,
    line_height_pt: float | None = None,
) -> LineBox:
    """Bind stable metrics and leading to the exact resolved run tuple."""

    metrics = measure_text(resolved, size_pt=size_pt)
    requested = (
        metrics.natural_height_pt
        if line_height_pt is None
        else _finite_number(line_height_pt, label="line height", positive=True)
    )
    return LineBox(
        text=resolved.text,
        resolved=resolved,
        size_pt=size_pt,
        width_pt=metrics.width_pt,
        ascent_pt=metrics.ascent_pt,
        descent_pt=metrics.descent_pt,
        natural_height_pt=metrics.natural_height_pt,
        line_height_pt=max(requested, metrics.natural_height_pt),
    )


def draw_line(
    canvas: DrawingCanvas,
    line: LineBox,
    *,
    x_pt: float,
    baseline_y_pt: float,
) -> DrawnLine:
    """Draw exactly the runs already used to produce ``line.width_pt``."""

    x_pt = _finite_number(x_pt, label="x coordinate", positive=False)
    baseline_y_pt = _finite_number(
        baseline_y_pt,
        label="baseline y coordinate",
        positive=False,
    )
    _finite_number(line.width_pt, label="line width", positive=False)
    _finite_number(line.size_pt, label="line font size", positive=True)
    cursor = x_pt
    for run in line.resolved.runs:
        canvas.setFont(run.font_name, line.size_pt)
        canvas.drawString(cursor, baseline_y_pt, run.text)
        cursor += pdfmetrics.stringWidth(run.text, run.font_name, line.size_pt)
    return DrawnLine(
        line=line,
        start_x_pt=x_pt,
        end_x_pt=x_pt + line.width_pt,
        baseline_y_pt=baseline_y_pt,
        width_pt=line.width_pt,
    )
