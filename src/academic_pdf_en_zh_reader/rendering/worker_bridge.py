# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

"""Parent bridge for fail-closed rendering in the Windows LPAC worker."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PureWindowsPath

from academic_pdf_en_zh_reader.job.canonical_json import canonical_json_bytes
from academic_pdf_en_zh_reader.job.hashing import sha256_bytes, sha256_canonical
from academic_pdf_en_zh_reader.job.storage import (
    ArtifactExistsError,
    write_immutable_artifact,
    write_immutable_bytes,
)
from academic_pdf_en_zh_reader.rendering.compose import (
    CompositionError,
    CompositionResult,
    _font_usages,
)
from academic_pdf_en_zh_reader.rendering.contracts import DEFAULT_OVERLAY_PLAN_LIMITS
from academic_pdf_en_zh_reader.rendering.metadata import metadata_policy
from academic_pdf_en_zh_reader.schema.validate import (
    SchemaValidationError,
    validate_artifact,
)
from academic_pdf_en_zh_reader.security.input_copy import (
    SafeInputCopy,
    UnsafeInputError,
    copy_untrusted_input,
    read_bounded_regular_file,
)
from academic_pdf_en_zh_reader.security.limits import DEFAULT_LIMITS, WorkerLimits
from academic_pdf_en_zh_reader.security.worker_protocol import (
    RENDER_HANDOFF_PATH,
    RENDER_POLICY_VERSION,
    WorkerRequest,
)
from academic_pdf_en_zh_reader.typography.font_registry import DEFAULT_FONT_MANIFEST

_ALLOWED_WORKER_ERROR_CODES = frozenset(
    {
        "RENDER_FAILED",
        "SANDBOX_CONTRACT_UNVERIFIED",
        "WORKER_LIMIT_EXCEEDED",
    }
)


@dataclass(frozen=True, slots=True)
class _RootSnapshot:
    path: Path
    identities: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _OutputPaths:
    root: _RootSnapshot
    output: Path
    manifest: Path


def _is_reparse(information: os.stat_result) -> bool:
    attributes = getattr(information, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(information.st_mode) or bool(attributes & reparse_flag)


def _root_snapshot(path: Path) -> _RootSnapshot:
    absolute = Path(os.path.abspath(os.fspath(path)))
    components = (*reversed(absolute.parents), absolute)
    identities: list[tuple[int, int]] = []
    for component in components:
        information = component.lstat()
        if not stat.S_ISDIR(information.st_mode) or _is_reparse(information):
            raise ValueError("render root contains a reparse component")
        identities.append((information.st_dev, information.st_ino))
    resolved = absolute.resolve(strict=True)
    if os.path.normcase(os.fspath(resolved)) != os.path.normcase(os.fspath(absolute)):
        raise ValueError("render root is not canonical")
    return _RootSnapshot(resolved, tuple(identities))


def _canonical_leaf(path: Path, root: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    name = absolute.name
    windows_name = PureWindowsPath(name)
    if (
        absolute.parent != root
        or not name
        or name != name.rstrip(" .")
        or ":" in name
        or windows_name.is_reserved()
    ):
        raise ValueError("render output is not a direct canonical child")
    try:
        information = absolute.lstat()
    except FileNotFoundError:
        return absolute
    if not stat.S_ISREG(information.st_mode) or _is_reparse(information):
        raise ValueError("render output is not a regular file")
    return absolute


def _run_platform_worker(
    request: WorkerRequest,
    workspace: Path,
    *,
    limits: WorkerLimits,
):
    if os.name != "nt":
        raise CompositionError(
            "SANDBOX_UNAVAILABLE",
            "the required Windows render worker is unavailable",
        )
    from academic_pdf_en_zh_reader.security.windows_worker import (
        SandboxCleanupError,
        SandboxUnavailableError,
        WorkerCpuLimitError,
        WorkerExecutionError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerReportedError,
        WorkerTimeoutError,
        run_worker,
    )

    try:
        return run_worker(request, workspace, limits=limits)
    except WorkerReportedError as error:
        code = (
            error.code if error.code in _ALLOWED_WORKER_ERROR_CODES else "RENDER_FAILED"
        )
        raise CompositionError(code, "isolated rendering failed") from error
    except SandboxCleanupError as error:
        raise CompositionError(
            "SANDBOX_CONTRACT_UNVERIFIED",
            "the isolated render worker cleanup could not be verified",
        ) from error
    except SandboxUnavailableError as error:
        raise CompositionError(
            "SANDBOX_UNAVAILABLE",
            "the required Windows render worker is unavailable",
        ) from error
    except (
        WorkerCpuLimitError,
        WorkerMemoryLimitError,
        WorkerOutputLimitError,
        WorkerTimeoutError,
    ) as error:
        raise CompositionError(
            "WORKER_LIMIT_EXCEEDED",
            "the isolated render worker exceeded a fixed limit",
        ) from error
    except WorkerExecutionError as error:
        raise CompositionError("RENDER_FAILED", "isolated rendering failed") from error


def _write_handoff(root: Path, encoded: bytes, limits: WorkerLimits) -> None:
    if not encoded or len(encoded) > limits.max_extraction_artifact_bytes:
        raise CompositionError("RENDER_HANDOFF_INVALID", "render handoff is oversized")
    path = root / RENDER_HANDOFF_PATH
    try:
        with path.open("x+b") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            information = os.fstat(stream.fileno())
            stream.seek(0)
            persisted = stream.read(limits.max_extraction_artifact_bytes + 1)
            final_information = os.fstat(stream.fileno())
    except OSError as error:
        raise CompositionError(
            "RENDER_HANDOFF_INVALID",
            "render handoff could not be staged",
        ) from error
    if (
        not stat.S_ISREG(information.st_mode)
        or _is_reparse(information)
        or final_information.st_size != information.st_size
        or information.st_size != len(encoded)
        or persisted != encoded
    ):
        raise CompositionError("RENDER_HANDOFF_INVALID", "render handoff changed")


def _decode_manifest(encoded: object, limits: WorkerLimits) -> dict[str, object]:
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > limits.max_result_object_bytes
    ):
        raise CompositionError("RENDER_MANIFEST_INVALID", "manifest is absent")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        decoded = json.loads(
            encoded.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
        )
        if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != encoded:
            raise ValueError("manifest is not canonical JSON")
        validate_artifact("render-manifest", decoded)
    except (
        UnicodeError,
        json.JSONDecodeError,
        SchemaValidationError,
        TypeError,
        ValueError,
    ) as error:
        raise CompositionError(
            "RENDER_MANIFEST_INVALID",
            "manifest is not canonical schema-valid JSON",
        ) from error
    return decoded


def _validate_output_paths(root: Path, output: Path, manifest: Path) -> _OutputPaths:
    try:
        snapshot = _root_snapshot(root)
        canonical_output = _canonical_leaf(output, snapshot.path)
        canonical_manifest = _canonical_leaf(manifest, snapshot.path)
        if canonical_output == canonical_manifest:
            raise ValueError
    except (OSError, RuntimeError, ValueError) as error:
        raise CompositionError(
            "RENDER_PATH_INVALID", "render path is invalid"
        ) from error
    return _OutputPaths(snapshot, canonical_output, canonical_manifest)


def _revalidate_output_paths(paths: _OutputPaths) -> None:
    try:
        observed = _root_snapshot(paths.root.path)
        if observed != paths.root:
            raise ValueError("render root identity changed")
        _canonical_leaf(paths.output, paths.root.path)
        _canonical_leaf(paths.manifest, paths.root.path)
    except (OSError, RuntimeError, ValueError) as error:
        raise CompositionError(
            "RENDER_PATH_INVALID", "render path changed before commit"
        ) from error


def _expected_manifest_bindings(
    *,
    source: Mapping[str, object],
    annotations: Mapping[str, object],
    frame_graph: Mapping[str, object],
    layout: Mapping[str, object],
    finalization_receipt: Mapping[str, object],
    policy_inputs: Mapping[str, object],
    overlay_plan: Mapping[str, object],
    expected_finalization_receipt_hash: str,
    expected_overlay_plan_hash: str,
    limits: WorkerLimits,
) -> tuple[dict[str, object], int]:
    try:
        receipt_hash = sha256_canonical(finalization_receipt)
        supplied_plan_hash = overlay_plan.get("overlay_plan_hash")
        plan_payload = dict(overlay_plan)
        plan_payload.pop("overlay_plan_hash", None)
        observed_plan_hash = sha256_canonical(plan_payload)
        render_style = overlay_plan["render_style"]
        overlay_limits = asdict(DEFAULT_OVERLAY_PLAN_LIMITS)
        font_manifest = read_bounded_regular_file(
            DEFAULT_FONT_MANIFEST,
            max_bytes=limits.max_result_object_bytes,
        )
        pages = overlay_plan["pages"]
        if not isinstance(pages, list):
            raise ValueError("overlay plan pages are invalid")
        fingerprint = frame_graph.get("font_fingerprint")
        if (
            policy_inputs.get("font-fingerprint") != fingerprint
            or overlay_plan.get("font_fingerprint") != fingerprint
        ):
            raise ValueError("font fingerprint parents differ")
        bindings: dict[str, object] = {
            "finalization_receipt_hash": expected_finalization_receipt_hash,
            "source_sha256": source.get("source_sha256"),
            "normalized_pdf_sha256": source.get("normalized_pdf_sha256"),
            "source_artifact_hash": sha256_canonical(source),
            "annotations_hash": sha256_canonical(annotations),
            "annotation_selection_hash": annotations.get("orange_selection_hash"),
            "frame_graph_hash": sha256_canonical(frame_graph),
            "layout_hash": sha256_canonical(layout),
            "render_style": render_style,
            "render_style_hash": sha256_canonical(render_style),
            "font_manifest_hash": font_manifest.sha256,
            "font_fingerprint": fingerprint,
            "font_usages": _font_usages(overlay_plan),
            "overlay_plan_hash": expected_overlay_plan_hash,
            "overlay_plan_limits": overlay_limits,
            "overlay_plan_limits_hash": sha256_canonical(overlay_limits),
            "metadata_policy": metadata_policy(),
        }
    except (OSError, UnsafeInputError, TypeError, KeyError, ValueError) as error:
        raise CompositionError(
            "RENDER_PARENT_BINDING_INVALID", "render parent bindings are invalid"
        ) from error
    if receipt_hash != expected_finalization_receipt_hash:
        raise CompositionError(
            "FINALIZATION_RECEIPT_MISMATCH", "finalization receipt hash differs"
        )
    if (
        supplied_plan_hash != expected_overlay_plan_hash
        or observed_plan_hash != expected_overlay_plan_hash
    ):
        raise CompositionError("OVERLAY_PLAN_MISMATCH", "overlay plan hash differs")
    return bindings, len(pages)


def _validate_manifest_bindings(
    manifest: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    expected_page_count: int,
) -> None:
    pages = manifest.get("pages")
    if any(manifest.get(name) != value for name, value in expected.items()) or not (
        isinstance(pages, list) and len(pages) == expected_page_count
    ):
        raise CompositionError(
            "RENDER_MANIFEST_INVALID", "manifest parent binding differs"
        )


def _commit_worker_outputs(
    *,
    paths: _OutputPaths,
    pdf_bytes: bytes,
    manifest: dict[str, object],
    limits: WorkerLimits,
) -> str:
    output_hash = hashlib.sha256(pdf_bytes).hexdigest()
    if manifest.get("output_pdf_sha256") != output_hash:
        raise CompositionError("RENDER_MANIFEST_INVALID", "output hash differs")
    manifest_bytes = canonical_json_bytes(manifest)
    created_output = False
    try:
        _revalidate_output_paths(paths)
        try:
            persisted_output_hash = write_immutable_bytes(paths.output, pdf_bytes)
            created_output = True
        except ArtifactExistsError:
            persisted_output = read_bounded_regular_file(
                paths.output,
                max_bytes=limits.max_output_pdf_bytes,
            )
            if persisted_output.data != pdf_bytes:
                raise CompositionError(
                    "RENDER_COMMIT_FAILED", "existing render output differs"
                ) from None
            persisted_output_hash = persisted_output.sha256
        if persisted_output_hash != output_hash:
            raise CompositionError("RENDER_COMMIT_FAILED", "render output differs")

        _revalidate_output_paths(paths)
        try:
            manifest_hash = write_immutable_artifact(
                paths.manifest,
                manifest,
                "render-manifest",
            )
        except ArtifactExistsError:
            persisted_manifest = read_bounded_regular_file(
                paths.manifest,
                max_bytes=limits.max_result_object_bytes,
            )
            if persisted_manifest.data != manifest_bytes:
                raise CompositionError(
                    "RENDER_COMMIT_FAILED", "existing render manifest differs"
                ) from None
            manifest_hash = persisted_manifest.sha256
        if manifest_hash != sha256_bytes(manifest_bytes):
            raise CompositionError("RENDER_COMMIT_FAILED", "render manifest differs")

        _revalidate_output_paths(paths)
        persisted_output = read_bounded_regular_file(
            paths.output,
            max_bytes=limits.max_output_pdf_bytes,
        )
        persisted_manifest = read_bounded_regular_file(
            paths.manifest,
            max_bytes=limits.max_result_object_bytes,
        )
        if (
            persisted_output.data != pdf_bytes
            or persisted_manifest.data != manifest_bytes
        ):
            raise CompositionError("RENDER_COMMIT_FAILED", "render commit changed")
        return manifest_hash
    except (CompositionError, OSError, UnsafeInputError, ValueError) as error:
        if created_output:
            try:
                _revalidate_output_paths(paths)
                if (
                    read_bounded_regular_file(
                        paths.output,
                        max_bytes=limits.max_output_pdf_bytes,
                    ).sha256
                    == output_hash
                ):
                    paths.output.unlink()
            except (CompositionError, OSError, UnsafeInputError):
                raise CompositionError(
                    "RENDER_ROLLBACK_FAILED",
                    "render output rollback failed",
                ) from error
        if isinstance(error, CompositionError):
            raise
        raise CompositionError(
            "RENDER_COMMIT_FAILED", "render commit failed"
        ) from error


def render_bilingual_pdf_in_worker(
    *,
    source_pdf_path: str | Path,
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
    expected_finalization_receipt_hash: str,
    expected_overlay_plan_hash: str,
    job_root: str | Path,
    output_pdf_path: str | Path,
    render_manifest_path: str | Path,
    limits: WorkerLimits = DEFAULT_LIMITS,
) -> CompositionResult:
    """Render only through the audited Windows worker and commit verified bytes."""

    paths = _validate_output_paths(
        Path(job_root),
        Path(output_pdf_path),
        Path(render_manifest_path),
    )
    expected_manifest, expected_page_count = _expected_manifest_bindings(
        source=source,
        annotations=annotations,
        frame_graph=frame_graph,
        layout=layout,
        finalization_receipt=finalization_receipt,
        policy_inputs=policy_inputs,
        overlay_plan=overlay_plan,
        expected_finalization_receipt_hash=expected_finalization_receipt_hash,
        expected_overlay_plan_hash=expected_overlay_plan_hash,
        limits=limits,
    )
    payload = {
        "schema_version": "1.0.0",
        "operation": "render",
        "source": dict(source),
        "units": dict(units),
        "translation": dict(translation),
        "review": dict(review),
        "annotations": dict(annotations),
        "frame_graph": dict(frame_graph),
        "layout": dict(layout),
        "finalization_receipt": dict(finalization_receipt),
        "policy_inputs": dict(policy_inputs),
        "overlay_plan": dict(overlay_plan),
        "expected_finalization_receipt_hash": expected_finalization_receipt_hash,
        "expected_overlay_plan_hash": expected_overlay_plan_hash,
    }
    encoded = canonical_json_bytes(payload)
    safe_copy: SafeInputCopy | None = None
    result = None
    try:
        input_limits = replace(
            limits,
            max_input_bytes=limits.max_normalized_pdf_bytes,
        )
        safe_copy = copy_untrusted_input(Path(source_pdf_path), limits=input_limits)
        if safe_copy.sha256 != source.get("normalized_pdf_sha256"):
            raise CompositionError("SOURCE_PDF_HASH_MISMATCH", "source hash differs")
        _write_handoff(safe_copy.root, encoded, limits)
        request = WorkerRequest(
            operation="render",
            input_path="input.pdf",
            parameters={
                "policy_version": RENDER_POLICY_VERSION,
                "input_bytes": safe_copy.size,
                "normalized_pdf_sha256": safe_copy.sha256,
                "handoff_bytes": len(encoded),
                "handoff_sha256": hashlib.sha256(encoded).hexdigest(),
            },
        )
        result = _run_platform_worker(request, safe_copy.root, limits=limits)
        provenance = getattr(result, "provenance", None)
        if (
            not isinstance(provenance, dict)
            or provenance.get("appcontainer_cleanup_verified") is not True
        ):
            raise CompositionError(
                "SANDBOX_CONTRACT_UNVERIFIED",
                "render worker cleanup was not verified",
            )
    except (OSError, UnsafeInputError) as error:
        raise CompositionError(
            "RENDER_INPUT_INVALID", "render input rejected"
        ) from error
    finally:
        if safe_copy is not None:
            try:
                safe_copy.cleanup()
                try:
                    safe_copy.root.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise OSError("render staging root still exists")
            except OSError as cleanup_error:
                raise CompositionError(
                    "SANDBOX_CONTRACT_UNVERIFIED",
                    "render staging cleanup failed",
                ) from cleanup_error
    if result is None:
        raise CompositionError("RENDER_FAILED", "render worker returned no result")
    pdf_bytes = result.render_pdf_bytes
    if (
        not isinstance(pdf_bytes, bytes)
        or not pdf_bytes
        or len(pdf_bytes) > limits.max_output_pdf_bytes
    ):
        raise CompositionError("RENDER_FAILED", "rendered PDF is invalid")
    manifest = _decode_manifest(result.render_manifest_bytes, limits)
    _validate_manifest_bindings(
        manifest,
        expected_manifest,
        expected_page_count=expected_page_count,
    )
    manifest_hash = _commit_worker_outputs(
        paths=paths,
        pdf_bytes=pdf_bytes,
        manifest=manifest,
        limits=limits,
    )
    pages = manifest.get("pages")
    overlay_hash = manifest.get("overlay_pdf_sha256")
    if not isinstance(pages, list) or not isinstance(overlay_hash, str):
        raise CompositionError("RENDER_MANIFEST_INVALID", "manifest summary is invalid")
    return CompositionResult(
        output_pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        render_manifest_hash=manifest_hash,
        overlay_pdf_sha256=overlay_hash,
        page_count=len(pages),
    )


__all__ = ["render_bilingual_pdf_in_worker"]
