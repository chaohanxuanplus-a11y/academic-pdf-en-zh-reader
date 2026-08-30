# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Streaming full-page PDFium raster QA at the fixed 144 DPI policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw

from academic_pdf_en_zh_reader.rendering.page_geometry import A4_WIDTH_MPT
from academic_pdf_en_zh_reader.rendering.vector_compose import (
    compose_source_pages_to_a3,
)


class RasterQaError(ValueError):
    """A stable full-page raster QA failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RasterPolicy:
    """Small fixed raster thresholds; no page sampling is permitted."""

    version: int = 1
    dpi: int = 144
    max_pages: int = 2_000
    max_pixels_per_page: int = 12_000_000
    pixel_delta_threshold: int = 12
    max_unmasked_changed_ppm: int = 250
    leader_mask_padding_px: int = 3
    minimum_nonwhite_ppm: int = 10
    maximum_black_ppm: int = 750_000
    maximum_edge_ink_ppm: int = 200_000

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(type(value) is not int for value in values.values()):
            raise RasterQaError("RASTER_POLICY_INVALID")
        if (
            self.version != 1
            or self.dpi != 144
            or self.max_pages < 1
            or self.max_pixels_per_page < 1
            or not 0 <= self.pixel_delta_threshold <= 255
            or not 0 <= self.max_unmasked_changed_ppm <= 1_000_000
            or self.leader_mask_padding_px < 0
            or not 0 <= self.minimum_nonwhite_ppm <= 1_000_000
            or not 0 <= self.maximum_black_ppm <= 1_000_000
            or not 0 <= self.maximum_edge_ink_ppm <= 1_000_000
        ):
            raise RasterQaError("RASTER_POLICY_INVALID")


@dataclass(frozen=True, slots=True)
class RasterAudit:
    page_count: int
    rasterized_page_count: int
    left_equivalent: bool
    pages_sane: bool
    changed_pixel_count: int


DEFAULT_RASTER_POLICY = RasterPolicy()


def _render_page(document: object, index: int, *, scale: float) -> Image.Image:
    page = None
    bitmap = None
    try:
        page = document[index]  # type: ignore[index]
        bitmap = page.render(scale=scale)
        return bitmap.to_pil().convert("RGB")
    except Exception as exc:
        raise RasterQaError("RASTER_PDFIUM_FAILED") from exc
    finally:
        if bitmap is not None:
            bitmap.close()
        if page is not None:
            page.close()


def _point_to_pixel(
    point: object, *, scale: float, image_height: int
) -> tuple[int, int]:
    if (
        not isinstance(point, list)
        or len(point) != 2
        or any(type(value) is not int for value in point)
    ):
        raise RasterQaError("RASTER_LEADER_MASK_INVALID")
    return (
        round(point[0] * scale / 1000),
        image_height - round(point[1] * scale / 1000),
    )


def _leader_mask(
    size: tuple[int, int],
    plan_page: Mapping[str, object],
    *,
    scale: float,
    padding: int,
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    routes = plan_page.get("leader_routes")
    if not isinstance(routes, list):
        raise RasterQaError("RASTER_LEADER_MASK_INVALID")
    for route in routes:
        try:
            points = [
                _point_to_pixel(point, scale=scale, image_height=size[1])
                for point in route["points_mpt"]
            ]
            width = max(
                1,
                round(int(route["width_mpt"]) * scale / 1000) + 2 * padding,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RasterQaError("RASTER_LEADER_MASK_INVALID") from exc
        # Crop coordinates naturally clip the route to the left A4 half. The
        # mask follows only the frozen stroke path, not a broad rectangle.
        draw.line(points, fill=255, width=width, joint="curve")
        radius = width // 2
        for x, y in points:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    return mask


def _changed_outside_mask(
    source: Image.Image,
    output_left: Image.Image,
    mask: Image.Image,
    *,
    threshold: int,
) -> int:
    difference = ImageChops.difference(source, output_left).convert("L")
    changed = difference.point(lambda value: 255 if value > threshold else 0)
    outside = ImageChops.subtract(changed, mask)
    return outside.histogram()[255]


def _page_sane(image: Image.Image, policy: RasterPolicy) -> bool:
    grayscale = image.convert("L")
    histogram = grayscale.histogram()
    pixels = image.width * image.height
    nonwhite = sum(histogram[:250])
    black = sum(histogram[:16])
    nonwhite_ppm = nonwhite * 1_000_000 // pixels
    black_ppm = black * 1_000_000 // pixels
    edge_width = min(2, image.width // 2, image.height // 2)
    if edge_width <= 0:
        return False
    edges = Image.new("L", (image.width * 2 + image.height * 2, edge_width), 255)
    cursor = 0
    strips = (
        grayscale.crop((0, 0, image.width, edge_width)),
        grayscale.crop((0, image.height - edge_width, image.width, image.height)),
        grayscale.crop((0, 0, edge_width, image.height)).rotate(90, expand=True),
        grayscale.crop((image.width - edge_width, 0, image.width, image.height)).rotate(
            90, expand=True
        ),
    )
    for strip in strips:
        edges.paste(strip, (cursor, 0))
        cursor += strip.width
    edge_histogram = edges.crop((0, 0, cursor, edge_width)).histogram()
    edge_pixels = cursor * edge_width
    edge_ink_ppm = sum(edge_histogram[:250]) * 1_000_000 // edge_pixels
    return (
        nonwhite_ppm >= policy.minimum_nonwhite_ppm
        and black_ppm <= policy.maximum_black_ppm
        and edge_ink_ppm <= policy.maximum_edge_ink_ppm
    )


def audit_full_page_rasters(
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    render_manifest: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    *,
    policy: RasterPolicy = DEFAULT_RASTER_POLICY,
) -> RasterAudit:
    """Render every output/source page pair once and release it before continuing."""

    if not isinstance(policy, RasterPolicy):
        raise RasterQaError("RASTER_POLICY_INVALID")
    source_document = None
    output_document = None
    try:
        normalized_source = BytesIO()
        compose_source_pages_to_a3(source_pdf_path, normalized_source)
        source_document = pdfium.PdfDocument(normalized_source.getvalue())
        output_document = pdfium.PdfDocument(str(Path(output_pdf_path)))
        mappings = render_manifest.get("pages")
        plan_pages = overlay_plan.get("pages")
        if (
            not isinstance(mappings, list)
            or not isinstance(plan_pages, list)
            or not 1 <= len(mappings) <= policy.max_pages
            or len(mappings) != len(plan_pages)
            or len(output_document) != len(mappings)
            or len(source_document) < 1
        ):
            raise RasterQaError("RASTER_PAGE_BINDING_INVALID")
        scale = policy.dpi / 72
        rasterized = 0
        changed_total = 0
        equivalent = True
        sane = True
        for output_index, (mapping, plan_page) in enumerate(
            zip(mappings, plan_pages, strict=True)
        ):
            source_index = int(mapping["source_page_number"]) - 1
            if not 0 <= source_index < len(source_document):
                raise RasterQaError("RASTER_PAGE_BINDING_INVALID")
            output_image = _render_page(output_document, output_index, scale=scale)
            source_image = _render_page(source_document, source_index, scale=scale)
            try:
                if (
                    output_image.width * output_image.height
                    > policy.max_pixels_per_page
                    or source_image.width * source_image.height
                    > policy.max_pixels_per_page
                ):
                    raise RasterQaError("RASTER_PIXEL_LIMIT")
                expected_left_width = round(A4_WIDTH_MPT * scale / 1000)
                if output_image.size != source_image.size or (
                    output_image.width < expected_left_width
                ):
                    equivalent = False
                else:
                    output_left = output_image.crop(
                        (0, 0, expected_left_width, output_image.height)
                    )
                    source_left = source_image.crop(
                        (0, 0, expected_left_width, source_image.height)
                    )
                    try:
                        mask = _leader_mask(
                            output_left.size,
                            plan_page,
                            scale=scale,
                            padding=policy.leader_mask_padding_px,
                        )
                        changed = _changed_outside_mask(
                            source_left,
                            output_left,
                            mask,
                            threshold=policy.pixel_delta_threshold,
                        )
                        changed_total += changed
                        if (
                            changed * 1_000_000
                            > source_left.width
                            * source_left.height
                            * policy.max_unmasked_changed_ppm
                        ):
                            equivalent = False
                    finally:
                        output_left.close()
                        source_left.close()
                if not _page_sane(output_image, policy):
                    sane = False
                rasterized += 1
            finally:
                output_image.close()
                source_image.close()
        return RasterAudit(
            page_count=len(mappings),
            rasterized_page_count=rasterized,
            left_equivalent=equivalent,
            pages_sane=sane,
            changed_pixel_count=changed_total,
        )
    except RasterQaError:
        raise
    except Exception as exc:
        raise RasterQaError("RASTER_PDFIUM_FAILED") from exc
    finally:
        if output_document is not None:
            output_document.close()
        if source_document is not None:
            source_document.close()


__all__ = [
    "DEFAULT_RASTER_POLICY",
    "RasterAudit",
    "RasterPolicy",
    "RasterQaError",
    "audit_full_page_rasters",
]
