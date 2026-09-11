# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_finish_routes_render_and_qa_only_through_worker_bridges() -> None:
    """The production orchestrator must not retain an in-process PDF path."""

    path = ROOT / "src/academic_pdf_en_zh_reader/orchestration/finish.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "compose_bilingual_pdf" not in imported_names
    assert "validate_and_persist_qa" not in imported_names
    assert "render_bilingual_pdf_in_worker" in called_names
    assert "validate_qa_in_worker" in called_names


@pytest.mark.skipif(os.name != "nt", reason="Windows LPAC integration")
def test_production_finish_completes_render_and_qa_in_lpac(tmp_path: Path) -> None:
    from tests.orchestration.test_finish import _finish, _finish_fixture

    fixture = _finish_fixture(tmp_path, job_id="finish-production-lpac")

    assert _finish(fixture) == {
        "status": "ok",
        "code": "FINISH_OK",
        "added_pages": 0,
    }
    assert fixture.output_pdf.is_file()
    assert not fixture.job_root.exists()
