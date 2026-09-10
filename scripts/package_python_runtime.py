# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Read-only integrity/PE checks and atomic packaging of the project runtime.

This does not execute the interpreter, certify LPAC operation, or attest that a
compiler produced the files from the declared sources. Release gates establish
those properties separately. Identical inputs get stable ZIP metadata, not a
claim that independent compiler builds are bit-identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "academic-pdf-en-zh-reader-cpython-3.12.14-win-amd64.zip"
MANIFEST_NAME = "build-manifest.json"
RUNTIME_ID = "project-cpython-windows-x64"
VERSION = "3.12.14"
REQUIRED_LICENSES = (
    "CPython-LICENSE.txt",
    "CPython-upstream-license-reference.rst",
    "Microsoft-runtime-notice.txt",
    "zlib-LICENSE.txt",
    "libffi-LICENSE.txt",
    "expat-COPYING.txt",
    "HACL-source-license-headers.txt",
    "sqlite3-LICENSE.txt",
)
BUILT_IMAGES = (
    "python.exe",
    "pythonw.exe",
    "python3.dll",
    "python312.dll",
    "DLLs/_ctypes.pyd",
    "DLLs/_socket.pyd",
    "DLLs/select.pyd",
    "DLLs/pyexpat.pyd",
    "DLLs/unicodedata.pyd",
    "DLLs/_overlapped.pyd",
    "DLLs/_multiprocessing.pyd",
    "DLLs/sqlite3.dll",
    "DLLs/_sqlite3.pyd",
)
TARGETS = (
    "_freeze_module",
    "python",
    "pythonw",
    "python3dll",
    "_ctypes",
    "_socket",
    "select",
    "pyexpat",
    "unicodedata",
    "_overlapped",
    "_multiprocessing",
    "sqlite3",
    "_sqlite3",
)
REQUIRED_FILES = {
    *BUILT_IMAGES,
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "DLLs/libffi-8.dll",
    "LICENSE.txt",
    "CHANGES-project-runtime.txt",
    "Lib/encodings/__init__.py",
    *(f"licenses/{name}" for name in REQUIRED_LICENSES),
}
_EXCLUDED_LIB_PARTS = {
    "__pycache__",
    "test",
    "tests",
    "site-packages",
    "ensurepip",
    "idlelib",
    "tkinter",
    "turtledemo",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)
_MAX_FILE_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _checked_path(path: Path) -> Path:
    # Inspect ancestors before resolving: resolving first would hide a junction.
    if ".." in path.parts:
        raise ValueError("parent traversal is not allowed")
    absolute = path.absolute()
    for component in absolute.parts[1:]:
        _member_name(component)
    for part in (*reversed(absolute.parents), absolute):
        if os.path.lexists(part) and _is_reparse(part):
            raise ValueError(f"reparse/link path is not allowed: {part}")
    return absolute


def _member_name(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > 500:
        raise ValueError("invalid manifest path")
    if "\\" in name or any(ord(c) < 32 or c in ':<>"|?*' for c in name):
        raise ValueError(f"unsafe manifest path: {name}")
    for part in name.split("/"):
        if (
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or _DEVICE.match(part)
        ):
            raise ValueError(f"unsafe manifest path: {name}")
    return name


def _file_info(path: Path) -> os.stat_result:
    _checked_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"nonregular file or hardlink is not allowed: {path}")
    if info.st_size > _MAX_FILE_BYTES:
        raise ValueError(f"runtime file too large: {path}")
    return info


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


def _read_file(path: Path) -> bytes:
    before = _file_info(path)
    with path.open("rb") as handle:
        if _stamp(os.fstat(handle.fileno())) != _stamp(before):
            raise ValueError(f"input changed before read: {path}")
        content = handle.read(_MAX_FILE_BYTES + 1)
        after = os.fstat(handle.fileno())
    if len(content) != before.st_size or _stamp(after) != _stamp(before):
        raise ValueError(f"input changed while reading: {path}")
    if _stamp(_file_info(path)) != _stamp(before):
        raise ValueError(f"input replaced while reading: {path}")
    return content


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json(content: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    if len(content) > 4 * 1024 * 1024:
        raise ValueError("manifest is too large")
    value = json.loads(content, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    return value


def _inventory(runtime: Path) -> dict[str, Path]:
    files = {}
    directories = set()
    seen = set()
    pending = [runtime]
    while pending:
        directory = pending.pop()
        _checked_path(directory)
        for path in directory.iterdir():
            name = _member_name(path.relative_to(runtime).as_posix())
            if name.casefold() in seen:
                raise ValueError("duplicate case-insensitive runtime path")
            seen.add(name.casefold())
            if len(seen) > 20000:
                raise ValueError("too many runtime paths")
            if _is_reparse(path):
                raise ValueError(f"reparse/link runtime path: {path}")
            if stat.S_ISDIR(path.lstat().st_mode):
                directories.add(name)
                pending.append(path)
            else:
                _file_info(path)
                files[name] = path
    implied = {
        str(parent).replace("\\", "/")
        for name in files
        for parent in Path(name).parents
        if str(parent) != "."
    }
    if directories != implied:
        raise ValueError("unlisted empty runtime directory")
    return files


def _validate_pe(content: bytes, name: str) -> None:
    """Bounded PE32+ header/resource-type checks; never load untrusted images."""

    def unpack(fmt: str, offset: int):
        if offset < 0 or offset + struct.calcsize(fmt) > len(content):
            raise ValueError(f"PE truncated structure: {name}")
        return struct.unpack_from(fmt, content, offset)

    if content[:2] != b"MZ":
        raise ValueError(f"PE missing DOS signature: {name}")
    (pe,) = unpack("<I", 60)
    if content[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"PE missing signature: {name}")
    machine, sections = unpack("<HH", pe + 4)
    (optional_size,) = unpack("<H", pe + 20)
    optional = pe + 24
    (magic,) = unpack("<H", optional)
    (flags,) = unpack("<H", optional + 70)
    (count,) = unpack("<I", optional + 108)
    if (
        machine != 0x8664
        or magic != 0x20B
        or optional_size < 136
        or not 1 <= sections <= 96
        or count < 3
        or flags & 0x4160 != 0x4160
    ):
        raise ValueError(f"PE x64/ASLR/DEP/HighEntropy/CFG policy failed: {name}")
    resource_rva, resource_size = unpack("<II", optional + 128)
    resource = None
    for index in range(sections):
        section = optional + optional_size + index * 40
        virtual_size, rva, raw_size, raw = unpack("<IIII", section + 8)
        if rva <= resource_rva < rva + virtual_size:
            delta = resource_rva - rva
            if (
                resource is not None
                or resource_size < 16
                or delta + resource_size > raw_size
                or raw + raw_size > len(content)
            ):
                raise ValueError(f"PE invalid resource section: {name}")
            resource = raw + delta
    if resource is None:
        raise ValueError(f"PE missing resource directory: {name}")
    named, ids = unpack("<HH", resource + 12)
    if 16 + (named + ids) * 8 > resource_size:
        raise ValueError(f"PE resource table exceeds bounds: {name}")
    types = set()
    for index in range(named + ids):
        kind, child = unpack("<II", resource + 16 + index * 8)
        if child & 0x80000000 == 0 or (child & 0x7FFFFFFF) + 16 > resource_size:
            raise ValueError(f"PE invalid resource subtree: {name}")
        if not kind & 0x80000000:
            if kind in types:
                raise ValueError(f"PE duplicate resource type: {name}")
            types.add(kind)
    if 16 not in types or (24 in types) != name.endswith(".exe"):
        raise ValueError(f"PE VERSIONINFO/RT_MANIFEST policy failed: {name}")


def _validate(runtime: Path, repo: Path) -> tuple[dict, dict, bytes]:
    inventory_bytes = _read_file(repo / "compliance/python-runtime.json")
    inventory = _json(inventory_bytes)
    raw = _read_file(runtime / MANIFEST_NAME)
    manifest = _json(raw)
    for document in (inventory, manifest):
        if (
            type(document.get("schema_version")) is not int
            or document["schema_version"] != 1
            or document.get("runtime") != RUNTIME_ID
            or document.get("version") != VERSION
        ):
            raise ValueError("runtime manifest identity mismatch")
    sources = inventory.get("sources")
    if (
        not isinstance(sources, list)
        or not sources
        or not isinstance(sources[0], dict)
        or sources[0].get("name") != "cpython"
        or sources[0].get("version") != VERSION
        or sources[0].get("url")
        != "https://www.python.org/ftp/python/3.12.14/Python-3.12.14.tgz"
        or sources[0].get("sha256")
        != "6c6df908d2c3fd24e6d76869e92542abd0f33aec9dfc18df8875f89660286d43"
    ):
        raise ValueError("official fixed CPython source identity mismatch")
    expected = {
        "recipe_sha256": _digest(
            _read_file(repo / "scripts/build_compatible_python.py")
        ),
        "source_inventory_sha256": _digest(inventory_bytes),
        "sources": sources,
        "targets": list(TARGETS),
        "files_exclude": [MANIFEST_NAME],
    }
    if inventory.get("targets") != list(TARGETS):
        raise ValueError("runtime build target inventory mismatch")
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "runtime manifest does not match current recipe/source inventory"
        )
    records = manifest.get("files")
    if not isinstance(records, list) or not 1 <= len(records) <= 10000:
        raise ValueError("invalid manifest file list")
    entries = {}
    names = set()
    total = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            raise ValueError("invalid manifest file record")
        name = _member_name(record["path"])
        if name.casefold() in names or name.casefold() == MANIFEST_NAME.casefold():
            raise ValueError("duplicate or self-included manifest path")
        names.add(name.casefold())
        # A rewritten manifest cannot authorize new root/DLLs executables or a
        # third-party package installed into this minimal, immutable base.
        if name not in REQUIRED_FILES and (
            not name.startswith("Lib/")
            or set(Path(name).parts) & _EXCLUDED_LIB_PARTS
            or Path(name).suffix.casefold() in {".pyc", ".dll", ".pyd", ".exe"}
        ):
            raise ValueError(f"file is outside the fixed runtime layout: {name}")
        if (
            type(record["size"]) is not int
            or not 0 <= record["size"] <= _MAX_FILE_BYTES
            or not isinstance(record["sha256"], str)
            or not _SHA256.fullmatch(record["sha256"])
        ):
            raise ValueError("invalid manifest size/hash")
        total += record["size"]
        entries[name] = record
    if total > _MAX_TOTAL_BYTES:
        raise ValueError("runtime total size exceeds limit")
    if not entries.keys() >= REQUIRED_FILES:
        raise ValueError("required runtime binaries/licenses are missing")
    actual = _inventory(runtime)
    if actual.keys() != entries.keys() | {MANIFEST_NAME}:
        raise ValueError("runtime has missing or extra files")
    for name, record in entries.items():
        content = _read_file(actual[name])
        if len(content) != record["size"] or _digest(content) != record["sha256"]:
            raise ValueError(f"runtime file size/hash mismatch: {name}")
        if name in REQUIRED_FILES and not content:
            raise ValueError(f"required runtime file is empty: {name}")
        if name in BUILT_IMAGES:
            _validate_pe(content, name)
    return manifest, entries, raw


def package_runtime(
    runtime_dir: Path, output_dir: Path, *, repo_root: Path = REPO_ROOT
) -> Path:
    runtime = _checked_path(Path(runtime_dir))
    output = _checked_path(Path(output_dir))
    repo = _checked_path(Path(repo_root))
    if output.is_relative_to(runtime) or runtime.is_relative_to(output):
        raise ValueError("runtime and output paths must not overlap")
    if os.path.lexists(output):
        raise FileExistsError(f"output directory must be new: {output}")
    manifest, entries, raw = _validate(runtime, repo)
    output.parent.mkdir(parents=True, exist_ok=True)
    _checked_path(output.parent)
    stage = Path(tempfile.mkdtemp(prefix=".python-runtime-", dir=output.parent))
    try:
        archive = stage / ARCHIVE_NAME
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as handle:
            for name in sorted([*entries, MANIFEST_NAME]):
                content = _read_file(runtime / name)
                expected = entries.get(name, {"size": len(raw), "sha256": _digest(raw)})
                if (
                    len(content) != expected["size"]
                    or _digest(content) != expected["sha256"]
                ):
                    raise ValueError(f"runtime changed during packaging: {name}")
                member = zipfile.ZipInfo(
                    f"compatible-python/{name}", (1980, 1, 1, 0, 0, 0)
                )
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | 0o644) << 16
                handle.writestr(
                    member, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
                )
        # Revalidate after writing, including the source/recipe and full path set.
        if _validate(runtime, repo) != (manifest, entries, raw):
            raise ValueError("runtime manifest changed during packaging")
        receipt = {
            "schema_version": 1,
            "runtime": RUNTIME_ID,
            "version": VERSION,
            "archive": {
                "name": ARCHIVE_NAME,
                "sha256": _digest(_read_file(archive)),
                "size": archive.stat().st_size,
            },
            "build_manifest_sha256": _digest(raw),
            "recipe_sha256": manifest["recipe_sha256"],
            "source_inventory_sha256": manifest["source_inventory_sha256"],
            "packager_sha256": _digest(_read_file(Path(__file__))),
            "file_count": len(entries) + 1,
            "deterministic_zip_headers": True,
            "bit_identical_compiler_builds_verified": False,
            "lpac_verified_by_this_script": False,
        }
        (stage / "runtime-artifact.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _checked_path(output)
        if os.path.lexists(output):
            raise FileExistsError(f"output appeared during packaging: {output}")
        stage.rename(output)
        return output / ARCHIVE_NAME
    finally:
        if stage.exists():
            _checked_path(stage)
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-dir", type=Path, default=Path(".tools/compatible-python")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("dist/python-runtime"))
    args = parser.parse_args()
    try:
        print(package_runtime(args.runtime_dir, args.output_dir))
    except (ValueError, OSError) as exc:
        print(f"Runtime packaging failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
