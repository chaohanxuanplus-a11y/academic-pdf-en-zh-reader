# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.normalization.core import plan_displayed_crop

NORMALIZED_SHA = "c" * 64
NORMALIZATION_SHA = "d" * 64


def _crash_before_extraction_commit(destination: str, payload: bytes) -> None:
    from academic_pdf_en_zh_reader.extraction import api

    def crash_link(*_args, **_kwargs) -> None:
        os._exit(23)

    api.os.link = crash_link
    api._persist_exclusive(Path(destination), payload)


def _crash_after_extraction_commit(destination: str, payload: bytes) -> None:
    from academic_pdf_en_zh_reader.extraction import api

    original_link = os.link

    def commit_then_crash(*args, **kwargs) -> None:
        original_link(*args, **kwargs)
        os._exit(24)

    api.os.link = commit_then_crash
    api._persist_exclusive(Path(destination), payload)


def _preflight(source_sha256: str, source_size: int) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "preflight",
        "source_sha256": source_sha256,
        "passed": True,
        "error_codes": [],
        "warnings": [],
        "checks": [{"id": "pdf_catalog", "hard_gate": True, "passed": True}],
        "pages": [
            {
                "page_number": 1,
                "width_mpt": 595276,
                "height_mpt": 841890,
                "media_box_mpt": [0, 0, 595276, 841890],
                "crop_box_mpt": [0, 0, 595276, 841890],
                "rotation_degrees": 0,
                "extractable_character_count": 1,
            }
        ],
        "limits": {
            "file_bytes": {"observed": source_size, "maximum": 100 * 1024 * 1024},
            "page_count": {"observed": 1, "maximum": 500},
            "object_count": {"observed": 15, "maximum": 250_000},
            "recursion_depth": {"observed": 4, "maximum": 64},
            "decompressed_stream_bytes": {
                "observed": 1024,
                "maximum": 1024 * 1024 * 1024,
            },
            "image_bytes": {"observed": 0, "maximum": 512 * 1024 * 1024},
        },
    }


def _normalization(
    preflight: dict[str, object],
    preflight_sha256: str,
    normalized_pdf_sha256: str,
    normalized_pdf_bytes: int,
) -> dict[str, object]:
    source_page = preflight["pages"][0]
    plan = plan_displayed_crop(source_page["width_mpt"], source_page["height_mpt"])
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": "1.0.0",
        "source_sha256": preflight["source_sha256"],
        "preflight_sha256": preflight_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "normalized_pdf_bytes": normalized_pdf_bytes,
        "pages": [
            {
                "page_number": 1,
                "source_media_box_mpt": source_page["media_box_mpt"],
                "source_crop_box_mpt": source_page["crop_box_mpt"],
                "source_rotation_degrees": source_page["rotation_degrees"],
                "displayed_width_mpt": source_page["width_mpt"],
                "displayed_height_mpt": source_page["height_mpt"],
                "scale_ppm": plan.scale_ppm,
                "scaled_width_mpt": plan.scaled_width_mpt,
                "scaled_height_mpt": plan.scaled_height_mpt,
                "padding_left_mpt": plan.padding_left_mpt,
                "padding_bottom_mpt": plan.padding_bottom_mpt,
                "padding_right_mpt": plan.padding_right_mpt,
                "padding_top_mpt": plan.padding_top_mpt,
                "normalized_content_box_mpt": list(plan.normalized_content_box_mpt),
            }
        ],
    }


def _extraction(
    source_sha256: str,
    preflight_sha256: str,
    *,
    normalized_pdf_sha256: str = NORMALIZED_SHA,
    normalization_sha256: str = NORMALIZATION_SHA,
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "extraction",
        "source_sha256": source_sha256,
        "normalized_pdf_sha256": normalized_pdf_sha256,
        "preflight_sha256": preflight_sha256,
        "normalization_sha256": normalization_sha256,
        "format_version": "1.1.0",
        "counts": {
            "page_count": 1,
            "character_count": 1,
            "vector_count": 0,
            "image_count": 0,
            "line_count": 1,
            "graphic_region_count": 0,
            "caption_count": 0,
            "reference_count": 0,
        },
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595276, 841890],
                "crop_box_mpt": [0, 0, 595276, 841890],
                "rotation_degrees": 0,
                "chars": [
                    {
                        "id": "p0001-char-000001",
                        "page_number": 1,
                        "text": "A",
                        "bbox_mpt": [1000, 1000, 2000, 2000],
                        "font_name": "Helvetica",
                        "font_size_mpt": 10000,
                        "fill_color": None,
                        "stroke_color": None,
                        "upright": True,
                    }
                ],
                "rectangles": [],
                "curves": [],
                "images": [],
                "lines": [
                    {
                        "id": "p0001-line-00001",
                        "page_number": 1,
                        "text": "A",
                        "bbox_mpt": [1000, 1000, 2000, 2000],
                        "character_ids": ["p0001-char-000001"],
                        "font_names": ["Helvetica"],
                        "fill_colors": [None],
                        "max_font_size_mpt": 10000,
                        "confidence_ppm": 1000000,
                        "body_eligible": True,
                        "coverage_eligible": True,
                        "exclusion_kind": None,
                        "container_kind": None,
                        "container_id": None,
                    }
                ],
                "graphic_regions": [],
                "captions": [],
                "references": [],
            }
        ],
    }


def _normalized_expected_pages(
    preflight: dict[str, object],
) -> list[dict[str, object]]:
    return [
        {
            "page_number": page["page_number"],
            "width_mpt": 595_276,
            "height_mpt": 841_890,
            "media_box_mpt": [0, 0, 595_276, 841_890],
            "crop_box_mpt": [0, 0, 595_276, 841_890],
            "rotation_degrees": 0,
            "extractable_character_count": page["extractable_character_count"],
        }
        for page in preflight["pages"]
    ]


def test_parent_api_fresh_import_does_not_load_pdf_parsers() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (
        "import importlib,sys;"
        f"sys.path.insert(0,{str(root / 'src')!r});"
        "importlib.import_module('academic_pdf_en_zh_reader.extraction.api');"
        "assert not any(n == 'pdfplumber' or "
        "n.startswith(('pdfplumber.', 'pdfminer.')) for n in sys.modules)"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows worker bridge only")
def test_parent_bridge_preserves_scanned_pdf_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api
    from academic_pdf_en_zh_reader.security import windows_worker
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits
    from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest

    def reject_scan(*_args, **_kwargs):
        raise windows_worker.WorkerReportedError(
            "SCANNED_PDF_UNSUPPORTED",
            "scanned or OCR-overlay PDFs are unsupported",
        )

    monkeypatch.setattr(windows_worker, "run_worker", reject_scan)
    request = WorkerRequest(
        operation="extract",
        input_path="input.pdf",
        parameters={
            "policy_version": "1.1.0",
            "source_sha256": "a" * 64,
            "normalized_pdf_sha256": "c" * 64,
            "input_bytes": 1,
            "preflight_sha256": "b" * 64,
            "normalization_sha256": "d" * 64,
        },
    )

    with pytest.raises(api._StableBridgeError) as captured:
        api._run_platform_worker(request, tmp_path, limits=WorkerLimits())

    assert captured.value.code == "SCANNED_PDF_UNSUPPORTED"


@pytest.mark.skipif(os.name != "nt", reason="Windows worker bridge only")
def test_parent_bridge_preserves_worker_memory_limit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api
    from academic_pdf_en_zh_reader.security import windows_worker
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits
    from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest

    def reject_for_memory(*_args, **_kwargs):
        raise windows_worker.WorkerReportedError(
            "WORKER_LIMIT_EXCEEDED",
            "PDF extraction exceeded the worker memory limit",
        )

    monkeypatch.setattr(windows_worker, "run_worker", reject_for_memory)
    request = WorkerRequest(
        operation="extract",
        input_path="input.pdf",
        parameters={
            "policy_version": "1.1.0",
            "source_sha256": "a" * 64,
            "normalized_pdf_sha256": "c" * 64,
            "input_bytes": 1,
            "preflight_sha256": "b" * 64,
            "normalization_sha256": "d" * 64,
        },
    )

    with pytest.raises(api._StableBridgeError) as captured:
        api._run_platform_worker(request, tmp_path, limits=WorkerLimits())

    assert captured.value.code == "WORKER_LIMIT_EXCEEDED"


def test_parent_api_validates_preflight_and_persists_bound_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    normalized_pdf_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    raw_source_sha = "a" * 64
    job = tmp_path / "job"
    job.mkdir()
    preflight = _preflight(raw_source_sha, 42)
    preflight_bytes = canonical_json_bytes(preflight)
    (job / "preflight.json").write_bytes(preflight_bytes)
    preflight_sha = hashlib.sha256(preflight_bytes).hexdigest()
    normalization = _normalization(
        preflight,
        preflight_sha,
        normalized_pdf_sha,
        source.stat().st_size,
    )
    normalization_bytes = canonical_json_bytes(normalization)
    (job / "normalization.json").write_bytes(normalization_bytes)
    normalization_sha = hashlib.sha256(normalization_bytes).hexdigest()
    artifact = _extraction(
        raw_source_sha,
        preflight_sha,
        normalized_pdf_sha256=normalized_pdf_sha,
        normalization_sha256=normalization_sha,
    )

    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    safe_copy = SafeInputCopy(
        copied_root,
        copied,
        normalized_pdf_sha,
        copied.stat().st_size,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_a, **_k: safe_copy)

    def fake_run(request, workspace, *, limits):
        captured["request"] = request
        captured["workspace"] = workspace
        captured["normalization_handoff"] = (
            workspace / "normalization.json"
        ).read_bytes()
        return SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        )

    monkeypatch.setattr(api, "_run_platform_worker", fake_run)

    outcome = api.extract_untrusted_pdf(source, job)

    assert outcome["status"] == "ok"
    assert outcome["summary"] == artifact["counts"]
    assert (job / "extraction.json").read_bytes() == canonical_json_bytes(artifact)
    assert captured["request"].parameters == {
        "policy_version": "1.1.0",
        "source_sha256": raw_source_sha,
        "normalized_pdf_sha256": normalized_pdf_sha,
        "input_bytes": source.stat().st_size,
        "preflight_sha256": preflight_sha,
        "normalization_sha256": normalization_sha,
    }
    assert captured["normalization_handoff"] == normalization_bytes
    assert not copied_root.exists()


def test_parent_accepts_normalized_a4_geometry_for_rotated_source_page() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["rotation_degrees"] = 90
    preflight["pages"][0]["media_box_mpt"] = [0, 0, 841890, 595276]
    preflight["pages"][0]["crop_box_mpt"] = [0, 0, 841890, 595276]
    artifact = _extraction(source_sha, preflight_sha)

    validated = _validate_extraction_artifact(
        canonical_json_bytes(artifact),
        source_sha256=source_sha,
        normalized_pdf_sha256=NORMALIZED_SHA,
        preflight_sha256=preflight_sha,
        normalization_sha256=NORMALIZATION_SHA,
        expected_pages=_normalized_expected_pages(preflight),
        limits=WorkerLimits(),
    )

    assert validated == artifact


def test_parent_uses_the_same_normal_word_gap_as_line_builder() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 2
    artifact = _extraction(source_sha, preflight_sha)
    page = artifact["pages"][0]
    page["chars"].append(
        {
            "id": "p0001-char-000002",
            "page_number": 1,
            "text": "B",
            "bbox_mpt": [3_600, 1_000, 4_600, 2_000],
            "font_name": "Helvetica",
            "font_size_mpt": 10_000,
            "fill_color": None,
            "stroke_color": None,
            "upright": True,
        }
    )
    page["lines"][0].update(
        {
            "text": "A B",
            "bbox_mpt": [1_000, 1_000, 4_600, 2_000],
            "character_ids": [
                "p0001-char-000001",
                "p0001-char-000002",
            ],
        }
    )
    artifact["counts"]["character_count"] = 2

    validated = _validate_extraction_artifact(
        canonical_json_bytes(artifact),
        source_sha256=source_sha,
        normalized_pdf_sha256=NORMALIZED_SHA,
        preflight_sha256=preflight_sha,
        normalization_sha256=NORMALIZATION_SHA,
        expected_pages=_normalized_expected_pages(preflight),
        limits=WorkerLimits(),
    )

    assert validated == artifact


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_parent_ignores_raw_boxes_after_binding_normalized_a4_geometry(
    rotation: int,
) -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    expected = preflight["pages"][0]
    expected["media_box_mpt"] = [20_000, 30_000, 920_000, 650_000]
    expected["crop_box_mpt"] = [40_000, 70_000, 850_000, 600_000]
    expected["rotation_degrees"] = rotation
    expected["width_mpt"] = 530_000 if rotation in {90, 270} else 810_000
    expected["height_mpt"] = 810_000 if rotation in {90, 270} else 530_000
    artifact = _extraction(source_sha, preflight_sha)

    validated = _validate_extraction_artifact(
        canonical_json_bytes(artifact),
        source_sha256=source_sha,
        normalized_pdf_sha256=NORMALIZED_SHA,
        preflight_sha256=preflight_sha,
        normalization_sha256=NORMALIZATION_SHA,
        expected_pages=_normalized_expected_pages(preflight),
        limits=WorkerLimits(),
    )

    assert validated == artifact


def _drop_all_extracted_text(artifact: dict[str, object]) -> None:
    page = artifact["pages"][0]
    page["chars"] = []
    page["lines"] = []
    artifact["counts"]["character_count"] = 0
    artifact["counts"]["line_count"] = 0


def _duplicate_character_across_lines(artifact: dict[str, object]) -> None:
    page = artifact["pages"][0]
    duplicate = deepcopy(page["lines"][0])
    duplicate["id"] = "p0001-line-00002"
    page["lines"].append(duplicate)
    artifact["counts"]["line_count"] = 2


def _orphan_nonspace_character(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["lines"] = []
    artifact["counts"]["line_count"] = 0


def _move_line_away_from_its_character(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["lines"][0]["bbox_mpt"] = [
        10_000,
        10_000,
        11_000,
        11_000,
    ]


def _move_text_outside_visible_crop(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["chars"][0]["bbox_mpt"] = [
        -1_000_000_000,
        -1_000_000_000,
        -999_999_000,
        -999_999_000,
    ]
    artifact["pages"][0]["lines"][0]["bbox_mpt"] = [
        -1_000_000_000,
        -1_000_000_000,
        -999_999_000,
        -999_999_000,
    ]


def _add_huge_image(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["images"] = [
        {
            "id": "p0001-image-000001",
            "page_number": 1,
            "bbox_mpt": [0, 0, 595276, 841890],
            "pixel_width": 1_000_000_000_000,
            "pixel_height": 1_000_000_000_000,
        }
    ]
    artifact["counts"]["image_count"] = 1


def _shift_both_page_boxes(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["media_box_mpt"] = [
        10_000,
        20_000,
        605_276,
        861_890,
    ]
    artifact["pages"][0]["crop_box_mpt"] = [
        10_000,
        20_000,
        605_276,
        861_890,
    ]


def _fabricate_line_text(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["lines"][0]["text"] = (
        "Entire fabricated paragraph unrelated to character A"
    )


def _change_line_fill_color(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["lines"][0]["fill_colors"] = [[999_999]]


def _mismatch_graphic_id_and_kind(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["graphic_regions"] = [
        {
            "id": "p0001-figure-0001",
            "page_number": 1,
            "kind": "table",
            "bbox_mpt": [72_000, 300_000, 300_000, 500_000],
            "evidence": "intersecting-grid",
        }
    ]
    artifact["counts"]["graphic_region_count"] = 1


def _invent_unsupported_figure_and_hide_body(artifact: dict[str, object]) -> None:
    page = artifact["pages"][0]
    page["graphic_regions"] = [
        {
            "id": "p0001-figure-0001",
            "page_number": 1,
            "kind": "figure",
            "bbox_mpt": [0, 0, 595_276, 841_890],
            "evidence": "embedded-image",
        }
    ]
    line = page["lines"][0]
    line.update(
        confidence_ppm=400_000,
        body_eligible=False,
        coverage_eligible=False,
        container_kind="figure",
        container_id="p0001-figure-0001",
    )
    artifact["counts"]["graphic_region_count"] = 1


def _invent_repeated_header_exclusion(artifact: dict[str, object]) -> None:
    artifact["pages"][0]["lines"][0].update(
        confidence_ppm=0,
        body_eligible=False,
        coverage_eligible=False,
        exclusion_kind="repeated-header",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_all_extracted_text,
        _duplicate_character_across_lines,
        _orphan_nonspace_character,
        _move_line_away_from_its_character,
        _move_text_outside_visible_crop,
        _add_huge_image,
        _shift_both_page_boxes,
        _fabricate_line_text,
        _change_line_fill_color,
        _mismatch_graphic_id_and_kind,
        _invent_unsupported_figure_and_hide_body,
        _invent_repeated_header_exclusion,
    ],
)
def test_parent_rejects_incomplete_or_out_of_bounds_extraction(mutate) -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    artifact = _extraction(source_sha, preflight_sha)
    mutate(artifact)

    with pytest.raises(ValueError):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_accepts_reference_to_a_graphic_region_on_another_page() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    second_expected = deepcopy(preflight["pages"][0])
    second_expected["page_number"] = 2
    preflight["pages"].append(second_expected)
    artifact = _extraction(source_sha, preflight_sha)
    first_page = artifact["pages"][0]
    first_page["chars"][0]["text"] = "As shown in Fig. 1."
    first_page["lines"][0]["text"] = "As shown in Fig. 1."
    first_page["references"] = [
        {
            "id": "p0001-reference-0001",
            "page_number": 1,
            "line_id": "p0001-line-00001",
            "label": "Figure 1",
            "target_id": "p0002-figure-0001",
        }
    ]
    second_page = deepcopy(first_page)
    second_page["page_number"] = 2
    second_page["chars"][0]["id"] = "p0002-char-000001"
    second_page["chars"][0]["page_number"] = 2
    second_page["lines"][0]["id"] = "p0002-line-00001"
    second_page["lines"][0]["page_number"] = 2
    second_page["lines"][0]["character_ids"] = ["p0002-char-000001"]
    second_page["references"] = []
    second_page["chars"][0]["text"] = "Figure 1."
    second_page["chars"][0]["bbox_mpt"] = [
        72_000,
        280_000,
        73_000,
        290_000,
    ]
    second_page["lines"][0]["text"] = "Figure 1."
    second_page["lines"][0]["bbox_mpt"] = [72_000, 280_000, 73_000, 290_000]
    second_page["lines"][0]["body_eligible"] = False
    second_page["images"] = [
        {
            "id": "p0002-image-000001",
            "page_number": 2,
            "bbox_mpt": [72_000, 300_000, 300_000, 500_000],
            "pixel_width": 100,
            "pixel_height": 100,
        }
    ]
    second_page["graphic_regions"] = [
        {
            "id": "p0002-figure-0001",
            "page_number": 2,
            "kind": "figure",
            "bbox_mpt": [72_000, 300_000, 300_000, 500_000],
            "evidence": "embedded-image",
        }
    ]
    second_page["captions"] = [
        {
            "id": "p0002-figure-caption-0001",
            "page_number": 2,
            "kind": "figure",
            "number": 1,
            "text": "Figure 1.",
            "line_ids": ["p0002-line-00001"],
            "target_id": "p0002-figure-0001",
        }
    ]
    artifact["pages"].append(second_page)
    artifact["counts"].update(
        page_count=2,
        character_count=2,
        image_count=1,
        line_count=2,
        graphic_region_count=1,
        caption_count=1,
        reference_count=1,
    )
    for expected_page, extracted_page in zip(
        preflight["pages"], artifact["pages"], strict=True
    ):
        expected_page["extractable_character_count"] = sum(
            not character.isspace()
            for item in extracted_page["chars"]
            for character in item["text"]
        )

    validated = _validate_extraction_artifact(
        canonical_json_bytes(artifact),
        source_sha256=source_sha,
        normalized_pdf_sha256=NORMALIZED_SHA,
        preflight_sha256=preflight_sha,
        normalization_sha256=NORMALIZATION_SHA,
        expected_pages=_normalized_expected_pages(preflight),
        limits=WorkerLimits(),
    )

    assert validated == artifact

    wrong_number = deepcopy(artifact)
    wrong_number["pages"][0]["chars"][0]["text"] = "As shown in Figure 2."
    wrong_number["pages"][0]["lines"][0]["text"] = "As shown in Figure 2."
    wrong_number["pages"][0]["references"][0]["label"] = "Figure 2"
    with pytest.raises(ValueError, match="reference target"):
        _validate_extraction_artifact(
            canonical_json_bytes(wrong_number),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_rejects_major_character_loss_even_when_some_text_remains() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 100
    artifact = _extraction(source_sha, preflight_sha)

    with pytest.raises(ValueError, match="lost substantial"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_rejects_major_character_loss_on_a_short_page() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 40
    artifact = _extraction(source_sha, preflight_sha)

    with pytest.raises(ValueError, match="lost substantial"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_accepts_cid_cross_parser_character_count_delta() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 100
    artifact = _extraction(source_sha, preflight_sha)
    page = artifact["pages"][0]
    page["chars"][0]["text"] = "A" * 106
    for ordinal, left in ((2, 2_100), (3, 3_200)):
        character = deepcopy(page["chars"][0])
        character["id"] = f"p0001-char-{ordinal:06d}"
        character["text"] = "(cid:3)"
        character["bbox_mpt"] = [left, 1_000, left + 900, 2_000]
        page["chars"].append(character)
    page["lines"][0]["text"] = "A" * 106 + "(cid:3)(cid:3)"
    page["lines"][0]["bbox_mpt"] = [1_000, 1_000, 4_100, 2_000]
    page["lines"][0]["character_ids"] = [item["id"] for item in page["chars"]]
    artifact["counts"]["character_count"] = 3

    validated = _validate_extraction_artifact(
        canonical_json_bytes(artifact),
        source_sha256=source_sha,
        normalized_pdf_sha256=NORMALIZED_SHA,
        preflight_sha256=preflight_sha,
        normalization_sha256=NORMALIZATION_SHA,
        expected_pages=_normalized_expected_pages(preflight),
        limits=WorkerLimits(),
    )

    assert validated == artifact


def test_real_paper_scale_of_short_cid_placeholders_stays_within_budget() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _cid_representation_budget,
    )

    characters = [{"text": "(cid:3)"} for _ in range(65)]

    assert _cid_representation_budget(characters) == 390


def test_parent_rejects_overlong_cid_placeholder_budget() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 100
    artifact = _extraction(source_sha, preflight_sha)
    placeholder = f"(cid:{'9' * 64})"
    artifact["pages"][0]["chars"][0]["text"] = placeholder
    artifact["pages"][0]["lines"][0]["text"] = placeholder

    with pytest.raises(ValueError, match="CID placeholder exceeds"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def _fill_page_with_short_cids(
    page: dict[str, object],
    *,
    page_number: int,
    count: int,
) -> None:
    template = page["chars"][0]
    characters = []
    for ordinal in range(1, count + 1):
        character = deepcopy(template)
        character["id"] = f"p{page_number:04d}-char-{ordinal:06d}"
        character["page_number"] = page_number
        character["text"] = "(cid:3)"
        character["bbox_mpt"] = [
            ordinal * 1_000,
            1_000,
            (ordinal + 1) * 1_000,
            2_000,
        ]
        characters.append(character)
    page["page_number"] = page_number
    page["chars"] = characters
    line = page["lines"][0]
    line["id"] = f"p{page_number:04d}-line-00001"
    line["page_number"] = page_number
    line["text"] = "(cid:3)" * count
    line["bbox_mpt"] = [1_000, 1_000, (count + 1) * 1_000, 2_000]
    line["character_ids"] = [character["id"] for character in characters]


def test_parent_rejects_excessive_page_cid_representation_budget() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 86
    artifact = _extraction(source_sha, preflight_sha)
    page = artifact["pages"][0]
    _fill_page_with_short_cids(page, page_number=1, count=86)
    artifact["counts"]["character_count"] = 86

    with pytest.raises(ValueError, match="page CID representation budget"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_rejects_excessive_document_cid_representation_budget() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    artifact = _extraction(source_sha, preflight_sha)
    template_preflight_page = preflight["pages"][0]
    template_extraction_page = artifact["pages"][0]
    preflight_pages = []
    extraction_pages = []
    for page_number in range(1, 6):
        preflight_page = deepcopy(template_preflight_page)
        preflight_page["page_number"] = page_number
        preflight_page["extractable_character_count"] = 70
        preflight_pages.append(preflight_page)
        extraction_page = deepcopy(template_extraction_page)
        _fill_page_with_short_cids(
            extraction_page,
            page_number=page_number,
            count=70,
        )
        extraction_pages.append(extraction_page)
    preflight["pages"] = preflight_pages
    artifact["pages"] = extraction_pages
    artifact["counts"]["page_count"] = 5
    artifact["counts"]["character_count"] = 350
    artifact["counts"]["line_count"] = 5

    with pytest.raises(ValueError, match="document CID representation budget"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_rejects_same_character_delta_without_cid_placeholders() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    preflight["pages"][0]["extractable_character_count"] = 100
    artifact = _extraction(source_sha, preflight_sha)
    artifact["pages"][0]["chars"][0]["text"] = "A" * 120
    artifact["pages"][0]["lines"][0]["text"] = "A" * 120

    with pytest.raises(ValueError, match="added substantial"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_parent_rejects_substantial_character_injection() -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _validate_extraction_artifact,
    )
    from academic_pdf_en_zh_reader.security.limits import WorkerLimits

    source_sha = "a" * 64
    preflight_sha = "b" * 64
    preflight = _preflight(source_sha, 123)
    artifact = _extraction(source_sha, preflight_sha)
    injected = "A" * 40
    artifact["pages"][0]["chars"][0]["text"] = injected
    artifact["pages"][0]["lines"][0]["text"] = injected

    with pytest.raises(ValueError, match="added substantial"):
        _validate_extraction_artifact(
            canonical_json_bytes(artifact),
            source_sha256=source_sha,
            normalized_pdf_sha256=NORMALIZED_SHA,
            preflight_sha256=preflight_sha,
            normalization_sha256=NORMALIZATION_SHA,
            expected_pages=_normalized_expected_pages(preflight),
            limits=WorkerLimits(),
        )


def test_extraction_commit_never_exposes_partial_final_after_crash(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.extraction.api import _persist_exclusive

    destination = tmp_path / "extraction.json"
    payload = b'{"artifact_kind":"extraction"}'
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_before_extraction_commit,
        args=(str(destination), payload),
    )
    process.start()
    process.join(10)
    try:
        assert not process.is_alive(), "crash probe did not exit"
        assert process.exitcode == 23
        assert not destination.exists()

        digest = _persist_exclusive(destination, payload)

        assert len(digest) == 64
        assert destination.read_bytes() == payload
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)


def test_extraction_commit_is_complete_if_process_dies_after_atomic_link(
    tmp_path: Path,
) -> None:
    from academic_pdf_en_zh_reader.extraction.api import (
        _persist_exclusive,
        _StableBridgeError,
    )

    destination = tmp_path / "extraction.json"
    payload = b'{"artifact_kind":"extraction"}'
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_after_extraction_commit,
        args=(str(destination), payload),
    )
    process.start()
    process.join(10)
    try:
        assert not process.is_alive(), "post-commit crash probe did not exit"
        assert process.exitcode == 24
        assert destination.read_bytes() == payload
        with pytest.raises(_StableBridgeError, match="ARTIFACT_EXISTS"):
            _persist_exclusive(destination, payload)
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"passed": False}, "PREFLIGHT_ARTIFACT_INVALID"),
        ({"source_sha256": "0" * 64}, "NORMALIZATION_ARTIFACT_INVALID"),
        ({"schema_version": "2.0.0"}, "PREFLIGHT_ARTIFACT_INVALID"),
    ],
)
def test_parent_api_rejects_invalid_or_unbound_preflight_before_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
    expected_code: str,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api

    source = tmp_path / "secret-paper.pdf"
    source.write_bytes(b"PRIVATE PDF")
    job = tmp_path / "job"
    job.mkdir()
    artifact = _preflight(
        hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size
    )
    artifact.update(mutation)
    (job / "preflight.json").write_bytes(canonical_json_bytes(artifact))

    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_a, **_k: pytest.fail("invalid preflight reached worker"),
    )

    outcome = api.extract_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == expected_code
    assert source.name not in str(outcome)
    assert "PRIVATE" not in str(outcome)
    assert not (job / "extraction.json").exists()


def test_parent_rejects_noncanonical_preflight_before_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"PDF")
    job = tmp_path / "job"
    job.mkdir()
    artifact = _preflight(hashlib.sha256(b"PDF").hexdigest(), 3)
    (job / "preflight.json").write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_a, **_k: pytest.fail("noncanonical preflight reached worker"),
    )

    outcome = api.extract_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == "PREFLIGHT_ARTIFACT_INVALID"


@pytest.mark.parametrize("normalization_state", ["missing", "noncanonical"])
def test_parent_rejects_missing_or_noncanonical_normalization_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    normalization_state: str,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api

    source = tmp_path / "normalized.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    job = tmp_path / "job"
    job.mkdir()
    preflight = _preflight("a" * 64, 123)
    preflight_bytes = canonical_json_bytes(preflight)
    (job / "preflight.json").write_bytes(preflight_bytes)
    if normalization_state == "noncanonical":
        preflight_sha = hashlib.sha256(preflight_bytes).hexdigest()
        normalization = _normalization(
            preflight,
            preflight_sha,
            hashlib.sha256(source.read_bytes()).hexdigest(),
            source.stat().st_size,
        )
        (job / "normalization.json").write_text(
            json.dumps(normalization, indent=2),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        api,
        "copy_untrusted_input",
        lambda *_a, **_k: pytest.fail("invalid normalization reached input copy"),
    )

    outcome = api.extract_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == "NORMALIZATION_ARTIFACT_INVALID"
    assert not (job / "extraction.json").exists()


def test_parent_rejects_actual_input_not_bound_by_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "normalized.pdf"
    source.write_bytes(b"%PDF-1.7\nactual\n%%EOF\n")
    job = tmp_path / "job"
    job.mkdir()
    preflight = _preflight("a" * 64, 123)
    preflight_bytes = canonical_json_bytes(preflight)
    (job / "preflight.json").write_bytes(preflight_bytes)
    preflight_sha = hashlib.sha256(preflight_bytes).hexdigest()
    normalization = _normalization(
        preflight,
        preflight_sha,
        "b" * 64,
        source.stat().st_size,
    )
    (job / "normalization.json").write_bytes(canonical_json_bytes(normalization))
    copied_root = tmp_path / "copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    safe_copy = SafeInputCopy(
        copied_root,
        copied,
        hashlib.sha256(copied.read_bytes()).hexdigest(),
        copied.stat().st_size,
    )
    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_a, **_k: safe_copy)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_a, **_k: pytest.fail("unbound normalized PDF reached worker"),
    )

    outcome = api.extract_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == "NORMALIZATION_ARTIFACT_INVALID"
    assert not copied_root.exists()
    assert not (job / "extraction.json").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(source_sha256="0" * 64),
        lambda item: item.update(normalized_pdf_sha256="0" * 64),
        lambda item: item.update(preflight_sha256="0" * 64),
        lambda item: item.update(normalization_sha256="0" * 64),
        lambda item: item["pages"][0].update(page_number=2),
        lambda item: item["pages"][0]["chars"][0].update(
            bbox_mpt=[1000, 1000, 2000, 2.5]
        ),
        lambda item: item["counts"].update(character_count=2),
        lambda item: item["pages"][0]["lines"][0].update(id="p0001-char-000001"),
        lambda item: item["pages"][0]["chars"][0].update(unexpected=True),
        lambda item: item["pages"][0]["chars"][0].update(
            bbox_mpt=[2000, 1000, 1000, 2000]
        ),
        lambda item: item["pages"][0].update(rotation_degrees=90),
        lambda item: item["pages"][0]["chars"][0].update(id="char-1"),
        lambda item: item["pages"][0]["lines"][0].update(
            character_ids=["p0001-char-999999"]
        ),
        lambda item: item["pages"][0]["chars"][0].update(fill_color={"r": 1}),
        lambda item: item["pages"][0]["lines"][0].update(confidence_ppm=1_000_001),
        lambda item: item["pages"][0].update(crop_box_mpt=[-1000, 0, 594276, 841890]),
        lambda item: item["pages"][0].update(media_box_mpt=[0, 0, 0, 0]),
    ],
)
def test_parent_rejects_tampered_extraction_without_persisting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    from academic_pdf_en_zh_reader.extraction import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "secret.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    job = tmp_path / "job"
    job.mkdir()
    preflight = _preflight(source_sha, source.stat().st_size)
    preflight_bytes = canonical_json_bytes(preflight)
    (job / "preflight.json").write_bytes(preflight_bytes)
    preflight_sha = hashlib.sha256(preflight_bytes).hexdigest()
    normalization = _normalization(
        preflight,
        preflight_sha,
        source_sha,
        source.stat().st_size,
    )
    normalization_bytes = canonical_json_bytes(normalization)
    (job / "normalization.json").write_bytes(normalization_bytes)
    normalization_sha = hashlib.sha256(normalization_bytes).hexdigest()
    artifact = _extraction(
        source_sha,
        preflight_sha,
        normalized_pdf_sha256=source_sha,
        normalization_sha256=normalization_sha,
    )
    mutate(artifact)
    copied_root = tmp_path / "copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    safe = SafeInputCopy(copied_root, copied, source_sha, copied.stat().st_size)
    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_a, **_k: safe)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_a, **_k: SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        ),
    )

    outcome = api.extract_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == "EXTRACTION_ARTIFACT_INVALID"
    assert not (job / "extraction.json").exists()
    assert not copied_root.exists()
