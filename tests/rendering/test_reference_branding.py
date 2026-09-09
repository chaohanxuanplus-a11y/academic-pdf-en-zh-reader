# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering import branding as branding_module
from academic_pdf_en_zh_reader.rendering import overlay_plan as overlay_plan_module
from academic_pdf_en_zh_reader.rendering.branding import (
    freeze_brand_block,
    load_brand_manifest,
    reference_only_regions,
)
from academic_pdf_en_zh_reader.rendering.contracts import (
    OverlayPlanError,
    OverlayPlanLimits,
)
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan

from .test_text_styles import build_render_fixture


def _reference_plan() -> tuple[dict[str, object], ...]:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(include_reference_page=True)
    )
    plan = build_overlay_plan(source, graph, layout, annotations)
    return source, graph, layout, plan


def _assert_appended_disclaimer(plan: dict, layout: dict) -> None:
    assert len(plan["pages"]) == len(layout["pages"]) + 1
    page = plan["pages"][-1]
    assert page["page_number"] == len(plan["pages"])
    assert page["page_kind"] == "disclaimer"
    assert page["source_page_number"] is None
    assert page["continuation_index"] == 0
    assert page["continuation_label"] is None
    assert page["source_obstacle_count"] == 0
    assert page["source_obstacles_hash"] == sha256_canonical([])
    assert page["brand_block"] is not None
    assert page["underlines"] == page["leader_routes"] == []
    assert all(run["content_kind"] == "brand" for run in page["draw_runs"])
    assert all(previous["brand_block"] is None for previous in plan["pages"][:-1])
    assert plan["branding"]["rendered_page_number"] == page["page_number"]
    assert plan["branding"]["appended_page_number"] == page["page_number"]
    text = "".join(run["text"] for run in page["draw_runs"])
    assert "责任声明" in text
    assert "已追加本声明页" in text
    manifest, _asset, _hash = load_brand_manifest()
    assert all(line in text for line in manifest["disclaimer_zh_lines"])
    assert all(
        run["size_mpt"] >= 11_000
        for run in page["draw_runs"]
        if run["style_id"] == "brand:disclaimer"
    )


def test_brand_card_uses_the_final_empty_native_reference_page_once() -> None:
    _source, _graph, layout, plan = _reference_plan()

    branded = [page for page in plan["pages"] if page["brand_block"] is not None]
    assert len(branded) == 1
    assert branded[0]["page_number"] == 2
    assert branded[0]["source_page_number"] == 2
    assert branded[0]["page_kind"] == "native"
    assert layout["pages"][1]["blocks"] == []
    assert plan["branding"]["rendered_page_number"] == 2
    assert plan["branding"]["appended_page_number"] is None

    block = branded[0]["brand_block"]
    assert block["asset_path"] == "assets/branding/hanhai-wencai.png"
    assert (
        block["asset_sha256"]
        == "1bb1dad5b83bd3b98060e513f3298bb0ace2bf4669590f60c6a3fbbdfb4ba0ab"
    )
    assert block["brand_block_hash"] == sha256_canonical(
        {key: value for key, value in block.items() if key != "brand_block_hash"}
    )
    assert block["bbox_mpt"][0] >= 595_276
    assert block["bbox_mpt"][2] <= 1_190_551
    assert block["bbox_mpt"][1] >= 0
    assert block["bbox_mpt"][3] <= 841_890

    brand_runs = [
        run for run in branded[0]["draw_runs"] if run["content_kind"] == "brand"
    ]
    painted = "".join(run["text"] for run in brand_runs)
    manifest, _manifest_path, _manifest_hash = load_brand_manifest()
    assert "瀚海问材" in painted
    assert "Hanhai Materials" in painted
    assert "academic-pdf-en-zh-reader" in painted
    assert str(manifest["github_display"]) in painted
    assert "本项目不提供、下载、托管或自动发布论文" in painted
    expected_sizes = {
        "brand:brand": 17_000,
        "brand:brand-en": 10_500,
        "brand:skill": 10_000,
        "brand:github": 9_000,
        "brand:disclaimer": 7_500,
    }
    for style_id, size_mpt in expected_sizes.items():
        assert {
            int(run["size_mpt"]) for run in brand_runs if run["style_id"] == style_id
        } == {size_mpt}

    def _style_bbox(style_id: str) -> tuple[int, int]:
        runs = [run for run in brand_runs if run["style_id"] == style_id]
        return (
            min(int(run["bbox_mpt"][1]) for run in runs),
            max(int(run["bbox_mpt"][3]) for run in runs),
        )

    brand_bottom, _brand_top = _style_bbox("brand:brand")
    english_bottom, english_top = _style_bbox("brand:brand-en")
    _skill_bottom, skill_top = _style_bbox("brand:skill")
    brand_pair_gap = brand_bottom - english_top
    assert 0 <= brand_pair_gap <= 10_000
    assert brand_pair_gap < english_bottom - skill_top
    assert all(
        int(block["bbox_mpt"][0]) <= int(run["bbox_mpt"][0])
        and int(run["bbox_mpt"][2]) <= int(block["bbox_mpt"][2])
        and int(block["bbox_mpt"][1]) <= int(run["bbox_mpt"][1])
        and int(run["bbox_mpt"][3]) <= int(block["bbox_mpt"][3])
        for run in brand_runs
    )

    disclaimer_runs: dict[int, list[dict[str, object]]] = {}
    for run in brand_runs:
        if run["style_id"] == "brand:disclaimer":
            disclaimer_runs.setdefault(int(run["line_index"]), []).append(run)
    disclaimer_lines = [
        "".join(
            str(run["text"]) for run in sorted(runs, key=lambda run: run["draw_order"])
        )
        for _line_index, runs in sorted(disclaimer_runs.items())
    ]
    assert disclaimer_lines == [
        "本文件由用户主动调用本地工作区内的 Skill 和当前 Agent 生成",
        "输入论文由用户自行合法取得并提供；本项目不提供、下载、托管或自动发布论文",
        "用户须自行确认复制、翻译、标注、保存、分享或公开发布所需的授权或法律依据",
        "并在重要使用或发布前核验 Agent 生成的译文与注释",
        "项目许可证仅覆盖项目代码与文档，不授予对原论文、图表、其他第三方内容或生成译文的权利",
        "完整声明见项目仓库中的 DISCLAIMER.md",
    ]
    card_center_twice = int(block["bbox_mpt"][0]) + int(block["bbox_mpt"][2])
    for runs in disclaimer_runs.values():
        left = min(int(run["bbox_mpt"][0]) for run in runs)
        right = max(int(run["bbox_mpt"][2]) for run in runs)
        assert abs(left + right - card_center_twice) <= 2
    assert all(
        run["draw_run_hash"]
        == sha256_canonical(
            {key: value for key, value in run.items() if key != "draw_run_hash"}
        )
        for run in brand_runs
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


def test_brand_card_uses_safe_space_below_translated_references_heading() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(
            include_reference_page=True,
            include_reference_heading=True,
        )
    )
    frozen_layout = deepcopy(layout)

    plan = build_overlay_plan(source, graph, layout, annotations)

    branded = [page for page in plan["pages"] if page["brand_block"] is not None]
    assert len(branded) == 1
    page = branded[0]
    assert page["page_number"] == 2
    assert page["page_kind"] == "native"
    heading = next(
        block
        for block in layout["pages"][1]["blocks"]
        if block["style"]["semantic_role"] == "heading"
    )
    manifest, _asset_path, _manifest_hash = load_brand_manifest()
    brand_layout = manifest["layout"]
    regions = reference_only_regions(
        source,
        layout,
        page_margin_mpt=int(brand_layout["page_margin_mpt"]),
        heading_gap_mpt=int(brand_layout["section_gap_mpt"]),
    )
    assert regions[2] == (
        int(brand_layout["page_margin_mpt"]),
        int(heading["bbox_mpt"][1]) - int(brand_layout["section_gap_mpt"]),
    )
    brand_box = page["brand_block"]["bbox_mpt"]
    assert int(brand_box[3]) <= regions[2][1]
    assert layout == frozen_layout
    assert len(plan["pages"]) == len(layout["pages"])
    assert plan["branding"]["rendered_page_number"] == 2


@pytest.mark.parametrize("final_region", [None, (48_000, 48_001)])
def test_disclaimer_page_is_appended_instead_of_falling_back_to_an_earlier_page(
    monkeypatch: pytest.MonkeyPatch,
    final_region: tuple[int, int] | None,
) -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(
            include_reference_page=True,
            include_following_reference_page=True,
        )
    )
    frozen_layout = deepcopy(layout)
    regions = {2: (48_000, 793_890)}
    if final_region is not None:
        regions[3] = final_region
    monkeypatch.setattr(
        overlay_plan_module, "reference_only_regions", lambda *_args, **_kwargs: regions
    )

    plan = build_overlay_plan(source, graph, layout, annotations)

    _assert_appended_disclaimer(plan, layout)
    assert layout == frozen_layout


def test_brand_card_skips_short_heading_region_for_next_reference_page() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(
            include_reference_page=True,
            include_reference_heading=True,
            reference_heading_chinese_text="参考文献" * 275,
            include_following_reference_page=True,
        )
    )

    plan = build_overlay_plan(source, graph, layout, annotations)

    assert plan["pages"][1]["brand_block"] is None
    assert plan["pages"][2]["brand_block"] is not None
    assert plan["branding"]["rendered_page_number"] == 3


def test_brand_card_appends_disclaimer_when_heading_region_is_too_short() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(
            include_reference_page=True,
            include_reference_heading=True,
            reference_heading_chinese_text="参考文献" * 275,
        )
    )

    plan = build_overlay_plan(source, graph, layout, annotations)

    _assert_appended_disclaimer(plan, layout)


def test_reference_regions_reject_a_nonterminal_reference_page() -> None:
    source = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [
                    {
                        "id": "reference-entry",
                        "role": "reference-entry",
                        "translation_policy": "excluded",
                        "text": "[1] Example",
                    }
                ],
            },
            {
                "page_number": 2,
                "blocks": [
                    {
                        "id": "body",
                        "role": "body",
                        "translation_policy": "required",
                        "text": "Appendix body",
                    }
                ],
            },
        ]
    }
    layout = {
        "pages": [
            {
                "page_number": 1,
                "source_page_number": 1,
                "page_kind": "native",
                "blocks": [],
            },
            {
                "page_number": 2,
                "source_page_number": 2,
                "page_kind": "native",
                "blocks": [],
            },
        ]
    }

    assert (
        reference_only_regions(
            source,
            layout,
            page_margin_mpt=48_000,
            heading_gap_mpt=8_000,
        )
        == {}
    )


def test_reference_regions_fail_closed_for_multiple_references_headings() -> None:
    source, _units, _translation, _review, _annotations, _graph, layout = (
        build_render_fixture(
            include_reference_page=True,
            include_reference_heading=True,
        )
    )
    duplicate = deepcopy(source["pages"][1]["blocks"][0])
    duplicate["id"] = "second-references-heading"
    source["pages"][1]["blocks"].insert(1, duplicate)

    assert (
        reference_only_regions(
            source,
            layout,
            page_margin_mpt=48_000,
            heading_gap_mpt=8_000,
        )
        == {}
    )


@pytest.mark.parametrize(
    ("drop_field", "overrides"),
    [
        pytest.param("page_margin_mpt", {}, id="missing"),
        pytest.param(None, {"section_gap_mpt": True}, id="bool"),
        pytest.param(None, {"section_gap_mpt": "8000"}, id="string"),
        pytest.param(None, {"section_gap_mpt": 0}, id="zero"),
        pytest.param(None, {"section_gap_mpt": -1}, id="negative"),
        pytest.param(None, {"policy_version": 2}, id="unknown-policy"),
        pytest.param(None, {"card_min_width_mpt": 430_000}, id="width-order"),
        pytest.param(None, {"card_padding_x_mpt": 210_000}, id="no-inner-width"),
        pytest.param(None, {"logo_size_mpt": 400_000}, id="logo-too-wide"),
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


def test_brand_card_appends_disclaimer_without_excluded_reference_entries() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    plan = build_overlay_plan(source, graph, layout, annotations)

    _assert_appended_disclaimer(plan, layout)


def test_appended_disclaimer_counts_toward_the_output_page_limit() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    with pytest.raises(OverlayPlanError) as caught:
        build_overlay_plan(
            source,
            graph,
            layout,
            annotations,
            limits=OverlayPlanLimits(max_pages=len(layout["pages"])),
        )
    assert caught.value.code == "PLAN_COMPLEXITY_LIMIT"


def test_brand_card_never_uses_a_continuation_page_or_moves_translation() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(
            chinese_text="甲。" * 3_000,
            include_reference_page=True,
        )
    )
    frozen_layout = deepcopy(layout)
    plan = build_overlay_plan(source, graph, layout, annotations)

    branded = [page for page in plan["pages"] if page["brand_block"] is not None]
    assert len(branded) == 1
    assert branded[0]["page_kind"] == "native"
    assert branded[0]["source_page_number"] == 2
    assert layout == frozen_layout
    for page in plan["pages"]:
        if page["page_kind"] == "continuation":
            assert page["brand_block"] is None
