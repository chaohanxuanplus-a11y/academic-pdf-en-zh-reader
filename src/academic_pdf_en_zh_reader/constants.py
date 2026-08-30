# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Shared, immutable project constants."""

from __future__ import annotations

from reportlab.lib.pagesizes import A4

__version__ = "0.1.0.dev0"

A4_WIDTH_PT = float(A4[0])
A4_HEIGHT_PT = float(A4[1])
FIXTURE_SCHEMA_VERSION = 1
FIXED_PDF_DATE = "D:20000101000000+00'00'"
