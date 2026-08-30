# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes


def _passing_artifact(source_sha256: str, source_size: int) -> dict[str, object]:
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
                "rotation_degrees": 0,
                "extractable_character_count": 12,
            }
        ],
        "limits": {
            "file_bytes": {
                "observed": source_size,
                "maximum": 100 * 1024 * 1024,
            },
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


def test_parent_api_has_no_pdf_parser_import() -> None:
    from academic_pdf_en_zh_reader.preflight import api

    tree = ast.parse(inspect.getsource(api))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not any(
        name == "pypdf"
        or name.startswith("pypdf.")
        or name == "pdfplumber"
        or name.startswith("pdfplumber.")
        for name in imported
    )


def test_parent_api_persists_only_a_valid_passing_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    artifact = _passing_artifact(digest, safe_copy.size)
    artifact_bytes = canonical_json_bytes(artifact)
    captured: dict[str, object] = {}

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)

    def fake_run(request, workspace, *, limits):
        captured["request"] = request
        captured["workspace"] = workspace
        captured["limits"] = limits
        return SimpleNamespace(
            artifact_bytes=artifact_bytes,
            provenance={"appcontainer_cleanup_verified": True},
        )

    monkeypatch.setattr(api, "_run_platform_worker", fake_run)

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")

    assert outcome["status"] == "ok"
    assert outcome["summary"]["page_count"] == 1
    assert outcome["artifact"]["name"] == "preflight.json"
    assert (tmp_path / "job" / "preflight.json").read_bytes() == artifact_bytes
    assert captured["request"].input_path == "input.pdf"
    assert captured["request"].parameters == {
        "policy_version": "1.0.0",
        "source_sha256": digest,
        "input_bytes": len(source.read_bytes()),
    }
    assert not copied_root.exists()


def test_parent_api_does_not_persist_failed_preflight(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    artifact = _passing_artifact(digest, safe_copy.size)
    artifact["passed"] = False
    artifact["error_codes"] = ["ACTIVE_CONTENT"]
    artifact["checks"][0]["passed"] = False
    artifact["checks"][0]["error_code"] = "ACTIVE_CONTENT"

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_args, **_kw: SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        ),
    )

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")

    assert outcome == {
        "status": "error",
        "error": {
            "code": "PREFLIGHT_REJECTED",
            "message": "The PDF did not pass mandatory preflight checks.",
        },
        "preflight_error_codes": ["ACTIVE_CONTENT"],
    }
    assert not (tmp_path / "job" / "preflight.json").exists()
    assert not copied_root.exists()


def test_parent_api_rejects_zero_byte_safe_copy_stably(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(b"")
    safe_copy = SafeInputCopy(copied_root, copied, hashlib.sha256(b"").hexdigest(), 0)
    called = False

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)

    def forbidden_worker(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("zero-byte input reached the worker")

    monkeypatch.setattr(api, "_run_platform_worker", forbidden_worker)

    outcome = api.preflight_untrusted_pdf(tmp_path / "empty.pdf", tmp_path / "job")

    assert outcome["error"]["code"] == "INPUT_REJECTED"
    assert called is False
    assert not copied_root.exists()
    assert not (tmp_path / "job" / "preflight.json").exists()


@pytest.mark.parametrize(
    ("limit_name", "bad_maximum"),
    [
        ("file_bytes", 1),
        ("page_count", 499),
        ("object_count", 249_999),
        ("recursion_depth", 63),
        ("decompressed_stream_bytes", 1024 * 1024 * 1024 - 1),
        ("image_bytes", 512 * 1024 * 1024 - 1),
    ],
)
def test_parent_api_rejects_any_limit_policy_mismatch(
    tmp_path: Path,
    monkeypatch,
    limit_name: str,
    bad_maximum: int,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied_root = tmp_path / f"private-{limit_name}"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    artifact = _passing_artifact(digest, safe_copy.size)
    artifact["limits"][limit_name]["maximum"] = bad_maximum

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_args, **_kw: SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        ),
    )

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")

    assert outcome["error"]["code"] == "PREFLIGHT_ARTIFACT_INVALID"
    assert not (tmp_path / "job" / "preflight.json").exists()


@pytest.mark.parametrize(
    ("page_numbers", "observed_count"),
    [([2], 1), ([1, 3], 2), ([1], 2)],
)
def test_parent_api_rejects_page_count_or_sequence_mismatch(
    tmp_path: Path,
    monkeypatch,
    page_numbers: list[int],
    observed_count: int,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied_root = tmp_path / "private-pages"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    artifact = _passing_artifact(digest, safe_copy.size)
    base_page = artifact["pages"][0]
    artifact["pages"] = [
        {**base_page, "page_number": number} for number in page_numbers
    ]
    artifact["limits"]["page_count"]["observed"] = observed_count

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_args, **_kw: SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        ),
    )

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")

    assert outcome["error"]["code"] == "PREFLIGHT_ARTIFACT_INVALID"
    assert not (tmp_path / "job" / "preflight.json").exists()


def test_parent_api_rejects_tampered_artifact_without_path_or_content_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "secret-paper-name.pdf"
    source.write_bytes(b"TOP SECRET PDF CONTENT")
    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    artifact = _passing_artifact("0" * 64, safe_copy.size)

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_args, **_kw: SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": True},
        ),
    )

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")
    serialized = str(outcome)

    assert outcome["error"]["code"] == "PREFLIGHT_ARTIFACT_INVALID"
    assert source.name not in serialized
    assert "TOP SECRET" not in serialized
    assert not (tmp_path / "job" / "preflight.json").exists()
    assert not copied_root.exists()


def test_parent_api_requires_verified_appcontainer_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)
    artifact = _passing_artifact(digest, safe_copy.size)

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)
    monkeypatch.setattr(
        api,
        "_run_platform_worker",
        lambda *_args, **_kw: SimpleNamespace(
            artifact_bytes=canonical_json_bytes(artifact),
            provenance={"appcontainer_cleanup_verified": False},
        ),
    )

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")

    assert outcome["error"]["code"] == "SANDBOX_CONTRACT_UNVERIFIED"
    assert not (tmp_path / "job" / "preflight.json").exists()
    assert not copied_root.exists()


def test_parent_api_maps_unexpected_worker_failure_without_leaking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import api
    from academic_pdf_en_zh_reader.security.input_copy import SafeInputCopy

    source = tmp_path / "secret-paper.pdf"
    source.write_bytes(b"PRIVATE CONTENT")
    copied_root = tmp_path / "private-copy"
    copied_root.mkdir()
    copied = copied_root / "input.pdf"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    safe_copy = SafeInputCopy(copied_root, copied, digest, copied.stat().st_size)

    monkeypatch.setattr(api, "copy_untrusted_input", lambda *_args, **_kw: safe_copy)

    def fail_worker(*_args, **_kwargs):
        raise RuntimeError(f"parser failed at {source}: PRIVATE CONTENT")

    monkeypatch.setattr(api, "_run_platform_worker", fail_worker)

    outcome = api.preflight_untrusted_pdf(source, tmp_path / "job")
    serialized = str(outcome)

    assert outcome["error"]["code"] == "WORKER_FAILED"
    assert source.name not in serialized
    assert "PRIVATE CONTENT" not in serialized
    assert not copied_root.exists()
    assert not (tmp_path / "job" / "preflight.json").exists()
