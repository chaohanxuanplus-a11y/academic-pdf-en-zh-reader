<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Product contract

Input: one unencrypted, born-digital, English-majority academic PDF with usable text and supported page boxes. Normalize visible pages to A4 without enlarging, cropping or stretching content. Unsupported active content and unusable extraction cannot produce a validated final result.

Output: one A3 landscape reading PDF with complete source on the left and complete Chinese reading flow on the right. Title/abstract/keywords and their supplements span the right panel; remaining Chinese and its supplements use two columns. References remain in the source language. Include useful vocabulary, contextual figure/table interpretation and the complete final responsibility statement. Use 11 pt body, 10 pt notes and 140% leading; center the final card with its 72 pt logo and 12 pt warning in the safe remaining right panel. Follow [layout policy](layout-policy.md) for spacing and budget.

Process: private local PDF handling, Agent-assisted paragraph translation, focused semantic review, deterministic selection/layout/rendering and final mechanical validation. Intermediate paper data stay outside Git and release archives. A successful extraction checkpoint and unchanged draft batches may be reused during repair. Successful delivery removes managed temporary jobs by default; recoverable failures use bounded resume retention.

Rights, privacy and publication responsibility are defined in [DISCLAIMER](../DISCLAIMER.md) and [PRIVACY](../PRIVACY.md). The tool neither fetches papers nor publishes generated PDFs automatically. OCR and a multi-paper hosted service are outside this product.
