# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Evidence-bearing optional notes for figures and tables."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from academic_pdf_en_zh_reader.job.hashing import sha256_bytes


@dataclass(frozen=True, order=True)
class DirectEvidence:
    unit_id: str
    source_start: int
    source_end: int
    quote: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_kind": "semantic-unit",
            "unit_id": self.unit_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "quote": self.quote,
        }


@dataclass(frozen=True, order=True)
class FrozenObjectEvidence:
    """Evidence extracted from a frozen graphic/table object upstream."""

    source_artifact_hash: str
    object_id: str
    locator: str
    evidence_text: str
    evidence_sha256: str

    @classmethod
    def from_text(
        cls,
        *,
        source_artifact_hash: str,
        object_id: str,
        locator: str,
        evidence_text: str,
    ) -> FrozenObjectEvidence:
        return cls(
            source_artifact_hash=source_artifact_hash,
            object_id=object_id,
            locator=locator,
            evidence_text=evidence_text,
            evidence_sha256=sha256_bytes(evidence_text.encode("utf-8")),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_kind": "frozen-object",
            "source_artifact_hash": self.source_artifact_hash,
            "object_id": self.object_id,
            "locator": self.locator,
            "evidence_text": self.evidence_text,
            "evidence_sha256": self.evidence_sha256,
        }


FrozenEvidenceVerifier = Callable[[FrozenObjectEvidence], bool]
FigureEvidence = DirectEvidence | FrozenObjectEvidence


@dataclass(frozen=True)
class FigureNoteCandidate:
    key: str
    figure_id: str
    caption_unit_id: str
    target_start: int
    target_end: int
    content: str
    compact_content: str
    evidence: tuple[FigureEvidence, ...]
    value_priority: int
    essential: bool


__all__ = [
    "DirectEvidence",
    "FigureEvidence",
    "FigureNoteCandidate",
    "FrozenEvidenceVerifier",
    "FrozenObjectEvidence",
]
