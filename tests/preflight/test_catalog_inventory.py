# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    TextStringObject,
)

from academic_pdf_en_zh_reader.preflight.checks import preflight_safe_copy
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"


def _fixture(tmp_path: Path, fixture_id: str) -> Path:
    output = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", output)
    return output


def test_javascript_and_open_action_are_inventory_warnings_not_hard_failures(
    tmp_path: Path,
) -> None:
    result = preflight_safe_copy(_fixture(tmp_path, "active-content"))

    assert result["passed"] is True
    assert result["inventory"]["javascript"] >= 1
    assert result["inventory"]["open_actions"] == 1
    assert {warning["code"] for warning in result["warnings"]} >= {
        "ACTIVE_JAVASCRIPT_PRESENT",
        "OPEN_ACTION_PRESENT",
    }
    assert all(check["passed"] for check in result["checks"] if check["hard_gate"])


def test_catalog_inventory_is_context_aware_and_counts_supported_categories(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path, "single-column")
    reader = PdfReader(source, strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    root = writer.root_object

    root[NameObject("/MetadataLikeText")] = TextStringObject(
        "The words /JavaScript and /Launch are inert here."
    )
    root[NameObject("/OpenAction")] = DictionaryObject(
        {
            NameObject("/S"): NameObject("/Launch"),
            NameObject("/F"): TextStringObject("never-executed.exe"),
        }
    )
    root[NameObject("/AcroForm")] = DictionaryObject(
        {
            NameObject("/Fields"): ArrayObject(
                [DictionaryObject({NameObject("/FT"): NameObject("/Tx")})]
            )
        }
    )
    root[NameObject("/Names")] = DictionaryObject(
        {
            NameObject("/EmbeddedFiles"): DictionaryObject(
                {
                    NameObject("/Names"): ArrayObject(
                        [
                            TextStringObject("note.txt"),
                            DictionaryObject(
                                {NameObject("/Type"): NameObject("/Filespec")}
                            ),
                        ]
                    )
                }
            )
        }
    )
    writer.pages[0][NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/RichMedia"),
                    NameObject("/A"): DictionaryObject(
                        {NameObject("/S"): NameObject("/SubmitForm")}
                    ),
                }
            )
        ]
    )
    output = tmp_path / "catalog-actions.pdf"
    with output.open("wb") as stream:
        writer.write(stream)

    result = preflight_safe_copy(output)

    assert result["passed"] is True
    assert result["inventory"] == {
        "javascript": 0,
        "open_actions": 1,
        "attachments": 1,
        "forms": 1,
        "launch_actions": 1,
        "rich_media": 1,
        "submit_actions": 1,
    }
    assert {warning["code"] for warning in result["warnings"]} == {
        "ATTACHMENT_PRESENT",
        "FORM_PRESENT",
        "LAUNCH_ACTION_PRESENT",
        "OPEN_ACTION_PRESENT",
        "RICH_MEDIA_PRESENT",
        "SUBMIT_ACTION_PRESENT",
    }
