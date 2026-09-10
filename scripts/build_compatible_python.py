# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Build the fixed, minimal Windows project interpreter; never install system tools.

Run with Windows x64 Python 3.12 and VS 2022/2026 with MSVC 14.44 (v143).
The output is a conventional base interpreter, not a complete CPython distribution.
Failure preserves the work directory. Successful output is committed by rename only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

INVENTORY_PATH = Path(__file__).resolve().parents[1] / "compliance/python-runtime.json"
SDK_VERSION = "10.0.26100.0"
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
EXTENSIONS = tuple(target for target in TARGETS[4:] if target != "sqlite3")
SQLITE_DIRECTORY = "sqlite-3.53.4.0"
SOURCES = (
    {
        "name": "cpython",
        "version": "3.12.14",
        "kind": "source",
        "url": "https://www.python.org/ftp/python/3.12.14/Python-3.12.14.tgz",
        "sha256": "6c6df908d2c3fd24e6d76869e92542abd0f33aec9dfc18df8875f89660286d43",
        "archive_root": "Python-3.12.14",
        "filename": "Python-3.12.14.tgz",
        "license": "PSF-2.0",
        "license_file": "LICENSE",
    },
    {
        "name": "zlib",
        "version": "1.3.1",
        "kind": "source",
        "url": "https://codeload.github.com/python/cpython-source-deps/zip/"
        "4dc98e1909830e2bdc2a9cc2236e3c5d5037335b",
        "sha256": "39d867c083ebc37c77cf03a442f87c0f83db006eb64343bd4a19f1c84c26f50c",
        "archive_root": "cpython-source-deps-4dc98e1909830e2bdc2a9cc2236e3c5d5037335b",
        "filename": "zlib-1.3.1.zip",
        "license": "Zlib",
        "license_file": "LICENSE",
    },
    {
        "name": "libffi",
        "version": "3.4.4",
        "kind": "upstream-binary-dependency",
        "url": "https://codeload.github.com/python/cpython-bin-deps/zip/"
        "94cb9a1c7feb608adf2b9f8fe2dbd6925ffbf90d",
        "sha256": "6b0f53490e0dd958708bff32df689cf2163c214147fcb8edb76a403fa6a877ca",
        "archive_root": "cpython-bin-deps-94cb9a1c7feb608adf2b9f8fe2dbd6925ffbf90d",
        "filename": "libffi-3.4.4.zip",
        "license": "MIT",
        "license_file": "LICENSE",
    },
    {
        "name": "sqlite3",
        "version": "3.53.4",
        "kind": "source",
        "url": "https://codeload.github.com/python/cpython-source-deps/zip/"
        "f048e6a40879da9828ca2cf3b260f3e0a26117fb",
        "sha256": "0c604ef459fad52cac8a6b0782a6dd4799bca5680053a99aa531d491f588ae3e",
        "archive_root": "cpython-source-deps-f048e6a40879da9828ca2cf3b260f3e0a26117fb",
        "filename": "sqlite-3.53.4.0.zip",
        "build_directory": SQLITE_DIRECTORY,
        "license": "blessing",
        "license_file": "sqlite3.h",
        "sqlite3_c_sha3_256": (
            "67f423e9ebbbdc473cbc4772c872ee6b89f31fde4ed0279a5c25d5f65c043a16"
        ),
    },
)
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 20000
PATCH_ORIGINAL = b'2 RT_MANIFEST "python.manifest"'
PATCH_REPLACEMENT = (
    b"// Project compatibility build: omit shared-DLL SxS manifest resource."
)
EXCLUDED_LIB_PARTS = {
    "__pycache__",
    "test",
    "tests",
    "site-packages",
    "ensurepip",
    "idlelib",
    "tkinter",
    "turtledemo",
}
BUILD_PROPERTIES = (
    "/p:Configuration=Release",
    "/p:Platform=x64",
    "/p:PlatformToolset=v143",
    f"/p:WindowsTargetPlatformVersion={SDK_VERSION}",
    "/p:EnableControlFlowGuard=Guard",
    "/p:KillPython=false",
    "/p:IncludeExternals=true",
    "/p:IncludeCTypes=false",
    "/p:IncludeSSL=false",
    "/p:IncludeTkinter=false",
    "/p:IncludeTests=false",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _checked_path(path: Path) -> Path:
    if ".." in path.parts or any(char in str(path) for char in ';"\r\n'):
        raise ValueError("ambiguous output/build path")
    path = path.absolute()
    for ancestor in (path, *path.parents):
        if _is_reparse(ancestor):
            raise ValueError(f"reparse path is forbidden: {ancestor}")
    return path


def _fresh_path(path: Path) -> Path:
    path = _checked_path(path)
    if path.exists():
        raise FileExistsError(f"path already exists; use a fresh path: {path}")
    return path


def _archive_member(name: str, archive_root: str) -> PurePosixPath:
    if not name or name.startswith("/") or "\\" in name:
        raise ValueError("unsafe archive member path")
    parts = name.rstrip("/").split("/")
    if parts[0] != archive_root:
        raise ValueError("archive member is outside the pinned root")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or ":" in part
            or part.endswith((".", " "))
            or any(ord(char) < 32 for char in part)
            or any(char in part for char in '<>"|?*')
            or re.fullmatch(
                r"(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])", part.split(".")[0]
            )
        ):
            raise ValueError("unsafe Windows archive member path")
    return PurePosixPath(*parts[1:])


def _extract_archive(archive: Path, destination: Path, archive_root: str) -> None:
    destination = _fresh_path(destination)
    is_zip = archive.suffix == ".zip"
    opener = zipfile.ZipFile if is_zip else tarfile.open
    with opener(archive, "r") as handle:
        members = handle.infolist() if is_zip else handle.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError("archive member count exceeds limit")
        checked = []
        entries: dict[str, bool] = {}
        total = 0
        for member in members:
            name = member.filename if is_zip else member.name
            relative = _archive_member(name, archive_root)
            directory = member.is_dir() if is_zip else member.isdir()
            size = member.file_size if is_zip else member.size
            if is_zip:
                mode = stat.S_IFMT(member.external_attr >> 16)
                regular = mode in {0, stat.S_IFREG, stat.S_IFDIR}
                regular = regular and not (member.flag_bits & 1)
                regular = regular and not (member.external_attr & 0x400)
            else:
                regular = member.isfile() or directory
            if not regular:
                raise ValueError("only regular files and directories are admitted")
            total += size
            if size < 0 or size > MAX_MEMBER_BYTES or total > MAX_EXTRACTED_BYTES:
                raise ValueError("archive uncompressed size exceeds limit")
            key = relative.as_posix().casefold()
            if key in entries:
                raise ValueError("archive contains colliding paths")
            entries[key] = directory
            checked.append((member, relative, directory))
        for _, relative, _ in checked:
            for parent in relative.parents:
                if entries.get(parent.as_posix().casefold()) is False:
                    raise ValueError("archive file conflicts with directory")
        destination.mkdir(parents=True, exist_ok=False)
        for member, relative, directory in checked:
            target = destination.joinpath(*relative.parts)
            _checked_path(target)
            if directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = handle.open(member) if is_zip else handle.extractfile(member)
            if source is None:
                raise ValueError("missing regular file content")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("pinned downloads must not redirect")


def _download(record: dict[str, str], destination: Path) -> None:
    if record not in SOURCES:
        raise ValueError("download is not one of the pinned sources")
    destination = _fresh_path(destination)
    digest = hashlib.sha256()
    opener = urllib.request.build_opener(_NoRedirect())
    with (
        opener.open(record["url"], timeout=60) as response,
        destination.open("xb") as out,
    ):
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError("download exceeds size limit")
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != record["sha256"]:
        raise ValueError("download SHA-256 does not match pinned input")


def _patch_resources(resource: Path) -> dict[str, str]:
    content = resource.read_bytes()
    if content.count(PATCH_ORIGINAL) != 1:
        raise ValueError("expected DLL manifest resource must occur exactly once")
    before = _sha256(resource)
    resource.write_bytes(content.replace(PATCH_ORIGINAL, PATCH_REPLACEMENT))
    return {
        "path": f"PC/{resource.name}",
        "before_sha256": before,
        "after_sha256": _sha256(resource),
    }


def _print_build_failure_tail(log: Path) -> None:
    """Emit at most 16 KiB / 80 lines after the fixed build's log writer closes."""
    limit = 16 * 1024
    try:
        with log.open("rb") as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(0, source.tell() - limit))
            raw = source.read(limit)
        tail = "\n".join(raw.decode("utf-8", errors="replace").splitlines()[-80:])
        # Replacement characters may expand invalid input; bound UTF-8 output too.
        # Reserve the final newline and Windows CRLF expansion for up to 80 lines.
        tail = tail.encode("utf-8")[-(limit - 81) :].decode("utf-8", errors="ignore")
    except OSError:
        tail = "Build failure log tail is unavailable."
    # Failure to print diagnostics must not replace the build failure.
    with suppress(OSError, UnicodeError, ValueError):
        print(tail, file=sys.stderr, flush=True)


def _run(arguments: list[str], *, env=None, log: Path | None = None) -> str:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    if log is not None:
        try:
            with log.open("xb") as output:
                subprocess.run(
                    arguments,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=env,
                    **options,
                )
        except subprocess.CalledProcessError:
            _print_build_failure_tail(log)
            raise
        return ""
    return subprocess.run(
        arguments,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **options,
    ).stdout.strip()


def _toolchain() -> tuple[Path, dict[str, str], dict[str, str]]:
    if any(os.environ.get(name) for name in ("CL", "_CL_", "LINK", "_LINK_")):
        raise ValueError("compiler/linker environment overrides are forbidden")
    program_files = Path(os.environ["PROGRAMFILES(X86)"])
    vswhere = program_files / "Microsoft Visual Studio/Installer/vswhere.exe"
    instances = json.loads(
        _run(
            [
                str(vswhere),
                "-products",
                "*",
                "-version",
                "[17.0,19.0)",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-format",
                "json",
                "-utf8",
            ]
        )
    )
    for instance in instances:
        if not re.fullmatch(
            r"(?:17|18)\.\d+\.\d+\.\d+", instance["installationVersion"]
        ):
            continue
        installation = Path(instance["installationPath"])
        # VS 2026 can carry v143 side by side with its newer default compiler.
        # Never infer the toolset from the IDE version or the default-version file.
        versions = [
            path.name
            for path in (installation / "VC/Tools/MSVC").glob("14.44.*")
            if re.fullmatch(r"14\.44\.\d+", path.name)
            and (path / "bin/Hostx64/x64/cl.exe").is_file()
        ]
        if versions:
            version = max(versions, key=lambda value: int(value.rsplit(".", 1)[1]))
            break
    else:
        raise RuntimeError(
            "Visual Studio 2022/2026 with the MSVC 14.44 v143 toolset is required"
        )
    msbuild = installation / "MSBuild/Current/Bin/MSBuild.exe"
    compiler = installation / "VC/Tools/MSVC" / version / "bin/Hostx64/x64/cl.exe"
    sdk_bin = program_files / "Windows Kits/10/bin" / SDK_VERSION / "x64"
    for required in (msbuild, compiler, sdk_bin / "rc.exe", sdk_bin / "mt.exe"):
        if not required.is_file():
            raise FileNotFoundError(
                f"required installed build tool is missing: {required}"
            )
    environment = dict(os.environ)
    environment["PATH"] = str(sdk_bin) + os.pathsep + environment.get("PATH", "")
    identity = {
        "visual_studio_version": instance["installationVersion"],
        "vctools_version": version,
        "platform_toolset": "v143",
        "sdk_version": SDK_VERSION,
        "msbuild_version": _run([str(msbuild), "-version", "-nologo"]),
        "msbuild_sha256": _sha256(msbuild),
        "compiler_sha256": _sha256(compiler),
        "resource_compiler_sha256": _sha256(sdk_bin / "rc.exe"),
    }
    return msbuild, environment, identity


def _copy_file(source: Path, target: Path) -> None:
    _checked_path(source)
    _checked_path(target)
    if not source.is_file():
        raise FileNotFoundError(f"required runtime member missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if _sha256(source) != _sha256(target):
        raise ValueError("runtime copy failed integrity verification")


def _assemble(source: Path, work: Path, stage: Path) -> None:
    built = source / "PCbuild/amd64"
    stage.mkdir(exist_ok=False)
    for name in (
        "python.exe",
        "pythonw.exe",
        "python3.dll",
        "python312.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "LICENSE.txt",
    ):
        _copy_file(built / name, stage / name)
    for name in EXTENSIONS:
        _copy_file(built / f"{name}.pyd", stage / "DLLs" / f"{name}.pyd")
    _copy_file(built / "libffi-8.dll", stage / "DLLs/libffi-8.dll")
    _copy_file(built / "sqlite3.dll", stage / "DLLs/sqlite3.dll")
    for path in sorted((source / "Lib").rglob("*")):
        relative = path.relative_to(source / "Lib")
        if set(relative.parts) & EXCLUDED_LIB_PARTS:
            continue
        _checked_path(path)
        if path.is_file() and path.suffix.casefold() != ".pyc":
            _copy_file(path, stage / "Lib" / relative)
    notices = {
        "CPython-LICENSE.txt": source / "LICENSE",
        "CPython-upstream-license-reference.rst": source / "Doc/license.rst",
        "Microsoft-runtime-notice.txt": source / "PC/crtlicense.txt",
        "zlib-LICENSE.txt": work / "zlib/LICENSE",
        "libffi-LICENSE.txt": work / "libffi/LICENSE",
        "expat-COPYING.txt": source / "Modules/expat/COPYING",
    }
    for name, original in notices.items():
        _copy_file(original, stage / "licenses" / name)
    sqlite_header = (work / SQLITE_DIRECTORY / "sqlite3.h").read_bytes()
    sqlite_notice = sqlite_header.split(b"*/", 1)[0] + b"*/\n"
    if (
        not sqlite_notice.startswith(b"/*")
        or b"disclaims copyright" not in sqlite_notice
    ):
        raise ValueError("SQLite public-domain source notice was not found")
    (stage / "licenses/sqlite3-LICENSE.txt").write_bytes(sqlite_notice)
    # Preserve verbatim license headers of the embedded HACL implementation.
    headers = set()
    for path in sorted((source / "Modules/_hacl").rglob("*")):
        if path.suffix in {".c", ".h"}:
            content = path.read_bytes()
            if content.startswith(b"/*") and b"*/" in content:
                header = content.split(b"*/", 1)[0] + b"*/"
                if b"Copyright" in header or b"License" in header:
                    headers.add(header)
    if not headers:
        raise ValueError("embedded HACL license headers were not found")
    (stage / "licenses/HACL-source-license-headers.txt").write_bytes(
        b"\n\n".join(sorted(headers)) + b"\n"
    )
    (stage / "CHANGES-project-runtime.txt").write_text(
        "Project-specific CPython 3.12.14 compatibility build.\n\n"
        "Only PC/python_nt.rc and PC/sqlite3.rc are intentionally changed:\n"
        "their shared-DLL RT_MANIFEST resource lines are omitted before compilation.\n"
        "The C implementations,\n"
        "EXE manifests and CPython APIs are unchanged. Signed upstream DLLs are\n"
        "not edited. ASLR, DEP and CFG remain enabled. No sandbox capabilities\n"
        "are granted.\n\n"
        "This is a minimal project base interpreter, not a full CPython distribution.\n"
        "SSL, tkinter, ensurepip, tests and other optional extensions are absent.\n"
        "The host import smoke test is not LPAC or production-render certification.\n"
        "libffi is an immutable upstream binary dependency, not locally rebuilt.\n"
        "SQLite 3.53.4 source replaces the upstream default 3.49.1 dependency via\n"
        "sqlite3Dir, including upstream security fixes; its C code is unchanged.\n"
        "Microsoft runtime DLLs are copied by upstream MSBuild from installed redist.\n"
        "SDK and Visual Studio Build Tools are not included.\n\n"
        "Sources and build identity: build-manifest.json. Input hashes are pinned;\n"
        "bit-identical builds have NOT been verified (build timestamps vary).\n"
        "LICENSE.txt includes CPython and upstream Windows runtime conditions. The\n"
        "licenses directory preserves actual dependency notices and upstream license\n"
        "reference; that reference also describes optional modules not present here.\n",
        encoding="utf-8",
        newline="\n",
    )


def _file_manifest(directory: Path) -> list[dict[str, object]]:
    result = []
    for path in sorted(directory.rglob("*")):
        _checked_path(path)
        if path.is_file():
            result.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": _sha256(path),
                    "size": path.stat().st_size,
                }
            )
    return result


def build(output_dir: Path, work_dir: Path | None = None) -> Path:
    if os.name != "nt" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        raise RuntimeError("this recipe requires Windows x64")
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise RuntimeError("bootstrap must be an existing x64 Python 3.12")
    output = _fresh_path(output_dir)
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    if inventory["sources"] != list(SOURCES) or inventory["targets"] != list(TARGETS):
        raise ValueError("source inventory does not match the fixed build recipe")
    output.parent.mkdir(parents=True, exist_ok=True)
    if work_dir is None:
        work = Path(
            tempfile.mkdtemp(prefix="compatible-python-build-", dir=output.parent)
        )
    else:
        work = _fresh_path(work_dir)
        if work.is_relative_to(output) or output.is_relative_to(work):
            raise ValueError("work and output directories must not overlap")
        if work.anchor.casefold() != output.anchor.casefold():
            raise ValueError("work and output must share a volume for atomic rename")
        work.mkdir(parents=True, exist_ok=False)
    print(f"Build work directory (preserved): {work}", flush=True)
    try:
        msbuild, environment, toolchain = _toolchain()
        for record in SOURCES:
            archive = work / record["filename"]
            _download(record, archive)
            source_dir = work / record.get("build_directory", record["name"])
            _extract_archive(archive, source_dir, record["archive_root"])
            if record["name"] == "sqlite3":
                digest = hashlib.sha3_256((source_dir / "sqlite3.c").read_bytes())
                if digest.hexdigest() != record["sqlite3_c_sha3_256"]:
                    raise ValueError("SQLite source differs from official release hash")
        source = work / "cpython"
        patches = [
            _patch_resources(source / name)
            for name in ("PC/python_nt.rc", "PC/sqlite3.rc")
        ]
        executable_resources = [
            {"path": name, "sha256": _sha256(source / name)}
            for name in ("PC/python_exe.rc", "PC/python.manifest")
        ]
        for target in TARGETS:
            print(f"Building {target}", flush=True)
            arguments = [
                str(msbuild),
                str(source / "PCbuild" / f"{target}.vcxproj"),
                "/t:Build",
                "/m:2",
                "/nologo",
                "/v:minimal",
                *BUILD_PROPERTIES,
                f"/p:VCToolsVersion={toolchain['vctools_version']}",
                f"/p:PythonForBuild={sys.executable}",
                f"/p:zlibDir={work / 'zlib'}\\",
                f"/p:libffiDir={work / 'libffi'}\\",
                f"/p:sqlite3Dir={work / SQLITE_DIRECTORY}\\",
            ]
            if target in EXTENSIONS:
                arguments.append("/p:BuildProjectReferences=false")
            _run(arguments, env=environment, log=work / f"{target}-build.log")
        stage = work / "runtime-stage"
        _assemble(source, work, stage)
        smoke = _run(
            [
                str(stage / "python.exe"),
                "-I",
                "-B",
                "-c",
                "import sys,json,ctypes,socket,select,pyexpat,unicodedata;"
                "import zlib,hashlib,venv,unittest.mock,asyncio,sqlite3;"
                "import multiprocessing,time;"
                "assert sys.version_info[:3] == (3,12,14);"
                "assert sqlite3.sqlite_version == '3.53.4';"
                "db=sqlite3.connect(':memory:');"
                "db.execute('CREATE TABLE smoke (value TEXT NOT NULL)');"
                "db.execute('INSERT INTO smoke VALUES (?)',('verified',));"
                "row=db.execute('SELECT value FROM smoke').fetchone();"
                "assert row==('verified',);"
                "assert db.execute('PRAGMA integrity_check').fetchone()==('ok',);"
                "db.close();"
                "ctx=multiprocessing.get_context('spawn');event=ctx.Event();"
                "event.set();assert event.is_set();"
                "child=ctx.Process(target=time.sleep,args=(0,));"
                "child.start();child.join(20);"
                "child.terminate() if child.is_alive() else None;child.join(5);"
                "assert child.exitcode == 0;child.close();"
                "print(json.dumps({'version':sys.version,'scope':'host-smoke-only',"
                "'sqlite_version':sqlite3.sqlite_version,'sqlite_integrity':'ok',"
                "'multiprocessing_spawn_exitcode':0}))",
            ]
        )
        manifest = {
            "schema_version": 1,
            "runtime": "project-cpython-windows-x64",
            "version": "3.12.14",
            "created_utc": datetime.now(UTC).isoformat(),
            "bootstrap_version": sys.version,
            "toolchain": toolchain,
            "sources": list(SOURCES),
            "source_changes": patches,
            "unchanged_executable_resources": executable_resources,
            "targets": list(TARGETS),
            "build_properties": list(BUILD_PROPERTIES),
            "recipe_sha256": _sha256(Path(__file__)),
            "source_inventory_sha256": _sha256(INVENTORY_PATH),
            "host_smoke": json.loads(smoke),
            "lpac_verified_by_this_script": False,
            "bit_identical_reproducibility_verified": False,
            "files_exclude": ["build-manifest.json"],
            "files": _file_manifest(stage),
        }
        (stage / "build-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _fresh_path(output)
        stage.rename(output)
    except Exception:
        print(f"Build failed; diagnostics preserved at: {work}", file=sys.stderr)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    try:
        output = build(args.output_dir, args.work_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Compatible Python build failed: {error}", file=sys.stderr)
        return 1
    print(f"Compatible project interpreter: {output / 'python.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
