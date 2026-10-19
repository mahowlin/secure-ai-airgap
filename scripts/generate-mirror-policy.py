#!/usr/bin/env python3
"""Generate IDMS/ITMS mirror policies from oc-mirror ImageSetConfiguration input."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml


def read_yaml(path: Path) -> dict:
    with path.open() as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping in {path}")
    return data


def read_optional_yaml(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    return read_yaml(path)


def image_source_prefix(image: str) -> str | None:
    image = image.removeprefix("docker://")
    image = image.split("@", 1)[0]
    image = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    parts = image.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[:2])


def mirror_for_source(source: str, local_registry: str, mirror_overrides: dict[str, str]) -> str:
    if source in mirror_overrides:
        return f"{local_registry.rstrip('/')}/{mirror_overrides[source].strip('/')}"
    parts = source.split("/")
    if len(parts) < 2:
        raise ValueError(f"cannot derive mirror path for source {source!r}")
    return f"{local_registry.rstrip('/')}/{parts[1]}"


def collect_sources(config: dict, extra_sources: list[str]) -> list[str]:
    mirror = config.get("mirror", {}) or {}
    sources: set[str] = set(extra_sources)

    for image in mirror.get("additionalImages", []) or []:
        image_name = image.get("name") if isinstance(image, dict) else None
        if not image_name:
            continue
        source = image_source_prefix(image_name)
        if source:
            sources.add(source)

    return sorted(sources)


def resource_name(base_name: str, kind: str) -> str:
    name = re.sub(r"[^a-z0-9.-]+", "-", base_name.lower()).strip("-.")
    if not name:
        raise ValueError("resource name cannot be empty")
    return name[:253]


def build_policy(kind: str, name: str, sources: list[str], local_registry: str, mirror_overrides: dict[str, str]) -> dict:
    entry_key = "imageDigestMirrors" if kind == "ImageDigestMirrorSet" else "imageTagMirrors"
    return {
        "apiVersion": "config.openshift.io/v1",
        "kind": kind,
        "metadata": {
            "name": name,
            "annotations": {
                "createdBy": "sync-images workflow",
            },
        },
        "spec": {
            entry_key: [
                {
                    "mirrors": [mirror_for_source(source, local_registry, mirror_overrides)],
                    "source": source,
                }
                for source in sources
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--local-registry", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-config", type=Path, help="Optional YAML file with additional source prefixes keyed by config path")
    parser.add_argument("--kinds", nargs="+", default=["ImageDigestMirrorSet", "ImageTagMirrorSet"], choices=["ImageDigestMirrorSet", "ImageTagMirrorSet"])
    args = parser.parse_args()

    config = read_yaml(args.config)
    source_config = read_optional_yaml(args.source_config)
    extra_sources = source_config.get("configs", {}).get(str(args.config), {}).get("sources", [])
    mirror_overrides = source_config.get("mirrors", {})
    sources = collect_sources(config, extra_sources)
    if not sources:
        raise SystemExit(f"no mirror sources derived from {args.config}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for kind in args.kinds:
        prefix = "idms" if kind == "ImageDigestMirrorSet" else "itms"
        path = args.output_dir / f"{prefix}-{args.name}.yaml"
        document = build_policy(kind, resource_name(args.name, kind), sources, args.local_registry, mirror_overrides)
        with path.open("w") as stream:
            yaml.safe_dump(document, stream, sort_keys=False)
        print(f"Generated {kind} {path} with sources: {', '.join(sources)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
