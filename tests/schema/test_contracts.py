# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.qa.api import _GATES as QA_GATES
from academic_pdf_en_zh_reader.schema.validate import (
    SCHEMA_NAMES,
    SchemaValidationError,
    load_schema,
    validate_artifact,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


@lru_cache(maxsize=1)
def _reading_examples():
    from tests.rendering.test_text_styles import build_render_fixture

    *_, graph, layout = build_render_fixture()
    return graph, layout


def _base(kind: str) -> dict[str, object]:
    return {"schema_version": "1.0.0", "artifact_kind": kind}


def _examples() -> dict[str, dict[str, object]]:
    examples = {
        "normalization": {
            **_base("normalization"),
            "policy_version": "1.0.0",
            "source_sha256": SHA_A,
            "preflight_sha256": SHA_B,
            "normalized_pdf_sha256": SHA_B,
            "normalized_pdf_bytes": 4096,
            "pages": [
                {
                    "page_number": 1,
                    "source_media_box_mpt": [0, 0, 595_276, 841_890],
                    "source_crop_box_mpt": [0, 0, 595_276, 841_890],
                    "source_rotation_degrees": 0,
                    "displayed_width_mpt": 595_276,
                    "displayed_height_mpt": 841_890,
                    "scale_ppm": 1_000_000,
                    "scaled_width_mpt": 595_276,
                    "scaled_height_mpt": 841_890,
                    "padding_left_mpt": 0,
                    "padding_bottom_mpt": 0,
                    "padding_right_mpt": 0,
                    "padding_top_mpt": 0,
                    "normalized_content_box_mpt": [0, 0, 595_276, 841_890],
                }
            ],
        },
        "source": {
            **_base("source"),
            "source_sha256": SHA_A,
            "normalized_pdf_sha256": SHA_B,
            "pages": [
                {
                    "page_number": 1,
                    "media_box_mpt": [0, 0, 595_276, 841_890],
                    "crop_box_mpt": [0, 0, 595_276, 841_890],
                    "rotation_degrees": 0,
                    "bands": [
                        {
                            "id": "p1-band-0",
                            "y_top_mpt": 800_000,
                            "y_bottom_mpt": 40_000,
                            "columns": [
                                {
                                    "id": "p1-col-0",
                                    "x_left_mpt": 40_000,
                                    "x_right_mpt": 555_276,
                                }
                            ],
                        }
                    ],
                    "graphic_nodes": [],
                    "blocks": [
                        {
                            "id": "p1-r0-body-0-12",
                            "role": "body",
                            "translation_policy": "required",
                            "band_id": "p1-band-0",
                            "column_id": "p1-col-0",
                            "reading_order": 0,
                            "source_char_start": 0,
                            "source_char_end": 12,
                            "text": "Source text.",
                            "bbox_mpt": [40_000, 700_000, 555_276, 730_000],
                            "first_line_bbox_mpt": [
                                40_000,
                                715_000,
                                300_000,
                                730_000,
                            ],
                            "confidence_ppm": 990_000,
                        }
                    ],
                }
            ],
        },
        "units": {
            **_base("units"),
            "source_sha256": SHA_A,
            "normalized_pdf_sha256": SHA_B,
            "units": [
                {
                    "id": "p1-r0-body-0-12",
                    "role": "body",
                    "reading_order": 0,
                    "source_text": "Source text.",
                    "confidence_ppm": 990_000,
                    "fragments": [
                        {
                            "page_number": 1,
                            "block_id": "p1-r0-body-0-12",
                            "source_char_start": 0,
                            "source_char_end": 12,
                        }
                    ],
                }
            ],
        },
        "translation": {
            **_base("translation"),
            "units_hash": SHA_A,
            "translator_id": "translator-agent-1",
            "translation_revision": 1,
            "units": [
                {
                    "unit_id": "p1-r0-body-0-12",
                    "chinese_text": "源文本。",
                    "spans": [
                        {
                            "source_start": 0,
                            "source_end": 6,
                            "target_start": 0,
                            "target_end": 3,
                        }
                    ],
                    "terminology": [],
                }
            ],
        },
        "review": {
            **_base("review"),
            "translation_hash": SHA_A,
            "reviewer_role": "independent",
            "translator_id": "translator-agent-1",
            "reviewer_id": "reviewer-agent-2",
            "reviewed_unit_ids": ["p1-r0-body-0-12"],
            "issues": [],
            "final_status": "passed",
        },
        "semantic-candidates": {
            **_base("semantic-candidates"),
            "units_hash": SHA_A,
            "translation_hash": SHA_A,
            "review_hash": SHA_B,
            "red_candidates": [],
            "ambiguity_occurrences": [],
            "teaching_candidates": [],
            "figure_candidates": [],
        },
        "annotations": {
            **_base("annotations"),
            "units_hash": SHA_A,
            "translation_hash": SHA_A,
            "review_hash": SHA_B,
            "annotations_input_hash": SHA_A,
            "mandatory_items_hash": SHA_A,
            "candidate_set_hash": SHA_A,
            "orange_selection_hash": SHA_A,
            "selection_hash": SHA_A,
            "style_version": 1,
            "colors": {
                "body": "#111111",
                "dark_red": "#7F1D1D",
                "dark_orange": "#A84F08",
                "bright_red": "#D00000",
            },
            "auxiliary_size_mpt": 8_600,
            "highlight_ratio_basis_points": 600,
            "teaching_ratio_basis_points": 300,
            "items": [],
        },
        "frame-graph": deepcopy(_reading_examples()[0]),
        "layout": deepcopy(_reading_examples()[1]),
        "render-manifest": {
            **_base("render-manifest"),
            "render_manifest_version": 2,
            "finalization_receipt_hash": SHA_A,
            "source_sha256": SHA_A,
            "normalized_pdf_sha256": SHA_B,
            "source_artifact_hash": SHA_A,
            "annotations_hash": SHA_A,
            "annotation_selection_hash": SHA_A,
            "frame_graph_hash": SHA_A,
            "layout_hash": SHA_A,
            "render_style": {
                "version": 2,
                "colors": {
                    "body": "#111111",
                    "dark_red": "#7F1D1D",
                    "dark_orange": "#A84F08",
                    "bright_red": "#D00000",
                    "muted_gray": "#666666",
                },
                "underline": {
                    "version": 1,
                    "offset_mpt": -1_000,
                    "thickness_mpt": 500,
                },
            },
            "render_style_hash": SHA_A,
            "font_manifest_hash": SHA_A,
            "font_fingerprint": [
                {
                    "role": role,
                    "reportlab_name": name,
                    "sha256": SHA_B,
                }
                for role, name in (
                    ("body", "ChineseRegular"),
                    ("heading", "ChineseSemibold"),
                    ("symbols", "Symbols"),
                )
            ],
            "font_files": [
                {
                    "role": role,
                    "path": path,
                    "reportlab_name": name,
                    "sha256": SHA_B,
                }
                for role, path, name in (
                    ("body", "assets/fonts/regular.ttf", "ChineseRegular"),
                    ("heading", "assets/fonts/semibold.ttf", "ChineseSemibold"),
                    ("symbols", "assets/fonts/symbols.ttf", "Symbols"),
                )
            ],
            "font_usages": [
                {
                    "font_role": "body",
                    "font_name": "ChineseRegular",
                    "size_mpt": 9_000,
                    "draw_run_count": 1,
                    "character_count": 2,
                }
            ],
            "overlay_plan_hash": SHA_A,
            "overlay_plan_limits": {
                "version": 1,
                "max_pages": 2_000,
                "max_blocks_per_page": 20_000,
                "max_lines": 1_000_000,
                "max_characters": 20_000_000,
                "max_draw_runs": 2_000_000,
            },
            "overlay_plan_limits_hash": SHA_A,
            "overlay_pdf_sha256": SHA_A,
            "render_input_hash": SHA_A,
            "dependencies": {"pypdf": "6.16.2", "reportlab": "5.0.1"},
            "runtime_fingerprint": {
                "python_implementation": "CPython",
                "python_version": "3.12.11",
                "platform": "Windows",
            },
            "metadata_policy": {
                "version": 1,
                "source_document_metadata": "discarded",
                "source_annotations": "stripped",
                "source_active_content": "rejected",
                "creation_date": "D:20000101000000+00'00'",
                "modification_date": "D:20000101000000+00'00'",
            },
            "pages": [
                {
                    "output_page_number": 1,
                    "source_page_number": 1,
                    "page_kind": "native",
                    "continuation_index": 0,
                    "source_crop_box_mpt": [0, 0, 595_276, 841_890],
                    "source_normalized_visible_box_mpt": [
                        0,
                        0,
                        595_276,
                        841_890,
                    ],
                    "source_rotation_degrees": 0,
                    "source_transform_mpt": [1000, 0, 0, 1000, 0, 0],
                    "overlay_page_plan_hash": SHA_A,
                    "overlay_image_fingerprints": [],
                    "continuation_label_present": False,
                }
            ],
            "block_mappings": [],
            "output_pdf_sha256": SHA_A,
        },
        "qa": {
            **_base("qa"),
            "qa_policy_version": 1,
            "render_manifest_hash": SHA_A,
            "finalization_receipt_hash": SHA_A,
            "source_pdf_sha256": SHA_A,
            "normalized_pdf_sha256": SHA_B,
            "output_pdf_sha256": SHA_A,
            "qa_config_hash": SHA_A,
            "checked_page_count": 1,
            "rasterized_page_count": 1,
            "passed": True,
            "checks": [
                {
                    "id": identifier,
                    "category": category,
                    "hard_gate": True,
                    "passed": True,
                    "details": "PASS",
                }
                for identifier, category in QA_GATES
            ],
        },
        "provenance": {
            **_base("provenance"),
            "source_sha256": SHA_A,
            "normalized_pdf_sha256": SHA_B,
            "config_hash": SHA_B,
            "code_version": "0.1.0.dev0",
            "dependency_lock_sha256": SHA_A,
            "font_manifest_hash": SHA_B,
            "runtime_fingerprint": {
                "python": "3.12.11",
                "unicode": "15.1.0",
                "platform": "win32",
            },
            "upstreams": [],
        },
        "correction-suggestions": {
            **_base("correction-suggestions"),
            "read_only": True,
            "suggestions": [
                {
                    "id": "corr-1",
                    "english": "robust",
                    "normalized_english": "robust",
                    "preferred_chinese": "稳健的",
                    "context_hash": SHA_A,
                    "domain": "statistics",
                    "source_part_of_speech": "adjective",
                    "target_grammar_function": "modifier",
                    "semantic_tags": ["method-quality"],
                }
            ],
        },
        "preflight": {
            **_base("preflight"),
            "source_sha256": SHA_A,
            "passed": True,
            "checks": [{"id": "page-tree", "hard_gate": True, "passed": True}],
            "pages": [
                {
                    "page_number": 1,
                    "width_mpt": 595_276,
                    "height_mpt": 841_890,
                    "rotation_degrees": 0,
                    "extractable_character_count": 1200,
                    "topology_confidence_ppm": 990_000,
                }
            ],
        },
        "job-state": {
            **_base("job-state"),
            "job_id": "job-001",
            "source_sha256": SHA_A,
            "translation_revision": 1,
            "stage": "initialized",
            "reused_from_job_id": None,
            "history": [
                {
                    "stage": "initialized",
                    "previous_state_hash": None,
                    "artifact_hashes": {},
                    "reused": False,
                }
            ],
        },
        "finalization-receipt": {
            **_base("finalization-receipt"),
            "artifact_hashes": {
                "source": SHA_A,
                "units": SHA_A,
                "translation": SHA_A,
                "review": SHA_A,
                "annotations": SHA_A,
                "frame-graph": SHA_A,
                "layout": SHA_A,
            },
            "policy_hashes": {
                "style-contract": SHA_A,
                "font-fingerprint": SHA_A,
                "frame-graph-config": SHA_A,
                "layout-limits": SHA_A,
                "annotation-adapter-limits": SHA_A,
                "orange-selection-policy": SHA_A,
            },
            "candidate_set_hash": SHA_A,
            "selection_hash": SHA_A,
            "solver_input_hash": SHA_A,
            "continuation_page_count": 0,
            "receipt_hash": SHA_A,
        },
    }
    receipt = examples["finalization-receipt"]
    receipt["receipt_hash"] = sha256_canonical(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )
    from academic_pdf_en_zh_reader.rendering.metadata import (
        render_input_hash_payload,
    )

    manifest = examples["render-manifest"]
    manifest["render_style_hash"] = sha256_canonical(manifest["render_style"])
    manifest["overlay_plan_limits_hash"] = sha256_canonical(
        manifest["overlay_plan_limits"]
    )
    manifest["render_input_hash"] = sha256_canonical(
        render_input_hash_payload(manifest)
    )
    return examples


def _iter_refs(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                yield child
            yield from _iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_refs(child)


def test_every_required_contract_is_versioned_and_validates() -> None:
    examples = _examples()
    assert set(examples) == SCHEMA_NAMES

    for name, instance in examples.items():
        schema = load_schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == (
            f"urn:academic-pdf-en-zh-reader:schema:{name}:{instance['schema_version']}"
        )
        assert not [ref for ref in _iter_refs(schema) if not str(ref).startswith("#")]
        validate_artifact(name, instance)


def test_schema_files_are_real_json_and_declared_set_is_exact() -> None:
    schema_dir = (
        Path(__file__).parents[2] / "src" / "academic_pdf_en_zh_reader" / "schema"
    )
    files = {
        path.name.removesuffix(".schema.json")
        for path in schema_dir.glob("*.schema.json")
    }
    assert files == SCHEMA_NAMES
    for path in schema_dir.glob("*.schema.json"):
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


@pytest.mark.parametrize("name", sorted(_examples()))
def test_contracts_fail_closed_on_wrong_version_and_unknown_fields(name: str) -> None:
    wrong_version = _examples()[name]
    wrong_version["schema_version"] = "99.0.0"
    with pytest.raises(SchemaValidationError):
        validate_artifact(name, wrong_version)

    unknown_field = _examples()[name]
    unknown_field["not_in_contract"] = True
    with pytest.raises(SchemaValidationError):
        validate_artifact(name, unknown_field)


def test_layout_contract_rejects_legacy_local_layout_input_hash() -> None:
    layout = _examples()["layout"]
    layout["layout_input_hash"] = layout.pop("solver_input_hash")

    with pytest.raises(SchemaValidationError, match="layout_input_hash"):
        validate_artifact("layout", layout)


def test_layout_contract_rejects_the_legacy_continuation_boolean() -> None:
    layout = _examples()["layout"]
    layout["pages"][0]["continuation_label"] = False

    with pytest.raises(SchemaValidationError, match="continuation_label"):
        validate_artifact("layout", layout)


def test_finalization_receipt_rejects_rehashed_internal_tamper() -> None:
    receipt = _examples()["finalization-receipt"]
    receipt["continuation_page_count"] = 1

    with pytest.raises(SchemaValidationError, match="receipt_hash"):
        validate_artifact("finalization-receipt", receipt)


def test_geometry_confidence_and_ratio_units_are_integer_bounded() -> None:
    source = _examples()["source"]
    source["pages"][0]["blocks"][0]["bbox_mpt"][0] = 40_000.5
    with pytest.raises(SchemaValidationError):
        validate_artifact("source", source)

    units = _examples()["units"]
    units["units"][0]["confidence_ppm"] = 1_000_001
    with pytest.raises(SchemaValidationError):
        validate_artifact("units", units)

    annotations = _examples()["annotations"]
    annotations["highlight_ratio_basis_points"] = 1_001
    with pytest.raises(SchemaValidationError):
        validate_artifact("annotations", annotations)

    layout = _examples()["layout"]
    layout["pages"][0]["utilization_basis_points"] = 22.5
    with pytest.raises(SchemaValidationError):
        validate_artifact("layout", layout)


def test_artifact_kind_must_match_selected_contract() -> None:
    source = _examples()["source"]
    source["artifact_kind"] = "units"
    with pytest.raises(SchemaValidationError):
        validate_artifact("source", source)


def test_order_ranges_and_hard_gate_summaries_are_semantically_checked() -> None:
    source = _examples()["source"]
    duplicate_page = dict(source["pages"][0])
    source["pages"].append(duplicate_page)
    with pytest.raises(SchemaValidationError, match="page_number"):
        validate_artifact("source", source)

    units = _examples()["units"]
    units["units"][0]["fragments"][0]["source_char_start"] = 10
    units["units"][0]["fragments"][0]["source_char_end"] = 5
    with pytest.raises(SchemaValidationError, match="character range"):
        validate_artifact("units", units)

    review = _examples()["review"]
    review["issues"] = [
        {
            "id": "issue-1",
            "unit_id": "p1-r0-body-0-12",
            "severity": "hard_error",
            "status": "unresolved",
            "message": "Number changed.",
        }
    ]
    with pytest.raises(SchemaValidationError, match="hard_error"):
        validate_artifact("review", review)

    for name in ("preflight", "qa"):
        artifact = _examples()[name]
        artifact["checks"][0]["passed"] = False
        with pytest.raises(SchemaValidationError, match="hard gate"):
            validate_artifact(name, artifact)


def test_preflight_does_not_claim_topology_assessment_is_mandatory() -> None:
    preflight = _examples()["preflight"]
    del preflight["pages"][0]["topology_confidence_ppm"]
    validate_artifact("preflight", preflight)


def test_job_state_contract_rejects_a_tampered_transition_hash() -> None:
    state = _examples()["job-state"]
    state["stage"] = "preflighted"
    state["history"].append(
        {
            "stage": "preflighted",
            "previous_state_hash": "0" * 64,
            "artifact_hashes": {
                "preflight": SHA_B,
                "normalization": SHA_A,
                "normalized-pdf": SHA_B,
            },
            "reused": False,
        }
    )
    with pytest.raises(SchemaValidationError, match="history"):
        validate_artifact("job-state", state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("displayed_height_mpt", 841_889),
        ("scale_ppm", 999_999),
        ("scaled_width_mpt", 595_275),
        ("padding_left_mpt", 1),
        ("normalized_content_box_mpt", [1, 0, 595_276, 841_890]),
    ],
)
def test_normalization_contract_recomputes_geometry(field: str, value: object) -> None:
    normalization = _examples()["normalization"]
    normalization["pages"][0][field] = value

    with pytest.raises(SchemaValidationError, match="normalization"):
        validate_artifact("normalization", normalization)


def test_normalization_contract_accepts_centered_padding_and_downscale() -> None:
    normalization = _examples()["normalization"]
    page = normalization["pages"][0]
    page.update(
        {
            "source_media_box_mpt": [0, 0, 700_000, 700_000],
            "source_crop_box_mpt": [0, 0, 700_000, 700_000],
            "displayed_width_mpt": 700_000,
            "displayed_height_mpt": 700_000,
            "scale_ppm": 850_394,
            "scaled_width_mpt": 595_276,
            "scaled_height_mpt": 595_276,
            "padding_left_mpt": 0,
            "padding_bottom_mpt": 123_307,
            "padding_right_mpt": 0,
            "padding_top_mpt": 123_307,
            "normalized_content_box_mpt": [0, 123_307, 595_276, 718_583],
        }
    )

    validate_artifact("normalization", normalization)


def test_normalization_contract_pads_a_smaller_page_without_upscaling() -> None:
    normalization = _examples()["normalization"]
    page = normalization["pages"][0]
    page.update(
        {
            "source_media_box_mpt": [0, 0, 595_000, 794_000],
            "source_crop_box_mpt": [0, 0, 595_000, 794_000],
            "displayed_width_mpt": 595_000,
            "displayed_height_mpt": 794_000,
            "scale_ppm": 1_000_000,
            "scaled_width_mpt": 595_000,
            "scaled_height_mpt": 794_000,
            "padding_left_mpt": 138,
            "padding_bottom_mpt": 23_945,
            "padding_right_mpt": 138,
            "padding_top_mpt": 23_945,
            "normalized_content_box_mpt": [138, 23_945, 595_138, 817_945],
        }
    )

    validate_artifact("normalization", normalization)


@pytest.mark.parametrize(
    "name", ["source", "units", "render-manifest", "qa", "provenance"]
)
def test_persisted_source_chain_requires_the_normalized_pdf_hash(name: str) -> None:
    artifact = _examples()[name]
    del artifact["normalized_pdf_sha256"]

    with pytest.raises(SchemaValidationError, match="normalized_pdf_sha256"):
        validate_artifact(name, artifact)


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Windows\Fonts\font.ttf",
        r"C:font.ttf",
        "//server/share/font.ttf",
        r"\\?\C:\font.ttf",
        "/absolute/font.ttf",
        r"assets\fonts\font.ttf",
        "assets//fonts/font.ttf",
        "assets/./fonts/font.ttf",
        "assets/../fonts/font.ttf",
        "assets/fonts/fo\x00nt.ttf",
        "assets/fonts/a\x01b.ttf",
        "assets/fonts/a\x7fb.ttf",
        "NUL",
        "assets/CON/font.ttf",
        "assets/fonts/COM1.ttf",
        "assets/fonts/COM¹.ttf",
        "assets/fonts/COM².ttf",
        "assets/fonts/COM³.ttf",
        "assets/fonts/LPT¹.ttf",
        "assets/fonts/LPT².ttf",
        "assets/fonts/LPT³.ttf",
    ],
)
def test_render_manifest_rejects_noncanonical_or_unsafe_font_paths(path: str) -> None:
    manifest = _examples()["render-manifest"]
    manifest["font_files"][0]["path"] = path
    with pytest.raises(SchemaValidationError):
        validate_artifact("render-manifest", manifest)


@pytest.mark.parametrize(
    "fingerprints",
    (
        "a" * 64,
        ["A" * 64],
        ["a" * 63],
        [1],
    ),
)
def test_render_manifest_rejects_malformed_overlay_image_fingerprints(
    fingerprints: object,
) -> None:
    manifest = _examples()["render-manifest"]
    manifest["pages"][0]["overlay_image_fingerprints"] = fingerprints
    with pytest.raises(SchemaValidationError):
        validate_artifact("render-manifest", manifest)


def test_render_manifest_requires_page_overlay_image_fingerprints() -> None:
    manifest = _examples()["render-manifest"]
    del manifest["pages"][0]["overlay_image_fingerprints"]
    with pytest.raises(SchemaValidationError):
        validate_artifact("render-manifest", manifest)


def test_render_manifest_allows_repeated_overlay_image_fingerprints() -> None:
    from academic_pdf_en_zh_reader.rendering.metadata import (
        render_input_hash_payload,
    )

    manifest = _examples()["render-manifest"]
    manifest["pages"][0]["overlay_image_fingerprints"] = [SHA_A, SHA_A]
    manifest["render_input_hash"] = sha256_canonical(
        render_input_hash_payload(manifest)
    )
    validate_artifact("render-manifest", manifest)


def test_preflight_and_qa_cannot_pass_without_hard_gate_evidence() -> None:
    for name in ("preflight", "qa"):
        no_checks = _examples()[name]
        no_checks["checks"] = []
        with pytest.raises(SchemaValidationError, match="hard gate|non-empty"):
            validate_artifact(name, no_checks)

        soft_only = _examples()[name]
        soft_only["checks"][0]["hard_gate"] = False
        with pytest.raises(SchemaValidationError, match="hard gate"):
            validate_artifact(name, soft_only)

    no_pages = _examples()["preflight"]
    no_pages["pages"] = []
    with pytest.raises(SchemaValidationError, match="page"):
        validate_artifact("preflight", no_pages)


def test_failed_preflight_may_have_no_pages_but_needs_error_evidence() -> None:
    failed = _examples()["preflight"]
    failed["passed"] = False
    failed["pages"] = []
    failed["error_codes"] = ["PAGE_TREE_INVALID"]
    failed["checks"] = [
        {
            "id": "page-tree",
            "hard_gate": True,
            "passed": False,
            "error_code": "PAGE_TREE_INVALID",
        }
    ]
    validate_artifact("preflight", failed)

    missing_error = _examples()["preflight"]
    missing_error["passed"] = False
    missing_error["pages"] = []
    missing_error["checks"][0]["passed"] = False
    with pytest.raises(SchemaValidationError, match="error"):
        validate_artifact("preflight", missing_error)

    unlinked_error = _examples()["preflight"]
    unlinked_error["passed"] = False
    unlinked_error["pages"] = []
    unlinked_error["error_codes"] = ["PAGE_TREE_INVALID"]
    unlinked_error["checks"][0]["passed"] = False
    with pytest.raises(SchemaValidationError, match="error"):
        validate_artifact("preflight", unlinked_error)


def test_preflight_cannot_pass_when_an_observed_limit_is_exceeded() -> None:
    preflight = _examples()["preflight"]
    preflight["limits"] = {"page_count": {"observed": 2, "maximum": 1}}
    with pytest.raises(SchemaValidationError, match="limit"):
        validate_artifact("preflight", preflight)


def test_source_block_id_must_be_derived_from_its_source_position() -> None:
    source = _examples()["source"]
    source["pages"][0]["blocks"][0]["id"] = "random-uuid-like-id"
    with pytest.raises(SchemaValidationError, match="stable source id"):
        validate_artifact("source", source)
