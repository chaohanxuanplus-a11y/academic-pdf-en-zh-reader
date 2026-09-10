# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Verify and install the three project-compatible Windows wheels offline.

The upstream lock pins versions and original artifacts, not a locally compiled
Pillow wheel. Its separate build manifest and the installed bytes are checked
here; only the live release workflow can attest to the tested build's origin.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import io
import json
import os
import re
import subprocess
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

if __package__:
    from . import package_python_runtime as integrity
else:
    import package_python_runtime as integrity

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "build-manifest.json"
RECEIPT_NAME = "compatible-dependencies.json"
FONTTOOLS_MANPAGE = "fonttools-4.63.0.data/data/share/man/man1/ttx.1"
EXPECTED = {
    "pillow": ("12.3.0", "source-build", "PIL"),
    "fonttools": ("4.63.0", "official-wheel", "fontTools"),
    "charset-normalizer": ("3.5.1", "official-wheel", "charset_normalizer"),
}


def _members(content: bytes, name: str, version: str) -> dict[str, bytes]:
    prefix = name.replace("-", "_") + "-" + version + ".dist-info"
    allowed = {prefix, EXPECTED[name][2]}
    if name == "pillow":
        allowed.add("pillow.libs")
    members = {}
    seen = set()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if len(archive.infolist()) > 10000:
            raise ValueError("too many wheel members")
        if sum(item.file_size for item in archive.infolist()) > 256 * 1024 * 1024:
            raise ValueError("expanded wheel exceeds size limit")
        for item in archive.infolist():
            member = integrity._member_name(item.filename.rstrip("/"))
            if member.casefold() in seen or (
                member.split("/")[0] not in allowed
                and not (name == "fonttools" and member == FONTTOOLS_MANPAGE)
            ):
                raise ValueError("duplicate or unexpected wheel member")
            seen.add(member.casefold())
            mode = (item.external_attr >> 16) & 0o170000
            if mode not in (0, 0o100000, 0o040000) or item.flag_bits & 1:
                raise ValueError("linked or encrypted wheel member")
            if not item.is_dir():
                members[member] = archive.read(item)
    metadata = BytesParser().parsebytes(members.get(prefix + "/METADATA", b""))
    if metadata.get("Name", "").casefold().replace("_", "-") != name:
        raise ValueError("wheel metadata name mismatch")
    if metadata.get("Version") != version:
        raise ValueError("wheel metadata version mismatch")
    record_name = prefix + "/RECORD"
    if record_name not in members or not any(
        member.startswith(prefix + "/licenses/") for member in members
    ):
        raise ValueError("wheel RECORD or original license texts are missing")
    rows = list(csv.reader(io.StringIO(members[record_name].decode("utf-8"))))
    if len(rows) != len(members) or any(len(row) != 3 for row in rows):
        raise ValueError("wheel RECORD does not cover the exact archive")
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("duplicate wheel RECORD entry")
    for member, digest, size in rows:
        if member not in members:
            raise ValueError("wheel RECORD contains an absent member")
        if member == record_name:
            if digest or size:
                raise ValueError("wheel RECORD must not hash itself")
            continue
        expected = (
            base64.urlsafe_b64encode(bytes.fromhex(integrity._digest(members[member])))
            .decode("ascii")
            .rstrip("=")
        )
        if digest != "sha256=" + expected or size != str(len(members[member])):
            raise ValueError("wheel RECORD hash or size mismatch")
    if name != "pillow" and any(
        Path(member).suffix.casefold() in {".pyd", ".dll", ".exe"} for member in members
    ):
        raise ValueError("pure Python wheel contains native code")
    return members


def validate_wheelhouse(
    wheelhouse: Path, *, repo_root: Path = REPO_ROOT
) -> tuple[dict, dict[str, dict[str, bytes]]]:
    wheelhouse = integrity._checked_path(wheelhouse)
    inventory_bytes = integrity._read_file(
        repo_root / "compliance/python-dependencies.json"
    )
    inventory = integrity._json(inventory_bytes)
    manifest = integrity._json(integrity._read_file(wheelhouse / MANIFEST_NAME))
    expected = {
        "recipe_sha256": integrity._digest(
            integrity._read_file(repo_root / "scripts/build_compatible_dependencies.py")
        ),
        "shared_helpers_sha256": integrity._digest(
            integrity._read_file(repo_root / "scripts/build_compatible_python.py")
        ),
        "source_inventory_sha256": integrity._digest(inventory_bytes),
        "lock_sha256": integrity._digest(integrity._read_file(repo_root / "uv.lock")),
        "sources": inventory.get("sources"),
        "source_changes": inventory.get("source_changes"),
    }
    if (
        type(inventory.get("schema_version")) is not int
        or inventory["schema_version"] != 1
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or not expected["sources"]
        or any(manifest.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("wheel build manifest differs from the reviewed recipe/inputs")
    wheels = manifest.get("wheels")
    if not isinstance(wheels, list) or len(wheels) != len(EXPECTED):
        raise ValueError("exactly three compatible wheels are required")
    lock = tomllib.loads((repo_root / "uv.lock").read_text(encoding="utf-8"))
    originals = {
        (package["name"], package["version"], Path(item["url"]).name): item["hash"]
        for package in lock["package"]
        for item in package.get("wheels", [])
    }
    contents = {}
    files = {MANIFEST_NAME}
    for wheel in wheels:
        if not isinstance(wheel, dict) or wheel.get("name") not in EXPECTED:
            raise ValueError("unapproved compatible wheel")
        name = wheel["name"]
        version, source_kind, _ = EXPECTED[name]
        filename = integrity._member_name(wheel.get("filename"))
        if (
            name in contents
            or "/" in filename
            or not filename.endswith(".whl")
            or wheel.get("version") != version
            or wheel.get("source_kind") != source_kind
            or type(wheel.get("size")) is not int
        ):
            raise ValueError("compatible wheel identity mismatch")
        raw = integrity._read_file(wheelhouse / filename)
        if len(raw) != wheel["size"] or integrity._digest(raw) != wheel.get("sha256"):
            raise ValueError("compatible wheel size/hash mismatch")
        if (
            source_kind == "official-wheel"
            and originals.get((name, version, filename)) != "sha256:" + wheel["sha256"]
        ):
            raise ValueError("official pure wheel differs from the upstream lock")
        contents[name] = _members(raw, name, version)
        files.add(filename)
    if set(contents) != set(EXPECTED) or set(integrity._inventory(wheelhouse)) != files:
        raise ValueError("wheelhouse contains missing or extra files")
    return manifest, contents


def _environment(python: Path, repo_root: Path) -> tuple[Path, dict]:
    python = integrity._checked_path(python)
    venv = python.parent.parent
    if python.parent.name != "Scripts" or not (venv / "pyvenv.cfg").is_file():
        raise ValueError("an explicit Windows virtual-environment python is required")
    command = (
        "import json,sys,struct; print(json.dumps(dict("
        "version=list(sys.version_info[:3]),bits=struct.calcsize('P')*8,"
        "prefix=sys.prefix,base=sys.base_prefix)))"
    )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    state = json.loads(result.stdout)
    if (
        state.get("version") != [3, 12, 14]
        or state.get("bits") != 64
        or Path(state.get("prefix", "")).resolve() != venv.resolve()
        or Path(state.get("base", "")).resolve() == venv.resolve()
    ):
        raise ValueError("virtual environment is not using the compatible x64 base")
    base = integrity._checked_path(Path(state["base"]))
    _, _, runtime_manifest = integrity._validate(base, repo_root)
    return venv, {
        "python_version": "3.12.14",
        "runtime_manifest_sha256": integrity._digest(runtime_manifest),
    }


def _verify_installed_record(site: Path, members: dict[str, bytes]) -> None:
    record_name = next(name for name in members if name.endswith(".dist-info/RECORD"))
    prefix = record_name.rsplit("/", 1)[0]
    original = {
        ("../../share/man/man1/ttx.1" if row[0] == FONTTOOLS_MANPAGE else row[0]): row[
            1:
        ]
        for row in csv.reader(io.StringIO(members[record_name].decode()))
    }
    rows = list(
        csv.reader(io.StringIO(integrity._read_file(site / record_name).decode()))
    )
    if any(len(row) != 3 for row in rows) or len({row[0] for row in rows}) != len(rows):
        raise ValueError("installed RECORD has malformed or duplicate entries")
    installed = {row[0]: row[1:] for row in rows}
    if any(installed.get(name) != record for name, record in original.items()):
        raise ValueError("installed RECORD differs from the original wheel members")
    generated = {
        prefix + "/" + name: site / prefix / name
        for name in ("INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json")
    }
    entrypoints = members.get(prefix + "/entry_points.txt")
    if entrypoints:
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read_string(entrypoints.decode())
        for group in ("console_scripts", "gui_scripts"):
            for name in config[group] if config.has_section(group) else ():
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
                    raise ValueError("invalid wheel entry point")
                for suffix in (".exe", "-script.py"):
                    generated["../../Scripts/" + name + suffix] = (
                        site.parent.parent / "Scripts" / (name + suffix)
                    )
    for name in installed.keys() - original.keys():
        if name not in generated:
            raise ValueError("installed RECORD admits an unreviewed member")
        raw = integrity._read_file(generated[name])
        digest = (
            base64.urlsafe_b64encode(bytes.fromhex(integrity._digest(raw)))
            .decode()
            .rstrip("=")
        )
        if installed[name] != ["sha256=" + digest, str(len(raw))]:
            raise ValueError("installed RECORD generated-member hash mismatch")


def verify_installed(venv: Path, contents: dict[str, dict[str, bytes]]) -> None:
    site = venv / "Lib/site-packages"
    for name, members in contents.items():
        for member, expected in members.items():
            if member.endswith(".dist-info/RECORD"):
                continue  # Installers legitimately add their generated entry points.
            installed_path = (
                venv / "share/man/man1/ttx.1"
                if member == FONTTOOLS_MANPAGE
                else site.joinpath(*member.split("/"))
            )
            if integrity._read_file(installed_path) != expected:
                raise ValueError(f"installed wheel member mismatch: {member}")
        _verify_installed_record(site, members)
        roots = [site / EXPECTED[name][2]]
        if name == "pillow" and os.path.lexists(site / "pillow.libs"):
            roots.append(site / "pillow.libs")
        for package_root in roots:
            for relative in integrity._inventory(package_root):
                member = package_root.name + "/" + relative
                if (
                    "__pycache__" in Path(relative).parts
                    and Path(relative).suffix == ".pyc"
                ):
                    continue  # The production copier never copies Python bytecode.
                if member not in members:
                    raise ValueError(f"unexpected installed package file: {member}")


def _write_receipt(path: Path, receipt: dict) -> None:
    integrity._checked_path(path)
    if os.path.lexists(path):
        integrity._file_info(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".compatible-receipt-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(receipt, indent=2) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(
    wheelhouse: Path,
    python: Path,
    uv: str,
    *,
    verify_only: bool = False,
    repo_root: Path = REPO_ROOT,
) -> dict:
    manifest, contents = validate_wheelhouse(wheelhouse, repo_root=repo_root)
    venv, runtime = _environment(python, repo_root)
    receipt = {
        "schema_version": 1,
        **runtime,
        "build_manifest_sha256": integrity._digest(
            integrity._read_file(wheelhouse / MANIFEST_NAME)
        ),
        "installer_sha256": integrity._digest(
            integrity._read_file(
                repo_root / "scripts/install_compatible_dependencies.py"
            )
        ),
        "lock_sha256": manifest["lock_sha256"],
        "wheels": manifest["wheels"],
    }
    receipt_path = venv / RECEIPT_NAME
    integrity._checked_path(receipt_path)
    if os.path.lexists(receipt_path):
        integrity._file_info(receipt_path)
    if not verify_only:
        # Temporary requirements have precise local wheel URLs and mandatory hashes;
        # no registry or source build is consulted during this installation.
        with tempfile.TemporaryDirectory(
            prefix="compatible-install-", dir=venv
        ) as temp:
            requirements = Path(temp) / "requirements.txt"
            requirements.write_text(
                "\n".join(
                    f"{wheel['name']} @ "
                    f"{(wheelhouse / wheel['filename']).resolve().as_uri()} "
                    f"--hash=sha256:{wheel['sha256']}"
                    for wheel in manifest["wheels"]
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--offline",
                    "--no-index",
                    "--no-deps",
                    "--no-build",
                    "--no-cache",
                    "--link-mode",
                    "copy",
                    "--reinstall",
                    "--require-hashes",
                    "-r",
                    str(requirements),
                ],
                check=True,
                timeout=180,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        validate_wheelhouse(wheelhouse, repo_root=repo_root)
        verify_installed(venv, contents)
        _write_receipt(receipt_path, receipt)
    else:
        verify_installed(venv, contents)
        if integrity._json(integrity._read_file(receipt_path)) != receipt:
            raise ValueError(
                "installed compatibility receipt differs from the tested inputs"
            )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        install(args.wheelhouse, args.python, args.uv, verify_only=args.verify_only)
    except (
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as exc:
        print(f"compatible dependencies: BLOCKED: {exc}")
        return 1
    print(
        "compatible dependencies: verified"
        if args.verify_only
        else "compatible dependencies: installed and verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
