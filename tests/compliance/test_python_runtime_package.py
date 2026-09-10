# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import zipfile
from pathlib import Path

import pytest

from scripts import package_python_runtime as package

REPO = Path(__file__).resolve().parents[2]


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pe(*, executable: bool = False, flags: int = 0x4160) -> bytes:
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<H", data, 148, 240)
    struct.pack_into("<H", data, 152, 0x20B)
    struct.pack_into("<H", data, 222, flags)
    struct.pack_into("<I", data, 260, 16)
    struct.pack_into("<II", data, 280, 0x1000, 128)
    struct.pack_into("<8sIIII", data, 392, b".rsrc", 512, 0x1000, 512, 512)
    struct.pack_into("<HH", data, 524, 0, 2 if executable else 1)
    struct.pack_into("<II", data, 528, 16, 0x80000040)
    if executable:
        struct.pack_into("<II", data, 536, 24, 0x80000060)
    return bytes(data)


def _tree(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "compliance").mkdir()
    for relative in (
        "scripts/build_compatible_python.py",
        "compliance/python-runtime.json",
    ):
        shutil.copyfile(REPO / relative, repo / relative)
    inventory = json.loads((repo / "compliance/python-runtime.json").read_text())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    members = {
        "python.exe": _pe(executable=True),
        "pythonw.exe": _pe(executable=True),
        "python3.dll": _pe(),
        "python312.dll": _pe(),
        "vcruntime140.dll": b"fixture CRT",
        "vcruntime140_1.dll": b"fixture CRT extension",
        "DLLs/libffi-8.dll": b"fixture ffi",
        "LICENSE.txt": b"Fixture project and runtime notice",
        "CHANGES-project-runtime.txt": b"Fixture modifications notice",
        "Lib/encodings/__init__.py": b"# fixture\n",
    }
    for name in ("_ctypes", "_socket", "select", "pyexpat", "unicodedata"):
        members[f"DLLs/{name}.pyd"] = _pe()
    for name in ("_overlapped", "_multiprocessing", "_sqlite3"):
        members[f"DLLs/{name}.pyd"] = _pe()
    members["DLLs/sqlite3.dll"] = _pe()
    for name in package.REQUIRED_LICENSES:
        members[f"licenses/{name}"] = b"Fixture license, not actual release rights"
    for relative, content in members.items():
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {
        "schema_version": 1,
        "runtime": "project-cpython-windows-x64",
        "version": "3.12.14",
        "recipe_sha256": _digest(
            (repo / "scripts/build_compatible_python.py").read_bytes()
        ),
        "source_inventory_sha256": _digest(
            (repo / "compliance/python-runtime.json").read_bytes()
        ),
        "sources": inventory["sources"],
        "targets": inventory["targets"],
        "files_exclude": ["build-manifest.json"],
        "files": [
            {"path": name, "size": len(content), "sha256": _digest(content)}
            for name, content in sorted(members.items())
        ],
    }
    _save_manifest(runtime, manifest)
    return repo, runtime, manifest


def _save_manifest(runtime: Path, manifest: dict) -> None:
    (runtime / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_roundtrip_includes_base_manifest_and_licenses(tmp_path: Path) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    output = tmp_path / "artifacts"
    archive = package.package_runtime(runtime, output, repo_root=repo)
    assert archive.name == "academic-pdf-en-zh-reader-cpython-3.12.14-win-amd64.zip"
    receipt = json.loads((output / "runtime-artifact.json").read_text())
    assert receipt["archive"]["sha256"] == _digest(archive.read_bytes())
    assert receipt["archive"]["size"] == archive.stat().st_size
    assert receipt["build_manifest_sha256"] == _digest(
        (runtime / "build-manifest.json").read_bytes()
    )
    with zipfile.ZipFile(archive) as handle:
        expected = {"compatible-python/" + item["path"] for item in manifest["files"]}
        expected.add("compatible-python/build-manifest.json")
        assert set(handle.namelist()) == expected
        for item in handle.infolist():
            assert item.date_time == (1980, 1, 1, 0, 0, 0)
            assert (
                handle.read(item)
                == (
                    runtime / item.filename.removeprefix("compatible-python/")
                ).read_bytes()
            )


def test_archive_headers_are_repeatable_for_identical_inputs(tmp_path: Path) -> None:
    repo, runtime, _ = _tree(tmp_path)
    first = package.package_runtime(runtime, tmp_path / "one", repo_root=repo)
    for path in runtime.rglob("*"):
        os.utime(path, (1700000000, 1700000000))
    second = package.package_runtime(runtime, tmp_path / "two", repo_root=repo)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("kind", ("changed", "missing", "extra", "omitted", "size"))
def test_file_inventory_is_independently_verified(tmp_path: Path, kind: str) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    target = runtime / "LICENSE.txt"
    if kind == "changed":
        target.write_bytes(b"tampered")
    elif kind == "missing":
        target.unlink()
    elif kind == "extra":
        (runtime / "extra.py").write_bytes(b"unexpected")
    elif kind == "omitted":
        manifest["files"] = [
            item for item in manifest["files"] if item["path"] != "LICENSE.txt"
        ]
    else:
        manifest["files"][0]["size"] += 1
    _save_manifest(runtime, manifest)
    with pytest.raises((ValueError, OSError)):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "3.12.10"),
        ("runtime", "other"),
        ("recipe_sha256", "0" * 64),
        ("source_inventory_sha256", "0" * 64),
        ("sources", []),
        ("targets", []),
        ("files_exclude", ["build-manifest.json", "extra"]),
        ("schema_version", True),
    ],
)
def test_identity_cannot_be_self_attested(tmp_path: Path, field: str, value) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    manifest[field] = value
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


@pytest.mark.parametrize(
    "member",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "a\\b",
        "a:stream",
        "CON.txt",
        "file.",
        "file ",
        "a//b",
        "a/./b",
        "build-manifest.json",
    ],
)
def test_manifest_paths_fail_closed(tmp_path: Path, member: str) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    manifest["files"].append({"path": member, "size": 0, "sha256": _digest(b"")})
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_duplicate_casefolded_manifest_path_is_rejected(tmp_path: Path) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    duplicate = dict(manifest["files"][0])
    duplicate["path"] = duplicate["path"].upper()
    manifest["files"].append(duplicate)
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_hardlinked_input_is_rejected(tmp_path: Path) -> None:
    repo, runtime, _ = _tree(tmp_path)
    os.link(runtime / "LICENSE.txt", tmp_path / "outside-link")
    with pytest.raises(ValueError, match="link"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_reparse_input_is_rejected_without_following(
    tmp_path: Path, monkeypatch
) -> None:
    repo, runtime, _ = _tree(tmp_path)
    original = package._is_reparse
    monkeypatch.setattr(
        package, "_is_reparse", lambda path: path == runtime / "Lib" or original(path)
    )
    with pytest.raises(ValueError, match="reparse"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    repo, runtime, _ = _tree(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "keep"
    marker.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        package.package_runtime(runtime, output, repo_root=repo)
    assert marker.read_bytes() == b"preserve"


def test_output_must_not_overlap_runtime(tmp_path: Path) -> None:
    repo, runtime, _ = _tree(tmp_path)
    with pytest.raises(ValueError):
        package.package_runtime(runtime, runtime / "output", repo_root=repo)


def test_forged_inventory_cannot_hide_missing_license(tmp_path: Path) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    relative = "licenses/libffi-LICENSE.txt"
    (runtime / relative).unlink()
    manifest["files"] = [item for item in manifest["files"] if item["path"] != relative]
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError, match="required"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


@pytest.mark.parametrize(
    "name,content",
    [
        ("python312.dll", _pe(flags=0x0160)),
        ("pythonw.exe", _pe()),
        ("DLLs/_ctypes.pyd", _pe(executable=True)),
        ("python312.dll", b"not a PE"),
    ],
)
def test_pe_policy_checks_actual_bytes_even_with_rehashed_manifest(
    tmp_path: Path, name: str, content: bytes
) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    (runtime / name).write_bytes(content)
    for item in manifest["files"]:
        if item["path"] == name:
            item.update(size=len(content), sha256=_digest(content))
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError, match="PE"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_zip_failure_never_publishes_success_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    repo, runtime, _ = _tree(tmp_path)
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("zip failure")),
    )
    with pytest.raises(OSError, match="zip failure"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)
    assert not (tmp_path / "output").exists()


def test_manifest_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    repo, runtime, _ = _tree(tmp_path)
    manifest_path = runtime / "build-manifest.json"
    content = manifest_path.read_text()
    manifest_path.write_text('{"version":"3.12.10",' + content[1:])
    with pytest.raises(ValueError, match="duplicate"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


@pytest.mark.parametrize(
    "name",
    (
        "extra.dll",
        "DLLs/extra.pyd",
        "Lib/site-packages/extra.py",
        "Lib/__pycache__/extra.pyc",
    ),
)
def test_extra_code_cannot_be_authorized_by_self_report(
    tmp_path: Path, name: str
) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    content = b"unexpected code"
    path = runtime / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    manifest["files"].append(
        {"path": name, "size": len(content), "sha256": _digest(content)}
    )
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError, match="layout"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_empty_unlisted_directory_is_rejected(tmp_path: Path) -> None:
    repo, runtime, _ = _tree(tmp_path)
    (runtime / "empty").mkdir()
    with pytest.raises(ValueError, match="empty"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


@pytest.mark.parametrize("value", (True, -1, "1024", package._MAX_FILE_BYTES + 1))
def test_manifest_file_size_is_bounded_integer(tmp_path: Path, value) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    manifest["files"][0]["size"] = value
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError, match="size/hash"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


def test_changed_during_zip_fails_without_output_or_stage(
    tmp_path: Path, monkeypatch
) -> None:
    repo, runtime, _ = _tree(tmp_path)
    original = zipfile.ZipFile.writestr

    def mutate(handle, member, content, **kwargs):
        result = original(handle, member, content, **kwargs)
        if member.filename == "compatible-python/LICENSE.txt":
            (runtime / "LICENSE.txt").write_bytes(b"changed after packing")
        return result

    monkeypatch.setattr(zipfile.ZipFile, "writestr", mutate)
    with pytest.raises(ValueError, match="mismatch"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".python-runtime-*"))


def test_output_appearing_during_packaging_is_preserved(
    tmp_path: Path, monkeypatch
) -> None:
    repo, runtime, _ = _tree(tmp_path)
    output = tmp_path / "output"
    original = zipfile.ZipFile.writestr

    def create_output(handle, member, content, **kwargs):
        result = original(handle, member, content, **kwargs)
        output.mkdir(exist_ok=True)
        (output / "keep").write_bytes(b"other owner")
        return result

    monkeypatch.setattr(zipfile.ZipFile, "writestr", create_output)
    with pytest.raises(FileExistsError):
        package.package_runtime(runtime, output, repo_root=repo)
    assert (output / "keep").read_bytes() == b"other owner"
    assert not (output / "runtime-artifact.json").exists()
    assert not list(tmp_path.glob(".python-runtime-*"))


def test_source_pin_remains_fixed_even_when_manifest_is_rehashed(
    tmp_path: Path,
) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    source_file = repo / "compliance/python-runtime.json"
    inventory = json.loads(source_file.read_bytes())
    inventory["sources"][0]["sha256"] = "0" * 64
    source_file.write_text(json.dumps(inventory))
    manifest["sources"] = inventory["sources"]
    manifest["source_inventory_sha256"] = _digest(source_file.read_bytes())
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError, match="fixed CPython"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)


@pytest.mark.parametrize(
    "offset,format_,value",
    [(60, "<I", 1000000), (148, "<H", 65535), (222, "<H", 0), (532, "<I", 0x80010000)],
)
def test_pe_structure_bounds_fail_closed(offset: int, format_: str, value: int) -> None:
    content = bytearray(_pe())
    struct.pack_into(format_, content, offset, value)
    with pytest.raises(ValueError, match="PE"):
        package._validate_pe(bytes(content), "python312.dll")


def test_async_and_database_closure_is_required() -> None:
    assert {
        "DLLs/_overlapped.pyd",
        "DLLs/_multiprocessing.pyd",
        "DLLs/_sqlite3.pyd",
        "DLLs/sqlite3.dll",
        "licenses/sqlite3-LICENSE.txt",
    } <= package.REQUIRED_FILES
    assert package.TARGETS[-4:] == (
        "_overlapped",
        "_multiprocessing",
        "sqlite3",
        "_sqlite3",
    )


@pytest.mark.parametrize(
    "name",
    [
        "DLLs/_overlapped.pyd",
        "DLLs/_multiprocessing.pyd",
        "DLLs/_sqlite3.pyd",
        "DLLs/sqlite3.dll",
        "licenses/sqlite3-LICENSE.txt",
    ],
)
def test_required_async_and_database_members_cannot_be_omitted(
    tmp_path: Path, name: str
) -> None:
    repo, runtime, manifest = _tree(tmp_path)
    (runtime / name).unlink()
    manifest["files"] = [item for item in manifest["files"] if item["path"] != name]
    _save_manifest(runtime, manifest)
    with pytest.raises(ValueError, match="required"):
        package.package_runtime(runtime, tmp_path / "output", repo_root=repo)
