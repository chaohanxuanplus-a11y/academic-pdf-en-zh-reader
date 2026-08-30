# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Register the only font chain approved for deterministic PDF rendering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont as FontToolsTTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as ReportLabTTFont

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APPROVED_FONT_DIR = (PROJECT_ROOT / "assets" / "fonts").resolve()
DEFAULT_FONT_MANIFEST = PROJECT_ROOT / "assets" / "font-manifest.json"
APPROVED_FONT_ROLES = frozenset({"body", "heading", "symbols"})
EXPECTED_FONT_IDENTITIES = {
    "body": ("Noto Serif SC", "Regular", "NotoSerifSC-Regular"),
    "heading": ("Noto Serif SC", "SemiBold", "NotoSerifSC-SemiBold"),
    "symbols": (
        "Noto Sans Symbols 2",
        "Regular",
        "NotoSansSymbols2-Regular",
    ),
}


class FontRegistryError(ValueError):
    """Raised when the pinned font manifest or an approved font is invalid."""


@dataclass(frozen=True)
class RegisteredFontFace:
    """One verified file registered under a stable ReportLab name."""

    role: str
    path: Path
    sha256: str
    reportlab_name: str
    codepoints: frozenset[int]

    def covers(self, codepoint: int) -> bool:
        """Return whether the verified file maps the Unicode code point."""

        return codepoint in self.codepoints


@dataclass(frozen=True)
class _InspectedFont:
    family: str | None
    subfamily: str | None
    postscript_name: str | None
    codepoints: frozenset[int]


@dataclass(frozen=True)
class FontRegistry:
    """Immutable, complete body/heading/symbol font registry."""

    faces: tuple[RegisteredFontFace, ...]

    def face(self, role: str) -> RegisteredFontFace:
        matches = tuple(face for face in self.faces if face.role == role)
        if len(matches) != 1:
            raise FontRegistryError(f"font role is not uniquely registered: {role}")
        return matches[0]

    def chain(self, primary_role: str) -> tuple[RegisteredFontFace, ...]:
        """Return a primary face followed only by the approved symbol fallback."""

        if primary_role not in {"body", "heading"}:
            raise FontRegistryError("primary font role must be either body or heading")
        return self.face(primary_role), self.face("symbols")


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FontRegistryError(f"cannot read font manifest: {path}") from exc
    if not isinstance(value, dict):
        raise FontRegistryError("font manifest must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise FontRegistryError(f"cannot read approved font: {path}") from exc
    return digest.hexdigest()


def _font_path(record: dict[str, Any]) -> Path:
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise FontRegistryError("font path must be a non-empty string")
    segments = raw_path.split("/")
    if (
        "\\" in raw_path
        or ":" in raw_path
        or raw_path.startswith("/")
        or len(segments) != 3
        or segments[:2] != ["assets", "fonts"]
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise FontRegistryError(
            "font path must be a canonical relative assets/fonts path"
        )
    try:
        path = (PROJECT_ROOT / raw_path).resolve(strict=True)
        path.relative_to(APPROVED_FONT_DIR)
    except (OSError, ValueError) as exc:
        raise FontRegistryError(
            "font path must resolve inside repository assets/fonts"
        ) from exc
    if not path.is_file() or path.suffix.casefold() != ".ttf":
        raise FontRegistryError("approved runtime font must be a TrueType file")
    return path


def _inspect_font(path: Path) -> _InspectedFont:
    try:
        font = FontToolsTTFont(path, lazy=False)
    except Exception as exc:  # fontTools exposes several parse exception types.
        raise FontRegistryError(f"cannot parse approved font: {path}") from exc
    try:
        cmap = font.getBestCmap()
        if not cmap:
            raise FontRegistryError(f"approved font has no Unicode cmap: {path}")
        names = font["name"]
        return _InspectedFont(
            family=names.getDebugName(16) or names.getDebugName(1),
            subfamily=names.getDebugName(17) or names.getDebugName(2),
            postscript_name=names.getDebugName(6),
            codepoints=frozenset(cmap),
        )
    finally:
        font.close()


def _validated_face(record: object) -> RegisteredFontFace:
    if not isinstance(record, dict):
        raise FontRegistryError("every font record must be an object")
    role = record.get("role")
    if role not in APPROVED_FONT_ROLES:
        raise FontRegistryError(f"unapproved font role: {role!r}")
    path = _font_path(record)

    expected_size = record.get("size")
    if type(expected_size) is not int or expected_size <= 0:
        raise FontRegistryError(f"invalid font size for role {role}")
    if path.stat().st_size != expected_size:
        raise FontRegistryError(f"font size does not match manifest for role {role}")

    expected_hash = record.get("sha256")
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise FontRegistryError(f"invalid font SHA-256 for role {role}")
    actual_hash = _sha256(path)
    if actual_hash != expected_hash:
        raise FontRegistryError(f"font SHA-256 does not match manifest for role {role}")

    inspected = _inspect_font(path)
    identity = (
        inspected.family,
        inspected.subfamily,
        inspected.postscript_name,
    )
    if identity != EXPECTED_FONT_IDENTITIES[role]:
        raise FontRegistryError(f"actual font identity is not approved for role {role}")

    internal_name = f"APR-{role}-{actual_hash[:16]}"
    try:
        pdfmetrics.registerFont(ReportLabTTFont(internal_name, str(path), validate=1))
    except Exception as exc:  # ReportLab also raises multiple parse exceptions.
        raise FontRegistryError(
            f"ReportLab rejected approved font for role {role}"
        ) from exc
    return RegisteredFontFace(
        role=role,
        path=path,
        sha256=actual_hash,
        reportlab_name=internal_name,
        codepoints=inspected.codepoints,
    )


def load_font_registry(
    manifest_path: Path | str = DEFAULT_FONT_MANIFEST,
) -> FontRegistry:
    """Validate the manifest and files, then register the fixed font chain."""

    path = Path(manifest_path).resolve()
    manifest = _read_manifest(path)
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise FontRegistryError("unsupported font manifest schema version")
    if manifest.get("system_font_fallback") is not False:
        raise FontRegistryError("system font fallback must be explicitly disabled")
    if manifest.get("license") != "OFL-1.1":
        raise FontRegistryError("font manifest license must be OFL-1.1")
    records = manifest.get("fonts")
    if not isinstance(records, list):
        raise FontRegistryError("font manifest must contain a fonts list")
    roles = [record.get("role") for record in records if isinstance(record, dict)]
    if len(records) != len(APPROVED_FONT_ROLES) or set(roles) != APPROVED_FONT_ROLES:
        raise FontRegistryError(
            "font manifest must contain one complete approved role chain"
        )
    if len(roles) != len(set(roles)):
        raise FontRegistryError("font roles must be unique")

    by_role = {str(record["role"]): record for record in records}
    faces = tuple(
        _validated_face(by_role[role]) for role in ("body", "heading", "symbols")
    )
    return FontRegistry(faces=faces)
