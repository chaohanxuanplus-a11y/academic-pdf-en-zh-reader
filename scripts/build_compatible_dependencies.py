# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build the fixed, minimal cp312 Windows dependency wheelhouse for this project.

Run with a TLS-capable CPython 3.12 x64 that has development headers and pip.
The production interpreter is selected separately; this script never installs
into an existing project environment and does not attest production LPAC behavior.
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
import subprocess
import sys
import sysconfig
import urllib.request
import uuid
import zipfile
from pathlib import Path

import build_compatible_python as shared

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "compliance/python-dependencies.json"
INVENTORY_SHA256 = "165365a275388f63645a50ad456cdc9b01e9b075e9c065e571dda6a7930c7002"
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_WHEEL_BYTES = 256 * 1024 * 1024
PILLOW_WHEEL = "pillow-12.3.0-cp312-cp312-win_amd64.whl"
PILLOW_IMAGES = frozenset(
    f"PIL/{name}.cp312-win_amd64.pyd"
    for name in (
        "_imaging",
        "_imagingft",
        "_imagingmath",
        "_imagingmorph",
        "_imagingtk",
    )
)


def _inventory() -> dict:
    if shared._sha256(INVENTORY) != INVENTORY_SHA256:
        raise ValueError("dependency inventory differs from the reviewed fixed recipe")
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def _download(record: dict, destination: Path) -> None:
    if record not in _inventory()["sources"]:
        raise ValueError("download is not a fixed dependency input")
    destination = shared._fresh_path(destination)
    opener = urllib.request.build_opener(shared._NoRedirect())
    digest = hashlib.sha256()
    total = 0
    with (
        opener.open(record["url"], timeout=120) as response,
        destination.open("xb") as out,
    ):
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > min(record["size"], MAX_DOWNLOAD_BYTES):
                raise ValueError("fixed dependency download exceeds size limit")
            digest.update(chunk)
            out.write(chunk)
    if total != record["size"] or digest.hexdigest() != record["sha256"]:
        raise ValueError("fixed dependency download size/SHA-256 mismatch")


def _wheel_files(path: Path, *, max_bytes: int = MAX_WHEEL_BYTES) -> dict[str, bytes]:
    """Read a bounded wheel only after rejecting unsafe/colliding members."""
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 20_000:
            raise ValueError("wheel member count exceeds limit")
        paths: dict[str, bool] = {}
        checked = []
        total = 0
        for member in members:
            if member.orig_filename != member.filename:
                raise ValueError("wheel member was normalized by the ZIP parser")
            relative = shared._archive_member("wheel/" + member.filename, "wheel")
            if member.filename.startswith("/"):
                raise ValueError("absolute wheel member")
            key = relative.as_posix().casefold()
            mode = stat.S_IFMT(member.external_attr >> 16)
            if (
                key in paths
                or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                or member.flag_bits & 1
                or member.external_attr & 0x400
            ):
                raise ValueError("wheel contains colliding/link/special members")
            total += member.file_size
            if member.file_size > 64 * 1024 * 1024 or total > max_bytes:
                raise ValueError("wheel uncompressed size exceeds limit")
            paths[key] = member.is_dir()
            checked.append((member, relative))
        for _, relative in checked:
            if any(
                paths.get(parent.as_posix().casefold()) is False
                for parent in relative.parents
            ):
                raise ValueError("wheel file conflicts with a parent directory")
        return {
            relative.as_posix(): archive.read(member)
            for member, relative in checked
            if not member.is_dir()
        }


def _pe_info(data: bytes, name: str) -> dict:
    """Inspect PE headers/resources without loading the image."""

    def unpack(fmt: str, offset: int):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError(f"truncated PE: {name}")
        return struct.unpack_from(fmt, data, offset)

    (pe,) = unpack("<I", 60)
    if data[:2] != b"MZ" or data[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"invalid PE signature: {name}")
    machine, sections = unpack("<HH", pe + 4)
    (optional_size,) = unpack("<H", pe + 20)
    optional = pe + 24
    (magic,) = unpack("<H", optional)
    (flags,) = unpack("<H", optional + 70)
    (directories,) = unpack("<I", optional + 108)
    if (
        machine != 0x8664
        or magic != 0x20B
        or optional_size < 136
        or not 1 <= sections <= 96
        or directories < 3
        or flags & 0x4160 != 0x4160
    ):
        raise ValueError(f"PE architecture/mitigation check failed: {name}")
    resource_rva, resource_size = unpack("<II", optional + 128)
    types = []
    if resource_rva:
        matches = []
        for index in range(sections):
            section = optional + optional_size + index * 40
            virtual_size, rva, raw_size, raw = unpack("<IIII", section + 8)
            if rva <= resource_rva < rva + virtual_size:
                delta = resource_rva - rva
                if (
                    resource_size < 16
                    or delta + resource_size > raw_size
                    or raw + raw_size > len(data)
                ):
                    raise ValueError(f"invalid PE resource bounds: {name}")
                matches.append(raw + delta)
        if len(matches) != 1:
            raise ValueError(f"invalid PE resource mapping: {name}")
        resource = matches[0]
        named, ids = unpack("<HH", resource + 12)
        if 16 + 8 * (named + ids) > resource_size:
            raise ValueError(f"oversized PE resource table: {name}")
        for index in range(named + ids):
            kind, child = unpack("<II", resource + 16 + index * 8)
            if not child & 0x80000000 or (child & 0x7FFFFFFF) + 16 > resource_size:
                raise ValueError(f"invalid PE resource subtree: {name}")
            if kind in types:
                raise ValueError(f"duplicate PE resource type: {name}")
            types.append(kind)
    elif resource_size:
        raise ValueError(f"PE resources have size without RVA: {name}")
    if 24 in types:
        raise ValueError(
            f"DLL RT_MANIFEST must be omitted at link time: {name}".lower()
        )
    return {"dll_characteristics": hex(flags), "resource_types": types}


def _leading_comments(content: bytes) -> bytes:
    match = re.match(rb"(?:\s*/\*.*?\*/)+", content, re.DOTALL)
    if not match or b"Copyright" not in match[0]:
        raise ValueError("expected original leading copyright/license comments")
    return match[0]


def _apply_freetype_fix(source_roots: dict[str, Path], inventory: dict) -> list[dict]:
    """Apply only the two hunks of the pinned upstream fix, without fuzzy matching."""
    (change,) = inventory["source_changes"]
    patch_record = next(
        item for item in inventory["sources"] if item["name"] == change["patch_source"]
    )
    source = source_roots["freetype"] / "src/truetype/ttgxvar.c"
    patch = source_roots[change["patch_source"]].read_bytes()
    before = source.read_bytes()
    if (
        hashlib.sha256(patch).hexdigest() != patch_record["sha256"]
        or hashlib.sha256(before).hexdigest() != change["before_sha256"]
    ):
        raise ValueError("FreeType original source or official patch SHA-256 mismatch")
    lines = patch.splitlines(keepends=True)
    result = before
    hunks = 0
    for index, line in enumerate(lines):
        match = re.fullmatch(rb"@@ -(\d+),(\d+) \+(\d+),(\d+) @@[^\n]*\n", line)
        if not match:
            continue
        old_count, new_count = int(match[2]), int(match[4])
        old, new = [], []
        for body in lines[index + 1 :]:
            if len(old) == old_count and len(new) == new_count:
                break
            if body[:1] not in (b" ", b"-", b"+"):
                raise ValueError("FreeType official patch has an invalid hunk")
            if body[:1] in (b" ", b"-"):
                old.append(body[1:])
            if body[:1] in (b" ", b"+"):
                new.append(body[1:])
        original, replacement = b"".join(old), b"".join(new)
        if (
            len(old) != old_count
            or len(new) != new_count
            or result.count(original) != 1
        ):
            raise ValueError("FreeType official hunk does not match uniquely")
        result = result.replace(original, replacement, 1)
        hunks += 1
    if hunks != 2 or hashlib.sha256(result).hexdigest() != change["after_sha256"]:
        raise ValueError("FreeType fixed source SHA-256 or hunk count mismatch")
    # No bytes are changed until every source/patch/result check has passed.
    source.write_bytes(result)
    return inventory["source_changes"]


def _add_licenses(source_roots: dict[str, Path], inventory: dict) -> dict:
    pillow = source_roots["pillow"]
    blocks = [
        "Project-specific source build of Pillow 12.3.0 for CPython 3.12 Windows x64.\n"
        "This is not an official Pillow wheel or a complete-feature replacement.\n"
        "No installed DLL was modified. The only C implementation change is\n"
        "FreeType 2.14.3 src/truetype/ttgxvar.c: both hunks of the upstream fix\n"
        "https://github.com/freetype/freetype/commit/5a280ecde6f324de0d226261036e736e0cb49a71\n"
        "(TT_Get_Var_Design: Zero extras; issue 1436). Source and patch hashes\n"
        "are recorded in build-manifest.json; no other master changes apply.\n"
        "Documented optional\n"
        "feature and linker settings differ. DLL manifests are omitted;\n"
        "the compiler/linker mitigations are retained.\n"
        "Pillow metadata is extended to include these notices.\n\n"
        "This software is based in part on the work of the Independent JPEG Group.\n"
        "This software is based in part on the work of the FreeType Team.\n"
        "FreeType is used under the FreeType License (FTL), not its GPL alternative.\n"
        "FreeType project: https://freetype.org\n"
        "On redistribution, preserve the original Pillow LICENSE and notices below.\n"
        "Build Tools, Windows SDK and build-tool wheels are not included.\n"
    ]
    records = []
    for item in inventory["license_inputs"]:
        root = source_roots[item["source"]]
        path = root if root.is_file() else root / item["path"]
        content = path.read_bytes()
        copied = _leading_comments(content) if item.get("leading_comments") else content
        blocks.append(
            f"\n--- {item['source']}/{item['path']} ---\n" + copied.decode("utf-8")
        )
        records.append(
            {
                **item,
                "source_file_sha256": hashlib.sha256(content).hexdigest(),
                "notice_sha256": hashlib.sha256(copied).hexdigest(),
            }
        )
    notice = pillow / "LICENSE.compatible.txt"
    with notice.open("x", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(blocks) + "\n")
    project = pillow / "pyproject.toml"
    before = project.read_bytes()
    original = b'license-files = [ "LICENSE" ]'
    replacement = b'license-files = [ "LICENSE", "LICENSE.compatible.txt" ]'
    if before.count(original) != 1:
        raise ValueError("expected original Pillow license metadata not found")
    project.write_bytes(before.replace(original, replacement))
    return {
        "inputs": records,
        "notice_sha256": shared._sha256(notice),
        "metadata_change": {
            "path": "pillow/pyproject.toml",
            "before_sha256": hashlib.sha256(before).hexdigest(),
            "after_sha256": shared._sha256(project),
        },
    }


def _run(
    arguments: list[str], work: Path, label: str, *, env: dict, cwd: Path | None = None
) -> str:
    print(label, flush=True)
    log = work / (label + ".log")
    with log.open("x", encoding="utf-8") as output:
        output.write(json.dumps(arguments) + "\n")
        output.flush()
        result = subprocess.run(
            arguments,
            cwd=cwd or work,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(f"{label} exited {result.returncode}; see {log}")
    return log.read_text(encoding="utf-8", errors="replace")


def _build_environment(msbuild: Path, identity: dict, work: Path) -> dict:
    # Canonical casing is essential: Windows can inherit both Path and PATH,
    # allowing an older value to replace vcvarsall's configured toolchain path.
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
        "NUMBER_OF_PROCESSORS",
    }
    env = {
        key.upper(): value
        for key, value in os.environ.items()
        if key.upper() in allowed
    }
    installation = msbuild.parents[3]
    vcvars = installation / "VC/Auxiliary/Build/vcvarsall.bat"
    command = f'cmd.exe /d /s /c ""{vcvars}" x64 {shared.SDK_VERSION} && set"'
    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("installed vcvarsall could not initialize the pinned SDK")
    # Do not write or redistribute the user's full environment in build logs.
    for line in result.stdout.decode("mbcs").splitlines():
        if "=" in line and not line.startswith("="):
            key, value = line.split("=", 1)
            if key.upper() in allowed | {
                "INCLUDE",
                "LIB",
                "LIBPATH",
                "VCTOOLSINSTALLDIR",
                "WINDOWSSDKVERSION",
                "WINDOWSSDKDIR",
            }:
                env[key.upper()] = value
    if env.get("WINDOWSSDKVERSION", "").rstrip("\\/") != shared.SDK_VERSION:
        raise ValueError("vcvarsall selected a different SDK")
    compiler = Path(env["VCTOOLSINSTALLDIR"]) / "bin/Hostx64/x64/cl.exe"
    if shared._sha256(compiler) != identity["compiler_sha256"]:
        raise ValueError("vcvarsall selected a different compiler")
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "DISTUTILS_USE_SDK": "1",
            "MSSdk": "1",
            "CL": "/guard:cf",
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    identity["vcvarsall_sha256"] = shared._sha256(vcvars)
    return env


def _build_native(
    work: Path, sources: dict[str, Path], inventory: dict, env: dict, cmake: Path
) -> Path:
    stage = work / "native-stage"
    (stage / "lib").mkdir(parents=True)
    (stage / "include").mkdir()
    for name, target in (
        ("libjpeg-turbo", "jpeg-static"),
        ("zlib-ng", "zlib-ng"),
        ("freetype", "freetype"),
    ):
        build_dir = work / (name + "-build")
        options = [
            f"-D{key}={value}"
            for key, value in inventory["native_configuration"][name].items()
        ]
        _run(
            [
                str(cmake),
                "-S",
                str(sources[name]),
                "-B",
                str(build_dir),
                "-G",
                "NMake Makefiles",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_C_FLAGS=/nologo /guard:cf",
                "-DCMAKE_CXX_FLAGS=/nologo /guard:cf",
                "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL",
                *options,
            ],
            work,
            name + "-configure",
            env=env,
        )
        _run(
            [
                str(cmake),
                "--build",
                str(build_dir),
                "--target",
                target,
                "--parallel",
                "2",
            ],
            work,
            name + "-build",
            env=env,
        )
        if name == "libjpeg-turbo":
            shared._copy_file(build_dir / "jpeg-static.lib", stage / "lib/libjpeg.lib")
            shared._copy_file(build_dir / "jconfig.h", stage / "include/jconfig.h")
            for header in sorted((sources[name] / "src").glob("j*.h")):
                shared._copy_file(header, stage / "include" / header.name)
        elif name == "zlib-ng":
            shared._copy_file(build_dir / "zlibstatic.lib", stage / "lib/zlib.lib")
            # Generated configuration headers supersede the original templates.
            headers = {
                path.name: path
                for root in (sources[name], build_dir)
                for path in root.glob("z*.h")
            }
            for filename, path in sorted(headers.items()):
                shared._copy_file(path, stage / "include" / filename)
        else:
            shared._copy_file(build_dir / "freetype.lib", stage / "lib/freetype.lib")
            shutil.copytree(
                sources[name] / "include", stage / "include", dirs_exist_ok=True
            )
    return stage


def _host_smoke(python: Path, wheel: Path, work: Path, env: dict) -> dict:
    files = _wheel_files(wheel)
    native = {
        name: _pe_info(data, name)
        for name, data in files.items()
        if name.lower().endswith((".pyd", ".dll"))
    }
    if set(native) != PILLOW_IMAGES:
        raise ValueError("Pillow wheel omitted or added an unexpected native module")
    if "pillow-12.3.0.dist-info/licenses/LICENSE.compatible.txt" not in files:
        raise ValueError("Pillow wheel omitted the static dependency notices")
    extracted = work / "host-smoke-wheel"
    extracted.mkdir()
    for name, data in files.items():
        path = extracted / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    font = ROOT / "assets/fonts/NotoSerifSC-Regular.ttf"
    code = f"""
import io, json, sys
sys.path.insert(0, {str(extracted)!r})
from PIL import Image, ImageDraw, ImageFont, features
assert all(features.check(name) for name in ('jpg', 'zlib', 'freetype2'))
image = Image.new('RGB', (32, 24), (42, 100, 160))
for format in ('PNG', 'JPEG'):
    stream = io.BytesIO()
    image.save(stream, format=format, quality=95, progressive=True)
    stream.seek(0)
    restored = Image.open(stream)
    restored.load()
    assert restored.size == image.size
    pixels = zip(image.getpixel((0,0)), restored.getpixel((0,0)))
    assert max(abs(a-b) for a,b in pixels) <= 3
font = ImageFont.truetype({str(font)!r}, 18, layout_engine=ImageFont.Layout.BASIC)
assert font.getmask('中文ABC').getbbox()
ImageDraw.Draw(image).text((0, 0), '中文', font=font, fill='white')
print(json.dumps({{
    'png': True, 'progressive_jpeg': True, 'freetype_cjk': True,
    'supported': features.get_supported()
}}))
"""
    output = _run(
        [str(python), "-I", "-S", "-B", "-c", code], work, "host-smoke", env=env
    )
    return {
        "scope": "bootstrap-host-only-not-production-lpac",
        "result": json.loads(output.splitlines()[-1]),
        "font_sha256": shared._sha256(font),
        "pe_images": native,
    }


def build(output_dir: Path, work_dir: Path | None = None) -> Path:
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeError("use a TLS-capable CPython 3.12 Windows x64 bootstrap")
    import ssl

    import pip

    header = Path(sysconfig.get_path("include")) / "Python.h"
    import_library = Path(sys.base_prefix) / "libs/python312.lib"
    for path in (header, import_library):
        if not path.is_file():
            raise FileNotFoundError(
                "bootstrap CPython development headers/libs are required"
            )
    inventory = _inventory()
    bindings = {
        "recipe_sha256": shared._sha256(Path(__file__)),
        "source_inventory_sha256": shared._sha256(INVENTORY),
        "shared_helpers_sha256": shared._sha256(Path(shared.__file__)),
        "lock_sha256": shared._sha256(ROOT / "uv.lock"),
    }
    output = shared._fresh_path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = shared._fresh_path(
        work_dir or output.with_name(output.name + "-build-" + uuid.uuid4().hex[:8])
    )
    if output.is_relative_to(work) or work.is_relative_to(output):
        raise ValueError("output and build directory must not contain one another")
    work.mkdir(parents=True)
    stage = shared._fresh_path(
        output.with_name("." + output.name + "-staging-" + uuid.uuid4().hex[:8])
    )
    stage.mkdir()
    print(f"Build work directory: {work}", flush=True)
    try:
        msbuild, _, toolchain = shared._toolchain()
        env = _build_environment(msbuild, toolchain, work)
        downloads = work / "downloads"
        downloads.mkdir()
        source_roots = {}
        build_wheels = []
        for record in inventory["sources"]:
            print("Download fixed input: " + record["name"], flush=True)
            destination = downloads / record["filename"]
            _download(record, destination)
            if record["kind"] == "source":
                root = work / record["name"]
                shared._extract_archive(destination, root, record["archive_root"])
                source_roots[record["name"]] = root
            elif record["kind"] in {"license", "source-patch"}:
                source_roots[record["name"]] = destination
            else:
                _wheel_files(destination)
                if record["kind"] == "build-tool-wheel":
                    build_wheels.append(destination)
                else:
                    shared._copy_file(destination, stage / record["filename"])
        source_changes = _apply_freetype_fix(source_roots, inventory)
        build_env = work / "build-env"
        _run(
            [sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(build_env)],
            work,
            "create-build-env",
            env=env,
        )
        _run(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "pip",
                "--python",
                str(build_env),
                "install",
                "--no-index",
                "--no-deps",
                *map(str, build_wheels),
            ],
            work,
            "install-fixed-build-tools",
            env=env,
        )
        python = build_env / "Scripts/python.exe"
        cmake = build_env / "Lib/site-packages/cmake/data/bin/cmake.exe"
        toolchain["cmake_executable_sha256"] = shared._sha256(cmake)
        native_stage = _build_native(work, source_roots, inventory, env, cmake)
        license_receipt = _add_licenses(source_roots, inventory)
        env.update(
            {
                name: str(native_stage)
                for name in ("JPEG_ROOT", "ZLIB_ROOT", "FREETYPE_ROOT")
            }
        )
        env["_LINK_"] = " ".join(inventory["pillow_linker_flags"])
        code = (
            "import sys; sys.path.insert(0, '_custom_build'); import backend; "
            "print(backend.build_wheel("
            + repr(str(stage))
            + ", "
            + repr(inventory["pillow_configuration"])
            + "))"
        )
        _run(
            [str(python), "-B", "-c", code],
            work,
            "pillow-build-wheel",
            env=env,
            cwd=source_roots["pillow"],
        )
        smoke = _host_smoke(python, stage / PILLOW_WHEEL, work, env)
        wheels = []
        for path in sorted(stage.glob("*.whl")):
            original = next(
                (
                    source
                    for source in inventory["sources"]
                    if source["filename"] == path.name
                ),
                None,
            )
            wheels.append(
                {
                    "name": original["name"] if original else "pillow",
                    "version": original["version"] if original else "12.3.0",
                    "filename": path.name,
                    "sha256": shared._sha256(path),
                    "size": path.stat().st_size,
                    "source_kind": "official-wheel" if original else "source-build",
                }
            )
        if {item["name"] for item in wheels} != {
            "pillow",
            "fonttools",
            "charset-normalizer",
        } or len(wheels) != 3:
            raise ValueError(
                "wheelhouse must contain precisely three fixed project wheels"
            )
        current = {
            "recipe_sha256": shared._sha256(Path(__file__)),
            "source_inventory_sha256": shared._sha256(INVENTORY),
            "shared_helpers_sha256": shared._sha256(Path(shared.__file__)),
            "lock_sha256": shared._sha256(ROOT / "uv.lock"),
        }
        if bindings != current:
            raise ValueError("recipe/inventory/helpers/lock changed during the build")
        manifest = {
            "schema_version": 1,
            **bindings,
            "sources": inventory["sources"],
            "source_changes": source_changes,
            "wheels": wheels,
            "scope": inventory["scope"],
            "bit_identical_reproducibility_verified": False,
            "pillow_configuration": inventory["pillow_configuration"],
            "native_configuration": inventory["native_configuration"],
            "toolchain": toolchain,
            "bootstrap": {
                "version": sys.version,
                "executable_sha256": shared._sha256(Path(sys.executable)),
                "python_header_sha256": shared._sha256(header),
                "python_import_library_sha256": shared._sha256(import_library),
                "pip_version": pip.__version__,
                "openssl_version": ssl.OPENSSL_VERSION,
            },
            "licenses": license_receipt,
            "host_smoke": smoke,
        }
        with (stage / "build-manifest.json").open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        shared._fresh_path(output)
        stage.rename(output)
        return output
    except BaseException:
        print(
            f"Build failed; preserve diagnostic work: {work}; "
            f"unpublished stage: {stage}",
            file=sys.stderr,
            flush=True,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    arguments = parser.parse_args()
    result = build(arguments.output_dir, arguments.work_dir)
    print(
        f"Verified host wheelhouse: {result}; production LPAC validation still required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
