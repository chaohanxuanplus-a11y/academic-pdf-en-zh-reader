# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from copy import deepcopy

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.rendering.overlay_plan import build_overlay_plan

from .test_text_styles import build_render_fixture


def _reference_plan() -> tuple[dict[str, object], ...]:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture(include_reference_page=True)
    )
    plan = build_overlay_plan(source, graph, layout, annotations)
    return source, graph, layout, plan


def test_brand_card_uses_the_first_empty_native_reference_page_once() -> None:
    _source, _graph, layout, plan = _reference_plan()

    branded = [page for page in plan["pages"] if page["brand_block"] is not None]
    assert len(branded) == 1
    assert branded[0]["page_number"] == 2
    assert branded[0]["source_page_number"] == 2
    assert branded[0]["page_kind"] == "native"
    assert layout["pages"][1]["blocks"] == []
    assert plan["branding"]["rendered_page_number"] == 2

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
    assert "瀚海问材" in painted
    assert "Hanhai Materials" in painted
    assert "academic-pdf-en-zh-reader" in painted
    assert "GitHub：公开发布后提供" in painted
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


def test_brand_card_is_absent_without_excluded_reference_entries() -> None:
    source, _units, _translation, _review, annotations, graph, layout = (
        build_render_fixture()
    )
    plan = build_overlay_plan(source, graph, layout, annotations)

    assert plan["branding"]["rendered_page_number"] is None
    assert all(page["brand_block"] is None for page in plan["pages"])
    assert not any(
        run["content_kind"] == "brand"
        for page in plan["pages"]
        for run in page["draw_runs"]
    )


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
