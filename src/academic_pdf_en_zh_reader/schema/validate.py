# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Offline-only validation for the packaged Draft 2020-12 schemas."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import NoSuchResource

SCHEMA_NAMES = frozenset(
    {
        "source",
        "normalization",
        "units",
        "translation",
        "review",
        "semantic-candidates",
        "annotations",
        "frame-graph",
        "layout",
        "render-manifest",
        "qa",
        "provenance",
        "correction-suggestions",
        "preflight",
        "job-state",
        "finalization-receipt",
    }
)
_SCHEMA_DIRECTORY = Path(__file__).parent
_A4_WIDTH_MPT = 595_276
_A4_HEIGHT_MPT = 841_890
_SCALE_DENOMINATOR = 1_000_000
_CONTINUATION_TEXT = "译文续页"
_CONTINUATION_GAP_AFTER_MPT = 3_000
_WINDOWS_DEVICE_NAMES = {
    "AUX",
    "CLOCK$",
    "CON",
    "CONIN$",
    "CONOUT$",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in "¹²³"),
    *(f"LPT{number}" for number in "¹²³"),
}


class SchemaValidationError(ValueError):
    """Raised when a packaged schema or artifact violates its contract."""


def _iter_references(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                yield child
            yield from _iter_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_references(child)


def _offline_retrieve(uri: str):
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=len(SCHEMA_NAMES))
def _load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise SchemaValidationError(f"unknown artifact schema: {name!r}")
    path = _SCHEMA_DIRECTORY / f"{name}.schema.json"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaValidationError(f"cannot load schema {name!r}") from exc

    version = "2.0.0" if name in {"frame-graph", "layout"} else "1.0.0"
    expected_id = f"urn:academic-pdf-en-zh-reader:schema:{name}:{version}"
    if schema.get("$id") != expected_id:
        raise SchemaValidationError(f"schema {name!r} has an unexpected $id")
    external_refs = [
        ref
        for ref in _iter_references(schema)
        if not isinstance(ref, str) or not ref.startswith("#")
    ]
    if external_refs:
        raise SchemaValidationError(
            f"schema {name!r} contains forbidden external references"
        )
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # jsonschema exposes several schema error subclasses
        raise SchemaValidationError(f"schema {name!r} is invalid") from exc
    return schema


def load_schema(name: str) -> dict[str, Any]:
    """Return an isolated copy of one self-checked packaged schema."""

    return deepcopy(_load_schema(name))


@lru_cache(maxsize=len(SCHEMA_NAMES))
def _validator(name: str) -> Draft202012Validator:
    registry: Registry[Any] = Registry(retrieve=_offline_retrieve)
    return Draft202012Validator(_load_schema(name), registry=registry)


def validate_artifact(name: str, instance: object) -> None:
    """Validate an artifact against exactly one packaged contract."""

    errors = sorted(
        _validator(name).iter_errors(instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise SchemaValidationError(
            f"{name} artifact is invalid at {location}: {first.message}"
        )
    _validate_semantics(name, instance)


def _strictly_increasing(items: list[dict[str, Any]], key: str, *, label: str) -> None:
    values = [item[key] for item in items]
    if any(left >= right for left, right in zip(values, values[1:], strict=False)):
        raise SchemaValidationError(f"{label} must be strictly increasing by {key}")


def _positive_box(box: list[int], *, label: str) -> None:
    if box[2] <= box[0] or box[3] <= box[1]:
        raise SchemaValidationError(f"{label} must have positive width and height")


def _validate_normalization(instance: dict[str, Any]) -> None:
    pages = instance["pages"]
    _strictly_increasing(pages, "page_number", label="normalization pages")
    for page in pages:
        media = page["source_media_box_mpt"]
        crop = page["source_crop_box_mpt"]
        _positive_box(media, label="normalization source media box")
        _positive_box(crop, label="normalization source crop box")
        if not (
            media[0] <= crop[0] < crop[2] <= media[2]
            and media[1] <= crop[1] < crop[3] <= media[3]
        ):
            raise SchemaValidationError(
                "normalization source crop box must be contained in media box"
            )

        raw_width = crop[2] - crop[0]
        raw_height = crop[3] - crop[1]
        if page["source_rotation_degrees"] in {90, 270}:
            displayed_width, displayed_height = raw_height, raw_width
        else:
            displayed_width, displayed_height = raw_width, raw_height
        if (
            page["displayed_width_mpt"] != displayed_width
            or page["displayed_height_mpt"] != displayed_height
        ):
            raise SchemaValidationError(
                "normalization displayed dimensions disagree with source geometry"
            )

        scale_ppm = min(
            _SCALE_DENOMINATOR,
            _A4_WIDTH_MPT * _SCALE_DENOMINATOR // displayed_width,
            _A4_HEIGHT_MPT * _SCALE_DENOMINATOR // displayed_height,
        )
        if scale_ppm < 1 or page["scale_ppm"] != scale_ppm:
            raise SchemaValidationError(
                "normalization scale must be the deterministic no-upscale fit"
            )
        scaled_width = (
            displayed_width * scale_ppm + _SCALE_DENOMINATOR // 2
        ) // _SCALE_DENOMINATOR
        scaled_height = (
            displayed_height * scale_ppm + _SCALE_DENOMINATOR // 2
        ) // _SCALE_DENOMINATOR
        if (
            scaled_width < 1
            or scaled_height < 1
            or scaled_width > _A4_WIDTH_MPT
            or scaled_height > _A4_HEIGHT_MPT
            or page["scaled_width_mpt"] != scaled_width
            or page["scaled_height_mpt"] != scaled_height
        ):
            raise SchemaValidationError(
                "normalization scaled dimensions disagree with the fitted source"
            )

        horizontal_gap = _A4_WIDTH_MPT - scaled_width
        vertical_gap = _A4_HEIGHT_MPT - scaled_height
        left = horizontal_gap // 2
        bottom = vertical_gap // 2
        right = horizontal_gap - left
        top = vertical_gap - bottom
        expected_padding = (left, bottom, right, top)
        actual_padding = (
            page["padding_left_mpt"],
            page["padding_bottom_mpt"],
            page["padding_right_mpt"],
            page["padding_top_mpt"],
        )
        expected_content_box = [
            left,
            bottom,
            left + scaled_width,
            bottom + scaled_height,
        ]
        if (
            actual_padding != expected_padding
            or page["normalized_content_box_mpt"] != expected_content_box
        ):
            raise SchemaValidationError(
                "normalization padding or content box is not deterministically centered"
            )


def _check_gate_summary(instance: dict[str, Any], *, label: str) -> None:
    hard_gates = [check for check in instance["checks"] if check["hard_gate"]]
    if not hard_gates:
        raise SchemaValidationError(f"{label} requires at least one hard gate")
    failed_hard_gate = any(not check["passed"] for check in hard_gates)
    if instance["passed"] and failed_hard_gate:
        raise SchemaValidationError(
            f"{label} passed summary disagrees with its hard gate checks"
        )
    if not instance["passed"] and not failed_hard_gate:
        raise SchemaValidationError(
            f"{label} failure requires failed hard gate evidence"
        )


def _validate_font_path(path: str) -> None:
    drive_path = len(path) >= 2 and path[0].isalpha() and path[1] == ":"
    segments = path.split("/")
    reserved_device = any(
        segment.rstrip(" .").split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES
        for segment in segments
    )
    if (
        not path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or "\\" in path
        or ":" in path
        or drive_path
        or PurePosixPath(path).is_absolute()
        or path.startswith("//")
        or any(segment in {"", ".", ".."} for segment in segments)
        or any(segment.endswith((" ", ".")) for segment in segments)
        or reserved_device
        or PurePosixPath(path).as_posix() != path
    ):
        raise SchemaValidationError(
            "render-manifest font path must be a canonical POSIX relative path"
        )


def _validate_render_manifest(instance: dict[str, Any]) -> None:
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
    from academic_pdf_en_zh_reader.rendering.metadata import (
        render_input_hash_payload,
    )

    fingerprint = instance["font_fingerprint"]
    font_files = instance["font_files"]
    expected_roles = ["body", "heading", "symbols"]
    if [item["role"] for item in fingerprint] != expected_roles or [
        item["role"] for item in font_files
    ] != expected_roles:
        raise SchemaValidationError(
            "render-manifest font roles must use the complete stable order"
        )
    for fingerprint_item, file_item in zip(fingerprint, font_files, strict=True):
        _validate_font_path(file_item["path"])
        if (
            fingerprint_item["role"] != file_item["role"]
            or fingerprint_item["reportlab_name"] != file_item["reportlab_name"]
            or fingerprint_item["sha256"] != file_item["sha256"]
        ):
            raise SchemaValidationError(
                "render-manifest font files differ from the fingerprint"
            )
    font_names = {item["role"]: item["reportlab_name"] for item in fingerprint}
    usage_keys = [
        (item["font_role"], item["font_name"], item["size_mpt"])
        for item in instance["font_usages"]
    ]
    if usage_keys != sorted(set(usage_keys)) or any(
        font_names.get(role) != name for role, name, _size in usage_keys
    ):
        raise SchemaValidationError(
            "render-manifest font usages must be unique, sorted, and pinned"
        )
    pages = instance["pages"]
    if [page["output_page_number"] for page in pages] != list(range(1, len(pages) + 1)):
        raise SchemaValidationError("render-manifest output pages must be contiguous")
    from academic_pdf_en_zh_reader.qa.page_contract import source_manifest_pages

    try:
        source_manifest_pages(pages)
    except ValueError as exc:
        raise SchemaValidationError(str(exc)) from exc
    for page in pages:
        if page["source_page_number"] is None:
            continue
        _positive_box(page["source_crop_box_mpt"], label="source crop box")
        _positive_box(
            page["source_normalized_visible_box_mpt"],
            label="normalized source crop box",
        )
        normalized = page["source_normalized_visible_box_mpt"]
        if page["source_transform_mpt"] != [
            1000,
            0,
            0,
            1000,
            -normalized[0],
            -normalized[1],
        ]:
            raise SchemaValidationError(
                "render-manifest source transform must be exact 1:1 placement"
            )
    output_pages = {page["output_page_number"] for page in pages}
    block_ids: set[str] = set()
    for block in instance["block_mappings"]:
        _positive_box(block["bbox_mpt"], label="rendered block")
        if (
            block["block_id"] in block_ids
            or block["output_page_number"] not in output_pages
        ):
            raise SchemaValidationError(
                "render-manifest block mappings are duplicated or unbound"
            )
        block_ids.add(block["block_id"])
    if instance["render_style_hash"] != sha256_canonical(instance["render_style"]):
        raise SchemaValidationError(
            "render-manifest render_style_hash does not recompute"
        )
    if instance["overlay_plan_limits_hash"] != sha256_canonical(
        instance["overlay_plan_limits"]
    ):
        raise SchemaValidationError(
            "render-manifest overlay_plan_limits_hash does not recompute"
        )
    if instance["render_input_hash"] != sha256_canonical(
        render_input_hash_payload(instance)
    ):
        raise SchemaValidationError(
            "render-manifest render_input_hash does not recompute"
        )


def _validate_semantics(name: str, instance: object) -> None:
    if not isinstance(instance, dict):
        return
    if name == "normalization":
        _validate_normalization(instance)
    elif name == "source":
        _strictly_increasing(instance["pages"], "page_number", label="source pages")
        from academic_pdf_en_zh_reader.topology.contracts import (
            TopologyContractError,
            validate_source_topology,
        )

        try:
            validate_source_topology(instance)
        except TopologyContractError as exc:
            raise SchemaValidationError(str(exc)) from exc
        for page in instance["pages"]:
            _positive_box(page["media_box_mpt"], label="media_box_mpt")
            _positive_box(page["crop_box_mpt"], label="crop_box_mpt")
            _strictly_increasing(page["blocks"], "reading_order", label="source blocks")
            for block in page["blocks"]:
                if block["source_char_end"] <= block["source_char_start"]:
                    raise SchemaValidationError(
                        "source block character range is invalid"
                    )
                _positive_box(block["bbox_mpt"], label="block bbox_mpt")
                _positive_box(block["first_line_bbox_mpt"], label="first_line_bbox_mpt")
                from academic_pdf_en_zh_reader.job.hashing import stable_source_id

                expected_id = stable_source_id(
                    page_number=page["page_number"],
                    reading_order=block["reading_order"],
                    role=block["role"],
                    source_char_start=block["source_char_start"],
                    source_char_end=block["source_char_end"],
                )
                if block["id"] != expected_id:
                    raise SchemaValidationError(
                        "source block id does not match its stable source id"
                    )
    elif name == "units":
        _strictly_increasing(instance["units"], "reading_order", label="units")
        for unit in instance["units"]:
            for fragment in unit["fragments"]:
                if fragment["source_char_end"] <= fragment["source_char_start"]:
                    raise SchemaValidationError(
                        "unit fragment character range is invalid"
                    )
    elif name == "translation":
        for unit in instance["units"]:
            for span in unit["spans"]:
                if (
                    span["source_end"] < span["source_start"]
                    or span["target_end"] < span["target_start"]
                ):
                    raise SchemaValidationError("translation span range is invalid")
    elif name == "review":
        unresolved_hard_error = any(
            issue["severity"] == "hard_error" and issue["status"] == "unresolved"
            for issue in instance["issues"]
        )
        if instance["final_status"] == "passed" and unresolved_hard_error:
            raise SchemaValidationError(
                "passed review cannot contain an unresolved hard_error"
            )
    elif name in {"preflight", "qa"}:
        _check_gate_summary(instance, label=name)
        if name == "qa":
            expected_gates = (
                ("parent.chain", "security"),
                ("semantic.translation-coverage", "semantic"),
                ("semantic.review", "semantic"),
                ("content.annotation-policy", "content"),
                ("geometry.a3-pages", "geometry"),
                ("geometry.source-left-one-to-one", "geometry"),
                ("geometry.reading-frames", "geometry"),
                ("geometry.bounds-and-overlap", "geometry"),
                ("geometry.continuation-fixed-size", "geometry"),
                ("font.embedded-tounicode", "font"),
                ("font.draw-run-binding", "font"),
                ("font.glyph-coverage", "font"),
                ("security.active-content-absent", "security"),
                ("security.no-raster-substitution", "security"),
                ("render.pdfium-all-pages-144dpi", "render"),
                ("render.left-visual-equivalence", "render"),
                ("render.full-page-sanity", "render"),
            )
            actual_gates = tuple(
                (check["id"], check["category"]) for check in instance["checks"]
            )
            if actual_gates != expected_gates or any(
                check["hard_gate"] is not True for check in instance["checks"]
            ):
                raise SchemaValidationError(
                    "qa checks must contain the complete fixed hard gate order"
                )
            if instance["rasterized_page_count"] > instance["checked_page_count"]:
                raise SchemaValidationError(
                    "qa rasterized page count exceeds checked page count"
                )
        if name == "preflight":
            if instance["passed"] and not instance["pages"]:
                raise SchemaValidationError(
                    "passed preflight requires at least one page"
                )
            if not instance["passed"]:
                error_codes = set(instance.get("error_codes", []))
                failed_hard_gates = [
                    check
                    for check in instance["checks"]
                    if check["hard_gate"] and not check["passed"]
                ]
                if not error_codes or any(
                    not check.get("error_code")
                    or check["error_code"] not in error_codes
                    for check in failed_hard_gates
                ):
                    raise SchemaValidationError(
                        "failed preflight requires matching hard-gate error evidence"
                    )
            exceeded_limits = [
                limit_name
                for limit_name, values in instance.get("limits", {}).items()
                if values["observed"] > values["maximum"]
            ]
            if instance["passed"] and exceeded_limits:
                raise SchemaValidationError(
                    "passed preflight cannot exceed a declared resource limit"
                )
            _strictly_increasing(
                instance["pages"], "page_number", label="preflight pages"
            )
            for page in instance["pages"]:
                for field in ("media_box_mpt", "crop_box_mpt"):
                    if field in page:
                        _positive_box(page[field], label=field)
    elif name in {"frame-graph", "layout"}:
        from academic_pdf_en_zh_reader.layout.validation import (
            validate_reading_artifact,
        )

        validate_reading_artifact(name, instance)
    elif name == "render-manifest":
        _validate_render_manifest(instance)
    elif name == "job-state":
        from academic_pdf_en_zh_reader.job.state import JobState, JobStateError

        try:
            JobState.from_dict(instance)
        except (JobStateError, KeyError, TypeError, ValueError) as exc:
            raise SchemaValidationError(f"job-state history is invalid: {exc}") from exc
    elif name == "finalization-receipt":
        from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

        payload = {
            key: value for key, value in instance.items() if key != "receipt_hash"
        }
        if instance["receipt_hash"] != sha256_canonical(payload):
            raise SchemaValidationError("finalization receipt_hash does not recompute")
