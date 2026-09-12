<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Compact Agent artifacts

Program-owned parent hashes and offsets are generated with `scripts/agent_artifacts.py`. Paths are absolute and private, outside the repository; output revision directories must be new.

`packets --units UNITS --output PACKETS --max-chars 12000` groups complete logical paragraphs and provides short context. The `units_hash` binds every draft to the same source. Read only the active packet and the shared term list; fetch relevant caption/body context for figure analysis as needed.

Each draft is `{"units_hash":"from packets","units":[...]}`. A row needs `unit_id` and `chinese_text`. Optional `terms` entries have `english`, `target` (an exact translated quote), `explanation`, `essential` and `priority`. Repeated quotes can specify zero-based `source_occurrence` or `target_occurrence`. Terms are deduplicated at their first source occurrence.

A caption row's `figure_notes` entries have `figure_id`, `content` (complete explanation), `core` (short essential explanation), `essential`, `priority`, and `evidence:[{"unit_id":"...","quote":"exact source excerpt"}]`. An optional evidence `occurrence` disambiguates repeated quotes. Include caption and relevant results/discussion excerpts. The helper calculates source spans; it does not invent interpretations.

A review record contains `reviewer_role:"targeted"`, `reviewer_id`, `reviewed_unit_ids`, `issues` and `final_status`. Record only actual review. Existing review schema supports ambiguity issues and mechanical resolutions `{issue_hash,unit_id,reason}`. The helper preserves this supplied record and binds it to the assembled translation.

`assemble --units UNITS --draft DRAFT1 --draft DRAFT2 --review-record REVIEW --translator-id AGENT --output-dir NEW_REVISION` writes translation, review, semantic-candidates and diagnostics JSON. It rejects missing/duplicate IDs and mismatched sources. Mechanical diagnostics include program-generated issue hashes. Update only affected drafts/review and assemble into a new revision directory; reuse the other drafts. Remove superseded private revisions when no attempt needs them. No duplicate explanatory old/new content belongs in active instructions.

Use the existing full JSON schema only for advanced red emphasis or ambiguity annotations. Frame-graph/layout artifacts use schema 2.0.0 with frame-graph, solver and typography policy 3 and branding policy 2; Agents never write geometry. After a layout/style upgrade, reuse verified extraction and translation inputs in a legal new attempt or revision, then regenerate annotation selection, geometry and finalization. Never edit frozen versions or hashes in place.
