<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Upstream Sources

No third-party code source file is vendored in the tracked source tree. Three
OFL font binaries are redistributed under the provenance and
modification record in `assets/font-manifest.json` and
`THIRD_PARTY_NOTICES.md`.

## Project-compatible Windows Python

`scripts/build_compatible_python.py` constructs a separate minimal CPython
3.12.14 x64 runtime for this project. Its exact official source/dependency URLs,
archive roots, SHA-256 values, versions, and build targets are maintained in
`compliance/python-runtime.json`. Downloaded source and compiled outputs are not
vendored in the source or Skill ZIPs; an admitted release supplies the tested
runtime as a separate archive with its own manifest and license texts.

- CPython source: official `Python-3.12.14.tgz` from python.org, PSF-2.0 and
  retained component notices. Only `PC/python_nt.rc` and `PC/sqlite3.rc` are
  intentionally changed: their shared-DLL `RT_MANIFEST` resource lines are
  omitted before compilation.
  The C implementations and EXE manifests are unchanged. No official signed
  DLL is modified. Before/after source hashes are recorded per build.
- zlib 1.3.1 source: Python's `cpython-source-deps` repository at commit
  `4dc98e1909830e2bdc2a9cc2236e3c5d5037335b`, Zlib license, unchanged.
- libffi 3.4.4: Python's `cpython-bin-deps` repository at commit
  `94cb9a1c7feb608adf2b9f8fe2dbd6925ffbf90d`, MIT license. The selected x64
  library is an unmodified upstream binary dependency, not locally rebuilt.
- SQLite 3.53.4: Python's `cpython-source-deps` repository at tag
  `sqlite-3.53.4.0`, commit `f048e6a40879da9828ca2cf3b260f3e0a26117fb`. Its
  unmodified source is public domain, with the original blessing notice retained.
  It replaces CPython's older default through the `sqlite3Dir` build property;
  the input archive hash and official `sqlite3.c` SHA3-256 are checked. The
  security-review record cites SQLite's actual fixes rather than claiming the
  absence of all vulnerabilities.
- Microsoft VC runtime DLLs: upstream PCbuild copies the installed v143
  redistributable files. Per-build hashes identify the selected files; upstream
  `PC/crtlicense.txt` conditions are retained. Neither the SDK nor Build Tools
  are bundled.
- Embedded expat and HACL implementations retain their actual upstream notices
  from the pinned CPython source; see the runtime's `licenses/` directory and
  [third-party notices](THIRD_PARTY_NOTICES.md).

`build-manifest.json` records the recipe/input hashes, actual toolchain,
source change, and all runtime file hashes. Fixed inputs and stable ZIP metadata
do not establish bit-identical compiler builds. The manifest's host checks are
not a substitute for same-run, same-SHA LPAC and production-finish release gates.

## Documentation upstream

### Contributor Covenant 3.0

- Local file: `CODE_OF_CONDUCT.md`
- Upstream: Contributor Covenant, version 3.0
- Steward: Organization for Ethical Source
- Canonical source: <https://www.contributor-covenant.org/version/3/0/>
- License: Creative Commons Attribution-ShareAlike 4.0 International
  (`CC-BY-SA-4.0`); local license text:
  `LICENSES/CC-BY-SA-4.0.txt`
- Modifications: clarified the unreleased reporting status, moderator recusal,
  and repository formatting while preserving the substantive attribution in
  `CODE_OF_CONDUCT.md`

The design studied public PDF translation and layout projects, including Ezra,
AIKONG, BabelDOC, and pdf2zh, only to understand mechanisms and trade-offs. The
implementation must remain independent unless a later task records an exact
file-level reuse here with upstream URL, commit, original path, copyright,
license, and modification notes.

Large-scale reuse must preserve Git history through an appropriate fork. Small,
targeted reuse of an MIT/BSD file must retain its original notice and must not be
relicensed as Apache-2.0.
