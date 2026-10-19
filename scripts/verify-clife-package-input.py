#!/usr/bin/env python3
"""Verify the exact SAIF input bundle promoted into sys-admin."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml


EXPECTED_API_VERSION = "packaging.saif.example.com/v1alpha1"
EXPECTED_KIND = "PackageInputPromotion"
EXPECTED_ARTIFACTS = {
    "contract": ("contract.yaml", None),
    "schema": ("contract.schema.json", "schemas/clife-package-contract-v1.schema.json"),
    "validator": ("validate-contract.py", "scripts/validate-clife-package-contract.py"),
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    require(
        actual == expected,
        f"{label} keys differ: expected={sorted(expected)}, actual={sorted(actual)}",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(
    bundle: Path,
    repository: str,
    revision: str,
    contract_path: str,
) -> dict[str, Path]:
    provenance_path = bundle / "provenance.yaml"
    require(provenance_path.is_file(), f"missing package input provenance: {provenance_path}")
    expected_files = {"provenance.yaml"} | {
        local_name for local_name, _ in EXPECTED_ARTIFACTS.values()
    }
    actual_files = {path.name for path in bundle.iterdir() if path.is_file()}
    require(actual_files == expected_files, "promoted package input contains unexpected or missing files")
    document = yaml.safe_load(provenance_path.read_text())
    require(isinstance(document, dict), "package input provenance must be a mapping")
    require_exact_keys(document, {"apiVersion", "kind", "metadata", "spec"}, "provenance")
    require(document["apiVersion"] == EXPECTED_API_VERSION, "unexpected provenance apiVersion")
    require(document["kind"] == EXPECTED_KIND, "unexpected provenance kind")

    metadata = document["metadata"]
    spec = document["spec"]
    require(isinstance(metadata, dict), "provenance metadata must be a mapping")
    require_exact_keys(metadata, {"name"}, "provenance.metadata")
    require(metadata["name"] == Path(contract_path).stem, "unexpected promoted package name")
    require(isinstance(spec, dict), "provenance spec must be a mapping")
    require_exact_keys(spec, {"source", "artifacts"}, "provenance.spec")

    source = spec["source"]
    require(isinstance(source, dict), "provenance source must be a mapping")
    require_exact_keys(source, {"repository", "revision"}, "provenance.spec.source")
    require(
        source["repository"] == repository,
        "promoted source repository does not match requested contract",
    )
    require(
        source["revision"] == revision,
        "promoted source revision does not match requested contract",
    )
    require(bool(re.fullmatch(r"[0-9a-f]{40}", revision)), "source revision must be a full Git commit hash")

    artifacts = spec["artifacts"]
    require(isinstance(artifacts, dict), "provenance artifacts must be a mapping")
    require_exact_keys(artifacts, set(EXPECTED_ARTIFACTS), "provenance.spec.artifacts")
    verified: dict[str, Path] = {}
    for name, (local_name, fixed_source_path) in EXPECTED_ARTIFACTS.items():
        artifact = artifacts[name]
        require(isinstance(artifact, dict), f"artifact {name} must be a mapping")
        require_exact_keys(artifact, {"sourcePath", "localPath", "sha256"}, f"artifact {name}")
        require(artifact["localPath"] == local_name, f"artifact {name} has unexpected localPath")
        source_path = contract_path if fixed_source_path is None else fixed_source_path
        require(artifact["sourcePath"] == source_path, f"artifact {name} has unexpected sourcePath")
        expected_digest = artifact["sha256"]
        require(
            isinstance(expected_digest, str) and SHA256.fullmatch(expected_digest),
            f"artifact {name} has invalid SHA-256",
        )
        path = bundle / local_name
        require(path.is_file(), f"promoted artifact is missing: {path}")
        require(sha256(path) == expected_digest, f"promoted artifact hash differs: {name}")
        verified[name] = path
    return verified


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--contract-path", required=True)
    args = parser.parse_args()
    verified = verify(args.bundle, args.repository, args.revision, args.contract_path)
    print(
        "verified promoted CLIFE input: "
        + ", ".join(f"{name}={path}" for name, path in verified.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
