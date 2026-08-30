# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "assets" / "font-manifest.json"
BODY_HEADING_SAMPLE = (
    "中文研究者阅读英文文献事实程度逻辑数据专业术语：，。；（）"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "αβΔμ±×≤≥→℃²"
)
SYMBOL_SAMPLE = "⏱☢✓"
REQUIRED_SAMPLE = BODY_HEADING_SAMPLE + SYMBOL_SAMPLE


def _cmap(record: dict[str, object]) -> set[int]:
    path = ROOT / str(record["path"])
    if path.suffix.casefold() == ".ttc":
        collection = TTCollection(path, lazy=False)
        font = collection.fonts[int(record["face_index"])]
    else:
        font = TTFont(path, lazy=False)
    return set(font.getBestCmap() or {})


def test_approved_font_chain_covers_representative_academic_text() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    by_role = {record["role"]: record for record in manifest["fonts"]}
    cmaps = {role: _cmap(record) for role, record in by_role.items()}

    for role in ("body", "heading"):
        missing_for_role = [
            char for char in BODY_HEADING_SAMPLE if ord(char) not in cmaps[role]
        ]
        assert missing_for_role == [], role

    missing_symbols = [
        char for char in SYMBOL_SAMPLE if ord(char) not in cmaps["symbols"]
    ]
    assert missing_symbols == []

    missing = [
        char
        for char in REQUIRED_SAMPLE
        if not any(ord(char) in cmap for cmap in cmaps.values())
    ]
    assert missing == []
