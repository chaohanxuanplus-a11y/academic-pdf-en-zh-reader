# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Resolve conservative grapheme clusters to an immutable approved font run."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from academic_pdf_en_zh_reader.typography.font_registry import FontRegistry

_ZWJ = "\u200d"


class MissingGlyphError(ValueError):
    """Raised when one grapheme is not covered by a single approved face."""


def _is_variation_selector(character: str) -> bool:
    codepoint = ord(character)
    return 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF


def _is_emoji_modifier(character: str) -> bool:
    return 0x1F3FB <= ord(character) <= 0x1F3FF


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _extends_cluster(character: str) -> bool:
    return (
        bool(unicodedata.combining(character))
        or unicodedata.category(character) in {"Mc", "Me"}
        or _is_variation_selector(character)
        or _is_emoji_modifier(character)
    )


def grapheme_clusters(text: str) -> tuple[str, ...]:
    """Group marks, variation selectors, ZWJ sequences, and flag pairs.

    Python's standard library does not expose UAX #29 extended grapheme
    segmentation.  This deliberately conservative subset prevents every
    sequence that matters to this renderer from being split across fonts.
    """

    clusters: list[str] = []
    current = ""
    regional_count = 0
    for character in text:
        if not current:
            current = character
            regional_count = int(_is_regional_indicator(character))
            continue
        if _extends_cluster(character) or character == _ZWJ or current.endswith(_ZWJ):
            current += character
            regional_count = 0
            continue
        if _is_regional_indicator(character) and regional_count == 1:
            current += character
            regional_count = 2
            continue
        clusters.append(current)
        current = character
        regional_count = int(_is_regional_indicator(character))
    if current:
        clusters.append(current)
    return tuple(clusters)


def _coverage_codepoints(cluster: str) -> tuple[int, ...]:
    # ReportLab assigns a non-zero missing-glyph advance even to default
    # ignorables.  Requiring every code point prevents hidden tofu or width
    # drift for variation and ZWJ sequences that its text engine cannot shape.
    return tuple(ord(character) for character in cluster)


def _forbidden_invisible_codepoint(character: str) -> bool:
    category = unicodedata.category(character)
    return (
        category in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
        or _is_variation_selector(character)
        or character == _ZWJ
    )


@dataclass(frozen=True)
class FontRun:
    """Adjacent graphemes that use one already-registered font face."""

    text: str
    font_role: str
    font_name: str
    clusters: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedText:
    """Text bound once to immutable runs for both measuring and drawing."""

    text: str
    primary_font_role: str
    runs: tuple[FontRun, ...]


class FontRunResolver:
    """Choose one face per grapheme from the fixed primary/symbol chain."""

    def __init__(self, registry: FontRegistry) -> None:
        self._registry = registry

    @property
    def font_fingerprint(self) -> tuple[tuple[str, str, str], ...]:
        """Expose only the immutable identity needed by downstream hashes."""

        return tuple(
            (face.role, face.reportlab_name, face.sha256)
            for face in self._registry.faces
        )

    def resolve(self, text: str, *, font_role: str) -> ResolvedText:
        if font_role not in {"body", "heading"}:
            raise ValueError("font_role must be body or heading")
        if not text:
            raise ValueError("text must not be empty")

        forbidden = sorted(
            ord(character)
            for character in text
            if _forbidden_invisible_codepoint(character)
        )
        if forbidden:
            formatted = ", ".join(f"U+{value:04X}" for value in forbidden)
            raise MissingGlyphError(
                f"approved font chain cannot render invisible/control {formatted}"
            )

        runs: list[FontRun] = []
        for cluster in grapheme_clusters(text):
            required = _coverage_codepoints(cluster)
            face = next(
                (
                    candidate
                    for candidate in self._registry.chain(font_role)
                    if all(candidate.covers(codepoint) for codepoint in required)
                ),
                None,
            )
            if face is None:
                missing = sorted(
                    codepoint
                    for codepoint in required
                    if not any(
                        candidate.covers(codepoint)
                        for candidate in self._registry.chain(font_role)
                    )
                )
                formatted = ", ".join(f"U+{value:04X}" for value in missing)
                if not formatted:
                    formatted = "one incompatible grapheme sequence"
                raise MissingGlyphError(
                    f"approved font chain cannot render {formatted}"
                )
            if runs and runs[-1].font_role == face.role:
                previous = runs[-1]
                runs[-1] = FontRun(
                    text=previous.text + cluster,
                    font_role=previous.font_role,
                    font_name=previous.font_name,
                    clusters=(*previous.clusters, cluster),
                )
            else:
                runs.append(
                    FontRun(
                        text=cluster,
                        font_role=face.role,
                        font_name=face.reportlab_name,
                        clusters=(cluster,),
                    )
                )
        return ResolvedText(
            text=text,
            primary_font_role=font_role,
            runs=tuple(runs),
        )
