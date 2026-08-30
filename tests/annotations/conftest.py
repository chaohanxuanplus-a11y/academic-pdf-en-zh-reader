# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.review.review_validation import make_ambiguity_key


def make_bundle(
    rows: Sequence[tuple[str, str, str]],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    units_rows: list[dict[str, object]] = []
    translation_rows: list[dict[str, object]] = []
    for reading_order, (role, source_text, chinese_text) in enumerate(rows):
        unit_id = f"u-{reading_order}-{role}"
        units_rows.append(
            {
                "id": unit_id,
                "role": role,
                "reading_order": reading_order,
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
        )
        translation_rows.append(
            {
                "unit_id": unit_id,
                "chinese_text": chinese_text,
                "spans": [],
                "terminology": [],
            }
        )
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "a" * 64,
        "normalized_pdf_sha256": "b" * 64,
        "units": units_rows,
    }
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "translator-agent",
        "translation_revision": 1,
        "units": translation_rows,
    }
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "translator-agent",
        "reviewer_id": "reviewer-agent",
        "reviewed_unit_ids": [row["unit_id"] for row in translation_rows],
        "issues": [],
        "final_status": "passed",
    }
    return units, translation, review


@pytest.fixture
def ambiguity_key() -> dict[str, object]:
    return make_ambiguity_key(
        english_expression="associated with",
        syntactic_structure="past participle predicate with preposition",
        candidate_meanings=("statistically related", "mechanistically connected"),
        disciplinary_context="observational biomedical cohort",
        ambiguity_reason=(
            "The local evidence does not distinguish association from mechanism."
        ),
    )
