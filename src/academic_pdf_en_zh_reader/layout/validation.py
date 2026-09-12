# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
"""Structural integrity of the continuous reading artifacts."""

from academic_pdf_en_zh_reader.job.hashing import sha256_canonical
from academic_pdf_en_zh_reader.schema.validate import SchemaValidationError


def validate_reading_artifact(name, artifact):
    def require(condition, message):
        if not condition:
            raise SchemaValidationError(message)

    if name == "frame-graph":
        flows = artifact["unit_flows"] + artifact["auxiliary_flows"]
        ids = [f.get("id", f["unit_id"]) for f in flows]
        require(len(set(ids)) == len(ids), "duplicate reading flow")
        require(
            len(ids) == len(artifact["flow_order"])
            and set(ids) == set(artifact["flow_order"]),
            "reading flow coverage mismatch",
        )
        parents = {flow["unit_id"]: flow for flow in artifact["unit_flows"]}
        grouped_notes = {uid: [] for uid in parents}
        for parent in parents.values():
            expected_columns = (
                1 if parent["role"] in {"title", "abstract", "keywords"} else 2
            )
            require(
                parent["column_count"] == expected_columns,
                f"reading width differs from unit role: {parent['unit_id']}",
            )
        for note in artifact["auxiliary_flows"]:
            uid = note["unit_id"]
            require(
                uid in parents, f"note has no parent: {note['annotation_id']} ({uid})"
            )
            require(
                note["column_count"] == parents[uid]["column_count"],
                f"note width differs from parent: {note['annotation_id']} ({uid})",
            )
            grouped_notes[uid].append(note["id"])
        require(
            artifact["flow_order"]
            == [
                identifier
                for uid in parents
                for identifier in (uid, *grouped_notes[uid])
            ],
            "notes must immediately follow their parent in reading order",
        )
        for flow in flows:
            lines = flow["lines"]
            text = "".join(s["text"] for s in flow["composite_segments"])
            require(len(lines) == flow["line_count"], "line count mismatch")
            from academic_pdf_en_zh_reader.layout.unit_parts import grapheme_boundaries

            safe = grapheme_boundaries(text)
            cursor = 0
            for index, line in enumerate(lines):
                require(
                    line["index"] == index and line["composite_start"] == cursor,
                    "discontinuous reading lines",
                )
                require(
                    line["composite_start"] in safe and line["composite_end"] in safe,
                    "line splits a grapheme",
                )
                require(
                    "\n" not in line["text"] and "\r" not in line["text"],
                    "line contains a hard newline",
                )
                cursor = line["composite_end"]
                require(
                    text[line["composite_start"] : cursor].strip() == line["text"],
                    "line text differs from its complete content",
                )
                require(
                    "".join(r["text"] for r in line["runs"]) == line["text"],
                    "font runs differ from line text",
                )
                require(
                    line["line_height_mpt"] >= line["ascent_mpt"] - line["descent_mpt"],
                    "line height clips glyphs",
                )
                require(
                    line["line_box_hash"]
                    == sha256_canonical(
                        {
                            "line_box_contract_version": "2.0.0",
                            **{k: v for k, v in line.items() if k != "line_box_hash"},
                        }
                    ),
                    "line binding mismatch",
                )
            require(
                cursor == flow["composite_length"] == len(text),
                "incomplete reading content",
            )
            require(
                all(
                    1 <= n <= artifact["source_page_count"]
                    for n in flow["source_page_numbers"]
                ),
                "invalid semantic source page",
            )
    else:
        pages = artifact["pages"]
        require(
            [p["page_number"] for p in pages] == list(range(1, len(pages) + 1)),
            "target pages out of order",
        )
        require(
            [p["page_number"] for p in pages if p["warning_region_mpt"] is not None]
            == [len(pages)],
            "statement must occur once on final page",
        )
        for page in pages:
            if page["warning_region_mpt"] is not None:
                margin = artifact["flow_spacing"]["vertical_padding_mpt"]
                from academic_pdf_en_zh_reader.rendering.page_geometry import (
                    A3_LANDSCAPE_HEIGHT_MPT,
                )

                safe_top = min(
                    [block["bbox_mpt"][1] for block in page["blocks"]]
                    + [A3_LANDSCAPE_HEIGHT_MPT - margin]
                )
                if page["blocks"]:
                    safe_top -= artifact["flow_spacing"]["block_gap_mpt"]
                require(
                    page["warning_region_mpt"] == [margin, safe_top]
                    and safe_top > margin,
                    "statement region must be the complete final safe space",
                )
            frames = {f["id"]: f for f in page["frames"]}
            require(len(frames) == len(page["frames"]), "duplicate target frame")
            for block in page["blocks"]:
                require(
                    block["target_page_number"] == page["page_number"],
                    "target binding mismatch",
                )
                require(block["frame_id"] in frames, "missing target frame")
                require(
                    block["line_end"] - block["line_start"] == len(block["lines"]),
                    "incomplete placed lines",
                )
                box, frame = block["bbox_mpt"], frames[block["frame_id"]]["bbox_mpt"]
                require(
                    frame[0] <= box[0] < box[2] <= frame[2]
                    and frame[1] <= box[1] < box[3] <= frame[3],
                    "content outside its column",
                )
