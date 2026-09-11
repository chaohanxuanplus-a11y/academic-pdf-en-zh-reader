# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from academic_pdf_en_zh_reader.extraction import runtime


def test_pdfminer_adapter_rejects_unreviewed_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = tmp_path / "__init__.py"
    initializer.write_text(
        "raise RuntimeError('must never execute')\n", encoding="utf-8"
    )
    spec = importlib.util.spec_from_file_location(
        "pdfminer", initializer, submodule_search_locations=[str(tmp_path)]
    )
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda _name: spec)
    with pytest.raises(ImportError, match="differs from the reviewed version"):
        runtime.initialize_pinned_pdfminer()


def test_pdfminer_adapter_rejects_preloaded_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.find_spec("pdfminer")
    replacement = ModuleType("pdfminer")
    replacement.__spec__ = spec
    replacement.__version__ = "unreviewed"
    monkeypatch.setitem(sys.modules, "pdfminer", replacement)
    with pytest.raises(ImportError, match="loaded pdfminer version differs"):
        runtime.initialize_pinned_pdfminer()


def test_pdfminer_adapter_rejects_oversized_initializer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initializer = tmp_path / "__init__.py"
    initializer.write_bytes(b" " * 4097)
    spec = importlib.util.spec_from_file_location(
        "pdfminer", initializer, submodule_search_locations=[str(tmp_path)]
    )
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda _name: spec)
    with pytest.raises(ImportError, match="bounded regular file"):
        runtime.initialize_pinned_pdfminer()
