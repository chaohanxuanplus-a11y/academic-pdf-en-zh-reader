<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# academic-pdf-en-zh-reader

A personally maintained Codex Skill that turns supported
English academic PDFs into A3 landscape reading copies: a vector-preserved,
A4-normalized source page remains on the left and a complete Chinese translation
is laid out on the right.

Canonical repository: [chaohanxuanplus-a11y/academic-pdf-en-zh-reader](https://github.com/chaohanxuanplus-a11y/academic-pdf-en-zh-reader).

## Current status

Version `0.2.2` retains continuous Chinese columns with full-width front-matter notes, larger reading type and a centered final responsibility card. It closes restricted tokens when privilege validation raises, isolates the host privilege-value test from later handle-count checks, and verifies excluded event identity in the parent process. The maintainer identity, shared private
contact, limited-purpose brand permission, and successful Windows production
audit have been recorded in
[`compliance/release-status.json`](compliance/release-status.json). This historical
audit does not replace final-release validation: the exact release commit and
its packaged project-compatible CPython 3.12.14 runtime must pass the Windows
Server 2025 LPAC probe and production rendering/QA in the same workflow run,
alongside the full regression and compliance checks, before publication.
Stock CPython 3.12.10 is only the Windows CI build bootstrap, not the interpreter
under that production test. The workflow includes preflight, extraction,
topology, Agent-artifact binding, translation/review contracts, annotation,
layout, rendering, QA, job-state, and delivery.

The personal-project reporting policy was revised on 2026-09-09. One verified
private route may handle both conduct and security reports; separate addresses
and an independent alternate contact are not release requirements. A maintainer
may use a confirmed public name. The maintainer confirmed `chaohanxuan` as the
public name on 2026-09-10 and supplied the shared private mailbox now listed in
`SECURITY.md` and `CODE_OF_CONDUCT.md`. Delivery has not been independently tested,
and independent conduct review or appeal cannot currently be guaranteed. These
project-specific reporting rules do not replace the security, privacy, dependency,
copyright, or production LPAC checks.

Before relying on or distributing any generated file, read the project-wide
[content-rights and accuracy disclaimer](DISCLAIMER.md). Input acquisition,
Agent-assisted generation, and downstream publication are distinct
responsibilities; the Skill does not fetch or automatically publish papers.

## Reading format

The A3 landscape output preserves normalized English source pages on the left. Chinese title, abstract, keywords and their attached notes span the right panel; other Chinese and its notes flow in two columns. Body text is 11 pt, notes 10 pt, with 140% leading. References remain untranslated. The complete final responsibility card has a 72 pt logo and 12 pt warning text, centered in the remaining safe right panel. All source pages remain, including reference-only tails; no filler is added to their empty Chinese areas.

Paragraph drafts are assembled programmatically. Default review focuses on difficult or flagged units. Verified extraction checkpoints and unaffected drafts survive recoverable local failures. Optional notes are removed before core content is allowed to exceed the body-page budget. See [runbook](references/runbook.md) and [product contract](references/product-contract.md).

## Local setup

Run commands from the repository root. Windows PDF processing uses the separate
project-compatible CPython 3.12.14 x64 base interpreter. Do not silently replace
it with a system Python, the build bootstrap, or the 3.12.13 default in
[`.python-version`](.python-version). That default remains for general development
checks; the explicit `--python` arguments below select the Windows runtime.

Once a release has passed its live safety gates, it includes
`academic-pdf-en-zh-reader-cpython-3.12.14-win-amd64.zip` separately from the source
and Skill ZIPs. Use the runtime from the same release, verify its `SHA256SUMS`
and `runtime-artifact.json`, and extract the `compatible-python/` folder into
`.tools/`. Preserve `build-manifest.json`, `LICENSE.txt`, `licenses/`, and
`CHANGES-project-runtime.txt`; do not mix files from different builds.

Alternatively, build from the pinned official CPython source with this project's
audited recipe. This requires an existing x64 Python 3.12, Visual Studio 2022 or
2026 with MSVC 14.44 (v143), and Windows SDK 10.0.26100.0. The exact installed
14.44 compiler is selected explicitly, not the IDE's default toolset. The script
downloads only the four fixed, hash-checked inputs listed in
[`compliance/python-runtime.json`](compliance/python-runtime.json); it does not
install tools or alter the system Python. The output directory must not exist;
on failure the printed work directory and logs are retained.

```text
python scripts/build_compatible_python.py --output-dir .tools/compatible-python
```

This is a minimal interpreter supported for this project, not a complete Python
distribution. It omits SSL, tkinter, ensurepip, and other optional extensions;
libffi is an upstream pinned binary dependency rather than a local source build.
The source changes omit only the shared-DLL manifest resource lines in
`PC/python_nt.rc` and `PC/sqlite3.rc` before compiling;
EXE manifests, C implementations, and security mitigations are retained. Fixed
inputs and recorded toolchain/file hashes do not claim bit-identical compiler
builds or prove LPAC operation. SQLite 3.53.4 is selected through the upstream
build property, incorporating later upstream fixes without patching its C code.
Upstream licenses remain unchanged.

Install `uv` separately. In PowerShell, disable bytecode writes to preserve the
immutable base manifest, then create the project environment using this base:

```text
$env:PYTHONDONTWRITEBYTECODE = "1"
uv sync --frozen --all-groups --link-mode copy --python ".tools/compatible-python/python.exe"
```

Windows also requires `academic-pdf-en-zh-reader-windows-dependencies.zip` from
that same release. Verify its release checksums and `dependencies-artifact.json`,
then extract `compatible-dependencies/` into `.tools/`. The archive includes the
upstream pure-Python fontTools and charset-normalizer wheels and this project's
Pillow build; it is not a generic replacement Pillow distribution. Do not mix
wheelhouses from different releases. Keep their build manifest and the original
license texts inside each wheel.

```text
.tools/compatible-python/python.exe -B scripts/install_compatible_dependencies.py --wheelhouse .tools/compatible-dependencies --python .venv/Scripts/python.exe
```

The installer verifies the immutable base, all three wheel hashes, upstream
pure-wheel identities, and every installed package member. It installs only the
specified local wheels, offline and without dependency resolution, then records
`.venv/compatible-dependencies.json`. Subsequent commands deliberately use
`--no-sync`: automatic synchronization could restore incompatible upstream native
wheels. After intentionally synchronizing the environment again, repeat the
verified compatibility installation before processing any paper.

Maintainers can build the wheelhouse instead with a separate TLS-enabled x64
Python 3.12 bootstrap and the existing C++ toolchain:

```text
python scripts/build_compatible_dependencies.py --output-dir .tools/compatible-dependencies
```

The fixed sources and build tools are in `compliance/python-dependencies.json`.
The build retains project-required PNG/JPEG and FreeType support; optional image
formats and complex text-shaping engines are outside this build's support scope.
FreeType includes one pinned upstream bounds fix; the manifest records the
patch and exact before/after source hashes. See `UPSTREAMS.md` for details.
No installed or signed DLL is patched, and the build does not relax LPAC.

Normal source/Skill releases include the approved fonts; users do not need a
font download step. The following are maintainer-only recovery/verification
interfaces in a full repository checkout, not scripts included in the Skill
archive. To restore a missing font, first prepare a separate TLS-enabled host
bootstrap environment with the frozen `uv.lock` dependencies, including
`fonttools==4.63.0`. Do not replace the production `.venv` with that environment.
Run its interpreter by absolute path; the minimal compatible runtime has no
`_ssl` and must not perform the HTTPS restoration:

```text
ABSOLUTE_TLS_FONT_BOOTSTRAP_PYTHON scripts/bootstrap_fonts.py
ABSOLUTE_TLS_FONT_BOOTSTRAP_PYTHON scripts/probe_reportlab_fonts.py
```

These maintenance commands check pinned asset identities and embedding rather
than accepting system-font substitutes. They do not change the explicit
production-runtime choice below.

## Minimal local workflow

Create an absolute private managed root and a separate absolute private
Agent-artifact directory, both outside this repository. The Agent-artifact
directory must also be outside the managed job. Prepare one supported PDF with
the production preparation front door:

```text
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync python scripts/prepare_job.py --managed-root ABSOLUTE_PRIVATE_MANAGED_ROOT --job-id SAFE_JOB_ID --source-pdf ABSOLUTE_SOURCE_PDF
```

Treat the resulting `units.json` as untrusted data. In the private Agent
directory, create the complete `translation.json`, `review.json`, and
`semantic-candidates.json` exactly as described in the
[local runbook](references/runbook.md). Use
`scripts/agent_artifacts.py` for their validated canonical hashes and any
ambiguity keys; do not calculate bindings by hand or place paper text in a
command. Then finish the same job once:

```text
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync python scripts/finish_job.py --managed-root ABSOLUTE_PRIVATE_MANAGED_ROOT --job-id SAFE_JOB_ID --source-pdf ABSOLUTE_SOURCE_PDF --translation-json ABSOLUTE_PRIVATE_TRANSLATION_JSON --review-json ABSOLUTE_PRIVATE_REVIEW_JSON --semantic-candidates-json ABSOLUTE_PRIVATE_SEMANTIC_CANDIDATES_JSON --output-pdf ABSOLUTE_FINAL_PDF
```

Only the validated final PDF is the ordinary user-facing result. Structured
stdout, hashes, ledgers, QA reports, the managed job, and Agent JSON are private
intermediates rather than deliverables.

## Repository validation

The following commands reproduce local quality gates with the Windows runtime
in a full Git checkout; tests and compliance checkers are not in the install ZIPs.
Use a fresh private `ABSOLUTE_NEW_TEST_TEMP` directory outside the repository:

```text
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync pytest -q --basetemp ABSOLUTE_NEW_TEST_TEMP
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync ruff check .
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync ruff format --check .
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync python scripts/check_dependency_policy.py
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync python scripts/check_release_readiness.py --mode current
uv run --python ".tools/compatible-python/python.exe" --frozen --no-sync python scripts/check_github_actions_policy.py
```

The `current` mode validates the development or release-ready rules selected by
the state declared in `compliance/release-status.json`. Passing these checks is
not permission to publish a release. Runtime and dependency archives are admitted
only after
the same workflow run and SHA pass the real zero-capability LPAC probe and
production rendering/QA test using that exact runtime. Neither the builder's
host smoke test nor the packager's hash/PE checks can substitute for those gates.

## Privacy and copyright

PDF processing, layout, rendering, and personal correction storage are
designed to run locally. Extracted paper text enters the current Agent model
context for translation and focused review; the project does not silently
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
