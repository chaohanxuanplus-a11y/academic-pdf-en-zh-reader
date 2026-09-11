# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Small semantic inputs and deterministic assembly of Agent-authored text."""

from dataclasses import asdict

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.review.review_validation import validate_review
from academic_pdf_en_zh_reader.review.semantic_checks import (
    check_mechanical_semantics,
    mechanical_issue_hash,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact


def make_packets(units, max_chars=12_000):
    """Never split a logical paragraph; carry one preceding paragraph as context."""
    validate_artifact("units", units)
    if type(max_chars) is not int or max_chars < 1000:
        raise ValueError("packet character budget must be at least 1000")
    packets, current, length = [], [], 0
    previous = None
    heading = None
    for unit in units["units"]:
        if current and (
            length + len(unit["source_text"]) > max_chars
            or unit["role"] == "heading"
            and length >= max_chars // 2
        ):
            packets.append(
                {
                    "context": {"heading": heading, "previous": previous},
                    "units": current,
                }
            )
            previous, current, length = current[-1], [], 0
        row = {
            "unit_id": unit["id"],
            "role": unit["role"],
            "source_text": unit["source_text"],
            "source_pages": sorted({f["page_number"] for f in unit["fragments"]}),
        }
        if unit["role"] == "heading":
            heading = unit["source_text"]
        current.append(row)
        length += len(unit["source_text"])
    if current:
        packets.append(
            {"context": {"heading": heading, "previous": previous}, "units": current}
        )
    return {"units_hash": sha256_canonical(units), "packets": packets}


def _span(text, quote, occurrence=0):
    if (
        not isinstance(quote, str)
        or not quote
        or type(occurrence) is not int
        or occurrence < 0
    ):
        raise ValueError("a nonempty source/target quote is required")
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(quote, start + 1)
        if start < 0:
            raise ValueError("quote does not occur in its bound unit")
    return start, start + len(quote)


def assemble(units, drafts, *, translator_id, review_record, revision=1):
    """Bind supplied translations, actual review scope and notes; invent no review."""
    by_id = {}
    for draft in drafts:
        if draft.get("units_hash") != sha256_canonical(units):
            raise ValueError("draft belongs to different source units")
        for row in draft["units"]:
            if row["unit_id"] in by_id:
                raise ValueError("duplicate translated unit")
            by_id[row["unit_id"]] = row
    expected = {u["id"] for u in units["units"]}
    if set(by_id) != expected:
        raise ValueError("missing or unknown translated unit")
    source = {u["id"]: u["source_text"] for u in units["units"]}
    translation = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": translator_id,
        "translation_revision": revision,
        "units": [],
    }
    teaching, figures, seen_terms = [], [], set()
    for unit in units["units"]:
        uid = unit["id"]
        row, text = by_id[uid], by_id[uid]["chinese_text"]
        translation["units"].append(
            {"unit_id": uid, "chinese_text": text, "spans": [], "terminology": []}
        )
        for term in row.get("terms", []):
            key = term["english"].casefold()
            if key in seen_terms:
                continue
            seen_terms.add(key)
            ss, se = _span(
                source[uid], term["english"], term.get("source_occurrence", 0)
            )
            ts, te = _span(text, term["target"], term.get("target_occurrence", 0))
            teaching.append(
                {
                    "key": "term:" + sha256_canonical(key)[:20],
                    "english_original": term["english"],
                    "chinese_meaning": term["explanation"],
                    "value_priority": term.get("priority", 50),
                    "essential": term.get("essential", True),
                    "occurrences": [
                        {
                            "unit_id": uid,
                            "source_start": ss,
                            "source_end": se,
                            "target_start": ts,
                            "target_end": te,
                        }
                    ],
                }
            )
        for number, note in enumerate(row.get("figure_notes", [])):
            evidence = []
            for evidence_row in note["evidence"]:
                eid, quote = evidence_row["unit_id"], evidence_row["quote"]
                ss, se = _span(source[eid], quote, evidence_row.get("occurrence", 0))
                evidence.append(
                    {
                        "evidence_kind": "semantic-unit",
                        "unit_id": eid,
                        "source_start": ss,
                        "source_end": se,
                        "quote": quote,
                    }
                )
            figures.append(
                {
                    "key": f"figure:{uid}:{number}",
                    "figure_id": note.get("figure_id", uid),
                    "caption_unit_id": uid,
                    "target_start": 0,
                    "target_end": len(text),
                    "content": note["content"],
                    "compact_content": note.get("core", note["content"]),
                    "evidence": evidence,
                    "value_priority": note.get("priority", 80),
                    "essential": note.get("essential", True),
                }
            )
    validate_translation_artifact(units, translation)
    review = {
        **review_record,
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "translator_id": translator_id,
    }
    validate_review(translation, review)
    candidates = {
        "schema_version": "1.0.0",
        "artifact_kind": "semantic-candidates",
        "units_hash": sha256_canonical(units),
        "translation_hash": sha256_canonical(translation),
        "review_hash": sha256_canonical(review),
        "teaching_candidates": teaching,
        "figure_candidates": figures,
        "red_candidates": [],
        "ambiguity_occurrences": [],
    }
    validate_artifact("semantic-candidates", candidates)
    diagnostics = [
        {
            **asdict(issue),
            "issue_hash": mechanical_issue_hash(issue, units, translation),
        }
        for issue in check_mechanical_semantics(units, translation)
    ]
    return {
        "translation": translation,
        "review": review,
        "semantic-candidates": candidates,
        "diagnostics": {
            "translation_hash": sha256_canonical(translation),
            "issues": diagnostics,
        },
    }
