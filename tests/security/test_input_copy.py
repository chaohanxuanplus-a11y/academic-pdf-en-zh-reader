# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from academic_pdf_en_zh_reader.security.input_copy import (
    UnsafeInputError,
    copy_untrusted_input,
    private_directory_is_current_user_only,
)
from academic_pdf_en_zh_reader.security.limits import WorkerLimits


def test_regular_file_is_copied_to_a_private_directory_and_rehashed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    payload = b"%PDF-1.7\nsynthetic input\n%%EOF\n"
    source.write_bytes(payload)

    copied = copy_untrusted_input(source, temp_parent=tmp_path / "sandboxes")
    try:
        assert copied.path.parent == copied.root
        assert copied.path != source
        assert copied.path.read_bytes() == payload
        assert copied.sha256 == hashlib.sha256(payload).hexdigest()
        assert copied.size == len(payload)
        assert private_directory_is_current_user_only(copied.root)
    finally:
        copied.cleanup()

    assert not copied.root.exists()


def test_directory_and_reserved_device_name_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnsafeInputError, match="regular file"):
        copy_untrusted_input(tmp_path)

    if os.name == "nt":
        with pytest.raises(UnsafeInputError, match="device"):
            copy_untrusted_input(Path("NUL"))


def test_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.pdf"
    target.write_bytes(b"target")
    link = tmp_path / "link.pdf"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"creating symlinks is unavailable: {error}")

    with pytest.raises(UnsafeInputError, match="reparse|symlink"):
        copy_untrusted_input(link)


def test_source_size_limit_is_enforced_before_copy(tmp_path: Path) -> None:
    source = tmp_path / "large.pdf"
    source.write_bytes(b"12345")
    limits = WorkerLimits(max_input_bytes=4)

    with pytest.raises(UnsafeInputError, match="size limit"):
        copy_untrusted_input(source, limits=limits)


def test_copy_reads_the_already_opened_file_when_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import input_copy

    source = tmp_path / "paper.pdf"
    replacement = tmp_path / "replacement.pdf"
    source.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    original_copy = input_copy._copy_and_hash
    replacement_succeeded = False

    def replace_during_copy(*args: object, **kwargs: object) -> tuple[str, int]:
        nonlocal replacement_succeeded
        try:
            os.replace(replacement, source)
            replacement_succeeded = True
        except PermissionError:
            pass
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(input_copy, "_copy_and_hash", replace_during_copy)
    copied = copy_untrusted_input(source, temp_parent=tmp_path / "sandboxes")
    try:
        assert copied.path.read_bytes() == b"original"
        assert copied.sha256 == hashlib.sha256(b"original").hexdigest()
        if os.name == "nt":
            assert replacement_succeeded is False
    finally:
        copied.cleanup()


def test_keyboard_interrupt_during_copy_removes_private_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import input_copy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"synthetic paper")
    staging_parent = tmp_path / "sandboxes"

    def interrupt_copy(*_args: object, **_kwargs: object) -> tuple[str, int]:
        raise KeyboardInterrupt

    monkeypatch.setattr(input_copy, "_copy_and_hash", interrupt_copy)

    with pytest.raises(KeyboardInterrupt):
        copy_untrusted_input(source, temp_parent=staging_parent)

    assert list(staging_parent.iterdir()) == []


def test_keyboard_interrupt_during_private_directory_verification_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import input_copy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"synthetic paper")
    staging_parent = tmp_path / "sandboxes"

    def interrupt_verification(_path: Path) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        input_copy,
        "private_directory_is_current_user_only",
        interrupt_verification,
    )

    with pytest.raises(KeyboardInterrupt):
        copy_untrusted_input(source, temp_parent=staging_parent)

    assert list(staging_parent.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-path contract")
def test_windows_extended_device_namespace_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    extended_path = Path("\\\\?\\" + str(source))

    with pytest.raises(UnsafeInputError, match="device namespace"):
        copy_untrusted_input(extended_path, temp_parent=tmp_path / "sandboxes")


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-path contract")
def test_windows_open_handle_final_path_must_match_requested_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import input_copy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    redirected = tmp_path / "redirected.pdf"
    redirected.write_bytes(b"redirected")
    monkeypatch.setattr(
        input_copy,
        "_final_path_from_handle",
        lambda _handle: redirected,
        raising=False,
    )

    with pytest.raises(UnsafeInputError, match="final path|redirect"):
        copy_untrusted_input(source, temp_parent=tmp_path / "sandboxes")


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT handle ownership")
def test_windows_fdopen_failure_closes_transferred_descriptor_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.security import input_copy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    real_close = os.close
    closed_descriptors: list[int] = []
    close_handle_calls: list[object] = []
    real_close_handle = input_copy._kernel32.CloseHandle

    def close_descriptor(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    def close_handle(handle: object) -> object:
        close_handle_calls.append(handle)
        return real_close_handle(handle)

    def fail_fdopen(*_args: object, **_kwargs: object) -> object:
        raise OSError("synthetic fdopen failure")

    monkeypatch.setattr(input_copy.os, "close", close_descriptor)
    monkeypatch.setattr(input_copy.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(input_copy._kernel32, "CloseHandle", close_handle)

    with pytest.raises(OSError, match="synthetic fdopen failure"):
        input_copy._open_source(source, WorkerLimits())

    assert len(closed_descriptors) == 1
    assert close_handle_calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT handle ownership")
def test_windows_open_osfhandle_failure_closes_native_handle_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import msvcrt

    from academic_pdf_en_zh_reader.security import input_copy

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    close_handle_calls: list[object] = []
    real_close_handle = input_copy._kernel32.CloseHandle

    def close_handle(handle: object) -> object:
        close_handle_calls.append(handle)
        return real_close_handle(handle)

    def fail_open_osfhandle(*_args: object, **_kwargs: object) -> int:
        raise OSError("synthetic open_osfhandle failure")

    monkeypatch.setattr(msvcrt, "open_osfhandle", fail_open_osfhandle)
    monkeypatch.setattr(input_copy._kernel32, "CloseHandle", close_handle)

    with pytest.raises(OSError, match="synthetic open_osfhandle failure"):
        input_copy._open_source(source, WorkerLimits())

    assert len(close_handle_calls) == 1
