# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from academic_pdf_en_zh_reader.security.unsupported_worker import (
    UnsupportedPlatformError,
    probe_unsupported_platform,
    run_worker,
)


def test_unsupported_platform_fails_closed_without_launching_a_subprocess() -> None:
    with pytest.raises(UnsupportedPlatformError, match="no equivalent sandbox"):
        run_worker(platform_name="example-os")

    result = probe_unsupported_platform("example-os")
    assert result == {
        "platform": "example-os",
        "passed": False,
        "reason": "no equivalent sandbox adapter is implemented",
        "subprocess_fallback": False,
    }
