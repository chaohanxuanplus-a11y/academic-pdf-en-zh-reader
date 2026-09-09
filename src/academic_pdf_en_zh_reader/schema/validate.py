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

    expected_id = f"urn:academic-pdf-en-zh-reader:schema:{name}:1.0.0"
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
    for page_index, page in enumerate(pages):
        if page["page_kind"] == "disclaimer":
            if page_index != len(pages) - 1 or len(pages) < 2:
                raise SchemaValidationError(
                    "render-manifest disclaimer must be the sole final synthetic page"
                )
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
        continuation = page["page_kind"] == "continuation"
        if (
            continuation != page["continuation_label_present"]
            or (continuation and page["continuation_index"] < 1)
            or (not continuation and page["continuation_index"] != 0)
        ):
            raise SchemaValidationError(
                "render-manifest continuation page evidence is inconsistent"
            )
    output_pages = {
        page["output_page_number"]
        for page in pages
        if page["page_kind"] != "disclaimer"
    }
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


def _validate_frame_lines(
    lines: list[dict[str, Any]],
    style: dict[str, Any],
    font_names: dict[str, str],
    *,
    label: str,
    line_mapping_version: int = 1,
    initial_target_start: int = 0,
) -> None:
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

    if not lines:
        raise SchemaValidationError(f"{label} must contain lines")
    previous_end = initial_target_start
    previous_composite_end = 0
    for index, line in enumerate(lines):
        if any(character in line["text"] for character in "\r\n\u2028\u2029"):
            raise SchemaValidationError(f"{label} contains a hard line break")
        if line["index"] != index or line["target_start"] != previous_end:
            raise SchemaValidationError(
                f"{label} line indexes and target ranges must be contiguous"
            )
        if line_mapping_version == 1:
            if line["target_end"] <= line["target_start"]:
                raise SchemaValidationError(f"{label} target line range is invalid")
            line_contract_version = "1.0.0"
        else:
            if set(
                ("composite_start", "composite_end", "synthetic_annotation_ids")
            ) - set(line):
                raise SchemaValidationError(
                    f"{label} composite line fields are missing"
                )
            if (
                line["composite_start"] != previous_composite_end
                or line["composite_end"] <= line["composite_start"]
                or line["target_end"] < line["target_start"]
                or (
                    line["target_end"] == line["target_start"]
                    and not line["synthetic_annotation_ids"]
                )
            ):
                raise SchemaValidationError(f"{label} composite line range is invalid")
            line_contract_version = "2.0.0"
        if line["style_id"] != style["style_id"]:
            raise SchemaValidationError(f"{label} line style binding is invalid")
        if line["line_height_mpt"] < style["line_height_mpt"]:
            raise SchemaValidationError(f"{label} line height shrinks its fixed style")
        if "".join(run["text"] for run in line["runs"]) != line["text"]:
            raise SchemaValidationError(f"{label} font runs do not cover line text")
        for run in line["runs"]:
            if font_names.get(run["font_role"]) != run["font_name"]:
                raise SchemaValidationError(
                    f"{label} font run is outside the registered fingerprint"
                )
        payload = {key: value for key, value in line.items() if key != "line_box_hash"}
        expected_hash = sha256_canonical(
            {"line_box_contract_version": line_contract_version, **payload}
        )
        if line["line_box_hash"] != expected_hash:
            raise SchemaValidationError(f"{label} line_box_hash is invalid")
        previous_end = line["target_end"]
        if line_mapping_version == 2:
            previous_composite_end = line["composite_end"]


def _validate_composite_flow(
    flow: dict[str, Any],
    *,
    label: str,
    allow_synthetic: bool,
) -> None:
    """Verify both coordinate axes and every exact synthetic-line rebinding."""

    segments = flow["composite_segments"]
    if not segments:
        raise SchemaValidationError(f"{label} requires composite segments")
    composite_cursor = 0
    target_cursor = 0
    composite_parts: list[str] = []
    annotation_ids: set[str] = set()
    for index, segment in enumerate(segments):
        if (
            segment["index"] != index
            or segment["composite_start"] != composite_cursor
            or segment["composite_end"] <= segment["composite_start"]
            or len(segment["text"])
            != segment["composite_end"] - segment["composite_start"]
        ):
            raise SchemaValidationError(f"{label} composite segments are invalid")
        if segment["kind"] == "target":
            if (
                segment["target_start"] != target_cursor
                or segment["target_end"] <= segment["target_start"]
                or len(segment["text"])
                != segment["target_end"] - segment["target_start"]
            ):
                raise SchemaValidationError(f"{label} target segment is invalid")
            target_cursor = segment["target_end"]
        elif segment["kind"] == "ambiguity-label":
            if (
                not allow_synthetic
                or segment["target_offset"] != target_cursor
                or segment["annotation_id"] in annotation_ids
            ):
                raise SchemaValidationError(f"{label} synthetic segment is invalid")
            annotation_ids.add(segment["annotation_id"])
        else:
            raise SchemaValidationError(f"{label} segment kind is invalid")
        composite_cursor = segment["composite_end"]
        composite_parts.append(segment["text"])
    if (
        target_cursor != flow["base_target_length"]
        or composite_cursor != flow["composite_length"]
    ):
        raise SchemaValidationError(f"{label} coordinate lengths are invalid")

    composite_text = "".join(composite_parts)
    target_text = "".join(
        segment["text"] for segment in segments if segment["kind"] == "target"
    )
    from academic_pdf_en_zh_reader.layout.unit_parts import grapheme_boundaries

    safe_offsets = grapheme_boundaries(target_text)
    if any(
        (
            segment["target_start"] not in safe_offsets
            or segment["target_end"] not in safe_offsets
        )
        if segment["kind"] == "target"
        else segment["target_offset"] not in safe_offsets
        for segment in segments
    ):
        raise SchemaValidationError(f"{label} segment splits a grapheme cluster")
    safe_composite_offsets = grapheme_boundaries(composite_text)
    styled = flow["styled_spans"]
    styled_ids: set[str] = set()
    style_order = {"dark-red-highlight": 0, "bright-red-ambiguity": 1}
    if styled != sorted(
        styled,
        key=lambda item: (
            item["target_start"],
            style_order[item["kind"]],
            item["annotation_id"],
        ),
    ):
        raise SchemaValidationError(f"{label} styled spans are out of order")
    for span in styled:
        if (
            span["annotation_id"] in styled_ids
            or span["target_start"] not in safe_offsets
            or span["target_end"] not in safe_offsets
            or span["target_end"] <= span["target_start"]
            or (
                span["kind"] == "dark-red-highlight"
                and (span["color_token"] != "dark_red" or span["underline"])
            )
            or (
                span["kind"] == "bright-red-ambiguity"
                and (span["color_token"] != "bright_red" or not span["underline"])
            )
        ):
            raise SchemaValidationError(f"{label} styled span is invalid")
        styled_ids.add(span["annotation_id"])
    bright_ids = {
        span["annotation_id"]
        for span in styled
        if span["kind"] == "bright-red-ambiguity"
    }
    if not annotation_ids <= bright_ids:
        raise SchemaValidationError(f"{label} label has no ambiguity styled span")
    lines = flow["lines"]
    if (
        lines[0]["composite_start"] != 0
        or lines[0]["target_start"] != 0
        or lines[-1]["composite_end"] != flow["composite_length"]
        or lines[-1]["target_end"] != flow["base_target_length"]
    ):
        raise SchemaValidationError(
            f"{label} lines do not cover the complete coordinate axes"
        )
    for line in lines:
        composite_start = line["composite_start"]
        composite_end = line["composite_end"]
        if (
            composite_start not in safe_composite_offsets
            or composite_end not in safe_composite_offsets
        ):
            raise SchemaValidationError(f"{label} line splits a grapheme cluster")
        if composite_text[composite_start:composite_end].strip() != line["text"]:
            raise SchemaValidationError(f"{label} line text is not composite-bound")
        ranges: list[tuple[int, int]] = []
        synthetic_ids: list[str] = []
        anchors: set[int] = set()
        for segment in segments:
            overlap_start = max(composite_start, segment["composite_start"])
            overlap_end = min(composite_end, segment["composite_end"])
            if overlap_start >= overlap_end:
                continue
            if segment["kind"] == "target":
                start = segment["target_start"] + (
                    overlap_start - segment["composite_start"]
                )
                ranges.append((start, start + overlap_end - overlap_start))
            else:
                synthetic_ids.append(segment["annotation_id"])
                anchors.add(segment["target_offset"])
        if ranges:
            expected_start, expected_end = ranges[0][0], ranges[-1][1]
        elif len(anchors) == 1:
            expected_start = expected_end = next(iter(anchors))
        else:
            raise SchemaValidationError(f"{label} line has no exact target binding")
        if (
            line["target_start"] != expected_start
            or line["target_end"] != expected_end
            or line["synthetic_annotation_ids"] != list(dict.fromkeys(synthetic_ids))
        ):
            raise SchemaValidationError(f"{label} line mapping is invalid")


def _validate_auxiliary_flow_mapping(flow: dict[str, Any], *, label: str) -> None:
    segments = flow["composite_segments"]
    if len(segments) != 1:
        raise SchemaValidationError(f"{label} requires one frozen content segment")
    segment = segments[0]
    target_offset = flow["attachment_target_end"]
    if (
        flow["attachment_target_start"] < 0
        or target_offset <= flow["attachment_target_start"]
        or target_offset > flow["parent_target_length"]
        or segment["kind"] != "auxiliary-content"
        or segment["target_offset"] != target_offset
        or segment["composite_start"] != 0
        or segment["composite_end"] != flow["composite_length"]
        or len(segment["text"]) != flow["composite_length"]
    ):
        raise SchemaValidationError(f"{label} parent or content binding is invalid")
    from academic_pdf_en_zh_reader.layout.unit_parts import grapheme_boundaries

    safe_composite_offsets = grapheme_boundaries(segment["text"])
    previous_composite_end = 0
    for line in flow["lines"]:
        if (
            line["composite_start"] not in safe_composite_offsets
            or line["composite_end"] not in safe_composite_offsets
        ):
            raise SchemaValidationError(f"{label} line splits a grapheme cluster")
        if (
            line["composite_start"] != previous_composite_end
            or segment["text"][line["composite_start"] : line["composite_end"]].strip()
            != line["text"]
            or line["target_start"] != target_offset
            or line["target_end"] != target_offset
            or line["synthetic_annotation_ids"] != [flow["annotation_id"]]
        ):
            raise SchemaValidationError(f"{label} line mapping is invalid")
        previous_composite_end = line["composite_end"]
    if previous_composite_end != flow["composite_length"]:
        raise SchemaValidationError(f"{label} lines do not cover frozen content")


def _validate_continuation_text_contract(
    value: dict[str, Any],
    *,
    font_names: dict[str, str],
    label: str,
) -> None:
    style = value["style"]
    expected_style_id = (
        f"typography-v1:auxiliary:{style['font_role']}:"
        f"{style['size_mpt']}:{style['line_height_mpt']}"
    )
    if (
        value["contract_version"] != "1.0.0"
        or value["text"] != _CONTINUATION_TEXT
        or style["semantic_role"] != "auxiliary"
        or style["style_id"] != expected_style_id
        or value["line_height_mpt"] != style["line_height_mpt"]
        or value["ascent_mpt"] - value["descent_mpt"] > value["line_height_mpt"]
        or value["color_token"] != "muted_gray"
        or value["color_hex"] != "#666666"
    ):
        raise SchemaValidationError(f"{label} style or metrics are invalid")
    offset = 0
    text_parts: list[str] = []
    for index, run in enumerate(value["runs"]):
        if (
            run["run_index"] != index
            or run["x_offset_mpt"] != offset
            or font_names.get(run["font_role"]) != run["font_name"]
            or run["font_role"] not in {style["font_role"], "symbols"}
        ):
            raise SchemaValidationError(f"{label} run binding is invalid")
        offset += run["width_mpt"]
        text_parts.append(run["text"])
    if offset != value["width_mpt"] or "".join(text_parts) != value["text"]:
        raise SchemaValidationError(f"{label} runs do not reproduce its line")


def _validate_continuation_header(
    instance: dict[str, Any],
    *,
    font_names: dict[str, str],
) -> None:
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

    header = instance["continuation_header"]
    _validate_continuation_text_contract(
        header,
        font_names=font_names,
        label="continuation header",
    )
    usable_width = (
        instance["right_panel_bbox_mpt"][2]
        - instance["right_panel_bbox_mpt"][0]
        - 2 * instance["flow_spacing"]["horizontal_padding_mpt"]
    )
    if (
        header["top_inset_mpt"] != instance["flow_spacing"]["vertical_padding_mpt"]
        or header["gap_after_mpt"] != _CONTINUATION_GAP_AFTER_MPT
        or header["reserve_height_mpt"]
        != header["top_inset_mpt"] + header["line_height_mpt"] + header["gap_after_mpt"]
        or header["width_mpt"] > usable_width
        or header["horizontal_alignment"] != "right"
        or header["header_hash"]
        != sha256_canonical(
            {key: value for key, value in header.items() if key != "header_hash"}
        )
    ):
        raise SchemaValidationError("continuation header is inconsistent")


def _validate_continuation_label(
    page: dict[str, Any],
    *,
    right_panel: list[int],
    flow_spacing: dict[str, Any],
    font_names: dict[str, str],
) -> int:
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

    label = page["continuation_label"]
    _validate_continuation_text_contract(
        label,
        font_names=font_names,
        label="continuation label",
    )
    x_mpt = right_panel[2] - flow_spacing["horizontal_padding_mpt"] - label["width_mpt"]
    baseline_y_mpt = (
        page["page_height_mpt"]
        - flow_spacing["vertical_padding_mpt"]
        - label["ascent_mpt"]
    )
    expected_bbox = [
        x_mpt,
        baseline_y_mpt + label["descent_mpt"],
        x_mpt + label["width_mpt"],
        baseline_y_mpt + label["ascent_mpt"],
    ]
    if (
        label["source_page_number"] != page["source_page_number"]
        or label["continuation_index"] != page["continuation_index"]
        or label["x_mpt"] != x_mpt
        or label["baseline_y_mpt"] != baseline_y_mpt
        or label["bbox_mpt"] != expected_bbox
        or not (
            right_panel[0] <= expected_bbox[0] < expected_bbox[2] <= right_panel[2]
            and right_panel[1] <= expected_bbox[1] < expected_bbox[3] <= right_panel[3]
        )
        or label["label_hash"]
        != sha256_canonical(
            {key: value for key, value in label.items() if key != "label_hash"}
        )
    ):
        raise SchemaValidationError("continuation label is inconsistent")
    return (
        flow_spacing["vertical_padding_mpt"]
        + label["line_height_mpt"]
        + _CONTINUATION_GAP_AFTER_MPT
    )


def _validate_frame_graph(instance: dict[str, Any]) -> None:
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

    line_mapping_version = instance.get("line_mapping_version", 1)
    annotated = line_mapping_version == 2
    if annotated != (
        "annotation_binding" in instance and "auxiliary_flows" in instance
    ):
        raise SchemaValidationError(
            "annotated frame-graph root fields must use line mapping v2"
        )
    if not annotated and any(
        key in instance
        for key in ("annotation_binding", "auxiliary_flows", "line_mapping_version")
    ):
        raise SchemaValidationError("legacy frame graph cannot carry v2 fields")
    if annotated and instance["figure_note_flows"]:
        raise SchemaValidationError("annotated frame graph cannot carry legacy notes")
    _positive_box(instance["right_panel_bbox_mpt"], label="right panel bbox_mpt")
    right_panel = instance["right_panel_bbox_mpt"]
    roles = [face["role"] for face in instance["font_fingerprint"]]
    if roles != ["body", "heading", "symbols"]:
        raise SchemaValidationError(
            "frame-graph font fingerprint must use the complete stable role order"
        )
    font_names = {
        face["role"]: face["reportlab_name"] for face in instance["font_fingerprint"]
    }
    _validate_continuation_header(instance, font_names=font_names)

    frame_by_id: dict[str, dict[str, Any]] = {}
    band_ids: set[str] = set()
    native_order: list[str] = []
    for page in instance["pages"]:
        if page["page_height_mpt"] != right_panel[3] - right_panel[1]:
            raise SchemaValidationError("frame page height disagrees with right panel")
        page_frames: dict[str, dict[str, Any]] = {}
        for frame in page["frames"]:
            identifier = frame["id"]
            if identifier in frame_by_id:
                raise SchemaValidationError("frame identifiers must be unique")
            _positive_box(frame["bbox_mpt"], label="frame bbox_mpt")
            box = frame["bbox_mpt"]
            if not (
                right_panel[0] <= box[0] < box[2] <= right_panel[2]
                and right_panel[1] <= box[1] < box[3] <= right_panel[3]
            ):
                raise SchemaValidationError("frame must stay inside the right panel")
            if not box[0] <= frame["text_left_mpt"] < frame["text_right_mpt"] <= box[2]:
                raise SchemaValidationError("frame text bounds are outside its bbox")
            if frame["source_page_number"] != page["page_number"]:
                raise SchemaValidationError("frame source page disagrees with its page")
            if frame["column_index"] >= frame["column_count"]:
                raise SchemaValidationError("frame column index is outside its band")
            expected_activation = "always" if frame["kind"] == "native" else "candidate"
            if frame["activation"] != expected_activation:
                raise SchemaValidationError("frame kind and activation disagree")
            frame_by_id[identifier] = frame
            page_frames[identifier] = frame

        for expected_band_index, band in enumerate(page["bands"]):
            if band["id"] in band_ids or band["band_index"] != expected_band_index:
                raise SchemaValidationError(
                    "frame-graph bands need unique IDs and contiguous indexes"
                )
            band_ids.add(band["id"])
            if not (
                0
                <= band["preferred_top_offset_mpt"]
                < band["preferred_bottom_offset_mpt"]
                <= page["page_height_mpt"]
            ):
                raise SchemaValidationError("preferred band geometry is invalid")
            native = [
                page_frames.get(identifier) for identifier in band["native_frame_ids"]
            ]
            candidates = [
                page_frames.get(identifier)
                for identifier in band["continuation_template_frame_ids"]
            ]
            if any(frame is None for frame in (*native, *candidates)):
                raise SchemaValidationError("band references an unknown frame")
            if len(native) != len(candidates):
                raise SchemaValidationError("band native and candidate topology differ")
            assert all(frame is not None for frame in (*native, *candidates))
            first_native = native[0]
            assert first_native is not None
            if (
                band["preferred_top_offset_mpt"]
                != page["page_height_mpt"] - first_native["bbox_mpt"][3]
                or band["preferred_bottom_offset_mpt"]
                != page["page_height_mpt"] - first_native["bbox_mpt"][1]
            ):
                raise SchemaValidationError(
                    "preferred band offsets disagree with PDF frame geometry"
                )
            for expected_column, (native_frame, candidate_frame) in enumerate(
                zip(native, candidates, strict=True)
            ):
                assert native_frame is not None and candidate_frame is not None
                if (
                    native_frame["kind"] != "native"
                    or candidate_frame["kind"] != "continuation-template"
                    or native_frame["source_band_id"] != band["source_band_id"]
                    or candidate_frame["source_band_id"] != band["source_band_id"]
                    or native_frame["band_index"] != band["band_index"]
                    or candidate_frame["band_index"] != band["band_index"]
                    or native_frame["column_index"] != expected_column
                    or candidate_frame["column_index"] != expected_column
                ):
                    raise SchemaValidationError("band frame topology is inconsistent")
                native_order.append(native_frame["id"])
            if sum(frame["width_ratio_ppm"] for frame in native if frame) != 1_000_000:
                raise SchemaValidationError("native frame width ratios must sum to one")

    edge_ids: set[str] = set()
    for expected_order, edge in enumerate(instance["edges"]):
        if edge["id"] in edge_ids or edge["order"] != expected_order:
            raise SchemaValidationError(
                "frame edges need unique IDs and contiguous order"
            )
        edge_ids.add(edge["id"])
        source = frame_by_id.get(edge["from_frame_id"])
        target = frame_by_id.get(edge["to_frame_id"])
        if source is None or target is None or source is target:
            raise SchemaValidationError("frame edge references are invalid")
        if edge["activation"] == "always" and (
            source["kind"] != "native" or target["kind"] != "native"
        ):
            raise SchemaValidationError("always edge must connect native frames")
    always_pairs = [
        (edge["from_frame_id"], edge["to_frame_id"])
        for edge in instance["edges"]
        if edge["activation"] == "always"
    ]
    if always_pairs != list(zip(native_order, native_order[1:], strict=False)):
        raise SchemaValidationError("native frame edges do not follow reading order")

    flows: dict[str, dict[str, Any]] = {}
    native_rank = {identifier: index for index, identifier in enumerate(native_order)}
    for flow in instance["unit_flows"]:
        if flow["unit_id"] in flows:
            raise SchemaValidationError("unit flow identifiers must be unique")
        flows[flow["unit_id"]] = flow
        allowed = flow["allowed_native_frame_ids"]
        if flow["home_frame_id"] != allowed[0] or any(
            identifier not in native_rank for identifier in allowed
        ):
            raise SchemaValidationError("unit flow frame allowlist is invalid")
        ranks = [native_rank[identifier] for identifier in allowed]
        if ranks != sorted(ranks):
            raise SchemaValidationError("unit flow frame allowlist is out of order")
        if flow["continuation_owner_page_number"] != max(
            frame_by_id[identifier]["source_page_number"] for identifier in allowed
        ):
            raise SchemaValidationError(
                "unit continuation owner is unrelated to its source"
            )
        if flow["style"]["semantic_role"] != flow["role"]:
            raise SchemaValidationError("unit flow role and style disagree")
        _validate_frame_lines(
            flow["lines"],
            flow["style"],
            font_names,
            label="unit flow",
            line_mapping_version=line_mapping_version,
        )
        if flow["line_count"] != len(flow["lines"]):
            raise SchemaValidationError("unit flow line count is invalid")
        if annotated:
            required = {
                "base_target_length",
                "composite_length",
                "composite_segments",
                "styled_spans",
                "source_anchor_candidates",
            }
            if not required <= set(flow):
                raise SchemaValidationError("v2 unit flow fields are missing")
            _validate_composite_flow(flow, label="unit flow", allow_synthetic=True)
            sequence_payload = {
                "line_sequence_contract_version": "2.0.0",
                "style": flow["style"],
                "composite_segments": flow["composite_segments"],
                "lines": flow["lines"],
            }
        else:
            if any(
                key in flow
                for key in (
                    "base_target_length",
                    "composite_length",
                    "composite_segments",
                    "styled_spans",
                    "source_anchor_candidates",
                )
            ):
                raise SchemaValidationError("legacy unit flow cannot carry v2 fields")
            sequence_payload = {"style": flow["style"], "lines": flow["lines"]}
        expected_sequence_hash = sha256_canonical(sequence_payload)
        if flow["line_sequence_hash"] != expected_sequence_hash:
            raise SchemaValidationError("unit flow line sequence hash is invalid")
        breaks = [item["after_line"] for item in flow["legal_breaks"]]
        if breaks != sorted(set(breaks)) or any(
            value >= flow["line_count"] for value in breaks
        ):
            raise SchemaValidationError("unit flow legal breaks are invalid")
        home = frame_by_id[flow["home_frame_id"]]
        expected_anchor = "leader" if home["column_count"] == 1 else "soft-y"
        if (
            flow["anchor"]["kind"] != expected_anchor
            or flow["anchor"]["source_page_number"] != home["source_page_number"]
            or not 0
            <= flow["anchor"]["source_visual_center_offset_mpt"]
            <= right_panel[3] - right_panel[1]
        ):
            raise SchemaValidationError("unit flow anchor policy is invalid")
        if annotated:
            candidates = flow["source_anchor_candidates"]
            if {
                "source_band_id",
                "source_first_line_bbox_mpt",
                "source_endpoint_mpt",
            } - set(flow["anchor"]):
                raise SchemaValidationError("v2 unit anchor fields are missing")
            candidate_pages = [item["source_page_number"] for item in candidates]
            allowed_pages = {
                frame_by_id[identifier]["source_page_number"] for identifier in allowed
            }
            if (
                candidate_pages != sorted(set(candidate_pages))
                or set(candidate_pages) != allowed_pages
                or flow["anchor"]["source_first_line_bbox_mpt"]
                != candidates[0]["source_first_line_bbox_mpt"]
                or flow["anchor"]["source_endpoint_mpt"]
                != candidates[0]["source_endpoint_mpt"]
                or flow["anchor"]["source_band_id"] != candidates[0]["source_band_id"]
            ):
                raise SchemaValidationError("unit source anchor candidates are invalid")
            for candidate in candidates:
                box = candidate["source_first_line_bbox_mpt"]
                _positive_box(box, label="source anchor first-line bbox")
                if (
                    candidate["source_endpoint_mpt"] != [box[2], (box[1] + box[3]) // 2]
                    or not (
                        0 <= box[0] < box[2] <= right_panel[0]
                        and 0 <= box[1] < box[3] <= right_panel[3] - right_panel[1]
                    )
                    or candidate["source_visual_center_offset_mpt"]
                    != right_panel[3] - right_panel[1] - (box[1] + box[3]) // 2
                    or not 0
                    <= candidate["source_visual_center_offset_mpt"]
                    <= right_panel[3] - right_panel[1]
                    or candidate["kind"]
                    not in {
                        (
                            "leader"
                            if frame_by_id[identifier]["column_count"] == 1
                            else "soft-y"
                        )
                        for identifier in allowed
                        if frame_by_id[identifier]["source_page_number"]
                        == candidate["source_page_number"]
                        and frame_by_id[identifier]["source_band_id"]
                        == candidate["source_band_id"]
                    }
                ):
                    raise SchemaValidationError("source anchor geometry is invalid")
        available_width = min(
            frame_by_id[identifier]["text_right_mpt"]
            - frame_by_id[identifier]["text_left_mpt"]
            for identifier in allowed
        )
        if any(line["width_mpt"] > available_width for line in flow["lines"]):
            raise SchemaValidationError("unit line exceeds an allowed frame width")

    part_by_unit: dict[str, dict[str, Any]] = {}
    native_ids = set(native_order)
    for part in instance["unit_parts"]:
        if part["unit_id"] in part_by_unit or part["frame_id"] not in native_ids:
            raise SchemaValidationError("initial unit parts must be unique and native")
        part_by_unit[part["unit_id"]] = part
    if set(part_by_unit) != set(flows):
        raise SchemaValidationError("every unit flow needs exactly one initial part")
    for unit_id, flow in flows.items():
        part = part_by_unit[unit_id]
        if (
            part["frame_id"] != flow["home_frame_id"]
            or part["line_end"] != flow["line_count"]
            or part["source_fragment_end"] != flow["source_fragment_end"]
        ):
            raise SchemaValidationError(
                "initial unit part does not cover its full flow"
            )

    note_ids: set[str] = set()
    note_indexes: dict[str, list[int]] = {}
    for note in instance["figure_note_flows"]:
        if (
            note["id"] in note_ids
            or note["unit_id"] not in flows
            or note["frame_id"] != flows[note["unit_id"]]["home_frame_id"]
            or note["style"]["semantic_role"] != "auxiliary"
        ):
            raise SchemaValidationError("figure note flow binding is invalid")
        note_ids.add(note["id"])
        note_indexes.setdefault(note["unit_id"], []).append(note["note_index"])
        _validate_frame_lines(
            note["lines"], note["style"], font_names, label="figure note flow"
        )
        if note["line_count"] != len(note["lines"]):
            raise SchemaValidationError("figure note line count is invalid")
        if note["line_sequence_hash"] != sha256_canonical(
            {"style": note["style"], "lines": note["lines"]}
        ):
            raise SchemaValidationError("figure note line sequence hash is invalid")
    if any(indexes != list(range(len(indexes))) for indexes in note_indexes.values()):
        raise SchemaValidationError("figure note indexes must be contiguous")

    auxiliary_ids: set[str] = set()
    auxiliary_annotation_ids: set[str] = set()
    auxiliary_indexes: dict[str, list[int]] = {}
    for auxiliary in instance.get("auxiliary_flows", []):
        unit_flow = flows.get(auxiliary["unit_id"])
        target_text = (
            ""
            if unit_flow is None
            else "".join(
                segment["text"]
                for segment in unit_flow["composite_segments"]
                if segment["kind"] == "target"
            )
        )
        from academic_pdf_en_zh_reader.layout.unit_parts import grapheme_boundaries

        safe_target_offsets = grapheme_boundaries(target_text)
        if (
            auxiliary["id"] in auxiliary_ids
            or unit_flow is None
            or auxiliary["annotation_id"] in auxiliary_annotation_ids
            or auxiliary["id"] != f"aux:{auxiliary['annotation_id']}"
            or auxiliary["attachment"] != "below-translation"
            or auxiliary["home_frame_id"] != unit_flow["home_frame_id"]
            or auxiliary["allowed_native_frame_ids"]
            != unit_flow["allowed_native_frame_ids"]
            or auxiliary["continuation_owner_page_number"]
            != unit_flow["continuation_owner_page_number"]
            or auxiliary["parent_target_length"] != unit_flow["base_target_length"]
            or auxiliary["attachment_target_start"] not in safe_target_offsets
            or auxiliary["attachment_target_end"] not in safe_target_offsets
            or auxiliary["style"]["semantic_role"] != "auxiliary"
        ):
            raise SchemaValidationError("auxiliary flow binding is invalid")
        auxiliary_ids.add(auxiliary["id"])
        auxiliary_annotation_ids.add(auxiliary["annotation_id"])
        auxiliary_indexes.setdefault(auxiliary["unit_id"], []).append(
            auxiliary["order_after_unit"]
        )
        _validate_frame_lines(
            auxiliary["lines"],
            auxiliary["style"],
            font_names,
            label="auxiliary flow",
            line_mapping_version=2,
            initial_target_start=auxiliary["attachment_target_end"],
        )
        _validate_auxiliary_flow_mapping(auxiliary, label="auxiliary flow")
        if auxiliary["line_count"] != len(auxiliary["lines"]):
            raise SchemaValidationError("auxiliary flow line count is invalid")
        expected_auxiliary_hash = sha256_canonical(
            {
                "line_sequence_contract_version": "2.0.0",
                "style": auxiliary["style"],
                "composite_segments": auxiliary["composite_segments"],
                "lines": auxiliary["lines"],
            }
        )
        if auxiliary["line_sequence_hash"] != expected_auxiliary_hash:
            raise SchemaValidationError("auxiliary flow line hash is invalid")
    if any(
        indexes != list(range(len(indexes))) for indexes in auxiliary_indexes.values()
    ):
        raise SchemaValidationError("auxiliary flow indexes must be contiguous")

    content_ids = (
        {f"unit:{identifier}" for identifier in flows} | note_ids | auxiliary_ids
    )
    for page in instance["pages"]:
        for band in page["bands"]:
            initial_ids = band["initial_unsplit_content_item_ids"]
            if len(initial_ids) != len(set(initial_ids)):
                raise SchemaValidationError("band content IDs must be unique")
            if any(identifier not in content_ids for identifier in initial_ids):
                raise SchemaValidationError("band references unknown content")


def _validate_layout(instance: dict[str, Any]) -> None:
    from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

    annotated = instance.get("line_mapping_version") == 2
    if not annotated and "line_mapping_version" in instance:
        raise SchemaValidationError("layout line mapping version is invalid")
    right_panel = instance["right_panel_bbox_mpt"]
    _positive_box(right_panel, label="layout right panel bbox_mpt")
    roles = [face["role"] for face in instance["font_fingerprint"]]
    if roles != ["body", "heading", "symbols"]:
        raise SchemaValidationError(
            "layout font fingerprint must use the complete stable role order"
        )
    font_names = {
        face["role"]: face["reportlab_name"] for face in instance["font_fingerprint"]
    }
    expected_input_hash = sha256_canonical(
        {
            "solver_contract_version": "1.0.0",
            "frame_graph_input_hash": instance["frame_graph_input_hash"],
            "frame_graph_hash": instance["frame_graph_hash"],
            "flow_spacing": instance["flow_spacing"],
            "solver_policy": instance["solver_policy"],
        }
    )
    if instance["solver_input_hash"] != expected_input_hash:
        raise SchemaValidationError("solver_input_hash does not bind the frozen inputs")

    page_keys: list[tuple[int, int]] = []
    total_continuations = 0
    block_ids: set[str] = set()
    for page in instance["pages"]:
        page_height = page["page_height_mpt"]
        if page_height != right_panel[3] - right_panel[1]:
            raise SchemaValidationError("layout page height disagrees with right panel")
        page_kind = page["page_kind"]
        continuation_index = page["continuation_index"]
        if page_kind == "native":
            if continuation_index != 0 or page["continuation_label"] is not None:
                raise SchemaValidationError("native layout page metadata is invalid")
            page_top_reserve = 0
        else:
            total_continuations += 1
            if continuation_index < 1:
                raise SchemaValidationError(
                    "continuation layout page metadata is invalid"
                )
            page_top_reserve = _validate_continuation_label(
                page,
                right_panel=right_panel,
                flow_spacing=instance["flow_spacing"],
                font_names=font_names,
            )
        page_keys.append((page["source_page_number"], continuation_index))

        bands = page["bands"]
        if [band["band_index"] for band in bands] != list(range(len(bands))):
            raise SchemaValidationError("layout band indexes must be contiguous")
        band_by_source: dict[str, dict[str, Any]] = {}
        previous_bottom = page_top_reserve
        for index, band in enumerate(bands):
            source_band_id = band["source_band_id"]
            if source_band_id in band_by_source:
                raise SchemaValidationError("layout source band IDs must be unique")
            band_by_source[source_band_id] = band
            _positive_box(band["bbox_mpt"], label="layout band bbox_mpt")
            top = band["solved_top_offset_mpt"]
            height = band["height_mpt"]
            if band["bbox_mpt"] != [
                right_panel[0],
                page_height - top - height,
                right_panel[2],
                page_height - top,
            ]:
                raise SchemaValidationError(
                    "layout band bbox disagrees with top-down geometry"
                )
            required_top = previous_bottom + (
                instance["solver_policy"]["band_gap_mpt"] if index else 0
            )
            if top < required_top or top + height > page_height:
                raise SchemaValidationError("layout bands overlap or leave page bounds")
            previous_bottom = top + height

        frame_by_id: dict[str, dict[str, Any]] = {}
        frames_by_band: dict[str, list[str]] = {}
        for frame in page["frames"]:
            identifier = frame["id"]
            if identifier in frame_by_id:
                raise SchemaValidationError("layout frame IDs must be unique per page")
            frame_by_id[identifier] = frame
            source_band_id = frame["source_band_id"]
            band = band_by_source.get(source_band_id)
            if band is None:
                raise SchemaValidationError("layout frame references an unknown band")
            frames_by_band.setdefault(source_band_id, []).append(identifier)
            _positive_box(frame["bbox_mpt"], label="layout frame bbox_mpt")
            frame_box = frame["bbox_mpt"]
            band_box = band["bbox_mpt"]
            if not (
                right_panel[0] <= frame_box[0] < frame_box[2] <= right_panel[2]
                and frame_box[1] == band_box[1]
                and frame_box[3] == band_box[3]
                and frame["source_page_number"] == page["source_page_number"]
                and frame["band_index"] == band["band_index"]
                and frame["column_index"] < frame["column_count"]
                and frame_box[0]
                <= frame["text_left_mpt"]
                < frame["text_right_mpt"]
                <= frame_box[2]
            ):
                raise SchemaValidationError(
                    "layout frame geometry or binding is invalid"
                )
        for source_band_id, band in band_by_source.items():
            if band["frame_ids"] != frames_by_band.get(source_band_id, []):
                raise SchemaValidationError("layout band frame order is invalid")

        blocks_by_frame: dict[str, list[dict[str, Any]]] = {}
        for block in page["blocks"]:
            if block["id"] in block_ids:
                raise SchemaValidationError("layout block IDs must be globally unique")
            block_ids.add(block["id"])
            frame = frame_by_id.get(block["frame_id"])
            if (
                frame is None
                or block["template_frame_id"] != frame["template_frame_id"]
            ):
                raise SchemaValidationError("layout block references an unknown frame")
            if block["line_end"] - block["line_start"] != len(block["lines"]):
                raise SchemaValidationError("layout block line range is invalid")
            _positive_box(block["bbox_mpt"], label="layout block bbox_mpt")
            block_box = block["bbox_mpt"]
            frame_box = frame["bbox_mpt"]
            if not (
                frame["text_left_mpt"] == block_box[0]
                and block_box[2] <= frame["text_right_mpt"]
                and frame_box[1] <= block_box[1] < block_box[3] <= frame_box[3]
            ):
                raise SchemaValidationError("layout block is outside its final frame")
            if annotated:
                if "selected_anchor" not in block:
                    raise SchemaValidationError("v2 layout block needs selected_anchor")
                selected_anchor = block["selected_anchor"]
                if block["creates_anchor"] != (selected_anchor is not None):
                    raise SchemaValidationError(
                        "layout anchor flag and selected anchor disagree"
                    )
                if selected_anchor is not None:
                    anchor_box = selected_anchor["source_first_line_bbox_mpt"]
                    _positive_box(anchor_box, label="layout source anchor bbox")
                    if (
                        block["part_index"] != 0
                        or selected_anchor["source_page_number"]
                        != page["source_page_number"]
                        or selected_anchor["source_endpoint_mpt"]
                        != [
                            anchor_box[2],
                            (anchor_box[1] + anchor_box[3]) // 2,
                        ]
                        or not (
                            0 <= anchor_box[0] < anchor_box[2] <= right_panel[0]
                            and 0 <= anchor_box[1] < anchor_box[3] <= page_height
                        )
                        or selected_anchor["source_visual_center_offset_mpt"]
                        != page_height - (anchor_box[1] + anchor_box[3]) // 2
                    ):
                        raise SchemaValidationError("layout selected anchor is invalid")
            elif "selected_anchor" in block:
                raise SchemaValidationError("legacy layout cannot select a v2 anchor")
            elif block["creates_anchor"] and (
                block["part_index"] != 0 or page_kind != "native"
            ):
                raise SchemaValidationError("layout anchor must be a first native part")

            cursor = block["solved_top_offset_mpt"]
            previous_target_end: int | None = None
            previous_composite_end: int | None = None
            max_width = 0
            for offset, line in enumerate(block["lines"]):
                if line["index"] != block["line_start"] + offset:
                    raise SchemaValidationError(
                        "layout line indexes are not contiguous"
                    )
                if (
                    previous_target_end is not None
                    and line["target_start"] != previous_target_end
                ):
                    raise SchemaValidationError(
                        "layout line target ranges are not contiguous"
                    )
                if annotated:
                    if (
                        set(
                            (
                                "composite_start",
                                "composite_end",
                                "synthetic_annotation_ids",
                            )
                        )
                        - set(line)
                        or (
                            previous_composite_end is not None
                            and line["composite_start"] != previous_composite_end
                        )
                        or line["composite_end"] <= line["composite_start"]
                        or line["target_end"] < line["target_start"]
                        or (
                            line["target_end"] == line["target_start"]
                            and not line["synthetic_annotation_ids"]
                        )
                    ):
                        raise SchemaValidationError(
                            "layout composite line range is invalid"
                        )
                    line_contract_version = "2.0.0"
                else:
                    if line["target_end"] <= line["target_start"]:
                        raise SchemaValidationError(
                            "layout line target range is invalid"
                        )
                    line_contract_version = "1.0.0"
                if (
                    line["style_id"] != block["style"]["style_id"]
                    or line["line_height_mpt"] < block["style"]["line_height_mpt"]
                    or "".join(run["text"] for run in line["runs"]) != line["text"]
                    or any(
                        font_names.get(run["font_role"]) != run["font_name"]
                        for run in line["runs"]
                    )
                ):
                    raise SchemaValidationError(
                        "layout line style or font binding is invalid"
                    )
                frozen_line = {
                    key: value
                    for key, value in line.items()
                    if key not in {"line_box_hash", "x_mpt", "baseline_y_mpt"}
                }
                if line["line_box_hash"] != sha256_canonical(
                    {
                        "line_box_contract_version": line_contract_version,
                        **frozen_line,
                    }
                ):
                    raise SchemaValidationError("layout line_box_hash is invalid")
                if (
                    line["x_mpt"] != frame["text_left_mpt"]
                    or line["baseline_y_mpt"]
                    != page_height - cursor - line["ascent_mpt"]
                ):
                    raise SchemaValidationError("layout line coordinates are invalid")
                cursor += line["line_height_mpt"]
                max_width = max(max_width, line["width_mpt"])
                previous_target_end = line["target_end"]
                if annotated:
                    previous_composite_end = line["composite_end"]
            height = cursor - block["solved_top_offset_mpt"]
            if block_box != [
                frame["text_left_mpt"],
                page_height - block["solved_top_offset_mpt"] - height,
                min(frame["text_right_mpt"], frame["text_left_mpt"] + max_width),
                page_height - block["solved_top_offset_mpt"],
            ]:
                raise SchemaValidationError(
                    "layout block bbox disagrees with its lines"
                )
            blocks_by_frame.setdefault(block["frame_id"], []).append(block)
        for blocks in blocks_by_frame.values():
            ordered = sorted(blocks, key=lambda block: block["solved_top_offset_mpt"])
            for upper, lower in zip(ordered, ordered[1:], strict=False):
                upper_bottom = upper["solved_top_offset_mpt"] + sum(
                    line["line_height_mpt"] for line in upper["lines"]
                )
                if upper_bottom > lower["solved_top_offset_mpt"]:
                    raise SchemaValidationError("layout blocks overlap")

    if page_keys != sorted(page_keys):
        raise SchemaValidationError(
            "layout pages must keep each continuation after its source page"
        )
    grouped_indexes: dict[int, list[int]] = {}
    for source_page, continuation_index in page_keys:
        grouped_indexes.setdefault(source_page, []).append(continuation_index)
    if any(
        indexes != list(range(len(indexes))) for indexes in grouped_indexes.values()
    ):
        raise SchemaValidationError("layout continuation indexes must be contiguous")
    trace = instance["solver_trace"]
    if trace["continuation_page_count"] != total_continuations:
        raise SchemaValidationError("layout continuation trace disagrees with pages")
    if trace["band_height_objective_mpt"] != [
        band["actual_content_height_mpt"] for band in trace["band_heights"]
    ]:
        raise SchemaValidationError(
            "layout band-height objective trace is inconsistent"
        )
    if (trace["native_status"] == "exhausted") != trace[
        "native_window_and_band_search_exhausted"
    ]:
        raise SchemaValidationError("layout native exhaustion trace is inconsistent")
    if (trace["native_exhaustion_reason"] is not None) != (
        trace["native_status"] == "exhausted"
    ):
        raise SchemaValidationError("layout native exhaustion reason is inconsistent")
    if (trace["continuation_reason"] is not None) != (total_continuations > 0):
        raise SchemaValidationError("layout continuation reason is inconsistent")
    for index, attempt in enumerate(trace["window_attempts"]):
        if (
            attempt["attempt_index"] != index
            or attempt["window_start"] >= attempt["window_end"]
            or attempt["frame_top_mpt"] >= attempt["frame_bottom_mpt"]
            or attempt["frozen_before_hash"] != attempt["frozen_after_hash"]
        ):
            raise SchemaValidationError("layout window trace is invalid")
        accepted = attempt["result"] == "accepted"
        if accepted != (attempt["minimum_d_mpt"] is not None) or accepted != (
            attempt["weighted_l1_cost"] is not None
        ):
            raise SchemaValidationError("layout window result costs are inconsistent")


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
                ("semantic.independent-review", "semantic"),
                ("content.annotation-policy", "content"),
                ("geometry.a3-pages", "geometry"),
                ("geometry.source-left-one-to-one", "geometry"),
                ("geometry.mirrored-frames", "geometry"),
                ("geometry.bounds-and-overlap", "geometry"),
                ("geometry.leader-policy", "geometry"),
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
    elif name == "frame-graph":
        _strictly_increasing(
            instance["pages"], "page_number", label="frame-graph pages"
        )
        _validate_frame_graph(instance)
    elif name == "layout":
        _strictly_increasing(instance["pages"], "page_number", label="layout pages")
        _validate_layout(instance)
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
