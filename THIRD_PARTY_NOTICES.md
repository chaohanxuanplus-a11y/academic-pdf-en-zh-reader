<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Third-Party Notices

No third-party code, PDF, or Python wheel is currently redistributed in this
repository. One user-supplied candidate branding image is retained only for local
development while its public-release rights remain unverified. The following
three font binaries are redistributed so that rendering never depends on a
user's system fonts.

## Hanhai Wencai candidate brand asset

File: `assets/branding/hanhai-wencai.png`

The user supplied this image for local candidate development. Its SHA-256 is
`1bb1dad5b83bd3b98060e513f3298bb0ace2bf4669590f60c6a3fbbdfb4ba0ab`
and its size is 1,471,096 bytes.

Copyright and redistribution status: the SPDX copyright field records
`NOASSERTION` because ownership has not been independently verified. Public
redistribution authority has not been established, so this repository does not
authorize public redistribution of the asset. The temporary identifier
`LicenseRef-HanhaiWencai-Unreleased` records that unresolved status; it does not
grant public redistribution rights.

Trademark status: ownership, registration status, and authorization to use the
name or image publicly as a trademark have not been independently verified. No
public trademark-use permission is claimed.

See `LICENSES/LicenseRef-HanhaiWencai-Unreleased.txt` and
the separate `asset.copyright` and `asset.trademark` evidence records in
`compliance/project-identity.json`. The brand asset is not admitted to a public
release until both evidence records are verified, public redistribution and
public trademark-use permissions are established, the dedicated release blocker
is resolved, and these notices are updated.

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

High-attention binary boundaries are:

- `pypdfium2`: Python bindings plus a PDFium binary and its build-license
  bundle. Only standard non-V8/XFA wheels may be used; selected platform hashes
  and `BUILD_LICENSES` must enter release evidence.
- `Pillow`: official wheels contain native image-codec components. v1 does not
  redistribute wheels; any future offline bundle requires a platform-specific
  linked-library and notice audit.
- `cryptography`, `cffi`, and `rpds-py`: native wheels must appear in the
  platform SBOM and are not vendored by this project.

`uv` and the REUSE CLI are isolated external development tools and are not
runtime dependencies or release assets. The REUSE CLI is GPL-3.0-or-later with
separately licensed bundled materials; executing it does not relicense this
project, but the tool itself must not be silently redistributed.

The machine-readable source URLs, tags, commits, Git blob identifiers, hashes,
name-table facts, transformation record, and Gate G0 result are in
`assets/font-manifest.json`.
