# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Small platform primitive for publishing a file without replacement."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path

_MOVEFILE_WRITE_THROUGH = 0x8
_WINDOWS_FILE_EXISTS = {80, 183}


def atomic_publish_no_clobber(staging: Path, target: Path) -> None:
    """Atomically publish ``staging`` at ``target`` without replacing a target.

    On POSIX the staging hard link remains for the caller to remove. On Windows
    the move consumes it. Callers must therefore clean a still-existing staging
    path in ``finally`` on both platforms.
    """

    if staging.parent != target.parent:
        raise OSError(errno.EXDEV, "staging and target must share a directory")

    if os.name != "nt":
        os.link(staging, target)
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file_ex.restype = ctypes.c_int
    if move_file_ex(str(staging), str(target), _MOVEFILE_WRITE_THROUGH):
        return
    error = ctypes.get_last_error()
    if error in _WINDOWS_FILE_EXISTS or os.path.lexists(target):
        raise FileExistsError(error, "target already exists", str(target))
    raise OSError(error, "atomic publication failed", str(target))


__all__ = ["atomic_publish_no_clobber"]
