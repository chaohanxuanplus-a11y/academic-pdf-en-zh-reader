# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Small fail-closed contracts for deterministic overlay planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical


class OverlayPlanError(ValueError):
    """A stable pre-render failure that must not leave a PDF artifact."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LeaderReflowRequired(OverlayPlanError):
    """No collision-free route exists under the frozen finite lane policy."""

    def __init__(self, message: str = "leader route requires layout reflow") -> None:
        super().__init__("LEADER_REFLOW_REQUIRED", message)


@dataclass(frozen=True, slots=True)
class OverlayPlanLimits:
    """Finite complexity bounds checked before drawing-plan expansion."""

    version: int = 1
    max_pages: int = 2_000
    max_blocks_per_page: int = 20_000
    max_lines: int = 1_000_000
    max_characters: int = 20_000_000
    max_draw_runs: int = 2_000_000
    max_source_obstacles: int = 2_000_000
    max_leaders: int = 4_096
    max_leader_collision_checks: int = 5_000_000

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(type(value) is not int for value in values.values()):
            raise OverlayPlanError(
                "PLAN_COMPLEXITY_LIMIT",
                "overlay limits must be integers",
            )
        if self.version < 1 or any(
            value < 1 for key, value in values.items() if key != "version"
        ):
            raise OverlayPlanError(
                "PLAN_COMPLEXITY_LIMIT",
                "overlay limits must be positive",
            )


@dataclass(frozen=True, slots=True)
class FrozenContinuationStyle:
    style_id: str
    semantic_role: str
    font_role: str
    size_mpt: int
    line_height_mpt: int

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        code: str,
    ) -> FrozenContinuationStyle:
        expected = {
            "style_id",
            "semantic_role",
            "font_role",
            "size_mpt",
            "line_height_mpt",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise OverlayPlanError(code, "continuation style fields are not exact")
        if (
            not isinstance(value["style_id"], str)
            or not value["style_id"]
            or value["semantic_role"] != "auxiliary"
            or value["font_role"] not in {"body", "heading"}
            or type(value["size_mpt"]) is not int
            or int(value["size_mpt"]) <= 0
            or type(value["line_height_mpt"]) is not int
            or int(value["line_height_mpt"]) <= 0
        ):
            raise OverlayPlanError(code, "continuation style is invalid")
        return cls(
            style_id=str(value["style_id"]),
            semantic_role="auxiliary",
            font_role=str(value["font_role"]),
            size_mpt=int(value["size_mpt"]),
            line_height_mpt=int(value["line_height_mpt"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrozenContinuationRun:
    run_index: int
    text: str
    font_role: str
    font_name: str
    x_offset_mpt: int
    width_mpt: int

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        expected_index: int,
        expected_offset_mpt: int,
        code: str,
    ) -> FrozenContinuationRun:
        expected = {
            "run_index",
            "text",
            "font_role",
            "font_name",
            "x_offset_mpt",
            "width_mpt",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise OverlayPlanError(code, "continuation run fields are not exact")
        if (
            type(value["run_index"]) is not int
            or value["run_index"] != expected_index
            or not isinstance(value["text"], str)
            or not value["text"]
            or value["font_role"] not in {"body", "heading", "symbols"}
            or not isinstance(value["font_name"], str)
            or not value["font_name"]
            or type(value["x_offset_mpt"]) is not int
            or value["x_offset_mpt"] != expected_offset_mpt
            or type(value["width_mpt"]) is not int
            or int(value["width_mpt"]) <= 0
        ):
            raise OverlayPlanError(code, "continuation run is invalid")
        return cls(
            run_index=expected_index,
            text=str(value["text"]),
            font_role=str(value["font_role"]),
            font_name=str(value["font_name"]),
            x_offset_mpt=expected_offset_mpt,
            width_mpt=int(value["width_mpt"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def _continuation_runs(
    value: object,
    *,
    code: str,
) -> tuple[FrozenContinuationRun, ...]:
    if not isinstance(value, list) or not value:
        raise OverlayPlanError(code, "continuation runs are invalid")
    result: list[FrozenContinuationRun] = []
    offset = 0
    for index, raw in enumerate(value):
        run = FrozenContinuationRun.from_mapping(
            raw,
            expected_index=index,
            expected_offset_mpt=offset,
            code=code,
        )
        result.append(run)
        offset += run.width_mpt
    return tuple(result)


def _continuation_metrics(
    value: Mapping[str, object],
    *,
    style: FrozenContinuationStyle,
    runs: tuple[FrozenContinuationRun, ...],
    code: str,
) -> None:
    integer_fields = (
        "width_mpt",
        "line_height_mpt",
        "ascent_mpt",
        "descent_mpt",
    )
    if any(type(value[field]) is not int for field in integer_fields):
        raise OverlayPlanError(code, "continuation metrics are invalid")
    if (
        int(value["width_mpt"]) != sum(run.width_mpt for run in runs)
        or int(value["line_height_mpt"]) != style.line_height_mpt
        or int(value["ascent_mpt"]) <= 0
        or int(value["descent_mpt"]) > 0
        or int(value["ascent_mpt"]) - int(value["descent_mpt"])
        > int(value["line_height_mpt"])
        or "".join(run.text for run in runs) != value["text"]
        or value["color_token"] != "muted_gray"
        or value["color_hex"] != "#666666"
    ):
        raise OverlayPlanError(code, "continuation metrics or paint are inconsistent")


@dataclass(frozen=True, slots=True)
class FrozenContinuationHeader:
    contract_version: str
    text: str
    style: FrozenContinuationStyle
    runs: tuple[FrozenContinuationRun, ...]
    width_mpt: int
    line_height_mpt: int
    ascent_mpt: int
    descent_mpt: int
    top_inset_mpt: int
    gap_after_mpt: int
    reserve_height_mpt: int
    color_token: str
    color_hex: str
    horizontal_alignment: str
    header_hash: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> FrozenContinuationHeader:
        code = "CONTINUATION_HEADER_INVALID"
        expected = {
            "contract_version",
            "text",
            "style",
            "runs",
            "width_mpt",
            "line_height_mpt",
            "ascent_mpt",
            "descent_mpt",
            "top_inset_mpt",
            "gap_after_mpt",
            "reserve_height_mpt",
            "color_token",
            "color_hex",
            "horizontal_alignment",
            "header_hash",
        }
        if set(value) != expected:
            raise OverlayPlanError(code, "continuation header fields are not exact")
        style = FrozenContinuationStyle.from_mapping(value["style"], code=code)
        runs = _continuation_runs(value["runs"], code=code)
        _continuation_metrics(value, style=style, runs=runs, code=code)
        spacing = ("top_inset_mpt", "gap_after_mpt", "reserve_height_mpt")
        if (
            value["contract_version"] != "1.0.0"
            or value["text"] != "译文续页"
            or value["horizontal_alignment"] != "right"
            or any(type(value[field]) is not int for field in spacing)
            or int(value["top_inset_mpt"]) < 0
            or int(value["gap_after_mpt"]) < 0
            or int(value["reserve_height_mpt"])
            != int(value["top_inset_mpt"])
            + int(value["line_height_mpt"])
            + int(value["gap_after_mpt"])
            or value["header_hash"]
            != sha256_canonical(
                {key: value[key] for key in expected if key != "header_hash"}
            )
        ):
            raise OverlayPlanError(code, "continuation header is inconsistent")
        return cls(
            contract_version="1.0.0",
            text="译文续页",
            style=style,
            runs=runs,
            width_mpt=int(value["width_mpt"]),
            line_height_mpt=int(value["line_height_mpt"]),
            ascent_mpt=int(value["ascent_mpt"]),
            descent_mpt=int(value["descent_mpt"]),
            top_inset_mpt=int(value["top_inset_mpt"]),
            gap_after_mpt=int(value["gap_after_mpt"]),
            reserve_height_mpt=int(value["reserve_height_mpt"]),
            color_token="muted_gray",
            color_hex="#666666",
            horizontal_alignment="right",
            header_hash=str(value["header_hash"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "text": self.text,
            "style": self.style.to_mapping(),
            "runs": [run.to_mapping() for run in self.runs],
            "width_mpt": self.width_mpt,
            "line_height_mpt": self.line_height_mpt,
            "ascent_mpt": self.ascent_mpt,
            "descent_mpt": self.descent_mpt,
            "top_inset_mpt": self.top_inset_mpt,
            "gap_after_mpt": self.gap_after_mpt,
            "reserve_height_mpt": self.reserve_height_mpt,
            "color_token": self.color_token,
            "color_hex": self.color_hex,
            "horizontal_alignment": self.horizontal_alignment,
            "header_hash": self.header_hash,
        }


@dataclass(frozen=True, slots=True)
class FrozenContinuationLabel:
    """Exact layout-owned continuation label consumed without inference."""

    contract_version: str
    header_hash: str
    source_page_number: int
    continuation_index: int
    text: str
    style: FrozenContinuationStyle
    runs: tuple[FrozenContinuationRun, ...]
    width_mpt: int
    line_height_mpt: int
    ascent_mpt: int
    descent_mpt: int
    color_token: str
    color_hex: str
    x_mpt: int
    baseline_y_mpt: int
    bbox_mpt: tuple[int, int, int, int]
    label_hash: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> FrozenContinuationLabel:
        code = "CONTINUATION_LABEL_INVALID"
        expected = {
            "contract_version",
            "header_hash",
            "source_page_number",
            "continuation_index",
            "text",
            "style",
            "runs",
            "width_mpt",
            "line_height_mpt",
            "ascent_mpt",
            "descent_mpt",
            "color_token",
            "color_hex",
            "x_mpt",
            "baseline_y_mpt",
            "bbox_mpt",
            "label_hash",
        }
        if set(value) != expected:
            raise OverlayPlanError(code, "continuation label fields are not exact")
        style = FrozenContinuationStyle.from_mapping(value["style"], code=code)
        runs = _continuation_runs(value["runs"], code=code)
        _continuation_metrics(value, style=style, runs=runs, code=code)
        integers = (
            "source_page_number",
            "continuation_index",
            "x_mpt",
            "baseline_y_mpt",
        )
        raw_box = value["bbox_mpt"]
        if (
            value["contract_version"] != "1.0.0"
            or value["text"] != "译文续页"
            or any(type(value[key]) is not int for key in integers)
            or int(value["source_page_number"]) < 1
            or int(value["continuation_index"]) < 1
            or int(value["x_mpt"]) < 0
            or int(value["baseline_y_mpt"]) < 0
            or not isinstance(value["header_hash"], str)
            or len(value["header_hash"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in value["header_hash"]
            )
            or not isinstance(raw_box, list)
            or len(raw_box) != 4
            or any(type(item) is not int for item in raw_box)
        ):
            raise OverlayPlanError(code, "continuation label values are invalid")
        box = tuple(int(item) for item in raw_box)
        baseline = int(value["baseline_y_mpt"])
        if box != (
            int(value["x_mpt"]),
            baseline + int(value["descent_mpt"]),
            int(value["x_mpt"]) + int(value["width_mpt"]),
            baseline + int(value["ascent_mpt"]),
        ) or value["label_hash"] != sha256_canonical(
            {key: value[key] for key in expected if key != "label_hash"}
        ):
            raise OverlayPlanError(code, "continuation label is inconsistent")
        return cls(
            contract_version="1.0.0",
            header_hash=str(value["header_hash"]),
            source_page_number=int(value["source_page_number"]),
            continuation_index=int(value["continuation_index"]),
            text="译文续页",
            style=style,
            runs=runs,
            width_mpt=int(value["width_mpt"]),
            line_height_mpt=int(value["line_height_mpt"]),
            ascent_mpt=int(value["ascent_mpt"]),
            descent_mpt=int(value["descent_mpt"]),
            color_token="muted_gray",
            color_hex="#666666",
            x_mpt=int(value["x_mpt"]),
            baseline_y_mpt=baseline,
            bbox_mpt=box,
            label_hash=str(value["label_hash"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "header_hash": self.header_hash,
            "source_page_number": self.source_page_number,
            "continuation_index": self.continuation_index,
            "text": self.text,
            "style": self.style.to_mapping(),
            "runs": [run.to_mapping() for run in self.runs],
            "width_mpt": self.width_mpt,
            "line_height_mpt": self.line_height_mpt,
            "ascent_mpt": self.ascent_mpt,
            "descent_mpt": self.descent_mpt,
            "color_token": self.color_token,
            "color_hex": self.color_hex,
            "x_mpt": self.x_mpt,
            "baseline_y_mpt": self.baseline_y_mpt,
            "bbox_mpt": list(self.bbox_mpt),
            "label_hash": self.label_hash,
        }


DEFAULT_OVERLAY_PLAN_LIMITS = OverlayPlanLimits()


__all__ = [
    "DEFAULT_OVERLAY_PLAN_LIMITS",
    "FrozenContinuationHeader",
    "FrozenContinuationLabel",
    "LeaderReflowRequired",
    "OverlayPlanError",
    "OverlayPlanLimits",
]
