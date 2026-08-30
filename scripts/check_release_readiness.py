# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

Mode = Literal["development", "release"]

REQUIRED_BLOCKERS = {
    "brand_asset_rights_unverified",
    "canonical_repository_url_unconfigured",
    "maintainer_identity_unverified",
    "coc_primary_channel_unconfigured",
    "coc_alternate_channel_unconfigured",
    "security_private_channel_unconfigured",
}
PRIVATE_CHANNEL_BLOCKERS = {
    "coc_primary_channel_unconfigured",
    "coc_alternate_channel_unconfigured",
    "security_private_channel_unconfigured",
}
PENDING_BRAND_LICENSE = "LicenseRef-HanhaiWencai-Unreleased"
DEVELOPMENT_REPOSITORY_DISPLAY = "GitHub：公开发布后提供"
UNRELEASED_DOCUMENT_MARKERS = {
    "README.md": ("public release remains blocked",),
    "PRIVACY.md": (
        "public release remains blocked",
        "not a release or deployment claim",
    ),
    "SECURITY.md": (
        "there are currently no supported public versions",
        "must not be published as a production-ready skill",
    ),
    "CONTRIBUTING.md": ("not ready to accept public contributions",),
    "CODE_OF_CONDUCT.md": (
        "have not yet been configured",
        "not ready for public participation or release",
    ),
}
REQUIRED_DISCLAIMER_LINKS = {
    "README.md": "DISCLAIMER.md",
    "SKILL.md": "DISCLAIMER.md",
    "PRIVACY.md": "DISCLAIMER.md",
    "references/product-contract.md": "../DISCLAIMER.md",
}


@dataclass(frozen=True)
class ReadinessResult:
    ok: bool
    errors: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_canonical_github_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parsed.path.endswith("/"):
        return False
    placeholders = {
        "<owner>",
        "<repo>",
        "org",
        "organization",
        "owner",
        "repo",
        "username",
        "your-name",
        "your-org",
        "your-repo",
    }
    return not any(part.casefold() in placeholders for part in parts)


def _complete_evidence(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("reference"), str)
        and bool(value["reference"].strip())
        and isinstance(value.get("verified_on"), str)
        and bool(value["verified_on"].strip())
    )


def _top_level_cff_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        if not line or line[0].isspace():
            continue
        key, separator, value = line.partition(":")
        if separator:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _version_metadata_errors(
    root: Path, *, mode: Mode, canonical_url: object
) -> list[str]:
    errors: list[str] = []
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = pyproject["project"]["version"]
    except (KeyError, OSError, tomllib.TOMLDecodeError, TypeError) as exc:
        errors.append(f"cannot read project version: {exc}")
        return errors
    try:
        constants = (
            root / "src" / "academic_pdf_en_zh_reader" / "constants.py"
        ).read_text(encoding="utf-8")
        match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', constants, re.MULTILINE
        )
        code_version = match.group(1) if match is not None else None
    except OSError as exc:
        errors.append(f"cannot read code version: {exc}")
        code_version = None
    try:
        citation = _top_level_cff_values(
            (root / "CITATION.cff").read_text(encoding="utf-8")
        )
    except OSError as exc:
        errors.append(f"cannot read CITATION.cff: {exc}")
        return errors

    if not isinstance(project_version, str) or not project_version:
        errors.append("pyproject version must be a non-empty string")
    if code_version != project_version:
        errors.append("code version differs from pyproject version")
    if citation.get("version") != project_version:
        errors.append("CITATION.cff version differs from pyproject version")

    citation_repository = citation.get("repository-code")
    if mode == "development":
        if citation_repository is not None:
            errors.append("development CITATION.cff must not invent a repository URL")
    else:
        if citation_repository != canonical_url:
            errors.append(
                "CITATION.cff repository differs from canonical repository URL"
            )
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", citation.get("date-released", "")):
            errors.append("CITATION.cff must contain an ISO release date")
    return errors


def _brand_annotation(
    reuse: dict[str, object], asset_path: str
) -> dict[str, object] | None:
    matches: list[dict[str, object]] = []
    annotations = reuse.get("annotations")
    if not isinstance(annotations, list):
        return None
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        paths = annotation.get("path")
        if paths == asset_path or (isinstance(paths, list) and asset_path in paths):
            matches.append(annotation)
    return matches[0] if len(matches) == 1 else None


def _brand_document_errors(
    root: Path, *, mode: Mode, identity: Mapping[str, object]
) -> list[str]:
    errors: list[str] = []
    asset = identity.get("asset")
    if not isinstance(asset, dict):
        return ["project identity asset must be an object"]
    copyright_record = asset.get("copyright")
    if not isinstance(copyright_record, dict):
        return ["brand copyright record must be an object"]
    asset_path = asset.get("path")
    if not isinstance(asset_path, str):
        return ["brand asset path must be a string"]
    try:
        reuse = tomllib.loads((root / "REUSE.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"cannot read REUSE.toml: {exc}"]
    annotation = _brand_annotation(reuse, asset_path)
    if annotation is None:
        errors.append("REUSE.toml must contain exactly one brand asset annotation")
    else:
        if annotation.get("SPDX-FileCopyrightText") != copyright_record.get(
            "spdx_copyright_text"
        ):
            errors.append("REUSE brand copyright differs from project identity")
        if annotation.get("SPDX-License-Identifier") != copyright_record.get(
            "spdx_license"
        ):
            errors.append("REUSE brand license differs from project identity")

    try:
        notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read THIRD_PARTY_NOTICES.md: {exc}")
        return errors
    license_expression = copyright_record.get("spdx_license")
    copyright_text = copyright_record.get("spdx_copyright_text")
    if isinstance(license_expression, str) and license_expression not in notices:
        errors.append("brand license is absent from THIRD_PARTY_NOTICES.md")
    if isinstance(copyright_text, str) and copyright_text not in notices:
        errors.append("brand copyright status is absent from THIRD_PARTY_NOTICES.md")

    if mode == "release":
        if not isinstance(license_expression, str) or not re.fullmatch(
            r"[A-Za-z0-9.+-]+", license_expression
        ):
            errors.append("brand release license must be one simple SPDX identifier")
        elif not (root / "LICENSES" / f"{license_expression}.txt").is_file():
            errors.append("brand release license text is missing")
        stale_markers = (
            PENDING_BRAND_LICENSE,
            "NOASSERTION",
            "not admitted to a public release",
            "public-release rights remain unverified",
            "unresolved status",
        )
        lowered_notices = notices.casefold()
        if any(marker.casefold() in lowered_notices for marker in stale_markers):
            errors.append(
                "THIRD_PARTY_NOTICES.md still describes unreleased brand rights"
            )
    return errors


def _release_document_errors(
    root: Path, *, canonical_url: object, channel_references: Mapping[str, str]
) -> list[str]:
    errors: list[str] = []
    contents: dict[str, str] = {}
    for relative, markers in UNRELEASED_DOCUMENT_MARKERS.items():
        try:
            content = (root / relative).read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read {relative}: {exc}")
            continue
        contents[relative] = content
        normalized = " ".join(content.casefold().split())
        if any(marker in normalized for marker in markers):
            errors.append(f"{relative} still contains unreleased-state text")

    try:
        readme = (root / "README.md").read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read README.md: {exc}")
    else:
        if not isinstance(canonical_url, str) or canonical_url not in readme:
            errors.append("README.md must contain the canonical repository URL")

    routes = {
        "coc_primary_channel_unconfigured": "CODE_OF_CONDUCT.md",
        "coc_alternate_channel_unconfigured": "CODE_OF_CONDUCT.md",
        "security_private_channel_unconfigured": "SECURITY.md",
    }
    for blocker_id, relative in routes.items():
        reference = channel_references.get(blocker_id)
        if reference and reference not in contents.get(relative, ""):
            errors.append(
                f"{relative} does not publish the verified {blocker_id} route"
            )
    return errors


def _project_identity_errors(
    root: Path, *, mode: Mode
) -> tuple[list[str], dict[str, object] | None]:
    errors: list[str] = []
    disclaimer_path = root / "DISCLAIMER.md"
    try:
        if not disclaimer_path.read_text(encoding="utf-8").strip():
            errors.append("DISCLAIMER.md must not be empty")
    except OSError as exc:
        errors.append(f"cannot read DISCLAIMER.md: {exc}")

    for relative, target in REQUIRED_DISCLAIMER_LINKS.items():
        try:
            content = (root / relative).read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read disclaimer route {relative}: {exc}")
            continue
        if f"]({target})" not in content:
            errors.append(f"{relative} must link to {target}")

    identity_path = root / "compliance" / "project-identity.json"
    manifest_path = root / "assets" / "branding" / "brand-manifest.json"
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read project identity: {exc}")
        return errors, None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read brand manifest: {exc}")
        return errors, identity if isinstance(identity, dict) else None
    if not isinstance(identity, dict) or identity.get("schema_version") != 1:
        errors.append("project identity schema_version must be 1")
        return errors, identity if isinstance(identity, dict) else None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        errors.append("brand manifest schema_version must be 1")
        return errors, identity

    brand = identity.get("brand")
    repository = identity.get("repository")
    asset = identity.get("asset")
    output_disclaimer = identity.get("output_disclaimer")
    if not all(
        isinstance(item, dict) for item in (brand, repository, asset, output_disclaimer)
    ):
        errors.append("project identity sections must be objects")
        return errors, identity
    assert isinstance(brand, dict)
    assert isinstance(repository, dict)
    assert isinstance(asset, dict)
    assert isinstance(output_disclaimer, dict)

    expected_brand = "瀚海问材"
    expected_brand_en = "Hanhai Materials"
    expected_skill = "academic-pdf-en-zh-reader"
    if brand.get("display_name_zh") != expected_brand:
        errors.append("project brand name is invalid")
    if brand.get("display_name_en") != expected_brand_en:
        errors.append("project English brand name is invalid")
    if brand.get("skill_name") != expected_skill:
        errors.append("project skill name is invalid")
    if repository.get("development_display") != DEVELOPMENT_REPOSITORY_DISPLAY:
        errors.append("development repository display is invalid")
    if manifest.get("brand_name_zh") != brand.get("display_name_zh"):
        errors.append("brand manifest name differs from project identity")
    if manifest.get("brand_name_en") != brand.get("display_name_en"):
        errors.append("brand manifest English name differs from project identity")
    if manifest.get("skill_name") != brand.get("skill_name"):
        errors.append("brand manifest skill name differs from project identity")
    if manifest.get("disclaimer_zh_short") != output_disclaimer.get("zh_short"):
        errors.append("brand manifest disclaimer differs from project identity")

    image = manifest.get("image")
    if not isinstance(image, dict):
        errors.append("brand manifest image must be an object")
        return errors, identity
    for field in ("path", "sha256", "size"):
        if image.get(field) != asset.get(field):
            errors.append(f"brand asset {field} differs between manifests")
    if asset.get("path") != "assets/branding/hanhai-wencai.png":
        errors.append("brand asset path is invalid")
    else:
        brand_path = root / str(asset["path"])
        try:
            if brand_path.is_symlink() or not brand_path.is_file():
                errors.append("brand asset must be a regular non-symlink file")
            else:
                if brand_path.stat().st_size != asset.get("size"):
                    errors.append("brand asset size differs from project identity")
                if _sha256(brand_path) != asset.get("sha256"):
                    errors.append("brand asset hash differs from project identity")
        except OSError as exc:
            errors.append(f"cannot verify brand asset: {exc}")

    canonical_url = repository.get("canonical_url")
    rights_status = asset.get("rights_status")
    rights_evidence = asset.get("evidence")
    asset_license = asset.get("spdx_license")
    copyright_record = asset.get("copyright")
    trademark_record = asset.get("trademark")
    if not isinstance(copyright_record, dict):
        errors.append("brand copyright record must be an object")
        copyright_record = {}
    if not isinstance(trademark_record, dict):
        errors.append("brand trademark record must be an object")
        trademark_record = {}
    if copyright_record.get("spdx_license") != asset_license:
        errors.append("brand copyright license differs from compatibility field")
    if mode == "development":
        if canonical_url is not None:
            errors.append("development canonical repository URL must remain null")
        if manifest.get("github_display") != DEVELOPMENT_REPOSITORY_DISPLAY:
            errors.append("development brand manifest must use the pending URL display")
        if (
            rights_status != "unverified"
            or rights_evidence is not None
            or asset_license != PENDING_BRAND_LICENSE
        ):
            errors.append("development brand rights must remain unverified")
        if (
            copyright_record.get("ownership_status") != "unverified"
            or copyright_record.get("public_redistribution_authorized") is not False
            or copyright_record.get("spdx_copyright_text") != "NOASSERTION"
            or copyright_record.get("spdx_license") != PENDING_BRAND_LICENSE
            or copyright_record.get("evidence") is not None
        ):
            errors.append("development brand copyright must remain unverified")
        if (
            trademark_record.get("status") != "unverified"
            or trademark_record.get("public_use_authorized") is not False
            or trademark_record.get("evidence") is not None
        ):
            errors.append("development brand trademark must remain unverified")
    else:
        if not _is_canonical_github_url(canonical_url):
            errors.append("canonical repository URL must be a real HTTPS GitHub URL")
        if not (
            isinstance(manifest.get("github_display"), str)
            and isinstance(canonical_url, str)
            and canonical_url in manifest["github_display"]
            and DEVELOPMENT_REPOSITORY_DISPLAY not in manifest["github_display"]
        ):
            errors.append(
                "release brand manifest must display the canonical repository"
            )
        if (
            rights_status != "verified"
            or not isinstance(rights_evidence, dict)
            or not isinstance(asset_license, str)
            or not asset_license.strip()
            or asset_license == PENDING_BRAND_LICENSE
        ):
            errors.append("brand asset rights must be verified for release")
        if (
            copyright_record.get("ownership_status") != "verified"
            or copyright_record.get("public_redistribution_authorized") is not True
            or not isinstance(copyright_record.get("spdx_copyright_text"), str)
            or not copyright_record["spdx_copyright_text"].strip()
            or copyright_record.get("spdx_copyright_text") == "NOASSERTION"
            or not isinstance(copyright_record.get("spdx_license"), str)
            or copyright_record.get("spdx_license") == PENDING_BRAND_LICENSE
            or not _complete_evidence(copyright_record.get("evidence"))
        ):
            errors.append(
                "brand copyright ownership and redistribution must be "
                "verified for release"
            )
        if (
            trademark_record.get("status") != "verified"
            or trademark_record.get("public_use_authorized") is not True
            or not _complete_evidence(trademark_record.get("evidence"))
        ):
            errors.append("brand trademark use must be verified for release")

    return errors, identity


def assess_release_readiness(root: Path, *, mode: Mode) -> ReadinessResult:
    status_path = root / "compliance" / "release-status.json"
    errors: list[str] = []
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReadinessResult(False, (f"cannot read release status: {exc}",))

    if status.get("schema_version") != 1:
        errors.append("release status schema_version must be 1")

    blocker_items = status.get("blockers")
    if not isinstance(blocker_items, list):
        return ReadinessResult(False, tuple(errors + ["blockers must be a list"]))

    blockers: dict[str, dict[str, object]] = {}
    for item in blocker_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            errors.append("each blocker must be an object with a string id")
            continue
        blocker_id = item["id"]
        if blocker_id in blockers:
            errors.append(f"duplicate blocker: {blocker_id}")
        blockers[blocker_id] = item

    if set(blockers) != REQUIRED_BLOCKERS:
        errors.append("release status must contain the six required blockers")

    project_errors, identity = _project_identity_errors(root, mode=mode)
    errors.extend(project_errors)
    canonical_url: object = None
    if identity is not None:
        repository = identity.get("repository")
        if isinstance(repository, dict):
            canonical_url = repository.get("canonical_url")
        errors.extend(
            _version_metadata_errors(
                root,
                mode=mode,
                canonical_url=canonical_url,
            )
        )
        errors.extend(_brand_document_errors(root, mode=mode, identity=identity))

    if mode == "development":
        if status.get("state") != "PUBLIC_RELEASE_BLOCKED":
            errors.append("development state must remain PUBLIC_RELEASE_BLOCKED")
        for blocker_id in REQUIRED_BLOCKERS:
            item = blockers.get(blocker_id, {})
            if item.get("resolved") is not False or item.get("evidence") is not None:
                errors.append(f"development blocker must be unresolved: {blocker_id}")
        return ReadinessResult(not errors, tuple(errors))

    if status.get("state") != "PUBLIC_RELEASE_READY":
        errors.append("release mode requires PUBLIC_RELEASE_READY")

    channel_references: dict[str, str] = {}
    for blocker_id in REQUIRED_BLOCKERS:
        item = blockers.get(blocker_id, {})
        evidence = item.get("evidence")
        if item.get("resolved") is not True or not isinstance(evidence, dict):
            errors.append(f"release blocker lacks verified evidence: {blocker_id}")
            continue
        reference = evidence.get("reference")
        verified_on = evidence.get("verified_on")
        if not isinstance(reference, str) or not reference.strip():
            errors.append(f"evidence reference missing: {blocker_id}")
        if not isinstance(verified_on, str) or not verified_on.strip():
            errors.append(f"evidence verification date missing: {blocker_id}")
        if blocker_id in PRIVATE_CHANNEL_BLOCKERS:
            if evidence.get("private") is not True:
                errors.append(f"reporting channel must be private: {blocker_id}")
            if isinstance(reference, str):
                channel_references[blocker_id] = reference

    folded_channels = [
        reference.casefold() for reference in channel_references.values()
    ]
    if len(folded_channels) != len(set(folded_channels)):
        errors.append("primary, alternate, and security channels must be distinct")

    if identity is not None:
        repository = identity.get("repository")
        asset = identity.get("asset")
        if isinstance(repository, dict):
            repository_evidence = blockers.get(
                "canonical_repository_url_unconfigured", {}
            ).get("evidence")
            if isinstance(repository_evidence, dict) and repository_evidence.get(
                "reference"
            ) != repository.get("canonical_url"):
                errors.append("repository blocker evidence differs from canonical URL")
        if isinstance(asset, dict):
            brand_evidence = blockers.get("brand_asset_rights_unverified", {}).get(
                "evidence"
            )
            if brand_evidence != asset.get("evidence"):
                errors.append("brand rights evidence differs from project identity")

    errors.extend(
        _release_document_errors(
            root,
            canonical_url=canonical_url,
            channel_references=channel_references,
        )
    )

    return ReadinessResult(not errors, tuple(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("development", "release"), required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = assess_release_readiness(args.root.resolve(), mode=args.mode)
    if result.ok:
        print(f"release readiness ({args.mode}): OK")
        return 0
    print(f"release readiness ({args.mode}): BLOCKED")
    for error in result.errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
