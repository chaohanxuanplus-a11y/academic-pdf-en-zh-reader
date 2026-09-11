# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from academic_pdf_en_zh_reader.typography.style_contract import (
    AUXILIARY_MIN_SIZE_MPT,
    FontSizeSample,
    build_style_contract,
    detect_body_size_mpt,
)


def test_body_size_uses_character_weighted_mode_after_robust_filtering() -> None:
    samples = (
        FontSizeSample(size_mpt=10_000, character_count=80),
        FontSizeSample(size_mpt=10_200, character_count=420),
        FontSizeSample(size_mpt=9_900, character_count=100),
        # A corrupt span may contain many characters but must not set body size.
        FontSizeSample(size_mpt=40_000, character_count=50_000),
    )

    assert detect_body_size_mpt(samples) == 10_200


def test_style_contract_freezes_document_wide_role_sizes() -> None:
    contract = build_style_contract(
        (
            FontSizeSample(size_mpt=10_000, character_count=700),
            FontSizeSample(size_mpt=9_800, character_count=20),
        )
    )

    assert contract.version == 2
    assert contract.style_for("body") == contract.style_for("abstract")
    assert contract.style_for("body").size_mpt == 10_000
    assert contract.style_for("body").font_role == "body"
    assert contract.style_for("title").font_role == "heading"
    assert contract.style_for("heading").font_role == "heading"
    assert contract.style_for("auxiliary").size_mpt == max(
        9_000,
        AUXILIARY_MIN_SIZE_MPT,
    )
    assert contract.style_for("auxiliary").size_mpt >= AUXILIARY_MIN_SIZE_MPT
    assert contract.ambiguity_size_mpt("title") == contract.style_for("title").size_mpt
    assert contract.ambiguity_size_mpt("body") == 10_000

    with pytest.raises(FrozenInstanceError):
        contract.version = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "samples",
    [
        (),
        (FontSizeSample(size_mpt=0, character_count=1),),
        (FontSizeSample(size_mpt=10_000, character_count=0),),
    ],
)
def test_body_size_rejects_missing_or_nonpositive_evidence(
    samples: tuple[FontSizeSample, ...],
) -> None:
    with pytest.raises(ValueError, match="positive"):
        detect_body_size_mpt(samples)
