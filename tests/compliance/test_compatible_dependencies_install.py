# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import csv
import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import install_compatible_dependencies as installer
from scripts import package_compatible_dependencies as packager


def _wheel(name: str, version: str) -> bytes:
    prefix = name.replace("-", "_") + "-" + version + ".dist-info"
    files = {
        f"{installer.EXPECTED[name][2]}/__init__.py": b"# original package\n",
        prefix + "/METADATA": f"Name: {name}\nVersion: {version}\n".encode(),
        prefix + "/WHEEL": b"Wheel-Version: 1.0\n",
        prefix + "/licenses/LICENSE": b"Original upstream license text\n",
    }
    if name == "fonttools":
        files["fonttools-4.63.0.data/data/share/man/man1/ttx.1"] = b"Upstream manual\n"
    rows = []
    for member, content in files.items():
        digest = (
            base64.urlsafe_b64encode(
                bytes.fromhex(installer.integrity._digest(content))
            )
            .decode()
            .rstrip("=")
        )
        rows.append((member, "sha256=" + digest, str(len(content))))
    rows.append((prefix + "/RECORD", "", ""))
    record = io.StringIO()
    csv.writer(record).writerows(rows)
    files[prefix + "/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for member, content in files.items():
            archive.writestr(member, content)
    return output.getvalue()


@pytest.fixture
def inputs(tmp_path: Path):
    repo = tmp_path / "repo"
    house = tmp_path / "wheels"
    house.mkdir()
    (repo / "scripts").mkdir(parents=True)
    (repo / "compliance").mkdir()
    for filename in (
        "build_compatible_dependencies.py",
        "build_compatible_python.py",
        "install_compatible_dependencies.py",
        "package_compatible_dependencies.py",
    ):
        (repo / "scripts" / filename).write_text("# reviewed recipe\n")
    inventory = {
        "schema_version": 1,
        "sources": [{"name": "reviewed-source"}],
        "source_changes": [
            {"source": "freetype", "before_sha256": "a" * 64, "after_sha256": "b" * 64}
        ],
    }
    inventory_path = repo / "compliance/python-dependencies.json"
    inventory_path.write_text(json.dumps(inventory))
    wheels = []
    lock = []
    for name, (version, source_kind, _) in installer.EXPECTED.items():
        filename = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
        content = _wheel(name, version)
        (house / filename).write_bytes(content)
        digest = installer.integrity._digest(content)
        wheels.append(
            dict(
                name=name,
                version=version,
                source_kind=source_kind,
                filename=filename,
                size=len(content),
                sha256=digest,
            )
        )
        lock.append(f'''[[package]]
name = "{name}"
version = "{version}"
[[package.wheels]]
url = "https://files.pythonhosted.org/{filename}"
hash = "sha256:{digest}"
''')
    (repo / "uv.lock").write_text("\n".join(lock))
    manifest = dict(
        schema_version=1,
        sources=inventory["sources"],
        source_changes=inventory["source_changes"],
        wheels=wheels,
        recipe_sha256=installer.integrity._digest(
            (repo / "scripts/build_compatible_dependencies.py").read_bytes()
        ),
        shared_helpers_sha256=installer.integrity._digest(
            (repo / "scripts/build_compatible_python.py").read_bytes()
        ),
        source_inventory_sha256=installer.integrity._digest(
            inventory_path.read_bytes()
        ),
        lock_sha256=installer.integrity._digest((repo / "uv.lock").read_bytes()),
    )
    (house / installer.MANIFEST_NAME).write_text(json.dumps(manifest))
    return repo, house, manifest


def test_verified_wheelhouse_preserves_original_license_and_record(inputs):
    repo, house, manifest = inputs
    actual, contents = installer.validate_wheelhouse(house, repo_root=repo)
    assert actual == manifest
    assert set(contents) == set(installer.EXPECTED)
    assert all(
        any("/licenses/LICENSE" in member for member in files)
        for files in contents.values()
    )


@pytest.mark.parametrize(
    "change",
    [
        "bytes",
        "size",
        "extra",
        "missing",
        "name",
        "version",
        "kind",
        "duplicate",
        "path",
    ],
)
def test_wheelhouse_mutations_fail_closed(inputs, change):
    repo, house, manifest = inputs
    wheel = manifest["wheels"][0]
    if change == "bytes":
        (house / wheel["filename"]).write_bytes(b"changed")
    elif change == "size":
        wheel["size"] = True
    elif change == "extra":
        (house / "unreviewed.whl").write_bytes(b"unexpected")
    elif change == "missing":
        (house / wheel["filename"]).unlink()
    elif change == "name":
        wheel["name"] = "unreviewed"
    elif change == "version":
        wheel["version"] = "0.0.0"
    elif change == "kind":
        wheel["source_kind"] = "official-wheel"
    elif change == "duplicate":
        manifest["wheels"][1] = dict(wheel)
    else:
        wheel["filename"] = "../outside.whl"
    (house / installer.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        installer.validate_wheelhouse(house, repo_root=repo)


@pytest.mark.parametrize(
    "field",
    [
        "recipe_sha256",
        "shared_helpers_sha256",
        "source_inventory_sha256",
        "lock_sha256",
        "sources",
        "schema_version",
    ],
)
def test_manifest_cannot_self_attest_current_inputs(inputs, field):
    repo, house, manifest = inputs
    manifest[field] = True if field == "schema_version" else "changed"
    (house / installer.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        installer.validate_wheelhouse(house, repo_root=repo)


@pytest.mark.parametrize("change", ["missing", "before", "after", "extra"])
def test_source_patch_receipt_must_match_reviewed_inventory(inputs, change):
    repo, house, manifest = inputs
    if change == "missing":
        del manifest["source_changes"]
    elif change == "extra":
        manifest["source_changes"].append({"source": "unreviewed"})
    else:
        manifest["source_changes"][0][change + "_sha256"] = "c" * 64
    (house / installer.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="reviewed recipe/inputs"):
        installer.validate_wheelhouse(house, repo_root=repo)


def test_pure_wheel_must_match_original_lock_not_only_new_manifest(inputs):
    repo, house, manifest = inputs
    wheel = manifest["wheels"][1]
    # Zip metadata changes preserve every RECORD member, but are not original bytes.
    changed = (house / wheel["filename"]).read_bytes() + b"changed zip comment"
    (house / wheel["filename"]).write_bytes(changed)
    wheel.update(size=len(changed), sha256=installer.integrity._digest(changed))
    (house / installer.MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="upstream lock"):
        installer.validate_wheelhouse(house, repo_root=repo)


def test_record_mismatch_is_not_hidden_by_zip_integrity():
    raw = _wheel("pillow", "12.3.0")
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for name in source.namelist():
            target.writestr(
                name, b"changed" if name.endswith("__init__.py") else source.read(name)
            )
    with pytest.raises(ValueError, match="RECORD hash"):
        installer._members(output.getvalue(), "pillow", "12.3.0")


def _write_installed(venv, contents):
    for members in contents.values():
        for member, content in members.items():
            path = (
                venv / "share/man/man1/ttx.1"
                if member == "fonttools-4.63.0.data/data/share/man/man1/ttx.1"
                else venv / "Lib/site-packages" / member
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.endswith(".dist-info/RECORD"):
                content = content.replace(
                    b"fonttools-4.63.0.data/data/share/man/man1/ttx.1",
                    b"../../share/man/man1/ttx.1",
                )
            path.write_bytes(content)


def test_install_uses_only_exact_local_hashed_wheels_and_verifies_receipt(
    inputs, tmp_path, monkeypatch
):
    repo, house, manifest = inputs
    venv = tmp_path / "fresh-venv"
    venv.mkdir()
    _, contents = installer.validate_wheelhouse(house, repo_root=repo)
    identity = {"python_version": "3.12.14", "runtime_manifest_sha256": "1" * 64}
    monkeypatch.setattr(installer, "_environment", lambda *args: (venv, identity))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        requirements = Path(command[-1]).read_text()
        for wheel in manifest["wheels"]:
            assert (house / wheel["filename"]).as_uri() in requirements
            assert "--hash=sha256:" + wheel["sha256"] in requirements
        _write_installed(venv, contents)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    python = venv / "Scripts/python.exe"
    receipt = installer.install(house, python, "reviewed-uv", repo_root=repo)
    assert len(calls) == 1
    for option in (
        "--offline",
        "--no-index",
        "--no-deps",
        "--no-build",
        "--require-hashes",
        "--reinstall",
    ):
        assert option in calls[0]
    assert (
        installer.install(house, python, "unused", verify_only=True, repo_root=repo)
        == receipt
    )
    assert len(calls) == 1
    assert str(tmp_path) not in json.dumps(receipt)
    (venv / "Lib/site-packages/PIL/__init__.py").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="installed wheel member"):
        installer.install(house, python, "unused", verify_only=True, repo_root=repo)


@pytest.mark.parametrize(
    "extra",
    ["fontTools/stale.pyd", "pillow.libs/extra.dll", "PIL/__pycache__/extra.pyd"],
)
def test_stale_native_extensions_are_rejected(inputs, tmp_path, extra):
    repo, house, _ = inputs
    _, contents = installer.validate_wheelhouse(house, repo_root=repo)
    venv = tmp_path / "venv"
    _write_installed(venv, contents)
    extra_path = venv / "Lib/site-packages" / extra
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_bytes(b"stale")
    with pytest.raises(ValueError, match="unexpected installed package file"):
        installer.verify_installed(venv, contents)


def test_installed_record_cannot_omit_a_runtime_member(inputs, tmp_path):
    repo, house, _ = inputs
    _, contents = installer.validate_wheelhouse(house, repo_root=repo)
    venv = tmp_path / "venv"
    _write_installed(venv, contents)
    record = venv / "Lib/site-packages/pillow-12.3.0.dist-info/RECORD"
    record.write_text("\n".join(record.read_text().splitlines()[1:]) + "\n")
    with pytest.raises(ValueError, match="RECORD"):
        installer.verify_installed(venv, contents)


def test_receipt_hardlink_cannot_overwrite_another_file(inputs, tmp_path, monkeypatch):
    repo, house, _ = inputs
    venv = tmp_path / "venv"
    venv.mkdir()
    outside = tmp_path / "preserve.txt"
    outside.write_bytes(b"preserve")
    os.link(outside, venv / installer.RECEIPT_NAME)
    monkeypatch.setattr(installer, "_environment", lambda *args: (venv, {}))

    def forbidden(*args, **kwargs):
        pytest.fail("unsafe receipt must be rejected before package installation")

    monkeypatch.setattr(installer.subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        installer.install(house, venv / "Scripts/python.exe", "uv", repo_root=repo)
    assert outside.read_bytes() == b"preserve"


def test_system_python_is_not_an_installation_target(tmp_path):
    with pytest.raises(ValueError, match="virtual-environment"):
        installer._environment(tmp_path / "python.exe", tmp_path)


def test_package_contains_exact_verified_wheels_and_portable_receipt(
    inputs, tmp_path, monkeypatch
):
    repo, house, manifest = inputs
    receipt = {
        "schema_version": 1,
        "build_manifest_sha256": installer.integrity._digest(
            (house / installer.MANIFEST_NAME).read_bytes()
        ),
        "runtime_manifest_sha256": "1" * 64,
        "wheels": manifest["wheels"],
    }
    calls = []

    def verify(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["verify_only"] is True
        return receipt

    monkeypatch.setattr(packager.dependencies, "install", verify)
    output = tmp_path / "artifacts"
    archive = packager.package_dependencies(
        house, tmp_path / "venv/Scripts/python.exe", output, repo_root=repo
    )
    artifact = json.loads((output / "dependencies-artifact.json").read_text())
    assert artifact["archive"]["sha256"] == installer.integrity._digest(
        archive.read_bytes()
    )
    assert artifact["runtime_manifest_sha256"] == receipt["runtime_manifest_sha256"]
    with zipfile.ZipFile(archive) as handle:
        assert len(handle.namelist()) == 5
        assert json.loads(handle.read("dependency-installation.json")) == receipt
        for wheel in manifest["wheels"]:
            assert (
                handle.read("compatible-dependencies/" + wheel["filename"])
                == (house / wheel["filename"]).read_bytes()
            )
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in handle.infolist()
        )
    repeated = packager.package_dependencies(
        house, tmp_path / "venv/Scripts/python.exe", tmp_path / "again", repo_root=repo
    )
    assert repeated.read_bytes() == archive.read_bytes()
    assert len(calls) == 2
    with pytest.raises(FileExistsError):
        packager.package_dependencies(
            house, tmp_path / "python.exe", output, repo_root=repo
        )


def test_package_is_not_created_without_installed_verification(
    inputs, tmp_path, monkeypatch
):
    repo, house, _ = inputs

    def fail(*args, **kwargs):
        raise ValueError("installed bytes differ")

    monkeypatch.setattr(packager.dependencies, "install", fail)
    output = tmp_path / "blocked"
    with pytest.raises(ValueError, match="installed bytes"):
        packager.package_dependencies(
            house, tmp_path / "python.exe", output, repo_root=repo
        )
    assert not output.exists()
