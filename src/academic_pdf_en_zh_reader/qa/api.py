# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Single fail-closed API for the fixed mechanical QA gate set."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.qa.fonts import (
    validate_draw_run_fonts,
    validate_embedded_fonts,
    validate_glyph_coverage,
)
from academic_pdf_en_zh_reader.qa.geometry import (
    validate_a3_pages,
    validate_bounds_and_overlap,
    validate_continuation_and_sizes,
    validate_reading_frames,
    validate_source_left_one_to_one,
)
from academic_pdf_en_zh_reader.qa.page_contract import (
    source_manifest_pages,
    source_plan_pages,
)
from academic_pdf_en_zh_reader.qa.pdf_structure import (
    validate_no_active_content,
    validate_no_new_page_rasters,
)
from academic_pdf_en_zh_reader.qa.raster_compare import (
    RasterAudit,
    RasterPolicy,
    audit_full_page_rasters,
)
from academic_pdf_en_zh_reader.qa.semantic import (
    validate_annotation_policy,
)
from academic_pdf_en_zh_reader.rendering.contracts import OverlayPlanLimits
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from academic_pdf_en_zh_reader.review.review_validation import (
    validate_review,
)
from academic_pdf_en_zh_reader.review.translation_validation import (
    validate_translation_artifact,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.typography.font_registry import (
    DEFAULT_FONT_MANIFEST,
    load_font_registry,
)

_ARTIFACT_NAMES = (
    "source",
    "units",
    "translation",
    "review",
    "annotations",
    "frame-graph",
    "layout",
)
_POLICY_NAMES = (
    "style-contract",
    "font-fingerprint",
    "frame-graph-config",
    "layout-limits",
    "annotation-adapter-limits",
    "orange-selection-policy",
)
_GATES = (
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
FIXED_GATE_IDS = tuple(identifier for identifier, _category in _GATES)


class QaParentError(ValueError):
    """A stable failure of the externally trusted immutable parent chain."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class QaConfig:
    """Versioned QA policy; 144 DPI and the fixed gates are not configurable."""

    version: int = 1
    raster: RasterPolicy = field(default_factory=RasterPolicy)

    def __post_init__(self) -> None:
        if self.version != 1 or not isinstance(self.raster, RasterPolicy):
            raise ValueError("QA_CONFIG_INVALID")

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_QA_CONFIG = QaConfig()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise QaParentError("PARENT_FILE_UNREADABLE") from exc
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_parent_chain(
    *,
    source_pdf_path: Path,
    output_pdf_path: Path,
    artifacts: Mapping[str, Mapping[str, object]],
    finalization_receipt: Mapping[str, object],
    policy_inputs: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    render_manifest: Mapping[str, object],
    expected_render_manifest_hash: str,
    font_manifest_path: Path,
) -> None:
    actual_manifest_hash = sha256_canonical(render_manifest)
    if (
        not _is_sha256(expected_render_manifest_hash)
        or actual_manifest_hash != expected_render_manifest_hash
    ):
        raise QaParentError("PARENT_MANIFEST_TRUST_MISMATCH")
    try:
        for name, artifact in artifacts.items():
            validate_artifact(name, artifact)
        validate_artifact("finalization-receipt", finalization_receipt)
        validate_artifact("render-manifest", render_manifest)
    except Exception as exc:
        raise QaParentError("PARENT_SCHEMA_INVALID") from exc
    if set(artifacts) != set(_ARTIFACT_NAMES) or set(policy_inputs) != set(
        _POLICY_NAMES
    ):
        raise QaParentError("PARENT_SET_INVALID")

    receipt_hash = sha256_canonical(finalization_receipt)
    expected_artifact_hashes = {
        name: sha256_canonical(artifacts[name]) for name in _ARTIFACT_NAMES
    }
    expected_policy_hashes = {
        name: sha256_canonical(policy_inputs[name]) for name in _POLICY_NAMES
    }
    try:
        receipt_valid = (
            finalization_receipt["artifact_hashes"] == expected_artifact_hashes
            and finalization_receipt["policy_hashes"] == expected_policy_hashes
            and render_manifest["finalization_receipt_hash"] == receipt_hash
            and render_manifest["source_artifact_hash"]
            == expected_artifact_hashes["source"]
            and render_manifest["annotations_hash"]
            == expected_artifact_hashes["annotations"]
            and render_manifest["frame_graph_hash"]
            == expected_artifact_hashes["frame-graph"]
            and render_manifest["layout_hash"] == expected_artifact_hashes["layout"]
            and render_manifest["annotation_selection_hash"]
            == artifacts["annotations"]["orange_selection_hash"]
        )
    except (KeyError, TypeError) as exc:
        raise QaParentError("PARENT_RECEIPT_MISMATCH") from exc
    if not receipt_valid:
        raise QaParentError("PARENT_RECEIPT_MISMATCH")

    normalized_pdf_sha = _sha256_path(source_pdf_path)
    output_sha = _sha256_path(output_pdf_path)
    raw_source_sha = artifacts["source"].get("source_sha256")
    declared_normalized_sha = artifacts["source"].get("normalized_pdf_sha256")
    if (
        raw_source_sha != artifacts["units"].get("source_sha256")
        or raw_source_sha != render_manifest.get("source_sha256")
        or declared_normalized_sha != artifacts["units"].get("normalized_pdf_sha256")
        or normalized_pdf_sha != declared_normalized_sha
        or normalized_pdf_sha != render_manifest.get("normalized_pdf_sha256")
        or output_sha != render_manifest.get("output_pdf_sha256")
    ):
        raise QaParentError("PARENT_PDF_HASH_MISMATCH")

    try:
        registry = load_font_registry(font_manifest_path)
        fingerprint = [
            {
                "role": face.role,
                "reportlab_name": face.reportlab_name,
                "sha256": face.sha256,
            }
            for face in registry.faces
        ]
        limits = OverlayPlanLimits(**render_manifest["overlay_plan_limits"])
        expected_plan = build_overlay_plan(
            artifacts["source"],
            artifacts["frame-graph"],
            artifacts["layout"],
            artifacts["annotations"],
            limits=limits,
        )
    except Exception as exc:
        raise QaParentError("PARENT_PLAN_RECOMPUTE_FAILED") from exc
    if (
        canonical_json_bytes(expected_plan) != canonical_json_bytes(overlay_plan)
        or render_manifest.get("overlay_plan_hash")
        != expected_plan.get("overlay_plan_hash")
        or render_manifest.get("render_style") != expected_plan.get("render_style")
        or render_manifest.get("font_fingerprint") != fingerprint
        or artifacts["frame-graph"].get("font_fingerprint") != fingerprint
        or policy_inputs.get("font-fingerprint") != fingerprint
    ):
        raise QaParentError("PARENT_PLAN_MISMATCH")
    try:
        manifest_pages = render_manifest["pages"]
        plan_pages = expected_plan["pages"]
        source_plan_pages(artifacts["layout"], expected_plan)
        source_manifest_pages(manifest_pages)
        if len(manifest_pages) != len(plan_pages) or any(
            manifest_page["overlay_page_plan_hash"] != plan_page["page_plan_hash"]
            or manifest_page["output_page_number"] != plan_page["page_number"]
            or manifest_page["source_page_number"] != plan_page["source_page_number"]
            or manifest_page["page_kind"] != plan_page["page_kind"]
            or manifest_page["continuation_index"] != plan_page["continuation_index"]
            or manifest_page["continuation_label_present"]
            != (plan_page["continuation_label"] is not None)
            for manifest_page, plan_page in zip(manifest_pages, plan_pages, strict=True)
        ):
            raise QaParentError("PARENT_PLAN_MISMATCH")
    except (KeyError, TypeError, ValueError) as exc:
        raise QaParentError("PARENT_PLAN_MISMATCH") from exc


def _check(
    identifier: str,
    category: str,
    callback: Callable[[], object],
    *,
    fallback_code: str,
) -> dict[str, object]:
    try:
        callback()
    except Exception as exc:
        code = getattr(exc, "code", fallback_code)
        if (
            not isinstance(code, str)
            or not code
            or any(
                character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
                for character in code
            )
        ):
            code = fallback_code
        return {
            "id": identifier,
            "category": category,
            "hard_gate": True,
            "passed": False,
            "details": code,
        }
    return {
        "id": identifier,
        "category": category,
        "hard_gate": True,
        "passed": True,
        "details": "PASS",
    }


def _skipped(identifier: str, category: str) -> dict[str, object]:
    return {
        "id": identifier,
        "category": category,
        "hard_gate": True,
        "passed": False,
        "details": "PARENT_CHAIN_REQUIRED",
    }


def run_mechanical_qa(
    *,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    finalization_receipt: Mapping[str, object],
    policy_inputs: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    render_manifest: Mapping[str, object],
    expected_render_manifest_hash: str,
    font_manifest_path: str | Path = DEFAULT_FONT_MANIFEST,
    config: QaConfig = DEFAULT_QA_CONFIG,
) -> dict[str, object]:
    """Run all fixed gates and return only bounded hashes, counts, and codes."""

    if not isinstance(config, QaConfig):
        raise ValueError("QA_CONFIG_INVALID")
    source_path = Path(source_pdf_path)
    output_path = Path(output_pdf_path)
    artifacts = {
        "source": source,
        "units": units,
        "translation": translation,
        "review": review,
        "annotations": annotations,
        "frame-graph": frame_graph,
        "layout": layout,
    }
    parent = _check(
        _GATES[0][0],
        _GATES[0][1],
        lambda: _validate_parent_chain(
            source_pdf_path=source_path,
            output_pdf_path=output_path,
            artifacts=artifacts,
            finalization_receipt=finalization_receipt,
            policy_inputs=policy_inputs,
            overlay_plan=overlay_plan,
            render_manifest=render_manifest,
            expected_render_manifest_hash=expected_render_manifest_hash,
            font_manifest_path=Path(font_manifest_path),
        ),
        fallback_code="PARENT_CHAIN_INVALID",
    )
    if not parent["passed"]:
        checks = [parent, *(_skipped(*gate) for gate in _GATES[1:])]
        rasterized_pages = 0
    else:
        try:
            output_reader = PdfReader(output_path, strict=True)
            source_reader = PdfReader(source_path, strict=True)
        except Exception:
            output_reader = None
            source_reader = None
        checks = [parent]
        callbacks: tuple[tuple[Callable[[], object], str], ...] = (
            (
                lambda: validate_translation_artifact(units, translation),
                "SEMANTIC_TRANSLATION_INVALID",
            ),
            (
                lambda: validate_review(translation, review),
                "SEMANTIC_REVIEW_INVALID",
            ),
            (
                lambda: validate_annotation_policy(
                    units, translation, review, annotations, frame_graph, overlay_plan
                ),
                "ANNOTATION_POLICY_INVALID",
            ),
            (
                lambda: validate_a3_pages(output_reader, render_manifest),  # type: ignore[arg-type]
                "GEOMETRY_A3_INVALID",
            ),
            (
                lambda: validate_source_left_one_to_one(source, render_manifest),
                "GEOMETRY_SOURCE_PLACEMENT_INVALID",
            ),
            (
                lambda: validate_reading_frames(source, frame_graph, layout),
                "GEOMETRY_READING_FRAMES_INVALID",
            ),
            (
                lambda: validate_bounds_and_overlap(layout, overlay_plan),
                "GEOMETRY_BOUNDS_INVALID",
            ),
            (
                lambda: validate_continuation_and_sizes(
                    frame_graph, layout, overlay_plan
                ),
                "GEOMETRY_FIXED_SIZE_INVALID",
            ),
            (
                lambda: validate_embedded_fonts(output_reader, render_manifest),  # type: ignore[arg-type]
                "FONT_EMBEDDING_INVALID",
            ),
            (
                lambda: validate_draw_run_fonts(
                    output_reader,
                    render_manifest,
                    overlay_plan,  # type: ignore[arg-type]
                ),
                "FONT_DRAW_BINDING_INVALID",
            ),
            (
                lambda: validate_glyph_coverage(
                    render_manifest,
                    overlay_plan,
                    font_manifest_path=font_manifest_path,
                ),
                "FONT_GLYPH_COVERAGE_INVALID",
            ),
            (
                lambda: validate_no_active_content(output_reader),  # type: ignore[arg-type]
                "PDF_ACTIVE_CONTENT",
            ),
            (
                lambda: validate_no_new_page_rasters(
                    source_reader,
                    output_reader,
                    render_manifest,  # type: ignore[arg-type]
                ),
                "PDF_RASTER_SUBSTITUTION",
            ),
        )
        for (identifier, category), (callback, fallback) in zip(
            _GATES[1:14], callbacks, strict=True
        ):
            checks.append(
                _check(identifier, category, callback, fallback_code=fallback)
            )

        raster_audit: RasterAudit | None = None
        raster_error: str | None = None
        try:
            raster_audit = audit_full_page_rasters(
                source_path,
                output_path,
                render_manifest,
                overlay_plan,
                policy=config.raster,
            )
        except Exception as exc:
            raster_error = getattr(exc, "code", "RASTER_PDFIUM_FAILED")
        if raster_audit is None:
            for identifier, category in _GATES[14:]:
                checks.append(
                    {
                        "id": identifier,
                        "category": category,
                        "hard_gate": True,
                        "passed": False,
                        "details": raster_error,
                    }
                )
            rasterized_pages = 0
        else:
            rasterized_pages = raster_audit.rasterized_page_count
            raster_results = (
                (
                    raster_audit.rasterized_page_count == raster_audit.page_count,
                    "RASTER_INCOMPLETE",
                ),
                (raster_audit.left_equivalent, "RASTER_LEFT_DIFFERENT"),
                (raster_audit.pages_sane, "RASTER_PAGE_SANITY_FAILED"),
            )
            for (identifier, category), (passed, code) in zip(
                _GATES[14:], raster_results, strict=True
            ):
                checks.append(
                    {
                        "id": identifier,
                        "category": category,
                        "hard_gate": True,
                        "passed": passed,
                        "details": "PASS" if passed else code,
                    }
                )

    raw_source_sha = source.get("source_sha256")
    source_pdf_sha = raw_source_sha if _is_sha256(raw_source_sha) else "0" * 64
    try:
        normalized_pdf_sha = _sha256_path(source_path)
    except QaParentError:
        normalized_pdf_sha = "0" * 64
    try:
        output_pdf_sha = _sha256_path(output_path)
    except QaParentError:
        output_pdf_sha = "0" * 64
    receipt_hash = sha256_canonical(finalization_receipt)
    manifest_hash = sha256_canonical(render_manifest)
    qa: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_kind": "qa",
        "qa_policy_version": 1,
        "render_manifest_hash": manifest_hash,
        "finalization_receipt_hash": receipt_hash,
        "source_pdf_sha256": source_pdf_sha,
        "normalized_pdf_sha256": normalized_pdf_sha,
        "output_pdf_sha256": output_pdf_sha,
        "qa_config_hash": sha256_canonical(config.to_mapping()),
        "checked_page_count": len(render_manifest.get("pages", [])),
        "rasterized_page_count": rasterized_pages,
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }
    validate_artifact("qa", qa)
    return qa


__all__ = [
    "DEFAULT_QA_CONFIG",
    "FIXED_GATE_IDS",
    "QaConfig",
    "QaParentError",
    "run_mechanical_qa",
]
