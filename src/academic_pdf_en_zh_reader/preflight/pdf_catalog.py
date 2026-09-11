# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Bounded PDF graph, stream, image, and active-content inspection."""

from __future__ import annotations

import base64
import binascii
import tempfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, BinaryIO

from pypdf._utils import read_non_whitespace, read_until_regex
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    EncodedStreamObject,
    IndirectObject,
    NameObject,
    StreamObject,
    read_object,
)

if TYPE_CHECKING:
    from pypdf._page import PageObject
    from pypdf._reader import PdfReader

_CHUNK_BYTES = 64 * 1024
_INLINE_HEADER_BYTES = 64 * 1024
_INLINE_HEADER_ENTRIES = 64
_JPEG_MARKER_LIMIT = 65_536
_JPEG_LENGTH_MARKERS = frozenset(
    (*range(0xC0, 0xD0), *range(0xDA, 0xE0), *range(0xE0, 0xFF))
)
_MAX_REPORTED_BYTES = (1 << 63) - 1
_KNOWN_FILTERS = frozenset(
    {
        "/ASCII85Decode",
        "/ASCIIHexDecode",
        "/CCITTFaxDecode",
        "/DCTDecode",
        "/FlateDecode",
        "/JPXDecode",
        "/RunLengthDecode",
        "/A85",
        "/AHx",
        "/CCF",
        "/DCT",
        "/Fl",
        "/RL",
    }
)
_TERMINAL_IMAGE_FILTERS = frozenset(
    {"/CCITTFaxDecode", "/DCTDecode", "/JPXDecode", "/CCF", "/DCT"}
)
_FLATE_FILTERS = frozenset({"/FlateDecode", "/Fl"})
_ASCII85_FILTERS = frozenset({"/ASCII85Decode", "/A85"})
_ASCIIHEX_FILTERS = frozenset({"/ASCIIHexDecode", "/AHx"})
_WHITESPACE = frozenset(b"\x00\x09\x0a\x0c\x0d\x20")
_INHERENT_COLOR_SPACES = {
    "/DeviceGray": 1,
    "/DeviceRGB": 3,
    "/DeviceCMYK": 4,
    "/G": 1,
    "/RGB": 3,
    "/CMYK": 4,
}
_INVENTORY_KEYS = (
    "javascript",
    "open_actions",
    "attachments",
    "forms",
    "launch_actions",
    "rich_media",
    "submit_actions",
)


@dataclass(frozen=True, slots=True)
class CatalogLimits:
    max_objects: int
    max_recursion_depth: int
    max_decoded_stream_bytes: int
    max_image_bytes: int


@dataclass(slots=True)
class CatalogFacts:
    object_count: int = 0
    recursion_depth: int = 0
    decoded_stream_bytes: int = 0
    image_bytes: int = 0
    inventory: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_INVENTORY_KEYS, 0)
    )
    errors: list[tuple[str, str]] = field(default_factory=list)

    def reject(self, code: str, details: str) -> None:
        if all(existing_code != code for existing_code, _ in self.errors):
            self.errors.append((code, details))


def _resolved(value: Any) -> Any:
    return value.get_object() if isinstance(value, IndirectObject) else value


def _filter_names(raw: Any, facts: CatalogFacts) -> tuple[str, ...] | None:
    if raw is None:
        return ()
    try:
        raw = _resolved(raw)
        if isinstance(raw, NameObject):
            values: list[Any] = [raw]
        elif isinstance(raw, ArrayObject):
            values = list(raw)
        else:
            facts.reject("MALFORMED_PDF_FILTER", "PDF filter entry is malformed")
            return None
        names: list[str] = []
        for value in values:
            value = _resolved(value)
            if not isinstance(value, NameObject):
                facts.reject("MALFORMED_PDF_FILTER", "PDF filter entry is malformed")
                return None
            name = str(value)
            if name in {"/JBIG2Decode", "/JBIG2"}:
                facts.reject("UNSUPPORTED_JBIG2", "JBIG2 streams are not accepted")
                return None
            if name not in _KNOWN_FILTERS:
                facts.reject(
                    "UNSUPPORTED_PDF_FILTER",
                    "PDF stream uses an unsupported filter",
                )
                return None
            names.append(name)
        return tuple(names)
    except Exception:
        facts.reject("MALFORMED_PDF_FILTER", "PDF filter entry is malformed")
        return None


def _filters(stream: StreamObject, facts: CatalogFacts) -> tuple[str, ...] | None:
    return _filter_names(stream.get("/Filter"), facts)


def _bounded_flate_size(data: bytes, maximum: int) -> tuple[int, bool]:
    decoder = zlib.decompressobj()
    total = 0
    offset = 0
    try:
        while offset < len(data):
            pending = data[offset : offset + _CHUNK_BYTES]
            offset += len(pending)
            while pending:
                allowance = min(_CHUNK_BYTES, maximum - total + 1)
                decoded = decoder.decompress(pending, max(1, allowance))
                total += len(decoded)
                if total > maximum:
                    return total, True
                pending = decoder.unconsumed_tail
        while not decoder.eof:
            allowance = min(_CHUNK_BYTES, maximum - total + 1)
            decoded = decoder.decompress(b"", max(1, allowance))
            total += len(decoded)
            if total > maximum:
                return total, True
            if not decoded:
                break
    except zlib.error:
        return 0, False
    return total, decoder.eof


def _unwrap_ascii(data: bytes, name: str, facts: CatalogFacts) -> bytes | None:
    try:
        if name in _ASCII85_FILTERS:
            return base64.a85decode(data, adobe=True)
        compact = b"".join(data.split()).removesuffix(b">")
        if len(compact) % 2:
            compact += b"0"
        return binascii.unhexlify(compact)
    except (binascii.Error, TypeError, ValueError):
        facts.reject("MALFORMED_PDF_STREAM", "encoded PDF stream is malformed")
        return None


def _stream_size(
    stream: StreamObject,
    filters: tuple[str, ...],
    *,
    remaining: int,
    facts: CatalogFacts,
) -> int | None:
    encoded = getattr(stream, "_data", None)
    if not isinstance(encoded, bytes):
        facts.reject("MALFORMED_PDF_STREAM", "PDF stream payload is malformed")
        return None
    working = encoded
    remaining_filters = filters
    if remaining_filters and remaining_filters[0] in (
        _ASCII85_FILTERS | _ASCIIHEX_FILTERS
    ):
        unwrapped = _unwrap_ascii(working, remaining_filters[0], facts)
        if unwrapped is None:
            return None
        working = unwrapped
        remaining_filters = remaining_filters[1:]
    if not remaining_filters:
        return len(working)
    if len(remaining_filters) == 1 and remaining_filters[0] in _FLATE_FILTERS:
        size, complete = _bounded_flate_size(working, max(0, remaining))
        if not complete and size <= remaining:
            facts.reject("MALFORMED_PDF_STREAM", "Flate PDF stream is malformed")
            return None
        return size
    if len(remaining_filters) == 1 and remaining_filters[0] in (
        _TERMINAL_IMAGE_FILTERS
    ):
        return len(working)
    if len(remaining_filters) == 1 and remaining_filters[0] in {
        "/RunLengthDecode",
        "/RL",
    }:
        return min(_MAX_REPORTED_BYTES, len(working) * 128)
    facts.reject(
        "UNSUPPORTED_PDF_FILTER",
        "PDF filter chain has no bounded decoder",
    )
    return None


def _plain_positive_int(value: Any) -> int | None:
    try:
        value = _resolved(value)
        if isinstance(value, bool):
            return None
        number = int(value)
    except Exception:
        return None
    return number if number > 0 and number == value else None


def _positive_int(value: Any, *, field_name: str, facts: CatalogFacts) -> int | None:
    number = _plain_positive_int(value)
    if number is None:
        facts.reject("MALFORMED_IMAGE", f"{field_name} must be a positive integer")
    return number


def _color_components(
    value: Any,
    facts: CatalogFacts,
    *,
    aliases: DictionaryObject | None = None,
    depth: int = 0,
    seen: frozenset[int | str] = frozenset(),
) -> int | None:
    if depth > 16:
        facts.reject("MALFORMED_COLOR_SPACE", "color-space nesting is invalid")
        return None
    try:
        value = _resolved(value)
    except Exception:
        facts.reject("MALFORMED_COLOR_SPACE", "color-space object is invalid")
        return None
    if isinstance(value, NameObject):
        name = str(value)
        components = _INHERENT_COLOR_SPACES.get(name)
        if components is not None:
            return components
        if aliases is not None and value in aliases and name not in seen:
            return _color_components(
                aliases[value],
                facts,
                aliases=aliases,
                depth=depth + 1,
                seen=seen | {name},
            )
        facts.reject("MALFORMED_COLOR_SPACE", "color-space name is unsupported")
        return None
    if not isinstance(value, ArrayObject) or not value:
        facts.reject("MALFORMED_COLOR_SPACE", "color-space object is malformed")
        return None
    try:
        family_object = _resolved(value[0])
    except Exception:
        family_object = None
    if not isinstance(family_object, NameObject):
        facts.reject("MALFORMED_COLOR_SPACE", "color-space family must be a name")
        return None
    identity = id(value)
    if identity in seen:
        facts.reject("MALFORMED_COLOR_SPACE", "color-space cycle is invalid")
        return None
    nested_seen = seen | {identity}
    family = str(family_object)
    if family in {"/DeviceGray", "/DeviceRGB", "/DeviceCMYK"}:
        if len(value) != 1:
            facts.reject("MALFORMED_COLOR_SPACE", "device color space is invalid")
            return None
        return _INHERENT_COLOR_SPACES[family]
    if family in {"/CalGray", "/CalRGB", "/Lab"}:
        if len(value) != 2 or not isinstance(_resolved(value[1]), DictionaryObject):
            facts.reject("MALFORMED_COLOR_SPACE", "calibrated color space is invalid")
            return None
        return 1 if family == "/CalGray" else 3
    if family == "/Indexed":
        if len(value) != 4:
            facts.reject("MALFORMED_COLOR_SPACE", "Indexed color space is invalid")
            return None
        base = _color_components(
            value[1],
            facts,
            aliases=aliases,
            depth=depth + 1,
            seen=nested_seen,
        )
        try:
            high_value = int(_resolved(value[2]))
        except Exception:
            high_value = -1
        if base is None or not 0 <= high_value <= 255:
            facts.reject("MALFORMED_COLOR_SPACE", "Indexed color space is invalid")
            return None
        return 1
    if family == "/Separation":
        if len(value) != 4 or not isinstance(_resolved(value[1]), NameObject):
            facts.reject("MALFORMED_COLOR_SPACE", "Separation color space is invalid")
            return None
        alternate = _color_components(
            value[2],
            facts,
            aliases=aliases,
            depth=depth + 1,
            seen=nested_seen,
        )
        return 1 if alternate is not None else None
    if family == "/DeviceN":
        if len(value) not in {4, 5}:
            facts.reject("MALFORMED_COLOR_SPACE", "DeviceN color space is invalid")
            return None
        names = _resolved(value[1])
        if (
            not isinstance(names, ArrayObject)
            or not 1 <= len(names) <= 32
            or any(not isinstance(_resolved(name), NameObject) for name in names)
        ):
            facts.reject("MALFORMED_COLOR_SPACE", "DeviceN color space is invalid")
            return None
        alternate = _color_components(
            value[2],
            facts,
            aliases=aliases,
            depth=depth + 1,
            seen=nested_seen,
        )
        return len(names) if alternate is not None else None
    if family == "/ICCBased":
        if len(value) != 2:
            facts.reject("MALFORMED_COLOR_SPACE", "ICCBased color space is invalid")
            return None
        profile = _resolved(value[1])
        if not isinstance(profile, StreamObject):
            facts.reject("MALFORMED_COLOR_SPACE", "ICCBased color space is invalid")
            return None
        components = _plain_positive_int(profile.get("/N"))
        if components not in {1, 3, 4}:
            facts.reject("MALFORMED_COLOR_SPACE", "ICCBased color space is invalid")
            return None
        alternate = profile.get("/Alternate")
        if alternate is not None:
            alternate_components = _color_components(
                alternate,
                facts,
                aliases=aliases,
                depth=depth + 1,
                seen=nested_seen,
            )
            if alternate_components != components:
                facts.reject("MALFORMED_COLOR_SPACE", "ICCBased alternate is invalid")
                return None
        return components
    # Report only fixed known family names, never arbitrary PDF strings.
    detail = {
        "/Pattern": "Pattern color space cannot describe image samples",
        "/I": "abbreviated Indexed color-space family is unsupported",
        "/CalCMYK": "legacy CalCMYK color-space family is unsupported",
    }.get(family, "color-space family is unsupported")
    facts.reject("MALFORMED_COLOR_SPACE", detail)
    return None


def _scanline_image_bytes(width: int, height: int, bits: int, components: int) -> int:
    if width > (_MAX_REPORTED_BYTES * 8) // bits // components:
        return _MAX_REPORTED_BYTES
    row_bits = width * bits * components
    row_bytes = (row_bits + 7) // 8
    if row_bytes > _MAX_REPORTED_BYTES // height:
        return _MAX_REPORTED_BYTES
    return row_bytes * height


def _image_size(
    stream: StreamObject,
    facts: CatalogFacts,
    *,
    aliases: DictionaryObject | None = None,
) -> int | None:
    image_mask = bool(stream.get("/ImageMask", False))
    width = _positive_int(stream.get("/Width"), field_name="Width", facts=facts)
    height = _positive_int(stream.get("/Height"), field_name="Height", facts=facts)
    if image_mask:
        bits = 1
        components = 1
    else:
        bits = _positive_int(
            stream.get("/BitsPerComponent"),
            field_name="BitsPerComponent",
            facts=facts,
        )
        if bits not in {1, 2, 4, 8, 16}:
            facts.reject("MALFORMED_IMAGE", "BitsPerComponent is unsupported")
            return None
        components = _color_components(
            stream.get("/ColorSpace"), facts, aliases=aliases
        )
    if width is None or height is None or bits is None or components is None:
        return None
    return _scanline_image_bytes(width, height, bits, components)


def _add_image_bytes(size: int, facts: CatalogFacts, limits: CatalogLimits) -> None:
    facts.image_bytes = min(_MAX_REPORTED_BYTES, facts.image_bytes + size)
    if facts.image_bytes > limits.max_image_bytes:
        facts.reject(
            "IMAGE_BYTES_LIMIT_EXCEEDED",
            "estimated decoded image bytes exceed the limit",
        )


def _count_inventory(dictionary: DictionaryObject, facts: CatalogFacts) -> None:
    action_type = dictionary.get("/S")
    if action_type is not None:
        action_name = str(_resolved(action_type))
        if action_name == "/JavaScript":
            facts.inventory["javascript"] += 1
        elif action_name == "/Launch":
            facts.inventory["launch_actions"] += 1
        elif action_name == "/SubmitForm":
            facts.inventory["submit_actions"] += 1
    subtype = str(_resolved(dictionary.get("/Subtype")))
    if subtype == "/RichMedia":
        facts.inventory["rich_media"] += 1
    if subtype == "/FileAttachment":
        facts.inventory["attachments"] += 1


def _root_inventory(root: DictionaryObject, facts: CatalogFacts) -> None:
    if root.get("/OpenAction") is not None:
        facts.inventory["open_actions"] = 1
    acroform = root.get("/AcroForm")
    if acroform is not None:
        resolved = _resolved(acroform)
        fields = (
            resolved.get("/Fields") if isinstance(resolved, DictionaryObject) else None
        )
        fields = _resolved(fields) if fields is not None else None
        facts.inventory["forms"] = (
            max(1, len(fields)) if isinstance(fields, ArrayObject) else 1
        )
    names = root.get("/Names")
    names = _resolved(names) if names is not None else None
    embedded = (
        names.get("/EmbeddedFiles") if isinstance(names, DictionaryObject) else None
    )
    embedded = _resolved(embedded) if embedded is not None else None
    entries = embedded.get("/Names") if isinstance(embedded, DictionaryObject) else None
    entries = _resolved(entries) if entries is not None else None
    if isinstance(entries, ArrayObject):
        facts.inventory["attachments"] += len(entries) // 2
    elif embedded is not None:
        facts.inventory["attachments"] += 1


def _decoded_content_chunks(
    stream: StreamObject,
    filters: tuple[str, ...],
    facts: CatalogFacts,
    maximum: int,
) -> Iterator[bytes]:
    encoded = getattr(stream, "_data", None)
    if not isinstance(encoded, bytes):
        facts.reject("MALFORMED_PDF_STREAM", "PDF stream payload is malformed")
        return
    working = encoded
    remaining_filters = filters
    if remaining_filters and remaining_filters[0] in (
        _ASCII85_FILTERS | _ASCIIHEX_FILTERS
    ):
        unwrapped = _unwrap_ascii(working, remaining_filters[0], facts)
        if unwrapped is None:
            return
        working = unwrapped
        remaining_filters = remaining_filters[1:]
    if not remaining_filters:
        for offset in range(0, len(working), _CHUNK_BYTES):
            yield working[offset : offset + _CHUNK_BYTES]
        return
    if len(remaining_filters) != 1 or remaining_filters[0] not in _FLATE_FILTERS:
        facts.reject(
            "UNSUPPORTED_PDF_FILTER",
            "page content filter has no bounded scanner",
        )
        return
    decoder = zlib.decompressobj()
    produced = 0
    offset = 0
    try:
        while offset < len(working):
            pending = working[offset : offset + _CHUNK_BYTES]
            offset += len(pending)
            while pending:
                decoded = decoder.decompress(pending, _CHUNK_BYTES)
                produced += len(decoded)
                if produced > maximum:
                    facts.reject(
                        "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
                        "decoded page content exceeds the limit",
                    )
                    return
                if decoded:
                    yield decoded
                pending = decoder.unconsumed_tail
    except zlib.error:
        facts.reject("MALFORMED_PDF_STREAM", "Flate PDF stream is malformed")
        return
    if not decoder.eof:
        facts.reject("MALFORMED_PDF_STREAM", "Flate PDF stream is malformed")


def _content_streams(page: PageObject, facts: CatalogFacts) -> list[StreamObject]:
    try:
        contents = page.get("/Contents")
        if contents is None:
            return []
        contents = _resolved(contents)
        values = list(contents) if isinstance(contents, ArrayObject) else [contents]
        streams = [_resolved(value) for value in values]
        if any(not isinstance(value, StreamObject) for value in streams):
            raise TypeError
        return streams
    except Exception:
        facts.reject("MALFORMED_PDF_STREAM", "page content stream is malformed")
        return []


def _inline_aliases(page: PageObject, facts: CatalogFacts) -> DictionaryObject | None:
    try:
        resources = page.get("/Resources")
        resources = _resolved(resources) if resources is not None else None
        if resources is None:
            return None
        if not isinstance(resources, DictionaryObject):
            raise TypeError
        color_spaces = resources.get("/ColorSpace")
        color_spaces = _resolved(color_spaces) if color_spaces is not None else None
        if color_spaces is None:
            return None
        if not isinstance(color_spaces, DictionaryObject):
            raise TypeError
        for key, value in color_spaces.items():
            if not isinstance(key, NameObject):
                raise TypeError
            value = _resolved(value)
            if isinstance(value, NameObject) and value == "/Pattern":
                continue
            if (
                isinstance(value, ArrayObject)
                and value
                and isinstance(_resolved(value[0]), NameObject)
                and _resolved(value[0]) == NameObject("/Pattern")
            ):
                # Page painting resources may use Pattern; image samples may not.
                if len(value) == 2:
                    _color_components(value[1], facts, aliases=color_spaces)
                elif len(value) != 1:
                    facts.reject("MALFORMED_COLOR_SPACE", "Pattern resource is invalid")
                continue
            _color_components(value, facts, aliases=color_spaces)
        return color_spaces
    except Exception:
        facts.reject("MALFORMED_COLOR_SPACE", "resource color-space map is malformed")
        return None


def _consume_inline_end(stream: BinaryIO) -> bool:
    marker = read_non_whitespace(stream)
    if marker != b"E":
        return False
    stream.seek(-1, 1)
    try:
        return (
            read_until_regex(
                stream=stream, regex=NameObject.delimiter_pattern, length=32
            )
            == b"EI"
        )
    except Exception:
        return False


def _seek_format_end(stream: BinaryIO, marker: bytes) -> bool:
    """Find an encoded-format terminator, then consume the actual PDF EI."""

    matched = 0
    while current := stream.read(1):
        if current[0] == marker[matched]:
            matched += 1
            if matched == len(marker):
                after_marker = stream.tell()
                if _consume_inline_end(stream):
                    return True
                stream.seek(after_marker)
                matched = 0
        else:
            matched = 1 if current[0] == marker[0] else 0
    return False


def _add_inline_decoded_bytes(
    amount: int, facts: CatalogFacts, limits: CatalogLimits
) -> bool:
    remaining = limits.max_decoded_stream_bytes - facts.decoded_stream_bytes
    if amount > remaining:
        facts.decoded_stream_bytes = limits.max_decoded_stream_bytes + 1
        facts.reject(
            "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
            "decoded inline image data exceed the limit",
        )
        return False
    facts.decoded_stream_bytes += amount
    return True


def _seek_flate_inline_end(
    stream: BinaryIO, facts: CatalogFacts, limits: CatalogLimits
) -> bool:
    decoder = zlib.decompressobj()
    try:
        while chunk := stream.read(4096):
            pending = chunk
            while pending:
                decoded = decoder.decompress(pending, _CHUNK_BYTES)
                if not _add_inline_decoded_bytes(len(decoded), facts, limits):
                    return False
                if decoder.eof:
                    if decoder.unused_data:
                        stream.seek(-len(decoder.unused_data), 1)
                    return _consume_inline_end(stream)
                pending = decoder.unconsumed_tail
    except zlib.error:
        return False
    return False


def _seek_run_length_inline_end(
    stream: BinaryIO, facts: CatalogFacts, limits: CatalogLimits
) -> bool:
    while control := stream.read(1):
        length = control[0]
        if length == 128:
            return _consume_inline_end(stream)
        if length <= 127:
            decoded = length + 1
            if len(stream.read(decoded)) != decoded:
                return False
        else:
            decoded = 257 - length
            if len(stream.read(1)) != 1:
                return False
        if not _add_inline_decoded_bytes(decoded, facts, limits):
            return False
    return False


def _jpeg_marker(stream: BinaryIO) -> int | None:
    if stream.read(1) != b"\xff":
        return None
    marker = stream.read(1)
    while marker == b"\xff":
        marker = stream.read(1)
    if not marker or marker == b"\x00":
        return None
    return marker[0]


def _jpeg_entropy_marker(stream: BinaryIO) -> int | None:
    while chunk := stream.read(_CHUNK_BYTES):
        marker_at = chunk.find(b"\xff")
        if marker_at < 0:
            continue
        stream.seek(marker_at + 1 - len(chunk), 1)
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker:
            return None
        code = marker[0]
        if code == 0 or 0xD0 <= code <= 0xD7:
            continue
        return code
    return None


def _discard_exact(stream: BinaryIO, amount: int) -> bool:
    while amount:
        chunk = stream.read(min(amount, _CHUNK_BYTES))
        if not chunk:
            return False
        amount -= len(chunk)
    return True


def _seek_jpeg_inline_end(stream: BinaryIO) -> bool:
    """Consume one marker-valid JPEG payload and its following PDF EI."""

    if stream.read(2) != b"\xff\xd8":
        return False
    marker: int | None = None
    for _ in range(_JPEG_MARKER_LIMIT):
        if marker is None:
            marker = _jpeg_marker(stream)
        if marker is None:
            return False
        if marker == 0xD9:
            return _consume_inline_end(stream)
        if marker == 0x01:
            marker = None
            continue
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7:
            return False
        if marker not in _JPEG_LENGTH_MARKERS:
            return False
        length_bytes = stream.read(2)
        if len(length_bytes) != 2:
            return False
        length = int.from_bytes(length_bytes, "big")
        if length < 2 or not _discard_exact(stream, length - 2):
            return False
        marker = _jpeg_entropy_marker(stream) if marker == 0xDA else None
    return False


def _skip_filtered_inline_payload(
    stream: BinaryIO,
    filters: tuple[str, ...],
    facts: CatalogFacts,
    limits: CatalogLimits,
) -> bool:
    """Consume one bounded single-filter payload, failing closed otherwise."""

    if len(filters) != 1:
        facts.reject(
            "UNSUPPORTED_PDF_FILTER",
            "inline image filter chains are not accepted",
        )
        return False

    first = filters[0]
    if first in {"/JPXDecode", "/CCITTFaxDecode", "/CCF"}:
        facts.reject(
            "UNSUPPORTED_PDF_FILTER",
            "inline image filter cannot be unambiguously bounded",
        )
        return False
    if first in _ASCII85_FILTERS:
        return _seek_format_end(stream, b"~>")
    if first in _ASCIIHEX_FILTERS:
        return _seek_format_end(stream, b">")
    if first in _FLATE_FILTERS:
        return _seek_flate_inline_end(stream, facts, limits)
    if first in {"/RunLengthDecode", "/RL"}:
        return _seek_run_length_inline_end(stream, facts, limits)
    if first in {"/DCTDecode", "/DCT"}:
        return _seek_jpeg_inline_end(stream)
    return False


def _read_inline_settings(
    stream: BinaryIO, reader: PdfReader, facts: CatalogFacts
) -> DictionaryObject | None:
    settings = DictionaryObject()
    start = stream.tell()
    try:
        while len(settings) < _INLINE_HEADER_ENTRIES:
            if stream.tell() - start > _INLINE_HEADER_BYTES:
                break
            token = read_non_whitespace(stream)
            if not token:
                break
            stream.seek(-1, 1)
            if token == b"I":
                operator = read_until_regex(
                    stream=stream, regex=NameObject.delimiter_pattern, length=32
                )
                if operator != b"ID":
                    break
                separator = stream.read(1)
                if not separator or separator[0] not in _WHITESPACE:
                    break
                if separator == b"\r":
                    possible_lf = stream.read(1)
                    if possible_lf != b"\n":
                        stream.seek(-1, 1)
                return settings
            read_non_whitespace(stream)
            stream.seek(-1, 1)
            key = read_object(stream, reader)
            read_non_whitespace(stream)
            stream.seek(-1, 1)
            value = read_object(stream, reader)
            if not isinstance(key, NameObject):
                break
            settings[key] = value
    except Exception:
        pass
    facts.reject("MALFORMED_IMAGE", "inline image dictionary is malformed")
    return None


def _inline_value(settings: DictionaryObject, long: str, short: str) -> Any:
    return settings.get(long, settings.get(short))


def _inspect_inline_image(
    stream: BinaryIO,
    reader: PdfReader,
    aliases: DictionaryObject | None,
    facts: CatalogFacts,
    limits: CatalogLimits,
) -> bool:
    settings = _read_inline_settings(stream, reader, facts)
    if settings is None:
        return False
    filters = _filter_names(_inline_value(settings, "/Filter", "/F"), facts)
    image_mask = bool(_inline_value(settings, "/ImageMask", "/IM") or False)
    width = _positive_int(
        _inline_value(settings, "/Width", "/W"), field_name="Width", facts=facts
    )
    height = _positive_int(
        _inline_value(settings, "/Height", "/H"), field_name="Height", facts=facts
    )
    if image_mask:
        bits = 1
        components = 1
    else:
        bits = _positive_int(
            _inline_value(settings, "/BitsPerComponent", "/BPC"),
            field_name="BitsPerComponent",
            facts=facts,
        )
        if bits not in {1, 2, 4, 8, 16}:
            facts.reject("MALFORMED_IMAGE", "BitsPerComponent is unsupported")
            return False
        components = _color_components(
            _inline_value(settings, "/ColorSpace", "/CS"),
            facts,
            aliases=aliases,
        )
    if filters is None or None in {width, height, bits, components}:
        return False
    size = _scanline_image_bytes(width, height, bits, components)
    _add_image_bytes(size, facts, limits)
    if not filters:
        stream.seek(size, 1)
        if not _consume_inline_end(stream):
            facts.reject("MALFORMED_IMAGE", "inline image terminator is invalid")
            return False
        return True
    skipped = _skip_filtered_inline_payload(stream, filters, facts, limits)
    if not skipped:
        if not facts.errors:
            facts.reject("MALFORMED_IMAGE", "inline image terminator is invalid")
        return False
    return True


def _scan_page_inline_images(
    page: PageObject,
    reader: PdfReader,
    facts: CatalogFacts,
    limits: CatalogLimits,
) -> None:
    aliases = _inline_aliases(page, facts)
    streams = _content_streams(page, facts)
    if facts.errors:
        return
    with tempfile.SpooledTemporaryFile(
        max_size=256 * 1024, mode="w+b", dir="."
    ) as decoded:
        for content in streams:
            filters = _filters(content, facts)
            if filters is None:
                return
            for chunk in _decoded_content_chunks(
                content, filters, facts, limits.max_decoded_stream_bytes
            ):
                decoded.write(chunk)
            decoded.write(b"\n")
            if facts.errors:
                return
        decoded.seek(0)
        operands = 0
        try:
            while True:
                token = read_non_whitespace(decoded)
                if not token:
                    return
                decoded.seek(-1, 1)
                if token.isalpha() or token in {b"'", b'"'}:
                    operator = read_until_regex(
                        stream=decoded,
                        regex=NameObject.delimiter_pattern,
                        length=32,
                    )
                    if operator == b"BI":
                        if operands:
                            raise ValueError
                        keep_scanning = _inspect_inline_image(
                            decoded, reader, aliases, facts, limits
                        )
                        if facts.errors or not keep_scanning:
                            return
                    operands = 0
                elif token == b"%":
                    while token not in {b"\r", b"\n", b""}:
                        token = decoded.read(1)
                else:
                    read_object(decoded, reader)
                    operands += 1
                    if operands > 4096:
                        raise ValueError
        except Exception:
            facts.reject("MALFORMED_PDF_STREAM", "page content syntax is malformed")


def clear_page_decoded_stream_caches(page: PageObject, max_nodes: int) -> bool:
    """Release pypdf decoded stream caches reachable from one extracted page."""

    stack: list[Any] = [page]
    seen_indirect: set[tuple[int, int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0
    try:
        while stack:
            value = stack.pop()
            if isinstance(value, IndirectObject):
                identity = (value.idnum, value.generation, id(value.pdf))
                if identity in seen_indirect:
                    continue
                seen_indirect.add(identity)
                value = value.get_object()
            if isinstance(value, (DictionaryObject, ArrayObject)):
                identity = id(value)
                if identity in seen_direct:
                    continue
                seen_direct.add(identity)
            visited += 1
            if visited > max_nodes:
                return False
            if isinstance(value, EncodedStreamObject):
                value.decoded_self = None
            if isinstance(value, DictionaryObject):
                children = [
                    child for key, child in value.items() if str(key) != "/Parent"
                ]
            elif isinstance(value, ArrayObject):
                children = value
            else:
                continue
            if visited + len(stack) + len(children) > max_nodes:
                return False
            stack.extend(reversed(children))
    except Exception:
        return False
    return True


def inspect_catalog(
    reader: PdfReader,
    limits: CatalogLimits,
    *,
    pages: list[PageObject],
) -> CatalogFacts:
    """Traverse every reachable node with bounded stack and decoded output."""

    facts = CatalogFacts()
    try:
        root = _resolved(reader.trailer["/Root"])
    except Exception:
        facts.reject("PDF_CATALOG_INVALID", "PDF catalog cannot be resolved")
        return facts
    if not isinstance(root, DictionaryObject):
        facts.reject("PDF_CATALOG_INVALID", "PDF catalog is malformed")
        return facts
    try:
        _root_inventory(root, facts)
    except Exception:
        facts.reject(
            "PDF_OBJECT_RESOLUTION_ERROR",
            "PDF catalog inventory cannot be resolved",
        )

    seen_indirect: set[tuple[int, int, int]] = set()
    seen_direct: set[int] = set()
    stack: list[tuple[Any, int]] = [(reader.trailer, 0)]
    while stack:
        value, depth = stack.pop()
        facts.recursion_depth = max(facts.recursion_depth, depth)
        if depth > limits.max_recursion_depth:
            facts.recursion_depth = limits.max_recursion_depth + 1
            facts.reject(
                "RECURSION_DEPTH_LIMIT_EXCEEDED",
                "reachable object graph exceeds the recursion-depth limit",
            )
            continue

        from_indirect = isinstance(value, IndirectObject)
        if from_indirect:
            identity = (value.idnum, value.generation, id(value.pdf))
            if identity in seen_indirect:
                continue
            seen_indirect.add(identity)
        elif isinstance(value, (DictionaryObject, ArrayObject)):
            direct_identity = id(value)
            if direct_identity in seen_direct:
                continue
            seen_direct.add(direct_identity)
        facts.object_count += 1
        if facts.object_count > limits.max_objects:
            facts.object_count = limits.max_objects + 1
            facts.reject(
                "OBJECT_COUNT_LIMIT_EXCEEDED",
                "reachable nodes exceed the object-count limit",
            )
            break

        if from_indirect:
            try:
                value = value.get_object()
            except Exception:
                facts.reject(
                    "PDF_OBJECT_RESOLUTION_ERROR",
                    "reachable PDF object cannot be resolved",
                )
                continue
            if value is None:
                facts.reject(
                    "PDF_OBJECT_RESOLUTION_ERROR",
                    "reachable PDF object resolves to null",
                )
                continue
            if isinstance(value, (DictionaryObject, ArrayObject)):
                seen_direct.add(id(value))

        if isinstance(value, DictionaryObject):
            try:
                _count_inventory(value, facts)
            except Exception:
                facts.reject(
                    "PDF_OBJECT_RESOLUTION_ERROR",
                    "active-content inventory cannot be resolved",
                )
            if isinstance(value, StreamObject):
                filters = _filters(value, facts)
                try:
                    is_image = str(_resolved(value.get("/Subtype"))) == "/Image"
                except Exception:
                    is_image = False
                    facts.reject(
                        "PDF_OBJECT_RESOLUTION_ERROR",
                        "stream metadata cannot be resolved",
                    )
                if is_image:
                    try:
                        estimate = _image_size(value, facts)
                    except Exception:
                        estimate = None
                        facts.reject(
                            "PDF_OBJECT_RESOLUTION_ERROR",
                            "image metadata cannot be resolved",
                        )
                    if estimate is not None:
                        _add_image_bytes(estimate, facts, limits)
                if filters is not None and facts.decoded_stream_bytes <= (
                    limits.max_decoded_stream_bytes
                ):
                    size = _stream_size(
                        value,
                        filters,
                        remaining=(
                            limits.max_decoded_stream_bytes - facts.decoded_stream_bytes
                        ),
                        facts=facts,
                    )
                    if size is not None:
                        facts.decoded_stream_bytes += size
                        if facts.decoded_stream_bytes > (
                            limits.max_decoded_stream_bytes
                        ):
                            facts.decoded_stream_bytes = (
                                limits.max_decoded_stream_bytes + 1
                            )
                            facts.reject(
                                "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
                                "decoded stream bytes exceed the limit",
                            )
            children = value.values()
        elif isinstance(value, ArrayObject):
            children = value
        else:
            continue
        if facts.object_count + len(stack) + len(children) > limits.max_objects:
            facts.object_count = limits.max_objects + 1
            facts.reject(
                "OBJECT_COUNT_LIMIT_EXCEEDED",
                "reachable nodes exceed the object-count limit",
            )
            break
        stack.extend((child, depth + 1) for child in reversed(children))

    if facts.errors:
        return facts
    for page in pages:
        _scan_page_inline_images(page, reader, facts, limits)
        if facts.errors:
            break
    return facts
