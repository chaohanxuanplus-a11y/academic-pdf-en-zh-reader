# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Child-worker PDF preflight internals.

The package intentionally exposes no CLI or parent-process parsing shortcut.
Worker integration imports the explicit entry point from :mod:`.checks` only
after the sandboxed child has started.
"""
