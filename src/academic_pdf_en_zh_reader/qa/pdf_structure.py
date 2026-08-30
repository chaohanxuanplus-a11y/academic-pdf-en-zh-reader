# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Bounded final-PDF security and no-raster-substitution gates."""

from __future__ import annotations

from collections.abc import Mapping

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
)

from academic_pdf_en_zh_reader.preflight.pdf_catalog import (
    CatalogLimits,
    inspect_catalog,
)
from academic_pdf_en_zh_reader.qa.image_fingerprints import (
    ImageFingerprintError,
    page_image_fingerprints,
)

_CATALOG_LIMITS = CatalogLimits(
    max_objects=50_000,
    max_recursion_depth=64,
    max_decoded_stream_bytes=256 * 1024 * 1024,
    max_image_bytes=512 * 1024 * 1024,
)
_FORBIDDEN_KEYS = frozenset(
    {
        "/OpenAction",
        "/AA",
        "/JS",
        "/EmbeddedFiles",
        "/AcroForm",
    }
)
_FORBIDDEN_NAMES = frozenset(
    {"/JavaScript", "/Launch", "/SubmitForm", "/RichMedia", "/Filespec"}
)


class PdfStructureQaError(ValueError):
    """A stable final-PDF structure failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _resolved(value: object) -> object:
    return value.get_object() if isinstance(value, IndirectObject) else value


def _contains_forbidden_pdf_name(reader: PdfReader) -> bool:
    """Inspect names/keys across the reachable object graph with finite bounds."""

    stack: list[tuple[object, int]] = [(reader.trailer, 0)]
    seen_indirect: set[tuple[int, int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0
    while stack:
        value, depth = stack.pop()
        if depth > _CATALOG_LIMITS.max_recursion_depth:
            return True
        if isinstance(value, IndirectObject):
            identity = (value.idnum, value.generation, id(value.pdf))
            if identity in seen_indirect:
                continue
            seen_indirect.add(identity)
            try:
                value = value.get_object()
            except Exception:
                return True
        elif isinstance(value, (DictionaryObject, ArrayObject)):
            identity = id(value)
            if identity in seen_direct:
                continue
            seen_direct.add(identity)
        visited += 1
        if visited > _CATALOG_LIMITS.max_objects:
            return True
        if isinstance(value, DictionaryObject):
            for key, child in value.items():
                if str(key) in _FORBIDDEN_KEYS:
                    return True
                if isinstance(child, NameObject) and str(child) in _FORBIDDEN_NAMES:
                    return True
                stack.append((child, depth + 1))
        elif isinstance(value, ArrayObject):
            stack.extend((child, depth + 1) for child in value)
    return False


def validate_no_active_content(reader: PdfReader) -> dict[str, int]:
    """Reject every active-content family explicitly required by policy."""

    if reader.is_encrypted or not reader.pages:
        raise PdfStructureQaError("PDF_ACTIVE_CONTENT")
    try:
        pages = list(reader.pages)
        facts = inspect_catalog(reader, _CATALOG_LIMITS, pages=pages)
    except Exception as exc:
        raise PdfStructureQaError("PDF_ACTIVE_CONTENT") from exc
    if (
        facts.errors
        or any(facts.inventory.values())
        or _contains_forbidden_pdf_name(reader)
    ):
        raise PdfStructureQaError("PDF_ACTIVE_CONTENT")
    return {"page_count": len(pages), "object_count": facts.object_count}


def _declared_overlay_fingerprints(mapping: Mapping[str, object]) -> list[str]:
    declared = mapping.get("overlay_image_fingerprints")
    if not isinstance(declared, list):
        raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
    if any(
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        for fingerprint in declared
    ):
        raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
    if declared != sorted(declared):
        raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
    return declared


def validate_no_new_page_rasters(
    source_reader: PdfReader,
    output_reader: PdfReader,
    render_manifest: Mapping[str, object],
) -> dict[str, int]:
    """Forbid any output image absent from the corresponding frozen source page."""

    try:
        mappings = render_manifest["pages"]
        if not isinstance(mappings, list) or len(mappings) != len(output_reader.pages):
            raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
        source_images = [page_image_fingerprints(page) for page in source_reader.pages]
        output_image_count = 0
        for output_page_number, (output_page, mapping) in enumerate(
            zip(output_reader.pages, mappings, strict=True),
            start=1,
        ):
            if not isinstance(mapping, Mapping) or (
                mapping.get("output_page_number") != output_page_number
                or isinstance(mapping.get("source_page_number"), bool)
                or not isinstance(mapping.get("source_page_number"), int)
            ):
                raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
            source_index = mapping["source_page_number"] - 1
            if not 0 <= source_index < len(source_images):
                raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
            declared = _declared_overlay_fingerprints(mapping)
            expected_images = source_images[source_index].copy()
            expected_images.update(declared)
            output_images = page_image_fingerprints(output_page)
            if output_images != expected_images:
                raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION")
            output_image_count += sum(output_images.values())
    except PdfStructureQaError:
        raise
    except ImageFingerprintError as exc:
        raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION") from exc
    except Exception as exc:
        raise PdfStructureQaError("PDF_RASTER_SUBSTITUTION") from exc
    return {"image_count": output_image_count}


__all__ = [
    "PdfStructureQaError",
    "validate_no_active_content",
    "validate_no_new_page_rasters",
]
