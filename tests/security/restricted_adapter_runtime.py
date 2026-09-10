# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Private copies for trusted token/Job probes, not a production sandbox.

The current user can write these copies, just like the legacy adapter's original
runtime. Production LPAC execution and its runtime preparation are not replaced.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security import windows_worker as worker
from academic_pdf_en_zh_reader.security.input_copy import _create_private_directory
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import WorkerRequest


@dataclass(frozen=True)
class RestrictedProbeRuntime:
    root: Path
    python: worker._PythonRuntimeCopy
    project: worker._WorkerRuntimeCopy

    def run_probe(
        self,
        request: WorkerRequest,
        workspace: Path,
        *,
        limits: WorkerLimits = DEFAULT_LIMITS,
    ) -> worker.WorkerRunResult:
        if request.operation != "probe":
            raise worker.SandboxUnavailableError(
                "test runtime copies support only trusted probe requests"
            )
        original_command = worker._child_command
        pythonw = self.python.root / "pythonw.exe"

        def copied_command(runtime_root=None, python_executable=None, limits=None):
            if runtime_root is not None or python_executable != pythonw:
                raise worker.SandboxUnavailableError(
                    "test runtime copy cannot replace a production command"
                )
            return original_command(self.project.root, pythonw, limits)

        # Restore both callables even when the original launcher raises. No
        # production entrypoint or token/Job/handle-list function is patched.
        with pytest.MonkeyPatch.context() as scoped:
            scoped.setattr(
                worker,
                "_python_executable",
                lambda: str(self.python.root / "python.exe"),
            )
            scoped.setattr(worker, "_child_command", copied_command)
            return worker._run_restricted_worker_for_test(
                request, workspace, limits=limits
            )


@contextmanager
def prepare_restricted_probe_runtime(parent: Path):
    root = _create_private_directory(parent)
    try:
        # Reuse the existing source-manifest, size/hash and path checks. No
        # user input or secret is copied into this trusted probe-only tree.
        project = worker._copy_worker_runtime(root, operation="probe")
        python = worker._copy_minimal_python_runtime(root)
        yield RestrictedProbeRuntime(root=root, python=python, project=project)
    finally:
        errors = worker._remove_tree_and_verify(root, label="test-adapter runtime copy")
        if errors:
            raise worker.SandboxCleanupError("; ".join(errors))
