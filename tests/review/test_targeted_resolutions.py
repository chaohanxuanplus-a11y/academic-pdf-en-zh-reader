# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.review.semantic_checks import (
    check_mechanical_semantics,
    mechanical_issue_hash,
    unresolved_mechanical_issues,
)

from .test_numbers_units_logic import _artifacts


def test_text_bound_resolution_accepts_language_flag_and_rejects_stale_text():
    units, translation = _artifacts("Values increased.", "数值趋于更高水平。")
    issues = check_mechanical_semantics(units, translation)
    issue = next(i for i in issues if i.category == "direction")
    review = {
        "reviewed_unit_ids": ["unit-0"],
        "mechanical_resolutions": [
            {
                "unit_id": "unit-0",
                "issue_hash": mechanical_issue_hash(issue, units, translation),
                "reason": "已对照原句，更高水平表达相同的上升方向。",
            }
        ],
    }
    assert issue not in unresolved_mechanical_issues(units, translation, review)
    changed = deepcopy(translation)
    changed["units"][0]["chinese_text"] = "数值也趋于更高水平。"
    with pytest.raises(ValueError):
        unresolved_mechanical_issues(units, changed, review)


def test_numeric_mismatch_cannot_be_waived_as_language_style():
    units, translation = _artifacts("There were 12 samples.", "共有13个样本。")
    issue = next(
        i
        for i in check_mechanical_semantics(units, translation)
        if i.category == "number"
    )
    review = {
        "reviewed_unit_ids": ["unit-0"],
        "mechanical_resolutions": [
            {
                "unit_id": "unit-0",
                "issue_hash": mechanical_issue_hash(issue, units, translation),
                "reason": "不能用文风理由忽略真实的数字不一致。",
            }
        ],
    }
    with pytest.raises(ValueError):
        unresolved_mechanical_issues(units, translation, review)
