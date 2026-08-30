# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = (
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "DISCLAIMER.md",
    "LICENSE",
    "LICENSES/Apache-2.0.txt",
    "LICENSES/CC-BY-SA-4.0.txt",
    "LICENSES/LicenseRef-HanhaiWencai-Unreleased.txt",
    "NOTICE",
    "PRIVACY.md",
    "README.md",
    "REUSE.toml",
    "SECURITY.md",
    "TEST_DATA_ATTRIBUTION.md",
    "THIRD_PARTY_NOTICES.md",
    "UPSTREAMS.md",
    "compliance/dependencies.json",
    "compliance/external-tools.json",
    "compliance/github-actions.json",
    "compliance/project-identity.json",
    "compliance/release-status.json",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/ci.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/release.yml",
    "pyproject.toml",
    "scripts/build_release_artifacts.py",
    "scripts/check_github_actions_policy.py",
    "uv.lock",
)

ALLOWED_LICENSES = {
    "BSD-2-Clause",
    "BSD-3-Clause",
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "PSF-2.0",
    "Apache-2.0 OR BSD-2-Clause",
    "Apache-2.0 OR BSD-3-Clause",
}

BLOCKED_LICENSE_TOKENS = {
    "AGPL",
    "GPL",
    "LGPL",
    "MPL",
    "NC",
    "ND",
    "NOASSERTION",
    "SSPL",
    "UNKNOWN",
}


class RepositoryComplianceTests(unittest.TestCase):
    def test_required_repository_files_exist(self) -> None:
        missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
        self.assertEqual([], missing, f"missing required files: {missing}")

    def test_python_and_dependency_lock_are_fixed(self) -> None:
        python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        self.assertRegex(python_version, r"^3\.12\.\d+$")

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(">=3.12,<3.13", pyproject["project"]["requires-python"])

        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        self.assertEqual(1, lock["version"])
        self.assertEqual("==3.12.*", lock["requires-python"])

    def test_every_locked_distribution_has_an_approved_inventory_entry(self) -> None:
        inventory = json.loads(
            (ROOT / "compliance/dependencies.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, inventory["schema_version"])

        records = inventory["dependencies"]
        self.assertTrue(records)
        by_key = {(item["name"].lower(), item["version"]): item for item in records}

        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        locked = {
            (item["name"].lower(), item["version"])
            for item in lock["package"]
            if item.get("source", {}).get("registry")
        }
        self.assertEqual(
            locked,
            set(by_key),
            "inventory must match all registry packages",
        )

        for key, item in by_key.items():
            with self.subTest(dependency=key):
                self.assertIn(item["kind"], {"runtime", "development"})
                self.assertIn(item["license"], ALLOWED_LICENSES)
                upper_license = item["license"].upper()
                self.assertFalse(
                    any(token in upper_license for token in BLOCKED_LICENSE_TOKENS)
                )
                self.assertTrue(item["source"].startswith("https://"))
                self.assertTrue(item["license_source"].startswith("https://"))
                self.assertEqual("uv.lock", item["integrity_source"])
                self.assertTrue(item["reviewed_on"])

        locked_packages = {
            (item["name"].lower(), item["version"]): item
            for item in lock["package"]
            if item.get("source", {}).get("registry")
        }
        for key, package in locked_packages.items():
            with self.subTest(locked_artifacts=key):
                artifacts = []
                if package.get("sdist"):
                    artifacts.append(package["sdist"])
                artifacts.extend(package.get("wheels", []))
                self.assertTrue(artifacts, "each locked package needs artifacts")
                for artifact in artifacts:
                    self.assertRegex(artifact["hash"], r"^sha256:[0-9a-f]{64}$")

    def test_development_state_explicitly_blocks_public_release(self) -> None:
        status = json.loads(
            (ROOT / "compliance/release-status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, status["schema_version"])
        self.assertEqual("PUBLIC_RELEASE_BLOCKED", status["state"])
        blockers = {item["id"]: item for item in status["blockers"]}
        self.assertEqual(
            {
                "brand_asset_rights_unverified",
                "canonical_repository_url_unconfigured",
                "maintainer_identity_unverified",
                "coc_primary_channel_unconfigured",
                "coc_alternate_channel_unconfigured",
                "security_private_channel_unconfigured",
            },
            set(blockers),
        )
        self.assertTrue(all(not item["resolved"] for item in blockers.values()))
        self.assertTrue(all(item["evidence"] is None for item in blockers.values()))
        self.assertNotIn("example.com", json.dumps(status).lower())

    def test_community_documents_do_not_invent_contacts(self) -> None:
        combined = "\n".join(
            (ROOT / name).read_text(encoding="utf-8")
            for name in ("CODE_OF_CONDUCT.md", "SECURITY.md", "CONTRIBUTING.md")
        ).lower()
        self.assertNotIn("security@example", combined)
        self.assertNotIn("maintainer@example", combined)
        self.assertNotIn("info@ethicalsource.dev", combined)

    def test_development_version_metadata_is_consistent(self) -> None:
        from academic_pdf_en_zh_reader.constants import __version__

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cff_values: dict[str, str] = {}
        for line in (ROOT / "CITATION.cff").read_text(encoding="utf-8").splitlines():
            if not line or line[0].isspace():
                continue
            key, separator, value = line.partition(":")
            if separator:
                cff_values[key] = value.strip().strip('"')

        self.assertEqual(pyproject["project"]["version"], __version__)
        self.assertEqual(__version__, cff_values["version"])
        self.assertNotIn("repository-code", cff_values)

    def test_repository_text_does_not_expose_a_local_windows_profile(self) -> None:
        ignored_parts = {
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            ".test-tmp",
            ".tools",
            ".venv",
            "dist",
            "output",
            "tmp",
        }
        text_suffixes = {".cff", ".json", ".md", ".py", ".toml", ".yaml", ".yml"}
        windows_profile = ("c:" + "\\users\\").casefold()
        windows_profile_forward = ("c:" + "/users/").casefold()

        for path in ROOT.rglob("*"):
            relative_parts = path.relative_to(ROOT).parts
            if (
                not path.is_file()
                or path.suffix.casefold() not in text_suffixes
                or ignored_parts.intersection(relative_parts)
                or any(
                    part.startswith((".audit-", ".pytest-", ".task", ".test-", ".tmp-"))
                    for part in relative_parts
                )
            ):
                continue
            content = path.read_text(encoding="utf-8").casefold()
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(windows_profile, content)
                self.assertNotIn(windows_profile_forward, content)

    def test_third_party_document_reuse_is_in_the_central_inventories(self) -> None:
        upstreams = (ROOT / "UPSTREAMS.md").read_text(encoding="utf-8")
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for content in (upstreams, notices):
            self.assertIn("Contributor Covenant", content)
            self.assertIn("CC-BY-SA-4.0", content)

    def test_test_code_uses_the_project_code_license(self) -> None:
        for path in (ROOT / "tests").rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn(
                    "SPDX-License-" + "Identifier: Apache-2.0",
                    content,
                )

    def test_copyleft_compliance_tool_is_isolated_from_project_lock(self) -> None:
        tools = json.loads(
            (ROOT / "compliance/external-tools.json").read_text(encoding="utf-8")
        )["tools"]
        by_name = {item["name"]: item for item in tools}
        self.assertEqual({"reuse", "uv"}, set(by_name))
        self.assertFalse(by_name["reuse"]["included_in_release"])
        self.assertIn("GPL-3.0-or-later", by_name["reuse"]["license"])
        self.assertIn("isolated", by_name["reuse"]["scope"])

        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        locked_names = {item["name"] for item in lock["package"]}
        self.assertNotIn("reuse", locked_names)
        self.assertNotIn("uv", locked_names)

    def test_release_readiness_has_distinct_development_and_release_modes(self) -> None:
        from scripts.check_release_readiness import assess_release_readiness

        development = assess_release_readiness(ROOT, mode="development")
        release = assess_release_readiness(ROOT, mode="release")
        self.assertTrue(development.ok, development.errors)
        self.assertFalse(release.ok)
        self.assertTrue(release.errors)
        release_errors = "\n".join(release.errors)
        for relative in (
            "README.md",
            "PRIVACY.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
        ):
            self.assertIn(relative, release_errors)


if __name__ == "__main__":
    unittest.main()
