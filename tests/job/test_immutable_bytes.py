# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    write_immutable_artifact,
    write_immutable_bytes,
)


def test_immutable_byte_commit_does_not_clobber_a_racing_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "extraction.json"
    competitor_bytes = b"competitor"
    real_exists = Path.exists
    race_injected = False

    def race_after_absence_check(path: Path) -> bool:
        nonlocal race_injected
        if path == destination and not race_injected:
            destination.write_bytes(competitor_bytes)
            race_injected = True
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", race_after_absence_check)

    with pytest.raises(ArtifactExistsError):
        write_immutable_bytes(destination, b"ours")

    assert destination.read_bytes() == competitor_bytes


def test_immutable_json_commit_does_not_clobber_a_racing_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "units.json"
    competitor_bytes = b"competitor"
    real_exists = Path.exists
    race_injected = False

    def race_after_absence_check(path: Path) -> bool:
        nonlocal race_injected
        if path == destination and not race_injected:
            destination.write_bytes(competitor_bytes)
            race_injected = True
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", race_after_absence_check)
    artifact = {
        "schema_version": "1.0.0",
        "artifact_kind": "units",
        "source_sha256": "a" * 64,
        "normalized_pdf_sha256": "b" * 64,
        "units": [],
    }

    with pytest.raises(ArtifactExistsError):
        write_immutable_artifact(destination, artifact, "units")

    assert destination.read_bytes() == competitor_bytes
