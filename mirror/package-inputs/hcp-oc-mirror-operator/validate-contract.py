#!/usr/bin/env python3
"""Validate the reviewed, non-secret SAIF operator bootstrap contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def validate(root: Path, contract: Path, expected_registry: str) -> dict:
    root = root.resolve()
    contract = contract.resolve()
    if not contract.is_relative_to(root):
        raise ValueError("operator contract is outside checkout")
    data = json.loads(contract.read_text())
    if (data.get("schemaVersion") != "hcp-oc-mirror-producer/v3"
            or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", data.get("release", {}).get("version", ""))
            or not re.fullmatch(r"[0-9a-f]{40}", data.get("release", {}).get("commit", ""))
            or data.get("destinationRegistry") != expected_registry
            or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?", expected_registry)):
        raise ValueError("operator contract release or site destination differs from runner")
    trust = data.get("trust", {})
    if trust.get("mode") == "bundle":
        ca_relative = trust.get("caBundle")
        if not isinstance(ca_relative, str) or not ca_relative.startswith("mirror/certs/"):
            raise ValueError("operator CA path is not in reviewed certificate area")
        ca = (root / ca_relative).resolve()
        if (not ca.is_relative_to(root / "mirror/certs") or not ca.is_file()
                or hashlib.sha256(ca.read_bytes()).hexdigest() != trust.get("caSha256")):
            raise ValueError("operator site CA differs from checked contract")
    elif trust != {"mode": "system"}:
        raise ValueError("operator trust must explicitly select system roots or a checked bundle")
    installation = data.get("installation", {})
    if (installation.get("method") != "olm" or not installation.get("package")
            or not installation.get("channel") or not installation.get("startingCSV")):
        raise ValueError("operator contract requires explicit OLM package/channel/CSV")
    images = data.get("images")
    if not isinstance(images, list):
        raise ValueError("operator image closure is missing")
    roles = [item.get("role") for item in images]
    if len(set(roles)) != len(roles) or not {"controller", "manager", "worker", "bundle", "catalog"} <= set(roles):
        raise ValueError("operator image closure is incomplete or duplicated")
    for item in images:
        digest = item.get("digest")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("operator image digest is not pinned")
        source = item.get("source", "")
        destination = item.get("destination", "")
        source_repo, separator, source_digest = source.rpartition("@")
        if (not separator or source_digest != digest or not source_repo
                or destination.split("/", 1)[0] != expected_registry
                or "/" not in destination
                or destination.split("/", 1)[1].rsplit(":", 1)[0] != source_repo
                or ":" not in destination.rsplit("/", 1)[-1]):
            raise ValueError("operator source or destination leaves reviewed release path")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--contract", type=Path,
                        default=Path(__file__).with_name("contract.json"))
    parser.add_argument("--expected-registry", required=True)
    parser.add_argument("--emit", choices=("check", "ca", "images"), default="check")
    args = parser.parse_args()
    data = validate(args.root, args.contract, args.expected_registry)
    if args.emit == "ca":
        print(data["trust"].get("caBundle", ""))
    elif args.emit == "images":
        for item in data["images"]:
            print("\t".join((item["source"], item["destination"], item["digest"])))
    else:
        print("Validated pinned OLM operator bootstrap contract")


if __name__ == "__main__":
    main()
