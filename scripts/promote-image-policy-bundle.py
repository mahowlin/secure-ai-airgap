#!/usr/bin/env python3
"""Promote scoped immutable image-policy bundles from oc-mirror output."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


BUNDLE_SCOPES = {
    "ocp-install-core": {
        "required_jobs": {"sync-platform"},
        "inputs": ["mirror/platform-images.yaml"],
    },
    "cilium-day1": {
        "required_jobs": {"sync-isovalent"},
        "inputs": ["mirror/isovalent-images.yaml"],
        "required_catalogs": ["certified-operator-index"],
    },
    "gitops-bootstrap": {
        "required_jobs": {"sync-redhat-ops"},
        "inputs": ["mirror/redhat-operators.yaml"],
    },
    "platform-day2": {
        "required_jobs": {"sync-redhat-ops", "sync-isovalent", "sync-other"},
        "inputs": [
            "mirror/redhat-operators.yaml",
            "mirror/isovalent-images.yaml",
            "mirror/other-images.yaml",
        ],
    },
}


IDMS_KINDS = {"ImageDigestMirrorSet", "ImageTagMirrorSet"}
COPY_KINDS = IDMS_KINDS | {"CatalogSource", "ClusterCatalog", "UpdateService", "ConfigMap"}


def read_yaml_documents(path: Path) -> list[dict]:
    with path.open() as stream:
        return [doc for doc in yaml.safe_load_all(stream) if isinstance(doc, dict)]


def write_yaml_document(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        yaml.safe_dump(document, stream, sort_keys=False)


def canonical_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def hash_directory(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        digest.update(str(relative).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def collect_required_images(paths: list[Path]) -> list[str]:
    required: set[str] = set()
    for path in paths:
        for doc in read_yaml_documents(path):
            mirror = doc.get("mirror", {})
            for channel in mirror.get("platform", {}).get("channels", []) or []:
                name = channel.get("name")
                min_version = channel.get("minVersion")
                max_version = channel.get("maxVersion")
                if name:
                    required.add(f"platform:{name}:{min_version or '*'}:{max_version or '*'}")
            for operator in mirror.get("operators", []) or []:
                catalog = operator.get("catalog", "unknown-catalog")
                for package in operator.get("packages", []) or []:
                    package_name = package.get("name")
                    if package_name:
                        required.add(f"operator:{catalog}:{package_name}")
            for image in mirror.get("additionalImages", []) or []:
                image_name = image.get("name") if isinstance(image, dict) else None
                if image_name:
                    required.add(image_name)
    return sorted(required)


def image_source_prefix(image: str) -> str | None:
    image = image.removeprefix("docker://")
    image = image.split("@", 1)[0]
    image = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    parts = image.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[:2])


def collect_sources_from_inputs(paths: list[Path], source_config: Path) -> list[str]:
    sources: set[str] = set()
    source_overrides = read_yaml_documents(source_config)[0] if source_config.exists() else {}
    configs = source_overrides.get("configs", {}) if isinstance(source_overrides, dict) else {}

    for path in paths:
        for source in configs.get(str(path), {}).get("sources", []) or []:
            sources.add(source)
        for doc in read_yaml_documents(path):
            mirror = doc.get("mirror", {})
            for image in mirror.get("additionalImages", []) or []:
                image_name = image.get("name") if isinstance(image, dict) else None
                if image_name and (source := image_source_prefix(image_name)):
                    sources.add(source)
    return sorted(sources)


def git_value(args: list[str], default: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True).strip() or default
    except Exception:
        return default


def source_commit_created_at(commit: str) -> str:
    """Return a reproducible UTC timestamp for the bundle's source commit."""
    if commit == "unknown":
        return "unknown"

    value = git_value(["show", "-s", "--format=%cI", commit], "unknown")
    if value == "unknown":
        return "unknown"

    try:
        timestamp = dt.datetime.fromisoformat(value)
    except ValueError:
        return "unknown"
    if timestamp.tzinfo is None:
        return "unknown"
    return timestamp.astimezone(dt.UTC).isoformat(timespec="seconds")


def source_matches(source: str, required: str) -> bool:
    return source == required or source.startswith(f"{required}/") or source.startswith(f"{required}:") or source.startswith(f"{required}@")


def collect_policy_sources(manifest_files: list[Path]) -> set[str]:
    sources: set[str] = set()
    for path in manifest_files:
        for doc in read_yaml_documents(path):
            if doc.get("kind") == "ImageDigestMirrorSet":
                entries = doc.get("spec", {}).get("imageDigestMirrors", [])
            elif doc.get("kind") == "ImageTagMirrorSet":
                entries = doc.get("spec", {}).get("imageTagMirrors", [])
            else:
                entries = []
            for entry in entries or []:
                source = entry.get("source")
                if source:
                    sources.add(source)
    return sources


def collect_resource_keys(manifest_files: list[Path]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for path in manifest_files:
        for doc in read_yaml_documents(path):
            kind = doc.get("kind")
            name = doc.get("metadata", {}).get("name")
            if kind and name:
                keys.add((kind, name))
    return keys


def has_resource_key(path: Path, keys: set[tuple[str, str]]) -> bool:
    for doc in read_yaml_documents(path):
        kind = doc.get("kind")
        name = doc.get("metadata", {}).get("name")
        if (kind, name) in keys:
            return True
    return False


def filter_entries(entries: list[dict], required_sources: list[str]) -> list[dict]:
    return [
        entry for entry in entries or []
        if any(source_matches(entry.get("source", ""), required) for required in required_sources)
    ]


def catalog_matches(doc: dict, required_catalogs: list[str]) -> bool:
    if not required_catalogs:
        return False
    spec = doc.get("spec", {})
    refs = [
        spec.get("image", ""),
        spec.get("source", {}).get("image", {}).get("ref", ""),
    ]
    return any(catalog in ref for ref in refs for catalog in required_catalogs)


def normalize_manifest(path: Path, scope: str, index: int, output_dir: Path, required_sources: list[str], required_catalogs: list[str]) -> Path | None:
    documents = read_yaml_documents(path)
    if not documents:
        return None

    normalized_docs: list[dict] = []
    for doc in documents:
        kind = doc.get("kind")
        if kind not in COPY_KINDS:
            continue
        if kind == "ImageDigestMirrorSet":
            entries = filter_entries(doc.get("spec", {}).get("imageDigestMirrors", []), required_sources)
            if not entries:
                continue
            doc.setdefault("spec", {})["imageDigestMirrors"] = entries
        elif kind == "ImageTagMirrorSet":
            entries = filter_entries(doc.get("spec", {}).get("imageTagMirrors", []), required_sources)
            if not entries:
                continue
            doc.setdefault("spec", {})["imageTagMirrors"] = entries
        elif kind in {"CatalogSource", "ClusterCatalog"} and not catalog_matches(doc, required_catalogs):
            continue
        elif scope != "ocp-install-core" and kind in {"UpdateService", "ConfigMap"}:
            continue

        metadata = doc.setdefault("metadata", {})
        name = metadata.get("name", path.stem)
        if kind in IDMS_KINDS and (name.startswith("idms-") or name.startswith("itms-")):
            prefix = "idms" if kind == "ImageDigestMirrorSet" else "itms"
            metadata["name"] = f"{prefix}-{scope}-{index:02d}"
        annotations = metadata.setdefault("annotations", {})
        annotations["saif.platform/bundle-scope"] = scope
        annotations["saif.platform/source-file"] = path.name
        normalized_docs.append(doc)

    if not normalized_docs:
        return None

    destination = output_dir / f"{index:02d}-{scope}-{path.name}"
    with destination.open("w") as stream:
        yaml.safe_dump_all(normalized_docs, stream, sort_keys=False)
    return destination


def promote_selected_cache(args):
    """Retain selected-cache content evidence separately from install policy bundles."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('selected_cache', Path(__file__).with_name('hcp-cache-images.py'))
    cache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cache)
    contract = cache.load_contract(Path(args.cache_contract), args.registry)
    receipt = json.loads(Path(args.cache_verification).read_text())
    expected = {cache.destination(contract, root, False) for root in contract['roots']}
    observed = receipt.get('images', {})
    if (receipt.get('stage') != 'site' or receipt.get('contractSha256') != contract['sha256']
            or receipt.get('inventorySha256') != contract['inventorySha256']
            or receipt.get('trust') != contract['policy']['producerTrust']
            or receipt.get('credentialRef') != contract['policy']['siteWriterSecretRef']
            or set(observed) != expected or any(not x.get('fullBytesVerified') for x in observed.values())):
        raise ValueError('incomplete or mismatched selected cache verification')
    verified = dt.datetime.fromisoformat(receipt['verifiedAt'])
    age = (dt.datetime.now(dt.timezone.utc) - verified).total_seconds()
    if age < -60 or age > contract['policy']['maxAgeSeconds']:
        raise ValueError('selected cache verification is stale or future-dated')
    evidence = {'contract': contract, 'verification': receipt}
    payload = (json.dumps(evidence, sort_keys=True, indent=2) + '\n').encode()
    digest = hashlib.sha256(payload).hexdigest()
    bundle = Path(args.output_root) / 'hcp-cache' / digest
    output = bundle / 'content-evidence.json'
    if output.exists() and output.read_bytes() != payload:
        raise ValueError('refusing immutable selected cache evidence overwrite')
    bundle.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    (bundle.parent / 'current.json').write_text(json.dumps({'scope': 'hcp-cache', 'contentHash': digest, 'path': str(bundle)}, sort_keys=True) + '\n')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", required=True, choices=sorted(BUNDLE_SCOPES) + ["hcp-cache"])
    parser.add_argument("--artifact-dir", default="idms-download")
    parser.add_argument("--output-root", default="mirror/bundles")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--sync-target", default="all")
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--workflow", default=os.environ.get("GITHUB_WORKFLOW", "local"))
    parser.add_argument("--profile", default="", help="Compatibility profile for platform-day2 aliases, for example ocp-4.19-cilium-1.18")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--base-current-bundle", action="store_true", help="For partial promotion, compose new manifests with the current bundle for unchanged sources")
    parser.add_argument("--source-config", default="mirror/mirror-policy-sources.yaml")
    parser.add_argument("--cache-contract")
    parser.add_argument("--cache-verification")
    args = parser.parse_args()
    if args.scope == "hcp-cache":
        if args.sync_target != "hcp-cache" or not args.cache_contract or not args.cache_verification:
            parser.error("hcp-cache requires its exact target, contract and verification")
        return promote_selected_cache(args)

    scope = BUNDLE_SCOPES[args.scope]
    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.exists():
        print(f"artifact directory does not exist: {artifact_dir}", file=sys.stderr)
        return 1

    requested_targets = {args.sync_target}
    if args.sync_target == "all" or args.sync_target == "":
        requested_targets = {"sync-platform", "sync-redhat-ops", "sync-isovalent", "sync-other"}
    target_to_job = {
        "platform": "sync-platform",
        "redhat-operators": "sync-redhat-ops",
        "isovalent-images": "sync-isovalent",
        "other-images": "sync-other",
    }
    requested_targets = {target_to_job.get(target, target) for target in requested_targets}

    if not args.allow_partial and not scope["required_jobs"].issubset(requested_targets):
        missing_jobs = sorted(scope["required_jobs"] - requested_targets)
        print(
            f"refusing to promote {args.scope}: run target {args.sync_target!r} lacks {missing_jobs}",
            file=sys.stderr,
        )
        return 2

    input_paths = [Path(path) for path in scope["inputs"]]
    recipe_paths = list(input_paths)
    if Path("mirror/isovalent-images.yaml") in input_paths:
        import importlib.util
        module_spec = importlib.util.spec_from_file_location("isovalent_selection", Path(__file__).with_name("inspect-isovalent-release.py"))
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        selected = module.load_selection(Path("mirror/isovalent-selection.yaml"))
        if selected.get("destination") and args.registry != selected["destination"]["registry"]:
            raise ValueError("bundle registry differs from maintained Isovalent selection")
        input_paths = [p for p in input_paths if p != Path("mirror/isovalent-images.yaml")]
        input_paths.extend(Path(p) for p in selected["inputs"])
        recipe_paths = [p for p in recipe_paths if p != Path("mirror/isovalent-images.yaml")] + [Path(selected["imageSet"])]
    missing_inputs = [str(path) for path in input_paths if not path.exists()]
    if missing_inputs:
        print(f"missing bundle spec inputs: {missing_inputs}", file=sys.stderr)
        return 1
    source_config = Path(args.source_config)
    required_sources = collect_sources_from_inputs(recipe_paths, source_config)

    manifest_files = sorted(artifact_dir.glob("*.yaml"))
    if args.base_current_bundle:
        current_ref = Path(args.output_root) / args.scope / "current.json"
        if not current_ref.exists():
            print(f"cannot compose from current bundle: missing {current_ref}", file=sys.stderr)
            return 1
        current_data = json.loads(current_ref.read_text())
        current_manifest_dir = Path(current_data["path"]) / "manifests"
        if not current_manifest_dir.exists():
            print(f"cannot compose from current bundle: missing {current_manifest_dir}", file=sys.stderr)
            return 1
        replacement_keys = collect_resource_keys(manifest_files)
        current_manifest_files = [
            path for path in sorted(current_manifest_dir.glob("*.yaml"))
            if not has_resource_key(path, replacement_keys)
        ]
        manifest_files = current_manifest_files + manifest_files
        current_sources = collect_policy_sources(current_manifest_files)
        partial_sources = collect_policy_sources(sorted(artifact_dir.glob("*.yaml")))
        required_sources = sorted(current_sources | partial_sources)
    if not manifest_files:
        print("no manifests found to promote", file=sys.stderr)
        return 1

    observed_sources = collect_policy_sources(manifest_files)
    missing_sources = [
        required for required in required_sources
        if not any(source_matches(source, required) for source in observed_sources)
    ]
    if missing_sources:
        print(f"refusing to promote {args.scope}: missing required sources {missing_sources}", file=sys.stderr)
        print(f"observed sources: {sorted(observed_sources)}", file=sys.stderr)
        return 3

    spec_hash = canonical_hash(input_paths)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"{args.scope}-", dir=output_root) as tmp:
        tmp_root = Path(tmp)
        manifests_dir = tmp_root / "manifests"
        manifests_dir.mkdir(parents=True)

        promoted_files: list[Path] = []
        for index, manifest_file in enumerate(manifest_files, start=1):
            promoted = normalize_manifest(
                manifest_file,
                args.scope,
                index,
                manifests_dir,
                required_sources,
                scope.get("required_catalogs", []),
            )
            if promoted:
                promoted_files.append(promoted)

        if not promoted_files:
            print(f"refusing to promote {args.scope}: no manifests remained after scope filtering", file=sys.stderr)
            return 4

        content_hash = hash_directory(manifests_dir)
        bundle_version = f"{spec_hash[:12]}-{content_hash[:12]}"
        bundle_root = output_root / args.scope / bundle_version
        sys_admin_commit = git_value(["rev-parse", "HEAD"], "unknown")
        sys_admin_ref = git_value(["rev-parse", "--abbrev-ref", "HEAD"], "unknown")

        metadata_fields = {
            "name": args.scope,
            "scope": args.scope,
            "specHash": spec_hash,
            "contentHash": content_hash,
            "version": bundle_version,
            "createdAt": source_commit_created_at(sys_admin_commit),
        }
        if args.scope == "platform-day2" and args.profile:
            metadata_fields["profile"] = args.profile

        metadata = {
            "apiVersion": "saif.platform/v1alpha1",
            "kind": "ImagePolicyBundle",
            "metadata": metadata_fields,
            "spec": {
                "registry": args.registry,
                "workflow": args.workflow,
                "workflowRun": args.run_id,
                "syncTarget": args.sync_target,
                "sysAdminCommit": sys_admin_commit,
                "sysAdminRef": sys_admin_ref,
                "ocMirrorVersion": os.environ.get("OC_MIRROR_VERSION", "unknown"),
                "inputs": [str(path) for path in input_paths],
                "requiredSources": required_sources,
                "requiredCatalogs": scope.get("required_catalogs", []),
                "requiredImages": collect_required_images(recipe_paths),
                "observedSources": sorted(observed_sources),
                "manifests": [str(path.relative_to(tmp_root)) for path in promoted_files],
            },
        }
        write_yaml_document(tmp_root / "bundle.yaml", metadata)

        if bundle_root.exists():
            existing_hash = hash_directory(bundle_root / "manifests")
            if existing_hash != content_hash:
                print(
                    f"refusing to overwrite existing {args.scope} bundle {bundle_version}: content hash mismatch",
                    file=sys.stderr,
                )
                return 5
            shutil.rmtree(tmp_root)
        else:
            bundle_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_root), str(bundle_root))

    current_data = {"scope": args.scope, "specHash": spec_hash, "contentHash": content_hash, "version": bundle_version, "path": str(bundle_root)}
    if args.scope == "platform-day2" and args.profile:
        current_data["profile"] = args.profile

    current_ref = output_root / args.scope / "current.json"
    current_ref.parent.mkdir(parents=True, exist_ok=True)
    current_ref.write_text(json.dumps(current_data, indent=2) + "\n")

    print(f"Promoted {args.scope} bundle {bundle_version} with {len(promoted_files)} manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
