# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.storage import write_immutable_artifact
from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS


def _preflight(source_sha256: str, source_bytes: int) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "preflight",
        "source_sha256": source_sha256,
        "passed": True,
        "error_codes": [],
        "warnings": [],
        "checks": [{"id": "page-geometry", "hard_gate": True, "passed": True}],
        "pages": [
            {
                "page_number": 1,
                "width_mpt": 595_000,
                "height_mpt": 794_000,
                "media_box_mpt": [42_000, 45_000, 637_000, 839_000],
                "crop_box_mpt": [42_000, 45_000, 637_000, 839_000],
                "rotation_degrees": 0,
                "extractable_character_count": 100,
            }
        ],
        "inventory": {
            "javascript": 0,
            "open_actions": 0,
            "attachments": 0,
            "forms": 0,
            "launch_actions": 0,
            "rich_media": 0,
            "submit_actions": 0,
        },
        "limits": {
            "file_bytes": {
                "observed": source_bytes,
                "maximum": DEFAULT_LIMITS.max_input_bytes,
            },
            "page_count": {"observed": 1, "maximum": DEFAULT_LIMITS.max_pages},
            "object_count": {"observed": 10, "maximum": DEFAULT_LIMITS.max_objects},
            "recursion_depth": {
                "observed": 2,
                "maximum": DEFAULT_LIMITS.max_recursion_depth,
            },
            "decompressed_stream_bytes": {
                "observed": 0,
                "maximum": DEFAULT_LIMITS.max_uncompressed_bytes,
            },
            "image_bytes": {"observed": 0, "maximum": DEFAULT_LIMITS.max_image_bytes},
        },
    }


def _normalization(
    *, source_sha256: str, preflight_sha256: str, normalized_pdf: bytes
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "normalization",
        "policy_version": "1.0.0",
        "source_sha256": source_sha256,
        "preflight_sha256": preflight_sha256,
        "normalized_pdf_sha256": hashlib.sha256(normalized_pdf).hexdigest(),
        "normalized_pdf_bytes": len(normalized_pdf),
        "pages": [
            {
                "page_number": 1,
                "source_media_box_mpt": [42_000, 45_000, 637_000, 839_000],
                "source_crop_box_mpt": [42_000, 45_000, 637_000, 839_000],
                "source_rotation_degrees": 0,
                "displayed_width_mpt": 595_000,
                "displayed_height_mpt": 794_000,
                "scale_ppm": 1_000_000,
                "scaled_width_mpt": 595_000,
                "scaled_height_mpt": 794_000,
                "padding_left_mpt": 138,
                "padding_bottom_mpt": 23_945,
                "padding_right_mpt": 138,
                "padding_top_mpt": 23_945,
                "normalized_content_box_mpt": [138, 23_945, 595_138, 817_945],
            }
        ],
    }


def _fixture(tmp_path: Path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\nraw fixture\n%%EOF\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    job = tmp_path / "job"
    job.mkdir()
    preflight = _preflight(digest, source.stat().st_size)
    preflight_hash = write_immutable_artifact(
        job / "preflight.json", preflight, "preflight"
    )
    return source, digest, job, preflight_hash


def test_parent_api_persists_both_bound_artifacts_and_cleans_safe_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.normalization import api

    source, digest, job, preflight_hash = _fixture(tmp_path)
    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    normalized_pdf = b"%PDF-1.7\nnormalized fixture\n%%EOF\n"
    artifact = _normalization(
        source_sha256=digest,
        preflight_sha256=preflight_hash,
        normalized_pdf=normalized_pdf,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_a, **_k: safe_copy)

    def fake_run(request, workspace, *, limits):
        captured.update(request=request, workspace=workspace, limits=limits)
        return SimpleNamespace(
            normalized_pdf_bytes=normalized_pdf,
            normalization_artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        )

    monkeypatch.setattr(api, "_run_platform_worker", fake_run)

    outcome = api.normalize_untrusted_pdf(source, job)

    assert outcome["status"] == "ok"
    assert (job / "normalized-source.pdf").read_bytes() == normalized_pdf
    assert (job / "normalization.json").read_bytes() == canonical_json_bytes(artifact)
    assert captured["request"].parameters == {
        "policy_version": "1.0.0",
        "source_sha256": digest,
        "input_bytes": len(source.read_bytes()),
        "preflight_sha256": preflight_hash,
    }
    assert not copied_root.exists()


def test_parent_api_rejects_source_swap_before_worker_launch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.normalization import api

    source, _digest, job, _preflight_hash = _fixture(tmp_path)
    source.write_bytes(b"%PDF-1.7\nswapped\n%%EOF\n")
    launched = False

    def fake_run(*_args, **_kwargs):
        nonlocal launched
        launched = True

    monkeypatch.setattr(api, "_run_platform_worker", fake_run)

    outcome = api.normalize_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == "PREFLIGHT_ARTIFACT_INVALID"
    assert launched is False
    assert not (job / "normalized-source.pdf").exists()
    assert not (job / "normalization.json").exists()


def test_parent_api_rejects_tampered_dual_result_without_partial_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.normalization import api

    source, digest, job, preflight_hash = _fixture(tmp_path)
    normalized_pdf = b"%PDF-1.7\nnormalized fixture\n%%EOF\n"
    artifact = _normalization(
        source_sha256=digest,
        preflight_sha256=preflight_hash,
        normalized_pdf=normalized_pdf,
    )
    artifact["normalized_pdf_sha256"] = "f" * 64
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_a, **_k: SimpleNamespace(
            normalized_pdf_bytes=normalized_pdf,
            normalization_artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        ),
    )

    outcome = api.normalize_untrusted_pdf(source, job)

    assert outcome["error"]["code"] == "NORMALIZATION_ARTIFACT_INVALID"
    assert not (job / "normalized-source.pdf").exists()
    assert not (job / "normalization.json").exists()
