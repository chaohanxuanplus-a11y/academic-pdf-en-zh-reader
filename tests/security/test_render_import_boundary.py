# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Worker imports remain usable when the sandbox denies Winsock initialization."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_render_and_qa_import_when_network_initialization_is_denied() -> None:
    code = f"""
import builtins, sys
sys.path.insert(0, {json.dumps(str(ROOT / "src"))})
original_import = builtins.__import__
def reject_network_import(name, *args, **kwargs):
    if name.partition(".")[0] in {{"socket", "_socket"}}:
        raise ImportError("WSAStartup failed: error code 10107")
    return original_import(name, *args, **kwargs)
builtins.__import__ = reject_network_import
from academic_pdf_en_zh_reader.rendering.compose import compose_bilingual_pdf
from academic_pdf_en_zh_reader.qa.api import run_mechanical_qa
assert callable(compose_bilingual_pdf) and callable(run_mechanical_qa)
assert "importlib.metadata" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_extraction_when_network_initialization_is_denied(tmp_path: Path) -> None:
    from scripts.generate_synthetic_fixtures import generate_fixture

    source = tmp_path / "network-free-extraction.pdf"
    generate_fixture(ROOT / "tests/fixtures-synthetic/specs/single-column.json", source)
    code = f"""
import builtins, sys
from pathlib import Path
sys.path.insert(0, {json.dumps(str(ROOT / "src"))})
original_import = builtins.__import__
def reject_network_import(name, *args, **kwargs):
    if name.partition(".")[0] in {{"socket", "_socket"}}:
        raise ImportError("WSAStartup failed: error code 10107")
    return original_import(name, *args, **kwargs)
builtins.__import__ = reject_network_import
from academic_pdf_en_zh_reader.extraction import extract_document
result = extract_document(Path({json.dumps(str(source))}))
assert len(result["pages"]) == 1
assert result["pages"][0]["chars"]
assert "socket" not in sys.modules and "_socket" not in sys.modules
assert "importlib.metadata" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
