#!/usr/bin/env python3
"""Validate the bounded Portworx air-gap image inventory."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import yaml


EXPECTED_COUNT = 38
EXPECTED_ORDERED_INVENTORY_SHA256 = (
    "a650d93cce4bffd15177d08969acadc9701a24799aa6b7583b0ce61dc01d457e"
)
EXPECTED_HELPERS = {
    "registry.k8s.io/kube-scheduler-amd64:v1.32.10":
        "sha256:88c492275c6f99b21e219407a562b6be4448d6a69c537e4fee91abe2489ba154",
    "registry.k8s.io/kube-controller-manager-amd64:v1.32.10":
        "sha256:2e1338a9181832f3d23df0dbe45f3170fbc71b2f791131b4a0327beb8d19c28f",
}


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    images = document.get("mirror", {}).get("additionalImages", [])
    refs = [item.get("name") for item in images if isinstance(item, dict)]

    if len(refs) != EXPECTED_COUNT:
        errors.append(f"expected {EXPECTED_COUNT} images, found {len(refs)}")
    if len(refs) != len(set(refs)):
        errors.append("image inventory contains duplicate references")

    inventory_sha256 = hashlib.sha256("\n".join(
        ref if isinstance(ref, str) else repr(ref) for ref in refs
    ).encode("utf-8")).hexdigest()
    if inventory_sha256 != EXPECTED_ORDERED_INVENTORY_SHA256:
        errors.append(
            "ordered image inventory differs from the approved exact 38-reference contract: "
            f"expected sha256:{EXPECTED_ORDERED_INVENTORY_SHA256}, "
            f"found sha256:{inventory_sha256}"
        )

    digest_pattern = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
    for ref in refs:
        if not isinstance(ref, str) or not digest_pattern.fullmatch(ref):
            errors.append(f"image is not digest-pinned: {ref!r}")

    by_tag = {ref.rsplit("@", 1)[0]: ref.rsplit("@", 1)[1] for ref in refs if isinstance(ref, str) and "@" in ref}
    for tagged_ref, digest in EXPECTED_HELPERS.items():
        if by_tag.get(tagged_ref) != digest:
            errors.append(f"missing exact helper {tagged_ref}@{digest}")

    if any(":v1.32.0@" in ref for ref in refs if isinstance(ref, str)):
        errors.append("obsolete Kubernetes v1.32.0 helper remains in inventory")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("mirror/portworx-images.yaml"))
    args = parser.parse_args()
    errors = validate(args.config)
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"validated {EXPECTED_COUNT} digest-pinned Portworx images")


if __name__ == "__main__":
    main()
