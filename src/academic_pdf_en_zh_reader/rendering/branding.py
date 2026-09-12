# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Freeze the final provenance and responsibility card without moving content."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from PIL import Image
from reportlab.pdfbase import pdfmetrics

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.contracts import OverlayPlanError
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A4_WIDTH_MPT,
)
from academic_pdf_en_zh_reader.security.runtime_paths import resolve_runtime_path
from academic_pdf_en_zh_reader.typography.cjk_breaker import break_text
from academic_pdf_en_zh_reader.typography.font_registry import (
    PROJECT_ROOT,
    load_font_registry,
)
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.measure import LineBox

BRAND_MANIFEST_PATH = PROJECT_ROOT / "assets" / "branding" / "brand-manifest.json"
_BRAND_DIR = (PROJECT_ROOT / "assets" / "branding").resolve()
_BODY_COLOR = "#111111"
_MUTED_COLOR = "#666666"
_LAYOUT_POSITIVE_INT_FIELDS = (
    "policy_version",
    "page_margin_mpt",
    "card_max_width_mpt",
    "card_min_width_mpt",
    "card_padding_x_mpt",
    "card_padding_y_mpt",
    "logo_size_mpt",
    "section_gap_mpt",
    "brand_pair_gap_mpt",
    "brand_size_mpt",
    "brand_line_height_mpt",
    "brand_en_size_mpt",
    "brand_en_line_height_mpt",
    "skill_size_mpt",
    "skill_line_height_mpt",
    "github_size_mpt",
    "github_line_height_mpt",
    "disclaimer_size_mpt",
    "disclaimer_line_height_mpt",
)


def _validated_brand_layout(value: object, *, code: str) -> dict[str, int]:
    def invalid() -> OverlayPlanError:
        return OverlayPlanError(code, "brand manifest layout is invalid")

    if not isinstance(value, Mapping) or set(value) != set(_LAYOUT_POSITIVE_INT_FIELDS):
        raise invalid()
    if any(
        field not in value or type(value[field]) is not int or int(value[field]) <= 0
        for field in _LAYOUT_POSITIVE_INT_FIELDS
    ):
        raise invalid()
    layout = {field: int(value[field]) for field in _LAYOUT_POSITIVE_INT_FIELDS}
    page_margin = layout["page_margin_mpt"]
    panel_width = A4_WIDTH_MPT - 2 * page_margin
    panel_height = A3_LANDSCAPE_HEIGHT_MPT - 2 * page_margin
    card_width = min(layout["card_max_width_mpt"], panel_width)
    inner_width = card_width - 2 * layout["card_padding_x_mpt"]
    text_pairs = (
        (layout["brand_size_mpt"], layout["brand_line_height_mpt"]),
        (layout["brand_en_size_mpt"], layout["brand_en_line_height_mpt"]),
        (layout["skill_size_mpt"], layout["skill_line_height_mpt"]),
        (layout["github_size_mpt"], layout["github_line_height_mpt"]),
        (
            layout["disclaimer_size_mpt"],
            layout["disclaimer_line_height_mpt"],
        ),
    )
    minimum_card_height = (
        2 * layout["card_padding_y_mpt"]
        + layout["logo_size_mpt"]
        + layout["brand_line_height_mpt"]
        + layout["brand_en_line_height_mpt"]
        + layout["skill_line_height_mpt"]
        + layout["github_line_height_mpt"]
        + 6 * layout["disclaimer_line_height_mpt"]
        + layout["brand_pair_gap_mpt"]
        + 4 * layout["section_gap_mpt"]
    )
    if (
        layout["policy_version"] != 2
        or panel_width <= 0
        or panel_height <= 0
        or layout["card_min_width_mpt"] > layout["card_max_width_mpt"]
        or card_width < layout["card_min_width_mpt"]
        or inner_width <= 0
        or layout["logo_size_mpt"] > inner_width
        or any(size > line_height for size, line_height in text_pairs)
        or minimum_card_height >= panel_height
    ):
        raise invalid()
    return layout


def _mpt_ceil(value_pt: float) -> int:
    return int(
        (Decimal(str(value_pt)) * 1000).to_integral_value(rounding=ROUND_CEILING)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_brand_manifest(
    path: str | Path = BRAND_MANIFEST_PATH,
) -> tuple[dict[str, object], Path, str]:
    """Load and verify the fixed local brand asset without trusting plan paths."""

    try:
        manifest_path = resolve_runtime_path(Path(path), strict=True)
        manifest_path.relative_to(_BRAND_DIR)
        raw = manifest_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise OverlayPlanError(
            "BRAND_ASSET_INVALID", "brand manifest is invalid"
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise OverlayPlanError("BRAND_ASSET_INVALID", "brand manifest is invalid")
    image = value.get("image")
    layout = value.get("layout")
    required_text = (
        value.get("brand_name_zh"),
        value.get("brand_name_en"),
        value.get("skill_name"),
        value.get("github_display"),
        value.get("disclaimer_zh_short"),
    )
    disclaimer_lines = value.get("disclaimer_zh_lines")
    if (
        not isinstance(image, dict)
        or not isinstance(layout, dict)
        or any(not isinstance(item, str) or not item for item in required_text)
        or not isinstance(disclaimer_lines, list)
        or len(disclaimer_lines) != 6
        or any(not isinstance(item, str) or not item for item in disclaimer_lines)
    ):
        raise OverlayPlanError("BRAND_ASSET_INVALID", "brand manifest is incomplete")
    _validated_brand_layout(layout, code="BRAND_ASSET_INVALID")
    raw_path = image.get("path")
    expected_hash = image.get("sha256")
    if (
        raw_path != "assets/branding/hanhai-wencai.png"
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
    ):
        raise OverlayPlanError("BRAND_ASSET_INVALID", "brand image identity is invalid")
    try:
        asset_path = resolve_runtime_path(PROJECT_ROOT / raw_path, strict=True)
        asset_path.relative_to(_BRAND_DIR)
    except (OSError, ValueError) as exc:
        raise OverlayPlanError(
            "BRAND_ASSET_INVALID", "brand image path is invalid"
        ) from exc
    try:
        asset_bytes_differ = (
            not asset_path.is_file()
            or asset_path.stat().st_size != image.get("size")
            or _sha256(asset_path) != expected_hash
        )
    except OSError as exc:
        raise OverlayPlanError(
            "BRAND_ASSET_INVALID", "brand image cannot be read"
        ) from exc
    if asset_bytes_differ:
        raise OverlayPlanError("BRAND_ASSET_INVALID", "brand image bytes differ")
    try:
        with Image.open(asset_path) as opened:
            identity = (opened.width, opened.height, opened.mode, opened.format)
    except (OSError, ValueError) as exc:
        raise OverlayPlanError(
            "BRAND_ASSET_INVALID", "brand image cannot be read"
        ) from exc
    if identity != (
        image.get("width_px"),
        image.get("height_px"),
        image.get("mode"),
        "PNG",
    ):
        raise OverlayPlanError("BRAND_ASSET_INVALID", "brand image metadata differs")
    return value, asset_path, hashlib.sha256(raw).hexdigest()


def _text_lines(
    manifest: Mapping[str, object],
    *,
    inner_width_mpt: int,
) -> list[tuple[str, LineBox, str, int]]:
    layout = manifest["layout"]  # type: ignore[index]
    resolver = FontRunResolver(load_font_registry())
    result: list[tuple[str, LineBox, str, int]] = []
    entries = (
        (
            "brand",
            str(manifest["brand_name_zh"]),
            "heading",
            int(layout["brand_size_mpt"]),
            int(layout["brand_line_height_mpt"]),
            _BODY_COLOR,
        ),
        (
            "brand-en",
            str(manifest["brand_name_en"]),
            "body",
            int(layout["brand_en_size_mpt"]),
            int(layout["brand_en_line_height_mpt"]),
            _BODY_COLOR,
        ),
        (
            "skill",
            str(manifest["skill_name"]),
            "body",
            int(layout["skill_size_mpt"]),
            int(layout["skill_line_height_mpt"]),
            _BODY_COLOR,
        ),
        (
            "github",
            str(manifest["github_display"]),
            "body",
            int(layout["github_size_mpt"]),
            int(layout["github_line_height_mpt"]),
            _MUTED_COLOR,
        ),
    )
    entries = (
        *entries,
        *(
            (
                "disclaimer",
                str(text),
                "body",
                int(layout["disclaimer_size_mpt"]),
                int(layout["disclaimer_line_height_mpt"]),
                _MUTED_COLOR,
            )
            for text in manifest["disclaimer_zh_lines"]
        ),
    )
    for kind, text, font_role, size_mpt, line_height_mpt, color in entries:
        lines = break_text(
            text,
            max_width_pt=inner_width_mpt / 1000,
            resolver=resolver,
            font_role=font_role,
            size_pt=size_mpt / 1000,
            line_height_pt=line_height_mpt / 1000,
        )
        result.extend((kind, line, color, line_height_mpt) for line in lines)
    return result


def _transition_gap_mpt(
    layout: Mapping[str, object], upper_kind: str, lower_kind: str
) -> int:
    if (upper_kind, lower_kind) == ("brand", "brand-en"):
        return int(layout["brand_pair_gap_mpt"])
    return int(layout["section_gap_mpt"])


def freeze_brand_block(
    *,
    output_page_number: int,
    start_draw_order: int,
    manifest: Mapping[str, object],
    manifest_sha256: str,
    available_y_mpt: tuple[int, int] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]] | None:
    """Freeze image geometry and vector text for one already selected safe page."""

    layout = _validated_brand_layout(
        manifest.get("layout"), code="BRAND_LAYOUT_INVALID"
    )
    page_margin = layout["page_margin_mpt"]
    card_width = min(layout["card_max_width_mpt"], A4_WIDTH_MPT - 2 * page_margin)
    padding_x = layout["card_padding_x_mpt"]
    padding_y = layout["card_padding_y_mpt"]
    inner_width = card_width - 2 * padding_x
    lines = _text_lines(manifest, inner_width_mpt=inner_width)
    logo_size = int(layout["logo_size_mpt"])
    gap = int(layout["section_gap_mpt"])
    text_height = sum(item[3] for item in lines)
    transition_gaps = sum(
        _transition_gap_mpt(layout, left[0], right[0])
        for left, right in zip(lines, lines[1:], strict=False)
        if left[0] != right[0]
    )
    card_height = 2 * padding_y + logo_size + gap + transition_gaps + text_height
    full_bottom = page_margin
    full_top = A3_LANDSCAPE_HEIGHT_MPT - page_margin
    if card_height >= full_top - full_bottom:
        raise OverlayPlanError("BRAND_LAYOUT_INVALID", "brand card does not fit")
    region_bottom, region_top = (
        (full_bottom, full_top) if available_y_mpt is None else available_y_mpt
    )
    region_bottom = max(region_bottom, full_bottom)
    region_top = min(region_top, full_top)
    if card_height > region_top - region_bottom:
        return None
    left = A4_WIDTH_MPT + (A4_WIDTH_MPT - card_width) // 2
    bottom = region_bottom + (region_top - region_bottom - card_height) // 2
    right = left + card_width
    top = bottom + card_height
    logo_left = left + (card_width - logo_size) // 2
    logo_top = top - padding_y
    logo_box = [logo_left, logo_top - logo_size, logo_left + logo_size, logo_top]

    cursor_top = logo_box[1] - gap
    bindings: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []
    composite_cursor = 0
    draw_order = start_draw_order
    previous_kind: str | None = None
    for line_index, (kind, line, color, line_height_mpt) in enumerate(lines):
        if previous_kind is not None and kind != previous_kind:
            cursor_top -= _transition_gap_mpt(layout, previous_kind, kind)
        ascent_mpt = _mpt_ceil(line.ascent_pt)
        baseline = cursor_top - ascent_mpt
        width_mpt = _mpt_ceil(line.width_pt)
        centered = kind in {
            "brand",
            "brand-en",
            "skill",
            "github",
            "disclaimer",
        }
        line_x = left + padding_x
        if centered:
            line_x = left + (card_width - width_mpt) // 2
        line_payload: dict[str, object] = {
            "content_id": "brand-block",
            "unit_id": "brand-block",
            "content_kind": "brand",
            "part_index": 0,
            "line_index": line_index,
            "target_start": 0,
            "target_end": 0,
            "composite_start": composite_cursor,
            "composite_end": composite_cursor + len(line.text),
            "visible_composite_start": composite_cursor,
            "visible_composite_end": composite_cursor + len(line.text),
            "synthetic_annotation_ids": [],
            "line_box_hash": sha256_canonical(
                {
                    "text": line.text,
                    "x_mpt": line_x,
                    "baseline_y_mpt": baseline,
                    "width_mpt": width_mpt,
                    "size_mpt": round(line.size_pt * 1000),
                    "line_height_mpt": line_height_mpt,
                }
            ),
        }
        line_payload["line_binding_hash"] = sha256_canonical(line_payload)
        bindings.append(line_payload)
        run_cursor = line_x
        for font_run in line.resolved.runs:
            width = _mpt_ceil(
                pdfmetrics.stringWidth(font_run.text, font_run.font_name, line.size_pt)
            )
            ascent, descent = pdfmetrics.getAscentDescent(
                font_run.font_name, line.size_pt
            )
            draw: dict[str, object] = {
                "draw_run_id": f"draw:p{output_page_number:05d}:{draw_order:07d}",
                "draw_order": draw_order,
                "content_id": "brand-block",
                "unit_id": "brand-block",
                "content_kind": "brand",
                "part_index": 0,
                "line_index": line_index,
                "style_id": f"brand:{kind}",
                "font_role": font_run.font_role,
                "font_name": font_run.font_name,
                "text": font_run.text,
                "size_mpt": round(line.size_pt * 1000),
                "color_token": "body" if color == _BODY_COLOR else "muted_gray",
                "color_hex": color,
                "target_start": 0,
                "target_end": 0,
                "composite_start": composite_cursor,
                "composite_end": composite_cursor + len(font_run.text),
                "x_mpt": run_cursor,
                "baseline_y_mpt": baseline,
                "width_mpt": width,
                "bbox_mpt": [
                    run_cursor,
                    baseline + _mpt_ceil(descent),
                    run_cursor + width,
                    baseline + _mpt_ceil(ascent),
                ],
            }
            draw["draw_run_hash"] = sha256_canonical(draw)
            runs.append(draw)
            run_cursor += width
            composite_cursor += len(font_run.text)
            draw_order += 1
        cursor_top -= line_height_mpt
        previous_kind = kind

    block: dict[str, object] = {
        "asset_path": manifest["image"]["path"],  # type: ignore[index]
        "asset_sha256": manifest["image"]["sha256"],  # type: ignore[index]
        "asset_size": manifest["image"]["size"],  # type: ignore[index]
        "manifest_sha256": manifest_sha256,
        "bbox_mpt": [left, bottom, right, top],
        "image_bbox_mpt": logo_box,
        "text_run_count": len(runs),
        "policy_version": layout["policy_version"],
    }
    block["brand_block_hash"] = sha256_canonical(block)
    return block, bindings, runs


def validate_brand_block(block: object) -> Mapping[str, object] | None:
    if block is None:
        return None
    required = {
        "asset_path",
        "asset_sha256",
        "asset_size",
        "manifest_sha256",
        "bbox_mpt",
        "image_bbox_mpt",
        "text_run_count",
        "policy_version",
        "brand_block_hash",
    }
    if (
        not isinstance(block, Mapping)
        or set(block) != required
        or block.get("brand_block_hash")
        != sha256_canonical(
            {key: value for key, value in block.items() if key != "brand_block_hash"}
        )
        or block.get("asset_path") != "assets/branding/hanhai-wencai.png"
    ):
        raise OverlayPlanError("PLAN_TAMPERED", "brand block is invalid")
    manifest, _asset_path, manifest_hash = load_brand_manifest()
    image = manifest["image"]
    if (
        block.get("asset_sha256") != image["sha256"]
        or block.get("asset_size") != image["size"]
        or block.get("manifest_sha256") != manifest_hash
    ):
        raise OverlayPlanError("PLAN_TAMPERED", "brand asset binding is invalid")
    return block


__all__ = [
    "BRAND_MANIFEST_PATH",
    "freeze_brand_block",
    "load_brand_manifest",
    "validate_brand_block",
]
