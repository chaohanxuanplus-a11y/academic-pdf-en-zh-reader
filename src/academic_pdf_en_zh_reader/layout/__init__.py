# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic right-panel flow graph construction."""

from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    DEFAULT_ANNOTATION_ADAPTER_LIMITS,
    AnnotationAdapterError,
    AnnotationAdapterLimits,
    build_final_annotated_frame_graph,
    make_annotation_layout_trial,
    validate_annotated_frame_graph_against_inputs,
)
from academic_pdf_en_zh_reader.layout.frame_graph import (
    DEFAULT_FRAME_GRAPH_CONFIG,
    FrameGraphConfig,
    FrameGraphError,
    build_frame_graph,
    validate_frame_graph_against_inputs,
)
from academic_pdf_en_zh_reader.layout.solver import (
    DEFAULT_LAYOUT_LIMITS,
    LayoutComplexityError,
    LayoutInfeasibleError,
    LayoutLimits,
    LayoutSolverError,
    solve_layout,
    validate_layout_against_frame_graph,
)

__all__ = [
    "DEFAULT_ANNOTATION_ADAPTER_LIMITS",
    "AnnotationAdapterLimits",
    "AnnotationAdapterError",
    "DEFAULT_FRAME_GRAPH_CONFIG",
    "DEFAULT_LAYOUT_LIMITS",
    "FrameGraphConfig",
    "FrameGraphError",
    "LayoutComplexityError",
    "LayoutInfeasibleError",
    "LayoutLimits",
    "LayoutSolverError",
    "build_frame_graph",
    "build_final_annotated_frame_graph",
    "make_annotation_layout_trial",
    "solve_layout",
    "validate_frame_graph_against_inputs",
    "validate_annotated_frame_graph_against_inputs",
    "validate_layout_against_frame_graph",
]
