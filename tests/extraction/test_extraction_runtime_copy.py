# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
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
    source, manifest, _fingerprint = windows_worker._extraction_runtime_manifest()
    copied_bytes = 0
    copied_fingerprint = hashlib.sha256()
    for relative, _size, _digest in manifest:
        payload = (runtime.root / relative).read_bytes()
        assert payload == (source / relative).read_bytes()
        copied_bytes += len(payload)
        digest = hashlib.sha256(payload).hexdigest()
        copied_fingerprint.update(
            f"{relative.as_posix()}\0{len(payload)}\0{digest}\n".encode()
        )
    assert copied_fingerprint.hexdigest() == runtime.extraction_runtime_fingerprint
    # The reviewed CRLF baseline and official pure 3.5.1 wheel differ only in
    # ten charset_normalizer files' line endings. Bind both exact closures.
    pinned_totals = {
        "093307a844bdf9488d9558c5235bfc68ca2706b395aae130d9181cab64cc23af": 19_394_207,
        "8542426ad87b0467d373a27d791764afaa0bdf1a3aec049b311c59c2ad730a6b": 19_388_267,
        # Same fixed dependencies with locally rebuilt CFFI 2.1.1: unchanged
        # official source, no empty manifest, CFG/ASLR/NX enabled; see issue log.
        "b3c35eab9eafcc6a96c961c0658d31b91f3421376c3db3eeb60a7f33317a528d": 19_388_779,
    }
    assert runtime.extraction_runtime_bytes == copied_bytes
    assert copied_bytes == pinned_totals[copied_fingerprint.hexdigest()]
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
