<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Test Data Attribution

Only the following fixture specifications and the PDF content generated
directly from them are project-authored synthetic works released under
CC0-1.0. No other test file, fixture, generated output, or repository content is
covered by CC0-1.0 merely because it appears under `tests/` or was generated
during a test run:

- `single-column`
- `first-page-mixed`
- `two-column`
- `three-column`
- `cross-column-paragraph`
- `cross-page-paragraph`
- `figures-and-tables`
- `long-translation`
- `active-content`
- `prompt-injection`

Creator: academic-pdf-en-zh-reader contributors. Source: for every listed fixture
except `prompt-injection`, the matching JSON file under
`tests/fixtures-synthetic/specs/`; for `prompt-injection`,
`tests/integration/fixtures/prompt-injection.json`. Each is rendered by
`scripts/generate_synthetic_fixtures.py`. Modification: deterministic
programmatic layout and PDF generation. Generated location: the caller's output
directory (the CLI default is ignored `tmp/synthetic-fixtures/`); generated PDFs
are not copied from or modeled on a real paper.

The PDFs embed only the approved Noto font assets recorded separately in
`assets/font-manifest.json`; those font programs retain their stated OFL
licenses and are not relicensed as CC0. User papers, publisher PDFs, journal
screenshots, complete real translations, and unverified web assets remain
prohibited from tests, examples, screenshots, and release archives.
