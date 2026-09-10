# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Idempotent QA/provenance commit followed by the final VALIDATED CAS."""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from academic_pdf_en_zh_reader.constants import __version__
from academic_pdf_en_zh_reader.job.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
)
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.job.state import (
    JobStage,
    JobState,
    advance_job,
    state_hash,
)
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    write_immutable_artifact,
    write_job_state,
)
from academic_pdf_en_zh_reader.qa.api import (
    DEFAULT_QA_CONFIG,
    QaConfig,
    run_mechanical_qa,
)
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.security.input_copy import (
    UnsafeInputError,
    read_bounded_regular_file,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.typography.font_registry import (
    DEFAULT_FONT_MANIFEST,
    PROJECT_ROOT,
)

DEFAULT_DEPENDENCY_LOCK = PROJECT_ROOT / "uv.lock"
_CODE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}\Z")
_DEFAULT_QA_CONFIG_HASH = sha256_canonical(DEFAULT_QA_CONFIG.to_mapping())


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


@dataclass(frozen=True, slots=True)
class _ValidatedQaInputs:
    root: Path
    root_identities: tuple[tuple[int, int], ...]
    state: JobState
    rendered_state_hash: str
    render_manifest_hash: str
    output_pdf_sha256: str
    normalized_pdf_sha256: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class _RootSnapshot:
    path: Path
    identities: tuple[tuple[int, int], ...]


def _is_reparse(information: os.stat_result) -> bool:
    attributes = getattr(information, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(information.st_mode) or bool(attributes & reparse_flag)


def _capture_root(path: str | Path) -> _RootSnapshot:
    absolute = Path(os.path.abspath(os.fspath(path)))
    components = (*reversed(absolute.parents), absolute)
    identities: list[tuple[int, int]] = []
    for component in components:
        information = component.lstat()
        if not stat.S_ISDIR(information.st_mode) or _is_reparse(information):
            raise ValueError("QA root contains a reparse component")
        identities.append((information.st_dev, information.st_ino))
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(os.fspath(resolved)) != os.path.normcase(os.fspath(absolute)):
        raise ValueError("QA root is not canonical")
    return _RootSnapshot(resolved, tuple(identities))


def _revalidate_root(root: Path, identities: tuple[tuple[int, int], ...]) -> None:
    try:
        observed = _capture_root(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise QaCommitError("QA_JOB_ROOT_INVALID") from exc
    if observed.path != root or observed.identities != identities:
        raise QaCommitError("QA_JOB_ROOT_CHANGED")


def _bounded_file(path: Path, *, maximum: int, code: str):
    try:
        return read_bounded_regular_file(path, max_bytes=maximum)
    except (OSError, UnsafeInputError, ValueError) as exc:
        raise QaCommitError(code) from exc


def _job_root(path: str | Path) -> _RootSnapshot:
    try:
        return _capture_root(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise QaCommitError("QA_JOB_ROOT_INVALID") from exc


def _direct_regular_file(path: Path, root: Path, *, code: str) -> Path:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        name = absolute.name
        information = absolute.lstat()
        if (
            absolute.parent != root
            or not name
            or name != name.rstrip(" .")
            or ":" in name
            or PureWindowsPath(name).is_reserved()
            or not stat.S_ISREG(information.st_mode)
            or _is_reparse(information)
        ):
            raise ValueError
        resolved = absolute.resolve(strict=True)
        if resolved != absolute:
            raise ValueError
    except (OSError, RuntimeError, ValueError) as exc:
        raise QaCommitError(code) from exc
    return resolved


def _canonical_file_matches(
    path: Path,
    value: Mapping[str, object],
    *,
    code: str,
    maximum: int,
    schema_name: str,
) -> str:
    try:
        expected = canonical_json_bytes(value)
        validate_artifact(schema_name, value)
        actual = _bounded_file(path, maximum=maximum, code=code)
    except QaCommitError:
        raise
    except (CanonicalJsonError, SchemaValidationError, TypeError, ValueError) as exc:
        raise QaCommitError(code) from exc
    if actual.data != expected:
        raise QaCommitError(code)
    return actual.sha256


def _load_bounded_job_state(path: Path, *, maximum: int) -> JobState:
    bounded = _bounded_file(path, maximum=maximum, code="QA_STATE_INVALID")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        value = json.loads(
            bounded.data.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
        )
        if not isinstance(value, dict) or canonical_json_bytes(value) != bounded.data:
            raise ValueError("job state is not canonical")
        validate_artifact("job-state", value)
        return JobState.from_dict(value)
    except (
        CanonicalJsonError,
        SchemaValidationError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise QaCommitError("QA_STATE_INVALID") from exc


def _write_or_verify_same(
    path: Path,
    value: dict[str, object],
    schema_name: str,
    *,
    root: Path,
    root_identities: tuple[tuple[int, int], ...],
    maximum: int,
) -> str:
    """Create once, or accept only identical canonical bytes on a retry."""

    invalid_code = (
        "QA_PROVENANCE_INVALID"
        if schema_name == "provenance"
        else "QA_ARTIFACT_INVALID"
    )
    try:
        encoded = canonical_json_bytes(value)
        validate_artifact(schema_name, value)
    except (CanonicalJsonError, SchemaValidationError, TypeError, ValueError) as exc:
        raise QaCommitError(invalid_code) from exc
    if path.parent != root or path.is_symlink():
        raise QaCommitError("QA_ARTIFACT_PATH_INVALID")
    _revalidate_root(root, root_identities)
    try:
        digest = write_immutable_artifact(path, value, schema_name)
    except ArtifactExistsError:
        digest = ""
    except (OSError, SchemaValidationError, ValueError) as exc:
        raise QaCommitError("QA_ARTIFACT_COMMIT_FAILED") from exc
    _revalidate_root(root, root_identities)
    actual = _bounded_file(
        path,
        maximum=maximum,
        code="QA_ARTIFACT_CONFLICT",
    )
    if actual.data != encoded:
        raise QaCommitError("QA_ARTIFACT_CONFLICT")
    if digest and digest != actual.sha256:
        raise QaCommitError("QA_ARTIFACT_CONFLICT")
    return actual.sha256


def _provenance(
    *,
    qa: Mapping[str, object],
    qa_hash: str,
    render_manifest: Mapping[str, object],
    dependency_lock_path: Path,
    code_version: str,
    limits: WorkerLimits,
) -> dict[str, object]:
    # Provenance is committed by the parent. Importing metadata at module load
    # also initializes email/socket and fails inside the zero-capability worker.
    import importlib.metadata

    if not _CODE_VERSION.fullmatch(code_version):
        raise QaCommitError("QA_CODE_VERSION_INVALID")
    dependency_hash = _bounded_file(
        dependency_lock_path,
        maximum=limits.max_result_object_bytes,
        code="QA_COMMIT_INPUT_UNREADABLE",
    ).sha256
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


def _validate_qa_inputs(
    *,
    job_root: str | Path,
    expected_rendered_state_hash: str,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    source: Mapping[str, object],
    render_manifest: Mapping[str, object],
    expected_render_manifest_hash: str,
    limits: WorkerLimits,
) -> _ValidatedQaInputs:
    root_snapshot = _job_root(job_root)
    root = root_snapshot.path
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
        maximum=limits.max_result_object_bytes,
        schema_name="render-manifest",
    )
    output_hash = _bounded_file(
        output_path,
        maximum=limits.max_output_pdf_bytes,
        code="QA_OUTPUT_FILE_INVALID",
    ).sha256
    normalized_pdf_hash = _bounded_file(
        Path(source_pdf_path),
        maximum=limits.max_normalized_pdf_bytes,
        code="QA_SOURCE_FILE_INVALID",
    ).sha256
    raw_source_hash = source.get("source_sha256")
    if not isinstance(raw_source_hash, str):
        raise QaCommitError("QA_STATE_BINDING_MISMATCH")
    if render_manifest_hash != expected_render_manifest_hash:
        raise QaCommitError("QA_RENDER_MANIFEST_FILE_INVALID")
    state = _load_bounded_job_state(
        state_path,
        maximum=limits.max_result_object_bytes,
    )
    rendered_state_hash = _validate_state_bindings(
        state=state,
        expected_rendered_state_hash=expected_rendered_state_hash,
        render_manifest_hash=render_manifest_hash,
        output_pdf_sha256=output_hash,
        source_sha256=raw_source_hash,
        normalized_pdf_sha256=normalized_pdf_hash,
    )
    return _ValidatedQaInputs(
        root=root,
        root_identities=root_snapshot.identities,
        state=state,
        rendered_state_hash=rendered_state_hash,
        render_manifest_hash=render_manifest_hash,
        output_pdf_sha256=output_hash,
        normalized_pdf_sha256=normalized_pdf_hash,
        source_sha256=raw_source_hash,
    )


def persist_qa_result(
    *,
    job_root: str | Path,
    expected_rendered_state_hash: str,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    source: Mapping[str, object],
    render_manifest: Mapping[str, object],
    expected_render_manifest_hash: str,
    expected_finalization_receipt_hash: str,
    qa: Mapping[str, object],
    expected_qa_config_hash: str = _DEFAULT_QA_CONFIG_HASH,
    dependency_lock_path: str | Path = DEFAULT_DEPENDENCY_LOCK,
    code_version: str = __version__,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> QaCommitResult:
    """Validate a worker-produced QA artifact, persist it, then CAS state."""

    validated = _validate_qa_inputs(
        job_root=job_root,
        expected_rendered_state_hash=expected_rendered_state_hash,
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_pdf_path,
        source=source,
        render_manifest=render_manifest,
        expected_render_manifest_hash=expected_render_manifest_hash,
        limits=limits,
    )
    root = validated.root
    state = validated.state
    state_path = root / "job-state.json"
    rendered_state_hash = validated.rendered_state_hash

    try:
        validate_artifact("qa", qa)
    except (SchemaValidationError, TypeError, ValueError) as exc:
        raise QaCommitError("QA_ARTIFACT_INVALID") from exc
    checks = qa.get("checks")
    if not isinstance(checks, list) or qa.get("passed") is not all(
        isinstance(check, Mapping) and check.get("passed") is True for check in checks
    ):
        raise QaCommitError("QA_ARTIFACT_INVALID")
    manifest_pages = render_manifest.get("pages")
    if not isinstance(manifest_pages, list):
        raise QaCommitError("QA_ARTIFACT_BINDING_MISMATCH")
    if (
        qa.get("render_manifest_hash") != validated.render_manifest_hash
        or qa.get("finalization_receipt_hash") != expected_finalization_receipt_hash
        or render_manifest.get("finalization_receipt_hash")
        != expected_finalization_receipt_hash
        or qa.get("source_pdf_sha256") != validated.source_sha256
        or qa.get("normalized_pdf_sha256") != validated.normalized_pdf_sha256
        or qa.get("output_pdf_sha256") != validated.output_pdf_sha256
        or qa.get("qa_config_hash") != expected_qa_config_hash
        or qa.get("checked_page_count") != len(manifest_pages)
        or (
            qa.get("passed") is True
            and qa.get("rasterized_page_count") != len(manifest_pages)
        )
    ):
        raise QaCommitError("QA_ARTIFACT_BINDING_MISMATCH")
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
        limits=limits,
    )
    persisted_qa_hash = _write_or_verify_same(
        root / "qa.json",
        dict(qa),
        "qa",
        root=root,
        root_identities=validated.root_identities,
        maximum=limits.max_result_object_bytes,
    )
    persisted_provenance_hash = _write_or_verify_same(
        root / "provenance.json",
        provenance,
        "provenance",
        root=root,
        root_identities=validated.root_identities,
        maximum=limits.max_result_object_bytes,
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
        _revalidate_root(root, validated.root_identities)
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
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> QaCommitResult:
    """Test/helper path that computes QA before using the parent-only committer."""

    _validate_qa_inputs(
        job_root=job_root,
        expected_rendered_state_hash=expected_rendered_state_hash,
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_pdf_path,
        source=source,
        render_manifest=render_manifest,
        expected_render_manifest_hash=expected_render_manifest_hash,
        limits=limits,
    )
    qa = run_mechanical_qa(
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_pdf_path,
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
    return persist_qa_result(
        job_root=job_root,
        expected_rendered_state_hash=expected_rendered_state_hash,
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_pdf_path,
        source=source,
        render_manifest=render_manifest,
        expected_render_manifest_hash=expected_render_manifest_hash,
        expected_finalization_receipt_hash=sha256_canonical(finalization_receipt),
        qa=qa,
        expected_qa_config_hash=sha256_canonical(config.to_mapping()),
        dependency_lock_path=dependency_lock_path,
        code_version=code_version,
        limits=limits,
    )


__all__ = [
    "DEFAULT_DEPENDENCY_LOCK",
    "QaCommitError",
    "QaCommitResult",
    "persist_qa_result",
    "validate_and_persist_qa",
]
