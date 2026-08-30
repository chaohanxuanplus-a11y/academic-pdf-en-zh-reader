# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.typography.font_registry import (
    DEFAULT_FONT_MANIFEST,
    FontRegistryError,
    load_font_registry,
)
from academic_pdf_en_zh_reader.typography.font_runs import (
    FontRunResolver,
    MissingGlyphError,
    grapheme_clusters,
)


def test_conservative_graphemes_keep_marks_variants_and_zwj_sequences() -> None:
    assert grapheme_clusters("e\u0301☢\ufe0f👩\u200d🔬") == (
        "e\u0301",
        "☢\ufe0f",
        "👩\u200d🔬",
    )


def test_resolver_uses_only_primary_then_approved_symbol_fallback() -> None:
    registry = load_font_registry()
    resolved = FontRunResolver(registry).resolve("中文A☢B", font_role="body")

    assert tuple(run.font_role for run in resolved.runs) == (
        "body",
        "symbols",
        "body",
    )
    assert "".join(run.text for run in resolved.runs) == "中文A☢B"
    assert resolved.runs[1].font_name == registry.face("symbols").reportlab_name

    with pytest.raises(FrozenInstanceError):
        resolved.runs[0].text = "changed"  # type: ignore[misc]


def test_heading_chain_stays_semibold_while_symbol_fallback_keeps_baseline() -> None:
    registry = load_font_registry()
    resolved = FontRunResolver(registry).resolve("标题⏱", font_role="heading")

    assert tuple(run.font_role for run in resolved.runs) == (
        "heading",
        "symbols",
    )
    assert resolved.runs[0].font_name == registry.face("heading").reportlab_name


def test_missing_grapheme_fails_instead_of_using_a_system_font() -> None:
    resolver = FontRunResolver(load_font_registry())

    with pytest.raises(MissingGlyphError, match=r"U\+1F600"):
        resolver.resolve("未批准😀", font_role="body")
    with pytest.raises(MissingGlyphError, match=r"U\+FE0F"):
        resolver.resolve("☢\ufe0f", font_role="body")
    with pytest.raises(MissingGlyphError, match=r"U\+200D"):
        resolver.resolve("☢\u200d☢", font_role="body")
    with pytest.raises(MissingGlyphError, match="incompatible grapheme"):
        resolver.resolve("☢\u0301", font_role="body")


@pytest.mark.parametrize("character", ["\x00", "\u00ad", "\u200b", "\u202e"])
def test_invisible_and_control_characters_fail_closed(character: str) -> None:
    resolver = FontRunResolver(load_font_registry())

    with pytest.raises(MissingGlyphError, match=rf"U\+{ord(character):04X}"):
        resolver.resolve(f"正文{character}内容", font_role="body")


def test_registry_rejects_a_manifest_that_allows_system_fallback(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "font-manifest.json"
    manifest.write_text(
        '{"schema_version":1,"system_font_fallback":true,"fonts":[]}',
        encoding="utf-8",
    )

    with pytest.raises(FontRegistryError, match="system font fallback"):
        load_font_registry(manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "schema version"),
        ("schema_version", True, "schema version"),
        ("schema_version", 1.0, "schema version"),
        ("license", "unknown", "license"),
    ],
)
def test_registry_rejects_an_unknown_manifest_contract(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = json.loads(DEFAULT_FONT_MANIFEST.read_text(encoding="utf-8"))
    payload[field] = value
    manifest = tmp_path / "font-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FontRegistryError, match=message):
        load_font_registry(manifest)


def test_registry_names_are_stably_derived_from_verified_role_and_hash() -> None:
    registry = load_font_registry()

    for role in ("body", "heading", "symbols"):
        face = registry.face(role)
        assert face.reportlab_name == f"APR-{role}-{face.sha256[:16]}"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "0" * 64, "SHA-256 does not match"),
        ("path", "pyproject.toml", "assets/fonts"),
    ],
)
def test_registry_revalidates_actual_font_metadata_and_path_containment(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = json.loads(DEFAULT_FONT_MANIFEST.read_text(encoding="utf-8"))
    payload["fonts"][0][field] = value
    manifest = tmp_path / "font-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FontRegistryError, match=message):
        load_font_registry(manifest)


def test_registry_rejects_noncanonical_in_directory_font_path(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_FONT_MANIFEST.read_text(encoding="utf-8"))
    payload["fonts"][0]["path"] = "assets/fonts/../fonts/NotoSerifSC-Regular.ttf"
    manifest = tmp_path / "font-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FontRegistryError, match="canonical.*assets/fonts"):
        load_font_registry(manifest)


def test_registry_rejects_a_stale_positive_file_size(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_FONT_MANIFEST.read_text(encoding="utf-8"))
    payload["fonts"][0]["size"] += 1
    manifest = tmp_path / "font-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FontRegistryError, match="size does not match"):
        load_font_registry(manifest)


def test_registry_verifies_actual_font_identity_for_each_role(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_FONT_MANIFEST.read_text(encoding="utf-8"))
    by_role = {record["role"]: record for record in payload["fonts"]}
    for field in ("path", "size", "sha256"):
        by_role["body"][field] = by_role["symbols"][field]
    manifest = tmp_path / "font-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FontRegistryError, match="identity"):
        load_font_registry(manifest)
