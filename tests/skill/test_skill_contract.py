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
    assert "ordinary text" in description

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
        "SECURITY.md",
        "compliance/release-status.json",
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


def test_skill_routes_only_the_two_production_front_doors() -> None:
    content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    lowered = content.casefold()

    assert "scripts/prepare_job.py" in content
    assert "scripts/finish_job.py" in content
    assert "whole `units.json`" in lowered
    assert "batch" in lowered and "stop" in lowered
    assert all(
        wrapper in content
        for wrapper in (
            "scripts/preflight.py",
            "scripts/extract.py",
            "scripts/solve_layout.py",
            "scripts/qa_pdf.py",
        )
    )
    assert "diagnostic" in lowered
    assert "internal stdout" in lowered
    assert "final pdf" in lowered


def test_runbook_uses_the_safe_binding_helper_with_private_templates() -> None:
    runbook_path = ROOT / "references" / "runbook.md"
    runbook = runbook_path.read_text(encoding="utf-8")
    lowered = runbook.casefold()

    assert "outside the repository" in lowered
    assert "outside the managed job" in lowered
    assert "absolute" in lowered and "output" in lowered
    assert "untrusted data" in lowered
    assert all(
        field in runbook
        for field in (
            "review.translation_hash",
            "units_hash",
            "translation_hash",
            "review_hash",
            "ambiguity_key.id",
        )
    )
    assert "result.artifact_hashes.units" in runbook
    assert "scripts/agent_artifacts.py" in runbook
    assert (ROOT / "scripts" / "agent_artifacts.py").is_file()
    assert "canonical-hash --schema translation" in runbook
    assert "canonical-hash --schema review" in runbook
    assert "canonical-hash --schema semantic-candidates" in runbook
    assert "ambiguity-key --input" in runbook
    assert "not implemented" not in lowered
    assert "whitespace" in lowered and "key order" in lowered
    assert not re.search(
        r"(?m)^\s*(?:python|py)\s+-c\b",
        runbook,
    )

    templates = [
        json.loads(raw)
        for raw in re.findall(r"```json\n(.*?)\n```", runbook, re.DOTALL)
    ]
    assert {template.get("artifact_kind") for template in templates} >= {
        "translation",
        "review",
        "semantic-candidates",
    }


def test_public_status_is_locally_accepted_candidate_not_released() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    privacy = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    plan = (
        ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-27-academic-pdf-bilingual-reader-implementation-plan.md"
    ).read_text(encoding="utf-8")

    for public_doc in (readme, privacy):
        top = "\n".join(public_doc.splitlines()[:25]).casefold()
        assert "local candidate" in top
        assert "passed local acceptance" in top
        assert "public release" in top and "blocked" in top
        assert "production-ready" not in top
    plan_top = "\n".join(plan.splitlines()[:15])
    assert "本地候选实现" in plan_top
    assert "验收已完成" in plan_top
    assert "公开发布仍阻断" in plan_top


def test_schema_guide_covers_parent_bound_semantic_candidates() -> None:
    guide = (ROOT / "references" / "schemas.md").read_text(encoding="utf-8")

    assert "semantic-candidates.json" in guide
    assert all(
        field in guide for field in ("units_hash", "translation_hash", "review_hash")
    )
