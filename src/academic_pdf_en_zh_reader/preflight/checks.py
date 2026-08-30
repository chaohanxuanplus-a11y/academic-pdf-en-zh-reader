# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Sandbox-child orchestration for fail-closed PDF preflight."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, fields
from pathlib import Path

from pypdf._page import PageObject
from pypdf._reader import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

from .page_boxes import PageBoxError, inspect_page_boxes
from .pdf_catalog import (
    CatalogLimits,
    clear_page_decoded_stream_caches,
    inspect_catalog,
)

_PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n|\r\n)")
_ERROR_ORDER = (
    "INVALID_SAFE_COPY_PATH",
    "FILE_SIZE_LIMIT_EXCEEDED",
    "PDF_PARSE_ERROR",
    "PDF_ENCRYPTED",
    "PDF_PAGE_TREE_INVALID",
    "PAGE_COUNT_LIMIT_EXCEEDED",
    "PDF_CATALOG_INVALID",
    "PDF_OBJECT_RESOLUTION_ERROR",
    "OBJECT_COUNT_LIMIT_EXCEEDED",
    "RECURSION_DEPTH_LIMIT_EXCEEDED",
    "MALFORMED_PDF_FILTER",
    "UNSUPPORTED_JBIG2",
    "UNSUPPORTED_PDF_FILTER",
    "MALFORMED_PDF_STREAM",
    "MALFORMED_IMAGE",
    "MALFORMED_COLOR_SPACE",
    "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
    "IMAGE_BYTES_LIMIT_EXCEEDED",
    "INVALID_PAGE_BOX",
    "PAGE_BOX_OUTSIDE_MEDIA",
    "UNSUPPORTED_USER_UNIT",
    "INVALID_PAGE_ROTATION",
    "INCONSISTENT_PAGE_ROTATION",
    "TEXT_EXTRACTION_ERROR",
    "TEXT_LAYER_INSUFFICIENT",
    "ENGLISH_TEXT_INSUFFICIENT",
)
_ERROR_RANK = {code: index for index, code in enumerate(_ERROR_ORDER)}
_WARNING_DETAILS = (
    ("javascript", "ACTIVE_JAVASCRIPT_PRESENT", "JavaScript actions were inventoried"),
    ("open_actions", "OPEN_ACTION_PRESENT", "document OpenAction was inventoried"),
    ("attachments", "ATTACHMENT_PRESENT", "embedded attachments were inventoried"),
    ("forms", "FORM_PRESENT", "interactive form fields were inventoried"),
    ("launch_actions", "LAUNCH_ACTION_PRESENT", "launch actions were inventoried"),
    ("rich_media", "RICH_MEDIA_PRESENT", "rich-media annotations were inventoried"),
    ("submit_actions", "SUBMIT_ACTION_PRESENT", "submit actions were inventoried"),
)
_INHERITABLE_PAGE_KEYS = ("/Resources", "/MediaBox", "/CropBox", "/Rotate")


@dataclass(frozen=True, slots=True)
class PreflightLimits:
    """Versioned preflight bounds; small overrides are useful for safe tests."""

    max_file_bytes: int = 100 * 1024 * 1024
    max_pages: int = 500
    max_objects: int = 50_000
    max_recursion_depth: int = 64
    max_decoded_stream_bytes: int = 256 * 1024 * 1024
    max_image_bytes: int = 512 * 1024 * 1024
    min_extractable_characters: int = 40
    min_english_letters: int = 20
    min_english_letter_ratio_ppm: int = 500_000

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{item.name} must be a non-negative integer")
        if self.min_english_letter_ratio_ppm > 1_000_000:
            raise ValueError("min_english_letter_ratio_ppm must not exceed 1000000")


class _Result:
    def __init__(self, *, source_sha256: str, file_bytes: int, limits: PreflightLimits):
        self.source_sha256 = source_sha256
        self.file_bytes = file_bytes
        self.config = limits
        self.checks: list[dict[str, object]] = []
        self.errors: dict[str, str] = {}
        self.pages: list[dict[str, object]] = []
        self.inventory = {
            "javascript": 0,
            "open_actions": 0,
            "attachments": 0,
            "forms": 0,
            "launch_actions": 0,
            "rich_media": 0,
            "submit_actions": 0,
        }
        self.observed = {
            "file_bytes": file_bytes,
            "page_count": 0,
            "object_count": 0,
            "recursion_depth": 0,
            "decompressed_stream_bytes": 0,
            "image_bytes": 0,
        }

    def check(
        self,
        check_id: str,
        passed: bool,
        *,
        error_code: str | None = None,
        details: str | None = None,
    ) -> None:
        record: dict[str, object] = {
            "id": check_id,
            "hard_gate": True,
            "passed": passed,
        }
        if error_code is not None:
            record["error_code"] = error_code
            self.errors.setdefault(error_code, details or error_code)
        if details is not None:
            record["details"] = details[:512]
        self.checks.append(record)

    def reject(self, code: str, details: str) -> None:
        self.errors.setdefault(code, details)

    def finish(self) -> dict[str, object]:
        error_codes = sorted(
            self.errors,
            key=lambda code: (_ERROR_RANK.get(code, len(_ERROR_RANK)), code),
        )
        warnings = [
            {"code": code, "message": message}
            for key, code, message in _WARNING_DETAILS
            if self.inventory[key]
        ]
        maximums = {
            "file_bytes": self.config.max_file_bytes,
            "page_count": self.config.max_pages,
            "object_count": self.config.max_objects,
            "recursion_depth": self.config.max_recursion_depth,
            "decompressed_stream_bytes": self.config.max_decoded_stream_bytes,
            "image_bytes": self.config.max_image_bytes,
        }
        return {
            "schema_version": "1.0.0",
            "artifact_kind": "preflight",
            "source_sha256": self.source_sha256,
            "passed": not error_codes,
            "error_codes": error_codes,
            "warnings": warnings,
            "checks": self.checks,
            "pages": self.pages,
            "inventory": self.inventory,
            "limits": {
                key: {"observed": self.observed[key], "maximum": maximum}
                for key, maximum in maximums.items()
            },
        }


def _hash_open_file(stream) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        total += len(chunk)
    stream.seek(0)
    return digest.hexdigest(), total


def _empty_failed_result(
    code: str, details: str, limits: PreflightLimits
) -> dict[str, object]:
    result = _Result(source_sha256="0" * 64, file_bytes=0, limits=limits)
    result.check("safe-copy", False, error_code=code, details=details)
    return result.finish()


class _PageTreeError(ValueError):
    def __init__(
        self,
        code: str,
        details: str,
        *,
        pages: int = 0,
        nodes: int = 0,
        depth: int = 0,
    ) -> None:
        super().__init__(details)
        self.code = code
        self.details = details
        self.pages = pages
        self.nodes = nodes
        self.depth = depth


def _integer_count(value: object) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 and number == value else None


def _controlled_page_tree(
    reader: PdfReader, limits: PreflightLimits
) -> list[PageObject]:
    try:
        root_reference = reader.trailer.raw_get("/Root")
        root = root_reference.get_object()
        if not isinstance(root, DictionaryObject):
            raise TypeError
        pages_reference = root.raw_get("/Pages")
        pages_root = pages_reference.get_object()
        if not isinstance(pages_root, DictionaryObject):
            raise TypeError
        declared_count = _integer_count(pages_root.get("/Count"))
    except Exception as error:
        raise _PageTreeError(
            "PDF_PAGE_TREE_INVALID", "PDF page-tree root is malformed"
        ) from error
    if declared_count is None or declared_count < 1:
        raise _PageTreeError(
            "PDF_PAGE_TREE_INVALID", "PDF page-tree count is malformed"
        )
    if declared_count > limits.max_pages:
        raise _PageTreeError(
            "PAGE_COUNT_LIMIT_EXCEEDED",
            "declared page count exceeds the configured limit",
            pages=declared_count,
        )

    pages: list[PageObject] = []
    stack: list[tuple[object, int, dict[str, object]]] = [(pages_reference, 0, {})]
    seen_indirect: set[tuple[int, int, int]] = set()
    seen_direct: set[int] = set()
    nodes = 0
    maximum_depth = 0
    while stack:
        value, depth, inherited = stack.pop()
        maximum_depth = max(maximum_depth, depth)
        if depth > limits.max_recursion_depth:
            raise _PageTreeError(
                "RECURSION_DEPTH_LIMIT_EXCEEDED",
                "PDF page tree exceeds the recursion-depth limit",
                pages=len(pages),
                nodes=nodes,
                depth=limits.max_recursion_depth + 1,
            )
        reference = value if isinstance(value, IndirectObject) else None
        if reference is not None:
            identity = (reference.idnum, reference.generation, id(reference.pdf))
            if identity in seen_indirect:
                raise _PageTreeError(
                    "PDF_PAGE_TREE_INVALID", "PDF page tree contains a cycle"
                )
            seen_indirect.add(identity)
            try:
                value = reference.get_object()
            except Exception as error:
                raise _PageTreeError(
                    "PDF_PAGE_TREE_INVALID", "PDF page-tree node cannot be resolved"
                ) from error
        if not isinstance(value, DictionaryObject):
            raise _PageTreeError(
                "PDF_PAGE_TREE_INVALID", "PDF page-tree node is malformed"
            )
        direct_identity = id(value)
        if direct_identity in seen_direct:
            raise _PageTreeError(
                "PDF_PAGE_TREE_INVALID", "PDF page tree contains a cycle"
            )
        seen_direct.add(direct_identity)
        nodes += 1
        if nodes > limits.max_objects:
            raise _PageTreeError(
                "OBJECT_COUNT_LIMIT_EXCEEDED",
                "PDF page-tree nodes exceed the object-count limit",
                pages=len(pages),
                nodes=limits.max_objects + 1,
                depth=maximum_depth,
            )

        node_type = value.get("/Type")
        if not isinstance(node_type, NameObject):
            raise _PageTreeError(
                "PDF_PAGE_TREE_INVALID", "PDF page-tree node type is malformed"
            )
        if node_type == "/Page":
            page = PageObject(reader)
            page.update(value)
            page.indirect_reference = reference
            for key, inherited_value in inherited.items():
                if key not in page:
                    page[NameObject(key)] = inherited_value
            pages.append(page)
            if len(pages) > limits.max_pages:
                raise _PageTreeError(
                    "PAGE_COUNT_LIMIT_EXCEEDED",
                    "reachable pages exceed the configured limit",
                    pages=limits.max_pages + 1,
                    nodes=nodes,
                    depth=maximum_depth,
                )
            continue
        if node_type != "/Pages":
            raise _PageTreeError(
                "PDF_PAGE_TREE_INVALID", "PDF page-tree node type is unsupported"
            )
        next_inherited = dict(inherited)
        for key in _INHERITABLE_PAGE_KEYS:
            if key in value:
                next_inherited[key] = value.raw_get(key)
        try:
            children = value["/Kids"]
        except Exception as error:
            raise _PageTreeError(
                "PDF_PAGE_TREE_INVALID", "PDF page-tree kids are malformed"
            ) from error
        if not isinstance(children, ArrayObject) or not children:
            raise _PageTreeError(
                "PDF_PAGE_TREE_INVALID", "PDF page-tree kids are malformed"
            )
        if nodes + len(stack) + len(children) > limits.max_objects:
            raise _PageTreeError(
                "OBJECT_COUNT_LIMIT_EXCEEDED",
                "PDF page-tree nodes exceed the object-count limit",
                pages=len(pages),
                nodes=limits.max_objects + 1,
                depth=maximum_depth,
            )
        stack.extend((child, depth + 1, next_inherited) for child in reversed(children))

    if declared_count != len(pages):
        raise _PageTreeError(
            "PDF_PAGE_TREE_INVALID",
            "declared and reachable page counts disagree",
            pages=len(pages),
            nodes=nodes,
            depth=maximum_depth,
        )
    return pages


def _inspect_pages(pages: list[PageObject], result: _Result) -> None:
    rotations: list[int] = []
    geometry_errors: list[PageBoxError] = []
    extraction_errors: list[int] = []
    total_characters = 0
    total_english_letters = 0
    total_letters = 0

    for number, page in enumerate(pages, start=1):
        try:
            geometry = inspect_page_boxes(page)
        except PageBoxError as exc:
            geometry_errors.append(exc)
            continue
        rotations.append(geometry.rotation_degrees)
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
            extraction_errors.append(number)
        finally:
            if (
                not clear_page_decoded_stream_caches(page, result.config.max_objects)
                and number not in extraction_errors
            ):
                extraction_errors.append(number)
        character_count = sum(not character.isspace() for character in text)
        english_letters = sum(
            ("A" <= character <= "Z") or ("a" <= character <= "z") for character in text
        )
        all_letters = sum(character.isalpha() for character in text)
        total_characters += character_count
        total_english_letters += english_letters
        total_letters += all_letters
        result.pages.append(
            {
                "page_number": number,
                "width_mpt": geometry.width_mpt,
                "height_mpt": geometry.height_mpt,
                "media_box_mpt": list(geometry.media_box_mpt),
                "crop_box_mpt": list(geometry.crop_box_mpt),
                "rotation_degrees": geometry.rotation_degrees,
                "extractable_character_count": character_count,
            }
        )

    if geometry_errors:
        first = geometry_errors[0]
        for error in geometry_errors:
            result.reject(error.code, error.details)
        result.check(
            "page-geometry",
            False,
            error_code=first.code,
            details=first.details,
        )
    else:
        result.check("page-geometry", True)

    if rotations and len(set(rotations)) > 1:
        result.check(
            "page-rotation-consistency",
            False,
            error_code="INCONSISTENT_PAGE_ROTATION",
            details="page rotations are not consistent across the document",
        )
    elif not geometry_errors:
        result.check("page-rotation-consistency", True)

    if geometry_errors:
        return

    required_characters = result.config.min_extractable_characters * len(pages)
    required_english_letters = result.config.min_english_letters * len(pages)
    if extraction_errors:
        result.check(
            "text-extraction",
            False,
            error_code="TEXT_EXTRACTION_ERROR",
            details="text extraction failed on one or more pages",
        )
    elif total_characters < required_characters:
        result.check(
            "text-layer",
            False,
            error_code="TEXT_LAYER_INSUFFICIENT",
            details="document has too little extractable text",
        )
    else:
        result.check("text-layer", True)

    english_ratio_ppm = (
        total_english_letters * 1_000_000 // total_letters if total_letters else 0
    )
    if (
        not extraction_errors
        and total_characters >= required_characters
        and (
            total_english_letters < required_english_letters
            or english_ratio_ppm < result.config.min_english_letter_ratio_ppm
        )
    ):
        result.check(
            "english-text",
            False,
            error_code="ENGLISH_TEXT_INSUFFICIENT",
            details="document does not meet the English-text threshold",
        )
    elif not extraction_errors and total_characters >= required_characters:
        result.check("english-text", True)


def preflight_safe_copy(
    safe_copy_path: Path, *, limits: PreflightLimits | None = None
) -> dict[str, object]:
    """Inspect one trusted worker-local safe-copy path without executing content.

    This function is a child-side primitive, not a parent-process convenience API.
    The caller must first establish the G2 sandbox and private input copy.
    """

    config = limits or PreflightLimits()
    if not isinstance(safe_copy_path, Path) or not safe_copy_path.is_absolute():
        return _empty_failed_result(
            "INVALID_SAFE_COPY_PATH",
            "safe-copy input must be an absolute pathlib.Path",
            config,
        )
    try:
        if safe_copy_path.is_symlink():
            return _empty_failed_result(
                "INVALID_SAFE_COPY_PATH",
                "safe-copy input must not be a symbolic link",
                config,
            )
        stream = safe_copy_path.open("rb")
    except OSError:
        return _empty_failed_result(
            "INVALID_SAFE_COPY_PATH", "safe-copy input could not be opened", config
        )

    with stream:
        try:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return _empty_failed_result(
                    "INVALID_SAFE_COPY_PATH",
                    "safe-copy input is not a regular file",
                    config,
                )
            source_sha256, file_bytes = _hash_open_file(stream)
        except OSError:
            return _empty_failed_result(
                "INVALID_SAFE_COPY_PATH", "safe-copy input could not be read", config
            )
        result = _Result(
            source_sha256=source_sha256, file_bytes=file_bytes, limits=config
        )
        if file_bytes > config.max_file_bytes:
            result.check(
                "file-size",
                False,
                error_code="FILE_SIZE_LIMIT_EXCEEDED",
                details="safe-copy byte size exceeds the configured limit",
            )
            return result.finish()
        result.check("file-size", True)

        header = stream.read(10)
        stream.seek(0)
        if _PDF_HEADER.match(header) is None:
            result.check(
                "pdf-syntax",
                False,
                error_code="PDF_PARSE_ERROR",
                details="strict PDF header is absent or unsupported",
            )
            return result.finish()
        try:
            reader = PdfReader(stream, strict=True, root_object_recovery_limit=0)
        except Exception:
            result.check(
                "pdf-syntax",
                False,
                error_code="PDF_PARSE_ERROR",
                details="strict PDF parser rejected the input",
            )
            return result.finish()
        result.check("pdf-syntax", True)

        if reader.is_encrypted or "/Encrypt" in reader.trailer:
            result.check(
                "encryption",
                False,
                error_code="PDF_ENCRYPTED",
                details="encrypted PDFs are not accepted",
            )
            return result.finish()
        result.check("encryption", True)

        try:
            pages = _controlled_page_tree(reader, config)
        except _PageTreeError as error:
            result.observed["page_count"] = error.pages
            result.observed["object_count"] = error.nodes
            result.observed["recursion_depth"] = error.depth
            result.check(
                "page-tree",
                False,
                error_code=error.code,
                details=error.details,
            )
            return result.finish()
        result.observed["page_count"] = len(pages)
        result.check("page-count", True)

        catalog = inspect_catalog(
            reader,
            CatalogLimits(
                max_objects=config.max_objects,
                max_recursion_depth=config.max_recursion_depth,
                max_decoded_stream_bytes=config.max_decoded_stream_bytes,
                max_image_bytes=config.max_image_bytes,
            ),
            pages=pages,
        )
        result.inventory = catalog.inventory
        result.observed.update(
            {
                "object_count": catalog.object_count,
                "recursion_depth": catalog.recursion_depth,
                "decompressed_stream_bytes": catalog.decoded_stream_bytes,
                "image_bytes": catalog.image_bytes,
            }
        )
        if catalog.errors:
            for code, details in catalog.errors:
                result.reject(code, details)
            first_code, first_details = catalog.errors[0]
            result.check(
                "bounded-object-graph",
                False,
                error_code=first_code,
                details=first_details,
            )
            return result.finish()
        result.check("bounded-object-graph", True)

        _inspect_pages(pages, result)
        return result.finish()


def run_preflight_in_worker(
    safe_copy_path: Path, *, limits: PreflightLimits | None = None
) -> dict[str, object]:
    """Worker handler entry point; the bridge must import it only in the child."""

    return preflight_safe_copy(safe_copy_path, limits=limits)
