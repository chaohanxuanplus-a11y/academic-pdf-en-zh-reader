# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0
import pytest

from academic_pdf_en_zh_reader.job.cleanup import cleanup_after_job
from academic_pdf_en_zh_reader.job.storage import load_job_state
from academic_pdf_en_zh_reader.orchestration import finish as module
from academic_pdf_en_zh_reader.orchestration.recovery import create_finish_attempt
from tests.orchestration.test_finish import _finish, _finish_fixture


def test_verified_attempt_preserves_revision_and_refuses_changed_extraction(tmp_path):
    f = _finish_fixture(tmp_path, job_id="recovery-copy")
    attempt = create_finish_attempt(f.managed_root, f.job_root)
    assert load_job_state(attempt / "job-state.json").translation_revision == 1
    assert (attempt / "units.json").read_bytes() == (
        f.job_root / "units.json"
    ).read_bytes()
    (f.job_root / "units.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        create_finish_attempt(f.managed_root, f.job_root)


def test_layout_repair_reuses_checkpoint_and_unchanged_translation(
    tmp_path, monkeypatch
):
    f = _finish_fixture(tmp_path, job_id="recovery-layout")
    source = (f.job_root / "units.json").read_bytes()
    draft = f.translation_json.read_bytes()
    calls = []

    def attempt(**kwargs):
        root = kwargs["managed_root"] / kwargs["job_id"]
        calls.append(root)
        assert (root / "units.json").read_bytes() == source
        assert kwargs["translation_json"].read_bytes() == draft
        assert load_job_state(root / "job-state.json").translation_revision == 1
        if len(calls) == 1:
            raise module.FinishJobError("FINALIZATION_FAILED", "layout")
        cleanup_after_job(f.managed_root, root, outcome="success")
        return {"status": "ok", "code": "FINISH_OK", "added_pages": 0}

    monkeypatch.setattr(module, "_finish_attempt", attempt)
    with pytest.raises(module.FinishJobError) as error:
        _finish(f)
    assert error.value.recovery_action == "repair-layout-or-fonts"
    assert f.job_root.exists() and not calls[0].exists()
    assert (f.job_root / "units.json").read_bytes() == source
    assert _finish(f)["code"] == "FINISH_OK"
    assert calls[0] != calls[1]
    assert not f.job_root.exists()
