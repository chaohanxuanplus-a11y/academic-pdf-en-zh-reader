# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.annotations.input_manifest import (
    SemanticCandidateManifestError,
    adapt_semantic_candidate_manifest,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.orchestration.agent_packets import assemble, make_packets
from academic_pdf_en_zh_reader.typography.style_contract import build_style_contract
from tests.annotations.conftest import make_bundle


def inputs():
    units, translation, review = make_bundle(
        [
            ("body", "Porosity increased with time.", "孔隙率随时间上升。"),
            (
                "figure-caption",
                "Figure 1. Porosity over time.",
                "图1：孔隙率随时间变化。",
            ),
        ]
    )
    review = {
        k: v
        for k, v in review.items()
        if k
        not in {"schema_version", "artifact_kind", "translation_hash", "translator_id"}
    }
    review.update(reviewer_role="targeted", reviewer_id="translator-agent")
    rows = [
        {"unit_id": u["unit_id"], "chinese_text": u["chinese_text"]}
        for u in translation["units"]
    ]
    rows[0]["terms"] = [
        {
            "english": "Porosity",
            "target": "孔隙率",
            "explanation": "孔隙所占体积分数，反映材料内部空隙程度",
        }
    ]
    rows[1]["figure_notes"] = [
        {
            "content": "图中比较各时间点孔隙率，对应正文报告的上升趋势。具体刻度未辨明，不能补写数值。",  # noqa: E501
            "core": "孔隙率随时间上升；具体刻度不可辨。",
            "evidence": [
                {
                    "unit_id": units["units"][0]["id"],
                    "quote": units["units"][0]["source_text"],
                }
            ],
        }
    ]
    return units, {"units_hash": sha256_canonical(units), "units": rows}, review


def test_assemble_calculates_quotes_hashes_and_preserves_actual_review():
    units, draft, review = inputs()
    result = assemble(
        units, [draft], translator_id="translator-agent", review_record=review
    )
    adapt_semantic_candidate_manifest(
        units,
        result["translation"],
        result["review"],
        result["semantic-candidates"],
        style_contract=build_style_contract(()),
    )
    note = result["semantic-candidates"]["teaching_candidates"][0]
    assert note["occurrences"][0]["source_end"] == 8
    assert result["review"]["reviewer_id"] == "translator-agent"
    assert (
        result["semantic-candidates"]["figure_candidates"][0]["evidence"][0]["quote"]
        == "Porosity increased with time."
    )


@pytest.mark.parametrize("problem", ["missing", "duplicate", "stale", "wrong-quote"])
def test_assembly_rejects_incomplete_or_stale_content(problem):
    units, draft, review = inputs()
    if problem == "missing":
        draft["units"].pop()
    if problem == "duplicate":
        draft["units"].append(deepcopy(draft["units"][0]))
    if problem == "stale":
        draft["units_hash"] = "f" * 64
    if problem == "wrong-quote":
        draft["units"][0]["terms"][0]["english"] = "invented"
    with pytest.raises(ValueError):
        assemble(units, [draft], translator_id="translator-agent", review_record=review)


def test_packets_preserve_paragraphs_once_and_reuse_other_drafts():
    units, _, _ = make_bundle(
        [("body", "Long paragraph. " * 100, "长段落。") for _ in range(3)]
    )
    packets = make_packets(units, 1000)["packets"]
    assert len(packets) == 3
    assert [u["unit_id"] for p in packets for u in p["units"]] == [
        u["id"] for u in units["units"]
    ]
    assert packets[1]["context"]["previous"] == packets[0]["units"][-1]


@pytest.mark.parametrize(
    "field,code",
    [
        ("teaching_candidates", "CORE_VOCABULARY_MISSING"),
        ("figure_candidates", "CORE_FIGURE_READING_MISSING"),
    ],
)
def test_core_supplements_cannot_be_empty(field, code):
    units, draft, review = inputs()
    result = assemble(
        units, [draft], translator_id="translator-agent", review_record=review
    )
    result["semantic-candidates"][field] = []
    with pytest.raises(SemanticCandidateManifestError) as error:
        adapt_semantic_candidate_manifest(
            units,
            result["translation"],
            result["review"],
            result["semantic-candidates"],
            style_contract=build_style_contract(()),
        )
    assert error.value.code == code
