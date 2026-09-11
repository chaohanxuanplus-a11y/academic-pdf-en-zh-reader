# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Original pages, independent continuation pages, and the final warning."""

from copy import deepcopy
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import TextStringObject

from academic_pdf_en_zh_reader.qa.fonts import FontQaError, validate_draw_run_fonts
from academic_pdf_en_zh_reader.qa.geometry import (
    GeometryQaError,
    validate_a3_pages,
    validate_bounds_and_overlap,
    validate_source_left_one_to_one,
)
from academic_pdf_en_zh_reader.qa.page_contract import source_plan_pages
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)


def _source_and_manifest():
    box = [0, 0, 595276, 841890]
    source = {"pages": [{"page_number": 1, "crop_box_mpt": box}]}
    native = {
        "output_page_number": 1,
        "source_page_number": 1,
        "page_kind": "native",
        "continuation_index": 0,
        "continuation_label_present": False,
        "source_crop_box_mpt": box,
        "source_normalized_visible_box_mpt": box,
        "source_rotation_degrees": 0,
        "source_transform_mpt": [1000, 0, 0, 1000, 0, 0],
    }
    extra = {
        "output_page_number": 2,
        "source_page_number": None,
        "page_kind": "continuation",
        "continuation_index": 1,
        "continuation_label_present": False,
        "source_crop_box_mpt": None,
        "source_normalized_visible_box_mpt": None,
        "source_rotation_degrees": None,
        "source_transform_mpt": None,
    }
    return source, {"pages": [native, extra]}


def test_extra_page_does_not_repeat_an_original():
    source, manifest = _source_and_manifest()
    assert validate_source_left_one_to_one(source, manifest) == {
        "source_placement_count": 1
    }


@pytest.mark.parametrize(
    "mutation", ("reordered", "duplicate", "fake-source", "geometry")
)
def test_extra_page_cannot_hide_or_impersonate_source(mutation):
    source, manifest = _source_and_manifest()
    if mutation == "reordered":
        manifest["pages"].reverse()
    elif mutation == "duplicate":
        manifest["pages"].append(deepcopy(manifest["pages"][-1]))
    elif mutation == "fake-source":
        manifest["pages"][-1]["source_page_number"] = 1
    else:
        manifest["pages"][0]["source_transform_mpt"][0] = 999
    with pytest.raises(GeometryQaError, match="GEOMETRY_SOURCE_PLACEMENT_INVALID"):
        validate_source_left_one_to_one(source, manifest)


def test_warning_is_integrated_once_on_final_physical_page(composed_qa_fixture):
    layout, plan, manifest = [
        composed_qa_fixture[k] for k in ("layout", "overlay_plan", "render_manifest")
    ]
    assert len(plan["pages"]) == len(layout["pages"])
    assert source_plan_pages(layout, plan) == plan["pages"]
    assert [p["page_number"] for p in plan["pages"] if p["brand_block"]] == [
        len(plan["pages"])
    ]
    validate_artifact("render-manifest", manifest)
    validate_bounds_and_overlap(layout, plan)
    tampered = deepcopy(plan)
    tampered["pages"][-1]["brand_block"]["bbox_mpt"][0] = 0
    with pytest.raises(GeometryQaError, match="GEOMETRY_BOUNDS_INVALID"):
        validate_bounds_and_overlap(layout, tampered)


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "fake-source"))
def test_warning_and_page_bindings_cannot_be_bypassed(composed_qa_fixture, mutation):
    layout, plan = [
        deepcopy(composed_qa_fixture[k]) for k in ("layout", "overlay_plan")
    ]
    if mutation == "missing":
        plan["pages"][-1]["brand_block"] = None
    elif mutation == "duplicate":
        plan["pages"].append(deepcopy(plan["pages"][-1]))
    else:
        plan["pages"][0]["source_page_number"] = None
    with pytest.raises(ValueError):
        source_plan_pages(layout, plan)


def test_native_page_cannot_have_missing_source_identity(composed_qa_fixture):
    manifest = deepcopy(composed_qa_fixture["render_manifest"])
    manifest["pages"][0]["source_page_number"] = None
    with pytest.raises(SchemaValidationError):
        validate_artifact("render-manifest", manifest)


def test_final_warning_content_and_page_tampering_is_rejected(
    composed_qa_fixture: dict[str, object],
) -> None:
    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    manifest = composed_qa_fixture["render_manifest"]
    plan = composed_qa_fixture["overlay_plan"]
    for pages in (list(reader.pages)[:-1], [*reader.pages, reader.pages[-1]]):
        writer = PdfWriter()
        for page in pages:
            writer.add_page(page)
        payload = BytesIO()
        writer.write(payload)
        with pytest.raises(GeometryQaError, match="GEOMETRY_A3_INVALID"):
            validate_a3_pages(PdfReader(BytesIO(payload.getvalue())), manifest)

    writer = PdfWriter(clone_from=reader)
    disclaimer = writer.pages[-1]
    contents = disclaimer.get_contents()
    text_operands = [
        operands for operands, operator in contents.operations if operator == b"Tj"
    ]
    assert text_operands
    text_operands[-1][0] = TextStringObject("REPLACED")
    disclaimer.replace_contents(contents)
    payload = BytesIO()
    writer.write(payload)
    with pytest.raises(FontQaError, match="FONT_DRAW_BINDING_INVALID"):
        validate_draw_run_fonts(PdfReader(BytesIO(payload.getvalue())), manifest, plan)
