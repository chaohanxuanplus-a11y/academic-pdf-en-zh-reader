# SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ALLOWED_LICENSES = {
    "Apache-2.0 OR BSD-2-Clause",
    "Apache-2.0 OR BSD-3-Clause",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "PSF-2.0",
}
BLOCKED_LICENSE_TOKENS = (
    "AGPL",
    "GPL",
    "LGPL",
    "MPL",
    "NC",
    "ND",
    "NOASSERTION",
    "SSPL",
    "UNKNOWN",
)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate(root: Path) -> tuple[str, ...]:
    errors: list[str] = []
    inventory_path = root / "compliance" / "dependencies.json"
    lock_path = root / "uv.lock"
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return (f"cannot read dependency evidence: {exc}",)

    if inventory.get("schema_version") != 1:
        errors.append("dependency inventory schema_version must be 1")

    records: dict[tuple[str, str], dict[str, object]] = {}
    for item in inventory.get("dependencies", []):
        if not isinstance(item, dict):
            errors.append("dependency records must be objects")
            continue
        key = (str(item.get("name", "")).casefold(), str(item.get("version", "")))
        if key in records:
            errors.append(f"duplicate dependency record: {key}")
        records[key] = item

        license_expression = str(item.get("license", ""))
        if license_expression not in ALLOWED_LICENSES:
            errors.append(f"unapproved license for {key}: {license_expression}")
        upper_license = license_expression.upper()
        if any(token in upper_license for token in BLOCKED_LICENSE_TOKENS):
            errors.append(f"blocked license token for {key}: {license_expression}")
        for field in ("source", "license_source"):
            value = item.get(field)
            if not isinstance(value, str) or not value.startswith("https://"):
                errors.append(f"{field} must be an https URL for {key}")
        if item.get("integrity_source") != "uv.lock":
            errors.append(f"integrity_source must be uv.lock for {key}")

    locked: dict[tuple[str, str], dict[str, object]] = {}
    for package in lock.get("package", []):
        source = package.get("source", {})
        if not isinstance(source, dict) or not source.get("registry"):
            continue
        key = (str(package["name"]).casefold(), str(package["version"]))
        locked[key] = package
        artifacts: list[dict[str, object]] = []
        sdist = package.get("sdist")
        if isinstance(sdist, dict):
            artifacts.append(sdist)
        wheels = package.get("wheels", [])
        if isinstance(wheels, list):
            artifacts.extend(item for item in wheels if isinstance(item, dict))
        if not artifacts:
            errors.append(f"locked package has no hashed artifact: {key}")
        for artifact in artifacts:
            if not SHA256_RE.fullmatch(str(artifact.get("hash", ""))):
                errors.append(f"invalid artifact hash for {key}")

    if set(records) != set(locked):
        missing = sorted(set(locked) - set(records))
        extra = sorted(set(records) - set(locked))
        errors.append(f"inventory/lock mismatch; missing={missing}, extra={extra}")

    return tuple(errors)


def main() -> int:
    errors = validate(Path.cwd())
    if not errors:
        print("dependency policy: OK")
        return 0
    print("dependency policy: FAILED")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
