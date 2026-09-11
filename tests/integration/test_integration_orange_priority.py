# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)

from .conftest import build_case


def _candidates(source, units, translation):
    caption = next(unit for unit in units["units"] if unit["role"] == "figure-caption")
    body = next(unit for unit in units["units"] if unit["role"] == "body")
    target_by_id = {
        unit["unit_id"]: unit["chinese_text"] for unit in translation["units"]
    }
    figure_id = next(
        graphic["id"]
        for page in source["pages"]
        for graphic in page["graphic_nodes"]
        if graphic["kind"] == "figure"
    )
    caption_target = target_by_id[caption["id"]]
    figure = FigureNoteCandidate(
        key="synthetic-figure-reading",
        figure_id=figure_id,
        caption_unit_id=caption["id"],
        target_start=0,
        target_end=1,
        content="合成图形仅用于检验图注与图区的对应关系。",
        compact_content="合成图形仅用于排版检验。",
        evidence=(
            DirectEvidence(
                caption["id"],
                0,
                len(caption["source_text"]),
                caption["source_text"],
            ),
        ),
        value_priority=100,
        essential=True,
    )
    assert caption_target[0].strip()
    teaching = TeachingCandidate(
        key="ordinary-orange-must-not-displace-translation",
        english_original=body["source_text"][:3],
        chinese_meaning="普通橙色教学说明" * 1_000,
        occurrences=(TeachingOccurrence(body["id"], 0, 3, 0, 1),),
        value_priority=1_000,
        essential=False,
    )
    core = TeachingCandidate(
        "core-term",
        body["source_text"][4:7],
        "核心词汇",
        (TeachingOccurrence(body["id"], 4, 7, 1, 2),),
        100,
        True,
    )
    return (figure,), (teaching, core)


def test_figure_or_table_orange_precedes_and_survives_ordinary_orange_pressure(
    tmp_path: Path,
) -> None:
    case = build_case(
        tmp_path,
        "figures-and-tables",
        candidate_factory=_candidates,
    )
    items = case.annotations["items"]

    assert {item["kind"] for item in items} == {
        "figure-table-reading",
        "dark-orange-teaching",
    }
    assert all(item["essential"] for item in items)
    assert all(len(item["content"]) < 100 for item in items)
