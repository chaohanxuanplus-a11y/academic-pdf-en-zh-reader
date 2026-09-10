# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import struct
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "dependency_build_under_test", ROOT / "scripts/build_compatible_dependencies.py"
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_inventory_is_frozen_and_matches_the_existing_lock() -> None:
    inventory = builder._inventory()
    assert len(inventory["sources"]) == 11
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    for source in inventory["sources"]:
        assert source["url"].startswith("https://")
        assert len(source["sha256"]) == 64
        if source["kind"] == "official-wheel" or source["name"] == "pillow":
            assert source["sha256"] in lock
    assert inventory["pillow_configuration"]["freetype"] == "enable"
    assert inventory["native_configuration"]["libjpeg-turbo"]["WITH_SIMD"] == "OFF"
    assert len(inventory["source_changes"]) == 1
    assert inventory["source_changes"][0]["path"] == "src/truetype/ttgxvar.c"
    assert {
        "source": "pillow",
        "path": "src/Tk/_tkmini.h",
        "leading_comments": True,
    } in inventory["license_inputs"]


def test_inventory_rejects_modified_source_pin(tmp_path, monkeypatch) -> None:
    inventory = builder._inventory()
    inventory["sources"][0]["url"] = "https://example.invalid/arbitrary.tar.gz"
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")
    monkeypatch.setattr(builder, "INVENTORY", path)
    with pytest.raises(ValueError, match="inventory"):
        builder._inventory()


def test_unlisted_download_is_rejected_before_creating_file(tmp_path) -> None:
    destination = tmp_path / "download.whl"
    with pytest.raises(ValueError, match="fixed"):
        builder._download({"url": "https://example.invalid"}, destination)
    assert not destination.exists()


@pytest.mark.parametrize("oversized", [False, True])
def test_fixed_download_checks_actual_size_and_content(
    tmp_path, monkeypatch, oversized
) -> None:
    record = builder._inventory()["sources"][0]
    monkeypatch.setattr(
        builder.urllib.request,
        "build_opener",
        lambda *_: type("Opener", (), {"open": lambda *a, **k: io.BytesIO(b"wrong")})(),
    )
    if oversized:
        monkeypatch.setattr(builder, "MAX_DOWNLOAD_BYTES", 3)
    with pytest.raises(ValueError, match="size"):
        builder._download(record, tmp_path / record["filename"])


def test_fixed_download_never_overwrites_existing_file(tmp_path) -> None:
    record = builder._inventory()["sources"][0]
    destination = tmp_path / record["filename"]
    destination.write_bytes(b"existing work")
    with pytest.raises(FileExistsError):
        builder._download(record, destination)
    assert destination.read_bytes() == b"existing work"


def test_build_environment_keeps_architecture_for_cmake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.setenv("NUMBER_OF_PROCESSORS", "4")
    monkeypatch.setattr(builder.shared, "_sha256", lambda _: "verified-tool")
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"WINDOWSSDKVERSION=10.0.26100.0\\\nVCTOOLSINSTALLDIR={tmp_path}\n"
            ).encode(),
        )

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    monkeypatch.setattr(builder.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    identity = {"compiler_sha256": "verified-tool"}
    # The real Windows vcvars output is decoded using the Windows code page.
    if sys.platform != "win32":
        import codecs

        codecs.register(
            lambda encoding: codecs.lookup("utf-8") if encoding == "mbcs" else None
        )
    result = builder._build_environment(
        tmp_path / "VS/MSBuild/Current/Bin/MSBuild.exe", identity, tmp_path
    )
    assert observed["PROCESSOR_ARCHITECTURE"] == "AMD64"
    assert observed["NUMBER_OF_PROCESSORS"] == "4"
    assert result["PROCESSOR_ARCHITECTURE"] == "AMD64"
    assert "Path" not in result


@pytest.mark.parametrize(
    "name",
    [
        "/absolute",
        "../escape",
        "C:/drive",
        "pkg/file:ads",
        "pkg/CON",
        "pkg/a.",
        "pkg\\file",
    ],
)
def test_wheel_members_reject_unsafe_windows_paths(tmp_path, name) -> None:
    wheel = tmp_path / "test.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        info = zipfile.ZipInfo("placeholder")
        info.filename = name
        archive.writestr(info, b"payload")
    with pytest.raises(ValueError):
        builder._wheel_files(wheel)


@pytest.mark.parametrize("kind", ["symlink", "collision", "parent_file", "oversized"])
def test_wheel_members_reject_links_collisions_and_oversize(tmp_path, kind) -> None:
    wheel = tmp_path / "test.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        info = zipfile.ZipInfo("pkg/file")
        if kind == "symlink":
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"1234")
        if kind == "collision":
            archive.writestr("PKG/FILE", b"x")
        elif kind == "parent_file":
            archive.writestr("pkg/file/child", b"x")
    kwargs = {"max_bytes": 3} if kind == "oversized" else {}
    with pytest.raises(ValueError):
        builder._wheel_files(wheel, **kwargs)


def _image(flags=0x4160, resource=False) -> bytes:
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<H", data, 148, 240)
    struct.pack_into("<H", data, 152, 0x20B)
    struct.pack_into("<H", data, 222, flags)
    struct.pack_into("<I", data, 260, 16)
    if resource:
        struct.pack_into("<II", data, 280, 0x1000, 40)
        struct.pack_into("<IIII", data, 400, 512, 0x1000, 512, 512)
        struct.pack_into("<HH", data, 524, 0, 1)
        struct.pack_into("<II", data, 528, 24, 0x80000018)
    return bytes(data)


def test_native_image_keeps_mitigations_without_manifest() -> None:
    result = builder._pe_info(_image(), "PIL/_imaging.pyd")
    assert result["dll_characteristics"] == "0x4160"
    assert result["resource_types"] == []


@pytest.mark.parametrize("bit", [0x20, 0x40, 0x100, 0x4000])
def test_native_image_rejects_missing_mitigation(bit) -> None:
    with pytest.raises(ValueError, match="mitigation"):
        builder._pe_info(_image(0x4160 ^ bit), "PIL/_imaging.pyd")


def test_native_image_rejects_manifest() -> None:
    with pytest.raises(ValueError, match="manifest"):
        builder._pe_info(_image(resource=True), "PIL/_imaging.pyd")


def test_licenses_preserve_original_and_add_freetype_ijg_notices(tmp_path) -> None:
    pillow = tmp_path / "pillow"
    pillow.mkdir()
    (pillow / "LICENSE").write_bytes(b"Original Pillow license\n")
    (pillow / "pyproject.toml").write_bytes(b'license-files = [ "LICENSE" ]\n')
    freetype = tmp_path / "freetype"
    freetype.mkdir()
    (freetype / "FTL.TXT").write_bytes(b"Actual FTL license\n")
    inventory = {"license_inputs": [{"source": "freetype", "path": "FTL.TXT"}]}
    receipt = builder._add_licenses({"pillow": pillow, "freetype": freetype}, inventory)
    assert (pillow / "LICENSE").read_bytes() == b"Original Pillow license\n"
    combined = (pillow / "LICENSE.compatible.txt").read_text(encoding="utf-8")
    assert "FreeType Team" in combined and "Independent JPEG Group" in combined
    assert "Actual FTL license" in combined
    assert "5a280ecde6f324de0d226261036e736e0cb49a71" in combined
    assert "No C implementation" not in combined
    assert (
        receipt["metadata_change"]["before_sha256"]
        != receipt["metadata_change"]["after_sha256"]
    )


def test_leading_comment_retains_separate_copyright_block() -> None:
    source = (
        b"/* summary */\n\n/* Copyright Actual Owner\n"
        b" * Permission granted.\n */\n#include <a>"
    )
    comment = builder._leading_comments(source)
    assert b"Copyright Actual Owner" in comment
    assert b"Permission granted" in comment and b"#include" not in comment


def _fix_fixture(tmp_path):
    source = tmp_path / "freetype/src/truetype/ttgxvar.c"
    source.parent.mkdir(parents=True)
    original = b"first\nold one\nlast\nmiddle\nfirst two\nold two\nlast two\n"
    patched = original.replace(b"old one", b"new one").replace(b"old two", b"new two")
    patch = tmp_path / "fix.patch"
    patch.write_bytes(
        b"diff --git a/src/truetype/ttgxvar.c b/src/truetype/ttgxvar.c\n"
        b"--- a/src/truetype/ttgxvar.c\n+++ b/src/truetype/ttgxvar.c\n"
        b"@@ -1,3 +1,3 @@\n first\n-old one\n+new one\n last\n"
        b"@@ -5,3 +5,3 @@\n first two\n-old two\n+new two\n last two\n"
    )
    source.write_bytes(original)
    change = {
        "source": "freetype",
        "path": "src/truetype/ttgxvar.c",
        "patch_source": "freetype-security-fix",
        "before_sha256": hashlib.sha256(original).hexdigest(),
        "after_sha256": hashlib.sha256(patched).hexdigest(),
    }
    inventory = {
        "sources": [
            {
                "name": "freetype-security-fix",
                "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
            }
        ],
        "source_changes": [change],
    }
    roots = {"freetype": tmp_path / "freetype", "freetype-security-fix": patch}
    return source, patch, original, patched, inventory, roots


def test_freetype_fix_applies_both_reviewed_hunks(tmp_path) -> None:
    source, _, _, patched, inventory, roots = _fix_fixture(tmp_path)
    assert builder._apply_freetype_fix(roots, inventory) == inventory["source_changes"]
    assert source.read_bytes() == patched


@pytest.mark.parametrize("kind", ["source", "patch", "after_hash", "repeat"])
def test_freetype_fix_fails_closed_without_partial_rewrite(tmp_path, kind) -> None:
    source, patch, original, _, inventory, roots = _fix_fixture(tmp_path)
    if kind == "source":
        source.write_bytes(original + b"unexpected")
    elif kind == "patch":
        patch.write_bytes(patch.read_bytes() + b"unexpected")
    elif kind == "after_hash":
        inventory["source_changes"][0]["after_sha256"] = "0" * 64
    else:
        builder._apply_freetype_fix(roots, inventory)
    before = source.read_bytes()
    with pytest.raises(ValueError, match="FreeType"):
        builder._apply_freetype_fix(roots, inventory)
    assert source.read_bytes() == before
