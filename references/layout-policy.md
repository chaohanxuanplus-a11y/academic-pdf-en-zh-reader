<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Annotation layout policy

This policy is the contract between annotation selection and the layout solver.
Selection never changes the complete Chinese translation, its fixed role sizes, or
its reading order.

## Mandatory content and dark red

Lay out the complete translation, caption translations, selected dark-red spans,
and ambiguity labels before considering optional orange content. Dark red changes
glyph colour only. It is forbidden in titles, abstracts, and keywords. Its visible
character denominator excludes those roles; optional orange strings and the
inserted ambiguity labels are external to that denominator, while the ambiguous
Chinese translation span remains part of the body. Whitespace and Unicode
punctuation do not count. Six percent is a target, not a quota. Exact integer
arithmetic enforces the ten-percent hard cap. A span may simultaneously use dark
red glyphs and a bright-red ambiguity underline; downstream rendering preserves
both styles.

## Orange trial protocol

The selector materializes and validates the complete candidate set before the
first layout call. A `LayoutTrialRequest` is immutable and contains one of three
phases:

1. `mandatory-only` has no optional placements and establishes feasibility.
2. `candidate-trial` adds exactly one already-frozen candidate or one of its
   already-frozen compact variants to the currently accepted set.
3. `final-frozen` repeats the selected set and binds its selection hash.

Every request also carries the same frozen dark-red and ambiguity items plus
their canonical `mandatory_items_hash`. The final artifact serializes those exact
items from the selection result; callers cannot substitute a different mandatory
set after layout trials have completed.

`LayoutTrialResult.added_continuation_pages` is the total continuation count beyond
native source pages for that request. The mandatory-only result establishes the
baseline; an optional candidate adds a page only when its result exceeds the
currently accepted count. The callback must be pure for identical requests. It may
run any bounded solver, but it must not generate, rewrite, translate, or re-rank
content.

Figure/table notes are tried before ordinary teaching notes. A figure/table keeps
at most three notes. Full text is tried before its predeclared compact form. Only
an evidence-backed note marked essential may enable a continuation, and only after
both native trials fail. Ordinary teaching notes always use
`attachment = below-translation`, the document-wide auxiliary size, and exact text
`English original — 中文意思`. It may reuse continuation pages already required by
accepted figure/table notes, but it is rejected if the count rises. A repeated
item may then be tried at its next exact occurrence. Single-occurrence high-value
items sort before repeatable items.

Once `final-frozen` succeeds, downstream layout consumes the same item bytes. It
must not regenerate candidates from the resulting pages, so there is no
content-layout feedback loop.

`annotations.json` binds the candidate-set hash. Because the candidate set is not
reconstructible from `units.json` and `translation.json` alone, the production
pipeline must pass the expected hash from immutable job state to the parent-side
annotation validator.

## Figure and table evidence

Every note has at least one of two evidence forms:

- `semantic-unit`: an exact half-open slice and quote rebound to `units.json`;
- `frozen-object`: a source-artifact hash, graphic/table object ID, precise
  locator, evidence text, and text hash.

Frozen-object evidence is rejected unless an upstream verifier confirms that the
record exists in the already validated source/graphic/table artifact. The text and
its own hash are not sufficient proof. Unreadable or low-confidence objects yield
no note rather than a guessed finding.

## Ambiguity and style tokens

Each final unresolved Chinese span receives a bright-red underline, including in
an abstract. In reading order, only the first occurrence of each stable
`ambiguity_key` adds `（可能存在歧义）` immediately after the span at the containing
translation role's exact size. The label participates in mandatory measurement.

Annotation colours are versioned constants: body `#111111`, dark red `#7F1D1D`,
dark orange `#A84F08`, and bright red `#D00000`. A paper cannot override them.

## Downstream pre-layout adaptation

Task 15 selects content but does not place glyphs. Before the final FrameGraph and
vertical solver run, an adapter must consume the frozen records as follows:

- turn every orange placement into an auxiliary flow attached after its exact
  `unit_id` translation, using `attachment`, `content`, and
  `auxiliary_size_mpt`; its measured height participates in band/frame solving;
- insert each non-null ambiguity label immediately after `target_end` into a
  composite line-breaking stream at `label_size_mpt`, while retaining a mapping
  back to the untouched translation offsets; and
- carry dark-red colour and bright-red underline ranges as geometry-neutral style
  spans over that mapped stream.

Orange content and ambiguity labels must therefore be adapted before final layout,
not placed opportunistically by the renderer. Any height or line-wrap change reruns
FrameGraph/Task 14 with the already frozen annotation set; it never regenerates or
re-ranks candidates.
