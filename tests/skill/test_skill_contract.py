# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _frontmatter(content: str) -> dict[str, str]:
    match = re.match(r"\A---\n(.*?)\n---\n", content, re.DOTALL)
    assert match is not None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    return fields


def _quoted_yaml_value(content: str, key: str) -> str:
    match = re.search(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', content, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_skill_metadata_is_discriminating_and_discoverable() -> None:
    content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    metadata = _frontmatter(content)
    description = metadata["description"].casefold()

    assert metadata["name"] == "academic-pdf-en-zh-reader"
    assert all(term in description for term in ("a4", "a3", "academic", "pdf"))
    assert "born-digital" in description

    agent_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "allow_implicit_invocation: true" in agent_yaml
    assert "$academic-pdf-en-zh-reader" in _quoted_yaml_value(
        agent_yaml, "default_prompt"
    )


def test_skill_entrypoint_is_short_and_routes_to_maintained_references() -> None:
    content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    links = dict(re.findall(r"\[([^]]+)]\(([^)]+)\)", content))
    required = {
        "references/product-contract.md",
        "references/translation-policy.md",
        "references/layout-policy.md",
        "references/qa-policy.md",
        "references/schemas.md",
        "references/runbook.md",
        "DISCLAIMER.md",
        "PRIVACY.md",
    }

    assert required.issubset(set(links.values()))
    assert all((ROOT / relative).is_file() for relative in required)
    assert len(content.splitlines()) <= 120


def test_rights_documents_route_to_the_canonical_disclaimer() -> None:
    disclaimer = ROOT / "DISCLAIMER.md"
    assert disclaimer.is_file()

    for relative in ("README.md", "PRIVACY.md", "references/product-contract.md"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        links = {target for _, target in re.findall(r"\[([^]]+)]\(([^)]+)\)", content)}
        expected = (
            "../DISCLAIMER.md"
            if relative == "references/product-contract.md"
            else "DISCLAIMER.md"
        )
        assert expected in links


def test_repository_stage_wrappers_exist_without_a_broken_package_entrypoint() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "package = false" in pyproject
    assert "[project.scripts]" not in pyproject
    assert (ROOT / "scripts" / "prepare_job.py").is_file()
    assert (ROOT / "scripts" / "finish_job.py").is_file()
    assert (ROOT / "scripts" / "validate_translation.py").is_file()
    assert (ROOT / "scripts" / "solve_layout.py").is_file()


def test_skill_routes_paragraph_batches_focused_review_and_recovery():
    content = (ROOT / "SKILL.md").read_text(encoding="utf-8").casefold()
    assert "scripts/prepare_job.py" in content and "scripts/finish_job.py" in content
    assert "packets" in content and "assemble" in content
    assert "reuse" in content and "retry" in content
    assert "known issues" in content


def test_runbook_uses_private_compact_drafts_and_program_owned_bindings():
    content = (ROOT / "references/runbook.md").read_text(encoding="utf-8").casefold()
    assert "outside the repository" in content and "absolute" in content
    assert "agent_artifacts.py assemble" in content
    assert "extracted checkpoint" in content
    assert "only prepare and finish" in content


def test_public_status_matches_the_declared_release_lifecycle() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    status = json.loads(
        (ROOT / "compliance" / "release-status.json").read_text(encoding="utf-8")
    )
    state = status["state"]
    assert state in {"PUBLIC_RELEASE_BLOCKED", "PUBLIC_RELEASE_READY"}
    if state == "PUBLIC_RELEASE_BLOCKED":
        for public_doc in (readme, privacy):
            top = "\n".join(public_doc.splitlines()[:25]).casefold()
            assert "local candidate" in top
            assert "passed local acceptance" in top
            assert "public release" in top and "blocked" in top
            assert "production-ready" not in top
    else:
        assert "public release remains blocked" not in readme.casefold()
        assert "public release remains blocked" not in privacy.casefold()
        assert "not a release or deployment claim" not in privacy.casefold()


def test_schema_guide_covers_parent_bound_semantic_candidates() -> None:
    guide = (ROOT / "references" / "schemas.md").read_text(encoding="utf-8")

    assert "semantic-candidates" in guide
    assert "units_hash" in guide and "assemb" in guide
    assert "reviewed_unit_ids" in guide and "Frame-graph/layout" in guide
