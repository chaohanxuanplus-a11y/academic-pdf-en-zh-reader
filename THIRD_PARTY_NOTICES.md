<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Third-Party Notices

No third-party code source, user PDF, or Python wheel is vendored in the tracked
repository or source/Skill ZIPs. A separately built Windows runtime is described
below; it is not admitted for release until its live safety gates pass.
One publisher-designed branding image is provided under a separate
limited-purpose permission. The following three font binaries are redistributed
so that rendering never depends on a user's system fonts.

## Hanhai Wencai limited-purpose brand asset

File: `assets/branding/hanhai-wencai.png`

The publisher declares this image to be self-designed. Its SHA-256 is
`1bb1dad5b83bd3b98060e513f3298bb0ace2bf4669590f60c6a3fbbdfb4ba0ab`
and its size is 1,471,096 bytes.

Copyright 2026 chaohanxuan. The rights record is based on the publisher's direct
self-design and limited-use declaration dated 2026-09-10, not an independent
copyright or trademark-registration search.

License: `LicenseRef-HanhaiWencai-SkillOutputOnly`. The image may accompany this
project's public repository or Skill distribution only so that the Skill can
place the brand mark at the end of a processed output file. The implementation
limits this to one safe placement on the final output page; if the paper's final
page has no safe space, the publisher's 2026-09-10 follow-up authorizes appending
a final responsibility-statement page and notifying the user, without displacing
paper content. The brand remains confined to that final statement placement.
This permission does not authorize independent reuse, promotion, use in other
projects, or placement elsewhere in the output.

Permission to display the accompanying brand name and logo is limited to that
same purpose. No trademark registration or broader trademark permission is
claimed. The asset permission does not change the Apache-2.0 license for project
code and documentation, or grant rights in user papers and other third-party
content.

See `LICENSES/LicenseRef-HanhaiWencai-SkillOutputOnly.txt`,
`compliance/evidence/public/brand-use-declaration-20260910.md`, and the separate
`asset.copyright` and `asset.trademark` records in `compliance/project-identity.json`.
Resolution of this rights record does not clear the production safety gate or
authorize a release operation.

## Contributor Covenant 3.0

File: `CODE_OF_CONDUCT.md`

This project adapts Contributor Covenant version 3.0, stewarded by the
Organization for Ethical Source. The canonical source is
<https://www.contributor-covenant.org/version/3/0/>.

License: Creative Commons Attribution-ShareAlike 4.0 International
(`CC-BY-SA-4.0`). See `LICENSES/CC-BY-SA-4.0.txt`.

Project modifications clarify the unreleased reporting status, moderator
recusal, and repository formatting while preserving the substance of version
3.0. The upstream attribution and modification notice are also retained in
`CODE_OF_CONDUCT.md`.

## Noto Serif SC 2.003

Files:

- `assets/fonts/NotoSerifSC-Regular.ttf`
- `assets/fonts/NotoSerifSC-SemiBold.ttf`

Copyright 2017-2024 Adobe (http://www.adobe.com/).

These files are deterministic static 400 and 600 instances derived with
`fonttools==4.63.0` from the official regional TrueType variable font
`Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf` at tag `Serif2.003`, commit
`9b0f1436e455d902de067a2501422e5dc71ad16b`. The source SHA-256 is
`5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09`.
The transformation pins `wght` to 400 or 600, updates the name table, makes a
static instance, and disables timestamp recalculation. No other modification is
made. Output hashes and the complete recipe are recorded in
`assets/font-manifest.json`.

License: SIL Open Font License 1.1 with Reserved Font Name `Source`
(`OFL-1.1-RFN`). The generated names do not use that Reserved Font Name. See
`LICENSES/OFL-1.1-RFN.txt`.

## Noto Sans Symbols 2 2.008

File: `assets/fonts/NotoSansSymbols2-Regular.ttf`

Copyright 2022 The Noto Project Authors
(https://github.com/notofonts/symbols).

The unmodified font is the exact
`NotoSansSymbols2/unhinted/ttf/NotoSansSymbols2-Regular.ttf` member of the
official `NotoSansSymbols2-v2.008.zip` release asset. Its SHA-256 is
`c4a0a80f0041ce4be81e2478faad22776d23edb98ae3f0d19bd37044820ecf9d`.

License: SIL Open Font License 1.1 (`OFL-1.1`). See
`LICENSES/OFL-1.1.txt`.

The locked development environment retrieves Python distributions from PyPI.
Exact versions, source URLs, license evidence, and artifact hashes are recorded
in `compliance/dependencies.json` and `uv.lock`.

## Separate project-compatible CPython 3.12.14 runtime

The runtime archive is intended and supported for this project's Windows x64
workflow; this support scope does not change upstream license permissions. It
is a minimal project build, not an official PSF binary, a full standard-library
distribution, or an entirely source-built dependency stack. It omits SSL,
tkinter, ensurepip, and other optional extensions. No upstream endorsement or
bit-identical compiler-build guarantee is claimed.

Exact input URLs and hashes are in `compliance/python-runtime.json`. The runtime
preserves the following actual notices, rather than replacing them with this
project's Apache-2.0 license:

- CPython: PSF-2.0 and historical/component notices, retained verbatim in
  `LICENSE.txt` and `licenses/CPython-LICENSE.txt`. The only intentional source
  changes omit the shared-DLL manifest resource lines in `PC/python_nt.rc`
  and `PC/sqlite3.rc` before building. C implementations and EXE manifests remain unchanged;
  no signed upstream DLL is edited. The summary and per-build source hashes
  are retained in `CHANGES-project-runtime.txt` and `build-manifest.json`.
- zlib 1.3.1: Zlib license; the exact source notice is retained in
  `licenses/zlib-LICENSE.txt`.
- libffi 3.4.4: MIT license; the unmodified, pinned Python upstream x64 binary
  and its notice are retained as `DLLs/libffi-8.dll` and
  `licenses/libffi-LICENSE.txt`.
- SQLite 3.53.4: upstream public-domain dedication (SPDX `blessing`); the exact
  source header is retained in `licenses/sqlite3-LICENSE.txt`. The library is
  compiled without C patches from Python's fixed official dependency commit
  and selected through `sqlite3Dir`; this includes later upstream fixes than
  CPython's default SQLite 3.49.1. See the specific security-review sources in
  `compliance/python-runtime.json`, not a blanket vulnerability-free claim.
- expat: MIT license; the notice accompanying the copy embedded in `pyexpat`
  is retained from the pinned CPython source as `licenses/expat-COPYING.txt`.
- HACL: actual copyright and license headers from the embedded CPython source
  are preserved verbatim in `licenses/HACL-source-license-headers.txt`.
- Microsoft `vcruntime140.dll` and `vcruntime140_1.dll`: copied by upstream
  PCbuild from the installed v143 redistributable. The upstream Windows binary
  conditions are retained in `LICENSE.txt` and
  `licenses/Microsoft-runtime-notice.txt`; those conditions continue to apply
  to Microsoft Distributable Code. Visual Studio Build Tools and the Windows
  SDK are not redistributed.

`licenses/CPython-upstream-license-reference.rst` preserves Python's wider
license reference, including descriptions of optional modules not present in
this minimal build; its presence is not a claim that OpenSSL or those modules
are bundled. Keep all notices and `build-manifest.json` with the runtime.
The separate `runtime-artifact.json` and release checksum list bind the archived
files, while real LPAC and production tests in the same workflow run and SHA
determine release eligibility. Build/package checks alone do not clear that gate.

High-attention binary boundaries are:

- `pypdfium2`: Python bindings plus a PDFium binary and its build-license
  bundle. Only standard non-V8/XFA wheels may be used; selected platform hashes
  and `BUILD_LICENSES` must enter release evidence.
- `Pillow`: the separate Windows compatibility archive supplies a minimal
  project-built wheel with the exact linked-library notices described below;
  it does not redistribute an unreviewed upstream codec bundle.
- `cryptography`, `cffi`, and `rpds-py`: native wheels must appear in the
  platform SBOM and are not vendored by this project.

## Separate Windows compatibility wheels

`compliance/python-dependencies.json` pins all source/build inputs and the
project-specific configuration. The separate dependency archive contains
fontTools 4.63.0 and charset-normalizer 3.5.1 as unmodified official pure-Python
wheels, retaining their original license files (including fontTools'
`LICENSE.external`). Their exact hashes are also in the upstream `uv.lock`.
The Pillow 12.3.0 wheel is built from the pinned source, not presented as an
official upstream binary. Its own manifest records its actual bytes; original
PyPI wheel hashes do not describe this build.

The Pillow wheel retains its MIT-CMU license, the pythoncapi-compat 0BSD notice,
the original Tcl/Tk header notice from `src/Tk/_tkmini.h`, and the original
notices for its statically built native components:

- libjpeg-turbo 3.1.4.1: original `LICENSE.md` and `README.ijg` (IJG and the
  accompanying BSD-3-Clause text). This software is based in part on the work
  of the Independent JPEG Group.
- zlib-ng 2.3.3: the original Zlib notice.
- FreeType 2.14.3: the FreeType License (`FTL`) is selected, not the alternative
  GPL license. This software is based in part on the work of the FreeType Team
  ([FreeType Project](https://freetype.org)). The original FTL and applicable
  bundled-code notices are preserved, including its embedded zlib notice.

In addition to build configuration and notice metadata, FreeType's
`src/truetype/ttgxvar.c` includes only the upstream `TT_Get_Var_Design` bounds
fix from commit `5a280ecde6f324de0d226261036e736e0cb49a71`
([official change](https://github.com/freetype/freetype/commit/5a280ecde6f324de0d226261036e736e0cb49a71)).
The pinned patch bytes and original/modified file hashes are recorded in the
source inventory and build manifest. No other C implementation is changed. The build
omits DLL manifest generation before linking while retaining x64 ASLR, DEP,
high-entropy ASLR and CFG; no installed or signed DLL is edited. PNG, JPEG,
FreeType and the project's image primitives are supported. Optional codecs,
Raqm shaping, color-font PNG, and Brotli fonts are not supplied; libjpeg SIMD is
disabled. This is a project-specific support scope, not a restriction on rights
granted by the original licenses and not a complete Pillow replacement.

The pinned setuptools, pybind11 and CMake wheels are build tools only and are
not distributed. Visual Studio and the SDK are also not distributed. A release
must preserve the wheel licenses, build manifest, verified installation receipt,
dependency SBOM, and release checksums. Local build success does not replace the
same-run, same-SHA production safety gate, and no upstream endorsement or
bit-identical compiler-build guarantee is claimed.

`uv` and the REUSE CLI are isolated external development tools and are not
runtime dependencies or release assets. The REUSE CLI is GPL-3.0-or-later with
separately licensed bundled materials; executing it does not relicense this
project, but the tool itself must not be silently redistributed.

The machine-readable source URLs, tags, commits, Git blob identifiers, hashes,
name-table facts, transformation record, and Gate G0 result are in
`assets/font-manifest.json`.
