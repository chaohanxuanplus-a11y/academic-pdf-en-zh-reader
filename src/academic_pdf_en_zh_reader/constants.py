# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Shared, immutable project constants."""

from __future__ import annotations

from reportlab.lib.pagesizes import A4

from academic_pdf_en_zh_reader.version import __version__ as __version__

A4_WIDTH_PT = float(A4[0])
A4_HEIGHT_PT = float(A4[1])
FIXTURE_SCHEMA_VERSION = 1
FIXED_PDF_DATE = "D:20000101000000+00'00'"
