<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Continuous Chinese layout

The physical canvas is A3 landscape. All normalized source pages appear once in order in its left A4 panel. Chinese flows independently through the right panel; source page references remain metadata, not geometric anchors.

Title, abstract, keywords and their attached notes use the full panel width, including across pages. Each note inherits its parent unit's column count and measured width while retaining its auxiliary style. Body, headings, captions, footnotes and their notes use two columns, read left then right. A paragraph may span columns or pages without a label, forced truncation or repeated text. Each note directly follows its complete paragraph. No source-alignment spacer or comparison line is drawn.

Use 11 pt body, 10 pt notes, 140% leading, 32 pt margins, 16 pt column gutter, and 3.5 pt between paragraph groups. Title/heading/caption sizes remain 160%/120%/92% of body. Within a group, translation and notes have no extra block gap. Preserve role hierarchy, embedded fonts and legal line breaks. Reserve headings with actual following line heights. Never shrink type to satisfy the budget.

The final responsibility card uses a 72 pt logo, 12 pt warning text with 16.8 pt leading, and a centered 420 pt card. Measure all wrapped warning lines and padding before pagination. Center the entire card horizontally within the right panel and vertically within its complete remaining safe space. With no Chinese content on that page, use the whole safe right panel. Preserve all six warning text items and identity lines; they may wrap into more visual lines. Keep the warning larger than the body and render it exactly once.

Let B be the number of source pages containing body/front matter/figures/captions, excluding reference-only pages. Let C be the number of right panels required when translation, supplements and the responsibility statement are compactly paginated together, without padding for source-only tail pages. Aim for C <= B. First lay out full translation and essential notes with the final statement; compact figure detail if needed. Add optional notes by value only while the result fits. The statement always remains on the physical final page, even when that page contains source references.

Every paper retains key vocabulary; each readable figure/table retains its core explanation. Optional background, repeated vocabulary and secondary interpretation are expendable. Only the minimal core result may exceed B or add physical pages. Fill every available reading column before a necessary transition, allowing heading and statement reservations. Source-only tail pages remain because the complete original is preserved; never add filler to occupy their right panels. Larger type and the statement can require more Chinese pages; a short final column and safe space around the centered card are normal.

Separate output source page, Chinese target page and each block's semantic source pages in data. Layout artifacts use schema 2.0.0 with frame-graph, solver and typography policy 3, and branding policy 2. A changed style, font, source, translation or brand manifest invalidates derived measurements and downstream artifacts. Measure unchanged text once per resolver lifetime; trial selection reuses it.
