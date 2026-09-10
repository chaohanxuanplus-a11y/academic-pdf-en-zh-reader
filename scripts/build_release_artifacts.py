# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from .check_release_readiness import assess_release_readiness
else:
    from check_release_readiness import assess_release_readiness

COMMON_PATHS = (
    ".python-version",
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "DISCLAIMER.md",
    "LICENSE",
    "LICENSES",
    "NOTICE",
    "PRIVACY.md",
    "README.md",
    "REUSE.toml",
    "SECURITY.md",
    "TEST_DATA_ATTRIBUTION.md",
    "THIRD_PARTY_NOTICES.md",
    "UPSTREAMS.md",
    "assets",
    "compliance/dependencies.json",
    "compliance/evidence/public",
    "compliance/external-tools.json",
    "compliance/project-identity.json",
    "compliance/python-runtime.json",
    "compliance/python-dependencies.json",
    "compliance/release-status.json",
    "pyproject.toml",
    "references",
    "src",
    "uv.lock",
)
RUNTIME_SCRIPTS = (
    "scripts/agent_artifacts.py",
    "scripts/build_compatible_python.py",
    "scripts/build_compatible_dependencies.py",
    "scripts/install_compatible_dependencies.py",
    "scripts/package_compatible_dependencies.py",
    "scripts/package_python_runtime.py",
    "scripts/compose_pdf.py",
    "scripts/corrections.py",
    "scripts/deliver.py",
    "scripts/extract.py",
    "scripts/finish_job.py",
    "scripts/preflight.py",
    "scripts/prepare_job.py",
    "scripts/qa_pdf.py",
    "scripts/solve_layout.py",
    "scripts/validate_translation.py",
)
FORBIDDEN_SUFFIXES = {
    ".corrections.sqlite3",
    ".db",
    ".env",
    ".key",
    ".pdf",
    ".pem",
    ".sqlite",
    ".sqlite3",
}
DEVELOPMENT_BLOCK_MARKER = b"PUBLIC_RELEASE_" + b"BLOCKED"
ARCHIVE_ROOT = "academic-pdf-en-zh-reader"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(root: Path, entries: tuple[str, ...]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for entry in entries:
        path = root / entry
        if not path.exists():
            raise ValueError(f"required release member is missing: {entry}")
        if path.is_symlink():
            raise ValueError(f"release members must not be symlinks: {entry}")
        if path.is_file():
            files.add(path)
            continue
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ValueError(
                    "release directories must not contain symlinks: "
                    f"{child.relative_to(root).as_posix()}"
                )
            if child.is_file():
                files.add(child)
    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def _validate_members(root: Path, files: tuple[Path, ...]) -> None:
    for path in files:
        relative = path.relative_to(root).as_posix()
        lowered = relative.casefold()
        if any(lowered.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            raise ValueError(f"private or user-document file is forbidden: {relative}")
        if DEVELOPMENT_BLOCK_MARKER in path.read_bytes():
            raise ValueError(f"development release marker found in: {relative}")


def _validate_assets(root: Path) -> tuple[dict[str, object], ...]:
    font_manifest = json.loads(
        (root / "assets" / "font-manifest.json").read_text(encoding="utf-8")
    )
    font_records = {item["path"]: item for item in font_manifest["fonts"]}
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    records: list[dict[str, object]] = []
    for path in _collect(root, ("assets",)):
        relative = path.relative_to(root).as_posix()
        digest = _sha256(path)
        record: dict[str, object] = {
            "path": relative,
            "sha256": digest,
            "size": path.stat().st_size,
        }
        if path.suffix.casefold() == ".ttf":
            admitted = font_records.get(relative)
            if admitted is None:
                raise ValueError(f"font is absent from font-manifest.json: {relative}")
            if (
                admitted.get("sha256") != digest
                or admitted.get("size") != path.stat().st_size
            ):
                raise ValueError(f"font integrity differs from manifest: {relative}")
            if path.name not in notices:
                raise ValueError(
                    f"font is absent from THIRD_PARTY_NOTICES.md: {relative}"
                )
            record["spdx_license"] = admitted.get("spdx_license")
            record["source_url"] = admitted.get("source_url")
        records.append(record)
    return tuple(records)


def _write_zip(path: Path, root: Path, files: tuple[Path, ...]) -> int:
    with zipfile.ZipFile(
        path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source in files:
            relative = source.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    return len(files)


def build(root: Path, output_dir: Path) -> tuple[Path, ...]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(root):
        raise ValueError("release output must stay inside the repository")
    if output_dir.exists():
        raise FileExistsError(f"release output already exists: {output_dir}")

    readiness = assess_release_readiness(root, mode="release")
    if not readiness.ok:
        raise ValueError("release readiness is blocked: " + "; ".join(readiness.errors))

    source_files = _collect(root, COMMON_PATHS + RUNTIME_SCRIPTS)
    skill_files = _collect(
        root, COMMON_PATHS + RUNTIME_SCRIPTS + ("SKILL.md", "agents")
    )
    _validate_members(root, source_files)
    _validate_members(root, skill_files)
    asset_records = _validate_assets(root)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="release-candidate-", dir=output_dir.parent
    ) as temporary:
        stage = Path(temporary) / "release"
        stage.mkdir()
        source_zip = stage / "academic-pdf-en-zh-reader-source.zip"
        skill_zip = stage / "academic-pdf-en-zh-reader-skill.zip"
        source_count = _write_zip(source_zip, root, source_files)
        skill_count = _write_zip(skill_zip, root, skill_files)
        manifest = {
            "schema_version": 1,
            "disclaimer_sha256": _sha256(root / "DISCLAIMER.md"),
            "notice_sha256": _sha256(root / "NOTICE"),
            "third_party_notices_sha256": _sha256(root / "THIRD_PARTY_NOTICES.md"),
            "bundles": [
                {
                    "path": source_zip.name,
                    "member_count": source_count,
                    "sha256": _sha256(source_zip),
                    "size": source_zip.stat().st_size,
                },
                {
                    "path": skill_zip.name,
                    "member_count": skill_count,
                    "sha256": _sha256(skill_zip),
                    "size": skill_zip.stat().st_size,
                },
            ],
            "bundled_assets": asset_records,
        }
        manifest_path = stage / "asset-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.rename(stage, output_dir)

    return (
        output_dir / source_zip.name,
        output_dir / skill_zip.name,
        output_dir / "asset-manifest.json",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        outputs = build(args.root, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release artifact build: BLOCKED: {exc}")
        return 1
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
