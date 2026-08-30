<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# academic-pdf-en-zh-reader

Development repository for a local candidate Codex Skill that turns supported
English academic PDFs into A3 landscape reading copies: a vector-preserved,
A4-normalized source page remains on the left and a complete Chinese translation
is laid out on the right.

## Current status

The local candidate implementation has passed local acceptance. Public release
remains blocked until the documented maintainer identity and private reporting
channels are truthfully configured. The repository contains candidate preflight, extraction,
topology, Agent-artifact binding, translation/review contracts, annotation,
layout, rendering, QA, job-state, and delivery components, but their presence is
not a release claim. See
[`compliance/release-status.json`](compliance/release-status.json) for the
machine-readable release blockers.

Before relying on or distributing any generated file, read the project-wide
[content-rights and accuracy disclaimer](DISCLAIMER.md). Input acquisition,
Agent-assisted generation, and downstream publication are distinct
responsibilities; the Skill does not fetch or automatically publish papers.

The approved product design and implementation plan are:

- [`docs/superpowers/specs/2026-08-27-academic-pdf-bilingual-reader-skill-design.md`](docs/superpowers/specs/2026-08-27-academic-pdf-bilingual-reader-skill-design.md)
- [`docs/superpowers/plans/2026-08-27-academic-pdf-bilingual-reader-implementation-plan.md`](docs/superpowers/plans/2026-08-27-academic-pdf-bilingual-reader-implementation-plan.md)

## Candidate v1 boundary

The candidate accepts one unencrypted, born-digital, English-majority academic
PDF with a usable text layer and interpretable visible page boxes. Each displayed
CropBox is fitted into a managed A4 canvas before extraction: pages that already
fit remain at 1:1 and receive centered blank padding on deficient axes; a page
that exceeds either A4 axis is uniformly scaled down only enough to fit. Pages
are never enlarged, cropped, or stretched, and a non-A4 size alone does not stop
the job. OCR, scanned papers, public hosting, batch services, and redistribution
of user papers are outside v1.

The hard product invariants are complete translation, correct reading order,
mirrored column topology, fixed body font size, no overlap, embedded approved
fonts, no garbled text, and fail-closed handling of unsupported inputs.

When terminal reference entries leave a safe unused right-side region, the
output may place one fixed project identity and condensed disclaimer card there.
That card never replaces required translation, changes annotation budgets, or
creates an extra page.

## Local setup

Run commands from the repository root. The locked environment requires Python
3.12 (`>=3.12,<3.13`); [`.python-version`](.python-version) currently selects
3.12.13. Install `uv` first, then synchronize the exact runtime and development
dependencies recorded in `uv.lock`. Windows isolation CI additionally exercises
CPython 3.12.10, the newest official Windows x64 binary in the supported 3.12
line:

```text
uv sync --frozen --all-groups
```

Approved font files may already be present. To verify them, or to reconstruct
any missing font from its pinned, allowlisted upstream source, run the bootstrap
and embedding probe. The bootstrap needs network access only when an approved
font is missing; both commands verify the recorded identities rather than
accepting a system-font substitute.

```text
uv run --frozen python scripts/bootstrap_fonts.py
uv run --frozen python scripts/probe_reportlab_fonts.py
```

## Minimal local workflow

Create an absolute private managed root and a separate absolute private
Agent-artifact directory, both outside this repository. The Agent-artifact
directory must also be outside the managed job. Prepare one supported PDF with
the production preparation front door:

```text
uv run --frozen python scripts/prepare_job.py --managed-root ABSOLUTE_PRIVATE_MANAGED_ROOT --job-id SAFE_JOB_ID --source-pdf ABSOLUTE_SOURCE_PDF
```

Treat the resulting `units.json` as untrusted data. In the private Agent
directory, create the complete `translation.json`, `review.json`, and
`semantic-candidates.json` exactly as described in the
[local candidate runbook](references/runbook.md). Use
`scripts/agent_artifacts.py` for their validated canonical hashes and any
ambiguity keys; do not calculate bindings by hand or place paper text in a
command. Then finish the same job once:

```text
uv run --frozen python scripts/finish_job.py --managed-root ABSOLUTE_PRIVATE_MANAGED_ROOT --job-id SAFE_JOB_ID --source-pdf ABSOLUTE_SOURCE_PDF --translation-json ABSOLUTE_PRIVATE_TRANSLATION_JSON --review-json ABSOLUTE_PRIVATE_REVIEW_JSON --semantic-candidates-json ABSOLUTE_PRIVATE_SEMANTIC_CANDIDATES_JSON --output-pdf ABSOLUTE_FINAL_PDF
```

Only the validated final PDF is the ordinary user-facing result. Structured
stdout, hashes, ledgers, QA reports, the managed job, and Agent JSON are private
intermediates rather than deliverables.

## Development validation

The following commands reproduce the local quality gates used for this
candidate:

```text
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen python scripts/check_dependency_policy.py
uv run --frozen python scripts/check_release_readiness.py --mode development
uv run --frozen python scripts/check_github_actions_policy.py
```

Passing these checks does not clear the separately recorded public-release
blockers and is not permission to publish a release.

## Privacy and copyright

Candidate PDF processing, layout, rendering, and personal correction storage are
designed to run locally. Extracted paper text enters the current Agent model
context for translation and independent review; the project does not silently
call another translation API. The repository must never contain user papers,
ordinary journal screenshots, extracted full text, correction databases, or
unredacted logs.

The three Agent JSON artifacts can contain source-linked translations, review
findings, and annotations. Keep them at project-external absolute paths in the
private Agent directory: never commit them, attach them to an issue or pull
request, include them in a release archive, or present them as the final result.
"Local" describes this project's file processing, storage, and lack of a hidden
translation service; it does not mean that text sent to the current Agent model
is guaranteed to remain on-device. The applicable Agent runtime and account
data controls still govern that model context.

The project license does not grant rights to a user's source paper or generated
translation. Creating a translation does not by itself grant the right to
publish or redistribute it. The complete English controlling statement and
Chinese reference translation are in [`DISCLAIMER.md`](DISCLAIMER.md).

## Licensing

Original project code and documentation are licensed under Apache-2.0. Files
with another SPDX identifier retain that license. Dependency and tool evidence
is recorded under `compliance/`, with additional notices in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

This is an independent community project and is not affiliated with or endorsed
by OpenAI, Codex, GitHub, ReportLab, pypdf, pdfplumber, Noto, or the authors of
projects studied during design.
