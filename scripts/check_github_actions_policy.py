# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WORKFLOW_PATHS = (
    ".github/workflows/ci.yml",
    ".github/workflows/dependency-review.yml",
    ".github/workflows/release.yml",
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+){1,2}$")
USES_LINE_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s@]+)@([^\s#]+)(?:\s+#\s+(\S+))?\s*$")
SPDX_HEADER = "SPDX-License-" + "Identifier: Apache-2.0"
WRITE_PERMISSION_RE = re.compile(
    r"^\s+(actions|checks|contents|deployments|discussions|id-token|issues|"
    r"packages|pages|pull-requests|repository-projects|statuses):\s*write\s*$",
    re.MULTILINE,
)


def _action_map(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    actions = inventory.get("actions", [])
    if not isinstance(actions, list):
        return result
    for item in actions:
        if not isinstance(item, dict):
            continue
        for use_path in item.get("uses", []):
            if isinstance(use_path, str):
                result[use_path] = item
    return result


def _run_scripts(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    scripts: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^([ \t]*)(-[ \t]+)?run:[ \t]*(.*)$", line)
        if match is None:
            index += 1
            continue
        indent = len(match.group(1)) + len(match.group(2) or "")
        value = match.group(3).strip()
        if value not in {"|", ">", "|-", ">-"}:
            scripts.append(value)
            index += 1
            continue
        block: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                break
            block.append(candidate)
            index += 1
        scripts.append("\n".join(block))
    return tuple(scripts)


def _release_job_bypass_errors(
    relative_path: str, job_name: str, body: str
) -> list[str]:
    errors: list[str] = []
    control_key = re.search(
        r"""(?im)(?:^|\{|,)[ \t]*(?:-[ \t]*)?["']?"""
        r"""(if|continue-on-error|timeout(?:-minutes)?)["']?[ \t]*:""",
        body,
    )
    if control_key is not None:
        errors.append(
            f"{relative_path}: {job_name} must not use "
            f"{control_key.group(1)} bypass controls"
        )

    checkout_count = body.count("actions/checkout@")
    if checkout_count != 1:
        errors.append(f"{relative_path}: {job_name} must use exactly one checkout")
    if re.search(r"""(?im)(?:^|\{|,)\s*["']?(repository|ref)["']?\s*:""", body):
        errors.append(f"{relative_path}: {job_name} checkout must use the workflow SHA")
    for line in body.splitlines():
        if re.search(r"(?i)\bgit(?:\.exe)?\b", line) is None:
            continue
        if (
            job_name == "windows-2025-production-gate"
            and line.strip() == "$sha = git rev-parse HEAD"
        ):
            continue
        errors.append(
            f"{relative_path}: {job_name} must not change the checked-out "
            "source or run non-binding git commands"
        )
        return errors
    return errors


def _inventory_errors(inventory: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if inventory.get("schema_version") != 1:
        errors.append("github-actions inventory schema_version must be 1")
    actions = inventory.get("actions")
    if not isinstance(actions, list) or not actions:
        return errors + ["github-actions inventory actions must be a non-empty list"]

    seen_paths: set[str] = set()
    for item in actions:
        if not isinstance(item, dict):
            errors.append("github-actions inventory records must be objects")
            continue
        repository = item.get("repository")
        version = item.get("version")
        commit = item.get("commit")
        uses = item.get("uses")
        if not isinstance(repository, str) or repository.count("/") != 1:
            errors.append("each action repository must be owner/name")
        if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
            errors.append(f"invalid reviewed action version: {version!r}")
        if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
            errors.append(f"invalid reviewed action commit: {repository!r}")
        if not isinstance(uses, list) or not uses:
            errors.append(f"reviewed action has no uses paths: {repository!r}")
            continue
        for use_path in uses:
            if not isinstance(use_path, str) or not use_path.startswith(
                f"{repository}"
            ):
                errors.append(f"invalid uses path for {repository!r}: {use_path!r}")
                continue
            if use_path in seen_paths:
                errors.append(f"duplicate reviewed uses path: {use_path}")
            seen_paths.add(use_path)
        source = item.get("source")
        if not isinstance(source, str) or not source.startswith("https://github.com/"):
            errors.append(f"action source must be a GitHub HTTPS URL: {repository!r}")
        if not isinstance(item.get("license"), str) or not item["license"].strip():
            errors.append(f"action license is missing: {repository!r}")
        if not isinstance(item.get("purpose"), str) or not item["purpose"].strip():
            errors.append(f"action purpose is missing: {repository!r}")
    return errors


def validate_workflow_text(
    relative_path: str,
    text: str,
    inventory: dict[str, Any],
) -> tuple[str, ...]:
    errors: list[str] = []
    action_map = _action_map(inventory)

    if SPDX_HEADER not in text:
        errors.append(f"{relative_path}: SPDX license header is missing")
    if re.search(r"(?m)^\s*pull_request_target\s*:", text):
        errors.append(f"{relative_path}: pull_request_target is forbidden")
    if re.search(r"(?m)^permissions:\s*\{\}\s*$", text) is None:
        errors.append(f"{relative_path}: top-level permissions must be empty")
    if re.search(r"(?m)^\s*permissions:\s*write-all\s*$", text):
        errors.append(f"{relative_path}: permissions write-all is forbidden")
    for match in WRITE_PERMISSION_RE.finditer(text):
        errors.append(
            f"{relative_path}: {match.group(1)} write permission is not approved"
        )

    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        if "uses:" not in line:
            continue
        match = USES_LINE_RE.fullmatch(line)
        if match is None:
            errors.append(
                f"{relative_path}:{line_number}: every uses reference needs a full "
                "40-character commit SHA and adjacent version comment"
            )
            continue
        use_path, commit, version = match.groups()
        if SHA_RE.fullmatch(commit) is None:
            errors.append(
                f"{relative_path}:{line_number}: action must use a full "
                "40-character commit SHA"
            )
            continue
        reviewed = action_map.get(use_path)
        if reviewed is None:
            errors.append(
                f"{relative_path}:{line_number}: {use_path} is not in "
                "compliance/github-actions.json"
            )
            continue
        if commit != reviewed.get("commit"):
            errors.append(
                f"{relative_path}:{line_number}: unreviewed commit for {use_path}"
            )
        if version != reviewed.get("version"):
            errors.append(
                f"{relative_path}:{line_number}: version comment does not match "
                "inventory"
            )
        if use_path == "actions/checkout":
            following = "\n".join(lines[line_number : line_number + 3])
            if "persist-credentials: false" not in following:
                errors.append(
                    f"{relative_path}:{line_number}: checkout credentials must not "
                    "persist"
                )

    for script in _run_scripts(text):
        if "${{" in script:
            errors.append(
                f"{relative_path}: expressions are forbidden inside run scripts"
            )
        if re.search(r"(?im)\b(eval|bash\s+-c|sh\s+-c)\b", script):
            errors.append(f"{relative_path}: dynamic shell evaluation is forbidden")
        if re.search(r"(?im)\b(curl|wget)\b.*\|", script):
            errors.append(f"{relative_path}: remote pipe-to-shell is forbidden")

    if relative_path.endswith("ci.yml"):
        current_gate = "check_release_readiness.py --mode current"
        if current_gate not in text:
            errors.append(f"{relative_path}: CI must use the current readiness mode")
        if "check_release_readiness.py --mode development" in text:
            errors.append(
                f"{relative_path}: CI must not pin the development readiness mode"
            )

    if relative_path.endswith("release.yml"):
        trigger_match = re.search(r'(?ms)^"on":\s*\n(?P<body>.*?)^permissions:', text)
        trigger_body = trigger_match.group("body") if trigger_match else ""
        if re.search(
            r"(?m)^\s*(push|pull_request|pull_request_target|schedule|release|"
            r"workflow_run):\s*$",
            trigger_body,
        ):
            errors.append(f"{relative_path}: release workflow must be manual only")
        if "workflow_dispatch:" not in trigger_body:
            errors.append(f"{relative_path}: release workflow needs workflow_dispatch")

        lowered = text.casefold()
        forbidden_publish = (
            "actions/create-release",
            "gh release create",
            "upload-release-asset",
        )
        for token in forbidden_publish:
            if token in lowered:
                errors.append(
                    f"{relative_path}: publishing operation is forbidden: {token}"
                )
        if re.search(r"(?im)\bgit\s+(push|tag)\b", text):
            errors.append(f"{relative_path}: git push/tag is forbidden")

        scripts = _run_scripts(text)
        if not scripts or "check_release_readiness.py --mode release" not in scripts[0]:
            errors.append(
                f"{relative_path}: the first run step must be the "
                "release-readiness gate"
            )
        required = (
            "uv sync --frozen",
            "pytest -q",
            "ruff check .",
            "uv export --frozen",
            "pypa/gh-action-pip-audit@",
            "build_release_artifacts.py",
            "spdx-json",
            "asset-manifest.json",
            "SHA256SUMS",
            "PUBLIC_RELEASE_BLOCKED",
            "actions/upload-artifact@",
        )
        for token in required:
            if token not in text:
                errors.append(
                    f"{relative_path}: required release control missing: {token}"
                )

        job_pattern = (
            r"(?ms)^  {job_id}:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\s*\n|\Z)"
        )
        for protected_job in (
            "build-candidates",
            "windows-2025-production-gate",
        ):
            if len(re.findall(rf"(?m)^  {re.escape(protected_job)}:\s*$", text)) != 1:
                errors.append(
                    f"{relative_path}: {protected_job} job must be declared "
                    "exactly once"
                )
        windows_match = re.search(
            job_pattern.format(job_id="windows-2025-production-gate"), text
        )
        if windows_match is None:
            errors.append(
                f"{relative_path}: release workflow requires the "
                "windows-2025-production-gate job"
            )
            windows_body = ""
        else:
            windows_body = windows_match.group("body")
            windows_scripts = _run_scripts(windows_body)
            if not re.search(r"(?m)^    runs-on: windows-2025\s*$", windows_body):
                errors.append(
                    f"{relative_path}: Windows production gate must use windows-2025"
                )
            if 'python-version: "3.12.10"' not in windows_body:
                errors.append(
                    f"{relative_path}: Windows production gate must pin CPython 3.12.10"
                )
            probe_command = (
                'uv run --python "$env:pythonLocation\\python.exe" --frozen '
                "python scripts/probe_worker_sandbox.py"
            )
            if probe_command not in windows_scripts:
                errors.append(
                    f"{relative_path}: Windows production gate must directly run "
                    "scripts/probe_worker_sandbox.py"
                )
            finish_command = (
                'uv run --python "$env:pythonLocation\\python.exe" --frozen pytest -q '
                "tests/security/test_finish_worker_boundary.py::"
                "test_production_finish_completes_render_and_qa_in_lpac"
            )
            if finish_command not in windows_scripts:
                errors.append(
                    f"{relative_path}: Windows production gate must directly run the "
                    "production finish LPAC test"
                )
            trusted_outputs = (
                "lpac-probe-outcome: ${{ steps.lpac-probe.outcome }}",
                "lpac-finish-outcome: ${{ steps.lpac-finish.outcome }}",
                "lpac-tested-head-sha: ${{ steps.lpac-tested-head.outputs.sha }}",
            )
            for output in trusted_outputs:
                output_name = output.split(":", 1)[0]
                if (
                    output not in windows_body
                    or windows_body.count(f"{output_name}:") != 1
                ):
                    errors.append(
                        f"{relative_path}: Windows production gate output is not "
                        f"trusted: {output_name}"
                    )
            for step_id in ("lpac-tested-head", "lpac-probe", "lpac-finish"):
                if windows_body.count(f"id: {step_id}") != 1:
                    errors.append(
                        f"{relative_path}: Windows production gate requires the "
                        f"{step_id} step id"
                    )
            if not any(
                "git rev-parse HEAD" in script
                and '"sha=$sha" >> $env:GITHUB_OUTPUT' in script
                for script in windows_scripts
            ):
                errors.append(
                    f"{relative_path}: Windows production gate must report the "
                    "tested commit SHA"
                )
            tested_head_commands = [
                line.strip()
                for script in windows_scripts
                for line in script.splitlines()
                if line.strip() == "$sha = git rev-parse HEAD"
            ]
            if tested_head_commands != ["$sha = git rev-parse HEAD"]:
                errors.append(
                    f"{relative_path}: Windows production gate must use exactly one "
                    "fixed tested-HEAD git command"
                )
            if windows_body.find("id: lpac-tested-head") < windows_body.find(
                "id: lpac-finish"
            ):
                errors.append(
                    f"{relative_path}: tested commit SHA must be recorded after the "
                    "LPAC finish step"
                )

        build_match = re.search(job_pattern.format(job_id="build-candidates"), text)
        if build_match is None:
            errors.append(f"{relative_path}: build-candidates job is required")
            build_body = ""
        else:
            build_body = build_match.group("body")
            if not re.search(
                r"(?m)^    needs: windows-2025-production-gate\s*$", build_body
            ):
                errors.append(
                    f"{relative_path}: build-candidates must need "
                    "windows-2025-production-gate"
                )
            if len(re.findall(r"(?m)^    needs:\s*", build_body)) != 1:
                errors.append(
                    f"{relative_path}: build-candidates must declare exactly one "
                    "gate dependency"
                )

            trusted_gate_values = (
                ("RELEASE_LPAC_SIGNAL_SCHEMA", '"1"'),
                (
                    "RELEASE_LPAC_GATE_RESULT",
                    "${{ needs.windows-2025-production-gate.result }}",
                ),
                (
                    "RELEASE_LPAC_PROBE_OUTCOME",
                    "${{ needs.windows-2025-production-gate.outputs."
                    "lpac-probe-outcome }}",
                ),
                (
                    "RELEASE_LPAC_FINISH_OUTCOME",
                    "${{ needs.windows-2025-production-gate.outputs."
                    "lpac-finish-outcome }}",
                ),
                (
                    "RELEASE_LPAC_TESTED_HEAD_SHA",
                    "${{ needs.windows-2025-production-gate.outputs."
                    "lpac-tested-head-sha }}",
                ),
                ("RELEASE_LPAC_RUN_ID", "${{ github.run_id }}"),
                ("RELEASE_LPAC_RUN_ATTEMPT", "${{ github.run_attempt }}"),
            )
            job_env_matches = list(
                re.finditer(
                    r"(?m)^    env:[ \t]*\r?\n"
                    r"(?P<body>(?:^      [^\r\n]*(?:\r?\n|\Z))+)",
                    build_body,
                )
            )
            if len(job_env_matches) != 1:
                errors.append(
                    f"{relative_path}: build-candidates must declare exactly one "
                    "job-level live-gate environment"
                )
            job_env_body = (
                job_env_matches[0].group("body") if len(job_env_matches) == 1 else ""
            )
            for variable, expression in trusted_gate_values:
                expected_line = f"      {variable}: {expression}"
                key_occurrences = re.findall(
                    rf"(?im)(?:^|\{{|,)[ \t]*[\"']?{re.escape(variable)}"
                    r"[\"']?[ \t]*:",
                    build_body,
                )
                if expected_line not in job_env_body or len(key_occurrences) != 1:
                    errors.append(
                        f"{relative_path}: release readiness and artifact build must "
                        "inherit exactly one trusted job-level Windows gate value: "
                        f"{variable}"
                    )

        for job_name, job_body in (
            ("build-candidates", build_body),
            ("windows-2025-production-gate", windows_body),
        ):
            errors.extend(_release_job_bypass_errors(relative_path, job_name, job_body))

        protected_github_variables = (
            "GITHUB_ACTIONS",
            "GITHUB_EVENT_NAME",
            "GITHUB_REPOSITORY",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_RUN_ID",
            "GITHUB_SERVER_URL",
            "GITHUB_SHA",
            "GITHUB_WORKFLOW_REF",
        )
        for variable in protected_github_variables:
            if re.search(rf"(?m)^\s+{variable}:\s*", build_body):
                errors.append(
                    f"{relative_path}: build-candidates must not override {variable}"
                )

    return tuple(errors)


def validate(root: Path) -> tuple[str, ...]:
    errors: list[str] = []
    inventory_path = root / "compliance" / "github-actions.json"
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (f"cannot read GitHub Actions inventory: {exc}",)
    if not isinstance(inventory, dict):
        return ("github-actions inventory must be an object",)
    errors.extend(_inventory_errors(inventory))

    workflow_dir = root / ".github" / "workflows"
    actual_paths = {
        path.relative_to(root).as_posix() for path in workflow_dir.glob("*.yml")
    }
    expected_paths = set(WORKFLOW_PATHS)
    if actual_paths != expected_paths:
        errors.append(
            f"workflow set mismatch; missing={sorted(expected_paths - actual_paths)}, "
            f"extra={sorted(actual_paths - expected_paths)}"
        )

    used_actions: set[str] = set()
    for relative_path in WORKFLOW_PATHS:
        path = root / relative_path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read {relative_path}: {exc}")
            continue
        errors.extend(validate_workflow_text(relative_path, text, inventory))
        for line in text.splitlines():
            match = USES_LINE_RE.fullmatch(line)
            if match is not None and SHA_RE.fullmatch(match.group(2)):
                used_actions.add(match.group(1))

    reviewed_actions = set(_action_map(inventory))
    if used_actions != reviewed_actions:
        errors.append(
            "workflow/inventory action mismatch; "
            f"unused={sorted(reviewed_actions - used_actions)}, "
            f"unreviewed={sorted(used_actions - reviewed_actions)}"
        )

    required_text = {
        ".github/workflows/ci.yml": (
            "actions/setup-python@",
            "uv sync --frozen --all-groups",
            'uv sync --frozen --all-groups --python "$env:pythonLocation\\python.exe"',
            'uv run --python "$env:pythonLocation\\python.exe" --frozen pytest -q',
            "test_worker_uses_restricted_token_and_enforced_job_limits",
            "python scripts/probe_worker_sandbox.py",
            "pytest -q",
            "ruff check .",
            "ruff format --check .",
            "check_dependency_policy.py",
            "check_release_readiness.py --mode current",
            "check_github_actions_policy.py",
            "fsfe/reuse-action@",
            "github/codeql-action/init@",
            "github/codeql-action/analyze@",
            "- if: ${{ github.event.repository.private == true }}\n"
            "        uses: github/codeql-action/analyze@",
            "upload: never",
            "- if: ${{ github.event.repository.private == false }}\n"
            "        uses: github/codeql-action/analyze@",
            "upload: always",
        ),
        ".github/workflows/dependency-review.yml": (
            "actions/dependency-review-action@",
            "- if: ${{ github.event.repository.private == false }}\n"
            "        uses: actions/dependency-review-action@",
            "uv export --frozen",
            "pypa/gh-action-pip-audit@",
        ),
    }
    for relative_path, tokens in required_text.items():
        try:
            text = (root / relative_path).read_text(encoding="utf-8")
        except OSError:
            continue
        for token in tokens:
            if token not in text:
                errors.append(f"{relative_path}: required CI control missing: {token}")

    return tuple(errors)


def main() -> int:
    errors = validate(Path.cwd())
    if not errors:
        print("GitHub Actions policy: OK")
        return 0
    print("GitHub Actions policy: FAILED")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
