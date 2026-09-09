<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Product contract

## Supported v1 input

The Skill accepts one unencrypted, born-digital, English-majority academic PDF
with a usable text layer and interpretable visible page boxes. Scans, OCR
overlays, active content, attachments, forms, batch service operation, and public
redistribution are outside v1. Unsupported or low-confidence input stops with a
stable review or error outcome; it is never silently converted to a simpler
layout. A non-A4 page size by itself is supported and must not stop the job.

Before extraction, every page is normalized from its displayed CropBox to a
fixed A4 portrait canvas. Width is compared with A4 width and height with A4
height after applying the declared page rotation. When both dimensions fit, the
visible page remains at 1:1 and blank space is centered on every deficient axis.
If either dimension exceeds A4, one uniform factor scales the page down only as
much as necessary to fit, followed by centered blank padding. Normalization
never enlarges, crops, or stretches source content. The managed normalized PDF
is immutable and drives extraction and rendering; its SHA-256 and the raw upload
SHA-256 are recorded separately.

## Required output behavior

- Output pages are A3 landscape. The normalized A4 source page remains
  vector-preserved on the left; the Chinese reading layer occupies the right.
- The right side mirrors every source band and its one-, two-, or three-column
  topology. Fixed column left edges do not drift between runs.
- Title, abstract, keywords, headings, body paragraphs, figure captions, and table
  captions are translated completely and exactly once.
- Author and affiliation data, bibliographic metadata, repeated margins, page
  numbers, notes, acknowledgements, equations, variables, code, chemical formulae,
  pure data, references, table cells, and figure-internal text remain explicit
  excluded roles and do not enter translation coverage.
- A paragraph split by an English column or page boundary remains one semantic
  unit. Chinese flows continuously and is split only at a legal target-layout
  line boundary when space requires it.
- Body font size is fixed for the document. Required translation is never deleted,
  shrunk, reordered, overlapped, or replaced with optional teaching content.
- Only single-column or spanning bands use grey dashed leaders. Multi-column bands
  use no leader and treat first-line vertical proximity as a soft objective.
- Every successfully delivered file must contain exactly one fixed responsibility
  statement at its end. If the final paper page has a safe eligible right-side
  reference region, place the project identity and disclaimer card there below
  any translated `References` heading. Otherwise append one dedicated final A3
  landscape statement page, explicitly identified as project-authored rather
  than a source-paper page, with no fabricated source-page association. The card includes
  the approved logo, `瀚海问材`, the Skill name, the configured repository display,
  and fixed disclaimer text. It is project-authored provenance, not paper
  translation or annotation: it uses no leader, does not enter translation
  coverage or red/orange budgets and never overlaps required content. Do not omit
  the statement when space is insufficient and do not move or shrink paper text
  to make room. The dedicated page includes a fixed explanation that it was added
  because the preceding final page had no safe space. On successful delivery,
  surface the fixed appended-page notice to the user. If the required statement
  cannot be rendered or validated, fail closed without delivering a final PDF.

## Failure precedence

Safety, exact source binding, complete translation, reading order, mirrored
topology, fixed typography, embedded approved fonts, zero overlap, and readable
Unicode are hard gates. If these cannot all be satisfied, the Skill stops before
publishing a final PDF. Optional orange teaching content is reduced before any
required Chinese text; adding a continuation page is preferable to violating a
hard gate.

## Privacy and rights

Processing is local except that extracted source units enter the current Agent
context for translation and independent review. No additional translation service
is called silently. Source papers, extracted full text, translations, personal
corrections, and unredacted logs must not enter the repository. The project license
does not grant rights to publish a source paper or its translation. Input
acquisition, authorization, Agent-generated output, and downstream use are
governed by the English controlling and Chinese reference versions of the
[project disclaimer](../DISCLAIMER.md).
