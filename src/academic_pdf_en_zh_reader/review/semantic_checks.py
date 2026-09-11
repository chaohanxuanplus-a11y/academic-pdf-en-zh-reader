# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Finite, mechanical source/translation marker comparisons."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from academic_pdf_en_zh_reader.review.translation_validation import (
    validate_translation_artifact,
)

_UNSIGNED_NUMBER_CORE = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
_NUMBER_CORE = rf"[-+\u2212]?{_UNSIGNED_NUMBER_CORE}"
_NUMBER = re.compile(
    rf"(?<![A-Za-z0-9_.])(?:(?<!\w)-|[+\u2212])?{_UNSIGNED_NUMBER_CORE}"
)
_RANGE = re.compile(
    rf"(?<![A-Za-z0-9_.-])({_NUMBER_CORE})\s*"
    rf"(?:-|\u2013|\u2014|to|\u81f3|\u5230)\s*"
    rf"({_NUMBER_CORE})(?![A-Za-z0-9])",
    re.I,
)
_CHINESE_TRANSITION_RANGE = re.compile(
    rf"(?:从|由)\s*({_NUMBER_CORE})\s*"
    r"(?:(?:mm|cm|m|s|h|%)(?:[·/](?:a|s|h)(?:[−-]?\d+)?)?\s*)?"
    rf"(?:增加|提高|降低|减少|升高|下降|加速)?(?:至|到)\s*({_NUMBER_CORE})"
)
_SYMBOLIC_INEQUALITY = re.compile(
    rf"(?P<operator><=|>=|<|>|\u2264|\u2265)\s*(?P<number>{_NUMBER_CORE})"
)
_WORD_INEQUALITY = re.compile(
    rf"(?P<operator>no\s+less\s+than|no\s+more\s+than|at\s+least|at\s+most|"
    rf"greater\s+than|more\s+than|less\s+than|above|below|"
    rf"not\s+exceed(?:-\s*)?ing|exceed(?:-\s*)?ing|"
    rf"\u4e0d\u5c11\u4e8e|\u4e0d\u4f4e\u4e8e|\u81f3\u5c11|"
    rf"\u4e0d\u8d85\u8fc7|\u4e0d\u9ad8\u4e8e|\u81f3\u591a|"
    rf"\u5927\u4e8e|\u9ad8\u4e8e|\u5c0f\u4e8e|\u4f4e\u4e8e|超过)"
    rf"\s*(?:提高|增加|减少|降低|为)?\s*(?P<number>{_NUMBER_CORE})",
    re.I,
)
_UNIT = re.compile(
    r"(?<![A-Za-z'’])(?:%|\u00b0C|\u2103|k?Pa|MPa|kg|mg|\u03bcg|\u00b5g|ug|mm|cm|\u03bcm|\u00b5m|um|nm|mL|mmol/L|mol/L|Hz|m/s|min|h|s|K|L)(?![A-Za-z])"
)
_CITATION = re.compile(
    r"\[[0-9,;\s\u2013\u2014-]+\]|"
    r"\([A-Z][^()]{0,80}?(?:19|20)\d{2}[a-z]?[^()]*\)|"
    r"\uff08[A-Z][^\uff08\uff09]{0,80}?(?:19|20)\d{2}[a-z]?[^\uff08\uff09]*\uff09"
)
_FIGURE_TABLE = re.compile(
    r"(?:(Fig(?:ure)?|Table)\.?\s*([0-9]+[A-Za-z]?)|([\u56fe\u8868])\s*([0-9]+[A-Za-z]?))",
    re.I,
)
_CID_PLACEHOLDER = re.compile(r"\(cid:\d+\)", re.I)
_CID_FOLLOWED_NUMBER = re.compile(r"\(cid:\d+\)\s*\d+", re.I)
_ENGLISH_DECADE = re.compile(r"(?<![A-Za-z0-9])((?:1\d|20)\d0)s(?![A-Za-z0-9])")
_ARABIC_CHINESE_DECADE = re.compile(
    r"(?<!\d)(\d{1,2})\s*\u4e16\u7eaa\s*(\d{1,2})\s*\u5e74\u4ee3"
)
_WRITTEN_CHINESE_DECADE = re.compile(
    r"([\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]{1,3})"
    r"\u4e16\u7eaa"
    r"([\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]{1,3})"
    r"\u5e74\u4ee3"
)
_ENGLISH_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}
_NUMBER_WORD = "|".join(sorted(_ENGLISH_NUMBER_WORDS, key=len, reverse=True))
_WORD_RANGE = re.compile(
    rf"\b(?P<start>{_NUMBER_WORD})\b\s*(?:-|\u2013|\u2014|to)\s*"
    rf"\b(?P<end>{_NUMBER_WORD})\b",
    re.I,
)
_LIGATURE_TRANSLATION = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
    }
)
_IDENTIFIER_SHORTHAND_LIST = re.compile(
    r"(?<![A-Za-z0-9])"
    r"[A-Za-z][A-Za-z0-9]*-\s*\d+"
    r"(?:[\s,\uff0c\u3001\u548c\u53ca]*(?:(?:and|or)\b[\s,\uff0c\u3001]*)?-\s*\d+)+",
    re.I,
)
_COMPACT_ALPHANUMERIC_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[A-Za-z0-9./\s-]*[A-Za-z])"
    r"(?=[A-Za-z0-9./\s-]*\d)"
    r"[A-Za-z][A-Za-z0-9]*(?:[-/]\s*[A-Za-z0-9]+)+"
    r"(?![A-Za-z0-9])"
)
_SPACED_ALPHANUMERIC_IDENTIFIER = re.compile(
    r"\b[A-Za-z]{2,}\s+\d+(?:\s*/\s*\d+)+(?![A-Za-z0-9])"
)
_CJK_IDENTIFIER_WITH_ALIAS = re.compile(r"[\u3400-\u9fff]{2,}-\d+(?=[\uff08(][A-Za-z])")
_GREEK_ALPHANUMERIC_IDENTIFIER = re.compile(
    r"[\u0370-\u03ff]\d+(?:\s*/\s*[\u0370-\u03ff]?\d+)*"
)
_IDENTIFIER_PATTERNS = (
    _IDENTIFIER_SHORTHAND_LIST,
    _COMPACT_ALPHANUMERIC_IDENTIFIER,
    _SPACED_ALPHANUMERIC_IDENTIFIER,
    _CJK_IDENTIFIER_WITH_ALIAS,
    _GREEK_ALPHANUMERIC_IDENTIFIER,
)

_STATISTICS = {
    "mean": (r"mean", r"\u5e73\u5747\u503c?"),
    "median": (r"median", r"\u4e2d\u4f4d\u6570"),
    "p": (r"p",),
    "n": (r"n",),
    "r2": (r"r2", r"r\u00b2"),
    "ci": (r"ci",),
    "or": (r"or",),
    "hr": (r"hr",),
}

_DIRECTION = {
    "increase": (
        r"\bincreas(?:e|ed|es|ing)\b",
        r"\u589e\u52a0",
        r"\u589e\u591a",
        r"\u5347\u9ad8",
        r"\u4e0a\u5347",
        r"\u63d0\u9ad8",
        r"\u589e\u957f",
        r"\u5ef6\u957f",
        r"\u66f4\u591a",
    ),
    "decrease": (
        r"\bdecreas(?:e|ed|es|ing)\b",
        r"\u51cf\u5c11",
        r"\u964d\u4f4e",
        r"\u4e0b\u964d",
    ),
    "higher": (
        r"\bhigher\b",
        r"\u66f4\u9ad8",
        r"\u8f83\u9ad8",
        r"\u9ad8\u4e8e",
        r"越高",
    ),
    "lower": (
        r"\blower\b",
        r"\u8f83?\u4f4e",
        r"\u4e0b(?:\u90e8|\u65b9)",
    ),
    "positive": (
        r"\bpositiv(?:e|ely)\b",
        r"\u6b63\u76f8\u5173",
        r"\u6b63\u5411",
        r"\u9633\u6027",
    ),
    "negative": (
        r"\bnegativ(?:e|ely)\b",
        r"\u8d1f\u76f8\u5173",
        r"\u8d1f\u5411",
        r"\u9634\u6027",
    ),
}
_NEGATION = {
    "negation": (
        r"\bnot\b(?!\s+only\b)",
        r"\bno\b",
        r"\bwithout\b",
        r"\bneither\b",
        r"\bnor\b",
        r"\bfailed to\b",
        r"\u5e76\u975e",
        r"\u5e76\u4e0d",
        r"\u5c1a\u672a?",
        r"\u672a(?:\u80fd|\u66fe|\u89c1|\u68c0\u6d4b|\u53d1\u73b0|\u89c2\u5bdf|\u62a5\u9053|\u660e\u786e|\u77e5|\u786e\u5b9a|\u53d1\u8868|\u5904\u7406|\u663e\u793a|\u8bc1\u660e|\u53d1\u751f|\u51fa\u73b0|\u8fbe\u5230|\u5f97\u5230)",
        r"\u4e0d(?:\u4f1a|\u80fd|\u518d|\u8db3\u4ee5|\u5f97|\u5177\u6709|\u5f71\u54cd|\u9700\u8981|\u4f9d\u8d56|\u660e\u786e|\u6e05\u695a|\u5b58\u5728|\u662f|\u5e94|\u53ef|\u5c5e\u4e8e|\u5305\u62ec|\u53d1\u751f|\u4ea7\u751f|\u5bfc\u81f4|\u652f\u6301|\u6539\u53d8|\u964d\u4f4e|\u589e\u52a0|\u5347\u9ad8|\u51cf\u5c11|\u8868\u660e|\u663e\u793a|\u8ba4\u4e3a|\u77e5)",
        r"\u65e0(?:\u6cd5|\u9700|\u987b|\u5173|\u6548|\u660e\u663e|\u663e\u8457|\u5dee\u5f02|\u5f71\u54cd|\u8bc1\u636e|\u53d8\u5316|\u5f02\u5e38|\u53cd\u5e94|\u4f5c\u7528|\u8868\u8fbe|\u76f8\u5173)",
        r"\u6ca1\u6709",
        r"\u4e0d\u5b58\u5728",
        r"\u4e0d\u8d85\u8fc7",
        r"\u4e0d\u4fc3\u8fdb",
        r"\u96be\u4ee5",
        r"\u76f4\u5230[^\u3002\uff01\uff1f.!?]{0,40}\u624d",
    )
}
_DEGREE = {
    "possibility": (
        r"\bmay\b",
        r"\bmight\b",
        r"\bcould\b",
        r"\u53ef\u80fd(?!\u6027)",
        r"\u6216\u8bb8",
        r"\u4e5f\u8bb8",
        r"\u9884\u8ba1",
    ),
    "suggestion": (
        r"\bsuggest(?:s|ed)?\b",
        r"\u63d0\u793a",
        r"\u63d0\u51fa",
        r"\u8868\u660e",
    ),
    "approximate": (r"\bapproximately\b", r"\babout\b", r"\u7ea6", r"\u5927\u7ea6"),
    "significant": (r"\bsignificantly\b", r"\u663e\u8457"),
    "slight": (
        r"\bslightly\b",
        r"\u7565\u5fae",
        r"\u7a0d(?:\u5fae|\u6709|\u663e|\u4f4e|\u9ad8|\u5c0f|\u5927|\u5c11|\u591a)",
        r"\u7565(?:\u4f4e|\u9ad8|\u5c0f|\u5927|\u5c11|\u591a|\u6709|\u663e)",
        r"\u8f7b\u5fae",
    ),
    "strong": (
        r"\bstrongly\b",
        r"\u5f3a\u70c8",
        r"\u5f3a\u76f8\u5173",
        r"\u5f3a(?:\u9633\u6027|\u8868\u8fbe|\u67d3\u8272)",
    ),
}
_LOGIC = {
    "reason": (r"\bbecause\b", r"\bdue to\b", r"\u56e0\u4e3a", r"\u7531\u4e8e"),
    "result": (
        r"\btherefore\b",
        r"\bthus\b",
        r"\u56e0\u6b64",
        r"\u6240\u4ee5",
        r"\u4ece\u800c",
        r"\u8fdb\u800c",
        r"\u7531\u6b64",
        r"\u5bfc\u81f4",
    ),
    "association": (
        r"\bassociated with\b",
        r"\u76f8\u5173",
        r"\u6709\u5173",
        r"\u5173\u8054",
        r"\u7ed3\u5408",
    ),
    "contrast": (
        r"\balthough\b",
        r"\bhowever\b",
        r"\bwhereas\b",
        r"\u5c3d\u7ba1",
        r"\u7136\u800c",
        r"\u4e0d\u8fc7",
        r"\u800c(?=[A-Za-z\u0370-\u03ff])",
        r"而(?!且|后|今)(?=[\u3400-\u9fff])",
    ),
    "condition": (r"\bif\b", r"\bprovided that\b", r"\u5982\u679c", r"\u82e5"),
    "comparison": (
        r"\bcompared with\b",
        r"\bversus\b",
        r"\u76f8\u8f83(?:\u4e8e)?",
        r"\u4e0e(?:[^\u3002\uff01\uff1f.!?]|(?<=\d)\.(?=\d)){0,120}\u76f8\u6bd4",
        r"\u6bd4[^\u3002\uff01\uff1f.!?]{0,30}(?:\u66f4|\u8f83|\u9ad8|\u4f4e|\u591a|\u5c11|\u5927|\u5c0f)",
        r"(?<!\u6bd4)\u8f83(?=[^\u3002\uff01\uff1f.!?]{0,20}(?:\u589e\u52a0|\u51cf\u5c11|\u5347\u9ad8|\u964d\u4f4e|\u9ad8|\u4f4e|\u591a|\u5c11|\u5927|\u5c0f))",
    ),
}


@dataclass(frozen=True)
class SemanticCheckIssue:
    """One mechanically observable marker mismatch."""

    unit_id: str
    category: str
    code: str
    source_markers: tuple[str, ...]
    target_markers: tuple[str, ...]


def _normalize_number(value: str) -> str:
    normalized = value.replace(",", "").replace("\u2212", "-")
    if normalized.startswith("."):
        return f"0{normalized}"
    if normalized.startswith(("-.", "+.")):
        return f"{normalized[0]}0{normalized[1:]}"
    return normalized


def _normalize_marker_text(text: str) -> str:
    """Normalize extraction artifacts that cannot carry semantic evidence."""

    normalized = text.translate(_LIGATURE_TRANSLATION)
    normalized = _CID_FOLLOWED_NUMBER.sub("", normalized)
    return _CID_PLACEHOLDER.sub("", normalized)


def _mask_matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    masked = list(text)
    for pattern in patterns:
        for match in pattern.finditer(text):
            masked[match.start() : match.end()] = " " * (match.end() - match.start())
    return "".join(masked)


def _chinese_integer(value: str) -> int | None:
    digits = {
        "\u4e00": 1,
        "\u4e8c": 2,
        "\u4e24": 2,
        "\u4e09": 3,
        "\u56db": 4,
        "\u4e94": 5,
        "\u516d": 6,
        "\u4e03": 7,
        "\u516b": 8,
        "\u4e5d": 9,
    }
    if value == "\u5341":
        return 10
    if "\u5341" in value:
        tens, ones = value.split("\u5341", 1)
        tens_value = digits.get(tens, 1) if tens else 1
        ones_value = digits.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    return digits.get(value)


def _decades(text: str) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    markers: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in _ENGLISH_DECADE.finditer(text):
        markers.append(f"{match.group(1)}s")
        spans.append(match.span())
    for match in _ARABIC_CHINESE_DECADE.finditer(text):
        century = int(match.group(1))
        decade = int(match.group(2))
        if 1 <= century <= 99 and 0 <= decade <= 90 and decade % 10 == 0:
            markers.append(f"{(century - 1) * 100 + decade}s")
            spans.append(match.span())
    for match in _WRITTEN_CHINESE_DECADE.finditer(text):
        century = _chinese_integer(match.group(1))
        decade = _chinese_integer(match.group(2))
        if (
            century is not None
            and decade is not None
            and 1 <= century <= 99
            and 0 <= decade <= 90
            and decade % 10 == 0
        ):
            markers.append(f"{(century - 1) * 100 + decade}s")
            spans.append(match.span())
    return tuple(markers), tuple(spans)


def _mask_spans(text: str, spans: tuple[tuple[int, int], ...]) -> str:
    masked = list(text)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    return "".join(masked)


def _numbers(text: str) -> tuple[str, ...]:
    decade_markers, decade_spans = _decades(text)
    numeric_text = _mask_spans(text, decade_spans)
    numeric_text = _mask_matches(numeric_text, (_CITATION, *_IDENTIFIER_PATTERNS))
    markers = list(decade_markers)
    markers.extend(
        _normalize_number(match.group()) for match in _NUMBER.finditer(numeric_text)
    )
    return tuple(sorted(set(markers)))


def _ranges(text: str) -> tuple[str, ...]:
    range_text = _mask_matches(text, (_CITATION, *_IDENTIFIER_PATTERNS))
    markers = {
        f"{_normalize_number(match.group(1))}:{_normalize_number(match.group(2))}"
        for match in _RANGE.finditer(range_text)
    }
    markers.update(
        f"{_normalize_number(match.group(1))}:{_normalize_number(match.group(2))}"
        for match in _CHINESE_TRANSITION_RANGE.finditer(range_text)
    )
    markers.update(
        f"{_ENGLISH_NUMBER_WORDS[match.group('start').casefold()]}:"
        f"{_ENGLISH_NUMBER_WORDS[match.group('end').casefold()]}"
        for match in _WORD_RANGE.finditer(range_text)
    )
    return tuple(sorted(markers))


def _inequalities(text: str) -> tuple[str, ...]:
    canonical_operators = {
        "<": "<",
        "<=": "<=",
        "\u2264": "<=",
        ">": ">",
        ">=": ">=",
        "\u2265": ">=",
        "no less than": ">=",
        "at least": ">=",
        "\u4e0d\u5c11\u4e8e": ">=",
        "\u4e0d\u4f4e\u4e8e": ">=",
        "\u81f3\u5c11": ">=",
        "no more than": "<=",
        "at most": "<=",
        "\u4e0d\u8d85\u8fc7": "<=",
        "\u4e0d\u9ad8\u4e8e": "<=",
        "\u81f3\u591a": "<=",
        "greater than": ">",
        "more than": ">",
        "above": ">",
        "exceeding": ">",
        "not exceeding": "<=",
        "超过": ">",
        "\u5927\u4e8e": ">",
        "\u9ad8\u4e8e": ">",
        "less than": "<",
        "below": "<",
        "\u5c0f\u4e8e": "<",
        "\u4f4e\u4e8e": "<",
    }
    markers: list[str] = []
    for pattern in (_SYMBOLIC_INEQUALITY, _WORD_INEQUALITY):
        for match in pattern.finditer(text):
            operator = " ".join(match.group("operator").casefold().split())
            operator = re.sub(r"-\s*", "", operator)
            markers.append(
                f"{canonical_operators[operator]}:{_normalize_number(match.group('number'))}"
            )
    return tuple(sorted(set(markers)))


def _units(text: str) -> tuple[str, ...]:
    _decade_markers, decade_spans = _decades(text)
    unit_text = _mask_spans(text, decade_spans)
    return tuple(
        sorted(
            {
                match.group()
                .replace("ug", "\u03bcg")
                .replace("um", "\u03bcm")
                .replace("\u00b5", "\u03bc")
                .replace("\u2103", "\u00b0C")
                for match in _UNIT.finditer(unit_text)
            }
        )
    )


def _statistics(text: str) -> tuple[str, ...]:
    markers: list[str] = []
    for name, labels in _STATISTICS.items():
        pattern = re.compile(
            rf"(?<![A-Za-z])(?:{'|'.join(labels)})\s*(=|<|>|\u2264|\u2265)?\s*({_NUMBER_CORE})",
            re.I,
        )
        for match in pattern.finditer(text):
            markers.append(
                f"{name}:{match.group(1) or ''}:{_normalize_number(match.group(2))}"
            )
    return tuple(sorted(set(markers)))


def _canonical_words(
    text: str,
    vocabulary: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    markers: list[str] = []
    for name, patterns in vocabulary.items():
        hits: list[tuple[int, int]] = []
        for pattern in patterns:
            hits.extend(match.span() for match in re.finditer(pattern, text, re.I))
        distinct_hits: list[tuple[int, int]] = []
        for start, end in sorted(hits):
            if distinct_hits and start < distinct_hits[-1][1]:
                previous_start, previous_end = distinct_hits[-1]
                distinct_hits[-1] = (previous_start, max(previous_end, end))
            else:
                distinct_hits.append((start, end))
        markers.extend(name for _hit in distinct_hits)
    return tuple(sorted(set(markers)))


def _citations(text: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                re.sub(r"\s+", "", match.group())
                .replace("\u2014", "\u2013")
                .replace("\uff08", "(")
                .replace("\uff09", ")")
                .replace("\uff0c", ",")
                .replace("\uff1b", ";")
                for match in _CITATION.finditer(text)
            }
        )
    )


def _figure_tables(text: str) -> tuple[str, ...]:
    markers: list[str] = []
    for match in _FIGURE_TABLE.finditer(text):
        if match.group(1):
            kind = "table" if match.group(1).lower() == "table" else "figure"
            number = match.group(2)
        else:
            kind = "figure" if match.group(3) == "\u56fe" else "table"
            number = match.group(4)
        markers.append(f"{kind}:{number.lower()}")
    return tuple(sorted(set(markers)))


_CHECKS: tuple[tuple[str, Callable[[str], tuple[str, ...]]], ...] = (
    ("number", _numbers),
    ("unit", _units),
    ("range", _ranges),
    ("inequality", _inequalities),
    ("statistic", _statistics),
    ("direction", lambda text: _canonical_words(text, _DIRECTION)),
    ("negation", lambda text: _canonical_words(text, _NEGATION)),
    ("degree", lambda text: _canonical_words(text, _DEGREE)),
    ("logic", lambda text: _canonical_words(text, _LOGIC)),
    ("citation", _citations),
    ("figure-table", _figure_tables),
)

_SOURCE_REQUIRED_CATEGORIES = frozenset(
    {"unit", "direction", "negation", "degree", "logic"}
)


def _number_word_aliases(text: str, candidates: set[str]) -> set[str]:
    aliases: set[str] = set()
    for word, number in _ENGLISH_NUMBER_WORDS.items():
        if number in candidates and re.search(rf"\b{word}\b", text, re.I):
            aliases.add(number)
    return aliases


def _identifier_numbers(text: str) -> set[str]:
    markers: set[str] = set()
    for pattern in _IDENTIFIER_PATTERNS:
        for match in pattern.finditer(text):
            markers.update(
                _normalize_number(number)
                for number in re.findall(_UNSIGNED_NUMBER_CORE, match.group())
            )
    return markers


def _markers_match(
    category: str,
    source_markers: tuple[str, ...],
    target_markers: tuple[str, ...],
    source_text: str,
    target_text: str,
) -> bool:
    """Compare only evidence that survives conservative cross-language mapping."""

    source_set = set(source_markers)
    target_set = set(target_markers)
    if category == "number":
        source_set.update(_number_word_aliases(source_text, target_set))
        target_set.update(_number_word_aliases(target_text, source_set))
        target_only = target_set - source_set
        target_set.difference_update(target_only & _identifier_numbers(source_text))
    if category in _SOURCE_REQUIRED_CATEGORIES:
        return source_set <= target_set
    return source_set == target_set


def check_mechanical_semantics(
    units: Mapping[str, object],
    translation: Mapping[str, object],
) -> tuple[SemanticCheckIssue, ...]:
    """Return marker mismatches; an empty result does not prove semantic correctness."""

    validate_translation_artifact(units, translation)
    source_by_id = {unit["id"]: unit for unit in units["units"]}  # type: ignore[index]
    issues: list[SemanticCheckIssue] = []
    for translated in translation["units"]:  # type: ignore[index]
        unit_id = translated["unit_id"]
        source_text = _normalize_marker_text(source_by_id[unit_id]["source_text"])
        target_text = _normalize_marker_text(translated["chinese_text"])
        for category, extractor in _CHECKS:
            source_markers = extractor(source_text)
            target_markers = extractor(target_text)
            if not _markers_match(
                category,
                source_markers,
                target_markers,
                source_text,
                target_text,
            ):
                issues.append(
                    SemanticCheckIssue(
                        unit_id=unit_id,
                        category=category,
                        code="marker_mismatch",
                        source_markers=source_markers,
                        target_markers=target_markers,
                    )
                )
    return tuple(issues)


def mechanical_issue_hash(issue, units, translation):
    """Bind a diagnostic to the exact source and translation of its unit."""
    from dataclasses import asdict

    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

    source = next(u["source_text"] for u in units["units"] if u["id"] == issue.unit_id)
    target = next(
        u["chinese_text"] for u in translation["units"] if u["unit_id"] == issue.unit_id
    )
    return sha256_canonical(
        {"issue": asdict(issue), "source": source, "target": target}
    )


def unresolved_mechanical_issues(units, translation, review):
    """Accept specific, text-bound resolutions only for heuristic language flags."""

    issues = check_mechanical_semantics(units, translation)
    by_hash = {
        mechanical_issue_hash(issue, units, translation): issue for issue in issues
    }
    resolved = set()
    for item in review.get("mechanical_resolutions", []):
        issue = by_hash.get(item["issue_hash"])
        if (
            issue is None
            or issue.unit_id != item["unit_id"]
            or issue.unit_id not in review["reviewed_unit_ids"]
            or issue.category
            not in {"unit", "direction", "negation", "degree", "logic"}
            or not item["reason"].strip()
            or item["issue_hash"] in resolved
        ):
            raise ValueError("invalid or stale mechanical resolution")
        resolved.add(item["issue_hash"])
    return tuple(issue for key, issue in by_hash.items() if key not in resolved)
