<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Artifact and schema guide

All persisted artifacts are UTF-8 canonical JSON, schema version `1.0.0`, with no
unknown fields. Arrays whose order is semantic remain in deterministic order.
Hashes are lowercase SHA-256 over exact bytes or the repository canonical-JSON
profile, as specified by the producing stage.

## Artifact chain

1. `preflight.json` binds the raw upload hash, visible page geometry, text counts,
   and hard resource limits.
2. `normalization.json` binds the raw hash and preflight hash to the immutable
   `normalized-source.pdf` hash, per-page uniform scale, centered padding, and
   normalized content box. The normalized PDF is the A4 source used downstream.
3. `extraction.json` binds raw visible characters, vectors, images, rebuilt lines,
   graphic regions, captions, and references to the normalized source chain.
4. `source.json` stores high-confidence bands, columns, roles, stable reading order,
   source character ranges, source font-size evidence, and graphic targets.
5. `units.json` maps every required source block exactly once and every excluded
   block zero times. A unit may contain several complete source fragments but never
   a target-layout break constraint.
6. `translation.json` binds the exact units hash and translator identity, and maps
   every unit ID exactly once to Chinese text, dual-axis alignment spans, and
   source/target-slice-bound terminology records.
7. `review.json` binds the translation hash and records independent structured
   review issues and the final gate status.
8. `semantic-candidates.json` binds the exact canonical parents in `units_hash`,
   `translation_hash`, and `review_hash`, then supplies bounded red emphasis,
   reviewed ambiguity occurrences, teaching notes, and figure/table notes.
9. `annotations.json`, `frame-graph.json`, `layout.json`, `render-manifest.json`,
   `qa.json`, and `provenance.json` freeze selected reading aids, layout decisions,
   rendering inputs, hard-gate evidence, and reproducibility identity.

## Offset convention

Text offsets are zero-based, half-open indexes over Python Unicode code points after
the repository's NFC normalization. Source blocks, unit fragments, translation
alignment spans, and terminology ranges are positive (`start < end`); every later
artifact states its own range rule. Geometry is displayed-orientation,
CropBox-relative, bottom-left-origin integer milli-points.

## Cross-artifact validation

JSON Schema checks local shape and bounded scalar types. Parent-side semantic
validators recompute stable IDs, geometry containment, reading order, source text,
fragment coverage, translation coverage, span bounds, hashes, review gates, and
annotation ratios. No child-process claim or Agent-returned derived field is trusted
when it can be recomputed from an earlier bound artifact.
