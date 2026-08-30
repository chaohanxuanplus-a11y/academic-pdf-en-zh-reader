# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Freeze one low-priority provenance card on a safe reference-only page."""

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

    manifest_path = Path(path).resolve(strict=True)
    try:
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
    raw_path = image.get("path")
    expected_hash = image.get("sha256")
    if (
        raw_path != "assets/branding/hanhai-wencai.png"
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
    ):
        raise OverlayPlanError("BRAND_ASSET_INVALID", "brand image identity is invalid")
    asset_path = (PROJECT_ROOT / raw_path).resolve(strict=True)
    try:
        asset_path.relative_to(_BRAND_DIR)
    except ValueError as exc:
        raise OverlayPlanError(
            "BRAND_ASSET_INVALID", "brand image path is invalid"
        ) from exc
    if (
        not asset_path.is_file()
        or asset_path.stat().st_size != image.get("size")
        or _sha256(asset_path) != expected_hash
    ):
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


def select_reference_only_page(
    source: Mapping[str, object],
    layout: Mapping[str, object],
) -> int | None:
    """Choose an empty native page with references and no required text."""

    source_pages = {
        int(page["page_number"]): page
        for page in source["pages"]  # type: ignore[index]
    }
    for output_page in layout["pages"]:  # type: ignore[index]
        if output_page["page_kind"] != "native" or output_page["blocks"]:
            continue
        source_page = source_pages.get(int(output_page["source_page_number"]))
        if source_page is None:
            continue
        blocks = source_page["blocks"]
        if any(block["translation_policy"] == "required" for block in blocks):
            continue
        if any(
            block["role"] == "reference-entry"
            and block["translation_policy"] == "excluded"
            for block in blocks
        ):
            return int(output_page["page_number"])
    return None


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
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    """Freeze image geometry and vector text for one already selected safe page."""

    layout = manifest["layout"]  # type: ignore[index]
    card_width = min(int(layout["card_max_width_mpt"]), A4_WIDTH_MPT - 96_000)
    if card_width < int(layout["card_min_width_mpt"]):
        raise OverlayPlanError("BRAND_LAYOUT_INVALID", "brand card width is invalid")
    padding_x = int(layout["card_padding_x_mpt"])
    padding_y = int(layout["card_padding_y_mpt"])
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
    card_height = 2 * padding_y + logo_size + transition_gaps + text_height
    if card_height >= A3_LANDSCAPE_HEIGHT_MPT - 2 * int(layout["page_margin_mpt"]):
        raise OverlayPlanError("BRAND_LAYOUT_INVALID", "brand card does not fit")
    left = A4_WIDTH_MPT + (A4_WIDTH_MPT - card_width) // 2
    bottom = (A3_LANDSCAPE_HEIGHT_MPT - card_height) // 2
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
        centered = kind in {"brand", "brand-en", "skill", "github", "disclaimer"}
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
    "select_reference_only_page",
    "validate_brand_block",
]
