#!/usr/bin/env python3
"""Discover or strictly verify authenticated IEP release artifacts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    )
)
ALLOWED_AUTH_HOSTS = {
    "artifactory.isovalent.com",
    "charts.isovalent.com",
    "containers.isovalent.com",
    "registry.isovalent.com",
}


def authenticated_https_origin(url: str) -> tuple[str, int]:
    try:
        target = urllib.parse.urlsplit(url)
        hostname = target.hostname
        port = target.port
    except ValueError:
        raise SystemExit("refusing malformed authenticated redirect") from None
    if (
        target.scheme.casefold() != "https"
        or not hostname
        or target.username is not None
        or target.password is not None
    ):
        raise SystemExit("refusing unsafe authenticated redirect")
    return hostname.casefold(), 443 if port is None else port


class CredentialSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        target_url = urllib.parse.urljoin(request.full_url, new_url)
        old_origin = authenticated_https_origin(request.full_url)
        new_origin = authenticated_https_origin(target_url)
        if old_origin != new_origin:
            safe_headers = {
                name: value
                for name, value in request.header_items()
                if name.lower() != "authorization"
            }
            request = urllib.request.Request(
                request.full_url,
                data=request.data,
                headers=safe_headers,
                origin_req_host=request.origin_req_host,
                unverifiable=True,
                method=request.get_method(),
            )
        return super().redirect_request(request, file_pointer, code, message, headers, target_url)


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"required environment variable is not set: {name}")
    return value


def read_contract(path: Path) -> dict:
    with path.open() as stream:
        contract = yaml.safe_load(stream)
    if not isinstance(contract, dict):
        raise SystemExit(f"release contract must be a YAML mapping: {path}")
    return contract


def load_selection(path: Path, root: Path | None = None) -> dict:
    """Bind the existing image recipe to its selected release contracts."""
    root = (root or Path.cwd()).resolve()
    def source(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
            raise ValueError("selection requires a repository-relative source path")
        result = (root / value).resolve()
        if root not in result.parents or not result.is_file():
            raise ValueError("selected source is missing or outside the repository")
        return result
    selection_path = source(str(path))
    selection = read_contract(selection_path)
    if set(selection) - {"imageSet", "contracts", "destination", "runtimeImages"} or not isinstance(selection.get("contracts"), list) or not selection["contracts"]:
        raise ValueError("selection requires imageSet and nonempty contracts")
    recipe_path = source(selection["imageSet"])
    recipe = read_contract(recipe_path)
    configured = {item["name"] for item in recipe["mirror"].get("additionalImages", [])}
    inputs = [selection_path, recipe_path]
    destination = selection.get("destination")
    if destination is not None:
        if set(destination) != {"registry", "caBundle"}:
            raise ValueError("destination requires registry and caBundle")
        authority = urllib.parse.urlsplit("https://" + destination["registry"])
        if not authority.hostname or authority.username or authority.password or authority.path or authority.query or authority.fragment:
            raise ValueError("destination registry must be an authority")
        if destination["caBundle"]:
            inputs.append(source(destination["caBundle"]))
    runtime_images = selection.get("runtimeImages", [])
    if not isinstance(runtime_images, list) or any(not isinstance(image, str) or image not in configured for image in runtime_images):
        raise ValueError("selected runtime images are absent from the selected copy recipe")
    contracts = []
    for item in selection["contracts"]:
        if set(item) != {"path", "mode"} or item["mode"] not in {"full", "package-only"}:
            raise ValueError("selected contract requires path and full/package-only mode")
        contract_path = source(item["path"])
        contract = read_contract(contract_path)
        images = [*contract.get("operands", []), *contract.get("runtimeImages", [])]
        bundle = contract["clifeBundle"]
        required = [bundle["controllerImage"]]
        for image in images:
            ref = image["image"]
            required.append(ref if "@" in ref else ref + "@" + image["digest"])
        required[0] = required[0] if "@" in required[0] else required[0] + "@" + bundle["controllerDigest"]
        if not set(required) <= configured:
            raise ValueError("selected release images are absent from the selected copy recipe")
        contracts.append({**item, "contract": contract})
        inputs.append(contract_path)
    identity = hashlib.sha256()
    for file in inputs:
        identity.update(str(file.relative_to(root)).encode() + b"\0" + file.read_bytes() + b"\0")
    return {"imageSet": selection["imageSet"], "contracts": contracts, "destination": destination, "runtimeImages": runtime_images,
            "inputs": [str(file.relative_to(root)) for file in inputs], "sha256": identity.hexdigest()}


def authenticated_get(url: str, username: str, token: str, accept: str | None = None):
    host = urllib.parse.urlsplit(url).hostname
    if host not in ALLOWED_AUTH_HOSTS:
        raise SystemExit(f"refusing to send Isovalent credentials to unapproved host: {host}")
    encoded = base64.b64encode(f"{username}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {encoded}"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(CredentialSafeRedirectHandler())
    return opener.open(request, timeout=120)


def netrc_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise SystemExit("registry credentials must not contain newline characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def curl_authenticated_download(url: str, username: str, token: str) -> bytes:
    host = urllib.parse.urlsplit(url).hostname
    if host not in ALLOWED_AUTH_HOSTS:
        raise SystemExit(f"refusing to send Isovalent credentials to unapproved host: {host}")
    netrc_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix="isovalent-netrc-",
            delete=False,
        ) as netrc_file:
            netrc_path = netrc_file.name
            netrc_file.write(
                f"machine {host}\n  login {netrc_value(username)}\n  password {netrc_value(token)}\n"
            )
        os.chmod(netrc_path, 0o600)
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--netrc-file",
                netrc_path,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        if netrc_path:
            os.unlink(netrc_path)
    if result.returncode != 0:
        raise SystemExit(f"authenticated download failed for {host}: curl exit {result.returncode}")
    return result.stdout


def registry_manifest_digest(registry: str, repository: str, reference: str, username: str, token: str) -> str:
    repository_path = urllib.parse.quote(repository, safe="/")
    reference_path = urllib.parse.quote(reference, safe=":")
    url = f"https://{registry}/v2/{repository_path}/manifests/{reference_path}"
    try:
        response = authenticated_get(url, username, token, MANIFEST_ACCEPT)
    except urllib.error.HTTPError as exc:
        challenge = exc.headers.get("WWW-Authenticate", "")
        if exc.code != 401 or not challenge.startswith("Bearer "):
            raise SystemExit(
                f"registry manifest access failed for {registry}/{repository}:{reference}: HTTP {exc.code}"
            ) from None
        values = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = values.get("realm")
        if not realm:
            raise SystemExit(f"registry authentication challenge has no realm: {registry}") from exc
        query = urllib.parse.urlencode(
            {key: value for key, value in (("service", values.get("service")), ("scope", values.get("scope"))) if value}
        )
        token_url = f"{realm}?{query}" if query else realm
        with authenticated_get(token_url, username, token) as token_response:
            token_document = json.loads(token_response.read())
        bearer_token = token_document.get("token") or token_document.get("access_token")
        if not bearer_token:
            raise SystemExit(f"registry token response did not contain a token: {registry}")
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {bearer_token}", "Accept": MANIFEST_ACCEPT},
        )
        response = urllib.request.build_opener(CredentialSafeRedirectHandler()).open(request, timeout=120)

    with response:
        body = response.read()
        digest = response.headers.get("Docker-Content-Digest")
    return digest or f"sha256:{hashlib.sha256(body).hexdigest()}"


def split_image(image: str) -> tuple[str, str, str]:
    registry, remainder = image.split("/", 1)
    if "@" in remainder:
        repository_with_optional_tag, reference = remainder.rsplit("@", 1)
        if ":" in repository_with_optional_tag.rsplit("/", 1)[-1]:
            repository = repository_with_optional_tag.rsplit(":", 1)[0]
        else:
            repository = repository_with_optional_tag
    elif ":" in remainder.rsplit("/", 1)[-1]:
        repository, reference = remainder.rsplit(":", 1)
    else:
        repository, reference = remainder, "latest"
    return registry, repository, reference


def find_clife_controller_image(bundle: bytes) -> str:
    images: set[str] = set()
    try:
        archive = tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz")
    except tarfile.TarError as exc:
        raise SystemExit("CLIFE bundle is not a valid gzip tar archive") from exc

    with archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith((".yaml", ".yml")):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            try:
                documents = yaml.safe_load_all(stream.read())
                for document in documents:
                    if not isinstance(document, dict):
                        continue
                    if document.get("kind") != "Deployment":
                        continue
                    if document.get("metadata", {}).get("name") != "clife-controller-manager":
                        continue
                    for container in document.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
                        image = container.get("image")
                        if image:
                            images.add(image)
            except yaml.YAMLError as exc:
                raise SystemExit(f"invalid YAML in CLIFE bundle member {member.name}") from exc

    if len(images) != 1:
        raise SystemExit(f"expected exactly one CLIFE controller image in bundle, found {len(images)}")
    return images.pop()


def clife_resource_inventory(bundle: bytes) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith((".yaml", ".yml")):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit(f"unable to read CLIFE bundle member: {member.name}")
            for document in yaml.safe_load_all(stream.read()):
                if document is None:
                    continue
                if not isinstance(document, dict):
                    raise SystemExit(f"invalid CLIFE resource in {member.name}")
                metadata = document.get("metadata") or {}
                identity = {
                    "apiVersion": str(document.get("apiVersion", "")),
                    "kind": str(document.get("kind", "")),
                    "name": str(metadata.get("name", "")),
                }
                if metadata.get("namespace"):
                    identity["namespace"] = str(metadata["namespace"])
                if not all((identity["apiVersion"], identity["kind"], identity["name"])):
                    raise SystemExit(f"incomplete CLIFE resource identity in {member.name}")
                resources.append(identity)
    return sorted(resources, key=lambda item: (item["apiVersion"], item["kind"], item.get("namespace", ""), item["name"]))


def write_ephemeral_bundle(path: Path, bundle: bytes) -> None:
    runner_temp = Path(required_env("RUNNER_TEMP")).resolve()
    target = path.resolve()
    if target == runner_temp or runner_temp not in target.parents:
        raise SystemExit("CLIFE bundle output must be a file below RUNNER_TEMP")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(f"refusing to overwrite CLIFE bundle output: {target}") from None
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(bundle)


def discover(
    contract: dict,
    username: str,
    token: str,
    bundle_only: bool = False,
    package_only: bool = False,
) -> tuple[dict, bytes]:
    bundle_contract = contract["clifeBundle"]
    with authenticated_get(bundle_contract["url"], username, token) as response:
        bundle = response.read()
    bundle_sha256 = hashlib.sha256(bundle).hexdigest()
    expected_bundle_sha256 = bundle_contract.get("sha256", "")
    if expected_bundle_sha256 and bundle_sha256 != expected_bundle_sha256:
        raise SystemExit(
            "release contract mismatch for CLIFE bundle sha256: "
            f"expected {expected_bundle_sha256}, discovered {bundle_sha256}"
        )
    controller_image = find_clife_controller_image(bundle)
    controller_registry, controller_repository, controller_reference = split_image(controller_image)
    if controller_registry != "containers.isovalent.com":
        raise SystemExit(
            f"CLIFE bundle references an unapproved controller registry: {controller_registry}"
        )
    print(f"Checking CLIFE controller image: {controller_image}", flush=True)
    controller_digest = registry_manifest_digest(
        controller_registry,
        controller_repository,
        controller_reference,
        username,
        token,
    )

    if bundle_only:
        return {
            "status": "candidate-bundle-only",
            "clifeBundle": {
                "url": bundle_contract["url"],
                "sha256": bundle_sha256,
                "controllerImage": controller_image,
                "controllerDigest": controller_digest,
                "sourceInventory": clife_resource_inventory(bundle),
            },
        }, bundle

    if package_only:
        runtime_images = []
        for item in contract["runtimeImages"]:
            registry, repository, reference = split_image(item["image"])
            runtime_images.append({
                **item,
                "digest": registry_manifest_digest(registry, repository, reference, username, token),
            })
        return {
            "status": "package-only",
            "release": str(contract["release"]),
            "version": str(contract["version"]),
            "clifeBundle": {
                "url": bundle_contract["url"],
                "sha256": bundle_sha256,
                "controllerImage": controller_image,
                "controllerDigest": controller_digest,
            },
            "runtimeImages": runtime_images,
        }, bundle

    chart_contract = contract["ociChart"]
    chart_digest = registry_manifest_digest(
        chart_contract["registry"],
        chart_contract["repository"],
        str(chart_contract["version"]),
        username,
        token,
    )

    helm_index = yaml.safe_load(
        curl_authenticated_download(contract["helmRepository"]["indexUrl"], username, token)
    )
    cilium_versions = {
        str(item.get("version"))
        for item in (helm_index or {}).get("entries", {}).get("cilium", [])
        if isinstance(item, dict)
    }
    if str(contract["version"]) not in cilium_versions:
        raise SystemExit(f"charts.isovalent.com index does not contain Cilium {contract['version']}")

    def discover_images(images: list[dict]) -> list[dict]:
        discovered = []
        for operand in images:
            registry, repository, reference = split_image(operand["image"])
            discovered_digest = registry_manifest_digest(registry, repository, reference, username, token)
            discovered.append(
                {
                    "name": operand["name"],
                    "image": operand["image"],
                    "digest": discovered_digest,
                    "mirrorRepository": operand["mirrorRepository"],
                }
            )
        return discovered

    operands = discover_images(contract["operands"])
    runtime_images = discover_images(contract.get("runtimeImages", []))

    evidence = {
        "release": str(contract["release"]),
        "version": str(contract["version"]),
        "ociChart": {
            "reference": f"{chart_contract['registry']}/{chart_contract['repository']}:{chart_contract['version']}",
            "digest": chart_digest,
        },
        "helmRepository": {
            "indexUrl": contract["helmRepository"]["indexUrl"],
            "versionPresent": True,
        },
        "clifeBundle": {
            "url": bundle_contract["url"],
            "sha256": bundle_sha256,
            "controllerImage": controller_image,
            "controllerDigest": controller_digest,
        },
        "operands": operands,
        "runtimeImages": runtime_images,
    }
    return evidence, bundle


def require_equal(label: str, expected: str, actual: str) -> None:
    if not expected:
        raise SystemExit(f"release contract is incomplete: {label}")
    if expected != actual:
        raise SystemExit(f"release contract mismatch for {label}: expected {expected}, discovered {actual}")


def verify(contract: dict, evidence: dict) -> None:
    require_equal("release", str(contract["release"]), evidence["release"])
    require_equal("version", str(contract["version"]), evidence["version"])
    if evidence.get("status") != "package-only":
        require_equal("OCI chart digest", contract["ociChart"].get("digest", ""), evidence["ociChart"]["digest"])
    require_equal("CLIFE bundle sha256", contract["clifeBundle"].get("sha256", ""), evidence["clifeBundle"]["sha256"])
    require_equal(
        "CLIFE controller image",
        contract["clifeBundle"].get("controllerImage", ""),
        evidence["clifeBundle"]["controllerImage"],
    )
    require_equal(
        "CLIFE controller digest",
        contract["clifeBundle"].get("controllerDigest", ""),
        evidence["clifeBundle"]["controllerDigest"],
    )

    def verify_images(label: str, expected_items: list[dict], actual_items: list[dict]) -> None:
        expected_images = {item["name"]: item for item in expected_items}
        actual_images = {item["name"]: item for item in actual_items}
        require_equal(
            f"{label} names",
            ",".join(sorted(expected_images)),
            ",".join(sorted(actual_images)),
        )
        for name, expected in expected_images.items():
            actual = actual_images[name]
            require_equal(f"{label} {name} image", expected["image"], actual["image"])
            require_equal(f"{label} {name} digest", expected["digest"], actual["digest"])
            require_equal(
                f"{label} {name} mirror repository",
                expected["mirrorRepository"],
                actual["mirrorRepository"],
            )

    if evidence.get("status") != "package-only":
        verify_images("operand", contract["operands"], evidence["operands"])
    verify_images("runtime image", contract.get("runtimeImages", []), evidence.get("runtimeImages", []))


def write_evidence(path: Path, evidence: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--contract", type=Path)
    choice.add_argument("--selection", type=Path)
    parser.add_argument("--selection-only", action="store_true", help="Resolve local input binding without registry access")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--bundle-only", action="store_true", help="Discover only CLIFE archive and controller metadata")
    parser.add_argument("--package-only", action="store_true", help="Verify CLIFE archive, controller and embedded-chart runtime images")
    parser.add_argument(
        "--bundle-output",
        type=Path,
        help="Write the verified bundle to a new file below RUNNER_TEMP for this job only",
    )
    args = parser.parse_args()

    if args.discovery_only and args.bundle_output is not None:
        raise SystemExit("--bundle-output requires strict verification; it cannot be used with --discovery-only")
    if args.bundle_only and (not args.discovery_only or args.bundle_output is not None):
        raise SystemExit("--bundle-only requires --discovery-only and cannot export the archive")
    if args.bundle_only and args.package_only:
        raise SystemExit("--bundle-only and --package-only are mutually exclusive")
    if args.package_only and args.discovery_only:
        raise SystemExit("--package-only requires strict verification")

    if args.selection:
        if args.discovery_only or args.bundle_only or args.package_only or args.bundle_output:
            parser.error("selection owns verification modes; separate discovery flags are not accepted")
        selected = load_selection(args.selection)
        if args.selection_only:
            write_evidence(args.output, selected)
            return 0
        records = []
        for item in selected["contracts"]:
            evidence, _ = discover(item["contract"], required_env("ISOVALENT_REGISTRY_USERNAME"),
                                   required_env("ISOVALENT_REGISTRY_TOKEN"), package_only=item["mode"] == "package-only")
            verify(item["contract"], evidence)
            records.append({"path": item["path"], "evidence": evidence})
        write_evidence(args.output, {"selectionSha256": selected["sha256"], "inputs": selected["inputs"], "contracts": records})
        return 0
    if args.selection_only:
        parser.error("selection-only requires selection")
    contract = read_contract(args.contract)
    evidence, bundle = discover(
        contract,
        required_env("ISOVALENT_REGISTRY_USERNAME"),
        required_env("ISOVALENT_REGISTRY_TOKEN"),
        bundle_only=args.bundle_only,
        package_only=args.package_only,
    )
    if not args.discovery_only:
        verify(contract, evidence)
    if args.bundle_output is not None:
        write_ephemeral_bundle(args.bundle_output, bundle)
    write_evidence(args.output, evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
