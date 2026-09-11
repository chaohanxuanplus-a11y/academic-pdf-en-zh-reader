# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.pdfgen.canvas import Canvas

import academic_pdf_en_zh_reader.rendering.compose as compose_module
import academic_pdf_en_zh_reader.rendering.overlay as overlay_module
from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.compose import (
    CompositionError,
    compose_bilingual_pdf,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan
from academic_pdf_en_zh_reader.rendering.page_geometry import (
    A3_LANDSCAPE_HEIGHT_MPT,
    A3_LANDSCAPE_WIDTH_MPT,
    A4_HEIGHT_MPT,
    A4_WIDTH_MPT,
    displayed_crop_relative_boxes,
    inspect_a4_page_geometry,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact

from .test_text_styles import build_render_fixture


def _source_pdf(
    path: Path,
    *,
    annotated: bool = False,
    active: str | None = None,
    rotation_degrees: int = 0,
    crop_inset_mpt: int = 0,
) -> str:
    raw_width_mpt = A4_HEIGHT_MPT if rotation_degrees in {90, 270} else A4_WIDTH_MPT
    raw_height_mpt = A4_WIDTH_MPT if rotation_degrees in {90, 270} else A4_HEIGHT_MPT
    if crop_inset_mpt:
        raw_width_mpt += crop_inset_mpt * 2
        raw_height_mpt += crop_inset_mpt * 2
    canvas = Canvas(
        str(path),
        pagesize=(raw_width_mpt / 1000, raw_height_mpt / 1000),
        invariant=1,
        pageCompression=0,
    )
    canvas.setFont("Helvetica", 12)
    canvas.drawString(45, 715, "Key scientific term in context.")
    canvas.line(45, 690, 300, 640)
    canvas.save()
    if annotated or active or rotation_degrees or crop_inset_mpt:
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        if rotation_degrees:
            writer.pages[0][NameObject("/Rotate")] = NumberObject(rotation_degrees)
        if crop_inset_mpt:
            inset = crop_inset_mpt / 1000
            writer.pages[0][NameObject("/CropBox")] = ArrayObject(
                [
                    FloatObject(inset),
                    FloatObject(inset),
                    FloatObject((crop_inset_mpt + A4_WIDTH_MPT) / 1000),
                    FloatObject((crop_inset_mpt + A4_HEIGHT_MPT) / 1000),
                ]
            )
        if active in {"open-action", "page-action"}:
            action = DictionaryObject(
                {
                    NameObject("/S"): NameObject("/JavaScript"),
                    NameObject("/JS"): TextStringObject("app.alert('blocked')"),
                }
            )
            if active == "open-action":
                writer.root_object[NameObject("/OpenAction")] = action
            else:
                writer.pages[0][NameObject("/AA")] = DictionaryObject(
                    {NameObject("/O"): action}
                )
        annotation = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject(
                    "/RichMedia" if active == "rich-media" else "/Text"
                ),
                NameObject("/Rect"): ArrayObject(
                    [
                        FloatObject(20),
                        FloatObject(20),
                        FloatObject(40),
                        FloatObject(40),
                    ]
                ),
                NameObject("/Contents"): TextStringObject("blocked"),
            }
        )
        writer.pages[0][NameObject("/Annots")] = ArrayObject([annotation])
        if active == "embedded-file":
            writer.root_object[NameObject("/Names")] = DictionaryObject(
                {
                    NameObject("/EmbeddedFiles"): DictionaryObject(
                        {
                            NameObject("/Names"): ArrayObject(
                                [
                                    TextStringObject("payload.bin"),
                                    DictionaryObject(),
                                ]
                            )
                        }
                    )
                }
            )
        writer.write(path)
    return sha256(path.read_bytes()).hexdigest()


def _receipt(
    artifacts: tuple[dict[str, object], ...],
    policy_inputs: dict[str, object],
) -> dict[str, object]:
    source, units, translation, review, annotations, graph, layout = artifacts
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact_kind": "finalization-receipt",
        "artifact_hashes": {
            "source": sha256_canonical(source),
            "units": sha256_canonical(units),
            "translation": sha256_canonical(translation),
            "review": sha256_canonical(review),
            "annotations": sha256_canonical(annotations),
            "frame-graph": sha256_canonical(graph),
            "layout": sha256_canonical(layout),
        },
        "policy_hashes": {
            key: sha256_canonical(policy_inputs[key])
            for key in (
                "style-contract",
                "font-fingerprint",
                "frame-graph-config",
                "layout-limits",
                "annotation-adapter-limits",
                "orange-selection-policy",
            )
        },
        "candidate_set_hash": annotations["candidate_set_hash"],
        "selection_hash": annotations["orange_selection_hash"],
        "solver_input_hash": layout["solver_input_hash"],
        "continuation_page_count": layout["solver_trace"]["continuation_page_count"],
    }
    receipt["receipt_hash"] = sha256_canonical(receipt)
    return receipt


def _inputs(
    tmp_path: Path,
    *,
    continuation: bool = False,
    annotated_source: bool = False,
    active_content: str | None = None,
    rotation_degrees: int = 0,
    crop_inset_mpt: int = 0,
) -> tuple[Path, tuple[dict[str, object], ...], dict[str, object], dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "source.pdf"
    normalized_pdf_sha256 = _source_pdf(
        source_path,
        annotated=annotated_source,
        active=active_content,
        rotation_degrees=rotation_degrees,
        crop_inset_mpt=crop_inset_mpt,
    )
    actual_geometry = inspect_a4_page_geometry(PdfReader(source_path).pages[0])
    artifact_media, artifact_crop = displayed_crop_relative_boxes(actual_geometry)
    artifacts = build_render_fixture(
        source_sha256="d" * 64,
        normalized_pdf_sha256=normalized_pdf_sha256,
        chinese_text=(
            "甲。" * 3_000
            if continuation
            else "甲☢乙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
        ),
        page_media_box_mpt=list(artifact_media),
        page_crop_box_mpt=list(artifact_crop),
        rotation_degrees=rotation_degrees,
    )
    source, _units, _translation, _review, annotations, graph, layout = artifacts
    plan = build_overlay_plan(source, graph, layout, annotations)
    policy_inputs: dict[str, object] = {
        "style-contract": {"version": 1, "name": "test-style"},
        "font-fingerprint": graph["font_fingerprint"],
        "frame-graph-config": graph["flow_spacing"],
        "layout-limits": layout["solver_policy"],
        "annotation-adapter-limits": {"version": 1},
        "orange-selection-policy": {"version": 1},
    }
    return (
        source_path,
        artifacts,
        policy_inputs,
        _receipt(artifacts, policy_inputs) | {"_overlay_plan": plan},
    )


def _compose(
    tmp_path: Path,
    *,
    continuation: bool = False,
    annotated_source: bool = False,
    rotation_degrees: int = 0,
    crop_inset_mpt: int = 0,
):
    source_path, artifacts, policies, packed = _inputs(
        tmp_path,
        continuation=continuation,
        annotated_source=annotated_source,
        rotation_degrees=rotation_degrees,
        crop_inset_mpt=crop_inset_mpt,
    )
    plan = packed.pop("_overlay_plan")
    job_root = tmp_path / "job"
    job_root.mkdir()
    output = job_root / "candidate.pdf"
    manifest = job_root / "render-manifest.json"
    result = compose_bilingual_pdf(
        source_pdf_path=source_path,
        source=artifacts[0],
        units=artifacts[1],
        translation=artifacts[2],
        review=artifacts[3],
        annotations=artifacts[4],
        frame_graph=artifacts[5],
        layout=artifacts[6],
        finalization_receipt=packed,
        policy_inputs=policies,
        overlay_plan=plan,
        expected_finalization_receipt_hash=sha256_canonical(packed),
        expected_overlay_plan_hash=plan["overlay_plan_hash"],
        job_root=job_root,
        output_pdf_path=output,
        render_manifest_path=manifest,
    )
    return result, output, manifest, artifacts, plan, policies, packed


def test_composition_registers_fonts_before_recomputing_plan_in_fresh_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reportlab.pdfbase import pdfmetrics

    source_path, artifacts, policies, packed = _inputs(tmp_path)
    plan = packed.pop("_overlay_plan")
    # A new child has none of the parent's font-registration side effects.
    monkeypatch.setattr(pdfmetrics, "_fonts", {})
    monkeypatch.setattr(pdfmetrics, "_dynFaceNames", {})
    job_root = tmp_path / "fresh-worker"
    job_root.mkdir()
    output = job_root / "candidate.pdf"
    result = compose_bilingual_pdf(
        source_pdf_path=source_path,
        source=artifacts[0],
        units=artifacts[1],
        translation=artifacts[2],
        review=artifacts[3],
        annotations=artifacts[4],
        frame_graph=artifacts[5],
        layout=artifacts[6],
        finalization_receipt=packed,
        policy_inputs=policies,
        overlay_plan=plan,
        expected_finalization_receipt_hash=sha256_canonical(packed),
        expected_overlay_plan_hash=plan["overlay_plan_hash"],
        job_root=job_root,
        output_pdf_path=output,
        render_manifest_path=job_root / "render-manifest.json",
    )
    assert result.page_count == len(PdfReader(output).pages)


def test_vector_overlay_and_a3_composition_are_exact_and_deterministic(
    tmp_path: Path,
) -> None:
    first = _compose(tmp_path / "first", continuation=True)
    second = _compose(tmp_path / "second", continuation=True)
    (
        first_result,
        first_pdf,
        first_manifest_path,
        artifacts,
        plan,
        _policies,
        receipt,
    ) = first
    second_result, second_pdf, second_manifest_path, *_rest = second

    assert first_pdf.read_bytes() == second_pdf.read_bytes()
    assert first_manifest_path.read_bytes() == second_manifest_path.read_bytes()
    assert first_result.output_pdf_sha256 == second_result.output_pdf_sha256

    manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
    validate_artifact("render-manifest", manifest)
    assert manifest["render_manifest_version"] == 2
    assert manifest["finalization_receipt_hash"] == sha256_canonical(receipt)
    assert manifest["source_sha256"] == artifacts[0]["source_sha256"]
    assert manifest["normalized_pdf_sha256"] == artifacts[0]["normalized_pdf_sha256"]
    assert manifest["source_sha256"] != manifest["normalized_pdf_sha256"]
    assert manifest["overlay_plan_hash"] == plan["overlay_plan_hash"]
    assert manifest["output_pdf_sha256"] == sha256(first_pdf.read_bytes()).hexdigest()
    assert manifest["overlay_pdf_sha256"] == first_result.overlay_pdf_sha256
    assert manifest["font_fingerprint"] == artifacts[5]["font_fingerprint"]
    assert manifest["font_usages"]
    assert len(manifest["block_mappings"]) == sum(
        len(page["blocks"]) for page in artifacts[6]["pages"]
    )

    reader = PdfReader(first_pdf, strict=True)
    assert len(reader.pages) == len(plan["pages"])
    assert len(reader.pages) > 1
    for page, mapping in zip(reader.pages, manifest["pages"], strict=True):
        assert round(float(page.mediabox.width) * 1000) == A3_LANDSCAPE_WIDTH_MPT
        assert round(float(page.mediabox.height) * 1000) == A3_LANDSCAPE_HEIGHT_MPT
        assert (
            mapping["overlay_page_plan_hash"]
            == plan["pages"][mapping["output_page_number"] - 1]["page_plan_hash"]
        )
    extracted = [page.extract_text() or "" for page in reader.pages]
    assert "Key scientific term in context." in extracted[0]
    assert "Key scientific term in context." not in extracted[-1]
    assert "本文件由用户主动调用" in extracted[-1]
    assert any("甲" in text for text in extracted)
    assert any(mapping["page_kind"] == "continuation" for mapping in manifest["pages"])
    assert {"/OpenAction", "/AA", "/AcroForm", "/Names"}.isdisjoint(reader.root_object)
    assert all("/Annots" not in page for page in reader.pages)
    assert b"/JavaScript" not in first_pdf.read_bytes()
    operators = {
        operator
        for page in reader.pages
        for _operands, operator in page.get_contents().operations
    }
    assert {b"Tj", b"S"} <= operators


def test_statement_stays_on_final_measured_page_and_preserves_original(tmp_path):
    result, output, manifest_path, artifacts, plan, *_ = _compose(tmp_path)
    reader = PdfReader(output, strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.page_count == len(artifacts[6]["pages"]) == len(reader.pages)
    assert plan["branding"]["rendered_page_number"] == result.page_count
    assert manifest["pages"][0]["source_page_number"] == 1
    assert "Key scientific term in context." in reader.pages[0].extract_text()
    assert "本文件由用户主动调用" in reader.pages[-1].extract_text()


def test_passive_source_annotations_are_stripped_by_the_fixed_policy(
    tmp_path: Path,
) -> None:
    _result, output, manifest_path, *_rest = _compose(
        tmp_path,
        annotated_source=True,
    )

    reader = PdfReader(output, strict=True)
    assert all("/Annots" not in page for page in reader.pages)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["metadata_policy"]["source_annotations"] == "stripped"


@pytest.mark.parametrize(
    ("rotation_degrees", "crop_inset_mpt", "expected_translation"),
    (
        (90, 0, [0, 0]),
        (270, 0, [0, 0]),
        (0, 10_000, [-10_000, -10_000]),
    ),
)
def test_rotated_and_nonzero_crop_sources_preserve_exact_one_to_one_geometry(
    tmp_path: Path,
    rotation_degrees: int,
    crop_inset_mpt: int,
    expected_translation: list[int],
) -> None:
    _result, output, manifest_path, artifacts, *_rest = _compose(
        tmp_path,
        rotation_degrees=rotation_degrees,
        crop_inset_mpt=crop_inset_mpt,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    [mapping] = [m for m in manifest["pages"] if m["source_page_number"] is not None]
    assert manifest["pages"][-1]["page_kind"] == "native"
    assert mapping["source_rotation_degrees"] == rotation_degrees
    assert mapping["source_transform_mpt"][:4] == [1000, 0, 0, 1000]
    assert mapping["source_transform_mpt"][4:] == expected_translation
    assert artifacts[0]["pages"][0]["crop_box_mpt"] == [
        0,
        0,
        A4_WIDTH_MPT,
        A4_HEIGHT_MPT,
    ]
    reader = PdfReader(output, strict=True)
    assert "Key scientific term in context." in (reader.pages[0].extract_text() or "")


def test_receipt_or_source_pdf_mismatch_fails_before_canvas_or_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifacts, policies, packed = _inputs(tmp_path)
    plan = packed.pop("_overlay_plan")
    packed["artifact_hashes"]["source"] = "0" * 64
    packed["receipt_hash"] = sha256_canonical(
        {key: value for key, value in packed.items() if key != "receipt_hash"}
    )
    expected_receipt_hash = sha256_canonical(_receipt(artifacts, policies))
    created: list[bool] = []
    monkeypatch.setattr(
        overlay_module,
        "Canvas",
        lambda *args, **kwargs: created.append(True),
    )
    job_root = tmp_path / "job"
    job_root.mkdir()
    with pytest.raises(CompositionError) as receipt_error:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=packed,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=expected_receipt_hash,
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=job_root / "candidate.pdf",
            render_manifest_path=job_root / "render-manifest.json",
        )
    assert receipt_error.value.code == "FINALIZATION_RECEIPT_MISMATCH"
    assert created == []
    assert list(job_root.iterdir()) == []


def test_self_consistent_rehashed_chain_cannot_replace_the_ledger_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifacts, policies, packed = _inputs(tmp_path)
    plan = packed.pop("_overlay_plan")
    ledger_hash = sha256_canonical(packed)
    forged_policies = deepcopy(policies)
    forged_policies["style-contract"]["name"] = "forged-style"
    forged_receipt = deepcopy(packed)
    forged_receipt["policy_hashes"]["style-contract"] = sha256_canonical(
        forged_policies["style-contract"]
    )
    forged_receipt["receipt_hash"] = sha256_canonical(
        {key: value for key, value in forged_receipt.items() if key != "receipt_hash"}
    )
    created: list[bool] = []
    monkeypatch.setattr(
        overlay_module,
        "Canvas",
        lambda *args, **kwargs: created.append(True),
    )
    job_root = tmp_path / "job"
    job_root.mkdir()

    with pytest.raises(CompositionError) as caught:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=forged_receipt,
            policy_inputs=forged_policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=ledger_hash,
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=job_root / "candidate.pdf",
            render_manifest_path=job_root / "render-manifest.json",
        )

    assert caught.value.code == "FINALIZATION_RECEIPT_MISMATCH"
    assert created == []
    assert list(job_root.iterdir()) == []

    clean = _receipt(artifacts, policies)
    corrupt_internal = deepcopy(clean)
    corrupt_internal["receipt_hash"] = "0" * 64
    with pytest.raises(CompositionError) as internal_error:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=corrupt_internal,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=sha256_canonical(corrupt_internal),
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=job_root / "candidate.pdf",
            render_manifest_path=job_root / "render-manifest.json",
        )
    assert internal_error.value.code == "FINALIZATION_RECEIPT_MISMATCH"
    assert created == []
    assert list(job_root.iterdir()) == []

    source_path.write_bytes(source_path.read_bytes() + b"\n")
    with pytest.raises(CompositionError) as source_error:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=clean,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=sha256_canonical(clean),
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=job_root / "candidate.pdf",
            render_manifest_path=job_root / "render-manifest.json",
        )
    assert source_error.value.code == "SOURCE_PDF_HASH_MISMATCH"
    assert created == []
    assert list(job_root.iterdir()) == []


@pytest.mark.parametrize(
    "active_kind",
    ("open-action", "page-action", "embedded-file", "rich-media"),
)
def test_active_content_inventory_is_rejected_before_canvas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_kind: str,
) -> None:
    source_path, artifacts, policies, packed = _inputs(
        tmp_path,
        active_content=active_kind,
    )
    plan = packed.pop("_overlay_plan")
    created: list[bool] = []
    monkeypatch.setattr(
        overlay_module,
        "Canvas",
        lambda *args, **kwargs: created.append(True),
    )
    job_root = tmp_path / "job"
    job_root.mkdir()

    with pytest.raises(CompositionError) as caught:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=packed,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=sha256_canonical(packed),
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=job_root / "candidate.pdf",
            render_manifest_path=job_root / "render-manifest.json",
        )

    assert caught.value.code == "SOURCE_ACTIVE_CONTENT"
    assert created == []
    assert list(job_root.iterdir()) == []


def test_no_clobber_and_manifest_failure_roll_back_owned_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifacts, policies, packed = _inputs(tmp_path)
    plan = packed.pop("_overlay_plan")
    job_root = tmp_path / "job"
    job_root.mkdir()
    output = job_root / "candidate.pdf"
    manifest = job_root / "render-manifest.json"
    output.write_bytes(b"existing")
    created: list[bool] = []
    monkeypatch.setattr(
        overlay_module,
        "Canvas",
        lambda *args, **kwargs: created.append(True),
    )
    with pytest.raises(CompositionError) as exists_error:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=packed,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=sha256_canonical(packed),
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=output,
            render_manifest_path=manifest,
        )
    assert exists_error.value.code == "RENDER_OUTPUT_EXISTS"
    assert output.read_bytes() == b"existing"
    assert created == []

    output.unlink()
    monkeypatch.undo()
    monkeypatch.setattr(
        compose_module,
        "write_immutable_artifact",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected")),
    )
    with pytest.raises(CompositionError) as commit_error:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=packed,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=sha256_canonical(packed),
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=output,
            render_manifest_path=manifest,
        )
    assert commit_error.value.code == "RENDER_COMMIT_FAILED"
    assert not output.exists()
    assert not manifest.exists()


def test_rehashed_geometry_tamper_is_rejected_by_parent_recomputed_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifacts, policies, packed = _inputs(tmp_path)
    plan = packed.pop("_overlay_plan")
    expected_hash = plan["overlay_plan_hash"]
    tampered = deepcopy(plan)
    run = tampered["pages"][0]["draw_runs"][0]
    run["x_mpt"] += 1_000
    run["bbox_mpt"][0] += 1_000
    run["bbox_mpt"][2] += 1_000
    run["draw_run_hash"] = sha256_canonical(
        {key: value for key, value in run.items() if key != "draw_run_hash"}
    )
    page = tampered["pages"][0]
    page["page_plan_hash"] = sha256_canonical(
        {key: value for key, value in page.items() if key != "page_plan_hash"}
    )
    tampered["overlay_plan_hash"] = sha256_canonical(
        {key: value for key, value in tampered.items() if key != "overlay_plan_hash"}
    )
    created: list[bool] = []
    monkeypatch.setattr(
        overlay_module,
        "Canvas",
        lambda *args, **kwargs: created.append(True),
    )
    job_root = tmp_path / "job"
    job_root.mkdir()

    with pytest.raises(CompositionError) as caught:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=packed,
            policy_inputs=policies,
            overlay_plan=tampered,
            expected_finalization_receipt_hash=sha256_canonical(packed),
            expected_overlay_plan_hash=expected_hash,
            job_root=job_root,
            output_pdf_path=job_root / "candidate.pdf",
            render_manifest_path=job_root / "render-manifest.json",
        )

    assert caught.value.code == "OVERLAY_PLAN_MISMATCH"
    assert created == []
    assert list(job_root.iterdir()) == []


def test_manifest_failure_never_deletes_a_path_swapped_after_candidate_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifacts, policies, packed = _inputs(tmp_path)
    plan = packed.pop("_overlay_plan")
    job_root = tmp_path / "job"
    job_root.mkdir()
    output = job_root / "candidate.pdf"
    manifest = job_root / "render-manifest.json"
    owned_backup = job_root / "owned-candidate.pdf"
    competitor = job_root / "must-survive.pdf"
    competitor_bytes = b"unrelated replacement"
    competitor.write_bytes(competitor_bytes)

    def swap_then_fail(*_args, **_kwargs):
        output.rename(owned_backup)
        competitor.rename(output)
        raise OSError("injected")

    monkeypatch.setattr(
        compose_module,
        "write_immutable_artifact",
        swap_then_fail,
    )

    with pytest.raises(CompositionError) as caught:
        compose_bilingual_pdf(
            source_pdf_path=source_path,
            source=artifacts[0],
            units=artifacts[1],
            translation=artifacts[2],
            review=artifacts[3],
            annotations=artifacts[4],
            frame_graph=artifacts[5],
            layout=artifacts[6],
            finalization_receipt=packed,
            policy_inputs=policies,
            overlay_plan=plan,
            expected_finalization_receipt_hash=sha256_canonical(packed),
            expected_overlay_plan_hash=plan["overlay_plan_hash"],
            job_root=job_root,
            output_pdf_path=output,
            render_manifest_path=manifest,
        )

    assert caught.value.code == "RENDER_ROLLBACK_FAILED"
    assert output.read_bytes() == competitor_bytes
    assert owned_backup.exists()
    assert not manifest.exists()
