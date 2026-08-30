# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.windows_worker import (
    SandboxUnavailableError,
)


def test_worker_runtime_copies_only_fixed_project_core_and_pypdf(
    tmp_path: Path,
) -> None:
    runtime = windows_worker._copy_worker_runtime(tmp_path)
    _source, manifest, fingerprint = windows_worker._pypdf_runtime_manifest()

    assert runtime.pypdf_version == "6.16.2"
    assert runtime.pypdf_fingerprint == fingerprint
    assert runtime.pypdf_file_count == len(manifest)
    assert runtime.pypdf_bytes == sum(size for _path, size, _hash in manifest)
    assert runtime.project_file_count == len(windows_worker._PROJECT_RUNTIME_FILES)
    assert (runtime.root / "pypdf" / "__init__.py").is_file()
    assert (
        runtime.root / "academic_pdf_en_zh_reader" / "preflight" / "checks.py"
    ).is_file()
    assert (
        runtime.root / "academic_pdf_en_zh_reader" / "job" / "canonical_json.py"
    ).is_file()
    assert not (runtime.root / "cryptography").exists()
    assert not any(
        path.suffix.casefold() in {".pyd", ".dll"} for path, _size, _hash in manifest
    )


def test_pypdf_runtime_rejects_any_version_other_than_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_cache = windows_worker._PYPDF_RUNTIME_MANIFEST_CACHE
    windows_worker._PYPDF_RUNTIME_MANIFEST_CACHE = None
    monkeypatch.setattr(
        windows_worker,
        "_pypdf_distribution",
        lambda: type("Distribution", (), {"version": "6.16.1"})(),
    )
    try:
        with pytest.raises(SandboxUnavailableError, match="exactly 6.16.2"):
            windows_worker._pypdf_runtime_manifest()
    finally:
        windows_worker._PYPDF_RUNTIME_MANIFEST_CACHE = original_cache


def test_runtime_source_rejects_symlink_or_reparse(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("value = 1\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(real)
    except OSError as error:
        pytest.skip(f"creating symlinks is unavailable: {error}")

    assert windows_worker._runtime_source_is_safe(tmp_path, link) is False


def test_private_python_runtime_includes_consoleless_entrypoint(
    tmp_path: Path,
) -> None:
    runtime = windows_worker._copy_minimal_python_runtime(tmp_path)

    assert (runtime.root / "python.exe").is_file()
    assert (runtime.root / "pythonw.exe").is_file()
    command = windows_worker._child_command(
        tmp_path,
        runtime.root / "pythonw.exe",
        windows_worker.DEFAULT_LIMITS,
    ).value
    assert str(runtime.root / "pythonw.exe") in command
    assert " -I -S -B -X no_debug_ranges " in command
