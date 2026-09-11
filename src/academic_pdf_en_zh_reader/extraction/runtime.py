# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Load the pinned pdfminer package without its socket-dependent version lookup."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path

_PDFMINER_VERSION = "20260107"
_PDFMINER_INIT_SHA256 = (
    "5eea23a99e7fde90b9d14ebb4b9138271a0083b14bdc952ee43c04f85908cf0e"
)


def initialize_pinned_pdfminer() -> None:
    """Adapt only the exact reviewed, version-only upstream initializer.

    Its importlib.metadata lookup imports email.utils/socket, whose Winsock
    initialization is intentionally denied by LPAC. All parser modules remain
    the unchanged, hash-verified upstream files. No networking API is replaced.
    The adapter itself is included in the worker's project-runtime fingerprint.
    """
    spec = importlib.util.find_spec("pdfminer")
    if spec is None or spec.origin is None or not spec.submodule_search_locations:
        raise ImportError("pinned pdfminer package is unavailable")
    # This is a parent-verified, read-only runtime file, not an external input.
    # LPAC cannot inspect ancestors outside its runtime tree. Verify the opened
    # file and exact bounded bytes without repeating parent-side path traversal.
    with Path(spec.origin).open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ImportError("pdfminer initializer is not a bounded regular file")
        initializer = stream.read(4097)
    if hashlib.sha256(initializer).hexdigest() != _PDFMINER_INIT_SHA256:
        raise ImportError("pdfminer initializer differs from the reviewed version")
    if "pdfminer" in sys.modules:
        if getattr(sys.modules["pdfminer"], "__version__", None) != _PDFMINER_VERSION:
            raise ImportError("loaded pdfminer version differs from the pinned version")
        return
    package = importlib.util.module_from_spec(spec)
    package.__version__ = _PDFMINER_VERSION
    sys.modules["pdfminer"] = package
