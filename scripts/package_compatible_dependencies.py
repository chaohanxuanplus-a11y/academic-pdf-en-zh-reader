# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Package the exact wheelhouse verified against the tested Windows environment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from . import install_compatible_dependencies as dependencies
else:
    import install_compatible_dependencies as dependencies

integrity = dependencies.integrity
REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "academic-pdf-en-zh-reader-windows-dependencies.zip"


def package_dependencies(
    wheelhouse: Path, python: Path, output_dir: Path, *, repo_root: Path = REPO_ROOT
) -> Path:
    output_dir = integrity._checked_path(output_dir)
    if output_dir.exists():
        raise FileExistsError("dependency package output already exists")
    receipt = dependencies.install(
        wheelhouse, python, "uv", verify_only=True, repo_root=repo_root
    )
    manifest, _ = dependencies.validate_wheelhouse(wheelhouse, repo_root=repo_root)
    raw_manifest = integrity._read_file(wheelhouse / dependencies.MANIFEST_NAME)
    if integrity._digest(raw_manifest) != receipt["build_manifest_sha256"]:
        raise ValueError("wheel manifest changed after installed verification")
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    files = {
        "compatible-dependencies/build-manifest.json": raw_manifest,
        "dependency-installation.json": receipt_bytes,
    }
    for wheel in manifest["wheels"]:
        content = integrity._read_file(wheelhouse / wheel["filename"])
        if (
            len(content) != wheel["size"]
            or integrity._digest(content) != wheel["sha256"]
        ):
            raise ValueError("wheel changed after installed verification")
        files["compatible-dependencies/" + wheel["filename"]] = content
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="dependency-package-", dir=output_dir.parent
    ) as temp:
        stage = Path(temp) / "artifacts"
        stage.mkdir()
        archive_path = stage / ARCHIVE_NAME
        with zipfile.ZipFile(
            archive_path, "x", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for name, content in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content, compresslevel=9)
        archive_bytes = integrity._read_file(archive_path)
        artifact = {
            "schema_version": 1,
            "archive": {
                "path": ARCHIVE_NAME,
                "size": len(archive_bytes),
                "sha256": integrity._digest(archive_bytes),
                "member_count": len(files),
            },
            "build_manifest_sha256": integrity._digest(raw_manifest),
            "installation_receipt_sha256": integrity._digest(receipt_bytes),
            "runtime_manifest_sha256": receipt["runtime_manifest_sha256"],
            "lock_sha256": manifest["lock_sha256"],
            "recipe_sha256": manifest["recipe_sha256"],
            "source_inventory_sha256": manifest["source_inventory_sha256"],
            "packager_sha256": integrity._digest(
                integrity._read_file(
                    repo_root / "scripts/package_compatible_dependencies.py"
                )
            ),
        }
        (stage / "dependencies-artifact.json").write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.rename(stage, output_dir)
    return output_dir / ARCHIVE_NAME


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(package_dependencies(args.wheelhouse, args.python, args.output_dir))
    except (
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as exc:
        print(f"compatible dependency package: BLOCKED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
