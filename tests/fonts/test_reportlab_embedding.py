# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from scripts.probe_reportlab_fonts import probe_fonts

ROOT = Path(__file__).resolve().parents[2]


def test_reportlab_embeds_searchable_fonts_and_repeats_deterministically(
    tmp_path: Path,
) -> None:
    manifest_path = ROOT / "assets" / "font-manifest.json"
    first = probe_fonts(manifest_path, tmp_path / "font-probe-a.pdf")
    second = probe_fonts(manifest_path, tmp_path / "font-probe-b.pdf")

    assert first["passed"] is True
    assert first["embedded_font_count"] >= 3
    assert first["embedded_roles"] == {
        "body": True,
        "heading": True,
        "symbols": True,
    }
    assert first["to_unicode_roles"] == {
        "body": True,
        "heading": True,
        "symbols": True,
    }
    assert first["extracted_text_matches"] is True
    assert first["render_nonwhite_ratio"] > 0.001
    assert all(
        value > 0.001 for value in first["render_nonwhite_ratio_by_role"].values()
    )
    assert first["width_measurements_match"] is True
    assert first["max_width_delta_pt"] <= 0.01
    assert first["used_unapproved_fonts"] == []
    assert first["errors"] == []
    assert first["pdf_sha256"] == second["pdf_sha256"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = manifest["embedding_probe"]
    assert recorded["status"] == "passed"
    assert recorded["reportlab_version"] == "5.0.1"
    assert recorded["probe_contract_hash"] == first["probe_contract_hash"]
    assert recorded["expected_text_sha256"] == first["expected_text_sha256"]
    assert recorded["font_hashes"] == {
        record["role"]: record["sha256"] for record in manifest["fonts"]
    }
