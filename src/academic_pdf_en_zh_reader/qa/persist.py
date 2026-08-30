# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Idempotent QA/provenance commit followed by the final VALIDATED CAS."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from academic_pdf_en_zh_reader.constants import __version__
from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    advance_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    load_job_state,
    write_immutable_artifact,
    write_job_state,
)
from academic_pdf_en_zh_reader.qa.api import (
    DEFAULT_QA_CONFIG,
    QaConfig,
    run_mechanical_qa,
)
from academic_pdf_en_zh_reader.schema.validate import validate_artifact
from academic_pdf_en_zh_reader.typography.font_registry import (
    DEFAULT_FONT_MANIFEST,
    PROJECT_ROOT,
)

DEFAULT_DEPENDENCY_LOCK = PROJECT_ROOT / "uv.lock"
_CODE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}\Z")


class QaCommitError(ValueError):
    """A stable commit failure without document content or local paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class QaCommitResult:
    code: str
    passed: bool
    qa_hash: str | None
    provenance_hash: str | None
    validated_state_hash: str | None


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise QaCommitError("QA_COMMIT_INPUT_UNREADABLE") from exc
    return digest.hexdigest()


def _job_root(path: str | Path) -> Path:
    raw = Path(path)
    try:
        if raw.is_symlink() or not raw.is_dir():
            raise ValueError
        resolved = raw.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise QaCommitError("QA_JOB_ROOT_INVALID") from exc
    return resolved


def _direct_regular_file(path: Path, root: Path, *, code: str) -> Path:
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError
        resolved = path.resolve(strict=True)
        if resolved.parent != root:
            raise ValueError
    except (OSError, ValueError) as exc:
        raise QaCommitError(code) from exc
    return resolved


def _canonical_file_matches(
    path: Path, value: Mapping[str, object], *, code: str
) -> str:
    expected = canonical_json_bytes(value)
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise QaCommitError(code) from exc
    if actual != expected:
        raise QaCommitError(code)
    return sha256_bytes(actual)


def _write_or_verify_same(
    path: Path,
    value: dict[str, object],
    schema_name: str,
    *,
    root: Path,
) -> str:
    """Create once, or accept only identical canonical bytes on a retry."""

    encoded = canonical_json_bytes(value)
    validate_artifact(schema_name, value)
    if path.parent != root or path.is_symlink():
        raise QaCommitError("QA_ARTIFACT_PATH_INVALID")
    try:
        return write_immutable_artifact(path, value, schema_name)
    except ArtifactExistsError:
        pass
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve(strict=True).parent != root
        ):
            raise ValueError
        actual = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise QaCommitError("QA_ARTIFACT_CONFLICT") from exc
    if actual != encoded:
        raise QaCommitError("QA_ARTIFACT_CONFLICT")
    validate_artifact(schema_name, value)
    return sha256_bytes(actual)


def _provenance(
    *,
    qa: Mapping[str, object],
    qa_hash: str,
    render_manifest: Mapping[str, object],
    dependency_lock_path: Path,
    code_version: str,
) -> dict[str, object]:
    if not _CODE_VERSION.fullmatch(code_version):
        raise QaCommitError("QA_CODE_VERSION_INVALID")
    dependency_hash = _sha256_path(dependency_lock_path)
    try:
        dependencies = render_manifest["dependencies"]
        upstreams = [
            {
                "name": "pypdf",
                "version": str(dependencies["pypdf"]),
                "source": "https://pypi.org/project/pypdf/",
            },
            {
                "name": "reportlab",
                "version": str(dependencies["reportlab"]),
                "source": "https://pypi.org/project/reportlab/",
            },
            {
                "name": "pypdfium2",
                "version": importlib.metadata.version("pypdfium2"),
                "source": "https://pypi.org/project/pypdfium2/",
            },
            {
                "name": "Pillow",
                "version": importlib.metadata.version("Pillow"),
                "source": "https://pypi.org/project/Pillow/",
            },
        ]
        config_hash = sha256_canonical(
            {
                "contract_version": "1.0.0",
                "qa_hash": qa_hash,
                "qa_config_hash": qa["qa_config_hash"],
                "render_manifest_hash": qa["render_manifest_hash"],
                "finalization_receipt_hash": qa["finalization_receipt_hash"],
            }
        )
        provenance: dict[str, object] = {
            "schema_version": "1.0.0",
            "artifact_kind": "provenance",
            "source_sha256": qa["source_pdf_sha256"],
            "normalized_pdf_sha256": qa["normalized_pdf_sha256"],
            "config_hash": config_hash,
            "code_version": code_version,
            "dependency_lock_sha256": dependency_hash,
            "font_manifest_hash": render_manifest["font_manifest_hash"],
            "runtime_fingerprint": {
                "python": platform.python_version(),
                "unicode": unicodedata.unidata_version,
                "platform": platform.system(),
            },
            "upstreams": upstreams,
        }
        validate_artifact("provenance", provenance)
    except QaCommitError:
        raise
    except Exception as exc:
        raise QaCommitError("QA_PROVENANCE_INVALID") from exc
    return provenance


def _validate_state_bindings(
    *,
    state: object,
    expected_rendered_state_hash: str,
    render_manifest_hash: str,
    output_pdf_sha256: str,
    source_sha256: str,
    normalized_pdf_sha256: str,
) -> str:
    try:
        current_hash = state_hash(state)  # type: ignore[arg-type]
        stage = state.stage  # type: ignore[attr-defined]
        history = state.history  # type: ignore[attr-defined]
        ledger = state.artifact_hashes  # type: ignore[attr-defined]
        source_hash = state.source_sha256  # type: ignore[attr-defined]
    except Exception as exc:
        raise QaCommitError("QA_STATE_INVALID") from exc
    if stage is JobStage.RENDERED:
        if current_hash != expected_rendered_state_hash:
            raise QaCommitError("QA_STATE_CAS_MISMATCH")
    elif stage is JobStage.VALIDATED:
        if history[-1].previous_state_hash != expected_rendered_state_hash:
            raise QaCommitError("QA_STATE_CAS_MISMATCH")
    else:
        raise QaCommitError("QA_STATE_NOT_RENDERED")
    if (
        ledger.get("render-manifest") != render_manifest_hash
        or ledger.get("pdf") != output_pdf_sha256
        or ledger.get("normalized-pdf") != normalized_pdf_sha256
        or source_hash != source_sha256
    ):
        raise QaCommitError("QA_STATE_BINDING_MISMATCH")
    return current_hash


def validate_and_persist_qa(
    *,
    job_root: str | Path,
    expected_rendered_state_hash: str,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    source: Mapping[str, object],
    units: Mapping[str, object],
    translation: Mapping[str, object],
    review: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    finalization_receipt: Mapping[str, object],
    policy_inputs: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    render_manifest: Mapping[str, object],
    expected_render_manifest_hash: str,
    font_manifest_path: str | Path = DEFAULT_FONT_MANIFEST,
    dependency_lock_path: str | Path = DEFAULT_DEPENDENCY_LOCK,
    code_version: str = __version__,
    config: QaConfig = DEFAULT_QA_CONFIG,
) -> QaCommitResult:
    """Commit QA/provenance idempotently, then CAS RENDERED to VALIDATED."""

    root = _job_root(job_root)
    state_path = _direct_regular_file(
        root / "job-state.json", root, code="QA_STATE_INVALID"
    )
    manifest_path = _direct_regular_file(
        root / "render-manifest.json",
        root,
        code="QA_RENDER_MANIFEST_FILE_INVALID",
    )
    output_path = _direct_regular_file(
        Path(output_pdf_path), root, code="QA_OUTPUT_FILE_INVALID"
    )
    render_manifest_hash = _canonical_file_matches(
        manifest_path,
        render_manifest,
        code="QA_RENDER_MANIFEST_FILE_INVALID",
    )
    output_hash = _sha256_path(output_path)
    normalized_pdf_hash = _sha256_path(Path(source_pdf_path))
    raw_source_hash = source.get("source_sha256")
    if not isinstance(raw_source_hash, str):
        raise QaCommitError("QA_STATE_BINDING_MISMATCH")
    if render_manifest_hash != expected_render_manifest_hash:
        raise QaCommitError("QA_RENDER_MANIFEST_FILE_INVALID")
    try:
        state = load_job_state(state_path)
    except Exception as exc:
        raise QaCommitError("QA_STATE_INVALID") from exc
    rendered_state_hash = _validate_state_bindings(
        state=state,
        expected_rendered_state_hash=expected_rendered_state_hash,
        render_manifest_hash=render_manifest_hash,
        output_pdf_sha256=output_hash,
        source_sha256=raw_source_hash,
        normalized_pdf_sha256=normalized_pdf_hash,
    )

    qa = run_mechanical_qa(
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_path,
        source=source,
        units=units,
        translation=translation,
        review=review,
        annotations=annotations,
        frame_graph=frame_graph,
        layout=layout,
        finalization_receipt=finalization_receipt,
        policy_inputs=policy_inputs,
        overlay_plan=overlay_plan,
        render_manifest=render_manifest,
        expected_render_manifest_hash=expected_render_manifest_hash,
        font_manifest_path=font_manifest_path,
        config=config,
    )
    validate_artifact("qa", qa)
    if qa["passed"] is not True:
        return QaCommitResult(
            code="QA_FAILED",
            passed=False,
            qa_hash=None,
            provenance_hash=None,
            validated_state_hash=None,
        )

    qa_hash = sha256_bytes(canonical_json_bytes(qa))
    provenance = _provenance(
        qa=qa,
        qa_hash=qa_hash,
        render_manifest=render_manifest,
        dependency_lock_path=Path(dependency_lock_path),
        code_version=code_version,
    )
    persisted_qa_hash = _write_or_verify_same(root / "qa.json", qa, "qa", root=root)
    persisted_provenance_hash = _write_or_verify_same(
        root / "provenance.json", provenance, "provenance", root=root
    )
    if persisted_qa_hash != qa_hash:
        raise QaCommitError("QA_ARTIFACT_CONFLICT")

    if state.stage is JobStage.VALIDATED:
        ledger = state.artifact_hashes
        if (
            ledger.get("qa") != persisted_qa_hash
            or ledger.get("provenance") != persisted_provenance_hash
        ):
            raise QaCommitError("QA_STATE_BINDING_MISMATCH")
        return QaCommitResult(
            code="QA_ALREADY_VALIDATED",
            passed=True,
            qa_hash=persisted_qa_hash,
            provenance_hash=persisted_provenance_hash,
            validated_state_hash=rendered_state_hash,
        )

    try:
        validated = advance_job(
            state,
            JobStage.VALIDATED,
            {"qa": persisted_qa_hash, "provenance": persisted_provenance_hash},
            expected_previous_state_hash=rendered_state_hash,
        )
        write_job_state(
            state_path,
            validated,
            expected_previous_state_hash=rendered_state_hash,
        )
    except Exception as exc:
        raise QaCommitError("QA_STATE_CAS_FAILED") from exc
    return QaCommitResult(
        code="QA_VALIDATED",
        passed=True,
        qa_hash=persisted_qa_hash,
        provenance_hash=persisted_provenance_hash,
        validated_state_hash=state_hash(validated),
    )


__all__ = [
    "DEFAULT_DEPENDENCY_LOCK",
    "QaCommitError",
    "QaCommitResult",
    "validate_and_persist_qa",
]
