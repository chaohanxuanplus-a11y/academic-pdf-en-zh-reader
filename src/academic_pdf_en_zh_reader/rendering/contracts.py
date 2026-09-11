# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Small fail-closed contracts for deterministic overlay planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass


class OverlayPlanError(ValueError):
    """A stable pre-render failure that must not leave a PDF artifact."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OverlayPlanLimits:
    """Finite complexity bounds checked before drawing-plan expansion."""

    version: int = 1
    max_pages: int = 2_000
    max_blocks_per_page: int = 20_000
    max_lines: int = 1_000_000
    max_characters: int = 20_000_000
    max_draw_runs: int = 2_000_000

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(type(value) is not int for value in values.values()):
            raise OverlayPlanError(
                "PLAN_COMPLEXITY_LIMIT",
                "overlay limits must be integers",
            )
        if self.version < 1 or any(
            value < 1 for key, value in values.items() if key != "version"
        ):
            raise OverlayPlanError(
                "PLAN_COMPLEXITY_LIMIT",
                "overlay limits must be positive",
            )


DEFAULT_OVERLAY_PLAN_LIMITS = OverlayPlanLimits()
