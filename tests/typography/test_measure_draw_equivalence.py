# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from reportlab.pdfbase import pdfmetrics

from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.measure import (
    draw_line,
    make_line_box,
    measure_text,
)


class RecordingCanvas:
    def __init__(self) -> None:
        self.font_calls: list[tuple[str, float]] = []
        self.draw_calls: list[tuple[float, float, str]] = []

    def setFont(self, name: str, size: float) -> None:  # noqa: N802
        self.font_calls.append((name, size))

    def drawString(self, x: float, y: float, text: str) -> None:  # noqa: N802
        self.draw_calls.append((x, y, text))


def test_measurement_and_drawing_reuse_the_exact_resolved_runs() -> None:
    registry = load_font_registry()
    resolved = FontRunResolver(registry).resolve("中文☢研究", font_role="body")
    line = make_line_box(
        resolved,
        size_pt=10.0,
        line_height_pt=17.0,
    )
    canvas = RecordingCanvas()

    drawn = draw_line(canvas, line, x_pt=72.0, baseline_y_pt=700.0)

    assert drawn.line is line
    assert canvas.font_calls == [(run.font_name, 10.0) for run in resolved.runs]
    assert [call[2] for call in canvas.draw_calls] == [
        run.text for run in resolved.runs
    ]
    assert drawn.width_pt == line.width_pt
    assert drawn.end_x_pt == pytest.approx(72.0 + line.width_pt, abs=1e-9)
    assert canvas.draw_calls[1][0] == pytest.approx(
        72.0
        + pdfmetrics.stringWidth(
            resolved.runs[0].text,
            resolved.runs[0].font_name,
            10.0,
        ),
        abs=1e-9,
    )


def test_line_metrics_repeat_exactly_and_cover_every_run() -> None:
    resolved = FontRunResolver(load_font_registry()).resolve(
        "正文⏱",
        font_role="body",
    )
    first = make_line_box(resolved, size_pt=10.0, line_height_pt=17.0)
    second = make_line_box(resolved, size_pt=10.0, line_height_pt=17.0)
    ascents = []
    descents = []
    for run in resolved.runs:
        ascent, descent = pdfmetrics.getAscentDescent(run.font_name, 10.0)
        ascents.append(ascent)
        descents.append(descent)

    assert first == second
    assert first.ascent_pt == max(ascents)
    assert first.descent_pt == min(descents)
    assert first.natural_height_pt == first.ascent_pt - first.descent_pt
    assert first.line_height_pt == 17.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_font_size_and_line_height_fail_closed(value: float) -> None:
    resolved = FontRunResolver(load_font_registry()).resolve(
        "正文",
        font_role="body",
    )

    with pytest.raises(ValueError, match="finite"):
        measure_text(resolved, size_pt=value)
    with pytest.raises(ValueError, match="finite"):
        make_line_box(resolved, size_pt=10.0, line_height_pt=value)


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_draw_coordinates_fail_before_canvas_use(coordinate: float) -> None:
    resolved = FontRunResolver(load_font_registry()).resolve(
        "正文",
        font_role="body",
    )
    line = make_line_box(resolved, size_pt=10.0, line_height_pt=17.0)
    canvas = RecordingCanvas()

    with pytest.raises(ValueError, match="finite"):
        draw_line(canvas, line, x_pt=coordinate, baseline_y_pt=700.0)
    with pytest.raises(ValueError, match="finite"):
        draw_line(canvas, line, x_pt=72.0, baseline_y_pt=coordinate)
    assert canvas.draw_calls == []
