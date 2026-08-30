# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Parent-only bridge for geometry extraction from a normalized PDF."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    ConcurrentStateError,
    write_immutable_bytes,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.security.input_copy import (
    SafeInputCopy,
    UnsafeInputError,
    copy_untrusted_input,
    read_bounded_regular_file,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    EXTRACTION_POLICY_VERSION,
    WorkerRequest,
)

_ARTIFACT_NAME = "extraction.json"
_PREFLIGHT_NAME = "preflight.json"
_NORMALIZATION_NAME = "normalization.json"
_A4_MEDIA_BOX_MPT = [0, 0, 595_276, 841_890]
_CAPTION_TEXT = re.compile(
    r"^(Figure|Fig\.?|Table)\s+([0-9]+)\b",
    re.IGNORECASE | re.ASCII,
)
_REFERENCE_TEXT = re.compile(
    r"\b(Figure|Fig\.?|Table)\s+([0-9]+)\b",
    re.IGNORECASE | re.ASCII,
)
_CID_PLACEHOLDER = re.compile(r"\(cid:([0-9]+)\)", re.ASCII)
_MAX_CID_VALUE = 65_535
_MAX_CID_REPRESENTATION_BUDGET_PER_ITEM = 10
_MAX_CID_REPRESENTATION_BUDGET_PER_PAGE = 512
_MAX_CID_REPRESENTATION_BUDGET_PER_DOCUMENT = 2_048
_ERROR_MESSAGES = {
    "INPUT_REJECTED": "The input file was rejected.",
    "PREFLIGHT_ARTIFACT_INVALID": "The passing preflight artifact is invalid.",
    "NORMALIZATION_ARTIFACT_INVALID": "The normalization artifact is invalid.",
    "SANDBOX_UNAVAILABLE": "The required isolated PDF worker is unavailable.",
    "SANDBOX_CONTRACT_UNVERIFIED": (
        "The isolated worker contract could not be verified."
    ),
    "WORKER_LIMIT_EXCEEDED": "The isolated PDF worker exceeded a resource limit.",
    "WORKER_FAILED": "The isolated PDF worker failed.",
    "SCANNED_PDF_UNSUPPORTED": (
        "Scanned or OCR-overlay PDFs are not supported in this version."
    ),
    "EXTRACTION_ARTIFACT_INVALID": "The isolated extraction result was invalid.",
    "ARTIFACT_EXISTS": "The immutable extraction artifact already exists.",
}


class _StableBridgeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error(code: str) -> dict[str, object]:
    return {
        "status": "error",
        "error": {"code": code, "message": _ERROR_MESSAGES[code]},
    }


def _run_platform_worker(
    request: WorkerRequest,
    workspace: Path,
    *,
    limits: WorkerLimits,
):
    if os.name != "nt":
        raise _StableBridgeError("SANDBOX_UNAVAILABLE")
    from academic_pdf_en_zh_reader.security.windows_worker import (
        SandboxUnavailableError,
        WorkerCpuLimitError,
        WorkerExecutionError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerReportedError,
        WorkerTimeoutError,
        run_worker,
    )

    try:
        return run_worker(request, workspace, limits=limits)
    except WorkerReportedError as error:
        if error.code in {
            "SANDBOX_CONTRACT_UNVERIFIED",
            "SCANNED_PDF_UNSUPPORTED",
            "WORKER_LIMIT_EXCEEDED",
        }:
            raise _StableBridgeError(error.code) from error
        raise _StableBridgeError("WORKER_FAILED") from error
    except SandboxUnavailableError as error:
        raise _StableBridgeError("SANDBOX_UNAVAILABLE") from error
    except (
        WorkerCpuLimitError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerTimeoutError,
    ) as error:
        raise _StableBridgeError("WORKER_LIMIT_EXCEEDED") from error
    except WorkerExecutionError as error:
        raise _StableBridgeError("WORKER_FAILED") from error


def _decode_json(encoded: bytes) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    decoded = json.loads(
        encoded.decode("utf-8", errors="strict"),
        parse_constant=reject_constant,
    )
    if not isinstance(decoded, dict):
        raise ValueError("artifact root must be an object")
    return decoded


def _character_count_bounds(
    expected: int,
    *,
    absolute_tolerance: int,
    zero_expected_ceiling: int,
    representation_budget: int = 0,
) -> tuple[int, int]:
    if expected == 0:
        return 0, zero_expected_ceiling + representation_budget
    proportional_minimum = (expected * 9 + 9) // 10
    proportional_maximum = (expected * 11 + 9) // 10
    return (
        max(
            1,
            proportional_minimum - representation_budget,
            expected - absolute_tolerance - representation_budget,
        ),
        min(
            proportional_maximum + representation_budget,
            expected + absolute_tolerance + representation_budget,
        ),
    )


def _cid_representation_budget(characters: list[dict[str, Any]]) -> int:
    budget = 0
    for item in characters:
        text = item["text"]
        match = _CID_PLACEHOLDER.fullmatch(text)
        if match is None:
            continue
        digits = match.group(1)
        item_budget = len(text) - 1
        if (
            len(digits) > 5
            or int(digits) > _MAX_CID_VALUE
            or item_budget > _MAX_CID_REPRESENTATION_BUDGET_PER_ITEM
        ):
            raise ValueError("CID placeholder exceeds its representation limit")
        budget += item_budget
        if budget > _MAX_CID_REPRESENTATION_BUDGET_PER_PAGE:
            raise ValueError("page CID representation budget exceeds its hard limit")
    return budget


def _read_valid_preflight(
    job_root: Path,
    *,
    limits: WorkerLimits,
) -> tuple[dict[str, Any], bytes, str]:
    bounded = read_bounded_regular_file(
        job_root / _PREFLIGHT_NAME,
        max_bytes=limits.max_result_object_bytes,
    )
    artifact = _decode_json(bounded.data)
    validate_artifact("preflight", artifact)
    if canonical_json_bytes(artifact) != bounded.data or artifact["passed"] is not True:
        raise ValueError("preflight must be canonical and passing")
    expected_maximums = {
        "file_bytes": limits.max_input_bytes,
        "page_count": limits.max_pages,
        "object_count": limits.max_objects,
        "recursion_depth": limits.max_recursion_depth,
        "decompressed_stream_bytes": limits.max_uncompressed_bytes,
        "image_bytes": limits.max_image_bytes,
    }
    observed_limits = artifact["limits"]
    if any(
        observed_limits[name]["maximum"] != maximum
        for name, maximum in expected_maximums.items()
    ):
        raise ValueError("preflight policy differs")
    pages = artifact["pages"]
    if observed_limits["page_count"]["observed"] != len(pages) or [
        page["page_number"] for page in pages
    ] != list(range(1, len(pages) + 1)):
        raise ValueError("preflight page inventory differs")
    return artifact, bounded.data, bounded.sha256


def _read_valid_normalization(
    job_root: Path,
    *,
    preflight: dict[str, Any],
    preflight_sha256: str,
    limits: WorkerLimits,
) -> tuple[dict[str, Any], bytes, str]:
    bounded = read_bounded_regular_file(
        job_root / _NORMALIZATION_NAME,
        max_bytes=limits.max_result_object_bytes,
    )
    artifact = _decode_json(bounded.data)
    validate_artifact("normalization", artifact)
    if canonical_json_bytes(artifact) != bounded.data:
        raise ValueError("normalization must be canonical")
    pages = artifact["pages"]
    raw_pages = preflight["pages"]
    if (
        artifact["source_sha256"] != preflight["source_sha256"]
        or artifact["preflight_sha256"] != preflight_sha256
        or len(pages) != len(raw_pages)
    ):
        raise ValueError("normalization lineage differs")
    for page_number, (page, raw_page) in enumerate(
        zip(pages, raw_pages, strict=True), start=1
    ):
        if (
            page["page_number"] != page_number
            or raw_page["page_number"] != page_number
            or page["source_media_box_mpt"] != raw_page["media_box_mpt"]
            or page["source_crop_box_mpt"] != raw_page["crop_box_mpt"]
            or page["source_rotation_degrees"] != raw_page["rotation_degrees"]
            or page["displayed_width_mpt"] != raw_page["width_mpt"]
            or page["displayed_height_mpt"] != raw_page["height_mpt"]
        ):
            raise ValueError("normalization page lineage differs")
    return artifact, bounded.data, bounded.sha256


def _expected_normalized_pages(
    preflight: dict[str, Any],
    normalization: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "page_number": raw_page["page_number"],
            "width_mpt": _A4_MEDIA_BOX_MPT[2],
            "height_mpt": _A4_MEDIA_BOX_MPT[3],
            "media_box_mpt": list(_A4_MEDIA_BOX_MPT),
            "crop_box_mpt": list(_A4_MEDIA_BOX_MPT),
            "rotation_degrees": 0,
            "extractable_character_count": raw_page["extractable_character_count"],
        }
        for raw_page, _normalized_page in zip(
            preflight["pages"], normalization["pages"], strict=True
        )
    ]


def _write_handoff(root: Path, name: str, encoded: bytes) -> None:
    destination = root / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(encoded)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("artifact handoff write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_preflight_handoff(root: Path, encoded: bytes) -> None:
    """Compatibility wrapper shared by the normalization parent bridge."""

    _write_handoff(root, _PREFLIGHT_NAME, encoded)


def _is_int(value: object) -> bool:
    return type(value) is int


def _validate_mpt_fields(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _validate_mpt_fields(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key.endswith("_box_mpt") or key == "bbox_mpt":
            if (
                not isinstance(item, list)
                or len(item) != 4
                or not all(_is_int(coordinate) for coordinate in item)
            ):
                raise ValueError("mpt box must contain four integers")
        elif key.endswith("_mpt") and not _is_int(item):
            raise ValueError("mpt scalar must be an integer")
        _validate_mpt_fields(item)


def _valid_text(value: object, *, maximum: int = 65_536) -> bool:
    return isinstance(value, str) and len(value) <= maximum and "\x00" not in value


def _normalized_label(match: re.Match[str]) -> tuple[str, int]:
    kind = "table" if match.group(1).lower() == "table" else "figure"
    return kind, int(match.group(2))


def _validate_color(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("color must be null or a bounded array")
    for component in value:
        if _is_int(component):
            if abs(component) > 1_000_000_000:
                raise ValueError("color integer is out of bounds")
        elif not _valid_text(component, maximum=256):
            raise ValueError("color component is invalid")


def _validate_box(value: object) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(_is_int(coordinate) for coordinate in value)
        or value[0] > value[2]
        or value[1] > value[3]
    ):
        raise ValueError("box coordinates are invalid")


def _box_contains(outer: list[int], inner: list[int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _displayed_axis_box(box: list[int], rotation: int) -> list[int]:
    if rotation in {90, 270}:
        return [box[1], box[0], box[3], box[2]]
    return list(box)


def _expected_child_page_boxes(
    expected_page: dict[str, Any],
) -> tuple[list[int], list[int]]:
    media = expected_page.get("media_box_mpt")
    crop = expected_page.get("crop_box_mpt")
    rotation = expected_page.get("rotation_degrees")
    _validate_box(media)
    _validate_box(crop)
    if (
        not _is_int(rotation)
        or rotation not in {0, 90, 180, 270}
        or media[0] >= media[2]
        or media[1] >= media[3]
        or crop[0] >= crop[2]
        or crop[1] >= crop[3]
        or not _box_contains(media, crop)
    ):
        raise ValueError("preflight page boxes are invalid")

    displayed_media = _displayed_axis_box(media, rotation)
    displayed_crop = _displayed_axis_box(crop, rotation)
    media_height = displayed_media[3] - displayed_media[1]

    def top_box(box: list[int]) -> list[int]:
        return [box[0], media_height - box[3], box[2], media_height - box[1]]

    media_top = top_box(displayed_media)
    crop_top = top_box(displayed_crop)
    relative_media = [
        media_top[0] - crop_top[0],
        crop_top[3] - media_top[3],
        media_top[2] - crop_top[0],
        crop_top[3] - media_top[1],
    ]
    relative_crop = [
        0,
        0,
        crop_top[2] - crop_top[0],
        crop_top[3] - crop_top[1],
    ]
    if (
        expected_page.get("width_mpt") != relative_crop[2]
        or expected_page.get("height_mpt") != relative_crop[3]
    ):
        raise ValueError("preflight displayed geometry is inconsistent")
    return relative_media, relative_crop


def _require_fields(item: dict[str, Any], expected: set[str]) -> None:
    if set(item) != expected:
        raise ValueError("extraction object fields differ")


def _validate_page_items(
    page: dict[str, Any],
    page_number: int,
    identifiers: set[str],
    *,
    crop_box: list[int],
    limits: WorkerLimits,
) -> None:
    patterns = {
        "chars": rf"p{page_number:04d}-char-\d{{6,}}",
        "rectangles": rf"p{page_number:04d}-rect-\d{{6,}}",
        "curves": rf"p{page_number:04d}-curve-\d{{6,}}",
        "images": rf"p{page_number:04d}-image-\d{{6,}}",
        "lines": rf"p{page_number:04d}-line-\d{{5,}}",
        "graphic_regions": rf"p{page_number:04d}-(figure|table)-\d{{4,}}",
        "captions": rf"p{page_number:04d}-(figure|table)-caption-\d{{4,}}",
        "references": rf"p{page_number:04d}-reference-\d{{4,}}",
    }
    expected_fields = {
        "chars": {
            "id",
            "page_number",
            "text",
            "bbox_mpt",
            "font_name",
            "font_size_mpt",
            "fill_color",
            "stroke_color",
            "upright",
        },
        "rectangles": {
            "id",
            "page_number",
            "source_kind",
            "bbox_mpt",
            "line_width_mpt",
            "fill_color",
            "stroke_color",
            "filled",
            "stroked",
        },
        "curves": {
            "id",
            "page_number",
            "source_kind",
            "bbox_mpt",
            "line_width_mpt",
            "fill_color",
            "stroke_color",
            "filled",
            "stroked",
        },
        "images": {
            "id",
            "page_number",
            "bbox_mpt",
            "pixel_width",
            "pixel_height",
        },
        "lines": {
            "id",
            "page_number",
            "text",
            "bbox_mpt",
            "character_ids",
            "font_names",
            "fill_colors",
            "max_font_size_mpt",
            "confidence_ppm",
            "body_eligible",
            "coverage_eligible",
            "exclusion_kind",
            "container_kind",
            "container_id",
        },
        "graphic_regions": {
            "id",
            "page_number",
            "kind",
            "bbox_mpt",
            "evidence",
        },
        "captions": {
            "id",
            "page_number",
            "kind",
            "number",
            "text",
            "line_ids",
            "target_id",
        },
        "references": {
            "id",
            "page_number",
            "line_id",
            "label",
            "target_id",
        },
    }
    for collection, pattern in patterns.items():
        for item in page[collection]:
            _require_fields(item, expected_fields[collection])
            identifier = item["id"]
            if (
                not isinstance(identifier, str)
                or re.fullmatch(pattern, identifier, flags=re.ASCII) is None
            ):
                raise ValueError("extraction object identifier has an invalid format")
            if identifier in identifiers:
                raise ValueError("extraction object identifiers are not unique")
            identifiers.add(identifier)
            if item["page_number"] != page_number:
                raise ValueError("extraction object page identity differs")
    sequential_ids = {
        "chars": ("char", 6),
        "rectangles": ("rect", 6),
        "curves": ("curve", 6),
        "images": ("image", 6),
        "lines": ("line", 5),
        "references": ("reference", 4),
    }
    for collection, (label, width) in sequential_ids.items():
        for ordinal, item in enumerate(page[collection], start=1):
            if item["id"] != (f"p{page_number:04d}-{label}-{ordinal:0{width}d}"):
                raise ValueError("extraction object identifiers are not sequential")

    for character in page["chars"]:
        _validate_box(character["bbox_mpt"])
        if (
            not _valid_text(character["text"])
            or not _valid_text(character["font_name"], maximum=1024)
            or not _is_int(character["font_size_mpt"])
            or character["font_size_mpt"] < 0
            or type(character["upright"]) is not bool
        ):
            raise ValueError("character fields are invalid")
        if not _box_contains(crop_box, character["bbox_mpt"]):
            raise ValueError("character lies outside the visible page")
        _validate_color(character["fill_color"])
        _validate_color(character["stroke_color"])
    for vector in [*page["rectangles"], *page["curves"]]:
        _validate_box(vector["bbox_mpt"])
        if (
            not _valid_text(vector["source_kind"], maximum=128)
            or not _is_int(vector["line_width_mpt"])
            or vector["line_width_mpt"] < 0
            or type(vector["filled"]) is not bool
            or type(vector["stroked"]) is not bool
        ):
            raise ValueError("vector fields are invalid")
        if not _box_contains(crop_box, vector["bbox_mpt"]):
            raise ValueError("vector lies outside the visible page")
        _validate_color(vector["fill_color"])
        _validate_color(vector["stroke_color"])
    for image in page["images"]:
        _validate_box(image["bbox_mpt"])
        if not _box_contains(crop_box, image["bbox_mpt"]):
            raise ValueError("image lies outside the visible page")
        width = image["pixel_width"]
        height = image["pixel_height"]
        if (width is None) != (height is None) or (
            width is not None
            and (
                not _is_int(width)
                or not _is_int(height)
                or width <= 0
                or height <= 0
                or width * height > limits.max_image_bytes * 8
            )
        ):
            raise ValueError("image dimensions are invalid")

    character_by_id = {item["id"]: item for item in page["chars"]}
    character_ids = set(character_by_id)
    line_ids = {item["id"] for item in page["lines"]}
    region_by_id = {item["id"]: item["kind"] for item in page["graphic_regions"]}
    assigned_character_ids: set[str] = set()
    for line in page["lines"]:
        _validate_box(line["bbox_mpt"])
        if (
            not _valid_text(line["text"])
            or not line["text"]
            or not isinstance(line["character_ids"], list)
            or not line["character_ids"]
            or not all(item in character_ids for item in line["character_ids"])
            or len(set(line["character_ids"])) != len(line["character_ids"])
            or not isinstance(line["font_names"], list)
            or not all(_valid_text(item, maximum=1024) for item in line["font_names"])
            or not isinstance(line["fill_colors"], list)
            or not _is_int(line["max_font_size_mpt"])
            or line["max_font_size_mpt"] < 0
            or not _is_int(line["confidence_ppm"])
            or not 0 <= line["confidence_ppm"] <= 1_000_000
            or type(line["body_eligible"]) is not bool
            or type(line["coverage_eligible"]) is not bool
            or (
                line["exclusion_kind"] is not None
                and not _valid_text(line["exclusion_kind"], maximum=128)
            )
        ):
            raise ValueError("line fields are invalid")
        if assigned_character_ids.intersection(line["character_ids"]):
            raise ValueError("character belongs to more than one line")
        assigned_character_ids.update(line["character_ids"])
        line_characters = [character_by_id[item] for item in line["character_ids"]]
        ordered_character_ids = [
            item["id"]
            for item in sorted(
                line_characters,
                key=lambda item: (item["bbox_mpt"][0], item["id"]),
            )
        ]
        pieces: list[str] = []
        previous: dict[str, Any] | None = None
        for character in line_characters:
            if previous is not None:
                gap = character["bbox_mpt"][0] - previous["bbox_mpt"][2]
                word_gap = max(
                    800,
                    3
                    * min(
                        previous["font_size_mpt"],
                        character["font_size_mpt"],
                    )
                    // 20,
                )
                if gap > word_gap:
                    pieces.append(" ")
            pieces.append(character["text"])
            previous = character
        expected_text = "".join(pieces).strip()
        expected_bbox = [
            min(item["bbox_mpt"][0] for item in line_characters),
            min(item["bbox_mpt"][1] for item in line_characters),
            max(item["bbox_mpt"][2] for item in line_characters),
            max(item["bbox_mpt"][3] for item in line_characters),
        ]
        expected_fonts = sorted({item["font_name"] for item in line_characters})
        expected_colors: list[object] = []
        for item in line_characters:
            color = item["fill_color"]
            if color not in expected_colors:
                expected_colors.append(color)
        expected_colors.sort(key=repr)
        expected_size = max(item["font_size_mpt"] for item in line_characters)
        if (
            line["bbox_mpt"] != expected_bbox
            or not _box_contains(crop_box, line["bbox_mpt"])
            or line["character_ids"] != ordered_character_ids
            or line["text"] != expected_text
            or line["font_names"] != expected_fonts
            or line["fill_colors"] != expected_colors
            or line["max_font_size_mpt"] != expected_size
        ):
            raise ValueError("line facts differ from their characters")
        for color in line["fill_colors"]:
            _validate_color(color)
        kind = line["container_kind"]
        container_id = line["container_id"]
        if (kind is None) != (container_id is None) or (
            container_id is not None and region_by_id.get(container_id) != kind
        ):
            raise ValueError("line container reference is invalid")
    nonspace_character_ids = {
        item["id"]
        for item in page["chars"]
        if any(not character.isspace() for character in item["text"])
    }
    if not nonspace_character_ids.issubset(assigned_character_ids):
        raise ValueError("non-whitespace character is not assigned to a line")
    for region in page["graphic_regions"]:
        _validate_box(region["bbox_mpt"])
        if region["kind"] not in {"figure", "table"} or not _valid_text(
            region["evidence"], maximum=256
        ):
            raise ValueError("graphic region fields are invalid")
        if not _box_contains(crop_box, region["bbox_mpt"]):
            raise ValueError("graphic region lies outside the visible page")
    line_by_id = {item["id"]: item for item in page["lines"]}
    claimed_caption_lines: set[str] = set()
    for caption in page["captions"]:
        caption_match = (
            _CAPTION_TEXT.match(caption["text"])
            if isinstance(caption.get("text"), str)
            else None
        )
        if (
            caption["kind"] not in {"figure", "table"}
            or not _is_int(caption["number"])
            or caption["number"] < 0
            or not _valid_text(caption["text"])
            or not isinstance(caption["line_ids"], list)
            or not caption["line_ids"]
            or not all(item in line_ids for item in caption["line_ids"])
            or len(set(caption["line_ids"])) != len(caption["line_ids"])
            or region_by_id.get(caption["target_id"]) != caption["kind"]
            or claimed_caption_lines.intersection(caption["line_ids"])
            or caption["text"]
            != " ".join(line_by_id[item]["text"] for item in caption["line_ids"])
            or caption_match is None
            or _normalized_label(caption_match) != (caption["kind"], caption["number"])
            or caption["id"]
            != (f"p{page_number:04d}-{caption['kind']}-caption-{caption['number']:04d}")
        ):
            raise ValueError("caption fields are invalid")
        claimed_caption_lines.update(caption["line_ids"])
    for reference in page["references"]:
        normalized_source_labels = {
            _normalized_label(match)
            for match in _REFERENCE_TEXT.finditer(
                line_by_id.get(reference.get("line_id"), {}).get("text", "")
            )
        }
        reference_match = (
            re.fullmatch(
                r"(Figure|Table) ([0-9]+)",
                reference["label"],
                flags=re.ASCII,
            )
            if isinstance(reference.get("label"), str)
            else None
        )
        if (
            reference["line_id"] not in line_ids
            or not _valid_text(reference["label"], maximum=1024)
            or not _valid_text(reference["target_id"], maximum=128)
            or reference_match is None
            or _normalized_label(reference_match) not in normalized_source_labels
        ):
            raise ValueError("reference fields are invalid")


def _validate_extraction_artifact(
    encoded: object,
    *,
    source_sha256: str,
    normalized_pdf_sha256: str,
    preflight_sha256: str,
    normalization_sha256: str,
    expected_pages: list[object],
    limits: WorkerLimits,
) -> dict[str, Any]:
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > limits.max_extraction_artifact_bytes
    ):
        raise ValueError("extraction bytes are absent or oversized")
    artifact = _decode_json(encoded)
    if canonical_json_bytes(artifact) != encoded:
        raise ValueError("extraction is not canonical JSON")
    if set(artifact) != {
        "schema_version",
        "artifact_kind",
        "source_sha256",
        "normalized_pdf_sha256",
        "preflight_sha256",
        "normalization_sha256",
        "format_version",
        "counts",
        "pages",
    }:
        raise ValueError("extraction envelope fields differ")
    pages = artifact["pages"]
    counts = artifact["counts"]
    if (
        artifact["schema_version"] != "1.0.0"
        or artifact["artifact_kind"] != "extraction"
        or artifact["format_version"] != "1.1.0"
        or artifact["source_sha256"] != source_sha256
        or artifact["normalized_pdf_sha256"] != normalized_pdf_sha256
        or artifact["preflight_sha256"] != preflight_sha256
        or artifact["normalization_sha256"] != normalization_sha256
        or not isinstance(pages, list)
        or len(pages) != len(expected_pages)
        or not isinstance(counts, dict)
    ):
        raise ValueError("extraction identity or page count differs")
    if [page.get("page_number") for page in pages if isinstance(page, dict)] != list(
        range(1, len(pages) + 1)
    ):
        raise ValueError("extraction page numbers are not contiguous")

    collection_counts = {
        "page_count": len(pages),
        "character_count": 0,
        "vector_count": 0,
        "image_count": 0,
        "line_count": 0,
        "graphic_region_count": 0,
        "caption_count": 0,
        "reference_count": 0,
    }
    collection_names = {
        "chars": "character_count",
        "rectangles": "vector_count",
        "curves": "vector_count",
        "images": "image_count",
        "lines": "line_count",
        "graphic_regions": "graphic_region_count",
        "captions": "caption_count",
        "references": "reference_count",
    }
    identifiers: set[str] = set()
    total_expected_characters = 0
    total_observed_characters = 0
    total_representation_budget = 0
    for page_number, page in enumerate(pages, start=1):
        if not isinstance(page, dict) or page.get("page_number") != page_number:
            raise ValueError("extraction page is invalid")
        _require_fields(
            page,
            {
                "page_number",
                "media_box_mpt",
                "crop_box_mpt",
                "rotation_degrees",
                "chars",
                "rectangles",
                "curves",
                "images",
                "lines",
                "graphic_regions",
                "captions",
                "references",
            },
        )
        _validate_box(page["media_box_mpt"])
        _validate_box(page["crop_box_mpt"])
        media = page["media_box_mpt"]
        crop = page["crop_box_mpt"]
        if (
            media[0] >= media[2]
            or media[1] >= media[3]
            or crop[0] >= crop[2]
            or crop[1] >= crop[3]
            or crop[0] < media[0]
            or crop[1] < media[1]
            or crop[2] > media[2]
            or crop[3] > media[3]
        ):
            raise ValueError("page boxes are invalid")
        if page["rotation_degrees"] not in {0, 90, 180, 270}:
            raise ValueError("page rotation is invalid")
        expected_page = expected_pages[page_number - 1]
        if not isinstance(expected_page, dict):
            raise ValueError("preflight page is invalid")
        expected_media, expected_crop = _expected_child_page_boxes(expected_page)
        if (
            expected_page.get("page_number") != page_number
            or expected_page.get("rotation_degrees") != page["rotation_degrees"]
            or media != expected_media
            or crop != expected_crop
        ):
            raise ValueError("extraction page geometry differs from preflight")
        for field, count_name in collection_names.items():
            items = page.get(field)
            if not isinstance(items, list):
                raise ValueError("extraction collection is invalid")
            collection_counts[count_name] += len(items)
            if not all(isinstance(item, dict) for item in items):
                raise ValueError("extraction collection item is invalid")
        _validate_page_items(
            page,
            page_number,
            identifiers,
            crop_box=crop,
            limits=limits,
        )
        expected_characters = expected_page.get("extractable_character_count")
        if not _is_int(expected_characters) or expected_characters < 0:
            raise ValueError("preflight character count is invalid")
        observed_characters = sum(
            not character.isspace()
            for item in page["chars"]
            for character in item["text"]
        )
        representation_budget = _cid_representation_budget(page["chars"])
        minimum_characters, maximum_characters = _character_count_bounds(
            expected_characters,
            absolute_tolerance=32,
            zero_expected_ceiling=8,
            representation_budget=representation_budget,
        )
        if observed_characters < minimum_characters:
            raise ValueError("extraction lost substantial preflight text")
        if observed_characters > maximum_characters:
            raise ValueError("extraction added substantial preflight text")
        total_expected_characters += expected_characters
        total_observed_characters += observed_characters
        total_representation_budget += representation_budget
    if total_representation_budget > _MAX_CID_REPRESENTATION_BUDGET_PER_DOCUMENT:
        raise ValueError("document CID representation budget exceeds its hard limit")
    total_minimum, total_maximum = _character_count_bounds(
        total_expected_characters,
        absolute_tolerance=64,
        zero_expected_ceiling=32,
        representation_budget=total_representation_budget,
    )
    if total_observed_characters < total_minimum:
        raise ValueError("document extraction lost substantial preflight text")
    if total_observed_characters > total_maximum:
        raise ValueError("document extraction added substantial preflight text")

    global_regions = {
        region["id"]: region["kind"]
        for page in pages
        for region in page["graphic_regions"]
    }
    caption_targets: dict[tuple[str, int], set[str]] = {}
    for page in pages:
        for caption in page["captions"]:
            caption_targets.setdefault(
                (caption["kind"], caption["number"]),
                set(),
            ).add(caption["target_id"])
    for page in pages:
        for reference in page["references"]:
            match = re.fullmatch(
                r"(Figure|Table) ([0-9]+)",
                reference["label"],
                flags=re.ASCII,
            )
            expected_label = _normalized_label(match) if match is not None else None
            if (
                match is None
                or expected_label is None
                or global_regions.get(reference["target_id"]) != expected_label[0]
                or caption_targets.get(expected_label) != {reference["target_id"]}
            ):
                raise ValueError("reference target is invalid")
    from academic_pdf_en_zh_reader.extraction.revalidate import (
        validate_derived_semantics,
    )

    validate_derived_semantics(pages)
    if set(counts) != set(collection_counts) or any(
        not _is_int(counts[name]) or counts[name] != observed
        for name, observed in collection_counts.items()
    ):
        raise ValueError("extraction counts differ")
    _validate_mpt_fields(artifact)
    return artifact


def _persist_exclusive(path: Path, encoded: bytes) -> str:
    try:
        return write_immutable_bytes(path, encoded)
    except (ArtifactExistsError, ConcurrentStateError) as error:
        raise _StableBridgeError("ARTIFACT_EXISTS") from error


def _cleanup_safe_copy(safe_copy: SafeInputCopy | None) -> bool:
    if safe_copy is None:
        return True
    try:
        safe_copy.cleanup()
    except OSError:
        return False
    return not safe_copy.root.exists()


def extract_untrusted_pdf(
    source: str | Path,
    job_root: str | Path,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> dict[str, object]:
    """Extract one normalized PDF; persist only a bound validated result."""

    try:
        preflight, preflight_bytes, preflight_sha = _read_valid_preflight(
            Path(job_root),
            limits=limits,
        )
    except Exception:
        return _error("PREFLIGHT_ARTIFACT_INVALID")
    try:
        normalization, normalization_bytes, normalization_sha = (
            _read_valid_normalization(
                Path(job_root),
                preflight=preflight,
                preflight_sha256=preflight_sha,
                limits=limits,
            )
        )
        expected_pages = _expected_normalized_pages(preflight, normalization)
    except Exception:
        return _error("NORMALIZATION_ARTIFACT_INVALID")

    safe_copy: SafeInputCopy | None = None
    artifact: dict[str, Any] | None = None
    encoded_artifact: bytes | None = None
    failure_code: str | None = None
    try:
        normalized_input_limits = replace(
            limits,
            max_input_bytes=limits.max_normalized_pdf_bytes,
        )
        safe_copy = copy_untrusted_input(Path(source), limits=normalized_input_limits)
        if (
            safe_copy.size <= 0
            or normalization["normalized_pdf_sha256"] != safe_copy.sha256
            or normalization["normalized_pdf_bytes"] != safe_copy.size
        ):
            raise _StableBridgeError("NORMALIZATION_ARTIFACT_INVALID")
        _write_handoff(safe_copy.root, _PREFLIGHT_NAME, preflight_bytes)
        _write_handoff(
            safe_copy.root,
            _NORMALIZATION_NAME,
            normalization_bytes,
        )
        request = WorkerRequest(
            operation="extract",
            input_path="input.pdf",
            parameters={
                "policy_version": EXTRACTION_POLICY_VERSION,
                "source_sha256": preflight["source_sha256"],
                "normalized_pdf_sha256": safe_copy.sha256,
                "input_bytes": safe_copy.size,
                "preflight_sha256": preflight_sha,
                "normalization_sha256": normalization_sha,
            },
        )
        result = _run_platform_worker(request, safe_copy.root, limits=limits)
        if (
            not isinstance(getattr(result, "provenance", None), dict)
            or result.provenance.get("appcontainer_cleanup_verified") is not True
        ):
            raise _StableBridgeError("SANDBOX_CONTRACT_UNVERIFIED")
        encoded_artifact = getattr(result, "artifact_bytes", None)
        artifact = _validate_extraction_artifact(
            encoded_artifact,
            source_sha256=preflight["source_sha256"],
            normalized_pdf_sha256=safe_copy.sha256,
            preflight_sha256=preflight_sha,
            normalization_sha256=normalization_sha,
            expected_pages=expected_pages,
            limits=limits,
        )
    except _StableBridgeError as error:
        failure_code = error.code
    except (OSError, UnsafeInputError):
        failure_code = "INPUT_REJECTED" if safe_copy is None else "WORKER_FAILED"
    except Exception:
        failure_code = "EXTRACTION_ARTIFACT_INVALID"
    except BaseException:
        _cleanup_safe_copy(safe_copy)
        raise

    if not _cleanup_safe_copy(safe_copy):
        artifact = None
        encoded_artifact = None
        failure_code = "SANDBOX_CONTRACT_UNVERIFIED"
    if failure_code is not None:
        return _error(failure_code)
    if artifact is None or encoded_artifact is None:
        return _error("WORKER_FAILED")
    try:
        digest = _persist_exclusive(
            Path(job_root) / _ARTIFACT_NAME,
            encoded_artifact,
        )
    except _StableBridgeError as error:
        return _error(error.code)
    except (OSError, UnsafeInputError, ValueError):
        return _error("EXTRACTION_ARTIFACT_INVALID")
    return {
        "status": "ok",
        "artifact": {"name": _ARTIFACT_NAME, "sha256": digest},
        "summary": dict(artifact["counts"]),
    }
