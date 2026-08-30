# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from academic_pdf_en_zh_reader.qa.pdf_structure import (
    PdfStructureQaError,
    validate_no_active_content,
)


@pytest.mark.parametrize(
    "action_name",
    ("/JavaScript", "/Launch", "/SubmitForm"),
)
def test_final_pdf_rejects_active_actions(
    composed_qa_fixture: dict[str, object], action_name: str
) -> None:
    original = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    writer = PdfWriter()
    writer.append_pages_from_reader(original)
    writer.root_object[NameObject("/OpenAction")] = DictionaryObject(
        {
            NameObject("/S"): NameObject(action_name),
            NameObject("/JS"): TextStringObject("blocked"),
        }
    )
    payload = BytesIO()
    writer.write(payload)
    reader = PdfReader(BytesIO(payload.getvalue()), strict=True)

    with pytest.raises(PdfStructureQaError, match="PDF_ACTIVE_CONTENT"):
        validate_no_active_content(reader)


def test_composed_fixture_contains_no_active_content(
    composed_qa_fixture: dict[str, object],
) -> None:
    reader = PdfReader(composed_qa_fixture["output_pdf_path"], strict=True)
    evidence = validate_no_active_content(reader)
    assert evidence["page_count"] == len(reader.pages)
