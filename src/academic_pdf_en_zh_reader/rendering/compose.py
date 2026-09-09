# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Trusted parent validation, vector A3 composition, and immutable publication."""

from __future__ import annotations

import platform
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

import pypdf
import reportlab
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import NameObject

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.cleanup import (
    _delete_regular_file_if_hash_matches,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    write_immutable_artifact,
    write_immutable_bytes,
)
from academic_pdf_en_zh_reader.preflight.pdf_catalog import (
    CatalogLimits,
    inspect_catalog,
)
from academic_pdf_en_zh_reader.qa.image_fingerprints import (
    ImageFingerprintError,
    page_image_fingerprints,
)
from academic_pdf_en_zh_reader.rendering.contracts import (
    DEFAULT_OVERLAY_PLAN_LIMITS,
    OverlayPlanError,
    OverlayPlanLimits,
)
from academic_pdf_en_zh_reader.rendering.metadata import (
    final_pdf_metadata,
    metadata_policy,
    render_input_hash_payload,
)
from academic_pdf_en_zh_reader.rendering.overlay import (
    OverlayRenderError,
    _render_validated_overlay_pdf,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    PageGeometry,
    PageGeometryError,
    displayed_crop_relative_boxes,
    inspect_a4_page_geometry,
)
from academic_pdf_en_zh_reader.rendering.vector_compose import _prepare_page
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.typography.font_registry import (
    DEFAULT_FONT_MANIFEST,
    PROJECT_ROOT,
    FontRegistryError,
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
_ACTIVE_ROOT_KEYS = ("/OpenAction", "/AA", "/AcroForm")
_CATALOG_LIMITS = CatalogLimits(
    max_objects=50_000,
    max_recursion_depth=64,
    max_decoded_stream_bytes=256 * 1024 * 1024,
    max_image_bytes=512 * 1024 * 1024,
)


class CompositionError(ValueError):
    """Stable composition failure that never includes paper text."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CompositionResult:
    """Hashes of one immutable render result."""

    output_pdf_sha256: str
    render_manifest_hash: str
    overlay_pdf_sha256: str
    page_count: int


@dataclass(frozen=True, slots=True)
class _PreparedSourcePage:
    page: object
    translate_x: float
    translate_y: float
    normalized_visible_box_mpt: tuple[int, int, int, int]


def _mapping(value: object, *, code: str, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CompositionError(code, f"{label} must be an object")
    return value


def _validate_job_paths(
    job_root: Path,
    source_pdf_path: Path,
    output_pdf_path: Path,
    render_manifest_path: Path,
) -> None:
    try:
        root = job_root.resolve(strict=True)
        source = source_pdf_path.resolve(strict=True)
        output_parent = output_pdf_path.parent.resolve(strict=True)
        manifest_parent = render_manifest_path.parent.resolve(strict=True)
    except OSError as exc:
        raise CompositionError("RENDER_PATH_INVALID", "render path is invalid") from exc
    if (
        not root.is_dir()
        or root.is_symlink()
        or output_parent != root
        or manifest_parent != root
        or output_pdf_path.name in {"", ".", ".."}
        or render_manifest_path.name in {"", ".", ".."}
        or output_pdf_path == render_manifest_path
        or source in {output_pdf_path.resolve(), render_manifest_path.resolve()}
    ):
        raise CompositionError(
            "RENDER_PATH_INVALID",
            "outputs must be distinct direct children of the job directory",
        )
    if output_pdf_path.exists() or render_manifest_path.exists():
        raise CompositionError("RENDER_OUTPUT_EXISTS", "render output already exists")


def _validate_receipt(
    artifacts: Mapping[str, Mapping[str, object]],
    receipt: Mapping[str, object],
    policy_inputs: Mapping[str, object],
) -> None:
    try:
        for name, artifact in artifacts.items():
            validate_artifact(name, artifact)
        validate_artifact("finalization-receipt", receipt)
    except (SchemaValidationError, KeyError, TypeError) as exc:
        raise CompositionError(
            "FINALIZATION_RECEIPT_MISMATCH",
            "finalization receipt or parent artifact is invalid",
        ) from exc
    if set(policy_inputs) != set(_POLICY_NAMES):
        raise CompositionError(
            "FINALIZATION_RECEIPT_MISMATCH",
            "finalization policy inputs are incomplete",
        )
    expected_artifacts = {
        name: sha256_canonical(artifacts[name]) for name in _ARTIFACT_NAMES
    }
    expected_policies = {
        name: sha256_canonical(policy_inputs[name]) for name in _POLICY_NAMES
    }
    annotations = artifacts["annotations"]
    layout = artifacts["layout"]
    try:
        valid = (
            receipt["artifact_hashes"] == expected_artifacts
            and receipt["policy_hashes"] == expected_policies
            and receipt["candidate_set_hash"] == annotations["candidate_set_hash"]
            and receipt["selection_hash"] == annotations["orange_selection_hash"]
            and receipt["solver_input_hash"] == layout["solver_input_hash"]
            and receipt["continuation_page_count"]
            == layout["solver_trace"]["continuation_page_count"]
        )
    except (KeyError, TypeError) as exc:
        raise CompositionError(
            "FINALIZATION_RECEIPT_MISMATCH",
            "finalization receipt does not bind all parents",
        ) from exc
    if not valid:
        raise CompositionError(
            "FINALIZATION_RECEIPT_MISMATCH",
            "finalization receipt differs from supplied parents",
        )


def _validate_source_pdf(
    source_pdf_path: Path,
    source: Mapping[str, object],
) -> tuple[bytes, PdfReader, tuple[PageGeometry, ...]]:
    try:
        source_bytes = source_pdf_path.read_bytes()
    except OSError as exc:
        raise CompositionError(
            "SOURCE_PDF_INVALID", "source PDF cannot be read"
        ) from exc
    if sha256_bytes(source_bytes) != source.get("normalized_pdf_sha256"):
        raise CompositionError(
            "SOURCE_PDF_HASH_MISMATCH",
            "normalized source PDF bytes differ from the source artifact",
        )
    try:
        reader = PdfReader(BytesIO(source_bytes), strict=True)
        if reader.is_encrypted or not reader.pages:
            raise CompositionError(
                "SOURCE_PDF_INVALID",
                "source PDF is encrypted or empty",
            )
        catalog = inspect_catalog(
            reader,
            _CATALOG_LIMITS,
            pages=list(reader.pages),
        )
        if catalog.errors:
            raise CompositionError(
                "SOURCE_PDF_INVALID",
                "source PDF failed bounded catalog inspection",
            )
        if any(catalog.inventory.values()):
            raise CompositionError(
                "SOURCE_ACTIVE_CONTENT",
                "source PDF contains active content",
            )
        geometries = tuple(inspect_a4_page_geometry(page) for page in reader.pages)
    except CompositionError:
        raise
    except (OSError, PageGeometryError, TypeError, ValueError) as exc:
        raise CompositionError("SOURCE_PDF_INVALID", "source PDF is invalid") from exc
    source_pages = source.get("pages")
    if not isinstance(source_pages, list) or len(source_pages) != len(geometries):
        raise CompositionError(
            "SOURCE_PDF_PARENT_MISMATCH",
            "source PDF page count differs from the source artifact",
        )
    for page_number, (record, geometry) in enumerate(
        zip(source_pages, geometries, strict=True),
        start=1,
    ):
        artifact_media, artifact_crop = displayed_crop_relative_boxes(geometry)
        if not isinstance(record, Mapping) or (
            record.get("page_number") != page_number
            or record.get("media_box_mpt") != list(artifact_media)
            or record.get("crop_box_mpt") != list(artifact_crop)
            or record.get("rotation_degrees") != geometry.rotation_degrees
        ):
            raise CompositionError(
                "SOURCE_PDF_PARENT_MISMATCH",
                "source PDF geometry differs from the source artifact",
            )
    return source_bytes, reader, geometries


def _font_contract(
    frame_graph: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    policy_inputs: Mapping[str, object],
    font_manifest_path: Path,
) -> tuple[str, list[dict[str, str]]]:
    try:
        registry = load_font_registry(font_manifest_path)
        manifest_hash = sha256_bytes(font_manifest_path.read_bytes())
        fingerprint = [
            {
                "role": face.role,
                "reportlab_name": face.reportlab_name,
                "sha256": face.sha256,
            }
            for face in registry.faces
        ]
        if (
            frame_graph.get("font_fingerprint") != fingerprint
            or overlay_plan.get("font_fingerprint") != fingerprint
            or policy_inputs.get("font-fingerprint") != fingerprint
        ):
            raise CompositionError(
                "FONT_FINGERPRINT_MISMATCH",
                "actual pinned fonts differ from frozen inputs",
            )
        font_files = [
            {
                "role": face.role,
                "path": face.path.relative_to(PROJECT_ROOT).as_posix(),
                "reportlab_name": face.reportlab_name,
                "sha256": face.sha256,
            }
            for face in registry.faces
        ]
    except CompositionError:
        raise
    except (FontRegistryError, OSError, ValueError) as exc:
        raise CompositionError(
            "FONT_FINGERPRINT_MISMATCH",
            "pinned font registry is invalid",
        ) from exc
    return manifest_hash, font_files


def _font_usages(plan: Mapping[str, object]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str, int]] = Counter()
    characters: Counter[tuple[str, str, int]] = Counter()
    for page in plan["pages"]:  # type: ignore[index]
        for run in page["draw_runs"]:  # type: ignore[index]
            key = (
                str(run["font_role"]),
                str(run["font_name"]),
                int(run["size_mpt"]),
            )
            counts[key] += 1
            characters[key] += len(str(run["text"]))
    return [
        {
            "font_role": role,
            "font_name": name,
            "size_mpt": size,
            "draw_run_count": counts[(role, name, size)],
            "character_count": characters[(role, name, size)],
        }
        for role, name, size in sorted(counts)
    ]


def _runtime_fingerprint() -> dict[str, str]:
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform": platform.system(),
    }


def _page_and_block_mappings(
    source: Mapping[str, object],
    layout: Mapping[str, object],
    plan: Mapping[str, object],
    prepared_sources: tuple[_PreparedSourcePage, ...],
    overlay_image_fingerprints: tuple[Counter[str], ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    source_pages = {
        int(page["page_number"]): page
        for page in source["pages"]  # type: ignore[index]
    }
    layout_pages = layout["pages"]  # type: ignore[index]
    pages: list[dict[str, object]] = []
    blocks: list[dict[str, object]] = []
    if len(overlay_image_fingerprints) != len(plan["pages"]):
        raise CompositionError(
            "OVERLAY_PDF_INVALID", "overlay image page count differs from plan"
        )
    for page_index, (plan_page, image_fingerprints) in enumerate(
        zip(plan["pages"], overlay_image_fingerprints, strict=True)
    ):
        if plan_page["page_kind"] == "disclaimer":
            pages.append(
                {
                    "output_page_number": plan_page["page_number"],
                    "source_page_number": None,
                    "page_kind": "disclaimer",
                    "continuation_index": 0,
                    "source_crop_box_mpt": None,
                    "source_normalized_visible_box_mpt": None,
                    "source_rotation_degrees": None,
                    "source_transform_mpt": None,
                    "overlay_page_plan_hash": plan_page["page_plan_hash"],
                    "overlay_image_fingerprints": sorted(
                        fingerprint
                        for fingerprint, count in image_fingerprints.items()
                        for _ in range(count)
                    ),
                    "continuation_label_present": False,
                }
            )
            continue
        layout_page = layout_pages[page_index]
        source_page = source_pages[int(layout_page["source_page_number"])]
        prepared = prepared_sources[int(layout_page["source_page_number"]) - 1]
        crop = source_page["crop_box_mpt"]
        transform = [
            1000,
            0,
            0,
            1000,
            round(prepared.translate_x * 1000),
            round(prepared.translate_y * 1000),
        ]
        pages.append(
            {
                "output_page_number": layout_page["page_number"],
                "source_page_number": layout_page["source_page_number"],
                "page_kind": layout_page["page_kind"],
                "continuation_index": layout_page["continuation_index"],
                "source_crop_box_mpt": list(crop),
                "source_normalized_visible_box_mpt": list(
                    prepared.normalized_visible_box_mpt
                ),
                "source_rotation_degrees": source_page["rotation_degrees"],
                "source_transform_mpt": transform,
                "overlay_page_plan_hash": plan_page["page_plan_hash"],
                "overlay_image_fingerprints": sorted(
                    fingerprint
                    for fingerprint, count in image_fingerprints.items()
                    for _ in range(count)
                ),
                "continuation_label_present": (
                    plan_page["continuation_label"] is not None
                ),
            }
        )
        for block in layout_page["blocks"]:
            blocks.append(
                {
                    "block_id": block["id"],
                    "content_id": block["content_id"],
                    "content_kind": block["content_kind"],
                    "unit_id": block["unit_id"],
                    "part_index": block["part_index"],
                    "output_page_number": layout_page["page_number"],
                    "frame_id": block["frame_id"],
                    "bbox_mpt": list(block["bbox_mpt"]),
                }
            )
    return pages, blocks


def _prepare_source_pages(
    source_reader: PdfReader,
    geometries: tuple[PageGeometry, ...],
) -> tuple[_PreparedSourcePage, ...]:
    staging_writer = PdfWriter()
    prepared: list[_PreparedSourcePage] = []
    for page, geometry in zip(source_reader.pages, geometries, strict=True):
        prepared_page, (translate_x, translate_y) = _prepare_page(
            staging_writer, page, geometry
        )
        normalized = inspect_a4_page_geometry(prepared_page)
        prepared.append(
            _PreparedSourcePage(
                page=prepared_page,
                translate_x=translate_x,
                translate_y=translate_y,
                normalized_visible_box_mpt=normalized.visible_box_mpt,
            )
        )
    return tuple(prepared)


def _compose_pdf_bytes(
    prepared_sources: tuple[_PreparedSourcePage, ...],
    overlay_pdf: bytes,
    plan: Mapping[str, object],
    *,
    render_input_hash: str,
) -> bytes:
    overlay_reader = PdfReader(BytesIO(overlay_pdf), strict=True)
    if len(overlay_reader.pages) != len(plan["pages"]):  # type: ignore[arg-type]
        raise CompositionError(
            "OVERLAY_PDF_INVALID",
            "overlay page count differs from the validated plan",
        )
    writer = PdfWriter()
    for page_index, plan_page in enumerate(plan["pages"]):  # type: ignore[index]
        destination = writer.add_blank_page(
            width=A3_LANDSCAPE_WIDTH_MPT / 1000,
            height=A3_LANDSCAPE_HEIGHT_MPT / 1000,
        )
        if plan_page["page_kind"] != "disclaimer":
            source_index = int(plan_page["source_page_number"]) - 1
            prepared = prepared_sources[source_index]
            destination.merge_transformed_page(
                prepared.page,
                Transformation().translate(prepared.translate_x, prepared.translate_y),
                expand=False,
            )
        destination.merge_page(overlay_reader.pages[page_index], expand=False)
        destination.pop(NameObject("/Annots"), None)
        destination.pop(NameObject("/AA"), None)
    writer.add_metadata(
        final_pdf_metadata(
            render_input_hash=render_input_hash,
            overlay_plan_hash=str(plan["overlay_plan_hash"]),
        )
    )
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _verify_final_pdf(final_pdf: bytes, expected_pages: int) -> None:
    try:
        reader = PdfReader(BytesIO(final_pdf), strict=True)
        valid = len(reader.pages) == expected_pages and not reader.is_encrypted
        for page in reader.pages:
            valid = valid and (
                round(float(page.mediabox.width) * 1000) == A3_LANDSCAPE_WIDTH_MPT
                and round(float(page.mediabox.height) * 1000) == A3_LANDSCAPE_HEIGHT_MPT
                and "/Annots" not in page
                and "/AA" not in page
            )
        valid = valid and not any(
            key in reader.root_object for key in (*_ACTIVE_ROOT_KEYS, "/Names")
        )
    except (OSError, TypeError, ValueError) as exc:
        raise CompositionError(
            "FINAL_PDF_SELF_CHECK_FAILED",
            "final PDF cannot be reopened",
        ) from exc
    if not valid:
        raise CompositionError(
            "FINAL_PDF_SELF_CHECK_FAILED",
            "final PDF failed structural self-check",
        )


def compose_bilingual_pdf(
    *,
    source_pdf_path: str | Path,
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
    expected_finalization_receipt_hash: str,
    expected_overlay_plan_hash: str,
    job_root: str | Path,
    output_pdf_path: str | Path,
    render_manifest_path: str | Path,
    font_manifest_path: str | Path = DEFAULT_FONT_MANIFEST,
    overlay_plan_limits: OverlayPlanLimits = DEFAULT_OVERLAY_PLAN_LIMITS,
) -> CompositionResult:
    """Validate the trusted chain, compose vectors, and publish two artifacts once."""

    source_path = Path(source_pdf_path)
    root = Path(job_root)
    output_path = Path(output_pdf_path)
    manifest_path = Path(render_manifest_path)
    _validate_job_paths(root, source_path, output_path, manifest_path)
    root = root.resolve(strict=True)
    output_path = root / output_path.name
    manifest_path = root / manifest_path.name
    artifacts = {
        "source": _mapping(source, code="ARTIFACT_INVALID", label="source"),
        "units": _mapping(units, code="ARTIFACT_INVALID", label="units"),
        "translation": _mapping(
            translation, code="ARTIFACT_INVALID", label="translation"
        ),
        "review": _mapping(review, code="ARTIFACT_INVALID", label="review"),
        "annotations": _mapping(
            annotations, code="ARTIFACT_INVALID", label="annotations"
        ),
        "frame-graph": _mapping(
            frame_graph, code="ARTIFACT_INVALID", label="frame graph"
        ),
        "layout": _mapping(layout, code="ARTIFACT_INVALID", label="layout"),
    }
    if (
        not isinstance(expected_finalization_receipt_hash, str)
        or len(expected_finalization_receipt_hash) != 64
        or sha256_canonical(finalization_receipt) != expected_finalization_receipt_hash
    ):
        raise CompositionError(
            "FINALIZATION_RECEIPT_MISMATCH",
            "finalization receipt differs from the validated job ledger",
        )
    _validate_receipt(artifacts, finalization_receipt, policy_inputs)
    source_bytes, source_reader, geometries = _validate_source_pdf(
        source_path, artifacts["source"]
    )
    if not isinstance(overlay_plan_limits, OverlayPlanLimits):
        raise CompositionError(
            "PLAN_COMPLEXITY_LIMIT",
            "overlay limits are invalid",
        )
    try:
        expected_plan = build_overlay_plan(
            artifacts["source"],
            artifacts["frame-graph"],
            artifacts["layout"],
            artifacts["annotations"],
            limits=overlay_plan_limits,
        )
    except OverlayPlanError as exc:
        raise CompositionError(exc.code, "overlay plan recomputation failed") from exc
    supplied_hash = overlay_plan.get("overlay_plan_hash")
    if (
        not isinstance(expected_overlay_plan_hash, str)
        or len(expected_overlay_plan_hash) != 64
        or expected_plan["overlay_plan_hash"] != expected_overlay_plan_hash
        or supplied_hash != expected_overlay_plan_hash
        or canonical_json_bytes(overlay_plan) != canonical_json_bytes(expected_plan)
    ):
        raise CompositionError(
            "OVERLAY_PLAN_MISMATCH",
            "overlay plan differs from trusted parent recomputation",
        )
    font_manifest_hash, font_files = _font_contract(
        artifacts["frame-graph"],
        expected_plan,
        policy_inputs,
        Path(font_manifest_path),
    )
    try:
        overlay_pdf = _render_validated_overlay_pdf(expected_plan)
        overlay_reader = PdfReader(BytesIO(overlay_pdf), strict=True)
        overlay_images = tuple(
            page_image_fingerprints(page) for page in overlay_reader.pages
        )
    except (
        ImageFingerprintError,
        OverlayRenderError,
        OverlayPlanError,
        OSError,
        ValueError,
    ) as exc:
        raise CompositionError(
            "OVERLAY_RENDER_FAILED", "overlay render failed"
        ) from exc
    overlay_pdf_sha256 = sha256_bytes(overlay_pdf)
    prepared_sources = _prepare_source_pages(source_reader, geometries)
    pages, block_mappings = _page_and_block_mappings(
        artifacts["source"],
        artifacts["layout"],
        expected_plan,
        prepared_sources,
        overlay_images,
    )
    render_style = expected_plan["render_style"]
    limits_payload = asdict(overlay_plan_limits)
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "render-manifest",
        "render_manifest_version": 2,
        "finalization_receipt_hash": expected_finalization_receipt_hash,
        "source_sha256": artifacts["source"]["source_sha256"],
        "normalized_pdf_sha256": artifacts["source"]["normalized_pdf_sha256"],
        "source_artifact_hash": sha256_canonical(artifacts["source"]),
        "annotations_hash": sha256_canonical(artifacts["annotations"]),
        "annotation_selection_hash": artifacts["annotations"]["orange_selection_hash"],
        "frame_graph_hash": sha256_canonical(artifacts["frame-graph"]),
        "layout_hash": sha256_canonical(artifacts["layout"]),
        "render_style": render_style,
        "render_style_hash": sha256_canonical(render_style),
        "font_manifest_hash": font_manifest_hash,
        "font_fingerprint": list(artifacts["frame-graph"]["font_fingerprint"]),
        "font_files": font_files,
        "font_usages": _font_usages(expected_plan),
        "overlay_plan_hash": expected_overlay_plan_hash,
        "overlay_plan_limits": limits_payload,
        "overlay_plan_limits_hash": sha256_canonical(limits_payload),
        "overlay_pdf_sha256": overlay_pdf_sha256,
        "dependencies": {
            "pypdf": pypdf.__version__,
            "reportlab": reportlab.Version,
        },
        "runtime_fingerprint": _runtime_fingerprint(),
        "metadata_policy": metadata_policy(),
        "pages": pages,
        "block_mappings": block_mappings,
    }
    manifest["render_input_hash"] = sha256_canonical(
        render_input_hash_payload(manifest)
    )
    final_pdf = _compose_pdf_bytes(
        prepared_sources,
        overlay_pdf,
        expected_plan,
        render_input_hash=str(manifest["render_input_hash"]),
    )
    _verify_final_pdf(final_pdf, len(pages))
    manifest["output_pdf_sha256"] = sha256_bytes(final_pdf)
    try:
        validate_artifact("render-manifest", manifest)
    except SchemaValidationError as exc:
        raise CompositionError(
            "RENDER_MANIFEST_INVALID",
            "render manifest failed validation",
        ) from exc
    try:
        source_unchanged = source_path.read_bytes() == source_bytes
    except OSError:
        source_unchanged = False
    if not source_unchanged:
        raise CompositionError(
            "SOURCE_PDF_HASH_MISMATCH",
            "source PDF changed during composition",
        )
    created_output = False

    def rollback_owned_output() -> bool:
        if not created_output:
            return True
        try:
            return _delete_regular_file_if_hash_matches(
                output_path,
                str(manifest["output_pdf_sha256"]),
                missing_ok=True,
            )
        except (OSError, ValueError):
            return False

    try:
        write_immutable_bytes(output_path, final_pdf)
        created_output = True
        manifest_hash = write_immutable_artifact(
            manifest_path, manifest, "render-manifest"
        )
    except ArtifactExistsError as exc:
        if not rollback_owned_output():
            raise CompositionError(
                "RENDER_ROLLBACK_FAILED",
                "render failed and the owned candidate could not be removed",
            ) from exc
        raise CompositionError(
            "RENDER_OUTPUT_EXISTS", "render output already exists"
        ) from exc
    except OSError as exc:
        if not rollback_owned_output():
            raise CompositionError(
                "RENDER_ROLLBACK_FAILED",
                "render failed and the owned candidate could not be removed",
            ) from exc
        raise CompositionError("RENDER_COMMIT_FAILED", "render commit failed") from exc
    if output_path.read_bytes() != final_pdf:
        raise CompositionError("RENDER_COMMIT_FAILED", "persisted PDF differs")
    return CompositionResult(
        output_pdf_sha256=str(manifest["output_pdf_sha256"]),
        render_manifest_hash=manifest_hash,
        overlay_pdf_sha256=overlay_pdf_sha256,
        page_count=len(pages),
    )


__all__ = ["CompositionError", "CompositionResult", "compose_bilingual_pdf"]
