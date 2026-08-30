<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Privacy

This local candidate has passed local acceptance, and public release remains
blocked. No PDF processing service or telemetry is activated by this repository.
Importing the package does not create a correction database. The local correction
store is created only by an explicit authorized correction or store command.

This is not a claim that translation is fully offline: extracted content needed
for translation and independent review enters the current Agent context. The
Skill does not fetch source papers or automatically upload or publish generated
PDFs. User and content-rights responsibilities are stated separately in the
[project disclaimer](DISCLAIMER.md).

The candidate privacy contract requires it to:

- process PDF parsing, layout, rendering, QA, and personal correction storage
  locally;
- send only bounded extracted content to the current Agent context for
  translation and independent review;
- not silently use another translation API;
- keep the managed root and Agent JSON private and outside the repository, and
  keep Agent JSON outside the managed job;
- keep ordinary logs free of paper text, full filenames, author identities,
  secrets, and personal corrections;
- delete temporary paper text and figure crops after success, failure, or
  cancellation unless the user explicitly selects a bounded resume/debug mode;
- store a correction only when the user explicitly corrects a bright-red
  ambiguity and the calling boundary also passes `authorized=True`, using the
  user's private data directory outside the Skill, project, job directory, and
  Git;
- never store ordinary feedback or a model-generated candidate as a personal
  correction.
- present only the final PDF to the normal user, never internal stdout, reports,
  ledgers, hashes, or private Agent artifacts.

Issues, pull requests, tests, screenshots, and release assets must never include
user papers, non-public research material, correction databases, or unredacted
logs.

These commitments describe the approved candidate contract, not a release or
deployment claim. They are enforced by local acceptance tests; the separate
public-release identity and private-contact gates remain blocked.
