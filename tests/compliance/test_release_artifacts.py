# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import pytest

from scripts import build_release_artifacts as artifact_module
from scripts import check_release_readiness as readiness_module
from scripts.build_release_artifacts import COMMON_PATHS, RUNTIME_SCRIPTS, build
from scripts.check_release_readiness import (
    MAX_PUBLIC_EVIDENCE_BYTES,
    assess_release_readiness,
)

PUBLIC_EVIDENCE_DIRECTORY = "compliance/evidence/public"
DIRECTORIES = {"LICENSES", "assets", "references", "src", PUBLIC_EVIDENCE_DIRECTORY}
CANONICAL_URL = "https://github.com/acme-research/academic-pdf-en-zh-reader"
VERIFIED_ON = "2026-08-28"
SYNTHETIC_HEAD_SHA = "a" * 40
SYNTHETIC_RUN_ID = "123456789"
SYNTHETIC_REPOSITORY = "acme-research/academic-pdf-en-zh-reader"
SYNTHETIC_RUN_URL = (
    f"https://github.com/{SYNTHETIC_REPOSITORY}/actions/runs/{SYNTHETIC_RUN_ID}"
)
CHANNEL_REFERENCES = {
    "security_private_channel_unconfigured": "mailto:security@hanhai-materials.org",
}
CHANNEL_VERIFICATION_REFERENCES = {
    blocker_id: f"{PUBLIC_EVIDENCE_DIRECTORY}/{blocker_id}.md"
    for blocker_id in CHANNEL_REFERENCES
}
LOCAL_EVIDENCE_REFERENCES = {
    "maintainer_identity_unverified": (
        f"{PUBLIC_EVIDENCE_DIRECTORY}/maintainer-identity.md"
    ),
    "brand_asset_rights_unverified": f"{PUBLIC_EVIDENCE_DIRECTORY}/brand-rights.md",
}
BRAND_COPYRIGHT_REFERENCE = f"{PUBLIC_EVIDENCE_DIRECTORY}/brand-copyright.md"
BRAND_TRADEMARK_REFERENCE = f"{PUBLIC_EVIDENCE_DIRECTORY}/brand-trademark.md"


def _write(path: Path, content: str | bytes = "test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


def _commit_synthetic_tree(root: Path) -> None:
    arguments = ["git", "-C", str(root), "-c", "core.autocrlf=false"]
    subprocess.run([*arguments, "add", "--all"], check=True)
    subprocess.run(
        [
            *arguments,
            "-c",
            "user.name=Synthetic Release Test",
            "-c",
            "user.email=release-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Synthetic release fixture",
        ],
        check=True,
    )


@pytest.fixture(autouse=True)
def _trusted_synthetic_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readiness_module,
        "_git_head_sha",
        lambda _root: SYNTHETIC_HEAD_SHA,
    )
    environment = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": SYNTHETIC_REPOSITORY,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": SYNTHETIC_RUN_ID,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_SHA": SYNTHETIC_HEAD_SHA,
        "GITHUB_WORKFLOW_REF": (
            f"{SYNTHETIC_REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
        ),
        "RELEASE_LPAC_GATE_RESULT": "success",
        "RELEASE_LPAC_SIGNAL_SCHEMA": "1",
        "RELEASE_LPAC_PROBE_OUTCOME": "success",
        "RELEASE_LPAC_FINISH_OUTCOME": "success",
        "RELEASE_LPAC_TESTED_HEAD_SHA": SYNTHETIC_HEAD_SHA,
        "RELEASE_LPAC_RUN_ID": SYNTHETIC_RUN_ID,
        "RELEASE_LPAC_RUN_ATTEMPT": "1",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


def _local_evidence_references(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(
            *(_local_evidence_references(item) for item in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_local_evidence_references(item) for item in value))
    if isinstance(value, str) and value.startswith(f"{PUBLIC_EVIDENCE_DIRECTORY}/"):
        return {value}
    return set()


def _ready_status() -> dict[str, object]:
    references = {
        **LOCAL_EVIDENCE_REFERENCES,
        **CHANNEL_REFERENCES,
        "canonical_repository_url_unconfigured": CANONICAL_URL,
    }
    blockers = []
    for blocker_id in (
        "maintainer_identity_unverified",
        "security_private_channel_unconfigured",
        "canonical_repository_url_unconfigured",
        "brand_asset_rights_unverified",
        "windows_lpac_production_path_unverified",
    ):
        if blocker_id == "windows_lpac_production_path_unverified":
            evidence: dict[str, object] = {
                "conclusion": "success",
                "head_sha": SYNTHETIC_HEAD_SHA,
                "job": "windows-2025-production-gate",
                "run_url": SYNTHETIC_RUN_URL,
                "verified_on": VERIFIED_ON,
                "workflow": ".github/workflows/release.yml",
            }
        else:
            evidence = {
                "reference": references[blocker_id],
                "verified_on": VERIFIED_ON,
            }
        if blocker_id in CHANNEL_REFERENCES:
            evidence["private"] = True
            evidence["verification_reference"] = CHANNEL_VERIFICATION_REFERENCES[
                blocker_id
            ]
        blockers.append({"id": blocker_id, "resolved": True, "evidence": evidence})
    return {"schema_version": 1, "state": "PUBLIC_RELEASE_READY", "blockers": blockers}


def _synthetic_ready_tree(root: Path) -> None:
    for entry in COMMON_PATHS:
        path = root / entry
        if entry in DIRECTORIES:
            path.mkdir(parents=True, exist_ok=True)
            if entry != PUBLIC_EVIDENCE_DIRECTORY:
                _write(path / "placeholder.txt")
        else:
            _write(path)
    for script in RUNTIME_SCRIPTS:
        _write(root / script)
    _write(root / "README.md", "[Disclaimer](DISCLAIMER.md)\n")
    _write(root / "SKILL.md", "[Disclaimer](DISCLAIMER.md)\n")
    _write(root / "PRIVACY.md", "[Disclaimer](DISCLAIMER.md)\n")
    _write(
        root / "references" / "product-contract.md",
        "[Disclaimer](../DISCLAIMER.md)\n",
    )
    _write(root / "references" / "runbook.md", "Synthetic release runbook\n")
    _write(root / "agents" / "openai.yaml")
    _write(root / "DISCLAIMER.md", "Synthetic disclaimer\n")

    font_bytes = b"synthetic-font"
    font_path = root / "assets" / "fonts" / "Synthetic-Regular.ttf"
    _write(font_path, font_bytes)
    font_record = {
        "path": "assets/fonts/Synthetic-Regular.ttf",
        "sha256": hashlib.sha256(font_bytes).hexdigest(),
        "size": len(font_bytes),
        "spdx_license": "OFL-1.1",
        "source_url": "https://example.invalid/not-fetched",
    }
    _write(
        root / "assets" / "font-manifest.json",
        json.dumps({"fonts": [font_record]}),
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", f"{CANONICAL_URL}.git"],
        check=True,
    )
    canonical_url = CANONICAL_URL
    _write(
        root / "pyproject.toml",
        '[project]\nname = "academic-pdf-en-zh-reader"\nversion = "0.1.0"\n',
    )
    _write(
        root / "src" / "academic_pdf_en_zh_reader" / "version.py",
        '__version__ = "0.1.0"\n',
    )
    _write(
        root / "CITATION.cff",
        "cff-version: 1.2.0\n"
        'title: "academic-pdf-en-zh-reader"\n'
        "type: software\n"
        'version: "0.1.0"\n'
        f'repository-code: "{canonical_url}"\n'
        'date-released: "2026-08-28"\n',
    )
    _write(root / "README.md", f"[Disclaimer](DISCLAIMER.md)\n{canonical_url}\n")
    security_channel = CHANNEL_REFERENCES["security_private_channel_unconfigured"]
    _write(
        root / "CODE_OF_CONDUCT.md",
        f"Shared private reporting route: {security_channel}\n",
    )
    _write(
        root / "SECURITY.md",
        f"Private security route: {security_channel}\n",
    )
    evidence_records = {
        LOCAL_EVIDENCE_REFERENCES["maintainer_identity_unverified"]: (
            "maintainer_identity_unverified"
        ),
        LOCAL_EVIDENCE_REFERENCES["brand_asset_rights_unverified"]: (
            "brand_asset_rights_unverified"
        ),
        BRAND_COPYRIGHT_REFERENCE: "brand_asset_rights_unverified",
        BRAND_TRADEMARK_REFERENCE: "brand_asset_rights_unverified",
        **{
            reference: blocker_id
            for blocker_id, reference in CHANNEL_VERIFICATION_REFERENCES.items()
        },
    }
    for relative, blocker_id in evidence_records.items():
        _write(
            root / relative,
            f"# Synthetic review record\n\nEvidence-ID: {blocker_id}\n",
        )
    brand_bytes = b"synthetic-brand"
    brand_path = root / "assets" / "branding" / "hanhai-wencai.png"
    _write(brand_path, brand_bytes)
    project_identity = {
        "schema_version": 1,
        "brand": {
            "display_name_zh": "瀚海问材",
            "display_name_en": "Hanhai Materials",
            "skill_name": "academic-pdf-en-zh-reader",
        },
        "repository": {
            "canonical_url": canonical_url,
            "development_display": "GitHub：公开发布后提供",
        },
        "asset": {
            "path": "assets/branding/hanhai-wencai.png",
            "sha256": hashlib.sha256(brand_bytes).hexdigest(),
            "size": len(brand_bytes),
            "spdx_license": "Apache-2.0",
            "rights_status": "verified",
            "evidence": {
                "reference": LOCAL_EVIDENCE_REFERENCES["brand_asset_rights_unverified"],
                "verified_on": VERIFIED_ON,
            },
            "copyright": {
                "ownership_status": "verified",
                "public_redistribution_authorized": True,
                "spdx_copyright_text": "Copyright 2026 Synthetic Brand Owner",
                "spdx_license": "Apache-2.0",
                "evidence": {
                    "reference": BRAND_COPYRIGHT_REFERENCE,
                    "verified_on": VERIFIED_ON,
                },
            },
            "trademark": {
                "status": "verified",
                "public_use_authorized": True,
                "evidence": {
                    "reference": BRAND_TRADEMARK_REFERENCE,
                    "verified_on": VERIFIED_ON,
                },
            },
        },
    }
    brand_manifest = {
        "schema_version": 1,
        "brand_name_zh": project_identity["brand"]["display_name_zh"],
        "brand_name_en": project_identity["brand"]["display_name_en"],
        "skill_name": project_identity["brand"]["skill_name"],
        "github_display": project_identity["repository"]["canonical_url"],
        "disclaimer_zh_short": "synthetic disclaimer",
        "image": {
            "path": project_identity["asset"]["path"],
            "sha256": project_identity["asset"]["sha256"],
            "size": project_identity["asset"]["size"],
            "width_px": 1,
            "height_px": 1,
            "mode": "RGBA",
        },
        "layout": {},
    }
    project_identity["output_disclaimer"] = {
        "zh_short": brand_manifest["disclaimer_zh_short"]
    }
    _write(
        root / "compliance" / "project-identity.json",
        json.dumps(project_identity, ensure_ascii=False),
    )
    _write(
        root / "assets" / "branding" / "brand-manifest.json",
        json.dumps(brand_manifest, ensure_ascii=False),
    )
    _write(
        root / "compliance" / "release-status.json",
        json.dumps(_ready_status()),
    )
    _write(
        root / "REUSE.toml",
        "version = 1\n\n"
        "[[annotations]]\n"
        'path = "assets/branding/hanhai-wencai.png"\n'
        'precedence = "override"\n'
        'SPDX-FileCopyrightText = "Copyright 2026 Synthetic Brand Owner"\n'
        'SPDX-License-Identifier = "Apache-2.0"\n',
    )
    _write(root / "LICENSES" / "Apache-2.0.txt", "Synthetic Apache text\n")
    _write(
        root / "THIRD_PARTY_NOTICES.md",
        "Synthetic-Regular.ttf\n"
        "Copyright 2026 Synthetic Brand Owner\n"
        "Apache-2.0\n"
        f"Trademark public use verified by {BRAND_TRADEMARK_REFERENCE}.\n",
    )
    _commit_synthetic_tree(root)


def _synthetic_blocked_tree(root: Path) -> None:
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["state"] = "PUBLIC_RELEASE_BLOCKED"
    maintainer = next(
        item
        for item in status["blockers"]
        if item["id"] == "maintainer_identity_unverified"
    )
    maintainer["resolved"] = False
    maintainer["evidence"] = None
    _write(status_path, json.dumps(status))
    _write(
        root / "CITATION.cff",
        "cff-version: 1.2.0\n"
        'title: "academic-pdf-en-zh-reader"\n'
        "type: software\n"
        'version: "0.1.0"\n',
    )
    manifest_path = root / "assets" / "branding" / "brand-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["github_display"] = "GitHub：公开发布后提供"
    _write(manifest_path, json.dumps(manifest, ensure_ascii=False))


def test_blocked_tree_fails_closed_before_writing_release_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_blocked_tree(root)
    output = root / "dist" / "release-policy-test"
    assert not output.exists()
    with pytest.raises(ValueError, match="release readiness is blocked"):
        build(root, output)
    assert not output.exists()


def test_current_tree_records_declared_windows_lpac_production_state() -> None:
    status = json.loads(
        (
            Path(__file__).resolve().parents[2] / "compliance" / "release-status.json"
        ).read_text(encoding="utf-8")
    )
    blocker = next(
        item
        for item in status["blockers"]
        if item["id"] == "windows_lpac_production_path_unverified"
    )

    if status["state"] == "PUBLIC_RELEASE_READY":
        assert blocker["resolved"] is True
        assert isinstance(blocker["evidence"], dict)
        assert set(blocker["evidence"]) == readiness_module.WINDOWS_LPAC_EVIDENCE_FIELDS
        assert blocker["evidence"]["conclusion"] == "success"
        current = assess_release_readiness(
            Path(__file__).resolve().parents[2], mode="current"
        )
        assert current.ok, current.errors
    else:
        assert blocker == {
            "id": "windows_lpac_production_path_unverified",
            "resolved": False,
            "evidence": None,
        }


def test_current_mode_follows_declared_ready_state(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)

    current = assess_release_readiness(root, mode="current")
    release = assess_release_readiness(root, mode="release")
    development = assess_release_readiness(root, mode="development")

    assert current.ok, current.errors
    assert release.ok, release.errors
    assert not development.ok


def test_current_ready_mode_does_not_require_release_workflow_live_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    monkeypatch.delenv("GITHUB_ACTIONS")

    current = assess_release_readiness(root, mode="current")
    release = assess_release_readiness(root, mode="release")

    assert current.ok, current.errors
    assert not release.ok
    assert any("live GitHub Actions LPAC gate" in error for error in release.errors)


@pytest.mark.parametrize(
    "reference",
    (
        "https://github.com/acme-research/academic-pdf-en-zh-reader/actions/runs/1",
        f"{PUBLIC_EVIDENCE_DIRECTORY}/windows-lpac-self-assertion.md",
    ),
)
def test_windows_lpac_cannot_be_resolved_by_a_generic_reference(
    tmp_path: Path, reference: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    if reference.endswith(".md"):
        _write(
            root / reference,
            "Evidence-ID: windows_lpac_production_path_unverified\n",
        )
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    blocker = next(
        item
        for item in status["blockers"]
        if item["id"] == "windows_lpac_production_path_unverified"
    )
    blocker["evidence"] = {
        "reference": reference,
        "verified_on": VERIFIED_ON,
    }
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("complete structured audit record" in error for error in result.errors)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("job", "renamed-gate", "evidence job is invalid"),
        ("workflow", ".github/workflows/ci.yml", "evidence workflow is invalid"),
        ("conclusion", "failure", "conclusion must be success"),
        ("head_sha", "short", "full commit SHA"),
        (
            "run_url",
            "https://github.com/acme-research/other/actions/runs/123456789",
            "run URL is invalid",
        ),
    ),
)
def test_windows_lpac_audit_record_fields_are_fixed(
    tmp_path: Path, field: str, value: str, expected: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    blocker = next(
        item
        for item in status["blockers"]
        if item["id"] == "windows_lpac_production_path_unverified"
    )
    blocker["evidence"][field] = value
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any(expected in error for error in result.errors)


@pytest.mark.parametrize(
    "variable",
    (
        "GITHUB_ACTIONS",
        "GITHUB_EVENT_NAME",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_RUN_ID",
        "GITHUB_SERVER_URL",
        "GITHUB_SHA",
        "GITHUB_WORKFLOW_REF",
        "RELEASE_LPAC_GATE_RESULT",
        "RELEASE_LPAC_SIGNAL_SCHEMA",
        "RELEASE_LPAC_PROBE_OUTCOME",
        "RELEASE_LPAC_FINISH_OUTCOME",
        "RELEASE_LPAC_TESTED_HEAD_SHA",
        "RELEASE_LPAC_RUN_ID",
        "RELEASE_LPAC_RUN_ATTEMPT",
    ),
)
def test_release_mode_requires_each_trusted_live_gate_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    monkeypatch.delenv(variable)

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert result.errors


@pytest.mark.parametrize(
    ("variable", "value"),
    (
        ("RELEASE_LPAC_SIGNAL_SCHEMA", "2"),
        ("RELEASE_LPAC_GATE_RESULT", "failure"),
        ("RELEASE_LPAC_PROBE_OUTCOME", "skipped"),
        ("RELEASE_LPAC_FINISH_OUTCOME", "failure"),
        ("RELEASE_LPAC_TESTED_HEAD_SHA", "b" * 40),
        ("RELEASE_LPAC_RUN_ID", "01"),
        ("RELEASE_LPAC_RUN_ATTEMPT", "0"),
    ),
)
def test_release_mode_rejects_forged_live_gate_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    monkeypatch.setenv(variable, value)

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert result.errors


def test_prior_lpac_audit_does_not_create_a_current_run_self_reference(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    blocker = next(
        item
        for item in status["blockers"]
        if item["id"] == "windows_lpac_production_path_unverified"
    )
    blocker["evidence"]["head_sha"] = "b" * 40
    blocker["evidence"]["run_url"] = (
        f"https://github.com/{SYNTHETIC_REPOSITORY}/actions/runs/987654321"
    )
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert result.ok, result.errors


def test_windows_lpac_head_sha_must_match_release_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    monkeypatch.setattr(readiness_module, "_git_head_sha", lambda _root: "b" * 40)

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("differs from the release source" in error for error in result.errors)


def test_blocked_state_allows_independently_resolved_blockers(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_blocked_tree(root)

    current = assess_release_readiness(root, mode="current")
    development = assess_release_readiness(root, mode="development")

    assert current.ok, current.errors
    assert development.ok, development.errors


def test_blocked_state_requires_at_least_one_unresolved_blocker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_blocked_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = _ready_status()
    status["state"] = "PUBLIC_RELEASE_BLOCKED"
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="current")

    assert not result.ok
    assert any("at least one unresolved blocker" in error for error in result.errors)


def test_future_evidence_date_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["blockers"][0]["evidence"]["verified_on"] = "9999-12-31"
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("verification date" in error for error in result.errors)


def test_verification_date_uses_the_fixed_shanghai_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is readiness_module.SHANGHAI_TIMEZONE
            return datetime(2026, 8, 31, 0, 30, tzinfo=tz)

    monkeypatch.setattr(readiness_module, "datetime", FrozenDateTime)

    assert readiness_module._valid_verification_date("2026-08-31")
    assert not readiness_module._valid_verification_date("2026-09-01")


@pytest.mark.parametrize(
    "invalid_reference",
    (
        "https://example.com/private-report",
        "https://placeholder.invalid/private-report",
        "https://user@hanhai-materials.org/private-report",
        "https://hanhai-materials.org/private-report?token=secret",
        "https://hanhai-materials.org./private-report",
        "verified-record-primary",
        "mailto:tbd@hanhai-materials.org",
        "mailto:%74%62%64@hanhai-materials.org",
        "mailto:.conduct@hanhai-materials.org",
        "mailto:conduct..team@hanhai-materials.org",
        "mailto:conduct@corp.internal",
    ),
)
def test_placeholder_private_route_fails_even_when_published(
    tmp_path: Path, invalid_reference: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    channel = next(
        item
        for item in status["blockers"]
        if item["id"] == "security_private_channel_unconfigured"
    )
    channel["evidence"]["reference"] = invalid_reference
    _write(status_path, json.dumps(status))
    for relative in ("CODE_OF_CONDUCT.md", "SECURITY.md"):
        _write(
            root / relative, f"Shared private reporting route: {invalid_reference}\n"
        )

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("routable" in error for error in result.errors)


@pytest.mark.parametrize("invalid_date", ("not-a-date", "2026-02-30", " 2026-08-28"))
def test_invalid_evidence_date_fails_closed(tmp_path: Path, invalid_date: str) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["blockers"][0]["evidence"]["verified_on"] = invalid_date
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("verification date" in error for error in result.errors)


def test_personal_project_accepts_one_shared_private_reporting_channel(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert {item["id"] for item in status["blockers"]} == {
        "maintainer_identity_unverified",
        "security_private_channel_unconfigured",
        "canonical_repository_url_unconfigured",
        "brand_asset_rights_unverified",
        "windows_lpac_production_path_unverified",
    }
    assert len(CHANNEL_REFERENCES) == 1

    result = assess_release_readiness(root, mode="release")

    assert result.ok, result.errors


@pytest.mark.parametrize("relative", ("CODE_OF_CONDUCT.md", "SECURITY.md"))
def test_shared_private_channel_must_be_published_in_both_policies(
    tmp_path: Path, relative: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(root / relative, "Contact information omitted.\n")

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any(
        f"{relative} does not publish the verified" in error for error in result.errors
    )


@pytest.mark.parametrize("private", (False, None))
def test_shared_reporting_channel_must_remain_private(
    tmp_path: Path, private: bool | None
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    channel = next(
        item
        for item in status["blockers"]
        if item["id"] == "security_private_channel_unconfigured"
    )
    if private is None:
        channel["evidence"].pop("private")
    else:
        channel["evidence"]["private"] = private
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("reporting channel must be private" in error for error in result.errors)


@pytest.mark.parametrize(
    "blocker_id",
    (
        "maintainer_identity_unverified",
        "security_private_channel_unconfigured",
        "brand_asset_rights_unverified",
        "windows_lpac_production_path_unverified",
    ),
)
def test_personal_release_policy_preserves_required_evidence_gates(
    tmp_path: Path, blocker_id: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    blocker = next(item for item in status["blockers"] if item["id"] == blocker_id)
    blocker["resolved"] = False
    blocker["evidence"] = None
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert f"release blocker remains unresolved: {blocker_id}" in result.errors


def test_private_channel_requires_a_public_verification_record(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    channel = next(
        item
        for item in status["blockers"]
        if item["id"] == "security_private_channel_unconfigured"
    )
    channel["evidence"].pop("verification_reference")
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("verification_reference" in error for error in result.errors)


def test_private_channel_verification_record_must_match_its_blocker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    verification_reference = CHANNEL_VERIFICATION_REFERENCES[
        "security_private_channel_unconfigured"
    ]
    _write(root / verification_reference, "Evidence-ID: wrong-blocker\n")

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("verification_reference" in error for error in result.errors)


@pytest.mark.parametrize("record_content", (b"", b"Evidence-ID: wrong-blocker\n"))
def test_public_evidence_record_must_be_nonempty_and_match_the_blocker(
    tmp_path: Path, record_content: bytes
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    record = root / LOCAL_EVIDENCE_REFERENCES["maintainer_identity_unverified"]
    _write(record, record_content)

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("reviewable evidence" in error for error in result.errors)


def test_public_evidence_record_rejects_invalid_utf8_and_excessive_size(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    record = root / LOCAL_EVIDENCE_REFERENCES["maintainer_identity_unverified"]

    _write(record, b"\xff\xfe")
    invalid_utf8 = assess_release_readiness(root, mode="release")
    assert not invalid_utf8.ok

    _write(record, b"x" * (MAX_PUBLIC_EVIDENCE_BYTES + 1))
    oversized = assess_release_readiness(root, mode="release")
    assert not oversized.ok


@pytest.mark.parametrize("invalid_reference", ("README.md", "\x00"))
def test_local_evidence_path_is_scoped_and_malformed_values_fail_structurally(
    tmp_path: Path, invalid_reference: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    maintainer = next(
        item
        for item in status["blockers"]
        if item["id"] == "maintainer_identity_unverified"
    )
    maintainer["evidence"]["reference"] = invalid_reference
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("reviewable evidence" in error for error in result.errors)


def test_missing_repository_evidence_record_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    status_path = root / "compliance" / "release-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    maintainer = next(
        item
        for item in status["blockers"]
        if item["id"] == "maintainer_identity_unverified"
    )
    maintainer["evidence"]["reference"] = f"{PUBLIC_EVIDENCE_DIRECTORY}/missing.md"
    _write(status_path, json.dumps(status))

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("reviewable evidence" in error for error in result.errors)


def test_canonical_url_must_match_normalized_origin(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "set-url",
            "origin",
            "https://github.com/other-owner/academic-pdf-en-zh-reader.git",
        ],
        check=True,
    )

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert any("remote.origin.url" in error for error in result.errors)


def test_ready_tree_builds_repeatable_notice_complete_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    referenced_evidence = set()
    for relative in (
        "compliance/release-status.json",
        "compliance/project-identity.json",
    ):
        referenced_evidence.update(
            _local_evidence_references(
                json.loads((root / relative).read_text(encoding="utf-8"))
            )
        )
    assert referenced_evidence

    first = build(root, root / "dist" / "first")
    second = build(root, root / "dist" / "second")
    assert [path.name for path in first] == [
        "academic-pdf-en-zh-reader-source.zip",
        "academic-pdf-en-zh-reader-skill.zip",
        "asset-manifest.json",
    ]
    assert [_hash(path) for path in first] == [_hash(path) for path in second]

    for archive_path in first[:2]:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            notice = archive.read("academic-pdf-en-zh-reader/NOTICE")
            assert notice == (root / "NOTICE").read_bytes()
            disclaimer = archive.read("academic-pdf-en-zh-reader/DISCLAIMER.md")
            assert disclaimer == (root / "DISCLAIMER.md").read_bytes()
            assert b"PUBLIC_RELEASE_" + b"BLOCKED" not in b"".join(
                archive.read(name) for name in archive.namelist()
            )
            assert "academic-pdf-en-zh-reader/REUSE.toml" in names
            assert "academic-pdf-en-zh-reader/compliance/python-runtime.json" in names
            assert (
                "academic-pdf-en-zh-reader/compliance/python-dependencies.json" in names
            )
            for script in (
                "build_compatible_dependencies.py",
                "install_compatible_dependencies.py",
                "package_compatible_dependencies.py",
            ):
                assert f"academic-pdf-en-zh-reader/scripts/{script}" in names
            assert (
                "academic-pdf-en-zh-reader/scripts/build_compatible_python.py" in names
            )
            assert (
                "academic-pdf-en-zh-reader/scripts/package_python_runtime.py" in names
            )
            assert (
                "academic-pdf-en-zh-reader/assets/fonts/Synthetic-Regular.ttf" in names
            )
            assert any(
                name.startswith("academic-pdf-en-zh-reader/LICENSES/") for name in names
            )
            assert {
                f"academic-pdf-en-zh-reader/{reference}"
                for reference in referenced_evidence
            }.issubset(names)
            for name in names:
                path_parts = {part.casefold() for part in PurePosixPath(name).parts}
                assert path_parts.isdisjoint({".git", "tests", "private"})
            assert not any(name.casefold().endswith(".pdf") for name in names)

    with zipfile.ZipFile(first[1]) as skill_archive:
        skill_names = set(skill_archive.namelist())
        assert "academic-pdf-en-zh-reader/SKILL.md" in skill_names
        assert "academic-pdf-en-zh-reader/agents/openai.yaml" in skill_names
        assert "academic-pdf-en-zh-reader/scripts/agent_artifacts.py" in skill_names
        assert "academic-pdf-en-zh-reader/scripts/prepare_job.py" in skill_names
        assert "academic-pdf-en-zh-reader/scripts/finish_job.py" in skill_names
        assert "academic-pdf-en-zh-reader/scripts/qa_pdf.py" in skill_names

    manifest = json.loads(first[2].read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["disclaimer_sha256"] == _hash(root / "DISCLAIMER.md")
    assert len(manifest["bundles"]) == 2
    assert any(
        item["path"] == "assets/fonts/Synthetic-Regular.ttf"
        for item in manifest["bundled_assets"]
    )


def test_package_member_with_development_marker_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(
        root / "README.md",
        f"[Disclaimer](DISCLAIMER.md)\n{CANONICAL_URL}\nPUBLIC_RELEASE_" + "BLOCKED\n",
    )
    _commit_synthetic_tree(root)
    output = root / "dist" / "release"

    with pytest.raises(ValueError, match="development release marker"):
        build(root, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "relative",
    (
        "src/__pycache__/module.cpython-312.pyc",
        "src/module.pyc",
        "references/local-notes.txt",
        "assets/build-diagnostics.txt",
    ),
)
def test_untracked_cache_and_plain_text_never_enter_candidates(
    tmp_path: Path, relative: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    first = build(root, root / "dist" / "first")
    _write(root / relative, b"synthetic local-only content")

    second = build(root, root / "dist" / "second")

    for archive_path in second[:2]:
        with zipfile.ZipFile(archive_path) as archive:
            assert f"academic-pdf-en-zh-reader/{relative}" not in archive.namelist()
    assert [_hash(path) for path in first] == [_hash(path) for path in second]


@pytest.mark.parametrize(
    "change", ("modified", "staged", "missing", "assume-unchanged")
)
def test_committed_member_cannot_be_replaced_or_omitted(
    tmp_path: Path, change: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    member = root / "src/placeholder.txt"
    if change == "missing":
        member.unlink()
        message = "committed release member is missing"
    else:
        _write(member, "synthetic replacement\n")
        if change == "staged":
            subprocess.run(
                ["git", "-C", str(root), "add", "src/placeholder.txt"], check=True
            )
            message = "staged changes"
        else:
            if change == "assume-unchanged":
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "update-index",
                        "--assume-unchanged",
                        "src/placeholder.txt",
                    ],
                    check=True,
                )
            message = "differs from committed HEAD"
    output = root / "dist/release"
    with pytest.raises(ValueError, match=message):
        build(root, output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("relative", "content", "message"),
    (
        ("references/local.pdf", b"synthetic document", "private or user-document"),
        (
            "references/status.txt",
            b"PUBLIC_RELEASE_" + b"BLOCKED",
            "development release marker",
        ),
        (
            "assets/fonts/Unreviewed.ttf",
            b"synthetic font",
            "font is absent from font-manifest",
        ),
    ),
)
def test_committed_members_still_enforce_content_and_font_gates(
    tmp_path: Path, relative: str, content: bytes, message: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(root / relative, content)
    _commit_synthetic_tree(root)
    output = root / "dist/release"
    with pytest.raises(ValueError, match=message):
        build(root, output)
    assert not output.exists()


def test_untracked_symlink_is_still_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    member = root / "references/synthetic-link"
    _write(member)
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path == member or original(path)
    )
    output = root / "dist/release"
    with pytest.raises(ValueError, match="must not contain symlinks"):
        build(root, output)
    assert not output.exists()


def test_git_snapshot_timeout_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)

    def timed_out(command, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(artifact_module.subprocess, "check_output", timed_out)
    with pytest.raises(ValueError, match="readable committed Git tree"):
        artifact_module._tracked_files(root, COMMON_PATHS)
    assert not (root / "dist").exists()


def test_missing_disclaimer_fails_before_writing_release_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    (root / "DISCLAIMER.md").unlink()
    output = root / "dist" / "release"

    with pytest.raises(ValueError, match="DISCLAIMER.md"):
        build(root, output)
    assert not output.exists()


def test_placeholder_repository_url_fails_before_release(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    identity_path = root / "compliance" / "project-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["repository"]["canonical_url"] = "https://github.com/OWNER/REPO"
    _write(identity_path, json.dumps(identity, ensure_ascii=False))
    output = root / "dist" / "release"

    with pytest.raises(ValueError, match="repository"):
        build(root, output)
    assert not output.exists()


def test_brand_asset_tampering_fails_before_release(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(root / "assets" / "branding" / "hanhai-wencai.png", b"tampered")
    output = root / "dist" / "release"

    with pytest.raises(ValueError, match="brand asset"):
        build(root, output)
    assert not output.exists()


def test_brand_trademark_requires_independent_release_evidence(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    identity_path = root / "compliance" / "project-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["asset"]["trademark"]["public_use_authorized"] = False
    identity["asset"]["trademark"]["evidence"] = None
    _write(identity_path, json.dumps(identity, ensure_ascii=False))

    with pytest.raises(ValueError, match="trademark"):
        build(root, root / "dist" / "release")


def test_brand_reuse_annotation_must_match_verified_identity(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    reuse_path = root / "REUSE.toml"
    content = reuse_path.read_text(encoding="utf-8").replace(
        'SPDX-License-Identifier = "Apache-2.0"',
        'SPDX-License-Identifier = "LicenseRef-HanhaiWencai-Unreleased"',
    )
    _write(reuse_path, content)

    with pytest.raises(ValueError, match="REUSE"):
        build(root, root / "dist" / "release")


def test_brand_release_license_requires_a_bundled_license_text(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    (root / "LICENSES" / "Apache-2.0.txt").unlink()

    with pytest.raises(ValueError, match="license text"):
        build(root, root / "dist" / "release")


def test_brand_release_license_rejects_a_made_up_spdx_identifier(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    identity_path = root / "compliance" / "project-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["asset"]["spdx_license"] = "Definitely-Not-SPDX"
    identity["asset"]["copyright"]["spdx_license"] = "Definitely-Not-SPDX"
    _write(identity_path, json.dumps(identity, ensure_ascii=False))
    reuse_path = root / "REUSE.toml"
    _write(
        reuse_path,
        reuse_path.read_text(encoding="utf-8").replace(
            "Apache-2.0", "Definitely-Not-SPDX"
        ),
    )
    notices_path = root / "THIRD_PARTY_NOTICES.md"
    _write(
        notices_path,
        notices_path.read_text(encoding="utf-8").replace(
            "Apache-2.0", "Definitely-Not-SPDX"
        ),
    )
    _write(
        root / "LICENSES" / "Definitely-Not-SPDX.txt",
        "Synthetic made-up license text\n",
    )

    with pytest.raises(ValueError, match="allowed SPDX"):
        build(root, root / "dist" / "release")


def test_brand_release_license_allows_a_documented_custom_license_ref(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    custom_license = "LicenseRef-Synthetic-Brand"
    identity_path = root / "compliance" / "project-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["asset"]["spdx_license"] = custom_license
    identity["asset"]["copyright"]["spdx_license"] = custom_license
    _write(identity_path, json.dumps(identity, ensure_ascii=False))
    reuse_path = root / "REUSE.toml"
    _write(
        reuse_path,
        reuse_path.read_text(encoding="utf-8").replace("Apache-2.0", custom_license),
    )
    notices_path = root / "THIRD_PARTY_NOTICES.md"
    _write(
        notices_path,
        notices_path.read_text(encoding="utf-8").replace("Apache-2.0", custom_license),
    )
    _write(root / "LICENSES" / f"{custom_license}.txt", "Synthetic terms\n")

    result = assess_release_readiness(root, mode="release")

    assert result.ok, result.errors


def test_ready_tree_rejects_obsolete_unreleased_brand_license(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(
        root / "LICENSES" / "LicenseRef-HanhaiWencai-Unreleased.txt",
        "Brand asset must not enter a public release.\n",
    )

    with pytest.raises(ValueError, match="obsolete unreleased brand license"):
        build(root, root / "dist" / "release")


def test_citation_version_and_repository_must_match_release_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    cff_path = root / "CITATION.cff"
    _write(
        cff_path,
        cff_path.read_text(encoding="utf-8").replace(
            'version: "0.1.0"', 'version: "0.2.0"'
        ),
    )

    with pytest.raises(ValueError, match="CITATION"):
        build(root, root / "dist" / "release")


def test_citation_release_date_must_be_a_real_calendar_date(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    cff_path = root / "CITATION.cff"
    _write(
        cff_path,
        cff_path.read_text(encoding="utf-8").replace("2026-08-28", "2026-99-99"),
    )

    with pytest.raises(ValueError, match="ISO release date"):
        build(root, root / "dist" / "release")


@pytest.mark.parametrize(
    "unreleased_text",
    (
        "A private reporting route has not yet been configured.",
        "Private reporting channels have not yet been configured.",
    ),
)
def test_unreleased_conduct_route_text_cannot_enter_a_release(
    tmp_path: Path, unreleased_text: str
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    channel = CHANNEL_REFERENCES["security_private_channel_unconfigured"]
    _write(root / "CODE_OF_CONDUCT.md", f"{unreleased_text}\nContact: {channel}\n")

    result = assess_release_readiness(root, mode="release")

    assert not result.ok
    assert "CODE_OF_CONDUCT.md still contains unreleased-state text" in result.errors


def test_unreleased_security_text_cannot_enter_a_release(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(root / "SECURITY.md", "There are currently no supported public versions.\n")

    with pytest.raises(ValueError, match="SECURITY.md"):
        build(root, root / "dist" / "release")


def test_unreleased_runbook_text_cannot_enter_a_release(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(root / "references" / "runbook.md", "Public release remains blocked.\n")

    with pytest.raises(ValueError, match="references/runbook.md"):
        build(root, root / "dist" / "release")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
