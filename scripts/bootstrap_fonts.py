# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fetch and verify the exact OFL fonts approved for the renderer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import fontTools
from fontTools.ttLib import TTCollection, TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "assets" / "font-manifest.json"
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "raw.githubusercontent.com"}

FONT_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "role": "body",
        "filename": "NotoSerifSC-Regular.ttf",
        "expected_family": "Noto Serif SC",
        "expected_subfamily": "Regular",
        "size": 14_851_736,
        "sha256": "ce123d40ed0f7167e6ad4b8626d5ba54ed0acf706f22f7aafdf6010c3029e147",
        "git_blob_sha1": "2c24ee5f827742d5a82ff41a9abf83af01e1dca0",
        "source_kind": "derived_variable_instance",
        "source_repository": "https://github.com/googlefonts/noto-cjk",
        "source_release": (
            "https://github.com/googlefonts/noto-cjk/releases/tag/Serif2.003"
        ),
        "source_tag": "Serif2.003",
        "source_commit": "9b0f1436e455d902de067a2501422e5dc71ad16b",
        "source_path": "Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf",
        "archive_name": "03_NotoSerifCJK-TTF-VF.zip",
        "archive_member": "Variable/TTF/Subset/NotoSerifSC-VF.ttf",
        "source_url": (
            "https://raw.githubusercontent.com/googlefonts/noto-cjk/Serif2.003/"
            "Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf"
        ),
        "source_binary": {
            "filename": "NotoSerifSC-VF.ttf",
            "size": 25_125_232,
            "sha256": (
                "5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09"
            ),
            "git_blob_sha1": "914d51db3ef9841a98ccb8f48db9e8bddb0b0d72",
        },
        "transformation": {
            "tool": "fonttools",
            "tool_version": "4.63.0",
            "operation": "instantiate_variable_font",
            "axis_location": {"wght": 400},
            "static": True,
            "update_name_table": True,
            "recalc_timestamp": False,
        },
        "modified": True,
        "license": "OFL-1.1",
        "spdx_license": "OFL-1.1-RFN",
        "reserved_font_names": ["Source"],
        "license_url": (
            "https://raw.githubusercontent.com/googlefonts/noto-cjk/Serif2.003/"
            "Serif/LICENSE"
        ),
    },
    {
        "role": "heading",
        "filename": "NotoSerifSC-SemiBold.ttf",
        "expected_family": "Noto Serif SC",
        "expected_subfamily": "SemiBold",
        "size": 14_851_208,
        "sha256": "c5964aba08d1215f658d78494c6af34cbb9323ecf46e519c2776474e57581fb9",
        "git_blob_sha1": "dda373849acfb9b5340fb9676bacac197fc43d65",
        "source_kind": "derived_variable_instance",
        "source_repository": "https://github.com/googlefonts/noto-cjk",
        "source_release": (
            "https://github.com/googlefonts/noto-cjk/releases/tag/Serif2.003"
        ),
        "source_tag": "Serif2.003",
        "source_commit": "9b0f1436e455d902de067a2501422e5dc71ad16b",
        "source_path": "Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf",
        "archive_name": "03_NotoSerifCJK-TTF-VF.zip",
        "archive_member": "Variable/TTF/Subset/NotoSerifSC-VF.ttf",
        "source_url": (
            "https://raw.githubusercontent.com/googlefonts/noto-cjk/Serif2.003/"
            "Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf"
        ),
        "source_binary": {
            "filename": "NotoSerifSC-VF.ttf",
            "size": 25_125_232,
            "sha256": (
                "5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09"
            ),
            "git_blob_sha1": "914d51db3ef9841a98ccb8f48db9e8bddb0b0d72",
        },
        "transformation": {
            "tool": "fonttools",
            "tool_version": "4.63.0",
            "operation": "instantiate_variable_font",
            "axis_location": {"wght": 600},
            "static": True,
            "update_name_table": True,
            "recalc_timestamp": False,
        },
        "modified": True,
        "license": "OFL-1.1",
        "spdx_license": "OFL-1.1-RFN",
        "reserved_font_names": ["Source"],
        "license_url": (
            "https://raw.githubusercontent.com/googlefonts/noto-cjk/Serif2.003/"
            "Serif/LICENSE"
        ),
    },
    {
        "role": "symbols",
        "filename": "NotoSansSymbols2-Regular.ttf",
        "expected_family": "Noto Sans Symbols 2",
        "expected_subfamily": "Regular",
        "size": 671_568,
        "sha256": "c4a0a80f0041ce4be81e2478faad22776d23edb98ae3f0d19bd37044820ecf9d",
        "git_blob_sha1": "a0a8cfe09d1b8bc0a0c2b43f8279fa392e9eb411",
        "source_kind": "github_release_archive",
        "source_repository": "https://github.com/notofonts/symbols",
        "source_release": (
            "https://github.com/notofonts/symbols/releases/tag/NotoSansSymbols2-v2.008"
        ),
        "source_tag": "NotoSansSymbols2-v2.008",
        "source_commit": "25b00f0d3f40873a005287514ba2a48558655314",
        "source_path": (
            "fonts/NotoSansSymbols2/unhinted/ttf/NotoSansSymbols2-Regular.ttf"
        ),
        "archive_name": "NotoSansSymbols2-v2.008.zip",
        "archive_size": 2_331_441,
        "archive_sha256": (
            "346c930bbe8eb946701a05c54e9c11a2094dee1d93c387bf1771c0a3e335688f"
        ),
        "archive_member": (
            "NotoSansSymbols2/unhinted/ttf/NotoSansSymbols2-Regular.ttf"
        ),
        "source_url": (
            "https://github.com/notofonts/symbols/releases/download/"
            "NotoSansSymbols2-v2.008/NotoSansSymbols2-v2.008.zip"
        ),
        "license": "OFL-1.1",
        "spdx_license": "OFL-1.1",
        "reserved_font_names": [],
        "modified": False,
        "license_url": (
            "https://raw.githubusercontent.com/notofonts/symbols/"
            "NotoSansSymbols2-v2.008/OFL.txt"
        ),
    },
)


def compute_git_blob_sha1(data: bytes) -> str:
    """Return the Git object id for raw blob bytes."""

    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _font_names(font: TTFont) -> dict[str, str]:
    names = font["name"]
    family = names.getBestFamilyName() or names.getDebugName(1)
    subfamily = names.getBestSubFamilyName() or names.getDebugName(2)
    postscript_name = names.getDebugName(6)
    copyright_notice = names.getDebugName(0)
    if not family or not subfamily or not postscript_name or not copyright_notice:
        raise ValueError("font has an incomplete name table")
    return {
        "family": family,
        "subfamily": subfamily,
        "typographic_family": names.getDebugName(16) or family,
        "typographic_subfamily": names.getDebugName(17) or subfamily,
        "postscript_name": postscript_name,
        "copyright": copyright_notice,
    }


def _outline_format(font: TTFont) -> str:
    if "glyf" in font and "loca" in font:
        return "glyf"
    if "CFF2" in font:
        return "CFF2"
    if "CFF " in font:
        return "CFF"
    return "unknown"


def _font_details(font: TTFont) -> dict[str, str | int]:
    return {
        **_font_names(font),
        "units_per_em": int(font["head"].unitsPerEm),
        "outline_format": _outline_format(font),
        "cmap_size": len(font.getBestCmap() or {}),
    }


def inspect_font(
    path: Path,
    *,
    expected_family: str,
    expected_subfamily: str,
) -> dict[str, str | int | None]:
    """Select a TTC face by exact names, or verify a standalone TTF."""

    expected = (expected_family.casefold(), expected_subfamily.casefold())
    if path.suffix.casefold() == ".ttc":
        collection = TTCollection(path, lazy=False)
        try:
            matches: list[dict[str, str | int | None]] = []
            for index, font in enumerate(collection.fonts):
                names = _font_details(font)
                actual = (names["family"].casefold(), names["subfamily"].casefold())
                if actual == expected:
                    matches.append(
                        {
                            "format": "ttc",
                            "face_count": len(collection.fonts),
                            "face_index": index,
                            **names,
                        }
                    )
        finally:
            collection.close()
    else:
        font = TTFont(path, lazy=False)
        try:
            names = _font_details(font)
            actual = (names["family"].casefold(), names["subfamily"].casefold())
            matches = (
                [
                    {
                        "format": "ttf",
                        "face_count": 1,
                        "face_index": None,
                        **names,
                    }
                ]
                if actual == expected
                else []
            )
        finally:
            font.close()

    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {expected_family} {expected_subfamily} face in "
            f"{path.name}, found {len(matches)}"
        )
    return matches[0]


def _download(url: str, destination: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"unapproved font source: {url}")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "academic-pdf-en-zh-reader-font-bootstrap/0.1"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"font download returned HTTP {response.status}: {url}")
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def _verify_data(data: bytes, spec: dict[str, Any], *, label: str) -> None:
    if len(data) != spec["size"]:
        raise ValueError(
            f"size mismatch for {label}: expected {spec['size']}, got {len(data)}"
        )
    blob_sha1 = compute_git_blob_sha1(data)
    if blob_sha1 != spec["git_blob_sha1"]:
        raise ValueError(
            f"Git blob mismatch for {label}: "
            f"expected {spec['git_blob_sha1']}, got {blob_sha1}"
        )
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != spec["sha256"]:
        raise ValueError(
            f"SHA-256 mismatch for {label}: expected {spec['sha256']}, got {sha256}"
        )


def _verified_bytes(path: Path, source: dict[str, Any]) -> bytes:
    data = path.read_bytes()
    _verify_data(data, source, label=source["filename"])
    return data


def _fetch_source_bytes(
    source: dict[str, Any], temporary_root: Path
) -> tuple[bytes, str | None]:
    downloaded = temporary_root / f"{source['filename']}.download"
    _download(source["source_url"], downloaded)
    archive_sha256: str | None = None
    if source["source_kind"] == "github_release_archive":
        archive_data = downloaded.read_bytes()
        if len(archive_data) != source["archive_size"]:
            raise ValueError(
                f"archive size mismatch for {source['archive_name']}: "
                f"expected {source['archive_size']}, got {len(archive_data)}"
            )
        archive_sha256 = hashlib.sha256(archive_data).hexdigest()
        if archive_sha256 != source["archive_sha256"]:
            raise ValueError(
                f"archive SHA-256 mismatch for {source['archive_name']}: "
                f"expected {source['archive_sha256']}, got {archive_sha256}"
            )
        with zipfile.ZipFile(downloaded) as archive:
            members = [
                info
                for info in archive.infolist()
                if info.filename == source["archive_member"]
            ]
            if len(members) != 1:
                raise ValueError(
                    f"expected exactly one {source['archive_member']} in "
                    f"{source['archive_name']}, found {len(members)}"
                )
            data = archive.read(members[0])
    else:
        data = downloaded.read_bytes()
    return data, archive_sha256


def _derive_static_instance(
    source: dict[str, Any],
    source_data: bytes,
    temporary_root: Path,
    destination: Path,
) -> None:
    source_binary = source["source_binary"]
    _verify_data(source_data, source_binary, label=source_binary["filename"])
    transformation = source["transformation"]
    if fontTools.__version__ != transformation["tool_version"]:
        raise RuntimeError(
            "fontTools version mismatch: "
            f"expected {transformation['tool_version']}, got {fontTools.__version__}"
        )

    source_path = temporary_root / source_binary["filename"]
    if not source_path.exists():
        source_path.write_bytes(source_data)
    variable_font = TTFont(
        source_path,
        recalcTimestamp=transformation["recalc_timestamp"],
        recalcBBoxes=True,
    )
    try:
        required_tables = {"fvar", "gvar", "glyf", "loca"}
        if not required_tables.issubset(variable_font.keys()):
            raise ValueError("approved variable TrueType source lacks required tables")
        if "CFF " in variable_font or "CFF2" in variable_font:
            raise ValueError("approved variable source unexpectedly has CFF outlines")
        static_font = instantiateVariableFont(
            variable_font,
            transformation["axis_location"],
            inplace=True,
            optimize=True,
            updateFontNames=transformation["update_name_table"],
            static=transformation["static"],
        )
        static_font.recalcTimestamp = transformation["recalc_timestamp"]
        static_font.save(destination)
    finally:
        variable_font.close()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def bootstrap_fonts(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    """Download, pin, inspect, and describe the approved font chain."""

    previous_manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    font_directory = manifest_path.parent / "fonts"
    font_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    source_cache: dict[str, bytes] = {}

    with tempfile.TemporaryDirectory(prefix="font-bootstrap-") as temp_directory:
        temporary_root = Path(temp_directory)
        for source in FONT_SOURCES:
            destination = font_directory / source["filename"]
            archive_sha256: str | None = None
            if destination.exists():
                data = _verified_bytes(destination, source)
            else:
                partial = temporary_root / f"{source['filename']}.partial"
                if source["source_url"] not in source_cache:
                    source_data, archive_sha256 = _fetch_source_bytes(
                        source, temporary_root
                    )
                    source_cache[source["source_url"]] = source_data
                else:
                    source_data = source_cache[source["source_url"]]
                if source["source_kind"] == "derived_variable_instance":
                    _derive_static_instance(
                        source, source_data, temporary_root, partial
                    )
                else:
                    partial.write_bytes(source_data)
                data = _verified_bytes(partial, source)
                os.replace(partial, destination)

            inspected = inspect_font(
                destination,
                expected_family=source["expected_family"],
                expected_subfamily=source["expected_subfamily"],
            )
            record = {key: value for key, value in source.items() if key != "filename"}
            record.update(inspected)
            record.update(
                {
                    "path": destination.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            if archive_sha256 is not None:
                record["archive_sha256"] = archive_sha256
            records.append(record)

    current_hashes = {record["role"]: record["sha256"] for record in records}
    embedding_probe: dict[str, Any] = {
        "status": "pending",
        "reportlab_version": None,
        "pdf_sha256": None,
        "embedded_font_count": None,
        "extracted_text_matches": None,
        "render_nonwhite_ratio": None,
    }
    if previous_manifest is not None:
        previous_probe = previous_manifest.get("embedding_probe", {})
        if (
            previous_probe.get("status") == "passed"
            and previous_probe.get("font_hashes") == current_hashes
        ):
            embedding_probe = previous_probe

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "license": "OFL-1.1",
        "system_font_fallback": False,
        "fonts": records,
        "embedding_probe": embedding_probe,
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="manifest to create (default: assets/font-manifest.json)",
    )
    args = parser.parse_args()
    manifest = bootstrap_fonts(args.manifest.resolve())
    print(
        f"Verified {len(manifest['fonts'])} fonts and wrote {args.manifest.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
