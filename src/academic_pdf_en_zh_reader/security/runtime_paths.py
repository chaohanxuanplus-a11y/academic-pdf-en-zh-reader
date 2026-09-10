# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Resolve runtime paths without granting access to worker ancestors."""

import os
from pathlib import Path

from .worker_protocol import ProtocolError


def _has_worker_token() -> bool:
    if os.name != "nt":
        return False
    from . import windows_worker

    try:
        return windows_worker._current_process_has_zero_capability_appcontainer()
    except OSError:
        return False


def resolve_runtime_path(path: Path, *, strict: bool = False) -> Path:
    """Keep normal resolution; on LPAC ancestor denial, verify only within cwd.

    The launcher has already copied, hashed, and restricted this workspace.
    No caller flag or environment variable enables the fallback: the current
    process must meet the existing zero-capability AppContainer child contract.
    This helper does not prove LPAC: when token class 46 is unsupported, LPAC
    evidence remains the parent's verified process-creation opt-out policy and
    isolation probes. No token, ACL, access grant or parent check is changed.
    """
    try:
        return path.resolve(strict=strict)
    except PermissionError:
        if not _has_worker_token():
            raise

    from . import windows_worker

    # Check the original path before abspath could erase parent traversal.
    if (
        ".." in path.parts
        or (path.drive and not path.is_absolute())
        or str(path).startswith(("\\\\", "//"))
    ):
        raise ProtocolError("runtime path is not a local normalized path")
    root = Path(os.path.abspath(os.getcwd()))
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ProtocolError("runtime path escapes the worker workspace") from error
    if candidate == root:
        if not root.is_dir() or windows_worker._child_path_is_reparse(root):
            raise ProtocolError("worker workspace is not a real directory")
        return root
    return windows_worker._resolve_prevalidated_appcontainer_path(
        relative.as_posix(), must_exist=strict
    )
