# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed adapter used where no equivalent worker sandbox exists."""

from __future__ import annotations

import sys
from typing import NoReturn


class UnsupportedPlatformError(RuntimeError):
    """The platform has no audited sandbox adapter."""


def run_worker(*, platform_name: str | None = None) -> NoReturn:
    platform = platform_name or sys.platform
    raise UnsupportedPlatformError(
        f"{platform}: no equivalent sandbox adapter is implemented; "
        "ordinary subprocess fallback is forbidden"
    )


def probe_unsupported_platform(platform_name: str | None = None) -> dict[str, object]:
    return {
        "platform": platform_name or sys.platform,
        "passed": False,
        "reason": "no equivalent sandbox adapter is implemented",
        "subprocess_fallback": False,
    }
