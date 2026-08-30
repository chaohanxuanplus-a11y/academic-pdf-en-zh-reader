# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Create one managed EXTRACTED job from an untrusted academic PDF."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_pdf_en_zh_reader.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["prepare-job", *sys.argv[1:]]))
