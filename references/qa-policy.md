<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Independent review and QA policy

## Independence gate

The whole-document translation records a non-empty translator identity. Review
records a non-empty reviewer identity. The review's translator identity must
match the identity bound into the translation; that identity and the reviewer
identity must differ, and the reviewer must not have participated in producing
the translation. If the environment cannot provide an independent Agent or
cannot review every unit in the current context, processing stops; a second pass
by the same role or stitched batches cannot be reported as independent review.

## Review scope

The reviewer compares every English unit with its candidate Chinese text and
checks the five translation priorities: fact, degree, logic, data, and terminology.
Mechanical checks for unit coverage, numbers, units, ranges, signs, citations, and
logic markers supplement this comparison but do not prove semantic correctness.

Issues have three semantic classes:

- `hard_error`: a factual, degree, logic, data, or terminology error that must be
  corrected and independently rechecked before layout;
- `style_improvement`: meaning-preserving Chinese improvement, recorded without
  disguising it as a semantic failure; and
- `unresolved_ambiguity`: available context cannot safely select one material
  meaning.

After a correction, the reviewer rechecks the changed unit, adjacent units, and
units sharing affected terminology. A passed review cannot contain an unresolved
`hard_error`.

## Ambiguity contract

Each unresolved material ambiguity has a stable `ambiguity_key` derived from five
parts: the English expression, its syntactic structure, candidate meanings, domain
context, and the specific reason the context is insufficient. Broad labels such as
"term ambiguity" are invalid. The key is stable across repeats of the same case.

Final unresolved spans receive a bright-red underline. At the first occurrence of
each stable key, the same-size bright-red text `（可能存在歧义）` follows the span. The label is
not a quality report and does not replace the translated text.

## Release gate

Layout may start only after exact unit coverage, bounded spans, mechanical semantic
checks, translation-hash binding, independent identities, and review status all
pass. Failed or incomplete review produces no candidate final PDF.
