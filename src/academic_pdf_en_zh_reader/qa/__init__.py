# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed mechanical QA for frozen bilingual PDF candidates."""

from academic_pdf_en_zh_reader.qa.api import (
    FIXED_GATE_IDS,
    QaConfig,
    run_mechanical_qa,
)
from academic_pdf_en_zh_reader.qa.persist import (
    QaCommitError,
    QaCommitResult,
    validate_and_persist_qa,
)

__all__ = [
    "FIXED_GATE_IDS",
    "QaCommitError",
    "QaCommitResult",
    "QaConfig",
    "run_mechanical_qa",
    "validate_and_persist_qa",
]
