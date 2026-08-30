# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Generate deterministic, project-authored CC0 academic PDF fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPECS_DIR = ROOT / "tests" / "fixtures-synthetic" / "specs"
FONT_MANIFEST_PATH = ROOT / "assets" / "font-manifest.json"
FONT_DIR = (ROOT / "assets" / "fonts").resolve()
MM_TO_PT = 72.0 / 25.4
FIXTURE_SCHEMA_VERSION = 1
FIXED_PDF_DATE = "D:20000101000000+00'00'"

FONT_ALIASES = {
    "body": "FixtureNotoSerifSCRegular",
    "heading": "FixtureNotoSerifSCSemiBold",
    "symbols": "FixtureNotoSansSymbols2",
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_font_manifest() -> dict[str, Any]:
    return json.loads(FONT_MANIFEST_PATH.read_text(encoding="utf-8"))


def _approved_font_records() -> dict[str, dict[str, Any]]:
    manifest = _read_font_manifest()
    if manifest.get("system_font_fallback") is not False:
        raise ValueError("font manifest must disable system font fallback")

    records: dict[str, dict[str, Any]] = {}
    for record in manifest["fonts"]:
        role = record["role"]
        if role not in FONT_ALIASES:
            continue
        path = (ROOT / record["path"]).resolve()
        if not path.is_relative_to(FONT_DIR):
            raise ValueError(f"font path is outside the approved directory: {path}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"font hash mismatch: {path}")
        records[role] = {**record, "resolved_path": path}

    if set(records) != set(FONT_ALIASES):
        raise ValueError("font manifest does not define every required fixture role")
    return records


APPROVED_FONT_PATHS = {
    record["resolved_path"] for record in _approved_font_records().values()
}


@dataclass(frozen=True)
class GenerationResult:
    """Stable facts returned by a fixture generation run."""

    fixture_id: str
    page_count: int
    sha256: str
    normalized_structure: dict[str, object]


def load_fixture_spec(spec_path: Path) -> dict[str, Any]:
    """Load and validate the small fixture contract used by later test gates."""

    spec_path = Path(spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError(f"unsupported fixture schema: {spec_path}")
    if spec.get("fixture_id") != spec_path.stem:
        raise ValueError(f"fixture_id must equal the spec filename: {spec_path}")
    if spec.get("license") != "CC0-1.0":
        raise ValueError(f"fixture must be CC0-1.0: {spec_path}")
    if any(
        spec.get(flag) is not False
        for flag in ("uses_system_fonts", "uses_randomness", "uses_current_time")
    ):
        raise ValueError(f"fixture enables a nondeterministic source: {spec_path}")
    _validate_structure(spec)
    return spec


def _validate_structure(spec: dict[str, Any]) -> None:
    band_ids: list[str] = []
    column_ids: list[str] = []
    roles: dict[str, str] = {}
    reading_order: list[str] = []

    pages = spec.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("fixture must contain at least one page")
    for expected_page_number, page in enumerate(pages, start=1):
        if page.get("page_number") != expected_page_number:
            raise ValueError("fixture page numbers must be contiguous and one-based")
        if page.get("size") != "A4" or page.get("orientation") != "portrait":
            raise ValueError("every synthetic fixture page must be A4 portrait")
        for band in page.get("bands", []):
            band_ids.append(band["id"])
            columns = band.get("columns", [])
            if band.get("column_count") != len(columns) or not columns:
                raise ValueError(f"invalid column count in {band['id']}")
            for column in columns:
                column_ids.append(column["id"])
                for block in column.get("blocks", []):
                    block_id = block["id"]
                    if block_id in roles:
                        raise ValueError(f"duplicate block id: {block_id}")
                    _validate_bbox(block["bbox_mm"], block_id)
                    roles[block_id] = block["role"]
                    reading_order.append(block_id)

    expected_truth = {
        "band_ids": band_ids,
        "column_ids": column_ids,
        "roles": roles,
        "reading_order": reading_order,
    }
    truth = spec.get("truth", {})
    for key, expected in expected_truth.items():
        if truth.get(key) != expected:
            raise ValueError(f"truth.{key} does not match the page structure")

    known_blocks = set(roles)
    for unit in truth.get("cross_boundary_units", []):
        if unit.get("kind") not in {"cross_column", "cross_page"}:
            raise ValueError(f"invalid cross-boundary kind: {unit}")
        fragments = unit.get("fragments", [])
        if len(fragments) < 2 or not set(fragments).issubset(known_blocks):
            raise ValueError(f"invalid cross-boundary fragments: {unit}")


def _validate_bbox(bbox_mm: object, block_id: str) -> None:
    if not isinstance(bbox_mm, list) or len(bbox_mm) != 4:
        raise ValueError(f"{block_id} must have a four-value bbox_mm")
    x0, y0, x1, y1 = (float(value) for value in bbox_mm)
    if not (0 <= x0 < x1 <= 210 and 0 <= y0 < y1 <= 297):
        raise ValueError(f"{block_id} bbox_mm is outside A4")


def _register_approved_fonts() -> None:
    for role, record in _approved_font_records().items():
        alias = FONT_ALIASES[role]
        pdfmetrics.registerFont(TTFont(alias, str(record["resolved_path"])))


def _font_for_role(role: str) -> tuple[str, float, float]:
    if role == "title":
        return FONT_ALIASES["heading"], 15.0, 18.0
    if role in {"abstract_heading", "section_heading"}:
        return FONT_ALIASES["heading"], 10.0, 12.0
    if role in {"figure_caption", "table_caption", "page_header"}:
        return FONT_ALIASES["body"], 7.5, 9.0
    if role == "author":
        return FONT_ALIASES["body"], 8.0, 10.0
    return FONT_ALIASES["body"], 8.5, 10.5


def _pt(value_mm: float) -> float:
    return float(value_mm) * MM_TO_PT


def _draw_text_block(canvas: Canvas, block: dict[str, Any]) -> None:
    lines = block.get("lines", [])
    if not lines:
        return
    x0, _y0, _x1, y1 = (float(value) for value in block["bbox_mm"])
    font_name, font_size, leading = _font_for_role(block["role"])
    text = canvas.beginText(_pt(x0), _pt(y1) - font_size)
    text.setFont(font_name, font_size)
    text.setLeading(leading)
    text.setFillColor(black)
    for line in lines:
        text.textLine(line)
    canvas.drawText(text)


def _draw_figure(canvas: Canvas, block: dict[str, Any]) -> None:
    x0, y0, x1, y1 = (_pt(float(value)) for value in block["bbox_mm"])
    width = x1 - x0
    height = y1 - y0
    canvas.setStrokeColor(Color(0.2, 0.2, 0.2))
    canvas.setLineWidth(0.6)
    canvas.rect(x0, y0, width, height, stroke=1, fill=0)
    canvas.line(x0 + 12, y0 + 12, x0 + 12, y1 - 10)
    canvas.line(x0 + 12, y0 + 12, x1 - 10, y0 + 12)
    points = ((0.12, 0.2), (0.33, 0.45), (0.55, 0.38), (0.78, 0.7))
    previous: tuple[float, float] | None = None
    for relative_x, relative_y in points:
        point = (
            x0 + 12 + relative_x * (width - 24),
            y0 + 12 + relative_y * (height - 24),
        )
        if previous is not None:
            canvas.line(previous[0], previous[1], point[0], point[1])
        canvas.circle(point[0], point[1], 1.8, stroke=1, fill=0)
        previous = point


def _draw_table(canvas: Canvas, block: dict[str, Any]) -> None:
    x0, y0, x1, y1 = (_pt(float(value)) for value in block["bbox_mm"])
    rows = 4
    columns = 3
    canvas.setStrokeColor(Color(0.2, 0.2, 0.2))
    canvas.setLineWidth(0.5)
    for row in range(rows + 1):
        y = y0 + (y1 - y0) * row / rows
        canvas.line(x0, y, x1, y)
    for column in range(columns + 1):
        x = x0 + (x1 - x0) * column / columns
        canvas.line(x, y0, x, y1)

    labels = (("Group", "Mean", "Range"), ("A", "4.2", "3-5"))
    canvas.setFont(FONT_ALIASES["body"], 6.5)
    for row, values in enumerate(labels):
        baseline = y1 - (row + 0.7) * (y1 - y0) / rows
        for column, value in enumerate(values):
            x = x0 + (column + 0.12) * (x1 - x0) / columns
            canvas.drawString(x, baseline, value)


def _draw_block(canvas: Canvas, block: dict[str, Any]) -> None:
    graphic = block.get("graphic")
    if graphic == "line_chart":
        _draw_figure(canvas, block)
    elif graphic == "table":
        _draw_table(canvas, block)
    elif graphic is not None:
        raise ValueError(f"unsupported synthetic graphic: {graphic}")
    _draw_text_block(canvas, block)


def _render_reportlab_pdf(spec: dict[str, Any]) -> bytes:
    _register_approved_fonts()
    buffer = BytesIO()
    canvas = Canvas(
        buffer,
        pagesize=A4,
        pageCompression=1,
        invariant=1,
        initialFontName=FONT_ALIASES["body"],
        initialFontSize=8.5,
        initialLeading=10.5,
    )
    canvas.setAuthor("academic-pdf-en-zh-reader contributors")
    canvas.setCreator("academic-pdf-en-zh-reader synthetic fixture generator")
    canvas.setTitle(spec["title"])
    canvas.setSubject("Project-authored synthetic academic paper fixture")
    canvas.setKeywords(f"fixture:{spec['fixture_id']};license:CC0-1.0")

    for page in spec["pages"]:
        canvas.setPageSize(A4)
        for band in page["bands"]:
            for column in band["columns"]:
                for block in column["blocks"]:
                    _draw_block(canvas, block)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _add_fixed_metadata_and_actions(source_pdf: bytes, spec: dict[str, Any]) -> bytes:
    reader = PdfReader(BytesIO(source_pdf))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    truth_json = _canonical_json(spec["truth"])
    writer.add_metadata(
        {
            "/Title": spec["title"],
            "/Author": "academic-pdf-en-zh-reader contributors",
            "/Creator": "academic-pdf-en-zh-reader synthetic fixture generator",
            "/Subject": "Project-authored synthetic academic paper fixture",
            "/Keywords": f"fixture:{spec['fixture_id']};license:CC0-1.0",
            "/CreationDate": FIXED_PDF_DATE,
            "/ModDate": FIXED_PDF_DATE,
            "/FixtureID": spec["fixture_id"],
            "/FixtureLicense": "CC0-1.0",
            "/FixtureTruth": truth_json,
        }
    )

    if spec.get("active_content"):
        action = DictionaryObject(
            {
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.alert('synthetic fixture');"),
            }
        )
        action_reference = writer._add_object(action)
        writer.root_object[NameObject("/OpenAction")] = action_reference
        writer.pages[0][NameObject("/AA")] = DictionaryObject(
            {NameObject("/O"): action_reference}
        )

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def generate_fixture(spec_path: Path, output_path: Path) -> GenerationResult:
    """Generate one deterministic PDF and return its normalized structure."""

    spec = load_fixture_spec(Path(spec_path))
    output_path = Path(output_path)
    pdf_bytes = _add_fixed_metadata_and_actions(_render_reportlab_pdf(spec), spec)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)

    truth_json = _canonical_json(spec["truth"])
    normalized_structure: dict[str, object] = {
        "fixture_id": spec["fixture_id"],
        "page_count": len(spec["pages"]),
        "page_size_pt": [float(A4[0]), float(A4[1])],
        "truth_sha256": hashlib.sha256(truth_json.encode("utf-8")).hexdigest(),
    }
    return GenerationResult(
        fixture_id=spec["fixture_id"],
        page_count=len(spec["pages"]),
        sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        normalized_structure=normalized_structure,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        action="append",
        type=Path,
        help="Fixture spec to generate; omit to generate all checked-in specs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "tmp" / "synthetic-fixtures",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    spec_paths = args.spec or sorted(DEFAULT_SPECS_DIR.glob("*.json"))
    results = [
        generate_fixture(path, args.output_dir / f"{path.stem}.pdf")
        for path in spec_paths
    ]
    print(
        _canonical_json(
            [
                {
                    "fixture_id": result.fixture_id,
                    "page_count": result.page_count,
                    "sha256": result.sha256,
                }
                for result in results
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
