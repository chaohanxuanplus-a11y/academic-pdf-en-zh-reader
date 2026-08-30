# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.bootstrap_fonts import compute_git_blob_sha1, inspect_font

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "assets" / "font-manifest.json"


def test_manifest_and_font_files_are_fully_pinned() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["license"] == "OFL-1.1"
    assert manifest["system_font_fallback"] is False
    assert manifest["embedding_probe"]["status"] == "passed"

    for record in manifest["fonts"]:
        path = ROOT / record["path"]
        data = path.read_bytes()
        assert path.resolve().is_relative_to((ROOT / "assets" / "fonts").resolve())
        assert len(data) == record["size"]
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert compute_git_blob_sha1(data) == record["git_blob_sha1"]
        assert record["archive_member"]
        assert record["copyright"]
        assert record["family"] == record["expected_family"]
        assert record["subfamily"] == record["expected_subfamily"]
        assert record["postscript_name"]
        generated_names = f"{record['family']} {record['postscript_name']}".casefold()
        assert all(
            reserved.casefold() not in generated_names
            for reserved in record["reserved_font_names"]
        )
        assert record["source_tag"]
        assert record["source_commit"]
        if record["source_kind"] == "derived_variable_instance":
            assert record["source_url"].startswith("https://raw.githubusercontent.com/")
            assert record["modified"] is True
            assert record["source_binary"]["sha256"]
            assert record["transformation"]["recalc_timestamp"] is False
        elif record["source_kind"] == "github_release_archive":
            assert record["source_url"].startswith("https://github.com/")
            assert len(record["archive_sha256"]) == 64
            assert record["modified"] is False
        else:
            raise AssertionError(f"unapproved source kind: {record['source_kind']}")

    by_role = {record["role"]: record for record in manifest["fonts"]}
    assert by_role["body"]["spdx_license"] == "OFL-1.1-RFN"
    assert by_role["heading"]["spdx_license"] == "OFL-1.1-RFN"
    assert by_role["symbols"]["spdx_license"] == "OFL-1.1"


def test_cjk_sc_fonts_are_selected_by_names_and_use_glyf_outlines() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    by_role = {record["role"]: record for record in manifest["fonts"]}

    for role in ("body", "heading"):
        record = by_role[role]
        inspected = inspect_font(
            ROOT / record["path"],
            expected_family=record["expected_family"],
            expected_subfamily=record["expected_subfamily"],
        )
        assert inspected["face_index"] == record["face_index"]
        assert inspected["family"] == record["expected_family"]
        assert inspected["postscript_name"] == record["postscript_name"]
        assert inspected["outline_format"] == record["outline_format"]
        assert inspected["outline_format"] == "glyf"


def test_manifest_never_references_a_system_font() -> None:
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8").casefold()
    forbidden = ("windows\\fonts", "/system/library/fonts", "/usr/share/fonts")
    assert not any(token in manifest_text for token in forbidden)
