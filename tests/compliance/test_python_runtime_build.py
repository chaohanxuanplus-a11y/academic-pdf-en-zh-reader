# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import build_compatible_python as recipe


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "root/a:stream",
        "root/../escape",
        "root\\escape",
        "root/CON.txt",
        "root/COM¹.txt",
        "root/trailing.",
        "root/trailing ",
        "other/file",
        "root//file",
        "root/./file",
    ],
)
def test_archive_member_rejects_ambiguous_windows_paths(name: str) -> None:
    with pytest.raises(ValueError):
        recipe._archive_member(name, "root")


def test_tar_rejects_links_before_writing_any_member(tmp_path: Path) -> None:
    archive = tmp_path / "source.tgz"
    with tarfile.open(archive, "w:gz") as handle:
        first = tarfile.TarInfo("root/ordinary")
        first.size = 4
        handle.addfile(first, io.BytesIO(b"safe"))
        link = tarfile.TarInfo("root/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "ordinary"
        handle.addfile(link)
    destination = tmp_path / "extracted"
    with pytest.raises(ValueError, match="regular"):
        recipe._extract_archive(archive, destination, "root")
    assert not destination.exists()


@pytest.mark.parametrize("kind", [tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_tar_rejects_hardlinks_and_devices(tmp_path: Path, kind: bytes) -> None:
    archive = tmp_path / "source.tgz"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("root/object")
        member.type = kind
        member.linkname = "root/another"
        handle.addfile(member)
    with pytest.raises(ValueError):
        recipe._extract_archive(archive, tmp_path / "extracted", "root")


@pytest.mark.parametrize("names", [["root/a", "root/A"], ["root/a", "root/a/b"]])
def test_zip_rejects_collisions(tmp_path: Path, names: list[str]) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name in names:
            handle.writestr(name, b"data")
    with pytest.raises(ValueError):
        recipe._extract_archive(archive, tmp_path / "extracted", "root")


def test_zip_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        member = zipfile.ZipInfo("root/link")
        member.create_system = 3
        member.external_attr = 0o120777 << 16
        handle.writestr(member, b"target")
    with pytest.raises(ValueError):
        recipe._extract_archive(archive, tmp_path / "extracted", "root")


def test_archive_size_limit_fails_before_writing(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("root/file", b"too large")
    monkeypatch.setattr(recipe, "MAX_MEMBER_BYTES", 3)
    with pytest.raises(ValueError, match="limit"):
        recipe._extract_archive(archive, tmp_path / "extracted", "root")
    assert not (tmp_path / "extracted").exists()


def test_safe_archive_extracts_only_beneath_fresh_output(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("root/", b"")
        handle.writestr("root/subdir/file", b"expected")
    destination = tmp_path / "extracted"
    recipe._extract_archive(archive, destination, "root")
    assert (destination / "subdir/file").read_bytes() == b"expected"
    with pytest.raises(FileExistsError):
        recipe._extract_archive(archive, destination, "root")


def test_existing_output_is_never_reused_even_when_empty(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        recipe._fresh_path(tmp_path)


def test_reparse_ancestor_is_rejected(tmp_path: Path, monkeypatch) -> None:
    original = recipe._is_reparse
    monkeypatch.setattr(
        recipe, "_is_reparse", lambda path: path == tmp_path or original(path)
    )
    with pytest.raises(ValueError, match="reparse"):
        recipe._fresh_path(tmp_path / "runtime")


def test_patch_changes_only_exact_dll_manifest_line(tmp_path: Path) -> None:
    resource = tmp_path / "python_nt.rc"
    before = b'// resources\r\n2 RT_MANIFEST "python.manifest"\r\nVERSIONINFO\r\n'
    resource.write_bytes(before)
    evidence = recipe._patch_resources(resource)
    assert resource.read_bytes() == before.replace(
        b'2 RT_MANIFEST "python.manifest"', recipe.PATCH_REPLACEMENT
    )
    assert evidence["before_sha256"] != evidence["after_sha256"]
    with pytest.raises(ValueError, match="exactly once"):
        recipe._patch_resources(resource)


def test_patch_refuses_unexpected_or_duplicate_resource(tmp_path: Path) -> None:
    resource = tmp_path / "python_nt.rc"
    original = b'2 RT_MANIFEST "python.manifest"\n' * 2
    resource.write_bytes(original)
    with pytest.raises(ValueError, match="exactly once"):
        recipe._patch_resources(resource)
    assert resource.read_bytes() == original


def test_runtime_targets_cover_required_database_and_spawn_modules() -> None:
    assert recipe.TARGETS[-4:] == (
        "_overlapped",
        "_multiprocessing",
        "sqlite3",
        "_sqlite3",
    )
    assert "sqlite3" not in recipe.EXTENSIONS
    assert {"_overlapped", "_multiprocessing", "_sqlite3"} <= set(recipe.EXTENSIONS)


def test_sqlite_resource_change_records_the_correct_file(tmp_path: Path) -> None:
    resource = tmp_path / "sqlite3.rc"
    resource.write_bytes(b'2 RT_MANIFEST "python.manifest"\nVERSIONINFO\n')
    assert recipe._patch_resources(resource)["path"] == "PC/sqlite3.rc"
    assert b"VERSIONINFO" in resource.read_bytes()


def test_sqlite_source_is_fixed_and_uses_versioned_build_directory() -> None:
    sqlite = next(item for item in recipe.SOURCES if item["name"] == "sqlite3")
    assert sqlite["version"] == "3.53.4"
    assert sqlite["sha256"] == (
        "0c604ef459fad52cac8a6b0782a6dd4799bca5680053a99aa531d491f588ae3e"
    )
    assert sqlite["build_directory"] == "sqlite-3.53.4.0"
    assert sqlite["sqlite3_c_sha3_256"] == (
        "67f423e9ebbbdc473cbc4772c872ee6b89f31fde4ed0279a5c25d5f65c043a16"
    )


def test_source_inventory_matches_hardcoded_recipe_pins() -> None:
    inventory = json.loads(recipe.INVENTORY_PATH.read_text(encoding="utf-8"))
    assert inventory["sources"] == list(recipe.SOURCES)
    assert inventory["bit_identical_reproducibility_verified"] is False
    assert inventory["sdk_version"] == recipe.SDK_VERSION
    assert inventory["targets"] == list(recipe.TARGETS)


def test_download_rejects_unpinned_url_before_network(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pinned"):
        recipe._download(
            {"url": "https://example.com/a", "sha256": "0" * 64}, tmp_path / "archive"
        )


def test_download_refuses_incorrect_hash(tmp_path: Path, monkeypatch) -> None:
    class FakeOpener:
        def open(self, url, timeout):
            return io.BytesIO(b"not the pinned archive")

    monkeypatch.setattr(recipe.urllib.request, "build_opener", lambda _: FakeOpener())
    with pytest.raises(ValueError, match="SHA-256"):
        recipe._download(recipe.SOURCES[0], tmp_path / "archive")


def test_download_size_limit_applies_before_hash(tmp_path: Path, monkeypatch) -> None:
    class FakeOpener:
        def open(self, url, timeout):
            return io.BytesIO(b"larger than test limit")

    monkeypatch.setattr(recipe.urllib.request, "build_opener", lambda _: FakeOpener())
    monkeypatch.setattr(recipe, "MAX_DOWNLOAD_BYTES", 3)
    with pytest.raises(ValueError, match="size limit"):
        recipe._download(recipe.SOURCES[0], tmp_path / "archive")


def test_download_redirect_is_forbidden() -> None:
    with pytest.raises(ValueError, match="redirect"):
        recipe._NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "http://example.com/unsafe"
        )


def test_compiler_override_is_rejected_before_tool_discovery(monkeypatch) -> None:
    monkeypatch.setenv("_CL_", "/GS-")
    with pytest.raises(ValueError, match="overrides"):
        recipe._toolchain()


def test_archive_count_limit(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("root/file", b"value")
    monkeypatch.setattr(recipe, "MAX_MEMBERS", 0)
    with pytest.raises(ValueError, match="count"):
        recipe._extract_archive(archive, tmp_path / "extracted", "root")
    assert not (tmp_path / "extracted").exists()


def test_file_manifest_hashes_every_file_and_rejects_links(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"expected")
    assert recipe._file_manifest(tmp_path) == [
        {"path": "file", "size": 8, "sha256": recipe._sha256(tmp_path / "file")}
    ]
