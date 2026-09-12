<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Focused validation

The default semantic review is `targeted`. The translating Agent may review its own difficult units; `reviewed_unit_ids` lists exactly the units checked, in source order. Do not claim whole-paper or independent review unless performed. A separately requested independent review retains distinct reviewer identity and complete coverage.

Programs check source/translation coverage and bindings, numbers/units/citations, annotation placement and core coverage, geometry/overlap, embedded glyphs, original-page preservation, active-content absence and final render readability. Run the final mechanical chain once; a failed check reports a local repair target. Keep final source identity and candidate-byte binding intact.

Inspect first/last/flagged pages, plus a useful figure sample. Inspect figure contents for interpretation independently of final layout sampling. Do not repeat full-paper translation or full visual inspection for a local text correction; rerender affected output and complete the necessary final byte checks.

On the first page and any front-matter continuation, verify notes share the title/abstract/keywords width and body begins only after their groups. Check 11 pt body and 10 pt notes with 140% leading, actual heading-following line space, and avoidable early column/page breaks. On the final page verify the complete card is centered in its safe remaining right panel, its logo is 72 pt and its 12 pt warning is larger than body text. Source-only tails and space around the centered card can remain empty.

Known local failures are repair work. Genuine missing input or a persistent unresolved safety/content defect blocks final delivery. Runtime/release audits belong to environment setup or publishing, not every paper. Report only actionable failures and actual checks; internal success logs are not the user's deliverable.
