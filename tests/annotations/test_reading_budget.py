# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
import pytest

from academic_pdf_en_zh_reader.annotations.figure_notes import (
    DirectEvidence,
    FigureNoteCandidate,
)
from academic_pdf_en_zh_reader.annotations.orange_candidates import (
    TeachingCandidate,
    TeachingOccurrence,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialResult,
    select_orange_annotations,
)

from .conftest import make_bundle


def candidates():
    units, translation, _ = make_bundle(
        [
            (
                "body",
                "Key term " + " ".join(f"optional{i}" for i in range(32)),
                "关键术语以及补充内容",
            ),
            ("figure-caption", "Figure 1 rises", "图1上升"),
        ]
    )
    terms = [
        TeachingCandidate(
            "core",
            "Key term",
            "关键术语",
            (TeachingOccurrence("u-0-body", 0, 8, 0, 4),),
            90,
            True,
        )
    ]
    for i in range(32):
        word = f"optional{i}"
        start = units["units"][0]["source_text"].index(word)
        terms.append(
            TeachingCandidate(
                f"optional-{i}",
                word,
                "补充内容",
                (TeachingOccurrence("u-0-body", start, start + len(word), 6, 10),),
                32 - i,
                False,
            )
        )
    figure = FigureNoteCandidate(
        "figure-core",
        "fig1",
        "u-1-figure-caption",
        0,
        4,
        "比较条件与对照后显示主要变量随时间上升。",
        "变量随时间上升。",
        (DirectEvidence("u-1-figure-caption", 0, 14, "Figure 1 rises"),),
        80,
        True,
    )
    return units, translation, terms, figure


@pytest.mark.parametrize("core_overflow", [False, True])
def test_drop_optional_before_allowing_core_overflow_with_logarithmic_trials(
    core_overflow,
):
    units, translation, terms, figure = candidates()
    calls = []

    def trial(request):
        calls.append(request)
        extra = sum(not p.essential for p in request.placements)
        pages = 2 if core_overflow or extra > 5 else 1
        return LayoutTrialResult(True, pages - 1, pages, 1)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=[figure],
        teaching_candidates=terms,
        auxiliary_size_mpt=9000,
        trial_layout=trial,
    )
    assert {p.kind for p in result.placements if p.essential} == {
        "dark-orange-teaching",
        "figure-table-reading",
    }
    assert sum(not p.essential for p in result.placements) == (
        0 if core_overflow else 5
    )
    assert len(calls) <= 10
    assert result.continuation_pages == int(core_overflow)
    if core_overflow:
        assert next(
            p for p in result.placements if p.kind == "figure-table-reading"
        ).compacted
