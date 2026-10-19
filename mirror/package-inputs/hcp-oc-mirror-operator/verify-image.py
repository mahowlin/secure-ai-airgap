#!/usr/bin/env python3
"""Copy only an absent pinned root and verify its entire normal-TLS OCI graph.

The descriptor/hash walk follows the SAIF hcp_image_audit.py pattern, without
its platform filter or small-blob limit. No credentials or TLS policy live here.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


INDEX = {"application/vnd.oci.image.index.v1+json",
         "application/vnd.docker.distribution.manifest.list.v2+json"}
IMAGE = {"application/vnd.oci.image.manifest.v1+json",
         "application/vnd.docker.distribution.manifest.v2+json"}
CONFIG = {"application/vnd.oci.image.config.v1+json",
          "application/vnd.docker.container.image.v1+json"}
LAYERS = {"application/vnd.oci.image.layer.v1.tar",
          "application/vnd.oci.image.layer.v1.tar+gzip",
          "application/vnd.oci.image.layer.v1.tar+zstd",
          "application/vnd.docker.image.rootfs.diff.tar.gzip"}
ATTESTATION = "application/vnd.docker.attestation.manifest.v1+json"
EMPTY = "application/vnd.oci.empty.v1+json"
IN_TOTO = "application/vnd.in-toto+json"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def descriptor(item):
    if (not isinstance(item, dict) or not DIGEST.fullmatch(item.get("digest", ""))
            or type(item.get("size")) is not int or item["size"] < 0
            or not isinstance(item.get("mediaType"), str)):
        raise ValueError("invalid descriptor")
    return item


class Crane:
    def manifest(self, ref, missing_ok=False):
        result = subprocess.run(["crane", "manifest", ref], capture_output=True)
        if result.returncode:
            # Only a registry's explicit missing-manifest response permits copy.
            error = result.stderr.decode(errors="replace")
            if missing_ok and re.search(r"\b(MANIFEST_UNKNOWN|NAME_UNKNOWN)\b|404 Not Found", error):
                return None
            raise RuntimeError(f"manifest read failed for {ref}: {error.strip()}")
        return result.stdout  # Crane0.22 manifest uses Fprint: exact raw bytes.

    def blob(self, repo, item, capture=False):
        if capture and item["size"] > 8 * 1024 * 1024:
            raise ValueError("JSON blob exceeds bounded parser size")
        # Inherit stderr; never buffer a potentially large blob in memory.
        with subprocess.Popen(["crane", "blob", repo + "@" + item["digest"]],
                              stdout=subprocess.PIPE) as process:
            sha, size, chunks = hashlib.sha256(), 0, []
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                sha.update(chunk)
                size += len(chunk)
                if size > item["size"]:
                    process.kill()
                    raise ValueError("blob size/digest mismatch: " + item["digest"])
                if capture:
                    if size > 8 * 1024 * 1024:
                        process.kill()
                        raise ValueError("JSON blob exceeds bounded parser size")
                    chunks.append(chunk)
            if process.wait() or size != item["size"] or "sha256:" + sha.hexdigest() != item["digest"]:
                raise ValueError("missing blob or blob size/digest mismatch: " + item["digest"])
        return b"".join(chunks)

    def validate(self, ref):
        subprocess.run(["crane", "validate", "--remote", ref], check=True)

    def copy(self, source, target):
        subprocess.run(["crane", "copy", "--jobs", "1", "--no-clobber", source, target], check=True)


def tree(client, repo, root):
    nodes = {}

    def visit(sha, parent=None, depth=0):
        if depth > 8 or len(nodes) > 256:
            raise ValueError("image graph exceeds bootstrap bound")
        raw = client.manifest(repo + "@" + sha)
        if digest(raw) != sha or (parent and len(raw) != parent["size"]):
            raise ValueError("manifest size/digest mismatch")
        value = json.loads(raw)
        media = value.get("mediaType")
        if value.get("schemaVersion") != 2 or media not in INDEX | IMAGE:
            raise ValueError("unknown manifest shape")
        if parent and media != parent["mediaType"]:
            raise ValueError("manifest media type mismatch")
        if sha in nodes:
            return
        value["_verifiedSize"] = len(raw)
        nodes[sha] = value
        if media in INDEX:
            if value.get("subject") or value.get("artifactType"):
                raise ValueError("unknown index artifact shape")
            if not isinstance(value.get("manifests"), list) or not value["manifests"]:
                raise ValueError("empty or malformed index")
            for child in value["manifests"]:
                descriptor(child)
                visit(child["digest"], child, depth + 1)
        else:
            descriptor(value.get("config"))
            if not isinstance(value.get("layers"), list):
                raise ValueError("malformed image layers")
            for layer in value["layers"]:
                descriptor(layer)

    if not DIGEST.fullmatch(root):
        raise ValueError("expected root must be sha256 pinned")
    visit(root)
    return nodes


def shapes(nodes):
    """Classify only declared attestations; unknown artifacts fail closed."""
    platforms, annotations = {}, {}
    for value in nodes.values():
        for child in value.get("manifests", []):
            sha = child["digest"]
            candidate = (child.get("platform"), child.get("annotations", {}))
            if sha in platforms and (platforms[sha], annotations[sha]) != candidate:
                raise ValueError("conflicting child classification")
            platforms[sha], annotations[sha] = candidate
    runnable, attestations = [], {}
    for sha, value in nodes.items():
        if value["mediaType"] in INDEX:
            continue
        marked = annotations.get(sha, {}).get("vnd.docker.reference.type")
        if value.get("artifactType") == ATTESTATION:
            subject = descriptor(value.get("subject"))
            target = nodes.get(subject["digest"])
            if (marked != "attestation-manifest" or platforms.get(sha) != {"os": "unknown", "architecture": "unknown"}
                    or annotations[sha].get("vnd.docker.reference.digest") != subject["digest"]
                    or not target or target.get("artifactType") or target["mediaType"] not in IMAGE
                    or subject["mediaType"] != target["mediaType"]
                    or subject["size"] != target["_verifiedSize"]
                    or value["config"]["mediaType"] != EMPTY or not value["layers"]
                    or any(layer["mediaType"] != IN_TOTO for layer in value["layers"])):
                raise ValueError("invalid attestation subject or shape")
            attestations[sha] = subject["digest"]
        else:
            if (value.get("artifactType") or value.get("subject") or marked
                    or value["config"]["mediaType"] not in CONFIG
                    or any(layer["mediaType"] not in LAYERS for layer in value["layers"])):
                raise ValueError("unknown or malformed runnable image")
            runnable.append(sha)
    if not runnable:
        raise ValueError("graph has no runnable image")
    return runnable, attestations, platforms


def verify(client, repo, root):
    nodes = tree(client, repo, root)
    runnable, attestations, platforms = shapes(nodes)
    receipts, configs = {}, {}
    for sha, value in nodes.items():
        if value["mediaType"] in INDEX:
            continue
        for item in [value["config"], *value["layers"]]:
            previous = receipts.get(item["digest"])
            if previous and previous["size"] != item["size"]:
                raise ValueError("conflicting blob size")
            capture = item == value["config"] or sha in attestations
            raw = client.blob(repo, item, capture)
            receipts[item["digest"]] = {"digest": item["digest"], "size": item["size"], "verified": True}
            if item == value["config"]:
                configs[sha] = json.loads(raw)
                if sha in attestations and configs[sha] != {}:
                    raise ValueError("invalid attestation empty config")
            elif sha in attestations:
                statement = json.loads(raw)
                subjects = statement.get("subject", [])
                if (statement.get("_type") != "https://in-toto.io/Statement/v1"
                        or not subjects or any(s.get("digest") != {"sha256": attestations[sha].split(":")[1]} for s in subjects)
                        or not isinstance(statement.get("predicateType"), str)
                        or not statement["predicateType"].startswith("https://")
                        or statement["predicateType"] != item.get("annotations", {}).get("in-toto.io/predicate-type")):
                    raise ValueError("wrong attestation statement subject or predicate")
    for sha in runnable:
        config = configs[sha]
        platform = platforms.get(sha)
        if (config.get("rootfs", {}).get("type") != "layers" or not config.get("os") or not config.get("architecture")
                or (platform and any(platform.get(k) != config.get(k) for k in ("os", "architecture")))):
            raise ValueError("malformed runnable config or platform")
        client.validate(repo + "@" + sha)
    return {"root": root, "manifests": [{"digest": sha, "size": value["_verifiedSize"],
                                         "mediaType": value["mediaType"]} for sha, value in nodes.items()],
            "blobs": list(receipts.values()),
            "runnable": runnable, "attestations": attestations,
            "platforms": [{"os": configs[sha]["os"], "architecture": configs[sha]["architecture"]} for sha in runnable],
            "fullBytesVerified": True}


def ensure_image(client, source, target, expected):
    source_repo, source_digest = source.rsplit("@", 1)
    target_repo = target.rsplit(":", 1)[0]
    if source_digest != expected:
        raise ValueError("source differs from expected digest")
    existing = client.manifest(target, missing_ok=True)
    copied, fallback = False, {}
    if existing is not None:
        if digest(existing) != expected:
            raise ValueError("existing destination tag conflicts; preserve it")
    else:
        nodes = tree(client, source_repo, expected)
        _, attestations, _ = shapes(nodes)
        for sha, subject in attestations.items():
            tag = subject.replace(":", "-", 1)
            fallback.setdefault(tag, set()).add(sha)
        # Crane's --no-clobber protects the requested tag, NOT fallback tags.
        # Preserve every preexisting fallback, even one we recognize.
        for tag in fallback:
            if client.manifest(target_repo + ":" + tag, missing_ok=True) is not None:
                raise ValueError("preexisting referrer fallback tag; preserve it: " + tag)
        client.copy(source, target)
        copied = True
    current = client.manifest(target)
    if digest(current) != expected:
        raise ValueError("destination tag drift")
    result = verify(client, target_repo, expected)
    for tag, children in fallback.items():
        raw = client.manifest(target_repo + ":" + tag)
        value = json.loads(raw)
        entries = value.get("manifests", [])
        expected = {(sha, nodes[sha]["_verifiedSize"], nodes[sha]["mediaType"]) for sha in children}
        if (value.get("schemaVersion") != 2 or value.get("mediaType") != "application/vnd.oci.image.index.v1+json"
                or len(entries) != len(expected)
                or {(item["digest"], item["size"], item["mediaType"]) for item in entries} != expected):
            raise ValueError("unexpected referrer fallback contents")
    result.update(destination=target, copyPerformed=copied,
                  permittedNewFallbackTags=sorted(fallback))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "destination", "digest", "receipt"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        result = ensure_image(Crane(), args.source, args.destination, args.digest)
    except Exception as error:
        Path(args.receipt).write_text(json.dumps({"destination": args.destination,
            "root": args.digest, "fullBytesVerified": False, "error": str(error)}, indent=2) + "\n")
        raise
    Path(args.receipt).write_text(json.dumps(result, indent=2) + "\n")
    print("Verified complete destination graph: " + args.digest)


if __name__ == "__main__":
    main()
