# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.qa.api import FIXED_GATE_IDS, run_mechanical_qa
from academic_pdf_en_zh_reader.qa.semantic import SemanticQaError, validate_semantics
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


def test_complete_fixed_gate_set_passes_and_validates_schema(
    composed_qa_fixture: dict[str, object],
) -> None:
    qa = run_mechanical_qa(**composed_qa_fixture)

    validate_artifact("qa", qa)
    assert qa["passed"] is True, [
        check["details"] for check in qa["checks"] if not check["passed"]
    ]
    assert tuple(check["id"] for check in qa["checks"]) == FIXED_GATE_IDS
    assert all(check["hard_gate"] is True for check in qa["checks"])
    assert "甲" not in str(qa)
    assert "Key scientific term" not in str(qa)


def test_rotated_and_nonzero_crop_sources_pass_the_same_fixed_gates(
    composed_qa_source_geometry_fixture: dict[str, object],
) -> None:
    qa = run_mechanical_qa(**composed_qa_source_geometry_fixture)

    assert qa["passed"] is True, [
        check["details"] for check in qa["checks"] if not check["passed"]
    ]
    assert qa["checked_page_count"] == qa["rasterized_page_count"]


def test_translation_must_cover_every_unit_once_in_order(
    composed_qa_fixture: dict[str, object],
) -> None:
    translation = deepcopy(composed_qa_fixture["translation"])
    translation["units"].append(deepcopy(translation["units"][0]))

    with pytest.raises(SemanticQaError, match="SEMANTIC_TRANSLATION_INVALID"):
        validate_semantics(
            composed_qa_fixture["units"],
            translation,
            composed_qa_fixture["review"],
        )


def test_same_translator_and_reviewer_never_passes_as_independent(
    composed_qa_fixture: dict[str, object],
) -> None:
    review = deepcopy(composed_qa_fixture["review"])
    review["reviewer_id"] = review["translator_id"]

    with pytest.raises(SemanticQaError, match="SEMANTIC_REVIEW_INVALID"):
        validate_semantics(
            composed_qa_fixture["units"],
            composed_qa_fixture["translation"],
            review,
        )


def test_rehashed_parent_swap_is_rejected_before_dependent_gates(
    composed_qa_fixture: dict[str, object],
) -> None:
    manifest = deepcopy(composed_qa_fixture["render_manifest"])
    manifest["output_pdf_sha256"] = "0" * 64
    inputs = dict(composed_qa_fixture)
    inputs["render_manifest"] = manifest

    qa = run_mechanical_qa(**inputs)

    assert qa["passed"] is False
    assert qa["checks"][0] == {
        "id": "parent.chain",
        "category": "security",
        "hard_gate": True,
        "passed": False,
        "details": "PARENT_MANIFEST_TRUST_MISMATCH",
    }
    assert all(
        check["details"] == "PARENT_CHAIN_REQUIRED" for check in qa["checks"][1:]
    )
