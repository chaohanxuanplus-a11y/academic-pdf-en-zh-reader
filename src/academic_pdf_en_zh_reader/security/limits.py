# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Version-one resource ceilings for untrusted PDF workers."""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    """Finite limits shared by input copying, IPC, and the worker job."""

    max_input_bytes: int = 100 * 1024 * 1024
    max_pages: int = 500
    max_objects: int = 250_000
    max_recursion_depth: int = 64
    max_uncompressed_bytes: int = 1024 * 1024 * 1024
    max_image_bytes: int = 512 * 1024 * 1024
    max_result_object_bytes: int = 16 * 1024 * 1024
    max_extraction_artifact_bytes: int = 128 * 1024 * 1024
    max_normalized_pdf_bytes: int = 128 * 1024 * 1024
    max_protocol_bytes: int = 64 * 1024
    process_memory_bytes: int = 1024 * 1024 * 1024
    job_memory_bytes: int = 1024 * 1024 * 1024
    active_process_limit: int = 1
    cpu_time_seconds: float = 120.0
    wall_time_seconds: float = 180.0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{item.name} must be greater than zero")
        if self.active_process_limit > 8:
            raise ValueError("active_process_limit must not exceed 8")
        if self.job_memory_bytes < self.process_memory_bytes:
            raise ValueError("job_memory_bytes must be at least process_memory_bytes")


DEFAULT_LIMITS = WorkerLimits()
