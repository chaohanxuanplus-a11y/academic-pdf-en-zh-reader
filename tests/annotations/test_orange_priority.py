# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from academic_pdf_en_zh_reader.annotations.red_emphasis import (
    RedCandidate,
    RedImportance,
    select_red_emphasis,
)
from academic_pdf_en_zh_reader.annotations.selection import (
    LayoutTrialRequest,
    LayoutTrialResult,
    select_orange_annotations,
)

from .conftest import make_bundle


def test_every_layout_trial_binds_the_same_frozen_mandatory_items() -> None:
    units, translation, review = make_bundle([("body", "Body", "甲" * 100)])
    red = select_red_emphasis(
        units,
        translation,
        (
            RedCandidate(
                "core",
                "u-0-body",
                0,
                6,
                RedImportance.CORE_CONCLUSION,
            ),
        ),
    )
    requests: list[LayoutTrialRequest] = []

    def trial(request: LayoutTrialRequest) -> LayoutTrialResult:
        requests.append(request)
        return LayoutTrialResult(True, 0, 1, 1)

    result = select_orange_annotations(
        units,
        translation,
        figure_candidates=(),
        teaching_candidates=(),
        mandatory_items=red.items,
        auxiliary_size_mpt=9_000,
        trial_layout=trial,
    )

    assert requests
    assert all(request.mandatory_items == red.items for request in requests)
    assert all(
        request.mandatory_items_hash == result.mandatory_items_hash
        for request in requests
    )
    artifact = result.to_artifact(
        units=units,
        translation=translation,
        review=review,
    )
    assert artifact["mandatory_items_hash"] == result.mandatory_items_hash
