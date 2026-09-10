# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Permission failures must not expand the worker's verified workspace."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from academic_pdf_en_zh_reader.security import runtime_paths


def _restricted(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime_paths, "_has_worker_token", lambda: True)

    def denied(self: Path, strict: bool = False) -> Path:
        raise PermissionError("ancestor inaccessible")

    monkeypatch.setattr(Path, "resolve", denied)


def test_regular_process_preserves_resolution(tmp_path: Path) -> None:
    assert (
        runtime_paths.resolve_runtime_path(tmp_path, strict=True) == tmp_path.resolve()
    )


def test_successful_resolve_does_not_query_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected() -> bool:
        raise AssertionError("normal resolution must not query the worker token")

    monkeypatch.setattr(runtime_paths, "_has_worker_token", unexpected)
    assert (
        runtime_paths.resolve_runtime_path(tmp_path, strict=True) == tmp_path.resolve()
    )


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), OSError("other")])
def test_only_permission_denied_can_trigger_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    def failed(self: Path, strict: bool = False) -> Path:
        raise error

    monkeypatch.setattr(Path, "resolve", failed)
    monkeypatch.setattr(runtime_paths, "_has_worker_token", lambda: True)
    with pytest.raises(type(error)) as caught:
        runtime_paths.resolve_runtime_path(tmp_path, strict=True)
    assert caught.value is error


@pytest.mark.skipif(os.name != "nt", reason="queries a Windows token")
@pytest.mark.parametrize(
    ("appcontainer", "capabilities", "lpac", "expected"),
    [
        (False, 0, (True, True), False),
        (True, 1, (True, True), False),
        (True, 0, (False, True), False),
        (True, 0, (None, False), True),
        (True, 0, (True, True), True),
    ],
)
def test_fallback_preserves_existing_child_token_contract(
    monkeypatch: pytest.MonkeyPatch,
    appcontainer: bool,
    capabilities: int,
    lpac: tuple,
    expected: bool,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker

    monkeypatch.setattr(
        windows_worker,
        "_current_process_security",
        lambda: (False, [], appcontainer, capabilities),
    )
    monkeypatch.setattr(windows_worker, "_current_process_lpac_status", lambda: lpac)
    assert runtime_paths._has_worker_token() is expected


@pytest.mark.skipif(os.name != "nt", reason="queries a Windows token")
def test_failed_token_query_does_not_authorize_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import windows_worker

    def failed() -> bool:
        raise OSError("query unavailable")

    monkeypatch.setattr(
        windows_worker, "_current_process_has_zero_capability_appcontainer", failed
    )
    assert runtime_paths._has_worker_token() is False


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
@pytest.mark.parametrize(
    "unsafe",
    ["C:relative", r"\\server\share\file", r"\\?\C:\file", "input.pdf:stream", "NUL"],
)
def test_lpac_rejects_windows_alias_and_device_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    _restricted(monkeypatch, tmp_path)
    with pytest.raises((OSError, ValueError)):
        runtime_paths.resolve_runtime_path(Path(unsafe), strict=False)


def test_permission_failure_without_lpac_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restricted(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_paths, "_has_worker_token", lambda: False)
    with pytest.raises(PermissionError):
        runtime_paths.resolve_runtime_path(tmp_path, strict=True)


def test_lpac_can_validate_root_and_existing_child_without_ancestor_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = tmp_path / "input.pdf"
    child.write_bytes(b"input")
    _restricted(monkeypatch, tmp_path)
    assert runtime_paths.resolve_runtime_path(tmp_path, strict=True) == tmp_path
    assert runtime_paths.resolve_runtime_path(child, strict=True) == child


@pytest.mark.parametrize("relative", ["../outside.pdf", "a/../input.pdf"])
def test_lpac_rejects_parent_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    _restricted(monkeypatch, tmp_path)
    with pytest.raises((OSError, ValueError)):
        runtime_paths.resolve_runtime_path(tmp_path / relative, strict=True)


def test_lpac_rejects_absolute_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restricted(monkeypatch, tmp_path)
    with pytest.raises((OSError, ValueError)):
        runtime_paths.resolve_runtime_path(tmp_path.parent / "outside", strict=True)


def test_lpac_requires_existing_intermediate_and_strict_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restricted(monkeypatch, tmp_path)
    with pytest.raises((OSError, ValueError)):
        runtime_paths.resolve_runtime_path(tmp_path / "missing", strict=True)
    with pytest.raises((OSError, ValueError)):
        runtime_paths.resolve_runtime_path(tmp_path / "missing" / "leaf", strict=False)
    assert (
        runtime_paths.resolve_runtime_path(tmp_path / "new", strict=False)
        == tmp_path / "new"
    )


@pytest.mark.parametrize("location", ["root", "directory", "leaf"])
def test_lpac_rejects_reparse_at_every_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    directory = tmp_path / "data"
    directory.mkdir()
    leaf = directory / "input.pdf"
    leaf.write_bytes(b"input")
    target = {"root": tmp_path, "directory": directory, "leaf": leaf}[location]
    original_lstat = Path.lstat
    _restricted(monkeypatch, tmp_path)

    def reparse(path: Path):
        if path == target:
            return SimpleNamespace(st_mode=0, st_file_attributes=0x400)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises((OSError, ValueError)):
        runtime_paths.resolve_runtime_path(leaf, strict=True)
