# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    create_job,
    state_hash,
)


@pytest.fixture
def reviewed_translation_bundle():
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "a" * 64,
        "normalized_pdf_sha256": "e" * 64,
        "units": [
            {
                "id": "unit-1",
                "role": "body",
                "reading_order": 0,
                "source_text": "Alpha protein binds cells.",
                "confidence_ppm": 990_000,
                "fragments": [
                    {
                        "page_number": 1,
                        "block_id": "block-1",
                        "source_char_start": 0,
                        "source_char_end": 26,
                    }
                ],
            }
        ],
    }
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": [
            {
                "unit_id": "unit-1",
                "chinese_text": "Alpha 蛋白结合细胞。",
                "spans": [],
                "terminology": [],
            }
        ],
    }
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": ["unit-1"],
        "issues": [],
        "final_status": "passed",
    }
    state = create_job(
        job_id="job-skill-test",
        source_sha256=str(units["source_sha256"]),
        translation_revision=1,
    )
    for stage, hashes in (
        (
            JobStage.PREFLIGHTED,
            {
                "preflight": "b" * 64,
                "normalization": "d" * 64,
                "normalized-pdf": "e" * 64,
            },
        ),
        (
            JobStage.EXTRACTED,
            {"source": "c" * 64, "units": sha256_canonical(units)},
        ),
        (JobStage.TRANSLATED, {"translation": sha256_canonical(translation)}),
    ):
        state = advance_job(
            state,
            stage,
            hashes,
            expected_previous_state_hash=state_hash(state),
        )
    return deepcopy(units), deepcopy(translation), deepcopy(review), state
