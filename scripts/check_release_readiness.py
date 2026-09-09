# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

Mode = Literal["current", "development", "release"]

REQUIRED_BLOCKERS = {
    "brand_asset_rights_unverified",
    "canonical_repository_url_unconfigured",
    "maintainer_identity_unverified",
    "security_private_channel_unconfigured",
    "windows_lpac_production_path_unverified",
}
PRIVATE_CHANNEL_BLOCKERS = {
    "security_private_channel_unconfigured",
}
WINDOWS_LPAC_BLOCKER = "windows_lpac_production_path_unverified"
WINDOWS_LPAC_JOB = "windows-2025-production-gate"
RELEASE_WORKFLOW_PATH = ".github/workflows/release.yml"
LIVE_GATE_RESULT_VARIABLE = "RELEASE_LPAC_GATE_RESULT"
LIVE_GATE_PROBE_VARIABLE = "RELEASE_LPAC_PROBE_OUTCOME"
LIVE_GATE_FINISH_VARIABLE = "RELEASE_LPAC_FINISH_OUTCOME"
LIVE_GATE_HEAD_VARIABLE = "RELEASE_LPAC_TESTED_HEAD_SHA"
LIVE_GATE_SCHEMA_VARIABLE = "RELEASE_LPAC_SIGNAL_SCHEMA"
LIVE_GATE_RUN_ID_VARIABLE = "RELEASE_LPAC_RUN_ID"
LIVE_GATE_RUN_ATTEMPT_VARIABLE = "RELEASE_LPAC_RUN_ATTEMPT"
WINDOWS_LPAC_EVIDENCE_FIELDS = {
    "conclusion",
    "head_sha",
    "job",
    "run_url",
    "verified_on",
    "workflow",
}
PENDING_BRAND_LICENSE = "LicenseRef-HanhaiWencai-Unreleased"
DEVELOPMENT_REPOSITORY_DISPLAY = "GitHub：公开发布后提供"
PUBLIC_EVIDENCE_DIRECTORY = PurePosixPath("compliance/evidence/public")
MAX_PUBLIC_EVIDENCE_BYTES = 128 * 1024
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
STANDARD_BRAND_LICENSES = {
    "Apache-2.0",
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CC0-1.0",
}
SPECIAL_USE_HOST_SUFFIXES = (
    ".alt",
    ".arpa",
    ".example",
    ".home.arpa",
    ".internal",
    ".invalid",
    ".local",
    ".localhost",
    ".onion",
    ".test",
)
PLACEHOLDER_TOKENS = (
    "<",
    ">",
    "changeme",
    "example",
    "placeholder",
    "replace-me",
    "replace_me",
    "tbd",
    "todo",
    "verified-record",
    "verified_record",
)
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
        "has not yet been configured",
        "not ready for public participation or release",
    ),
    "references/runbook.md": (
        "not a public production release",
        "public release remains blocked",
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


def _contains_placeholder(value: str) -> bool:
    folded = value.casefold()
    return any(token in folded for token in PLACEHOLDER_TOKENS)


def _normalize_github_repository_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return None
    if _contains_placeholder(value):
        return None
    scp_match = re.fullmatch(
        r"git@github\.com:([^/]+)/([^/]+)", value, flags=re.IGNORECASE
    )
    if scp_match is not None:
        owner, repository = scp_match.groups()
    else:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {"https", "ssh"}
            or parsed.hostname is None
            or parsed.hostname.casefold() != "github.com"
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        if parsed.scheme == "https" and (
            parsed.username is not None or port not in {None, 443}
        ):
            return None
        if parsed.scheme == "ssh" and (
            parsed.username not in {None, "git"} or port not in {None, 22}
        ):
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            return None
        owner, repository = parts
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    placeholder_parts = {
        "org",
        "organization",
        "owner",
        "repo",
        "username",
        "your-name",
        "your-org",
        "your-repo",
    }
    name_pattern = re.compile(r"[A-Za-z0-9_.-]+")
    if (
        not owner
        or not repository
        or owner in {".", ".."}
        or repository in {".", ".."}
        or owner.casefold() in placeholder_parts
        or repository.casefold() in placeholder_parts
        or name_pattern.fullmatch(owner) is None
        or name_pattern.fullmatch(repository) is None
    ):
        return None
    normalized = f"https://github.com/{owner}/{repository}"
    return None if _contains_placeholder(normalized) else normalized


def _is_canonical_github_url(value: object) -> bool:
    return isinstance(value, str) and _normalize_github_repository_url(value) == value


def _normalized_git_origin(root: Path) -> str | None:
    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip()
        if Path(top_level).resolve() != root.resolve():
            return None
        origin = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return _normalize_github_repository_url(origin)


def _git_head_sha(root: Path) -> str | None:
    try:
        value = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _valid_verification_date(value: object) -> bool:
    parsed = _parse_iso_date(value)
    shanghai_today = datetime.now(SHANGHAI_TIMEZONE).date()
    return parsed is not None and parsed <= shanghai_today


def _public_hostname(value: str | None) -> bool:
    if value is None:
        return False
    hostname = value.casefold()
    if (
        not hostname
        or hostname.endswith(".")
        or len(hostname) > 253
        or "." not in hostname
    ):
        return False
    if hostname in {
        "alt",
        "arpa",
        "home.arpa",
        "internal",
        "localhost",
        "onion",
    } or hostname.endswith(SPECIAL_USE_HOST_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        return all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is not None
            for label in labels
        )
    return address.is_global


def _is_reviewable_https(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or _contains_placeholder(value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and _public_hostname(parsed.hostname)
    )


def _is_routable_private_channel(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or _contains_placeholder(value)
        or "%" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return (
            parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and _public_hostname(parsed.hostname)
        )
    if parsed.scheme != "mailto" or parsed.netloc:
        return False
    address = parsed.path
    if address.count("@") != 1 or len(address) > 254:
        return False
    local, hostname = address.rsplit("@", 1)
    dot_atom = r"[A-Za-z0-9!#$&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$&'*+/=?^_`{|}~-]+)*"
    return (
        len(local) <= 64
        and re.fullmatch(dot_atom, local) is not None
        and _public_hostname(hostname)
    )


def _public_evidence_markdown(root: Path, value: object, *, blocker_id: str) -> bool:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\\" in value
        or _contains_placeholder(value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        relative = PurePosixPath(value)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return False
    if (
        relative.is_absolute()
        or relative.parent != PUBLIC_EVIDENCE_DIRECTORY
        or relative.suffix != ".md"
        or ".." in relative.parts
    ):
        return False
    candidate = root.joinpath(*relative.parts)
    try:
        if (
            not candidate.resolve().is_relative_to(root.resolve())
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            return False
        with candidate.open("rb") as handle:
            raw = handle.read(MAX_PUBLIC_EVIDENCE_BYTES + 1)
        if not raw or len(raw) > MAX_PUBLIC_EVIDENCE_BYTES:
            return False
        content = raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        return False
    evidence_id = f"Evidence-ID: {blocker_id}"
    return bool(content.strip()) and evidence_id in {
        line.strip() for line in content.splitlines()
    }


def _is_reviewable_evidence_reference(
    root: Path, value: object, *, blocker_id: str
) -> bool:
    if _is_reviewable_https(value):
        return True
    return _public_evidence_markdown(root, value, blocker_id=blocker_id)


def _complete_reviewable_evidence(
    root: Path, value: object, *, blocker_id: str
) -> bool:
    return (
        isinstance(value, dict)
        and _valid_verification_date(value.get("verified_on"))
        and _is_reviewable_evidence_reference(
            root,
            value.get("reference"),
            blocker_id=blocker_id,
        )
    )


def _windows_lpac_evidence_errors(
    root: Path,
    evidence: object,
    *,
    canonical_url: object,
    require_live_gate: bool,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, dict) or set(evidence) != WINDOWS_LPAC_EVIDENCE_FIELDS:
        return ["Windows LPAC evidence must be a complete structured audit record"]

    verified_on = evidence.get("verified_on")
    if not _valid_verification_date(verified_on):
        errors.append("Windows LPAC evidence verification date is invalid or future")
    if evidence.get("job") != WINDOWS_LPAC_JOB:
        errors.append("Windows LPAC evidence job is invalid")
    if evidence.get("workflow") != RELEASE_WORKFLOW_PATH:
        errors.append("Windows LPAC evidence workflow is invalid")
    if evidence.get("conclusion") != "success":
        errors.append("Windows LPAC evidence conclusion must be success")

    head_sha = evidence.get("head_sha")
    if not isinstance(head_sha, str) or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
        errors.append("Windows LPAC evidence head_sha must be a full commit SHA")
        head_sha = None
    normalized_url = _normalize_github_repository_url(canonical_url)
    repository_slug: str | None = None
    if normalized_url is not None:
        repository_slug = urlsplit(normalized_url).path.strip("/")
    run_url = evidence.get("run_url")
    if repository_slug is None or not isinstance(run_url, str):
        errors.append("Windows LPAC evidence run URL is invalid")
    else:
        match = re.fullmatch(
            rf"https://github\.com/{re.escape(repository_slug)}/actions/runs/([1-9][0-9]*)",
            run_url,
        )
        if match is None:
            errors.append("Windows LPAC evidence run URL is invalid")

    if not require_live_gate:
        return errors

    local_head_sha = _git_head_sha(root)
    github_sha = os.environ.get("GITHUB_SHA")
    tested_head_sha = os.environ.get(LIVE_GATE_HEAD_VARIABLE)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        errors.append("release mode requires the live GitHub Actions LPAC gate")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        errors.append("live LPAC gate must run in the manual release workflow")
    if os.environ.get(LIVE_GATE_SCHEMA_VARIABLE) != "1":
        errors.append("live Windows LPAC signal schema is invalid")
    if local_head_sha is None:
        errors.append("release source commit identity cannot be verified")
    if (
        not isinstance(github_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", github_sha) is None
    ):
        errors.append("live workflow SHA is invalid")
    if tested_head_sha != github_sha or local_head_sha != github_sha:
        errors.append("Windows LPAC tested SHA differs from the release source")
    if (
        repository_slug is not None
        and os.environ.get("GITHUB_REPOSITORY") != repository_slug
    ):
        errors.append("live workflow repository differs from release identity")
    if os.environ.get("GITHUB_SERVER_URL") != "https://github.com":
        errors.append("live workflow server is not GitHub Actions")
    workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF")
    expected_workflow_ref = (
        f"{repository_slug}/{RELEASE_WORKFLOW_PATH}@"
        if repository_slug is not None
        else None
    )
    if (
        expected_workflow_ref is None
        or not isinstance(workflow_ref, str)
        or not workflow_ref.startswith(expected_workflow_ref)
    ):
        errors.append("live workflow identity differs from the release workflow")
    github_run_id = os.environ.get("GITHUB_RUN_ID")
    live_run_id = os.environ.get(LIVE_GATE_RUN_ID_VARIABLE)
    if (
        not isinstance(github_run_id, str)
        or re.fullmatch(r"[1-9][0-9]*", github_run_id) is None
        or live_run_id != github_run_id
    ):
        errors.append("live Windows LPAC run id is invalid")
    github_run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    live_run_attempt = os.environ.get(LIVE_GATE_RUN_ATTEMPT_VARIABLE)
    if (
        not isinstance(github_run_attempt, str)
        or re.fullmatch(r"[1-9][0-9]*", github_run_attempt) is None
        or live_run_attempt != github_run_attempt
    ):
        errors.append("live Windows LPAC run attempt is invalid")
    if os.environ.get(LIVE_GATE_RESULT_VARIABLE) != "success":
        errors.append("live Windows LPAC gate did not conclude successfully")
    if os.environ.get(LIVE_GATE_PROBE_VARIABLE) != "success":
        errors.append("live Windows LPAC probe did not conclude successfully")
    if os.environ.get(LIVE_GATE_FINISH_VARIABLE) != "success":
        errors.append("live Windows LPAC finish did not conclude successfully")
    return errors


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
        version_module = (
            root / "src" / "academic_pdf_en_zh_reader" / "version.py"
        ).read_text(encoding="utf-8")
        match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
            version_module,
            re.MULTILINE,
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
        if _parse_iso_date(citation.get("date-released")) is None:
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


def _valid_brand_license(value: object) -> bool:
    return isinstance(value, str) and (
        value in STANDARD_BRAND_LICENSES
        or re.fullmatch(r"LicenseRef-[A-Za-z0-9][A-Za-z0-9.-]*", value) is not None
    )


def _nonempty_utf8_regular_file(path: Path) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and bool(path.read_text(encoding="utf-8").strip())
        )
    except (OSError, UnicodeError, ValueError):
        return False


def _brand_document_errors(
    root: Path, *, brand_rights_resolved: bool, identity: Mapping[str, object]
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

    if _valid_brand_license(license_expression) and not _nonempty_utf8_regular_file(
        root / "LICENSES" / f"{license_expression}.txt"
    ):
        errors.append("brand license text is missing or empty")

    if brand_rights_resolved:
        pending_license_path = root / "LICENSES" / f"{PENDING_BRAND_LICENSE}.txt"
        if pending_license_path.exists():
            errors.append("obsolete unreleased brand license text must be removed")
        if not _valid_brand_license(license_expression):
            errors.append(
                "brand release license must be an allowed SPDX identifier or "
                "LicenseRef-*"
            )
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


def _channel_document_errors(
    root: Path, *, channel_references: Mapping[str, str]
) -> list[str]:
    errors: list[str] = []
    routes = {
        "security_private_channel_unconfigured": (
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
        ),
    }
    for blocker_id, reference in channel_references.items():
        for relative in routes[blocker_id]:
            try:
                content = (root / relative).read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"cannot read {relative}: {exc}")
                continue
            if reference not in content:
                errors.append(
                    f"{relative} does not publish the verified {blocker_id} route"
                )
    return errors


def _release_document_errors(root: Path, *, canonical_url: object) -> list[str]:
    errors: list[str] = []
    for relative, markers in UNRELEASED_DOCUMENT_MARKERS.items():
        try:
            content = (root / relative).read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read {relative}: {exc}")
            continue
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

    return errors


def _project_identity_errors(
    root: Path, *, mode: Literal["development", "release"], brand_rights_resolved: bool
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
    if canonical_url is not None:
        if not _is_canonical_github_url(canonical_url):
            errors.append(
                "canonical repository URL must be a normalized HTTPS GitHub URL"
            )
        else:
            normalized_origin = _normalized_git_origin(root)
            if normalized_origin is None:
                errors.append("cannot verify canonical URL against remote.origin.url")
            elif normalized_origin != canonical_url:
                errors.append("canonical repository URL differs from remote.origin.url")

    if mode == "development":
        if manifest.get("github_display") != DEVELOPMENT_REPOSITORY_DISPLAY:
            errors.append("development brand manifest must use the pending URL display")
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

    if not brand_rights_resolved:
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
        if (
            rights_status != "verified"
            or not _complete_reviewable_evidence(
                root,
                rights_evidence,
                blocker_id="brand_asset_rights_unverified",
            )
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
            or not _complete_reviewable_evidence(
                root,
                copyright_record.get("evidence"),
                blocker_id="brand_asset_rights_unverified",
            )
        ):
            errors.append(
                "brand copyright ownership and redistribution must be "
                "verified for release"
            )
        if (
            trademark_record.get("status") != "verified"
            or trademark_record.get("public_use_authorized") is not True
            or not _complete_reviewable_evidence(
                root,
                trademark_record.get("evidence"),
                blocker_id="brand_asset_rights_unverified",
            )
        ):
            errors.append("brand trademark use must be verified for release")

    return errors, identity


def assess_release_readiness(root: Path, *, mode: Mode) -> ReadinessResult:
    status_path = root / "compliance" / "release-status.json"
    errors: list[str] = []
    if mode not in {"current", "development", "release"}:
        return ReadinessResult(False, (f"unsupported readiness mode: {mode}",))
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReadinessResult(False, (f"cannot read release status: {exc}",))
    if not isinstance(status, dict):
        return ReadinessResult(False, ("release status must be an object",))

    if status.get("schema_version") != 1:
        errors.append("release status schema_version must be 1")

    state = status.get("state")
    if mode == "current":
        if state == "PUBLIC_RELEASE_BLOCKED":
            effective_mode: Literal["development", "release"] = "development"
        elif state == "PUBLIC_RELEASE_READY":
            effective_mode = "release"
        else:
            return ReadinessResult(
                False,
                tuple(errors + ["current mode requires a recognized release state"]),
            )
    else:
        effective_mode = mode

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
        errors.append(
            "release status must contain exactly the required personal-project blockers"
        )

    brand_blocker = blockers.get("brand_asset_rights_unverified", {})
    brand_rights_resolved = brand_blocker.get("resolved") is True
    project_errors, identity = _project_identity_errors(
        root,
        mode=effective_mode,
        brand_rights_resolved=brand_rights_resolved,
    )
    errors.extend(project_errors)
    canonical_url: object = None
    if identity is not None:
        repository = identity.get("repository")
        if isinstance(repository, dict):
            canonical_url = repository.get("canonical_url")
        errors.extend(
            _version_metadata_errors(
                root,
                mode=effective_mode,
                canonical_url=canonical_url,
            )
        )
        errors.extend(
            _brand_document_errors(
                root,
                brand_rights_resolved=brand_rights_resolved,
                identity=identity,
            )
        )

    if effective_mode == "development":
        if state != "PUBLIC_RELEASE_BLOCKED":
            errors.append("development state must remain PUBLIC_RELEASE_BLOCKED")
    elif state != "PUBLIC_RELEASE_READY":
        errors.append("release mode requires PUBLIC_RELEASE_READY")

    channel_references: dict[str, str] = {}
    resolved: dict[str, bool] = {}
    for blocker_id in sorted(REQUIRED_BLOCKERS):
        item = blockers.get(blocker_id, {})
        resolved_value = item.get("resolved")
        evidence = item.get("evidence")
        if not isinstance(resolved_value, bool):
            errors.append(f"blocker resolved must be boolean: {blocker_id}")
            resolved[blocker_id] = False
            continue
        resolved[blocker_id] = resolved_value
        if not resolved_value:
            if evidence is not None:
                errors.append(
                    f"unresolved blocker must not carry evidence: {blocker_id}"
                )
            continue
        if not isinstance(evidence, dict):
            errors.append(f"resolved blocker lacks evidence: {blocker_id}")
            continue
        if blocker_id == WINDOWS_LPAC_BLOCKER:
            errors.extend(
                _windows_lpac_evidence_errors(
                    root,
                    evidence,
                    canonical_url=canonical_url,
                    require_live_gate=mode == "release",
                )
            )
            continue
        reference = evidence.get("reference")
        if not _valid_verification_date(evidence.get("verified_on")):
            errors.append(
                f"evidence verification date is invalid or future: {blocker_id}"
            )
        if blocker_id == "canonical_repository_url_unconfigured":
            if reference != canonical_url:
                errors.append("repository blocker evidence differs from canonical URL")
        elif blocker_id in PRIVATE_CHANNEL_BLOCKERS:
            if evidence.get("private") is not True:
                errors.append(f"reporting channel must be private: {blocker_id}")
            if not _is_routable_private_channel(reference):
                errors.append(
                    "reporting channel must be a routable HTTPS or mailto URI: "
                    f"{blocker_id}"
                )
            elif isinstance(reference, str):
                channel_references[blocker_id] = reference
            verification_reference = evidence.get("verification_reference")
            if not _public_evidence_markdown(
                root,
                verification_reference,
                blocker_id=blocker_id,
            ):
                errors.append(
                    "reporting channel verification_reference must point to a "
                    f"public evidence Markdown record: {blocker_id}"
                )
        elif not _is_reviewable_evidence_reference(
            root,
            reference,
            blocker_id=blocker_id,
        ):
            errors.append(f"reviewable evidence reference is invalid: {blocker_id}")

    canonical_blocker_resolved = resolved.get(
        "canonical_repository_url_unconfigured", False
    )
    if canonical_url is None:
        if canonical_blocker_resolved:
            errors.append("repository blocker cannot resolve without a canonical URL")
    elif not canonical_blocker_resolved:
        errors.append("configured canonical URL requires a resolved repository blocker")

    if state == "PUBLIC_RELEASE_BLOCKED" and all(
        resolved.get(blocker_id, False) for blocker_id in REQUIRED_BLOCKERS
    ):
        errors.append("blocked state requires at least one unresolved blocker")

    if effective_mode == "release":
        for blocker_id in sorted(REQUIRED_BLOCKERS):
            if not resolved.get(blocker_id, False):
                errors.append(f"release blocker remains unresolved: {blocker_id}")

    if identity is not None:
        asset = identity.get("asset")
        if isinstance(asset, dict):
            brand_evidence = blockers.get("brand_asset_rights_unverified", {}).get(
                "evidence"
            )
            if brand_evidence != asset.get("evidence"):
                errors.append("brand rights evidence differs from project identity")

    errors.extend(_channel_document_errors(root, channel_references=channel_references))
    if effective_mode == "release":
        errors.extend(_release_document_errors(root, canonical_url=canonical_url))

    return ReadinessResult(not errors, tuple(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("current", "development", "release"), required=True
    )
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
