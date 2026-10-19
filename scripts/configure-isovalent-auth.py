#!/usr/bin/env python3
"""Build an ephemeral Docker auth file without exposing registry credentials."""

from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path


ISOVALENT_HOSTS = (
    "containers.isovalent.com",
    "registry.isovalent.com",
    "charts.isovalent.com",
    "artifactory.isovalent.com",
)


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"required environment variable is not set: {name}")
    return value


def build_config(redhat_pull_secret: str, username: str, token: str) -> dict:
    try:
        config = json.loads(redhat_pull_secret)
    except json.JSONDecodeError as exc:
        raise SystemExit("REDHAT_PULL_SECRET is not valid JSON") from exc
    if not isinstance(config, dict):
        raise SystemExit("REDHAT_PULL_SECRET must contain a JSON object")

    auths = config.setdefault("auths", {})
    if not isinstance(auths, dict):
        raise SystemExit("REDHAT_PULL_SECRET.auths must be a JSON object")

    encoded = base64.b64encode(f"{username}:{token}".encode()).decode()
    for host in ISOVALENT_HOSTS:
        auths[host] = {"auth": encoded}
    return config


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary_name, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    config = build_config(
        required_env("REDHAT_PULL_SECRET"),
        required_env("ISOVALENT_REGISTRY_USERNAME"),
        required_env("ISOVALENT_REGISTRY_TOKEN"),
    )
    write_private_json(args.output, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
