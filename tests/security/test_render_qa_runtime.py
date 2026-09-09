# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.limits import WorkerLimits


def test_render_runtime_assets_resolve_from_copied_project_root(
    tmp_path: Path,
) -> None:
    runtime = windows_worker._copy_worker_runtime(tmp_path, operation="render")
    copied_registry = (
        runtime.root / "academic_pdf_en_zh_reader" / "typography" / "font_registry.py"
    )
    copied_project_root = copied_registry.resolve().parents[3]
    source_root, manifest, fingerprint = windows_worker._rendering_asset_manifest()

    assert copied_project_root == tmp_path.resolve()
    assert runtime.rendering_runtime_fingerprint
    assert runtime.rendering_runtime_file_count >= len(manifest)
    assert len(fingerprint) == 64
    assert not (runtime.root / "assets").exists()
    for relative, expected_size, expected_sha256 in manifest:
        copied = copied_project_root / relative
        source = source_root / relative
        assert copied.is_file()
        assert copied.stat().st_size == expected_size == source.stat().st_size
        assert windows_worker._file_sha256(copied) == expected_sha256

    assert (copied_project_root / "assets" / "font-manifest.json").is_file()
    assert (
        copied_project_root / "assets" / "branding" / "brand-manifest.json"
    ).is_file()


def test_render_runtime_has_a_closed_isolated_import_graph(tmp_path: Path) -> None:
    runtime = windows_worker._copy_worker_runtime(tmp_path, operation="render")
    bootstrap = (
        "import pathlib,sys;"
        f"root=pathlib.Path({str(runtime.root)!r}).resolve();"
        "sys.path.insert(0,str(root));"
        "import typing_extensions,referencing;"
        "import academic_pdf_en_zh_reader.rendering.compose as compose;"
        "import academic_pdf_en_zh_reader.qa.api as qa;"
        "modules=(typing_extensions,referencing,compose,qa);"
        "assert all(pathlib.Path(module.__file__).resolve().is_relative_to(root) "
        "for module in modules)"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr


def test_rendered_pdf_has_an_explicit_output_ceiling() -> None:
    limits = WorkerLimits()

    assert limits.max_output_pdf_bytes > limits.max_normalized_pdf_bytes
