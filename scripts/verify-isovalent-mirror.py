#!/usr/bin/env python3
"""Verify selected mirrored manifest identities using Skopeo auth-file and TLS."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import ssl
import urllib.parse
import yaml

spec = importlib.util.spec_from_file_location("selected_release", Path(__file__).with_name("inspect-isovalent-release.py"))
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


def manifest_digest(registry, repository, reference, auth_file=None, cert_dir=None, insecure=False):
    if insecure:
        raise ValueError("TLS verification cannot be disabled")
    url = urllib.parse.urlsplit(registry)
    if url.scheme != "https" or not url.netloc or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
        raise ValueError("registry must be a normal HTTPS authority")
    if not auth_file or not Path(auth_file).is_file() or Path(auth_file).stat().st_mode & 0o077:
        raise ValueError("a private mode0600 registry auth file is required")
    args = ["skopeo", "inspect", "--raw", "--tls-verify=true", "--authfile", str(auth_file)]
    if cert_dir:
        if not Path(cert_dir).is_dir():
            raise ValueError("registry certificate directory is missing")
        args += ["--cert-dir", str(cert_dir)]
    args += ["docker://" + url.netloc + "/" + repository + ("@" if reference.startswith("sha256:") else ":") + reference]
    result = subprocess.run(args, capture_output=True, timeout=120)
    if result.returncode:
        # Registry/client stderr can contain sensitive runtime diagnostics.
        raise ValueError("authenticated registry manifest verification failed")
    return "sha256:" + hashlib.sha256(result.stdout).hexdigest()


def load_contract(path):
    value = yaml.safe_load(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("release contract must be a YAML mapping")
    return value


def verify_mirror(contract, registry, auth_file=None, cert_dir=None):
    bundle = contract.get("clifeBundle", {})
    if not bundle.get("controllerDigest") or not bundle.get("controllerMirrorRepository"):
        raise SystemExit("release contract is incomplete: CLIFE controller mirror identity")
    images = [*contract.get("operands", []), *contract.get("runtimeImages", []),
              {"name": "clife-controller", "digest": bundle["controllerDigest"], "mirrorRepository": bundle["controllerMirrorRepository"]}]
    records = []
    for image in images:
        expected, repository = image.get("digest"), image.get("mirrorRepository")
        if not expected or not repository:
            raise ValueError("release contract has an incomplete mirror image identity")
        observed = manifest_digest(registry, repository, expected, auth_file, cert_dir)
        if observed != expected:
            raise ValueError("selected mirror manifest digest mismatch")
        records.append({"name": image.get("name"), "mirror": urllib.parse.urlsplit(registry).netloc + "/" + repository,
                        "reference": expected, "digest": observed})
    return records


def verify_runtime_images(images, registry, auth_file, cert_dir):
    """Compare raw source and mirror manifest bytes; never invent tag digests."""
    records = []
    for image in images:
        host, repository, reference = selection.split_image(image)
        expected = manifest_digest("https://" + host, repository, reference, auth_file)
        if reference.startswith("sha256:") and expected != reference:
            raise ValueError("selected source manifest digest mismatch")
        observed = manifest_digest(registry, repository, reference, auth_file, cert_dir)
        if expected != observed:
            raise ValueError("selected runtime source/mirror manifest mismatch")
        records.append({"source": image, "mirror": urllib.parse.urlsplit(registry).netloc + "/" + repository,
                        "reference": reference, "digest": observed})
    return records


def main():
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--contract", type=Path)
    choice.add_argument("--selection", type=Path)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--cert-dir", type=Path, help="Skopeo certificate directory with the site's normal registry CA")
    parser.add_argument("--output", type=Path, help="Non-secret selected-input verification receipt")
    args = parser.parse_args()
    if args.output:
        args.output.unlink(missing_ok=True)
    selected = selection.load_selection(args.selection) if args.selection else None
    if selected and selected.get("destination"):
        destination = selected["destination"]
        if args.registry.rstrip("/") != "https://" + destination["registry"]:
            raise ValueError("destination differs from selected registry")
        selected_ca = Path(destination["caBundle"]).read_bytes() if destination["caBundle"] else b"".join(
            ssl.DER_cert_to_PEM_cert(cert).encode()
            for cert in ssl.create_default_context().get_ca_certs(binary_form=True))
        actual_ca = args.cert_dir / "ca.crt" if args.cert_dir else None
        if not actual_ca or not actual_ca.is_file() or actual_ca.read_bytes() != selected_ca:
            raise ValueError("destination CA differs from selected CA")
    contracts = [i["contract"] for i in selected["contracts"]] if selected else [load_contract(args.contract)]
    records = verify_runtime_images(selected["runtimeImages"], args.registry, args.auth_file, args.cert_dir) if selected else []
    release_records = []
    for contract in contracts:
        release_records.extend(verify_mirror(contract, args.registry, args.auth_file, args.cert_dir))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"selectionSha256": selected["sha256"] if selected else None,
            "inputs": selected["inputs"] if selected else [str(args.contract)], "registry": args.registry,
            "runtimeImages": records, "releaseImages": release_records,
            "qualification": "authenticated selected manifest identities; not full blob/cold-pull proof"}, indent=2) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
