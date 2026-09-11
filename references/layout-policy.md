<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Continuous Chinese layout

The physical canvas is A3 landscape. All normalized source pages appear once in order in its left A4 panel. Chinese flows independently through the right panel; source page references remain metadata, not geometric anchors.

Only title, abstract and keywords use the full panel width. Body, headings, captions, footnotes and supplementary notes use two columns, read left then right. A paragraph may span columns or pages without a label, forced truncation or repeated text. Each note directly follows its paragraph. No source-alignment spacer or comparison line is drawn.

Use 10 pt body, 9 pt notes, 130% leading, 32 pt margins, 16 pt column gutter, and 3.5 pt between paragraph groups. Within a group, translation and notes have no extra block gap. Preserve role hierarchy, embedded fonts and legal line breaks. Never shrink type to satisfy the budget.

Let B be the number of source pages containing body/front matter/figures/captions, excluding reference-only pages. Let C be the number of right panels required when translation, supplements and the responsibility statement are compactly paginated together, without padding for source-only tail pages. Aim for C <= B. First lay out full translation and essential notes with the final statement; compact figure detail if needed. Add optional notes by value only while the result fits. The statement always remains on the physical final page, even when that page contains source references.

Every paper retains key vocabulary; each readable figure/table retains its core explanation. Optional background, repeated vocabulary and secondary interpretation are expendable. Only the minimal core result may exceed B or add physical pages. Source-only tail pages remain because the complete original is preserved; never add filler to occupy their right panels.

Separate output source page, Chinese target page and each block's semantic source pages in data. Layout artifacts use schema 2.0.0. A changed style, font, source or translation invalidates derived measurements. Measure unchanged text once per resolver lifetime; trial selection reuses it.
