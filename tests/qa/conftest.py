# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical

from ..rendering.test_final_composition import _compose


def _qa_fixture(
    tmp_path: Path,
    *,
    continuation: bool = False,
    rotation_degrees: int = 0,
    crop_inset_mpt: int = 0,
) -> dict[str, object]:
    (
        _result,
        output_pdf,
        manifest_path,
        artifacts,
        overlay_plan,
        policy_inputs,
        receipt,
    ) = _compose(
        tmp_path,
        continuation=continuation,
        rotation_degrees=rotation_degrees,
        crop_inset_mpt=crop_inset_mpt,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "source_pdf_path": tmp_path / "source.pdf",
        "output_pdf_path": output_pdf,
        "source": artifacts[0],
        "units": artifacts[1],
        "translation": artifacts[2],
        "review": artifacts[3],
        "annotations": artifacts[4],
        "frame_graph": artifacts[5],
        "layout": artifacts[6],
        "finalization_receipt": receipt,
        "policy_inputs": policy_inputs,
        "overlay_plan": overlay_plan,
        "render_manifest": manifest,
        "expected_render_manifest_hash": sha256_canonical(manifest),
    }


@pytest.fixture
def composed_qa_fixture(tmp_path: Path) -> dict[str, object]:
    return _qa_fixture(tmp_path)


@pytest.fixture
def composed_qa_continuation_fixture(tmp_path: Path) -> dict[str, object]:
    return _qa_fixture(tmp_path, continuation=True)


@pytest.fixture(params=((90, 0), (0, 10_000)))
def composed_qa_source_geometry_fixture(
    tmp_path: Path, request: pytest.FixtureRequest
) -> dict[str, object]:
    rotation, crop_inset = request.param
    return _qa_fixture(
        tmp_path,
        rotation_degrees=rotation,
        crop_inset_mpt=crop_inset,
    )
