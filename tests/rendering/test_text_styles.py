# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unicodedata import category

import pytest

from academic_pdf_en_zh_reader.annotations.ambiguity import (
    AmbiguityOccurrence,
    build_ambiguity_marks,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.red_emphasis import SelectedRed
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialResult,
    select_orange_annotations,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical, stable_source_id
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    build_final_annotated_frame_graph,
)
from academic_pdf_en_zh_reader.layout.solver import solve_layout
from academic_pdf_en_zh_reader.rendering.contracts import (
    FrozenContinuationHeader,
    FrozenContinuationLabel,
    OverlayPlanError,
    OverlayPlanLimits,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from academic_pdf_en_zh_reader.rendering.text_draw import _draw_validated_page
from academic_pdf_en_zh_reader.review.review_validation import make_ambiguity_key
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)


def build_render_fixture(
    *,
    source_sha256: str = "d" * 64,
    normalized_pdf_sha256: str | None = None,
    chinese_text: str = "甲☢乙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥",
    column_count: int = 1,
    crop_left: int = 0,
    crop_bottom: int = 0,
    narrow: bool = False,
    include_red: bool = True,
    include_ambiguity: bool = True,
    include_auxiliary: bool = True,
    graphic_bbox: list[int] | None = None,
    page_media_box_mpt: list[int] | None = None,
    page_crop_box_mpt: list[int] | None = None,
    rotation_degrees: int = 0,
    include_reference_page: bool = False,
    include_reference_heading: bool = False,
    reference_heading_chinese_text: str = "参考文献",
    include_following_reference_page: bool = False,
) -> tuple[dict[str, object], ...]:
    if normalized_pdf_sha256 is None:
        normalized_pdf_sha256 = source_sha256
    source_text = "Key scientific term in context."
    unit_id = stable_source_id(
        page_number=1,
        reading_order=0,
        role="body",
        source_char_start=0,
        source_char_end=len(source_text),
    )
    first_line_bbox = [
        crop_left + 45_000,
        crop_bottom + 700_000,
        crop_left + 110_000,
        crop_bottom + 730_000,
    ]
    block = {
        "id": unit_id,
        "role": "body",
        "translation_policy": "required",
        "band_id": "body-band",
        "column_id": "body-column-0",
        "reading_order": 0,
        "source_char_start": 0,
        "source_char_end": len(source_text),
        "text": source_text,
        "bbox_mpt": list(first_line_bbox),
        "first_line_bbox_mpt": list(first_line_bbox),
        "confidence_ppm": 990_000,
    }
    if column_count == 1:
        columns = [
            {
                "id": "body-column-0",
                "x_left_mpt": crop_left + 40_000,
                "x_right_mpt": crop_left + (115_000 if narrow else 555_276),
            }
        ]
    else:
        columns = [
            {
                "id": "body-column-0",
                "x_left_mpt": crop_left + 40_000,
                "x_right_mpt": crop_left + 290_000,
            },
            {
                "id": "body-column-1",
                "x_left_mpt": crop_left + 310_000,
                "x_right_mpt": crop_left + 555_276,
            },
        ]
    graphics = []
    if graphic_bbox is not None:
        graphics.append(
            {
                "id": "graphic-obstacle",
                "kind": "figure",
                "bbox_mpt": [
                    graphic_bbox[0] + crop_left,
                    graphic_bbox[1] + crop_bottom,
                    graphic_bbox[2] + crop_left,
                    graphic_bbox[3] + crop_bottom,
                ],
                "confidence_ppm": 990_000,
            }
        )
    source: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": (
                    page_media_box_mpt
                    if page_media_box_mpt is not None
                    else [
                        crop_left,
                        crop_bottom,
                        crop_left + 595_276,
                        crop_bottom + 841_890,
                    ]
                ),
                "crop_box_mpt": (
                    page_crop_box_mpt
                    if page_crop_box_mpt is not None
                    else [
                        crop_left,
                        crop_bottom,
                        crop_left + 595_276,
                        crop_bottom + 841_890,
                    ]
                ),
                "rotation_degrees": rotation_degrees,
                "bands": [
                    {
                        "id": "body-band",
                        "y_top_mpt": crop_bottom + 810_000,
                        "y_bottom_mpt": crop_bottom + 30_000,
                        "columns": columns,
                    }
                ],
                "graphic_nodes": graphics,
                "blocks": [block],
            }
        ],
    }
    if include_reference_page:
        reference_heading_text = "References"
        reference_heading_id = stable_source_id(
            page_number=2,
            reading_order=1,
            role="heading",
            source_char_start=0,
            source_char_end=len(reference_heading_text),
        )
        reference_text = "[1] Example reference entry. Journal 2026;1:1-2."
        reference_start = (
            len(reference_heading_text) + 1 if include_reference_heading else 0
        )
        reference_entry_top = 730_000 if include_reference_heading else 780_000
        reference_id = stable_source_id(
            page_number=2,
            reading_order=2 if include_reference_heading else 1,
            role="reference-entry",
            source_char_start=reference_start,
            source_char_end=reference_start + len(reference_text),
        )
        reference_blocks = []
        if include_reference_heading:
            reference_blocks.append(
                {
                    "id": reference_heading_id,
                    "role": "heading",
                    "translation_policy": "required",
                    "band_id": "references-band",
                    "column_id": "references-column-0",
                    "reading_order": 1,
                    "source_char_start": 0,
                    "source_char_end": len(reference_heading_text),
                    "text": reference_heading_text,
                    "bbox_mpt": [
                        crop_left + 40_000,
                        crop_bottom + 760_000,
                        crop_left + 180_000,
                        crop_bottom + 780_000,
                    ],
                    "first_line_bbox_mpt": [
                        crop_left + 40_000,
                        crop_bottom + 760_000,
                        crop_left + 180_000,
                        crop_bottom + 780_000,
                    ],
                    "confidence_ppm": 990_000,
                }
            )
        reference_blocks.append(
            {
                "id": reference_id,
                "role": "reference-entry",
                "translation_policy": "excluded",
                "band_id": "references-band",
                "column_id": "references-column-0",
                "reading_order": 2 if include_reference_heading else 1,
                "source_char_start": reference_start,
                "source_char_end": reference_start + len(reference_text),
                "text": reference_text,
                "bbox_mpt": [
                    crop_left + 40_000,
                    crop_bottom + 100_000,
                    crop_left + 555_276,
                    crop_bottom + reference_entry_top,
                ],
                "first_line_bbox_mpt": [
                    crop_left + 40_000,
                    crop_bottom + reference_entry_top - 20_000,
                    crop_left + 300_000,
                    crop_bottom + reference_entry_top,
                ],
                "confidence_ppm": 990_000,
            }
        )
        source["pages"].append(  # type: ignore[union-attr]
            {
                "page_number": 2,
                "media_box_mpt": [
                    crop_left,
                    crop_bottom,
                    crop_left + 595_276,
                    crop_bottom + 841_890,
                ],
                "crop_box_mpt": [
                    crop_left,
                    crop_bottom,
                    crop_left + 595_276,
                    crop_bottom + 841_890,
                ],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "references-band",
                        "y_top_mpt": crop_bottom + 810_000,
                        "y_bottom_mpt": crop_bottom + 30_000,
                        "columns": [
                            {
                                "id": "references-column-0",
                                "x_left_mpt": crop_left + 40_000,
                                "x_right_mpt": crop_left + 555_276,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [],
                "blocks": reference_blocks,
            }
        )
        if include_following_reference_page:
            following_text = "[2] Following reference entry. Journal 2026;1:3-4."
            following_reading_order = 3 if include_reference_heading else 2
            following_id = stable_source_id(
                page_number=3,
                reading_order=following_reading_order,
                role="reference-entry",
                source_char_start=0,
                source_char_end=len(following_text),
            )
            following_page = deepcopy(source["pages"][-1])  # type: ignore[index]
            following_page["page_number"] = 3
            following_band = following_page["bands"][0]
            following_band["id"] = "following-references-band"
            following_column = following_band["columns"][0]
            following_column["id"] = "following-references-column-0"
            following_entry = deepcopy(reference_blocks[-1])
            following_entry.update(
                id=following_id,
                band_id=following_band["id"],
                column_id=following_column["id"],
                reading_order=following_reading_order,
                source_char_start=0,
                source_char_end=len(following_text),
                text=following_text,
            )
            following_page["blocks"] = [following_entry]
            source["pages"].append(following_page)  # type: ignore[union-attr]
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": source["source_sha256"],
        "normalized_pdf_sha256": source["normalized_pdf_sha256"],
        "units": [
            {
                "id": unit_id,
                "role": "body",
                "reading_order": 0,
                "source_text": source_text,
                "confidence_ppm": 990_000,
                "fragments": [
                    {
                        "page_number": 1,
                        "block_id": unit_id,
                        "source_char_start": 0,
                        "source_char_end": len(source_text),
                    }
                ],
            }
        ],
    }
    if include_reference_page and include_reference_heading:
        units["units"].append(  # type: ignore[union-attr]
            {
                "id": reference_heading_id,
                "role": "heading",
                "reading_order": 1,
                "source_text": reference_heading_text,
                "confidence_ppm": 990_000,
                "fragments": [
                    {
                        "page_number": 2,
                        "block_id": reference_heading_id,
                        "source_char_start": 0,
                        "source_char_end": len(reference_heading_text),
                    }
                ],
            }
        )
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": unit_id,
                "chinese_text": chinese_text,
                "spans": [],
                "terminology": [],
            }
        ],
    }
    if include_reference_page and include_reference_heading:
        translation["units"].append(  # type: ignore[union-attr]
            {
                "unit_id": reference_heading_id,
                "chinese_text": reference_heading_chinese_text,
                "spans": [],
                "terminology": [],
            }
        )
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": [
            unit_id,
            *(
                [reference_heading_id]
                if include_reference_page and include_reference_heading
                else []
            ),
        ],
        "issues": [],
        "final_status": "passed",
    }
    style = build_style_contract((FontSizeSample(10_000, 100),))
    resolver = FontRunResolver(load_font_registry())
    mandatory: list[object] = []
    if include_red:
        mandatory.append(SelectedRed("core-result", unit_id, 0, 2, 400))
    if include_ambiguity:
        ambiguity_start = next(
            (
                index
                for index, character in enumerate(chinese_text)
                if index > 0 and category(character)[0] not in {"C", "P", "Z"}
            ),
            0,
        )
        ambiguity_end = ambiguity_start + 1
        key = make_ambiguity_key(
            english_expression="Key scientific term",
            syntactic_structure="attributive scientific noun phrase",
            candidate_meanings=("measurement label", "mechanistic construct"),
            disciplinary_context="experimental materials analysis",
            ambiguity_reason="The sentence cannot distinguish label from mechanism.",
        )
        review["issues"] = [
            {
                "id": "ambiguity-1",
                "unit_id": unit_id,
                "severity": "unresolved_ambiguity",
                "status": "unresolved",
                "message": "The technical meaning remains unresolved.",
                "ambiguity_key": key,
            }
        ]
        mandatory.extend(
            build_ambiguity_marks(
                units,
                translation,
                review,
                (
                    AmbiguityOccurrence(
                        str(key["id"]),
                        unit_id,
                        ambiguity_start,
                        ambiguity_end,
                    ),
                ),
                style,
            )
        )
    teaching = ()
    if include_auxiliary:
        teaching = (
            TeachingCandidate(
                key="key-scientific-term",
                english_original="Key",
                chinese_meaning="关键术语",
                occurrences=(TeachingOccurrence(unit_id, 0, 3, 0, 1),),
                value_priority=500,
            ),
        )
    selected = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=teaching,
        mandatory_items=tuple(mandatory),
        auxiliary_size_mpt=style.style_for("auxiliary").size_mpt,
        trial_layout=lambda _items: LayoutTrialResult(True, 0),
    )
    annotations = selected.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    frame_graph = build_final_annotated_frame_graph(
        source,
        units,
        translation,
        review,
        annotations,
        style_contract=style,
        resolver=resolver,
        expected_candidate_set_hash=annotations["candidate_set_hash"],
    )
    layout = solve_layout(frame_graph)
    return source, units, translation, review, annotations, frame_graph, layout


def test_exact_draw_runs_preserve_font_and_paint_boundaries() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )

    plan = build_overlay_plan(source, graph, layout, annotations)
    page = plan["pages"][0]
    unit_runs = [run for run in page["draw_runs"] if run["content_kind"] == "unit"]
    auxiliary_runs = [
        run for run in page["draw_runs"] if run["content_kind"] == "auxiliary"
    ]

    assert "".join(run["text"] for run in unit_runs) == "".join(
        line["text"]
        for block in layout["pages"][0]["blocks"]
        if block["content_kind"] == "unit"
        for line in block["lines"]
    )
    overlap = next(
        run for run in unit_runs if run["target_start"] <= 1 < run["target_end"]
    )
    assert overlap["color_token"] == "dark_red"
    assert overlap["color_hex"] == "#7F1D1D"
    assert any(
        item["target_start"] <= 1 < item["target_end"]
        and item["color_hex"] == "#D00000"
        for item in page["underlines"]
    )
    labels = [run for run in unit_runs if run["target_start"] == run["target_end"]]
    assert labels
    assert {run["color_token"] for run in labels} == {"bright_red"}
    assert {run["color_token"] for run in auxiliary_runs} == {"dark_orange"}
    assert any(run["font_role"] == "symbols" for run in unit_runs)
    assert all(
        run["draw_run_hash"]
        == sha256_canonical(
            {key: value for key, value in run.items() if key != "draw_run_hash"}
        )
        for run in page["draw_runs"]
    )


def test_hard_newline_whitespace_and_label_only_lines_keep_complete_mapping() -> None:
    fixture = build_render_fixture(
        chinese_text="  甲\n乙  ",
        narrow=True,
        include_red=False,
        include_auxiliary=False,
    )
    source, _units, _translation, _review, annotations, graph, layout = fixture

    plan = build_overlay_plan(source, graph, layout, annotations)
    flow = graph["unit_flows"][0]
    bindings = plan["pages"][0]["line_bindings"]

    assert bindings[0]["composite_start"] == 0
    assert bindings[-1]["composite_end"] == flow["composite_length"]
    assert all(
        left["composite_end"] == right["composite_start"]
        for left, right in zip(bindings, bindings[1:], strict=False)
    )
    assert any(binding["synthetic_annotation_ids"] for binding in bindings)
    assert any(
        run["target_start"] == run["target_end"]
        for run in plan["pages"][0]["draw_runs"]
    )


def test_plan_is_deterministic_and_draw_consumer_uses_only_frozen_runs() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    first = build_overlay_plan(source, graph, layout, annotations)
    second = build_overlay_plan(source, graph, layout, annotations)
    assert first == second
    assert first["overlay_plan_hash"] == sha256_canonical(
        {key: value for key, value in first.items() if key != "overlay_plan_hash"}
    )

    class RecordingCanvas:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def setFillColor(self, color: str) -> None:  # noqa: N802
            self.calls.append(("fill", color))

        def setStrokeColor(self, color: str) -> None:  # noqa: N802
            self.calls.append(("stroke", color))

        def setLineWidth(self, width: float) -> None:  # noqa: N802
            self.calls.append(("width", width))

        def setFont(self, name: str, size: float) -> None:  # noqa: N802
            self.calls.append(("font", name, size))

        def drawString(self, x: float, y: float, text: str) -> None:  # noqa: N802
            self.calls.append(("text", x, y, text))

        def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
            self.calls.append(("line", x1, y1, x2, y2))

    canvas = RecordingCanvas()
    _draw_validated_page(canvas, first["pages"][0])

    assert [call[3] for call in canvas.calls if call[0] == "text"] == [
        run["text"] for run in first["pages"][0]["draw_runs"]
    ]


def test_draw_consumer_rejects_rehashed_geometry_tamper_before_canvas() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    page = deepcopy(build_overlay_plan(source, graph, layout, annotations)["pages"][0])
    page["draw_runs"][0]["x_mpt"] += 1
    run = page["draw_runs"][0]
    run["draw_run_hash"] = sha256_canonical(
        {key: value for key, value in run.items() if key != "draw_run_hash"}
    )
    page["page_plan_hash"] = sha256_canonical(
        {key: value for key, value in page.items() if key != "page_plan_hash"}
    )

    class EmptyCanvas:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def __getattr__(self, _name: str):
            def record(*values: object) -> None:
                self.calls.append(values)

            return record

    canvas = EmptyCanvas()
    with pytest.raises(OverlayPlanError) as caught:
        _draw_validated_page(canvas, page)
    assert caught.value.code == "PLAN_TAMPERED"
    assert canvas.calls == []


def test_parent_source_and_complexity_tamper_fail_closed() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    tampered_annotations = deepcopy(annotations)
    tampered_annotations["items"][0]["priority"] += 1
    with pytest.raises(OverlayPlanError) as annotation_error:
        build_overlay_plan(source, graph, layout, tampered_annotations)
    assert annotation_error.value.code == "ANNOTATION_PARENT_MISMATCH"

    tampered_source = deepcopy(source)
    tampered_source["pages"][0]["blocks"][0]["first_line_bbox_mpt"][0] += 1
    with pytest.raises(OverlayPlanError) as source_error:
        build_overlay_plan(tampered_source, graph, layout, annotations)
    assert source_error.value.code == "SOURCE_ANCHOR_MISMATCH"

    with pytest.raises(OverlayPlanError) as complexity_error:
        build_overlay_plan(
            source,
            graph,
            layout,
            annotations,
            limits=replace(OverlayPlanLimits(), max_draw_runs=1),
        )
    assert complexity_error.value.code == "PLAN_COMPLEXITY_LIMIT"


def test_annotation_projection_and_source_replacement_are_bound() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    tampered_graph = deepcopy(graph)
    tampered_layout = deepcopy(layout)
    tampered_graph["unit_flows"][0]["styled_spans"][0]["annotation_id"] = (
        "annotation-projection-tamper"
    )
    tampered_layout["frame_graph_hash"] = sha256_canonical(tampered_graph)
    tampered_layout["solver_input_hash"] = sha256_canonical(
        {
            "solver_contract_version": "1.0.0",
            "frame_graph_input_hash": tampered_layout["frame_graph_input_hash"],
            "frame_graph_hash": tampered_layout["frame_graph_hash"],
            "flow_spacing": tampered_layout["flow_spacing"],
            "solver_policy": tampered_layout["solver_policy"],
        }
    )
    with pytest.raises(OverlayPlanError) as caught:
        build_overlay_plan(
            source,
            tampered_graph,
            tampered_layout,
            annotations,
        )
    assert caught.value.code == "ANNOTATION_PROJECTION_MISMATCH"

    original = build_overlay_plan(source, graph, layout, annotations)
    replacement = deepcopy(source)
    replacement["pages"][0]["graphic_nodes"].append(
        {
            "id": "unrelated-graphic",
            "kind": "figure",
            "bbox_mpt": [10_000, 10_000, 20_000, 20_000],
            "confidence_ppm": 990_000,
        }
    )
    replaced = build_overlay_plan(replacement, graph, layout, annotations)
    assert replaced["source_hash"] != original["source_hash"]
    assert (
        replaced["pages"][0]["source_obstacles_hash"]
        != (original["pages"][0]["source_obstacles_hash"])
    )


def test_continuation_object_is_exact_and_legacy_boolean_fails() -> None:
    fixture = build_render_fixture(
        chinese_text="甲。" * 1_500,
        include_red=False,
        include_ambiguity=False,
        include_auxiliary=False,
    )
    source, _units, _translation, _review, annotations, graph, layout = fixture
    continuation_pages = [
        page for page in layout["pages"] if page["page_kind"] == "continuation"
    ]
    assert continuation_pages
    assert all(
        isinstance(page["continuation_label"], dict) for page in continuation_pages
    )

    header = FrozenContinuationHeader.from_mapping(graph["continuation_header"])
    plan = build_overlay_plan(source, graph, layout, annotations)
    assert plan["continuation_header_hash"] == header.header_hash
    for layout_page in continuation_pages:
        page_plan = next(
            page
            for page in plan["pages"]
            if page["page_number"] == layout_page["page_number"]
        )
        label = FrozenContinuationLabel.from_mapping(layout_page["continuation_label"])
        assert page_plan["continuation_label"] == label.to_mapping()
        label_runs = [
            run
            for run in page_plan["draw_runs"]
            if run["content_kind"] == "continuation-label"
        ]
        assert label_runs
        assert "".join(run["text"] for run in label_runs) == label.text
        assert {run["color_token"] for run in label_runs} == {"muted_gray"}
        assert {run["color_hex"] for run in label_runs} == {"#666666"}
        assert label_runs[0]["x_mpt"] == label.x_mpt
        assert all(run["baseline_y_mpt"] == label.baseline_y_mpt for run in label_runs)

    legacy_layout = deepcopy(layout)
    legacy_layout["pages"][1]["continuation_label"] = True
    with pytest.raises(OverlayPlanError) as caught:
        build_overlay_plan(source, graph, legacy_layout, annotations)
    assert caught.value.code == "CONTINUATION_LABEL_NOT_FROZEN"

    style = {
        "style_id": "typography-v1:auxiliary:body:8600:14620",
        "semantic_role": "auxiliary",
        "font_role": "body",
        "size_mpt": 8_600,
        "line_height_mpt": 14_620,
    }
    runs = [
        {
            "run_index": 0,
            "text": "译文续页",
            "font_role": "body",
            "font_name": graph["font_fingerprint"][0]["reportlab_name"],
            "x_offset_mpt": 0,
            "width_mpt": 34_400,
        }
    ]
    raw_header = {
        "contract_version": "1.0.0",
        "text": "译文续页",
        "style": style,
        "runs": runs,
        "width_mpt": 34_400,
        "line_height_mpt": 14_620,
        "ascent_mpt": 7_568,
        "descent_mpt": -1_032,
        "top_inset_mpt": 4_000,
        "gap_after_mpt": 3_000,
        "reserve_height_mpt": 21_620,
        "color_token": "muted_gray",
        "color_hex": "#666666",
        "horizontal_alignment": "right",
    }
    raw_header["header_hash"] = sha256_canonical(raw_header)
    assert FrozenContinuationHeader.from_mapping(raw_header).to_mapping() == raw_header
    bool_tamper = deepcopy(raw_header)
    bool_tamper["runs"][0]["run_index"] = False
    bool_tamper["header_hash"] = sha256_canonical(
        {key: value for key, value in bool_tamper.items() if key != "header_hash"}
    )
    with pytest.raises(OverlayPlanError) as bool_error:
        FrozenContinuationHeader.from_mapping(bool_tamper)
    assert bool_error.value.code == "CONTINUATION_HEADER_INVALID"

    raw = {
        "contract_version": "1.0.0",
        "header_hash": raw_header["header_hash"],
        "source_page_number": 1,
        "continuation_index": 1,
        "text": "译文续页",
        "style": style,
        "runs": runs,
        "width_mpt": 34_400,
        "line_height_mpt": 14_620,
        "ascent_mpt": 7_568,
        "descent_mpt": -1_032,
        "color_token": "muted_gray",
        "color_hex": "#666666",
        "x_mpt": 1_152_151,
        "baseline_y_mpt": 830_322,
        "bbox_mpt": [1_152_151, 829_290, 1_186_551, 837_890],
    }
    raw["label_hash"] = sha256_canonical(raw)
    label = FrozenContinuationLabel.from_mapping(raw)
    assert label.to_mapping() == raw
