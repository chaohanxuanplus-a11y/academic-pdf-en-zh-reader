# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.rendering import branding as branding_module
from academic_pdf_en_zh_reader.rendering.branding import (
    freeze_brand_block,
    load_brand_manifest,
)
from academic_pdf_en_zh_reader.rendering.contracts import (
    OverlayPlanError,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan

from .test_text_styles import build_render_fixture


def _reference_plan() -> tuple[dict[str, object], ...]:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(include_reference_page=True)
    )
    plan = build_overlay_plan(source, graph, layout, annotations)
    return source, graph, layout, plan


@pytest.mark.parametrize("empty_last_panel", [False, True])
def test_large_statement_card_is_centered_in_all_remaining_safe_space(empty_last_panel):
    *_, graph, layout = build_render_fixture(include_reference_page=empty_last_panel)
    page = layout["pages"][-1]
    margin = graph["flow_spacing"]["vertical_padding_mpt"]
    expected_top = 841_890 - margin
    if page["blocks"]:
        expected_top = min(b["bbox_mpt"][1] for b in page["blocks"]) - 3_500
    assert page["warning_region_mpt"] == [margin, expected_top]
    manifest, _, manifest_hash = load_brand_manifest()
    card, _, runs = freeze_brand_block(
        output_page_number=page["page_number"],
        start_draw_order=0,
        manifest=manifest,
        manifest_sha256=manifest_hash,
        available_y_mpt=tuple(page["warning_region_mpt"]),
    )
    left, bottom, right, top = card["bbox_mpt"]
    assert abs(bottom + top - margin - expected_top) <= 1
    assert abs(left + right - 3 * 595_276) <= 1
    logo = card["image_bbox_mpt"]
    assert logo[2] - logo[0] == logo[3] - logo[1] == 72_000
    warning = [r for r in runs if r["style_id"] == "brand:disclaimer"]
    assert warning and {r["size_mpt"] for r in warning} == {12_000}
    assert all(
        r["size_mpt"] > graph["unit_flows"][0]["style"]["size_mpt"] for r in warning
    )


def test_brand_card_uses_only_the_final_output_page_when_earlier_pages_fit() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(
            include_reference_page=True,
            include_following_reference_page=True,
        )
    )
    frozen_layout = deepcopy(layout)

    plan = build_overlay_plan(source, graph, layout, annotations)

    assert [
        page["page_number"] for page in plan["pages"] if page["brand_block"] is not None
    ] == [3]
    assert plan["pages"][-1]["page_number"] == 3
    assert plan["branding"]["rendered_page_number"] == 3
    assert layout == frozen_layout
    assert len(plan["pages"]) == len(layout["pages"])


@pytest.mark.parametrize(
    ("drop_field", "overrides"),
    [
        pytest.param("page_margin_mpt", {}, id="missing"),
        pytest.param(None, {"section_gap_mpt": True}, id="bool"),
        pytest.param(None, {"section_gap_mpt": "8000"}, id="string"),
        pytest.param(None, {"section_gap_mpt": 0}, id="zero"),
        pytest.param(None, {"section_gap_mpt": -1}, id="negative"),
        pytest.param(None, {"policy_version": 99}, id="unknown-policy"),
        pytest.param(None, {"policy_version": 1}, id="obsolete-policy"),
        pytest.param(None, {"card_min_width_mpt": 600_000}, id="width-order"),
        pytest.param(None, {"card_padding_x_mpt": 300_000}, id="no-inner-width"),
        pytest.param(None, {"logo_size_mpt": 600_000}, id="logo-too-wide"),
        pytest.param(None, {"brand_size_mpt": 25_000}, id="font-too-tall"),
        pytest.param(None, {"page_margin_mpt": 300_000}, id="margin-too-wide"),
        pytest.param(None, {"unexpected_field": 1}, id="extra-field"),
    ],
)
def test_brand_layout_rejects_invalid_values_with_a_stable_error(
    drop_field: str | None,
    overrides: dict[str, object],
) -> None:
    manifest, _asset_path, manifest_hash = load_brand_manifest()
    invalid = deepcopy(manifest)
    if drop_field is not None:
        invalid["layout"].pop(drop_field)
    invalid["layout"].update(overrides)

    with pytest.raises(OverlayPlanError) as caught:
        freeze_brand_block(
            output_page_number=1,
            start_draw_order=0,
            manifest=invalid,
            manifest_sha256=manifest_hash,
        )

    assert caught.value.code == "BRAND_LAYOUT_INVALID"


def test_brand_manifest_loader_rejects_invalid_layout_with_a_stable_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _asset_path, _manifest_hash = load_brand_manifest()
    invalid = deepcopy(manifest)
    invalid["layout"].pop("page_margin_mpt")
    manifest_path = tmp_path / "brand-manifest.json"
    manifest_path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(branding_module, "_BRAND_DIR", tmp_path.resolve())

    with pytest.raises(OverlayPlanError) as caught:
        load_brand_manifest(manifest_path)

    assert caught.value.code == "BRAND_ASSET_INVALID"


def test_brand_manifest_loader_wraps_a_missing_manifest_with_a_stable_error(
    tmp_path,
) -> None:
    with pytest.raises(OverlayPlanError) as caught:
        load_brand_manifest(tmp_path / "missing-brand-manifest.json")

    assert caught.value.code == "BRAND_ASSET_INVALID"


def test_brand_manifest_loader_wraps_a_missing_asset_with_a_stable_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _asset_path, _manifest_hash = load_brand_manifest()
    brand_dir = tmp_path / "assets" / "branding"
    brand_dir.mkdir(parents=True)
    manifest_path = brand_dir / "brand-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(branding_module, "PROJECT_ROOT", tmp_path.resolve())
    monkeypatch.setattr(branding_module, "_BRAND_DIR", brand_dir.resolve())

    with pytest.raises(OverlayPlanError) as caught:
        load_brand_manifest(manifest_path)

    assert caught.value.code == "BRAND_ASSET_INVALID"


def test_brand_card_height_includes_the_logo_to_text_section_gap() -> None:
    manifest, _asset_path, manifest_hash = load_brand_manifest()
    constrained = deepcopy(manifest)
    constrained["layout"]["card_padding_y_mpt"] = 1
    constrained["layout"]["section_gap_mpt"] = 100_000

    frozen = freeze_brand_block(
        output_page_number=1,
        start_draw_order=0,
        manifest=constrained,
        manifest_sha256=manifest_hash,
        available_y_mpt=(48_000, 700_000),
    )

    assert frozen is not None
    block, _bindings, runs = frozen
    assert int(block["bbox_mpt"][1]) >= 48_000
    assert int(block["bbox_mpt"][3]) <= 700_000
    assert all(
        int(block["bbox_mpt"][1]) <= int(run["bbox_mpt"][1])
        and int(run["bbox_mpt"][3]) <= int(block["bbox_mpt"][3])
        for run in runs
    )


def test_statement_is_budgeted_once_and_keeps_source_reference_tail():
    source, _, _, _, annotations, graph, layout = build_render_fixture(
        include_reference_page=True
    )
    plan = build_overlay_plan(source, graph, layout, annotations)
    assert graph["body_page_budget"] == 1
    assert len(layout["pages"]) == 2
    assert len(plan["pages"]) == len(layout["pages"])
    assert plan["pages"][-1]["brand_block"] is not None
    assert layout["solver_trace"]["chinese_page_count"] == 1
    assert layout["solver_trace"]["budget_exceeded"] is False
    assert all(p["brand_block"] is None for p in plan["pages"][:-1])
    text = "".join(
        r["text"]
        for r in plan["pages"][-1]["draw_runs"]
        if r["content_kind"] == "brand"
    )
    manifest, _, _ = load_brand_manifest()
    assert all(line in text for line in manifest["disclaimer_zh_lines"])
