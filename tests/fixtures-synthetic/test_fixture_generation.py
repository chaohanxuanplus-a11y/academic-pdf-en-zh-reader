# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from academic_pdf_en_zh_reader.constants import (
    A4_HEIGHT_PT,
    A4_WIDTH_PT,
    FIXTURE_SCHEMA_VERSION,
)
from scripts.generate_synthetic_fixtures import (
    APPROVED_FONT_PATHS,
    generate_fixture,
    load_fixture_spec,
)

ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = Path(__file__).with_name("specs")
EXPECTED_FIXTURE_IDS = {
    "active-content",
    "cross-column-paragraph",
    "cross-page-paragraph",
    "figures-and-tables",
    "first-page-mixed",
    "long-translation",
    "single-column",
    "three-column",
    "two-column",
}
PAGE_TOLERANCE_PT = 0.001


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _spec_paths() -> list[Path]:
    return sorted(SPECS_DIR.glob("*.json"))


def _embedded_font_names(reader: PdfReader) -> set[str]:
    names: set[str] = set()
    for page in reader.pages:
        resources = page["/Resources"].get_object()
        for font_reference in resources["/Font"].get_object().values():
            font = font_reference.get_object()
            names.add(str(font["/BaseFont"]))
            descriptor_owner = font
            if font["/Subtype"] == "/Type0":
                descriptor_owner = font["/DescendantFonts"][0].get_object()
            descriptor = descriptor_owner["/FontDescriptor"].get_object()
            assert any(
                key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")
            )
    return names


def _assert_truth_inventory(spec: dict[str, object]) -> None:
    pages = spec["pages"]
    truth = spec["truth"]
    assert isinstance(pages, list) and pages
    assert isinstance(truth, dict)

    expected_band_ids: list[str] = []
    expected_column_ids: list[str] = []
    expected_roles: dict[str, str] = {}
    expected_reading_order: list[str] = []
    known_fragments: set[str] = set()

    for page_number, page in enumerate(pages, start=1):
        assert page["page_number"] == page_number
        assert page["size"] == "A4"
        assert page["orientation"] == "portrait"
        for band in page["bands"]:
            expected_band_ids.append(band["id"])
            assert band["column_count"] == len(band["columns"])
            assert band["column_count"] >= 1
            for column in band["columns"]:
                expected_column_ids.append(column["id"])
                for block in column["blocks"]:
                    block_id = block["id"]
                    assert block_id not in expected_roles
                    expected_roles[block_id] = block["role"]
                    expected_reading_order.append(block_id)
                    known_fragments.add(block_id)

    assert truth["band_ids"] == expected_band_ids
    assert truth["column_ids"] == expected_column_ids
    assert truth["roles"] == expected_roles
    assert truth["reading_order"] == expected_reading_order
    for unit in truth["cross_boundary_units"]:
        assert unit["kind"] in {"cross_column", "cross_page"}
        assert len(unit["fragments"]) >= 2
        assert set(unit["fragments"]).issubset(known_fragments)


def test_specs_are_exactly_the_nine_cc0_synthetic_scenarios() -> None:
    paths = _spec_paths()
    assert {path.stem for path in paths} == EXPECTED_FIXTURE_IDS

    for path in paths:
        raw = path.read_text(encoding="utf-8")
        spec = json.loads(raw)
        assert raw.endswith("\n")
        assert spec["schema_version"] == FIXTURE_SCHEMA_VERSION
        assert spec["fixture_id"] == path.stem
        assert spec["license"] == "CC0-1.0"
        assert spec["creator"] == "academic-pdf-en-zh-reader contributors"
        assert spec["source"] == "project-authored synthetic content"
        assert spec["uses_system_fonts"] is False
        assert spec["uses_randomness"] is False
        assert spec["uses_current_time"] is False
        _assert_truth_inventory(spec)


def test_test_data_attribution_declares_every_fixture_cc0() -> None:
    attribution = (ROOT / "TEST_DATA_ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "CC0-1.0" in attribution
    for fixture_id in EXPECTED_FIXTURE_IDS:
        assert f"`{fixture_id}`" in attribution


def test_only_manifest_approved_repository_fonts_are_available_to_generator() -> None:
    manifest = json.loads(
        (ROOT / "assets" / "font-manifest.json").read_text(encoding="utf-8")
    )
    expected = {
        (ROOT / record["path"]).resolve()
        for record in manifest["fonts"]
        if record["role"] in {"body", "heading", "symbols"}
    }
    assert expected == APPROVED_FONT_PATHS
    assert all(
        path.is_relative_to((ROOT / "assets" / "fonts").resolve())
        for path in APPROVED_FONT_PATHS
    )


@pytest.mark.parametrize("spec_path", _spec_paths(), ids=lambda path: path.stem)
def test_generated_pdf_is_a4_portrait_and_contains_canonical_truth(
    spec_path: Path, tmp_path: Path
) -> None:
    spec = load_fixture_spec(spec_path)
    output_path = tmp_path / f"{spec_path.stem}.pdf"

    result = generate_fixture(spec_path, output_path)

    assert result.fixture_id == spec_path.stem
    assert result.page_count == len(spec["pages"])
    assert result.sha256 == hashlib.sha256(output_path.read_bytes()).hexdigest()
    reader = PdfReader(output_path)
    assert len(reader.pages) == len(spec["pages"])
    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        assert width == pytest.approx(A4_WIDTH_PT, abs=PAGE_TOLERANCE_PT)
        assert height == pytest.approx(A4_HEIGHT_PT, abs=PAGE_TOLERANCE_PT)
        assert width < height

    assert reader.metadata["/FixtureID"] == spec["fixture_id"]
    assert json.loads(reader.metadata["/FixtureTruth"]) == spec["truth"]
    assert reader.metadata["/FixtureLicense"] == "CC0-1.0"
    assert reader.metadata["/CreationDate"] == "D:20000101000000+00'00'"
    assert reader.metadata["/ModDate"] == "D:20000101000000+00'00'"
    embedded_font_names = _embedded_font_names(reader)
    assert embedded_font_names
    assert all(
        "NotoSerifSC-Regular" in name or "NotoSerifSC-SemiBold" in name
        for name in embedded_font_names
    )


@pytest.mark.parametrize("spec_path", _spec_paths(), ids=lambda path: path.stem)
def test_generation_is_byte_deterministic(spec_path: Path, tmp_path: Path) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"

    first_result = generate_fixture(spec_path, first)
    second_result = generate_fixture(spec_path, second)

    assert first.read_bytes() == second.read_bytes()
    assert first_result.normalized_structure == second_result.normalized_structure
    assert first_result.sha256 == second_result.sha256


def test_cross_boundary_truth_is_present_only_in_the_expected_fixtures() -> None:
    by_id = {
        path.stem: load_fixture_spec(path)["truth"]["cross_boundary_units"]
        for path in _spec_paths()
    }
    assert by_id["cross-column-paragraph"] == [
        {
            "unit_id": "unit-cross-column",
            "kind": "cross_column",
            "fragments": ["p1-c1-body-2a", "p1-c2-body-2b"],
        }
    ]
    assert by_id["cross-page-paragraph"] == [
        {
            "unit_id": "unit-cross-page",
            "kind": "cross_page",
            "fragments": ["p1-c1-body-2a", "p2-c1-body-2b"],
        }
    ]
    assert all(
        not units
        for fixture_id, units in by_id.items()
        if fixture_id not in {"cross-column-paragraph", "cross-page-paragraph"}
    )


def test_active_content_fixture_contains_fixed_active_actions(tmp_path: Path) -> None:
    output_path = tmp_path / "active-content.pdf"
    generate_fixture(SPECS_DIR / "active-content.json", output_path)

    reader = PdfReader(output_path)
    root = reader.trailer["/Root"]
    assert root["/OpenAction"]["/S"] == "/JavaScript"
    assert root["/OpenAction"]["/JS"] == "app.alert('synthetic fixture');"
    assert reader.pages[0]["/AA"]["/O"]["/S"] == "/JavaScript"


def test_non_active_fixtures_have_no_active_actions(tmp_path: Path) -> None:
    for spec_path in _spec_paths():
        if spec_path.stem == "active-content":
            continue
        output_path = tmp_path / f"{spec_path.stem}.pdf"
        generate_fixture(spec_path, output_path)
        reader = PdfReader(output_path)
        root = reader.trailer["/Root"]
        assert "/OpenAction" not in root
        assert all("/AA" not in page for page in reader.pages)


def test_normalized_structure_has_no_runtime_specific_values(tmp_path: Path) -> None:
    spec_path = SPECS_DIR / "single-column.json"
    result = generate_fixture(spec_path, tmp_path / "fixture.pdf")
    serialized = _canonical(result.normalized_structure)

    assert str(tmp_path) not in serialized
    assert "generated_at" not in serialized
    assert "timestamp" not in serialized
    assert set(result.normalized_structure) == {
        "fixture_id",
        "page_count",
        "page_size_pt",
        "truth_sha256",
    }
