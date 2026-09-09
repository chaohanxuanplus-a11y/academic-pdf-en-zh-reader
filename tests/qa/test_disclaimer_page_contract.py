# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import TextStringObject

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.qa.api import run_mechanical_qa
from academic_pdf_en_zh_reader.qa.fonts import FontQaError, validate_draw_run_fonts
from academic_pdf_en_zh_reader.qa.geometry import (
    GeometryQaError,
    validate_a3_pages,
    validate_bounds_and_overlap,
    validate_source_left_one_to_one,
)
from academic_pdf_en_zh_reader.qa.page_contract import source_plan_pages
from academic_pdf_en_zh_reader.rendering.metadata import render_input_hash_payload
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)


def _source_and_manifest() -> tuple[dict, dict]:
    box = [0, 0, A4_WIDTH_MPT, A4_HEIGHT_MPT]
    source = {"pages": [{"page_number": 1, "crop_box_mpt": box}]}
    manifest = {
        "pages": [
            {
                "output_page_number": 1,
                "source_page_number": 1,
                "page_kind": "native",
                "source_crop_box_mpt": box,
                "source_normalized_visible_box_mpt": box,
                "source_transform_mpt": [1000, 0, 0, 1000, 0, 0],
            },
            {
                "output_page_number": 2,
                "source_page_number": None,
                "page_kind": "disclaimer",
                "continuation_index": 0,
                "source_crop_box_mpt": None,
                "source_normalized_visible_box_mpt": None,
                "source_rotation_degrees": None,
                "source_transform_mpt": None,
                "continuation_label_present": False,
            },
        ]
    }
    return source, manifest


def test_disclaimer_is_not_counted_as_a_source_placement() -> None:
    source, manifest = _source_and_manifest()

    assert validate_source_left_one_to_one(source, manifest) == {
        "source_placement_count": 1
    }


@pytest.mark.parametrize("mutation", ("not-final", "duplicate", "fake-source"))
def test_disclaimer_cannot_hide_or_impersonate_source_pages(mutation: str) -> None:
    source, manifest = _source_and_manifest()
    if mutation == "not-final":
        manifest["pages"].reverse()
    elif mutation == "duplicate":
        manifest["pages"].append(deepcopy(manifest["pages"][-1]))
    else:
        manifest["pages"][-1]["source_page_number"] = 1

    with pytest.raises(GeometryQaError, match="GEOMETRY_SOURCE_PLACEMENT_INVALID"):
        validate_source_left_one_to_one(source, manifest)


def test_disclaimer_does_not_exempt_original_page_geometry() -> None:
    source, manifest = _source_and_manifest()
    manifest["pages"][0]["source_transform_mpt"][0] = 999

    with pytest.raises(GeometryQaError, match="GEOMETRY_SOURCE_PLACEMENT_INVALID"):
        validate_source_left_one_to_one(source, manifest)


def test_real_disclaimer_is_appended_and_keeps_source_contracts(
    composed_qa_fixture: dict[str, object],
) -> None:
    layout = composed_qa_fixture["layout"]
    plan = composed_qa_fixture["overlay_plan"]
    manifest = composed_qa_fixture["render_manifest"]

    assert len(plan["pages"]) == len(layout["pages"]) + 1
    assert plan["pages"][-1]["page_kind"] == "disclaimer"
    assert manifest["pages"][-1]["source_page_number"] is None
    assert source_plan_pages(layout, plan) == plan["pages"][:-1]
    validate_artifact("render-manifest", manifest)
    validate_bounds_and_overlap(layout, plan)

    tampered = deepcopy(plan)
    tampered["pages"][-1]["brand_block"]["bbox_mpt"][0] = 0
    with pytest.raises(GeometryQaError, match="GEOMETRY_BOUNDS_INVALID"):
        validate_bounds_and_overlap(layout, tampered)


@pytest.mark.parametrize(
    "mutation",
    ("missing", "not-final", "duplicate", "fake-source", "leader", "unit-run"),
)
def test_synthetic_plan_cannot_bypass_source_or_disclaimer_contracts(
    mutation: str,
) -> None:
    native = {
        "page_number": 1,
        "source_page_number": 1,
        "page_kind": "native",
        "continuation_index": 0,
        "brand_block": None,
    }
    disclaimer = {
        "page_number": 2,
        "source_page_number": None,
        "page_kind": "disclaimer",
        "continuation_index": 0,
        "continuation_label": None,
        "brand_block": {"brand_block_hash": "a" * 64},
        "source_obstacle_count": 0,
        "source_obstacles_hash": sha256_canonical([]),
        "underlines": [],
        "leader_routes": [],
        "draw_runs": [{"content_kind": "brand"}],
        "line_bindings": [{"content_kind": "brand"}],
    }
    layout = {"pages": [native]}
    plan = {
        "pages": [deepcopy(native), disclaimer],
        "branding": {"appended_page_number": 2},
    }
    assert source_plan_pages(layout, plan) == [native]
    if mutation == "missing":
        plan["pages"].pop()
    elif mutation == "not-final":
        plan["pages"].reverse()
    elif mutation == "duplicate":
        plan["pages"].append(deepcopy(disclaimer))
    elif mutation == "fake-source":
        disclaimer["source_page_number"] = 1
    elif mutation == "leader":
        disclaimer["leader_routes"] = [{"unit_id": "invented"}]
    else:
        disclaimer["draw_runs"][0]["content_kind"] = "unit"

    with pytest.raises(ValueError):
        source_plan_pages(layout, plan)


def test_manifest_rejects_source_nulls_and_disclaimer_translation_blocks(
    composed_qa_fixture: dict[str, object],
) -> None:
    original = composed_qa_fixture["render_manifest"]
    tampered = deepcopy(original)
    tampered["pages"][0]["source_page_number"] = None
    with pytest.raises(SchemaValidationError):
        validate_artifact("render-manifest", tampered)

    tampered = deepcopy(original)
    tampered["block_mappings"][0]["output_page_number"] = len(tampered["pages"])
    with pytest.raises(SchemaValidationError, match="duplicated or unbound"):
        validate_artifact("render-manifest", tampered)


def test_real_disclaimer_content_and_page_tampering_is_rejected(
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


def test_rehashed_manifest_cannot_disguise_disclaimer_as_a_source_page(
    composed_qa_fixture: dict[str, object],
) -> None:
    manifest = deepcopy(composed_qa_fixture["render_manifest"])
    native, disclaimer = manifest["pages"][0], manifest["pages"][-1]
    for field in (
        "source_page_number",
        "source_crop_box_mpt",
        "source_normalized_visible_box_mpt",
        "source_rotation_degrees",
        "source_transform_mpt",
    ):
        disclaimer[field] = deepcopy(native[field])
    disclaimer["page_kind"] = "native"
    manifest["render_input_hash"] = sha256_canonical(
        render_input_hash_payload(manifest)
    )
    validate_artifact("render-manifest", manifest)
    inputs = dict(composed_qa_fixture)
    inputs["render_manifest"] = manifest
    inputs["expected_render_manifest_hash"] = sha256_canonical(manifest)

    qa = run_mechanical_qa(**inputs)

    assert qa["passed"] is False
    assert qa["checks"][0]["details"] == "PARENT_PLAN_MISMATCH"
    assert all(
        check["details"] == "PARENT_CHAIN_REQUIRED" for check in qa["checks"][1:]
    )
