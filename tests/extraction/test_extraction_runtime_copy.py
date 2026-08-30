# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security import windows_worker


@pytest.mark.skipif(os.name != "nt", reason="Windows extraction runtime closure")
def test_extraction_runtime_is_content_addressed_and_pinned(tmp_path: Path) -> None:
    runtime = windows_worker._copy_worker_runtime(tmp_path, operation="extract")

    assert runtime.pdfplumber_version == "0.11.10"
    assert runtime.pdfminer_version == "20260107"
    assert runtime.extraction_runtime_file_count == 295
    assert runtime.extraction_runtime_bytes == 19_394_207
    assert len(runtime.extraction_runtime_fingerprint) == 64
    assert (runtime.root / "pdfplumber" / "__init__.py").is_file()
    assert (runtime.root / "pdfminer" / "__init__.py").is_file()
    assert not (runtime.root / "pypdf").exists()
    assert not (runtime.root / "charset_normalizer" / "cd.cp312-win_amd64.pyd").exists()
    assert not (runtime.root / "charset_normalizer" / "md.cp312-win_amd64.pyd").exists()
    assert len(list(runtime.root.rglob("*.pyd"))) == 2
    assert (
        runtime.root / "academic_pdf_en_zh_reader" / "extraction" / "page_objects.py"
    ).is_file()


def test_extraction_runtime_does_not_copy_ambient_site_packages(
    tmp_path: Path,
) -> None:
    runtime = windows_worker._copy_worker_runtime(tmp_path, operation="extract")

    assert not (runtime.root / "pytest").exists()
    assert not (runtime.root / "jsonschema").exists()
