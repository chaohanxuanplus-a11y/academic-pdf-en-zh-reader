# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    load_schema,
    validate_artifact,
)
from academic_pdf_en_zh_reader.topology.contracts import (
    TopologyContractError,
    TopologyOutcome,
    TopologyStatus,
)

SHA256 = "a" * 64
NORMALIZED_SHA256 = "b" * 64

TRANSLATABLE_ROLES = {
    "title",
    "abstract",
    "keywords",
    "heading",
    "body",
    "figure-caption",
    "table-caption",
}
EXCLUDED_ROLES = {
    "author",
    "affiliation",
    "bibliographic-metadata",
    "header",
    "footer",
    "page-number",
    "watermark",
    "footnote",
    "endnote",
    "acknowledgements",
    "equation",
    "variable",
    "code",
    "chemical-formula",
    "pure-data",
    "reference-entry",
    "table-cell",
    "figure-text",
}


def _block(
    *,
    role: str,
    reading_order: int,
    start: int,
    end: int,
    bbox_mpt: list[int],
    policy: str,
    target_graphic_id: str | None = None,
) -> dict[str, object]:
    block: dict[str, object] = {
        "id": f"p1-r{reading_order}-{role}-{start}-{end}",
        "role": role,
        "translation_policy": policy,
        "band_id": "p1-band-0",
        "column_id": "p1-band-0-col-0",
        "reading_order": reading_order,
        "source_char_start": start,
        "source_char_end": end,
        "text": "Source text.",
        "bbox_mpt": bbox_mpt,
        "first_line_bbox_mpt": bbox_mpt.copy(),
        "confidence_ppm": 990_000,
    }
    if target_graphic_id is not None:
        block["target_graphic_id"] = target_graphic_id
    return block


def valid_source() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "artifact_kind": "source",
        "source_sha256": SHA256,
        "normalized_pdf_sha256": NORMALIZED_SHA256,
        "pages": [
            {
                "page_number": 1,
                "media_box_mpt": [0, 0, 595_276, 841_890],
                "crop_box_mpt": [0, 0, 595_276, 841_890],
                "rotation_degrees": 0,
                "bands": [
                    {
                        "id": "p1-band-0",
                        "y_top_mpt": 800_000,
                        "y_bottom_mpt": 40_000,
                        "columns": [
                            {
                                "id": "p1-band-0-col-0",
                                "x_left_mpt": 40_000,
                                "x_right_mpt": 555_276,
                            }
                        ],
                    }
                ],
                "graphic_nodes": [
                    {
                        "id": "p1-figure-1",
                        "kind": "figure",
                        "bbox_mpt": [40_000, 350_000, 555_276, 650_000],
                        "confidence_ppm": 980_000,
                    }
                ],
                "blocks": [
                    _block(
                        role="body",
                        reading_order=0,
                        start=0,
                        end=12,
                        bbox_mpt=[40_000, 700_000, 555_276, 730_000],
                        policy="required",
                    ),
                    _block(
                        role="figure-caption",
                        reading_order=1,
                        start=12,
                        end=24,
                        bbox_mpt=[40_000, 300_000, 555_276, 330_000],
                        policy="required",
                        target_graphic_id="p1-figure-1",
                    ),
                ],
            }
        ],
    }


def test_valid_source_contract_includes_topology_and_caption_target() -> None:
    validate_artifact("source", valid_source())


def test_source_font_size_is_optional_but_positive_when_present() -> None:
    source = valid_source()
    source["pages"][0]["blocks"][0]["source_font_size_mpt"] = 10_000
    validate_artifact("source", source)

    source["pages"][0]["blocks"][0]["source_font_size_mpt"] = 0
    with pytest.raises(SchemaValidationError):
        validate_artifact("source", source)


def test_role_vocabulary_is_complete_but_closed() -> None:
    role_schema = load_schema("source")["$defs"]["block"]["properties"]["role"]
    assert set(role_schema["enum"]) == TRANSLATABLE_ROLES | EXCLUDED_ROLES

    invalid = valid_source()
    invalid["pages"][0]["blocks"][0]["role"] = "made-up-role"
    with pytest.raises(SchemaValidationError):
        validate_artifact("source", invalid)


@pytest.mark.parametrize("field", ["band_id", "column_id", "translation_policy"])
def test_topology_and_translation_fields_are_required(field: str) -> None:
    invalid = valid_source()
    del invalid["pages"][0]["blocks"][0][field]
    with pytest.raises(SchemaValidationError):
        validate_artifact("source", invalid)


@pytest.mark.parametrize(
    ("role", "policy"),
    [("body", "excluded"), ("reference-entry", "required")],
)
def test_role_and_translation_policy_cannot_disagree(role: str, policy: str) -> None:
    invalid = valid_source()
    block = invalid["pages"][0]["blocks"][0]
    block["role"] = role
    block["translation_policy"] = policy
    block["id"] = f"p1-r0-{role}-0-12"
    with pytest.raises(SchemaValidationError, match="translation policy"):
        validate_artifact("source", invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    [("band_id", "missing-band"), ("column_id", "missing-column")],
)
def test_block_references_its_own_page_band_and_column(field: str, value: str) -> None:
    invalid = valid_source()
    invalid["pages"][0]["blocks"][0][field] = value
    with pytest.raises(SchemaValidationError, match="band|column"):
        validate_artifact("source", invalid)


def test_caption_target_must_exist_and_match_caption_kind() -> None:
    missing = valid_source()
    missing["pages"][0]["blocks"][1]["target_graphic_id"] = "missing"
    with pytest.raises(SchemaValidationError, match="graphic"):
        validate_artifact("source", missing)

    wrong_kind = valid_source()
    wrong_kind["pages"][0]["graphic_nodes"][0]["kind"] = "table"
    with pytest.raises(SchemaValidationError, match="caption"):
        validate_artifact("source", wrong_kind)

    absent = valid_source()
    del absent["pages"][0]["blocks"][1]["target_graphic_id"]
    with pytest.raises(SchemaValidationError, match="caption"):
        validate_artifact("source", absent)


def test_non_graphic_block_cannot_claim_a_caption_target() -> None:
    invalid = valid_source()
    invalid["pages"][0]["blocks"][0]["target_graphic_id"] = "p1-figure-1"
    with pytest.raises(SchemaValidationError, match="target_graphic_id"):
        validate_artifact("source", invalid)


def test_excluded_page_overlay_need_not_fit_the_translatable_column_frame() -> None:
    source = valid_source()
    header = source["pages"][0]["blocks"][0]
    header.update(
        {
            "id": "p1-r0-header-0-12",
            "role": "header",
            "translation_policy": "excluded",
            "bbox_mpt": [40_000, 810_000, 555_276, 820_000],
            "first_line_bbox_mpt": [40_000, 810_000, 555_276, 820_000],
        }
    )

    validate_artifact("source", source)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda source: source["pages"][0]["bands"][0].update(
            {"y_top_mpt": 40_000, "y_bottom_mpt": 800_000}
        ),
        lambda source: source["pages"][0]["bands"][0]["columns"][0].update(
            {"x_left_mpt": 555_276, "x_right_mpt": 40_000}
        ),
        lambda source: source["pages"][0]["blocks"][0].update(
            {"bbox_mpt": [20_000, 700_000, 555_276, 730_000]}
        ),
        lambda source: source["pages"][0]["blocks"][0].update(
            {"first_line_bbox_mpt": [40_000, 690_000, 555_276, 730_000]}
        ),
        lambda source: source["pages"][0].update(
            {"crop_box_mpt": [-1, 0, 595_276, 841_890]}
        ),
        lambda source: source["pages"][0]["graphic_nodes"][0].update(
            {"bbox_mpt": [40_000, 350_000, 40_000, 650_000]}
        ),
    ],
)
def test_invalid_topology_geometry_is_rejected(mutate) -> None:
    invalid = valid_source()
    mutate(invalid)
    with pytest.raises(SchemaValidationError, match="box|band|column|geometry"):
        validate_artifact("source", invalid)


def test_source_character_ranges_are_positive_and_monotonic() -> None:
    reversed_range = valid_source()
    block = reversed_range["pages"][0]["blocks"][0]
    block["source_char_end"] = 0
    block["id"] = "p1-r0-body-0-0"
    with pytest.raises(SchemaValidationError, match="source_char_end|character range"):
        validate_artifact("source", reversed_range)

    overlap = valid_source()
    block = overlap["pages"][0]["blocks"][1]
    block["source_char_start"] = 10
    block["id"] = "p1-r1-figure-caption-10-24"
    with pytest.raises(SchemaValidationError, match="character range"):
        validate_artifact("source", overlap)

    wrong_text_length = valid_source()
    wrong_text_length["pages"][0]["blocks"][0]["text"] = "Short"
    with pytest.raises(SchemaValidationError, match="text length"):
        validate_artifact("source", wrong_text_length)


def test_page_local_identifiers_are_unique() -> None:
    invalid = valid_source()
    duplicate = deepcopy(invalid["pages"][0]["graphic_nodes"][0])
    invalid["pages"][0]["graphic_nodes"].append(duplicate)
    with pytest.raises(SchemaValidationError, match="identifier"):
        validate_artifact("source", invalid)


def test_topology_outcome_is_fail_closed() -> None:
    accepted = TopologyOutcome(status=TopologyStatus.OK, source=valid_source())
    assert accepted.source is not None

    needs_review = TopologyOutcome(
        status=TopologyStatus.NEEDS_TOPOLOGY_REVIEW,
        source=None,
    )
    assert needs_review.source is None

    with pytest.raises(TopologyContractError, match="requires source"):
        TopologyOutcome(status=TopologyStatus.OK, source=None)
    with pytest.raises(TopologyContractError, match="must not carry source"):
        TopologyOutcome(
            status=TopologyStatus.NEEDS_TOPOLOGY_REVIEW,
            source=valid_source(),
        )


def test_topology_outcome_rejects_unvalidated_status_values() -> None:
    with pytest.raises(TopologyContractError, match="status"):
        TopologyOutcome(status="OK", source=valid_source())  # type: ignore[arg-type]
