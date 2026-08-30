# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from scripts.build_release_artifacts import COMMON_PATHS, RUNTIME_SCRIPTS, build

ROOT = Path(__file__).resolve().parents[2]
DIRECTORIES = {"LICENSES", "assets", "references", "src"}


def _write(path: Path, content: str | bytes = "test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


def _ready_status() -> dict[str, object]:
    blockers = []
    for index, blocker_id in enumerate(
        (
            "maintainer_identity_unverified",
            "coc_primary_channel_unconfigured",
            "coc_alternate_channel_unconfigured",
            "security_private_channel_unconfigured",
            "canonical_repository_url_unconfigured",
            "brand_asset_rights_unverified",
        )
    ):
        reference = f"verified-record-{index}"
        if blocker_id == "canonical_repository_url_unconfigured":
            reference = "https://github.com/example/academic-pdf-en-zh-reader"
        evidence: dict[str, object] = {
            "reference": reference,
            "verified_on": "2026-08-28",
        }
        if blocker_id in {
            "coc_primary_channel_unconfigured",
            "coc_alternate_channel_unconfigured",
            "security_private_channel_unconfigured",
        }:
            evidence["private"] = True
        blockers.append({"id": blocker_id, "resolved": True, "evidence": evidence})
    return {"schema_version": 1, "state": "PUBLIC_RELEASE_READY", "blockers": blockers}


def _synthetic_ready_tree(root: Path) -> None:
    for entry in COMMON_PATHS:
        path = root / entry
        if entry in DIRECTORIES:
            path.mkdir(parents=True, exist_ok=True)
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
    canonical_url = "https://github.com/example/academic-pdf-en-zh-reader"
    _write(
        root / "pyproject.toml",
        '[project]\nname = "academic-pdf-en-zh-reader"\nversion = "0.1.0"\n',
    )
    _write(
        root / "src" / "academic_pdf_en_zh_reader" / "constants.py",
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
    _write(
        root / "CODE_OF_CONDUCT.md",
        "Primary private route: verified-record-1\n"
        "Independent alternate private route: verified-record-2\n",
    )
    _write(root / "SECURITY.md", "Private security route: verified-record-3\n")
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
                "reference": "verified-record-5",
                "verified_on": "2026-08-28",
            },
            "copyright": {
                "ownership_status": "verified",
                "public_redistribution_authorized": True,
                "spdx_copyright_text": "Copyright 2026 Synthetic Brand Owner",
                "spdx_license": "Apache-2.0",
                "evidence": {
                    "reference": "synthetic-copyright-record",
                    "verified_on": "2026-08-28",
                },
            },
            "trademark": {
                "status": "verified",
                "public_use_authorized": True,
                "evidence": {
                    "reference": "synthetic-trademark-record",
                    "verified_on": "2026-08-28",
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
        "Trademark public use verified by synthetic-trademark-record.\n",
    )


def test_current_repository_fails_closed_before_writing_release_artifacts() -> None:
    output = ROOT / "dist" / "release-policy-test"
    assert not output.exists()
    with pytest.raises(ValueError, match="release readiness is blocked"):
        build(ROOT, output)
    assert not output.exists()


def test_ready_tree_builds_repeatable_notice_complete_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)

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
            assert (
                "academic-pdf-en-zh-reader/assets/fonts/Synthetic-Regular.ttf" in names
            )
            assert any(
                name.startswith("academic-pdf-en-zh-reader/LICENSES/") for name in names
            )
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
        "[Disclaimer](DISCLAIMER.md)\n"
        "https://github.com/example/academic-pdf-en-zh-reader\n"
        "PUBLIC_RELEASE_" + "BLOCKED\n",
    )
    output = root / "dist" / "release"

    with pytest.raises(ValueError, match="development release marker"):
        build(root, output)
    assert not output.exists()


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


def test_unreleased_security_text_cannot_enter_a_release(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _synthetic_ready_tree(root)
    _write(root / "SECURITY.md", "There are currently no supported public versions.\n")

    with pytest.raises(ValueError, match="SECURITY.md"):
        build(root, root / "dist" / "release")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
