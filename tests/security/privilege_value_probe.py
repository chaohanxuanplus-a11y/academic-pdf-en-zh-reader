# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Query the real host LUID without retaining RPC state in the pytest process."""

from __future__ import annotations

import ctypes
import json
from ctypes import wintypes


class Luid(ctypes.Structure):
    _fields_ = [("low_part", wintypes.DWORD), ("high_part", wintypes.LONG)]


if __name__ == "__main__":
    api = ctypes.WinDLL("advapi32", use_last_error=True).LookupPrivilegeValueW
    api.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(Luid)]
    api.restype = wintypes.BOOL
    value = Luid()
    if not api(None, "SeChangeNotifyPrivilege", ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    print(json.dumps({"low_part": value.low_part, "high_part": value.high_part}))
