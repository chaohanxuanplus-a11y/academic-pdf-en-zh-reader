# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Stable encoded-image fingerprints shared by composition and final QA."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256

from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    StreamObject,
)


class ImageFingerprintError(ValueError):
    """An image resource graph cannot be fingerprinted deterministically."""


def _resolved(value: object) -> object:
    return value.get_object() if isinstance(value, IndirectObject) else value


def page_image_fingerprints(page: object) -> Counter[str]:
    """Count encoded image XObjects reachable from one page resource graph."""

    try:
        resources = _resolved(page["/Resources"])  # type: ignore[index]
    except Exception as exc:
        raise ImageFingerprintError("IMAGE_RESOURCES_INVALID") from exc
    result: Counter[str] = Counter()
    stack: list[object] = [resources]
    seen: set[int | tuple[int, int]] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, IndirectObject):
            identity: int | tuple[int, int] = (value.idnum, value.generation)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                value = value.get_object()
            except Exception as exc:
                raise ImageFingerprintError("IMAGE_OBJECT_INVALID") from exc
        elif isinstance(value, (DictionaryObject, ArrayObject)):
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
        if (
            isinstance(value, StreamObject)
            and str(_resolved(value.get("/Subtype"))) == "/Image"
        ):
            raw = getattr(value, "_data", None)
            if not isinstance(raw, bytes):
                raise ImageFingerprintError("IMAGE_STREAM_INVALID")
            identity_payload = b"\0".join(
                (
                    str(_resolved(value.get("/Width"))).encode("ascii", "strict"),
                    str(_resolved(value.get("/Height"))).encode("ascii", "strict"),
                    str(_resolved(value.get("/Filter"))).encode("ascii", "strict"),
                    raw,
                )
            )
            result[sha256(identity_payload).hexdigest()] += 1
            continue
        if isinstance(value, DictionaryObject):
            xobjects = _resolved(value.get("/XObject"))
            if isinstance(xobjects, DictionaryObject):
                stack.extend(xobjects.values())
            if str(_resolved(value.get("/Subtype"))) == "/Form":
                nested = _resolved(value.get("/Resources"))
                if isinstance(nested, DictionaryObject):
                    stack.append(nested)
    return result


__all__ = ["ImageFingerprintError", "page_image_fingerprints"]
