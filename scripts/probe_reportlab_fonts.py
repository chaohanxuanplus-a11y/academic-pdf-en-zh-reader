# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Prove that the pinned fonts embed, extract, render, and repeat reliably."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pypdf
import pypdfium2 as pdfium
import reportlab
from fontTools.ttLib import TTCollection
from fontTools.ttLib import TTFont as FontToolsTTFont
from pypdf import PdfReader
from pypdfium2 import version as pdfium_version
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as ReportLabTTFont
from reportlab.pdfgen import canvas

from scripts.bootstrap_fonts import (
    ROOT,
    _atomic_write_json,
    compute_git_blob_sha1,
    inspect_font,
)

PROBE_LINES = (
    ("heading", "地道且精确的学术翻译", 15.0),
    (
        "body",
        "中文事实程度逻辑数据术语：αβΔμ±×≤≥→℃²。",
        11.0,
    ),
    ("symbols", "⏱ ☢ ✓", 11.0),
)
PROBE_CONTRACT = {
    "schema_version": 1,
    "page_size": list(A4),
    "left": 72.0,
    "top": 72.0,
    "line_gap_factor": 2.2,
    "page_compression": 1,
    "invariant": 1,
    "lines": [list(line) for line in PROBE_LINES],
}
SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_manifest_fonts(manifest: dict[str, Any]) -> dict[str, str]:
    font_hashes: dict[str, str] = {}
    for record in manifest["fonts"]:
        path = ROOT / str(record["path"])
        data = path.read_bytes()
        if len(data) != record["size"]:
            raise ValueError(f"font size is stale for role {record['role']}")
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != record["sha256"]:
            raise ValueError(f"font SHA-256 is stale for role {record['role']}")
        if compute_git_blob_sha1(data) != record["git_blob_sha1"]:
            raise ValueError(f"font Git blob is stale for role {record['role']}")
        inspected = inspect_font(
            path,
            expected_family=record["expected_family"],
            expected_subfamily=record["expected_subfamily"],
        )
        for key in ("face_index", "postscript_name", "outline_format"):
            if inspected[key] != record[key]:
                raise ValueError(f"font {key} is stale for role {record['role']}")
        font_hashes[str(record["role"])] = sha256
    return font_hashes


def _register_fonts(manifest: dict[str, Any]) -> dict[str, str]:
    registered: dict[str, str] = {}
    for record in manifest["fonts"]:
        role = str(record["role"])
        path = ROOT / str(record["path"])
        internal_name = f"APR-{role}-{record['sha256'][:8]}"
        kwargs: dict[str, Any] = {"validate": 1}
        if path.suffix.casefold() == ".ttc":
            kwargs["subfontIndex"] = int(record["face_index"])
        pdfmetrics.registerFont(ReportLabTTFont(internal_name, str(path), **kwargs))
        registered[role] = internal_name
    return registered


def _normalize_base_font(value: object) -> str:
    name = str(value or "").lstrip("/")
    return SUBSET_PREFIX.sub("", name)


def _font_resources(reader: PdfReader) -> list[dict[str, Any]]:
    resources_found: list[dict[str, Any]] = []
    for page in reader.pages:
        resources = page.get("/Resources")
        if not resources:
            continue
        fonts = resources.get_object().get("/Font")
        if not fonts:
            continue
        for font_reference in fonts.get_object().values():
            top_font = font_reference.get_object()
            candidates = [top_font]
            descendants = top_font.get("/DescendantFonts")
            if descendants:
                candidates.extend(item.get_object() for item in descendants)

            embedded = False
            to_unicode = "/ToUnicode" in top_font
            base_font = top_font.get("/BaseFont")
            for candidate in candidates:
                base_font = candidate.get("/BaseFont") or base_font
                to_unicode = to_unicode or "/ToUnicode" in candidate
                descriptor = candidate.get("/FontDescriptor")
                if not descriptor:
                    continue
                descriptor = descriptor.get_object()
                file_keys = ("/FontFile", "/FontFile2", "/FontFile3")
                embedded = embedded or any(key in descriptor for key in file_keys)

            resources_found.append(
                {
                    "base_font": _normalize_base_font(base_font),
                    "embedded": embedded,
                    "to_unicode": to_unicode,
                }
            )
    return resources_found


def _normalize_extracted_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [line.rstrip() for line in normalized.split("\n") if line.rstrip()]


def _fonttools_width(record: dict[str, Any], text: str, size: float) -> float:
    path = ROOT / str(record["path"])
    collection: TTCollection | None = None
    if path.suffix.casefold() == ".ttc":
        collection = TTCollection(path, lazy=False)
        font = collection.fonts[int(record["face_index"])]
    else:
        font = FontToolsTTFont(path, lazy=False)
    try:
        cmap = font.getBestCmap() or {}
        metrics = font["hmtx"].metrics
        units_per_em = font["head"].unitsPerEm
        advance_units = 0
        for character in text:
            glyph_name = cmap.get(ord(character))
            if glyph_name is None:
                raise ValueError(
                    f"probe glyph U+{ord(character):04X} is absent from {path.name}"
                )
            advance_units += metrics[glyph_name][0]
        return advance_units * size / units_per_em
    finally:
        if collection is not None:
            collection.close()
        else:
            font.close()


def _render_ratios(
    pdf_path: Path,
    placements: dict[str, dict[str, float]],
) -> tuple[float, dict[str, float]]:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[0]
        try:
            scale = 2.0
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil().convert("L")
                pixels = list(image.get_flattened_data())
                whole_ratio = sum(pixel < 250 for pixel in pixels) / len(pixels)
                ratios: dict[str, float] = {}
                page_height = A4[1]
                for role, placement in placements.items():
                    size = placement["size"]
                    left = max(0, int((placement["x"] - 2) * scale))
                    right = min(
                        image.width,
                        int((placement["x"] + placement["width"] + 2) * scale),
                    )
                    top = max(
                        0,
                        int((page_height - placement["y"] - size * 1.2) * scale),
                    )
                    bottom = min(
                        image.height,
                        int((page_height - placement["y"] + size * 0.4) * scale),
                    )
                    crop = image.crop((left, top, right, bottom))
                    crop_pixels = list(crop.get_flattened_data())
                    ratios[role] = sum(pixel < 250 for pixel in crop_pixels) / len(
                        crop_pixels
                    )
                return whole_ratio, ratios
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def probe_fonts(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    """Generate and inspect a deterministic one-page font probe PDF."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    font_hashes = _validate_manifest_fonts(manifest)
    registered = _register_fonts(manifest)
    by_role = {record["role"]: record for record in manifest["fonts"]}
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdf = canvas.Canvas(
        str(output_path),
        pagesize=A4,
        invariant=PROBE_CONTRACT["invariant"],
        pageCompression=PROBE_CONTRACT["page_compression"],
    )
    pdf.setAuthor("academic-pdf-en-zh-reader contributors")
    pdf.setCreator("academic-pdf-en-zh-reader font gate")
    pdf.setTitle("Deterministic CJK font embedding probe")
    pdf.setSubject("Internal Gate G0 artifact")

    page_width, page_height = A4
    pdf.setFillColorRGB(0.08, 0.08, 0.08)
    y = page_height - PROBE_CONTRACT["top"]
    placements: dict[str, dict[str, float]] = {}
    width_deltas: list[float] = []
    for role, probe_text, size in PROBE_LINES:
        font_name = registered[role]
        pdf.setFont(font_name, size)
        reportlab_width = pdfmetrics.stringWidth(probe_text, font_name, size)
        independent_width = _fonttools_width(by_role[role], probe_text, size)
        width_deltas.append(abs(reportlab_width - independent_width))
        if reportlab_width >= page_width - 144:
            raise ValueError(f"probe line is too wide for the page: {role}")
        pdf.drawString(PROBE_CONTRACT["left"], y, probe_text)
        placements[role] = {
            "x": PROBE_CONTRACT["left"],
            "y": y,
            "size": size,
            "width": reportlab_width,
        }
        y -= size * PROBE_CONTRACT["line_gap_factor"]
    pdf.showPage()
    pdf.save()

    reader = PdfReader(output_path)
    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    expected_lines = [text for _, text, _ in PROBE_LINES]
    extracted_text_matches = (
        _normalize_extracted_lines(extracted_text) == expected_lines
    )
    font_resources = _font_resources(reader)
    approved_names = {
        str(record["role"]): str(record["postscript_name"])
        for record in manifest["fonts"]
    }
    embedded_roles = {
        role: any(
            item["base_font"] == postscript_name and item["embedded"]
            for item in font_resources
        )
        for role, postscript_name in approved_names.items()
    }
    to_unicode_roles = {
        role: any(
            item["base_font"] == postscript_name and item["to_unicode"]
            for item in font_resources
        )
        for role, postscript_name in approved_names.items()
    }
    approved_postscript_names = set(approved_names.values())
    used_unapproved_fonts = sorted(
        {
            item["base_font"]
            for item in font_resources
            if item["embedded"] and item["base_font"] not in approved_postscript_names
        }
    )
    whole_ratio, ratios_by_role = _render_ratios(output_path, placements)
    max_width_delta = max(width_deltas, default=0.0)

    errors: list[str] = []
    if len(reader.pages) != 1:
        errors.append("page_count")
    if not all(embedded_roles.values()):
        errors.append("embedded_roles")
    if not all(to_unicode_roles.values()):
        errors.append("to_unicode_roles")
    if not extracted_text_matches:
        errors.append("extracted_text")
    if whole_ratio <= 0.001 or not all(
        ratio > 0.001 for ratio in ratios_by_role.values()
    ):
        errors.append("rendered_ink")
    if max_width_delta > 0.01:
        errors.append("width_measurement")
    if used_unapproved_fonts:
        errors.append("unapproved_fonts")

    return {
        "passed": not errors,
        "errors": errors,
        "page_count": len(reader.pages),
        "embedded_font_count": sum(item["embedded"] for item in font_resources),
        "embedded_roles": embedded_roles,
        "to_unicode_roles": to_unicode_roles,
        "used_unapproved_fonts": used_unapproved_fonts,
        "extracted_text_matches": extracted_text_matches,
        "render_nonwhite_ratio": whole_ratio,
        "render_nonwhite_ratio_by_role": ratios_by_role,
        "width_measurements_match": max_width_delta <= 0.01,
        "max_width_delta_pt": max_width_delta,
        "font_hashes": font_hashes,
        "probe_contract_hash": _canonical_hash(PROBE_CONTRACT),
        "expected_text_sha256": hashlib.sha256(
            "\n".join(expected_lines).encode("utf-8")
        ).hexdigest(),
        "pdf_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "reportlab_version": reportlab.Version,
        "pypdf_version": pypdf.__version__,
        "pypdfium2_version": str(pdfium_version.PYPDFIUM_INFO),
        "pdfium_version": str(pdfium_version.PDFIUM_INFO),
    }


def _update_manifest(manifest_path: Path, result: dict[str, Any]) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["embedding_probe"] = {
        "status": "passed" if result["passed"] else "failed",
        **{key: value for key, value in result.items() if key != "passed"},
    }
    _atomic_write_json(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "assets" / "font-manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "pdfs" / "font-gate" / "font-probe.pdf",
    )
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    output_path = args.output.resolve()
    result = probe_fonts(manifest_path, output_path)
    _update_manifest(manifest_path, result)
    if not result["passed"]:
        details = json.dumps(result, ensure_ascii=False)
        raise SystemExit(f"Font Gate G0 failed: {details}")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
