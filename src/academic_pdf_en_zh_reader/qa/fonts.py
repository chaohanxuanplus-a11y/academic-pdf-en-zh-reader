# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Embedded-font, ToUnicode, draw binding, and glyph coverage gates."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import DictionaryObject, IndirectObject

from academic_pdf_en_zh_reader.job.hashing import sha256_bytes
from academic_pdf_en_zh_reader.typography.font_registry import (
    DEFAULT_FONT_MANIFEST,
    EXPECTED_FONT_IDENTITIES,
    FontRegistryError,
    load_font_registry,
)


class FontQaError(ValueError):
    """A stable font QA failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _resolved(value: object) -> object:
    return value.get_object() if isinstance(value, IndirectObject) else value


def _font_dictionary(page: object) -> DictionaryObject:
    try:
        resources = _resolved(page["/Resources"])  # type: ignore[index]
        fonts = _resolved(resources["/Font"])  # type: ignore[index]
    except Exception as exc:
        raise FontQaError("FONT_EMBEDDING_INVALID") from exc
    if not isinstance(fonts, DictionaryObject):
        raise FontQaError("FONT_EMBEDDING_INVALID")
    return fonts


def _base_font(font: DictionaryObject) -> str:
    value = str(_resolved(font.get("/BaseFont"))).removeprefix("/")
    if "+" in value and len(value.split("+", 1)[0]) == 6:
        value = value.split("+", 1)[1]
    return value


def _font_role(font: DictionaryObject) -> str | None:
    base = _base_font(font)
    for role, identity in EXPECTED_FONT_IDENTITIES.items():
        if base == identity[2]:
            return role
    return None


def _descriptor(font: DictionaryObject) -> DictionaryObject | None:
    descendant = font
    if str(_resolved(font.get("/Subtype"))) == "/Type0":
        descendants = _resolved(font.get("/DescendantFonts"))
        try:
            descendant = _resolved(descendants[0])  # type: ignore[index]
        except Exception:
            return None
    value = _resolved(descendant.get("/FontDescriptor"))
    return value if isinstance(value, DictionaryObject) else None


def _is_embedded(font: DictionaryObject) -> bool:
    descriptor = _descriptor(font)
    return descriptor is not None and any(
        descriptor.get(key) is not None
        for key in ("/FontFile", "/FontFile2", "/FontFile3")
    )


def _valid_tounicode(font: DictionaryObject) -> bool:
    stream = _resolved(font.get("/ToUnicode"))
    try:
        data = stream.get_data()  # type: ignore[attr-defined]
    except Exception:
        return False
    return (
        isinstance(data, bytes)
        and b"begincmap" in data
        and (b"beginbfchar" in data or b"beginbfrange" in data)
        and b"endcmap" in data
    )


def _used_roles(render_manifest: Mapping[str, object]) -> set[str]:
    try:
        return {str(item["font_role"]) for item in render_manifest["font_usages"]}  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise FontQaError("FONT_EMBEDDING_INVALID") from exc


def validate_embedded_fonts(
    reader: PdfReader, render_manifest: Mapping[str, object]
) -> dict[str, int]:
    """Require each used approved translation font to be embedded and mapped."""

    used = _used_roles(render_manifest)
    found: set[str] = set()
    object_ids: set[tuple[int, int] | int] = set()
    for page in reader.pages:
        for reference in _font_dictionary(page).values():
            identity: tuple[int, int] | int
            if isinstance(reference, IndirectObject):
                identity = (reference.idnum, reference.generation)
            else:
                identity = id(reference)
            if identity in object_ids:
                continue
            object_ids.add(identity)
            font = _resolved(reference)
            if not isinstance(font, DictionaryObject):
                raise FontQaError("FONT_EMBEDDING_INVALID")
            role = _font_role(font)
            if role is None:
                continue
            if not _is_embedded(font) or not _valid_tounicode(font):
                raise FontQaError("FONT_EMBEDDING_INVALID")
            found.add(role)
    if not used or not used <= found:
        raise FontQaError("FONT_EMBEDDING_INVALID")
    return {"font_count": len(found)}


def _planned_bindings(
    overlay_plan: Mapping[str, object],
) -> list[list[tuple[str, int]]]:
    try:
        return [
            [(str(run["font_role"]), int(run["size_mpt"])) for run in page["draw_runs"]]
            for page in overlay_plan["pages"]  # type: ignore[index]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise FontQaError("FONT_DRAW_BINDING_INVALID") from exc


def _append_text_object_binding(
    observed: list[tuple[str, int] | None],
    bindings: set[tuple[str, int]],
    *,
    has_text: bool,
    has_unmapped_text: bool,
) -> None:
    if not has_text:
        return
    observed.append(
        next(iter(bindings)) if not has_unmapped_text and len(bindings) == 1 else None
    )


def validate_draw_run_fonts(
    reader: PdfReader,
    render_manifest: Mapping[str, object],
    overlay_plan: Mapping[str, object],
) -> dict[str, int]:
    """Match every planned draw run to the actual PDF font resource and size."""

    planned_pages = _planned_bindings(overlay_plan)
    if len(reader.pages) != len(planned_pages):
        raise FontQaError("FONT_DRAW_BINDING_INVALID")
    manifest_names = {
        str(item["role"]): str(item["reportlab_name"])
        for item in render_manifest.get("font_fingerprint", [])
    }
    if set(manifest_names) != {"body", "heading", "symbols"}:
        raise FontQaError("FONT_DRAW_BINDING_INVALID")
    total = 0
    for page, planned, plan_page in zip(
        reader.pages, planned_pages, overlay_plan["pages"], strict=True
    ):
        resources = _font_dictionary(page)
        roles_by_resource: dict[str, str] = {}
        for name, reference in resources.items():
            font = _resolved(reference)
            if isinstance(font, DictionaryObject) and (role := _font_role(font)):
                roles_by_resource[str(name)] = role
        current: tuple[str, int] | None = None
        observed: list[tuple[str, int] | None] = []
        in_text_object = False
        object_bindings: set[tuple[str, int]] = set()
        object_has_text = False
        object_has_unmapped_text = False

        try:
            operations = page.get_contents().operations
        except Exception as exc:
            raise FontQaError("FONT_DRAW_BINDING_INVALID") from exc
        for operands, operator in operations:
            if operator == b"BT":
                if in_text_object:
                    _append_text_object_binding(
                        observed,
                        object_bindings,
                        has_text=object_has_text,
                        has_unmapped_text=object_has_unmapped_text,
                    )
                in_text_object = True
                current = None
                object_bindings.clear()
                object_has_text = False
                object_has_unmapped_text = False
            elif operator == b"ET":
                if in_text_object:
                    _append_text_object_binding(
                        observed,
                        object_bindings,
                        has_text=object_has_text,
                        has_unmapped_text=object_has_unmapped_text,
                    )
                in_text_object = False
                current = None
                object_bindings.clear()
                object_has_text = False
                object_has_unmapped_text = False
            elif operator == b"Tf":
                resource = str(operands[0])
                role = roles_by_resource.get(resource)
                current = (
                    None if role is None else (role, round(float(operands[1]) * 1000))
                )
            elif operator in {b"Tj", b"TJ", b"'", b'"'}:
                if not in_text_object:
                    observed.append(None)
                    continue
                object_has_text = True
                if current is None:
                    object_has_unmapped_text = True
                else:
                    object_bindings.add(current)
        if in_text_object:
            _append_text_object_binding(
                observed,
                object_bindings,
                has_text=object_has_text,
                has_unmapped_text=object_has_unmapped_text,
            )
        # Source content can legitimately use the same public font. Composition
        # appends the frozen overlay, so the exact overlay sequence is the suffix.
        if plan_page.get("brand_block") is not None:
            expected_brand = "".join(
                str(run["text"])
                for run in plan_page["draw_runs"]
                if run["content_kind"] == "brand"
            )
            try:
                actual_text = page.extract_text()
            except Exception as exc:
                raise FontQaError("FONT_DRAW_BINDING_INVALID") from exc
            if not expected_brand or not "".join(actual_text.split()).endswith(
                "".join(expected_brand.split())
            ):
                raise FontQaError("FONT_DRAW_BINDING_INVALID")
        if planned and (
            len(observed) < len(planned) or observed[-len(planned) :] != planned
        ):
            raise FontQaError("FONT_DRAW_BINDING_INVALID")
        total += len(planned)
    if total != sum(
        int(item["draw_run_count"]) for item in render_manifest.get("font_usages", [])
    ):
        raise FontQaError("FONT_DRAW_BINDING_INVALID")
    return {"draw_run_count": total}


def validate_glyph_coverage(
    render_manifest: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    *,
    font_manifest_path: str | Path = DEFAULT_FONT_MANIFEST,
) -> dict[str, int]:
    """Verify exact pinned files and every planned Unicode code point."""

    manifest_path = Path(font_manifest_path)
    try:
        registry = load_font_registry(manifest_path)
        manifest_hash = sha256_bytes(manifest_path.read_bytes())
    except (FontRegistryError, OSError, ValueError) as exc:
        raise FontQaError("FONT_GLYPH_COVERAGE_INVALID") from exc
    fingerprint = [
        {
            "role": face.role,
            "reportlab_name": face.reportlab_name,
            "sha256": face.sha256,
        }
        for face in registry.faces
    ]
    if (
        render_manifest.get("font_manifest_hash") != manifest_hash
        or render_manifest.get("font_fingerprint") != fingerprint
        or overlay_plan.get("font_fingerprint") != fingerprint
    ):
        raise FontQaError("FONT_GLYPH_COVERAGE_INVALID")
    characters = 0
    try:
        for page in overlay_plan["pages"]:  # type: ignore[index]
            for run in page["draw_runs"]:
                face = registry.face(str(run["font_role"]))
                if run["font_name"] != face.reportlab_name or any(
                    not face.covers(ord(character)) for character in str(run["text"])
                ):
                    raise FontQaError("FONT_GLYPH_COVERAGE_INVALID")
                characters += len(str(run["text"]))
    except FontQaError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise FontQaError("FONT_GLYPH_COVERAGE_INVALID") from exc
    if characters <= 0:
        raise FontQaError("FONT_GLYPH_COVERAGE_INVALID")
    return {"character_count": characters}


__all__ = [
    "FontQaError",
    "validate_draw_run_fonts",
    "validate_embedded_fonts",
    "validate_glyph_coverage",
]
