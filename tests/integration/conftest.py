# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from academic_pdf_en_zh_reader.annotations.figure_notes import FigureNoteCandidate
from academic_pdf_en_zh_reader.annotations.orange_candidates import TeachingCandidate
from academic_pdf_en_zh_reader.annotations.selection import (
    freeze_orange_candidate_set,
    orange_selection_policy_payload,
)
from academic_pdf_en_zh_reader.extraction.unit_mapping import validate_unit_mapping
from academic_pdf_en_zh_reader.job.finalize import (
    _font_fingerprint_payload,
    _style_contract_payload,
    finalize_annotations_and_layout,
)
from academic_pdf_en_zh_reader.job.hashing import (
    sha256_canonical,
    stable_source_id,
)
from academic_pdf_en_zh_reader.layout.annotation_adapter import (
    DEFAULT_ANNOTATION_ADAPTER_LIMITS,
)
from academic_pdf_en_zh_reader.layout.frame_graph import DEFAULT_FRAME_GRAPH_CONFIG
from academic_pdf_en_zh_reader.layout.solver import DEFAULT_LAYOUT_LIMITS
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from academic_pdf_en_zh_reader.review.review_validation import (
    validate_independent_review,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.topology.contracts import validate_source_topology
from academic_pdf_en_zh_reader.typography.font_registry import load_font_registry
from academic_pdf_en_zh_reader.typography.font_runs import FontRunResolver
from academic_pdf_en_zh_reader.typography.style_contract import (
    FontSizeSample,
    build_style_contract,
)
from scripts.generate_synthetic_fixtures import generate_fixture, load_fixture_spec

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"
INTEGRATION_SPECS = ROOT / "tests" / "integration" / "fixtures"
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
MM_TO_MPT = 72_000 / 25.4
A4_MEDIA_BOX_MPT = [0, 0, 595_276, 841_890]

_ROLE_MAP = {
    "title": "title",
    "abstract_heading": "heading",
    "abstract": "abstract",
    "keywords": "keywords",
    "section_heading": "heading",
    "body": "body",
    "figure_caption": "figure-caption",
    "table_caption": "table-caption",
    "author": "author",
}
_EXCLUDED_ROLES = {"author"}
_GRAPHIC_ROLES = {"figure", "table"}


@dataclass(frozen=True)
class IntegrationCase:
    source_pdf_path: Path
    source: dict[str, object]
    units: dict[str, object]
    translation: dict[str, object]
    review: dict[str, object]
    annotations: dict[str, object]
    frame_graph: dict[str, object]
    layout: dict[str, object]
    receipt: dict[str, object]
    policy_inputs: dict[str, object]
    overlay_plan: dict[str, object]


def _mpt(value: float | int) -> int:
    return round(float(value) * MM_TO_MPT)


def _box_mpt(values: list[float | int]) -> list[int]:
    return [_mpt(value) for value in values]


def _translation_for(role: str, source_text: str) -> str:
    if role == "title":
        return "合成论文传感器校准研究"
    if role == "abstract":
        return (
            "本研究在固定实验条件下评估完全合成的纸基传感器；"
            "所有数据仅用于确定性排版测试。"
        )
    if role == "keywords":
        return "关键词：合成夹具；校准；确定性PDF"
    if role == "heading":
        return "合成章节标题"
    if role == "figure-caption":
        return "图注的完整中文翻译。"
    if role == "table-caption":
        return "表题的完整中文翻译。"
    return f"准确保留事实、程度、逻辑与数据的中文译文：{len(source_text)}个源字符。"


def _artifact_inputs(
    spec: dict[str, Any],
    *,
    source_sha256: str,
    translation_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    overrides = translation_overrides or {}
    pages: list[dict[str, object]] = []
    unit_records: list[dict[str, object]] = []
    translation_records: list[dict[str, object]] = []
    global_order = {
        block_id: order for order, block_id in enumerate(spec["truth"]["reading_order"])
    }

    for page_spec in spec["pages"]:
        bands: list[dict[str, object]] = []
        graphic_nodes: list[dict[str, object]] = []
        blocks: list[dict[str, object]] = []
        graphics_by_column: dict[tuple[str, str], str] = {}
        char_cursor = 0

        for band_spec in page_spec["bands"]:
            bx0, by0, bx1, by1 = _box_mpt(band_spec["bbox_mm"])
            columns: list[dict[str, object]] = []
            for column_spec in band_spec["columns"]:
                cx0, _cy0, cx1, _cy1 = _box_mpt(column_spec["bbox_mm"])
                columns.append(
                    {
                        "id": column_spec["id"],
                        "x_left_mpt": cx0,
                        "x_right_mpt": cx1,
                    }
                )
                for block_spec in column_spec["blocks"]:
                    if block_spec["role"] not in _GRAPHIC_ROLES:
                        continue
                    graphic_nodes.append(
                        {
                            "id": block_spec["id"],
                            "kind": block_spec["role"],
                            "bbox_mpt": _box_mpt(block_spec["bbox_mm"]),
                            "confidence_ppm": 1_000_000,
                        }
                    )
                    graphics_by_column[(column_spec["id"], block_spec["role"])] = (
                        block_spec["id"]
                    )
            bands.append(
                {
                    "id": band_spec["id"],
                    "y_top_mpt": by1,
                    "y_bottom_mpt": by0,
                    "columns": columns,
                }
            )

        for band_spec in page_spec["bands"]:
            for column_spec in band_spec["columns"]:
                for block_spec in column_spec["blocks"]:
                    raw_role = block_spec["role"]
                    if raw_role in _GRAPHIC_ROLES:
                        continue
                    role = _ROLE_MAP[raw_role]
                    text = " ".join(block_spec["lines"])
                    start = char_cursor
                    end = start + len(text)
                    char_cursor = end + 1
                    bbox = _box_mpt(block_spec["bbox_mm"])
                    first_line_height = min(12_000, bbox[3] - bbox[1])
                    stable_id = stable_source_id(
                        page_number=page_spec["page_number"],
                        reading_order=global_order[block_spec["id"]],
                        role=role,
                        source_char_start=start,
                        source_char_end=end,
                    )
                    block: dict[str, object] = {
                        "id": stable_id,
                        "role": role,
                        "translation_policy": (
                            "excluded" if raw_role in _EXCLUDED_ROLES else "required"
                        ),
                        "band_id": band_spec["id"],
                        "column_id": column_spec["id"],
                        "reading_order": global_order[block_spec["id"]],
                        "source_char_start": start,
                        "source_char_end": end,
                        "text": text,
                        "bbox_mpt": bbox,
                        "first_line_bbox_mpt": [
                            bbox[0],
                            bbox[3] - first_line_height,
                            bbox[2],
                            bbox[3],
                        ],
                        "confidence_ppm": 1_000_000,
                    }
                    if role in {"figure-caption", "table-caption"}:
                        kind = "figure" if role == "figure-caption" else "table"
                        block["target_graphic_id"] = graphics_by_column[
                            (column_spec["id"], kind)
                        ]
                    blocks.append(block)
                    if block["translation_policy"] == "excluded":
                        continue
                    unit = {
                        "id": block["id"],
                        "role": role,
                        "reading_order": block["reading_order"],
                        "source_text": text,
                        "confidence_ppm": 1_000_000,
                        "fragments": [
                            {
                                "page_number": page_spec["page_number"],
                                "block_id": block["id"],
                                "source_char_start": start,
                                "source_char_end": end,
                            }
                        ],
                    }
                    unit_records.append(unit)
                    translation_records.append(
                        {
                            "unit_id": block["id"],
                            "chinese_text": overrides.get(
                                block_spec["id"], _translation_for(role, text)
                            ),
                            "spans": [],
                            "terminology": [],
                        }
                    )

        pages.append(
            {
                "page_number": page_spec["page_number"],
                "media_box_mpt": list(A4_MEDIA_BOX_MPT),
                "crop_box_mpt": list(A4_MEDIA_BOX_MPT),
                "rotation_degrees": 0,
                "bands": bands,
                "graphic_nodes": graphic_nodes,
                "blocks": blocks,
            }
        )

    source: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": source_sha256,
        "pages": pages,
    }
    units: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": source_sha256,
        "units": unit_records,
    }
    translation: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "translation",
        "units_hash": sha256_canonical(units),
        "translator_id": "integration-translator",
        "translation_revision": 1,
        "units": translation_records,
    }
    review: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "review",
        "translation_hash": sha256_canonical(translation),
        "reviewer_role": "independent",
        "translator_id": "integration-translator",
        "reviewer_id": "integration-independent-reviewer",
        "reviewed_unit_ids": [unit["id"] for unit in unit_records],
        "issues": [],
        "final_status": "passed",
    }
    validate_artifact("source", source)
    validate_source_topology(source)
    validate_artifact("units", units)
    validate_unit_mapping(source, units)
    validate_translation_artifact(units, translation)
    validate_independent_review(translation, review)
    return source, units, translation, review


def policy_inputs_for(style_contract, resolver) -> dict[str, object]:
    return {
        "style-contract": _style_contract_payload(style_contract),
        "font-fingerprint": _font_fingerprint_payload(resolver),
        "frame-graph-config": asdict(DEFAULT_FRAME_GRAPH_CONFIG),
        "layout-limits": asdict(DEFAULT_LAYOUT_LIMITS),
        "annotation-adapter-limits": asdict(DEFAULT_ANNOTATION_ADAPTER_LIMITS),
        "orange-selection-policy": orange_selection_policy_payload(),
    }


def build_case(
    tmp_path: Path,
    spec_name: str,
    *,
    translation_overrides: dict[str, str] | None = None,
    candidate_factory: Callable[
        [dict[str, object], dict[str, object], dict[str, object]],
        tuple[Sequence[FigureNoteCandidate], Sequence[TeachingCandidate]],
    ]
    | None = None,
) -> IntegrationCase:
    spec_path = SPECS / f"{spec_name}.json"
    if not spec_path.exists():
        spec_path = INTEGRATION_SPECS / f"{spec_name}.json"
    source_pdf_path = tmp_path / f"{spec_name}.source.pdf"
    generation = generate_fixture(spec_path, source_pdf_path)
    spec = load_fixture_spec(spec_path)
    source, units, translation, review = _artifact_inputs(
        spec,
        source_sha256=generation.sha256,
        translation_overrides=translation_overrides,
    )
    style_contract = build_style_contract((FontSizeSample(10_000, 100),))
    resolver = FontRunResolver(load_font_registry())
    figure_candidates: Sequence[FigureNoteCandidate] = ()
    teaching_candidates: Sequence[TeachingCandidate] = ()
    if candidate_factory is not None:
        figure_candidates, teaching_candidates = candidate_factory(
            source, units, translation
        )
    candidate_set = freeze_orange_candidate_set(
        units,
        translation,
        figure_candidates=figure_candidates,
        teaching_candidates=teaching_candidates,
    )
    finalized = finalize_annotations_and_layout(
        source,
        units,
        translation,
        review,
        candidate_set=candidate_set,
        style_contract=style_contract,
        resolver=resolver,
    )
    overlay_plan = build_overlay_plan(
        source,
        finalized.frame_graph,
        finalized.layout,
        finalized.annotations,
    )
    return IntegrationCase(
        source_pdf_path=source_pdf_path,
        source=source,
        units=units,
        translation=translation,
        review=review,
        annotations=finalized.annotations,
        frame_graph=finalized.frame_graph,
        layout=finalized.layout,
        receipt=finalized.receipt,
        policy_inputs=policy_inputs_for(style_contract, resolver),
        overlay_plan=overlay_plan,
    )
