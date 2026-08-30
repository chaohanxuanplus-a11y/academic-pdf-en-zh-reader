# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.check_github_actions_policy import validate, validate_workflow_text

ROOT = Path(__file__).resolve().parents[2]


class GitHubActionsPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = json.loads(
            (ROOT / "compliance" / "github-actions.json").read_text(encoding="utf-8")
        )

    def test_repository_workflows_pass_policy(self) -> None:
        self.assertEqual((), validate(ROOT))

    def test_every_action_is_exactly_pinned_and_version_documented(self) -> None:
        workflows = tuple((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertEqual(
            {"ci.yml", "dependency-review.yml", "release.yml"},
            {path.name for path in workflows},
        )
        uses_re = re.compile(
            r"^\s*-?\s*uses:\s*([^\s@]+)@([0-9a-f]{40})\s+#\s+(v[^\s]+)\s*$",
            re.MULTILINE,
        )
        expected = {
            (path, item["commit"], item["version"])
            for item in self.inventory["actions"]
            for path in item["uses"]
        }
        actual: set[tuple[str, str, str]] = set()
        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            actual.update(uses_re.findall(text))
            self.assertNotRegex(text, r"(?m)^\s*-?\s*uses:\s*[^\n#]+@(v|main|master)\b")
        self.assertEqual(expected, actual)

    def test_windows_runtime_uses_official_cpython(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        quality, remaining = ci.split("  windows-runtime:\n", 1)
        windows, _remaining_jobs = remaining.split("\n  reuse:\n", 1)
        reviewed = next(
            item
            for item in self.inventory["actions"]
            if item["repository"] == "actions/setup-python"
        )

        self.assertNotIn("actions/setup-python@", quality)
        self.assertIn('python-version: "3.12.10"', windows)
        self.assertIn(
            f"actions/setup-python@{reviewed['commit']} # {reviewed['version']}",
            windows,
        )
        self.assertIn(
            "robocopy.exe $source $runtime /E /COPY:DAT /DCOPY:DAT",
            windows,
        )
        self.assertIn(
            "icacls.exe $runtime /inheritance:r /grant:r $grant /T /Q",
            windows,
        )
        self.assertIn("*${sid}:(OI)(CI)RX", windows)
        self.assertIn(
            'uv sync --frozen --all-groups --python "$env:ACADEMIC_PDF_CI_PYTHON"',
            windows,
        )
        self.assertIn(
            'uv run --python "$env:ACADEMIC_PDF_CI_PYTHON" --frozen pytest -q',
            windows,
        )
        self.assertIn(
            "tests/security/test_windows_worker_limits.py::"
            "test_worker_uses_restricted_token_and_enforced_job_limits",
            windows,
        )
        self.assertNotIn('--python "$env:pythonLocation\\python.exe"', windows)

    def test_mutable_or_unreviewed_action_is_rejected(self) -> None:
        original = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        mutable = re.sub(
            r"actions/checkout@[0-9a-f]{40}\s+#\s+v6\.0\.2",
            "actions/checkout@v6 # v6.0.2",
            original,
            count=1,
        )
        errors = validate_workflow_text(
            ".github/workflows/ci.yml", mutable, self.inventory
        )
        self.assertTrue(
            any("full 40-character commit SHA" in error for error in errors)
        )

        unreviewed = original.replace(
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
            "example/unknown@1111111111111111111111111111111111111111 # v1.0.0",
            1,
        )
        errors = validate_workflow_text(
            ".github/workflows/ci.yml", unreviewed, self.inventory
        )
        self.assertTrue(
            any("not in compliance/github-actions.json" in error for error in errors)
        )

    def test_dangerous_triggers_permissions_and_script_interpolation_are_rejected(
        self,
    ) -> None:
        original = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        mutations = (
            (
                original.replace("pull_request:", "pull_request_target:", 1),
                "pull_request_target",
            ),
            (
                original.replace("permissions: {}", "permissions: write-all", 1),
                "write-all",
            ),
            (
                original.replace(
                    "uv run --frozen pytest -q",
                    "uv run --frozen pytest -q "
                    '"${{ github.event.pull_request.title }}"',
                    1,
                ),
                "expressions are forbidden inside run scripts",
            ),
        )
        for mutated, expected in mutations:
            with self.subTest(expected=expected):
                errors = validate_workflow_text(
                    ".github/workflows/ci.yml", mutated, self.inventory
                )
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_release_is_manual_fail_closed_and_never_publishes(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", release)
        self.assertNotIn("release create", release.casefold())
        self.assertNotRegex(release.casefold(), r"\bgit\s+(push|tag)\b")
        self.assertNotIn("actions/create-release", release)
        self.assertIn("--mode release", release)
        self.assertLess(
            release.index("--mode release"),
            release.index("uv sync --frozen"),
            "release readiness must run before dependency sync and packaging",
        )
        self.assertIn("spdx-json", release)
        self.assertIn("pypa/gh-action-pip-audit", release)
        self.assertEqual(2, len(re.findall(r"(?m)^\s+file: dist/release/", release)))
        self.assertNotIn("path: dist/release/academic-pdf", release)
        self.assertIn("SHA256SUMS", release)
        self.assertIn("asset-manifest.json", release)
        self.assertIn("PUBLIC_RELEASE_BLOCKED", release)
        self.assertIn("actions/upload-artifact", release)

        dependency_review = (
            ROOT / ".github" / "workflows" / "dependency-review.yml"
        ).read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        public_repository_only = "${{ github.event.repository.private == false }}"
        self.assertIn(f"codeql:\n    if: {public_repository_only}", ci)
        self.assertIn(
            f"- if: {public_repository_only}\n"
            "        uses: actions/dependency-review-action@",
            dependency_review,
        )
        for license_id in (
            "AGPL-1.0-or-later",
            "AGPL-3.0-or-later",
            "GPL-2.0-or-later",
            "GPL-3.0-or-later",
            "LGPL-2.1-or-later",
            "LGPL-3.0-or-later",
        ):
            self.assertIn(license_id, dependency_review)

    def test_pr_template_and_issue_entrypoints_preserve_governance_boundaries(
        self,
    ) -> None:
        template = (
            (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md")
            .read_text(encoding="utf-8")
            .casefold()
        )
        for field in (
            "signed-off-by",
            "ai-assisted",
            "affected files",
            "human review",
            "tests",
            "public-code match",
            "confidential",
            "user paper",
        ):
            self.assertIn(field, template)

        issue_config = (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("blank_issues_enabled: false", issue_config)
        self.assertIn("contact_links: []", issue_config)
        self.assertNotIn("@", issue_config)
        self.assertNotIn("https://", issue_config)


if __name__ == "__main__":
    unittest.main()
