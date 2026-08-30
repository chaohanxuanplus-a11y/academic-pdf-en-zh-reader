# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Deterministic, privacy-minimal PDF metadata and render-input binding."""

from __future__ import annotations

from collections.abc import Mapping

FIXED_PDF_DATE = "D:20000101000000+00'00'"
RENDER_INPUT_FIELDS = (
    "finalization_receipt_hash",
    "source_sha256",
    "normalized_pdf_sha256",
    "source_artifact_hash",
    "annotations_hash",
    "annotation_selection_hash",
    "frame_graph_hash",
    "layout_hash",
    "render_style",
    "render_style_hash",
    "font_manifest_hash",
    "font_fingerprint",
    "font_files",
    "font_usages",
    "overlay_plan_hash",
    "overlay_plan_limits",
    "overlay_plan_limits_hash",
    "overlay_pdf_sha256",
    "dependencies",
    "runtime_fingerprint",
    "metadata_policy",
    "pages",
    "block_mappings",
)


def metadata_policy() -> dict[str, object]:
    """Return the complete v1 policy; source metadata and annotations are dropped."""

    return {
        "version": 1,
        "source_document_metadata": "discarded",
        "source_annotations": "stripped",
        "source_active_content": "rejected",
        "creation_date": FIXED_PDF_DATE,
        "modification_date": FIXED_PDF_DATE,
    }


def final_pdf_metadata(
    *,
    render_input_hash: str,
    overlay_plan_hash: str,
) -> dict[str, str]:
    """Return fixed pypdf metadata with only non-sensitive provenance hashes."""

    return {
        "/Title": "Academic bilingual reading PDF",
        "/Author": "",
        "/Subject": "English-Chinese academic reading copy",
        "/Creator": "academic-pdf-en-zh-reader",
        "/Producer": "academic-pdf-en-zh-reader deterministic renderer",
        "/CreationDate": FIXED_PDF_DATE,
        "/ModDate": FIXED_PDF_DATE,
        "/APRRenderInputHash": render_input_hash,
        "/APROverlayPlanHash": overlay_plan_hash,
    }


def render_input_hash_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    """Select the exact manifest fields covered by ``render_input_hash``."""

    return {
        "render_contract_version": "2.0.0",
        **{field: manifest[field] for field in RENDER_INPUT_FIELDS},
    }


__all__ = [
    "FIXED_PDF_DATE",
    "RENDER_INPUT_FIELDS",
    "final_pdf_metadata",
    "metadata_policy",
    "render_input_hash_payload",
]
