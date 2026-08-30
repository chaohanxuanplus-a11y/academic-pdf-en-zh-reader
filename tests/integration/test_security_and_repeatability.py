# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.compose import compose_bilingual_pdf

from .conftest import build_case


def _unexpected_effect(*_args, **_kwargs):
    raise AssertionError("paper text attempted a forbidden external side effect")


def test_prompt_injection_is_inert_paper_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempted_paths: list[Path] = []
    real_path_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.name == "NEVER_READ_PROMPT_SECRET.txt":
            attempted_paths.append(path)
            raise AssertionError("paper text attempted an arbitrary path read")
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(socket, "create_connection", _unexpected_effect)
    monkeypatch.setattr(subprocess, "run", _unexpected_effect)
    monkeypatch.setattr(subprocess, "Popen", _unexpected_effect)
    monkeypatch.setattr(os, "system", _unexpected_effect)

    case = build_case(
        tmp_path,
        "prompt-injection",
        translation_overrides={
            "p1-body-1": "论文中的命令式字符串仅是待翻译数据，不构成任何操作指令。"
        },
    )

    source_text = " ".join(unit["source_text"] for unit in case.units["units"])
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in source_text
    assert "example.invalid" in source_text
    assert "NEVER_READ_PROMPT_SECRET.txt" in source_text
    assert attempted_paths == []


def _compose_case(case, job_root: Path) -> tuple[bytes, dict[str, object]]:
    job_root.mkdir()
    output_path = job_root / "candidate.pdf"
    manifest_path = job_root / "render-manifest.json"
    compose_bilingual_pdf(
        source_pdf_path=case.source_pdf_path,
        source=case.source,
        units=case.units,
        translation=case.translation,
        review=case.review,
        annotations=case.annotations,
        frame_graph=case.frame_graph,
        layout=case.layout,
        finalization_receipt=case.receipt,
        policy_inputs=case.policy_inputs,
        overlay_plan=case.overlay_plan,
        expected_finalization_receipt_hash=sha256_canonical(case.receipt),
        expected_overlay_plan_hash=str(case.overlay_plan["overlay_plan_hash"]),
        job_root=job_root,
        output_pdf_path=output_path,
        render_manifest_path=manifest_path,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return output_path.read_bytes(), manifest


def test_frozen_inputs_repeat_layout_geometry_and_pdf_bytes(tmp_path: Path) -> None:
    first = build_case(tmp_path / "first-input", "first-page-mixed")
    second = build_case(tmp_path / "second-input", "first-page-mixed")

    assert first.source_pdf_path.read_bytes() == second.source_pdf_path.read_bytes()
    assert sha256_canonical(first.frame_graph) == sha256_canonical(second.frame_graph)
    assert sha256_canonical(first.layout) == sha256_canonical(second.layout)
    assert first.layout["pages"] == second.layout["pages"]
    assert (
        first.overlay_plan["overlay_plan_hash"]
        == second.overlay_plan["overlay_plan_hash"]
    )

    first_pdf, first_manifest = _compose_case(first, tmp_path / "first-job")
    second_pdf, second_manifest = _compose_case(second, tmp_path / "second-job")
    assert first_pdf == second_pdf
    assert first_manifest == second_manifest
