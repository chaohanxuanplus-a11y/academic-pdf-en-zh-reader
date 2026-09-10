# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security import windows_worker
from academic_pdf_en_zh_reader.security.limits import WorkerLimits


def _pillow_record_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    root = tmp_path / "site-packages"
    pillow = next(
        item for item in windows_worker._RENDERING_DEPENDENCIES if item[0] == "pillow"
    )
    recorded = ("PIL/__init__.py", "pillow.libs/libtiff-fixture.so.6.2.0")
    rows = []
    for name in (*recorded, "pillow.libs/unrecorded.so", "other.libs/unrelated.so"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = name.encode("ascii")
        path.write_bytes(payload)
        if name in recorded:
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
            rows.append(f"{name},sha256={digest.rstrip(b'=').decode()},{len(payload)}")
    info = root / f"pillow-{pillow[1]}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Name: pillow\nVersion: {pillow[1]}\n", encoding="utf-8"
    )
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    distribution = metadata.PathDistribution(info)
    monkeypatch.setattr(metadata, "distribution", lambda _name: distribution)
    monkeypatch.setattr(windows_worker, "_RENDERING_DEPENDENCIES", (pillow,))
    monkeypatch.setattr(windows_worker, "_RENDERING_RUNTIME_MANIFEST_CACHE", None)
    return root, info


def test_render_runtime_copies_only_recorded_pillow_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _info = _pillow_record_runtime(tmp_path, monkeypatch)
    source, manifest, _fingerprint = windows_worker._rendering_runtime_manifest()
    expected = {"PIL/__init__.py", "pillow.libs/libtiff-fixture.so.6.2.0"}
    assert source == root
    assert {path.as_posix() for path, _size, _digest in manifest} == expected
    destination = tmp_path / "copied"
    windows_worker._copy_verified_manifest(source, destination, manifest)
    assert {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    } == expected
    for name in expected:
        assert (destination / name).read_bytes() == (source / name).read_bytes()


@pytest.mark.parametrize(
    "invalid", ["missing", "modified", "traversal", "unhashed", "duplicate"]
)
def test_render_runtime_rejects_invalid_recorded_pillow_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    root, info = _pillow_record_runtime(tmp_path, monkeypatch)
    sidecar = root / "pillow.libs/libtiff-fixture.so.6.2.0"
    if invalid == "missing":
        sidecar.unlink()
    elif invalid == "modified":
        sidecar.write_bytes(b"changed after installation")
    elif invalid == "traversal":
        (root / "outside.so").write_bytes(b"outside")
        with (info / "RECORD").open("a", encoding="utf-8") as stream:
            stream.write("pillow.libs/../outside.so,,7\n")
    else:
        record = info / "RECORD"
        rows = record.read_text(encoding="utf-8").splitlines()
        if invalid == "unhashed":
            fields = rows[1].split(",")
            rows[1] = f"{fields[0]},,{fields[2]}"
        else:
            rows.append(rows[1])
        record.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(windows_worker.SandboxUnavailableError):
        windows_worker._rendering_runtime_manifest()


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
        "import academic_pdf_en_zh_reader.security.runtime_paths as runtime_paths;"
        "modules=(typing_extensions,referencing,compose,qa,runtime_paths);"
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
