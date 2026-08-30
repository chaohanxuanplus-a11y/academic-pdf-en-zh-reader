# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic CJK line breaking with stable protected academic tokens."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from academic_pdf_en_zh_reader.typography.font_runs import (
    FontRunResolver,
    grapheme_clusters,
)
from academic_pdf_en_zh_reader.typography.measure import LineBox, make_line_box

PROHIBITED_LINE_START = frozenset("，。、；：！？）》」』】〕〉”’…％%℃°,.!?;:)]}")
PROHIBITED_LINE_END = frozenset("（《「『【〔〈“‘([{\u00a5$£€")
SAFE_URL_BREAK_AFTER = frozenset("/?&#=._-")

_URL = re.compile(
    r"(?:https?://|www\.|doi:\s*|10\.\d{4,9}/)"
    r"[^\s，。；！？）》】]+",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\[(?:\d+(?:\s*[-–—,;]\s*\d+)*)\]")
_STATISTIC = re.compile(
    r"(?:p|n)\s*(?:=|<|>|≤|≥)\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)",
    re.IGNORECASE,
)
_CONFIDENCE_INTERVAL = re.compile(
    r"\d+(?:\.\d+)?%\s*CI(?:\s*[=:]?\s*\d+(?:\.\d+)?"
    r"\s*[-–—]\s*\d+(?:\.\d+)?)?",
    re.IGNORECASE,
)
_NUMBER_UNIT = re.compile(
    r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s*[-–—]\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+))?"
    r"(?:\s*(?:mmHg|kPa|MPa|mPa|kg|mg|µg|μg|ng|pg|mm|cm|km|mL|µL|μL|"
    r"mol|mmol|µmol|μmol|Hz|kHz|MHz|°C|℃|min|ms|days|day|years|year|"
    r"h|s|m|g|L|K|Pa|%))?",
    re.IGNORECASE,
)


class LineBreakError(ValueError):
    """Raised when fixed type and legal breaks cannot fit the requested width."""


@dataclass(frozen=True)
class _Atom:
    text: str
    kind: str = "ordinary"


def _url_atoms(text: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    start = 0
    for index, character in enumerate(text):
        if character in SAFE_URL_BREAK_AFTER:
            atoms.append(_Atom(text[start : index + 1], "url"))
            start = index + 1
    if start < len(text):
        atoms.append(_Atom(text[start:], "url"))
    return [atom for atom in atoms if atom.text]


def _cluster_index(text: str) -> tuple[dict[int, str], frozenset[int]]:
    by_start: dict[int, str] = {}
    boundaries = {0}
    cursor = 0
    for cluster in grapheme_clusters(text):
        by_start[cursor] = cluster
        cursor += len(cluster)
        boundaries.add(cursor)
    return by_start, frozenset(boundaries)


def _is_latin_or_greek(character: str) -> bool:
    name = unicodedata.name(character, "")
    return name.startswith("LATIN ") or name.startswith("GREEK ")


def _latin_word_end(
    text: str,
    start: int,
    clusters_by_start: dict[int, str],
) -> int | None:
    first = clusters_by_start[start]
    if not _is_latin_or_greek(first[0]):
        return None
    cursor = start
    while cursor < len(text):
        cluster = clusters_by_start[cursor]
        base = cluster[0]
        if not (
            _is_latin_or_greek(base)
            or base.isascii()
            and base.isdigit()
            or base in "-'’"
        ):
            break
        cursor += len(cluster)
    return cursor


def _raw_atoms(text: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    index = 0
    clusters_by_start, boundaries = _cluster_index(text)
    protected = (_URL, _CITATION, _STATISTIC, _CONFIDENCE_INTERVAL, _NUMBER_UNIT)
    while index < len(text):
        if text[index] in "\r\n\u2028\u2029":
            end = index + 1
            if text[index : index + 2] == "\r\n":
                end += 1
            atoms.append(_Atom(text[index:end], "hard-break"))
            index = end
            continue
        if text[index].isspace():
            end = index + 1
            while (
                end < len(text)
                and text[end].isspace()
                and text[end] not in "\r\n\u2028\u2029"
            ):
                end += 1
            atoms.append(_Atom(text[index:end], "space"))
            index = end
            continue
        match = None
        for candidate in protected:
            potential = candidate.match(text, index)
            if potential is not None and potential.end() in boundaries:
                match = potential
                break
        if match is not None:
            value = match.group(0)
            if match.re is _URL:
                atoms.extend(_url_atoms(value))
            else:
                atoms.append(_Atom(value, "semantic-protected"))
            index = match.end()
            continue
        latin_end = _latin_word_end(text, index, clusters_by_start)
        if latin_end is not None:
            atoms.append(_Atom(text[index:latin_end], "latin"))
            index = latin_end
            continue
        cluster = clusters_by_start[index]
        atoms.append(_Atom(cluster))
        index += len(cluster)
    return atoms


def _merged_kind(left: _Atom, right: _Atom) -> str:
    kinds = {left.kind, right.kind}
    if "url" in kinds or "url-group" in kinds:
        return "url-group"
    if "semantic-protected" in kinds:
        return "semantic-protected"
    if "latin" in kinds or "protected-group" in kinds:
        return "protected-group"
    return "ordinary"


def _merge(left: _Atom, right: _Atom) -> _Atom:
    return _Atom(left.text + right.text, _merged_kind(left, right))


def _legal_segment(segment: list[_Atom]) -> list[_Atom]:
    without_spaces: list[_Atom] = []
    for atom in segment:
        if atom.kind == "space":
            if without_spaces:
                previous = without_spaces[-1]
                without_spaces[-1] = _Atom(previous.text + atom.text, previous.kind)
            continue
        without_spaces.append(atom)

    if not without_spaces:
        raise LineBreakError("an explicit line must contain drawable text")

    legal: list[_Atom] = []
    index = 0
    while index < len(without_spaces):
        atom = without_spaces[index]
        while (
            atom.text.rstrip()
            and atom.text.rstrip()[-1] in PROHIBITED_LINE_END
            and index + 1 < len(without_spaces)
        ):
            index += 1
            following = without_spaces[index]
            atom = _merge(atom, following)
        if atom.text and atom.text[0] in PROHIBITED_LINE_START and legal:
            previous = legal[-1]
            legal[-1] = _merge(previous, atom)
        else:
            legal.append(atom)
        index += 1

    first = legal[0].text.lstrip()
    last = legal[-1].text.rstrip()
    if first and first[0] in PROHIBITED_LINE_START:
        raise LineBreakError("prohibited punctuation cannot start the text")
    if last and last[-1] in PROHIBITED_LINE_END:
        raise LineBreakError("prohibited punctuation cannot end the text")
    return legal


def _legal_atoms(text: str) -> list[_Atom]:
    legal: list[_Atom] = []
    segment: list[_Atom] = []
    for atom in _raw_atoms(text):
        if atom.kind != "hard-break":
            segment.append(atom)
            continue
        legal.extend(_legal_segment(segment))
        legal.append(atom)
        segment = []
    if not segment:
        if legal and legal[-1].kind == "hard-break":
            raise LineBreakError("text must not end with an empty explicit line")
        if not legal:
            raise LineBreakError("text produced no drawable line")
    else:
        legal.extend(_legal_segment(segment))
    return legal


def _box(
    text: str,
    *,
    resolver: FontRunResolver,
    font_role: str,
    size_pt: float,
    line_height_pt: float,
) -> LineBox:
    resolved = resolver.resolve(text, font_role=font_role)
    return make_line_box(
        resolved,
        size_pt=size_pt,
        line_height_pt=line_height_pt,
    )


def _emergency_atoms(atom: _Atom) -> list[_Atom]:
    if atom.kind in {"url", "url-group"}:
        raise LineBreakError(
            "URL segment has no approved safe break that fits the fixed width"
        )
    if atom.kind in {"semantic-protected", "protected-group"}:
        raise LineBreakError("protected academic token exceeds the fixed line width")
    clusters = grapheme_clusters(atom.text)
    groups: list[str] = []
    current = ""
    for cluster in clusters:
        if not current:
            current = cluster
            continue
        previous = current.rstrip()
        following = cluster.lstrip()
        boundary_is_illegal = (
            not following
            or (previous and previous[-1] in PROHIBITED_LINE_END)
            or (following and following[0] in PROHIBITED_LINE_START)
        )
        if boundary_is_illegal:
            current += cluster
        else:
            groups.append(current)
            current = cluster
    if current:
        groups.append(current)
    replacement_kind = "ordinary" if atom.kind == "ordinary" else "latin"
    replacements = [_Atom(group, replacement_kind) for group in groups]
    if len(replacements) == 1:
        raise LineBreakError(
            "minimum legal punctuation group exceeds the fixed line width"
        )
    return replacements


def break_text(
    text: str,
    *,
    max_width_pt: float,
    resolver: FontRunResolver,
    font_role: str,
    size_pt: float,
    line_height_pt: float,
) -> tuple[LineBox, ...]:
    """Greedily choose the furthest stable legal break at a fixed font size."""

    if not text:
        raise ValueError("text must not be empty")
    for label, value in (
        ("maximum line width", max_width_pt),
        ("font size", size_pt),
        ("line height", line_height_pt),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{label} must be finite and positive")
    unsupported_controls = sorted(
        {
            ord(character)
            for character in text
            if unicodedata.category(character) == "Cc" and character not in "\r\n"
        }
    )
    if unsupported_controls:
        formatted = ", ".join(f"U+{value:04X}" for value in unsupported_controls)
        raise LineBreakError(f"unsupported control character: {formatted}")

    pending = _legal_atoms(text)
    lines: list[LineBox] = []
    current: list[_Atom] = []
    accepted: LineBox | None = None
    index = 0
    while index < len(pending):
        atom = pending[index]
        if atom.kind == "hard-break":
            if accepted is None or not current:
                raise LineBreakError("explicit line break has no preceding text")
            lines.append(accepted)
            current = []
            accepted = None
            index += 1
            continue
        candidate_text = "".join(item.text for item in (*current, atom)).rstrip()
        candidate = _box(
            candidate_text,
            resolver=resolver,
            font_role=font_role,
            size_pt=size_pt,
            line_height_pt=line_height_pt,
        )
        if candidate.width_pt <= max_width_pt:
            current.append(atom)
            accepted = candidate
            index += 1
            continue
        if current:
            if accepted is None:
                raise LineBreakError("line state lost its accepted measurement")
            lines.append(accepted)
            current = []
            accepted = None
            continue

        replacements = _emergency_atoms(atom)
        pending[index : index + 1] = replacements

    if accepted is not None:
        lines.append(accepted)
    if not lines:
        raise LineBreakError("text produced no drawable line")
    return tuple(lines)
