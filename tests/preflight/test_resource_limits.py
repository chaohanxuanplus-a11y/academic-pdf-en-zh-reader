# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import zlib
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)

from academic_pdf_en_zh_reader.preflight.checks import (
    PreflightLimits,
    preflight_safe_copy,
)
from scripts.generate_synthetic_fixtures import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "tests" / "fixtures-synthetic" / "specs"


def _fixture(tmp_path: Path, fixture_id: str = "single-column") -> Path:
    output = tmp_path / f"{fixture_id}.pdf"
    generate_fixture(SPECS / f"{fixture_id}.json", output)
    return output


@pytest.mark.parametrize(
    ("override", "error_code", "limit_key"),
    [
        ({"max_file_bytes": 1}, "FILE_SIZE_LIMIT_EXCEEDED", "file_bytes"),
        ({"max_pages": 1}, "PAGE_COUNT_LIMIT_EXCEEDED", "page_count"),
        ({"max_objects": 1}, "OBJECT_COUNT_LIMIT_EXCEEDED", "object_count"),
        (
            {"max_recursion_depth": 1},
            "RECURSION_DEPTH_LIMIT_EXCEEDED",
            "recursion_depth",
        ),
        (
            {"max_decoded_stream_bytes": 32},
            "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
            "decompressed_stream_bytes",
        ),
    ],
)
def test_small_limits_stop_on_real_fixture_without_a_bomb(
    tmp_path: Path, override: dict[str, int], error_code: str, limit_key: str
) -> None:
    fixture_id = "cross-page-paragraph" if "max_pages" in override else "single-column"
    limits = PreflightLimits(**override)

    result = preflight_safe_copy(_fixture(tmp_path, fixture_id), limits=limits)

    assert result["passed"] is False
    assert error_code in result["error_codes"]
    assert (
        result["limits"][limit_key]["observed"] > result["limits"][limit_key]["maximum"]
    )


def _with_image(
    source: Path,
    output: Path,
    *,
    filter_name: str = "/FlateDecode",
    color_space: object = NameObject("/DeviceRGB"),
) -> Path:
    reader = PdfReader(source, strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    image = StreamObject()
    payload = b"tiny-test-payload"
    if filter_name == "/FlateDecode":
        payload = zlib.compress(payload)
    image.set_data(payload)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(100),
            NameObject("/Height"): NumberObject(100),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/ColorSpace"): color_space,
            NameObject("/Filter"): NameObject(filter_name),
        }
    )
    reference = writer._add_object(image)
    resources = writer.pages[0]["/Resources"].get_object()
    xobjects = resources.get("/XObject")
    if xobjects is None:
        xobjects = DictionaryObject()
        resources[NameObject("/XObject")] = xobjects
    else:
        xobjects = xobjects.get_object()
    xobjects[NameObject("/ImPreflightProbe")] = reference
    with output.open("wb") as stream:
        writer.write(stream)
    return output


def test_image_estimate_limit_is_dimension_based_not_payload_based(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    image_pdf = _with_image(source, tmp_path / "image.pdf")
    limits = PreflightLimits(max_image_bytes=10_000)

    result = preflight_safe_copy(image_pdf, limits=limits)

    assert "IMAGE_BYTES_LIMIT_EXCEEDED" in result["error_codes"]
    assert result["limits"]["image_bytes"]["observed"] == 30_000


@pytest.mark.parametrize(
    ("filter_name", "color_space", "error_code"),
    [
        ("/JBIG2Decode", NameObject("/DeviceGray"), "UNSUPPORTED_JBIG2"),
        ("/UnknownDecode", NameObject("/DeviceRGB"), "UNSUPPORTED_PDF_FILTER"),
        ("/FlateDecode", NameObject("/BrokenSpace"), "MALFORMED_COLOR_SPACE"),
    ],
)
def test_unsupported_image_encoding_fails_closed_without_decoding(
    tmp_path: Path, filter_name: str, color_space: object, error_code: str
) -> None:
    source = _fixture(tmp_path)
    image_pdf = _with_image(
        source,
        tmp_path / f"unsafe-{error_code}.pdf",
        filter_name=filter_name,
        color_space=color_space,
    )

    result = preflight_safe_copy(image_pdf)

    assert result["passed"] is False
    assert error_code in result["error_codes"]


def _with_inline_image(source: Path, output: Path, body: bytes) -> Path:
    reader = PdfReader(source, strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    content = DecodedStreamObject()
    content.set_data(body)
    reference = writer._add_object(content)
    existing = writer.pages[0]["/Contents"]
    writer.pages[0][NameObject("/Contents")] = ArrayObject([existing, reference])
    with output.open("wb") as stream:
        writer.write(stream)
    return output


def test_inline_image_uses_scanline_rounding_and_counts_toward_image_limit(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    inline = _with_inline_image(
        source,
        tmp_path / "inline.pdf",
        b"q\nBI /W 3 /H 2 /BPC 1 /CS /G ID\n\x00\x00\nEI\nQ\n",
    )

    rejected = preflight_safe_copy(inline, limits=PreflightLimits(max_image_bytes=1))
    accepted = preflight_safe_copy(inline, limits=PreflightLimits(max_image_bytes=2))

    assert "IMAGE_BYTES_LIMIT_EXCEEDED" in rejected["error_codes"]
    assert rejected["limits"]["image_bytes"]["observed"] == 2
    assert accepted["passed"] is True
    assert accepted["limits"]["image_bytes"]["observed"] == 2


@pytest.mark.parametrize(
    ("filter_name", "error_code"),
    [
        (b"/SECRETDecode", "UNSUPPORTED_PDF_FILTER"),
        (b"/JBIG2Decode", "UNSUPPORTED_JBIG2"),
    ],
)
def test_inline_image_filters_fail_closed_without_echoing_control_names(
    tmp_path: Path, filter_name: bytes, error_code: str
) -> None:
    source = _fixture(tmp_path)
    inline = _with_inline_image(
        source,
        tmp_path / "inline-filter.pdf",
        b"BI /W 1 /H 1 /BPC 8 /CS /G /F " + filter_name + b" ID\nx\nEI\n",
    )

    result = preflight_safe_copy(inline)

    assert error_code in result["error_codes"]
    assert filter_name.decode() not in str(result)


@pytest.mark.parametrize("filter_name", [b"/JPXDecode", b"/CCITTFaxDecode"])
def test_ambiguous_inline_image_filters_fail_closed(
    tmp_path: Path, filter_name: bytes
) -> None:
    source = _fixture(tmp_path)
    inline = _with_inline_image(
        source,
        tmp_path / "inline-ambiguous-filter.pdf",
        b"BI /W 1 /H 1 /BPC 8 /CS /G /F " + filter_name + b" ID\n\xff\xd9\nEI\n",
    )

    result = preflight_safe_copy(inline)

    assert result["passed"] is False
    assert "UNSUPPORTED_PDF_FILTER" in result["error_codes"]


def test_ambiguous_inline_filter_chain_fails_closed(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    inline = _with_inline_image(
        source,
        tmp_path / "inline-filter-chain.pdf",
        b"BI /W 1 /H 1 /BPC 8 /CS /G /F [/ASCII85Decode /CCITTFaxDecode] ID\n~>\nEI\n",
    )

    result = preflight_safe_copy(inline)

    assert result["passed"] is False
    assert "UNSUPPORTED_PDF_FILTER" in result["error_codes"]


@pytest.mark.parametrize("progressive", [False, True])
def test_inline_jpeg_ignores_a_false_pdf_ei_sequence_inside_valid_payload(
    tmp_path: Path, progressive: bool
) -> None:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (1, 1), (64, 128, 192)).save(
        buffer, format="JPEG", progressive=progressive
    )
    jpeg = buffer.getvalue()
    comment = b"prefix\xff\xd9\nEI\n\x01\x02\x03suffix"
    comment_segment = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
    jpeg_with_false_ei = jpeg[:2] + comment_segment + jpeg[2:]
    Image.open(BytesIO(jpeg_with_false_ei)).verify()

    source = _fixture(tmp_path)
    inline = _with_inline_image(
        source,
        tmp_path / "inline-jpeg.pdf",
        b"q\nBI /W 1 /H 1 /BPC 8 /CS /RGB /F /DCTDecode ID\n"
        + jpeg_with_false_ei
        + b"\nEI\nQ\n",
    )

    result = preflight_safe_copy(inline, limits=PreflightLimits(max_image_bytes=3))

    assert result["passed"] is True
    assert result["limits"]["image_bytes"]["observed"] == 3


def test_inline_flate_output_counts_toward_document_decode_limit() -> None:
    from academic_pdf_en_zh_reader.preflight import pdf_catalog

    facts = pdf_catalog.CatalogFacts(decoded_stream_bytes=32)
    limits = pdf_catalog.CatalogLimits(
        max_objects=10,
        max_recursion_depth=10,
        max_decoded_stream_bytes=96,
        max_image_bytes=10,
    )
    stream = BytesIO(zlib.compress(b"x" * 256) + b"\nEI\n")

    completed = pdf_catalog._seek_flate_inline_end(stream, facts, limits)

    assert completed is False
    assert facts.decoded_stream_bytes == 97
    assert facts.errors == [
        (
            "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
            "decoded inline image data exceed the limit",
        )
    ]


def test_inline_flate_bomb_is_rejected_by_public_preflight(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    empty = _with_inline_image(
        source,
        tmp_path / "inline-flate-empty.pdf",
        b"BI /W 1 /H 1 /BPC 8 /CS /G /F /FlateDecode ID\n"
        + zlib.compress(b"")
        + b"\nEI\n",
    )
    bomb = _with_inline_image(
        source,
        tmp_path / "inline-flate-bomb.pdf",
        b"BI /W 1 /H 1 /BPC 8 /CS /G /F /FlateDecode ID\n"
        + zlib.compress(b"x" * 4096)
        + b"\nEI\n",
    )
    baseline = preflight_safe_copy(empty)
    maximum = baseline["limits"]["decompressed_stream_bytes"]["observed"] + 1024

    result = preflight_safe_copy(
        bomb,
        limits=PreflightLimits(max_decoded_stream_bytes=int(maximum)),
    )

    assert "DECOMPRESSED_STREAM_LIMIT_EXCEEDED" in result["error_codes"]
    assert result["limits"]["decompressed_stream_bytes"] == {
        "observed": maximum + 1,
        "maximum": maximum,
    }


def test_inline_run_length_parser_skips_literal_eod_bytes_and_counts_output() -> None:
    from academic_pdf_en_zh_reader.preflight import pdf_catalog

    facts = pdf_catalog.CatalogFacts(decoded_stream_bytes=10)
    limits = pdf_catalog.CatalogLimits(
        max_objects=10,
        max_recursion_depth=10,
        max_decoded_stream_bytes=100,
        max_image_bytes=10,
    )
    stream = BytesIO(b"\x04A\x80\nEI\x80\nEI\nQ")

    completed = pdf_catalog._seek_run_length_inline_end(stream, facts, limits)

    assert completed is True
    assert facts.decoded_stream_bytes == 15
    assert stream.read() == b"\nQ"


def test_inline_run_length_output_limit_fails_closed() -> None:
    from academic_pdf_en_zh_reader.preflight import pdf_catalog

    facts = pdf_catalog.CatalogFacts(decoded_stream_bytes=4)
    limits = pdf_catalog.CatalogLimits(
        max_objects=10,
        max_recursion_depth=10,
        max_decoded_stream_bytes=64,
        max_image_bytes=10,
    )
    stream = BytesIO(b"\x81x\x80\nEI\n")

    completed = pdf_catalog._seek_run_length_inline_end(stream, facts, limits)

    assert completed is False
    assert facts.decoded_stream_bytes == 65
    assert facts.errors == [
        (
            "DECOMPRESSED_STREAM_LIMIT_EXCEEDED",
            "decoded inline image data exceed the limit",
        )
    ]


def test_recursive_color_spaces_reject_unknown_alternates(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    alternate = ArrayObject(
        [
            NameObject("/Separation"),
            NameObject("/Spot"),
            NameObject("/UnknownAlternate"),
            DictionaryObject(),
        ]
    )
    indexed = ArrayObject(
        [NameObject("/Indexed"), alternate, NumberObject(1), NameObject("/Lookup")]
    )
    image_pdf = _with_image(
        source,
        tmp_path / "recursive-colorspace.pdf",
        color_space=indexed,
    )

    result = preflight_safe_copy(image_pdf)

    assert "MALFORMED_COLOR_SPACE" in result["error_codes"]


@pytest.mark.parametrize(
    "color_space",
    [
        ArrayObject(
            [
                TextStringObject("Indexed"),
                NameObject("/DeviceRGB"),
                NumberObject(1),
                TextStringObject("lookup"),
            ]
        ),
        ArrayObject(
            [
                NameObject("/DeviceN"),
                ArrayObject([NameObject("/Cyan"), NameObject("/Spot")]),
                NameObject("/UnknownAlternate"),
                DictionaryObject(),
            ]
        ),
        ArrayObject(
            [
                NameObject("/ICCBased"),
                DictionaryObject({NameObject("/N"): NumberObject(3)}),
            ]
        ),
    ],
)
def test_complex_color_space_families_fail_closed_on_malformed_members(
    tmp_path: Path, color_space: object
) -> None:
    source = _fixture(tmp_path)
    image_pdf = _with_image(
        source,
        tmp_path / "malformed-complex-colorspace.pdf",
        color_space=color_space,
    )

    result = preflight_safe_copy(image_pdf)

    assert "MALFORMED_COLOR_SPACE" in result["error_codes"]


def test_direct_object_fanout_is_covered_before_stack_extension(
    tmp_path: Path,
) -> None:
    source = _fixture(tmp_path)
    baseline = preflight_safe_copy(source)
    baseline_nodes = baseline["limits"]["object_count"]["observed"]
    reader = PdfReader(source, strict=True)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.root_object[NameObject("/DirectFanout")] = ArrayObject(
        [
            DictionaryObject({NameObject("/N"): NumberObject(index)})
            for index in range(64)
        ]
    )
    output = tmp_path / "direct-fanout.pdf"
    with output.open("wb") as stream:
        writer.write(stream)

    result = preflight_safe_copy(
        output,
        limits=PreflightLimits(max_objects=int(baseline_nodes) + 8),
    )

    assert "OBJECT_COUNT_LIMIT_EXCEEDED" in result["error_codes"]
    assert (
        result["limits"]["object_count"]["observed"]
        > result["limits"]["object_count"]["maximum"]
    )


def test_flate_counter_never_requests_a_large_output_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from academic_pdf_en_zh_reader.preflight import pdf_catalog

    real_factory = zlib.decompressobj
    requested: list[int] = []

    class Proxy:
        def __init__(self):
            self._inner = real_factory()

        @property
        def eof(self):
            return self._inner.eof

        @property
        def unconsumed_tail(self):
            return self._inner.unconsumed_tail

        def decompress(self, data, max_length=0):
            requested.append(max_length)
            return self._inner.decompress(data, max_length)

    monkeypatch.setattr(pdf_catalog.zlib, "decompressobj", Proxy)

    size, complete = pdf_catalog._bounded_flate_size(
        zlib.compress(b"x" * (1024 * 1024)), 2 * 1024 * 1024
    )

    assert complete is True
    assert size == 1024 * 1024
    assert requested
    assert max(requested) <= 64 * 1024
