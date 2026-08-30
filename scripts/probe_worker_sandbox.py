# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Emit machine-readable evidence for the fail-closed worker sandbox gate."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    if os.name == "nt":
        from academic_pdf_en_zh_reader.security.windows_worker import (
            run_security_probe,
        )

        report = run_security_probe()
    else:
        from academic_pdf_en_zh_reader.security.unsupported_worker import (
            probe_unsupported_platform,
        )

        report = probe_unsupported_platform(sys.platform)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
