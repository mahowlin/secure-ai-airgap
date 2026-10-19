#!/usr/bin/env python3
"""Build and finalize the receipt for a deterministic CLIFE installer chart."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise SystemExit(f"expected YAML mapping: {path}")
    return value


def require_release_alignment(contract: dict[str, Any], release_contract: dict[str, Any]) -> None:
    spec = contract["spec"]
    if str(release_contract.get("release")) != spec["release"]["iep"]:
        raise SystemExit("SAIF and sys-admin IEP releases do not match")
    if str(release_contract.get("version")) != spec["release"]["cilium"]:
        raise SystemExit("SAIF and sys-admin Cilium versions do not match")
    bundle = release_contract.get("clifeBundle", {})
    if bundle.get("url") != spec["source"]["url"] or bundle.get("sha256") != spec["source"]["sha256"]:
        raise SystemExit("SAIF and sys-admin CLIFE archive identities do not match")

    expected = {
        item["name"]: (item["source"], item["digest"], item["mirror"])
        for item in spec["runtimeImages"]
    }
    mirror_registry = str(spec["chart"]["repository"]).split("/", 1)[0]
    controller_source = str(bundle.get("controllerImage", "")).split("@", 1)[0]
    actual = {
        "clife": (
            controller_source,
            bundle.get("controllerDigest", ""),
            f"{mirror_registry}/{bundle.get('controllerMirrorRepository', '')}",
        )
    }
    for item in release_contract.get("runtimeImages", []):
        actual[item["name"]] = (
            item["image"],
            item["digest"],
            f"{mirror_registry}/{item['mirrorRepository']}",
        )
    if actual != expected:
        raise SystemExit("SAIF package runtime image closure does not match sys-admin release contract")


def resource_identity(resource: dict[str, Any]) -> tuple[str, str, str, str]:
    metadata = resource.get("metadata", {}) or {}
    return (
        str(resource.get("apiVersion", "")),
        str(resource.get("kind", "")),
        str(metadata.get("namespace", "")),
        str(metadata.get("name", "")),
    )


def contract_identity(resource: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(resource.get("apiVersion", "")),
        str(resource.get("kind", "")),
        str(resource.get("namespace", "")),
        str(resource.get("name", "")),
    )


def identity_document(value: tuple[str, str, str, str]) -> dict[str, str]:
    api_version, kind, namespace, name = value
    result = {"apiVersion": api_version, "kind": kind, "name": name}
    if namespace:
        result["namespace"] = namespace
    return result


def validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"unsafe CLIFE archive member: {name}")


def load_archive(archive_path: Path, expected_sha256: str) -> list[dict[str, Any]]:
    archive_bytes = archive_path.read_bytes()
    actual_sha256 = sha256_bytes(archive_bytes)
    if actual_sha256 != expected_sha256:
        raise SystemExit(
            f"CLIFE archive sha256 mismatch: expected {expected_sha256}, discovered {actual_sha256}"
        )
    resources: list[dict[str, Any]] = []
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except tarfile.TarError as exc:
        raise SystemExit("CLIFE archive is not a valid gzip tar archive") from exc
    with archive:
        for member in archive.getmembers():
            validate_member_name(member.name)
            if member.issym() or member.islnk():
                raise SystemExit(f"CLIFE archive must not contain links: {member.name}")
            if not member.isfile() or not member.name.endswith((".yaml", ".yml")):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit(f"unable to read CLIFE archive member: {member.name}")
            try:
                documents = list(yaml.safe_load_all(stream.read()))
            except yaml.YAMLError as exc:
                raise SystemExit(f"invalid YAML in CLIFE archive member: {member.name}") from exc
            for index, document in enumerate(documents):
                if document is None:
                    continue
                if not isinstance(document, dict):
                    raise SystemExit(f"CLIFE resource must be a YAML mapping: {member.name}#{index + 1}")
                resources.append({"member": member.name, "document": index + 1, "resource": document})
    return resources


def require_exact_inventory(contract: dict[str, Any], resources: list[dict[str, Any]]) -> None:
    expected = {
        contract_identity(item)
        for item in contract["spec"]["resources"]["exactSourceInventory"]
    }
    actual_list = [resource_identity(item["resource"]) for item in resources]
    actual = set(actual_list)
    if len(actual_list) != len(actual):
        raise SystemExit("CLIFE archive contains duplicate resource identities")
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise SystemExit(f"CLIFE archive inventory mismatch: missing={missing}, unexpected={unexpected}")


def find_resource(resources: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any]:
    expected = contract_identity(target)
    matches = [item["resource"] for item in resources if resource_identity(item["resource"]) == expected]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one transformation target {expected}, found {len(matches)}")
    return matches[0]


def patch_manager_role(resource: dict[str, Any], transform: dict[str, Any]) -> None:
    required = set(transform["admissionResources"])
    rules = resource.setdefault("rules", [])
    rule = next(
        (item for item in rules if item.get("apiGroups") == ["admissionregistration.k8s.io"]),
        None,
    )
    if rule is None:
        rule = {
            "apiGroups": ["admissionregistration.k8s.io"],
            "resources": [],
            "verbs": ["create", "delete", "get", "list", "patch", "update", "watch"],
        }
        rules.append(rule)
    rule["resources"] = sorted(set(rule.get("resources", [])) | required)


HELM_VALUES = {
    "image": '{{ required "clife.image is required" .Values.clife.image | quote }}',
    "host": '{{ required "clife.kubernetesServiceHost is required" .Values.clife.kubernetesServiceHost | quote }}',
    "port": '{{ required "clife.kubernetesServicePort is required" .Values.clife.kubernetesServicePort | quote }}',
    "development": '{{ .Values.clife.developmentMode | quote }}',
}


def patch_controller(resource: dict[str, Any], transform: dict[str, Any]) -> None:
    containers = resource["spec"]["template"]["spec"].get("containers", [])
    matches = [item for item in containers if item.get("name") == transform["container"]]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one controller container {transform['container']}")
    container = matches[0]
    container["image"] = "HELMPLACEHOLDERIMAGE"
    managed_names = set(transform["environmentValues"])
    env = [item for item in container.get("env", []) if item.get("name") not in managed_names]
    env.extend(
        [
            {"name": "KUBERNETES_SERVICE_HOST", "value": "HELMPLACEHOLDERHOST"},
            {"name": "KUBERNETES_SERVICE_PORT", "value": "HELMPLACEHOLDERPORT"},
            {"name": "DEVELOPMENT_MODE", "value": "HELMPLACEHOLDERDEVELOPMENT"},
        ]
    )
    container["env"] = env


def yaml_template(resource: dict[str, Any]) -> str:
    rendered = yaml.safe_dump(resource, sort_keys=True)
    replacements = {
        "HELMPLACEHOLDERIMAGE": HELM_VALUES["image"],
        "HELMPLACEHOLDERHOST": HELM_VALUES["host"],
        "HELMPLACEHOLDERPORT": HELM_VALUES["port"],
        "HELMPLACEHOLDERDEVELOPMENT": HELM_VALUES["development"],
    }
    for marker, expression in replacements.items():
        rendered = rendered.replace(marker, expression)
    return rendered


def conditional_template(content: str, modes: list[str]) -> str:
    if modes == ["hosted", "management"]:
        return content
    if len(modes) != 1:
        raise SystemExit("resource is excluded from every package mode")
    return f'{{{{- if eq .Values.mode "{modes[0]}" }}}}\n{content}{{{{- end }}}}\n'


def cilium_config_template(resource: dict[str, Any]) -> str:
    metadata = yaml.safe_dump(resource.get("metadata", {}), sort_keys=True).rstrip()
    return (
        '{{- if eq .Values.mode "hosted" }}\n'
        f"apiVersion: {resource['apiVersion']}\n"
        f"kind: {resource['kind']}\n"
        "metadata:\n"
        + "\n".join(f"  {line}" for line in metadata.splitlines())
        + "\nspec:\n{{- toYaml .Values.ciliumConfig | nindent 2 }}\n{{- end }}\n"
    )


def network_template() -> str:
    return (
        '{{- if eq .Values.mode "hosted" }}\n'
        "apiVersion: operator.openshift.io/v1\n"
        "kind: Network\n"
        "metadata:\n"
        "  name: cluster\n"
        "spec:\n"
        "{{- toYaml .Values.networkOperator | nindent 2 }}\n"
        "{{- end }}\n"
    )


def safe_template_name(index: int, identity: tuple[str, str, str, str]) -> str:
    value = "-".join(part for part in (identity[1], identity[3]) if part)
    value = re.sub(r"[^a-zA-Z0-9.-]+", "-", value).strip("-").lower()
    return f"{index:03d}-{value}.yaml"


def chart_values_schema(contract: dict[str, Any]) -> dict[str, Any]:
    clife = contract["spec"]["values"]["clife"]
    cilium_schema = copy.deepcopy(contract["spec"]["values"]["ciliumConfig"]["schema"])
    network_schema = copy.deepcopy(contract["spec"]["values"]["networkOperator"]["schema"])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["mode", "clife"],
        "properties": {
            "mode": {"enum": ["management", "hosted"]},
            "clife": {
                "type": "object",
                "additionalProperties": False,
                "required": clife["requiredKeys"],
                "properties": {
                    "image": {"type": "string", "pattern": "@sha256:[0-9a-f]{64}$"},
                    "kubernetesServiceHost": {"type": "string", "minLength": 1},
                    "kubernetesServicePort": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "developmentMode": {"const": False},
                },
            },
            "ciliumConfig": {"type": "object"},
            "networkOperator": {"type": "object"},
        },
        "allOf": [
            {
                "if": {"properties": {"mode": {"const": "hosted"}}},
                "then": {
                    "required": ["ciliumConfig", "networkOperator"],
                    "properties": {
                        "ciliumConfig": cilium_schema,
                        "networkOperator": network_schema,
                    },
                },
                "else": {
                    "properties": {
                        "ciliumConfig": {"maxProperties": 0},
                        "networkOperator": {"maxProperties": 0},
                    }
                },
            }
        ],
    }


def package_validation_values(
    contract: dict[str, Any], pinned_controller_image: str
) -> dict[str, dict[str, Any]]:
    scenarios = {
        scenario["name"]: scenario
        for scenario in contract["spec"]["embeddedChartVerification"]["scenarios"]
    }
    if set(scenarios) != {"management", "tenant-a", "shared-ab"}:
        raise SystemExit("package validation requires exact management, tenant-a and shared-ab scenarios")

    result: dict[str, dict[str, Any]] = {}
    for name in ("management", "tenant-a", "shared-ab"):
        scenario = scenarios[name]
        cilium_values = copy.deepcopy(scenario["values"])
        clife_values = {
            "image": pinned_controller_image,
            "kubernetesServiceHost": cilium_values["k8sServiceHost"],
            "kubernetesServicePort": cilium_values["k8sServicePort"],
            "developmentMode": False,
        }
        if scenario["mode"] == "management":
            result[name] = {
                "mode": "management",
                "clife": clife_values,
                "ciliumConfig": {},
                "networkOperator": {},
            }
            continue

        ipam_operator = cilium_values["ipam"]["operator"]
        cidrs = ipam_operator["clusterPoolIPv4PodCIDRList"]
        host_prefix = ipam_operator["clusterPoolIPv4MaskSize"]
        result[name] = {
            "mode": "hosted",
            "clife": clife_values,
            "ciliumConfig": cilium_values,
            "networkOperator": {
                "managementState": "Managed",
                "clusterNetwork": [
                    {"cidr": cidr, "hostPrefix": host_prefix}
                    for cidr in cidrs
                ],
                "serviceNetwork": scenario["serviceNetwork"],
                "defaultNetwork": {"type": "Cilium"},
                "deployKubeProxy": False,
            },
        }
    return result


def load_embedded_chart(
    rootfs_path: Path, expected_version: str
) -> tuple[dict[str, str], dict[str, bytes]]:
    files: dict[str, bytes] = {}
    try:
        archive = tarfile.open(rootfs_path, "r:*")
    except tarfile.TarError as exc:
        raise SystemExit("CLIFE root filesystem is not a valid tar archive") from exc
    with archive:
        for member in archive.getmembers():
            validate_member_name(member.name)
            normalized = member.name.removeprefix("./").lstrip("/")
            if not member.isfile() or not normalized.startswith("manifests/"):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit(f"unable to read embedded chart member: {member.name}")
            if normalized in files:
                raise SystemExit(f"duplicate embedded chart member: {normalized}")
            files[normalized] = stream.read()
    if not files:
        raise SystemExit("CLIFE image does not contain an embedded chart below /manifests")
    if "manifests/Chart.yaml" not in files:
        raise SystemExit("expected one embedded manifests/Chart.yaml, found 0")
    chart = yaml.safe_load(files["manifests/Chart.yaml"])
    if chart.get("name") != "cilium" or str(chart.get("version")) != expected_version:
        raise SystemExit(
            f"unexpected embedded chart identity: {chart.get('name')} {chart.get('version')}"
        )
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    metadata = {
        "name": str(chart["name"]),
        "version": str(chart["version"]),
        "appVersion": str(chart.get("appVersion", "")),
        "contentSha256": digest.hexdigest(),
    }
    return metadata, files


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def enumerate_pod_images(document: Any) -> set[str]:
    images: set[str] = set()
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"containers", "initContainers", "ephemeralContainers"} and isinstance(value, list):
                for container in value:
                    if isinstance(container, dict) and isinstance(container.get("image"), str):
                        images.add(container["image"])
            images.update(enumerate_pod_images(value))
    elif isinstance(document, list):
        for value in document:
            images.update(enumerate_pod_images(value))
    return images


def normalized_rendered_manifest_sha256(documents: list[Any]) -> str:
    """Hash a render while masking only chart-declared generated Secret values."""
    normalized: list[Any] = []
    for document in documents:
        item = copy.deepcopy(document)
        if isinstance(item, dict):
            metadata = item.get("metadata", {})
            labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
            if not isinstance(labels, dict):
                labels = {}
            if labels.get("cilium.io/helm-template-non-idempotent") == "true":
                for field in ("data", "stringData"):
                    values = item.get(field)
                    if isinstance(values, dict):
                        item[field] = {key: "<non-idempotent>" for key in sorted(values)}
        normalized.append(item)
    rendered = yaml.safe_dump_all(normalized, sort_keys=True).encode()
    return sha256_bytes(rendered)


def render_embedded_chart(
    chart_metadata: dict[str, str],
    chart_files: dict[str, bytes],
    contract: dict[str, Any],
    helm_command: str = "helm",
) -> list[dict[str, Any]]:
    runtime_by_name = {item["name"]: item for item in contract["spec"]["runtimeImages"]}
    results: list[dict[str, Any]] = []
    selected_union: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="clife-embedded-chart-") as directory:
        chart_dir = Path(directory) / "cilium"
        for member_name, data in chart_files.items():
            relative = PurePosixPath(member_name).relative_to("manifests")
            destination = chart_dir.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

        for scenario in contract["spec"]["embeddedChartVerification"]["scenarios"]:
            values = copy.deepcopy(scenario["values"])
            overrides = copy.deepcopy(scenario["renderOverrides"])
            render_values = deep_merge(values, overrides)
            values_path = Path(directory) / f"{scenario['name']}-values.yaml"
            values_path.write_text(yaml.safe_dump(render_values, sort_keys=True))
            rendered = subprocess.run(
                [
                    helm_command,
                    "template",
                    f"closure-{scenario['name']}",
                    str(chart_dir),
                    "--namespace",
                    "cilium",
                    "--include-crds",
                    "-f",
                    str(values_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if rendered.returncode != 0:
                raise SystemExit(
                    f"embedded chart render failed for {scenario['name']}: {rendered.stderr.strip()}"
                )
            try:
                documents = list(yaml.safe_load_all(rendered.stdout))
            except yaml.YAMLError as exc:
                raise SystemExit(f"embedded chart rendered invalid YAML for {scenario['name']}") from exc
            actual_refs: set[str] = set()
            for document in documents:
                actual_refs.update(enumerate_pod_images(document))

            expected_names = set(scenario["expectedRuntimeImages"])
            expected_refs = {
                f"{runtime_by_name[name]['source']}@{runtime_by_name[name]['digest']}"
                for name in expected_names
            }
            if actual_refs != expected_refs:
                raise SystemExit(
                    f"embedded chart image closure mismatch for {scenario['name']}: "
                    f"missing={sorted(expected_refs - actual_refs)}, "
                    f"unexpected={sorted(actual_refs - expected_refs)}"
                )
            selected_union.update(expected_names)
            results.append(
                {
                    "name": scenario["name"],
                    "mode": scenario["mode"],
                    "valuesSha256": sha256_bytes(yaml.safe_dump(values, sort_keys=True).encode()),
                    "renderOverridesSha256": sha256_bytes(
                        yaml.safe_dump(overrides, sort_keys=True).encode()
                    ),
                    "normalizedRenderedManifestSha256": normalized_rendered_manifest_sha256(
                        documents
                    ),
                    "images": [
                        {
                            **copy.deepcopy(runtime_by_name[name]),
                            "reference": f"{runtime_by_name[name]['source']}@{runtime_by_name[name]['digest']}",
                            "mirrorReference": f"{runtime_by_name[name]['mirror']}@{runtime_by_name[name]['digest']}",
                        }
                        for name in sorted(expected_names)
                    ],
                }
            )

    expected_union = set(runtime_by_name) - {"clife"}
    if selected_union != expected_union:
        raise SystemExit(
            "embedded chart scenarios do not select the exact runtime image union: "
            f"missing={sorted(expected_union - selected_union)}, "
            f"unexpected={sorted(selected_union - expected_union)}"
        )
    return results


def classify_registry_lookup(exit_code: int, output: str) -> str:
    if exit_code == 0:
        if not SHA256_RE.fullmatch(output.strip()):
            raise SystemExit("registry lookup succeeded without an OCI sha256 digest")
        return "present"
    confirmed_absence = (
        "MANIFEST_UNKNOWN",
        "NAME_UNKNOWN",
        "manifest unknown",
        "name unknown",
    )
    if any(marker in output for marker in confirmed_absence):
        return "absent"
    raise SystemExit("registry lookup was ambiguous; refusing publication")


def classify_registry_command(args: argparse.Namespace) -> int:
    try:
        output = args.result.read_text()
    except OSError as exc:
        raise SystemExit("unable to read registry lookup result") from exc
    print(classify_registry_lookup(args.exit_code, output))
    return 0


def deterministic_chart_package(chart_dir: Path, output: Path) -> None:
    entries = [path for path in chart_dir.rglob("*") if path.is_file()]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(entries, key=lambda item: item.relative_to(chart_dir).as_posix()):
                    relative = path.relative_to(chart_dir).as_posix()
                    data = path.read_bytes()
                    info = tarfile.TarInfo(f"{chart_dir.name}/{relative}")
                    info.size = len(data)
                    info.mode = 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(data))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def build(args: argparse.Namespace) -> int:
    contract_bytes = args.contract.read_bytes()
    contract = yaml.safe_load(contract_bytes)
    spec = contract["spec"]
    release_contract = load_yaml(args.release_contract)
    require_release_alignment(contract, release_contract)
    if contract.get("kind") != "ClifePackageContract":
        raise SystemExit("unsupported package contract kind")
    if not GIT_SHA_RE.fullmatch(args.contract_revision) or not GIT_SHA_RE.fullmatch(args.executor_revision):
        raise SystemExit("contract and executor revisions must be full Git commit hashes")
    source_sha = spec["source"]["sha256"]
    version = spec["chart"]["versionTemplate"].format(
        sourceSha12=source_sha[:12],
        contractSha12=sha256_bytes(contract_bytes)[:12],
    )
    source_resources = load_archive(args.archive, source_sha)
    require_exact_inventory(contract, source_resources)

    input_inventory: list[dict[str, Any]] = []
    for item in sorted(source_resources, key=lambda entry: resource_identity(entry["resource"])):
        resource_id = resource_identity(item["resource"])
        normalized = yaml.safe_dump(item["resource"], sort_keys=True).encode()
        input_inventory.append(
            {
                **identity_document(resource_id),
                "archiveMember": item["member"],
                "document": item["document"],
                "normalizedSha256": sha256_bytes(normalized),
            }
        )

    resources = copy.deepcopy(source_resources)

    patch_manager_role(find_resource(resources, spec["transformations"]["managerRole"]["resource"]), spec["transformations"]["managerRole"])
    patch_controller(find_resource(resources, spec["transformations"]["controller"]["resource"]), spec["transformations"]["controller"])
    cilium_target = contract_identity(spec["transformations"]["ciliumConfig"]["resource"])

    chart_dir = args.output_dir / spec["chart"]["name"]
    templates_dir = chart_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    write_text(
        chart_dir / "Chart.yaml",
        yaml.safe_dump(
            {
                "apiVersion": "v2",
                "name": spec["chart"]["name"],
                "description": "Receipt-bound official CLIFE installer resources",
                "type": "application",
                "version": version,
                "appVersion": spec["chart"]["appVersion"],
            },
            sort_keys=False,
        ),
    )
    write_text(
        chart_dir / "values.yaml",
        yaml.safe_dump(
            {
                "mode": "",
                "clife": {
                    "image": "",
                    "kubernetesServiceHost": "",
                    "kubernetesServicePort": 443,
                    "developmentMode": False,
                },
                "ciliumConfig": {},
                "networkOperator": {},
            },
            sort_keys=False,
        ),
    )
    write_text(
        chart_dir / "values.schema.json",
        json.dumps(chart_values_schema(contract), indent=2, sort_keys=True) + "\n",
    )
    controller_image = next(item for item in spec["runtimeImages"] if item["name"] == "clife")
    pinned_controller_image = f"{controller_image['source']}@{controller_image['digest']}"
    for name, values in package_validation_values(contract, pinned_controller_image).items():
        write_text(
            args.output_dir / f"{name}-values.yaml",
            yaml.safe_dump(values, sort_keys=False),
        )

    excluded = {
        mode: {contract_identity(item) for item in mode_spec["exclude"]}
        for mode, mode_spec in spec["modes"].items()
    }
    output_inventory: list[dict[str, Any]] = []
    for index, item in enumerate(sorted(resources, key=lambda entry: resource_identity(entry["resource"])), 1):
        resource = item["resource"]
        resource_id = resource_identity(resource)
        modes = sorted(mode for mode in spec["modes"] if resource_id not in excluded[mode])
        if not modes:
            continue
        if resource_id == cilium_target:
            rendered = cilium_config_template(resource)
        else:
            rendered = conditional_template(yaml_template(resource), modes)
        template_name = safe_template_name(index, resource_id)
        write_text(templates_dir / template_name, rendered)
        output_inventory.append(
            {
                **identity_document(resource_id),
                "template": f"templates/{template_name}",
                "modes": modes,
                "sha256": sha256_bytes(rendered.encode()),
            }
        )

    network = network_template()
    network_name = "999-network-cluster.yaml"
    write_text(templates_dir / network_name, network)
    output_inventory.append(
        {
            "apiVersion": "operator.openshift.io/v1",
            "kind": "Network",
            "name": "cluster",
            "template": f"templates/{network_name}",
            "modes": ["hosted"],
            "sha256": sha256_bytes(network.encode()),
        }
    )

    required = {contract_identity(item) for item in spec["resources"]["requiredOutputInventory"]}
    for mode in spec["modes"]:
        present = {
            (item["apiVersion"], item["kind"], item.get("namespace", ""), item["name"])
            for item in output_inventory
            if mode in item["modes"]
        }
        if not required.issubset(present):
            raise SystemExit(f"package mode {mode} omits required resources: {sorted(required - present)}")

    embedded_chart, embedded_files = load_embedded_chart(
        args.embedded_rootfs,
        spec["embeddedChartVerification"].get("version", spec["release"]["cilium"]),
    )
    embedded_chart["scenarios"] = render_embedded_chart(
        embedded_chart,
        embedded_files,
        contract,
        getattr(args, "helm_command", "helm"),
    )
    package_path = args.output_dir / f"{spec['chart']['name']}-{version}.tgz"
    deterministic_chart_package(chart_dir, package_path)
    package_sha = sha256_bytes(package_path.read_bytes())
    receipt = {
        "apiVersion": "packaging.saif.example.com/v1alpha1",
        "kind": "ClifePackageReceipt",
        "metadata": {"name": f"clife-install-{version}"},
        "spec": {
            "source": {"url": spec["source"]["url"], "sha256": source_sha},
            "contract": {
                "repository": args.contract_repository,
                "revision": args.contract_revision,
                "path": args.contract_path,
                "sha256": sha256_bytes(contract_bytes),
            },
            "releaseContract": {
                "path": args.release_contract_path,
                "sha256": sha256_bytes(args.release_contract.read_bytes()),
            },
            "executor": {
                "repository": args.executor_repository,
                "revision": args.executor_revision,
                "scriptSha256": sha256_bytes(Path(__file__).read_bytes()),
            },
            "embeddedChart": embedded_chart,
            "inputInventory": input_inventory,
            "outputInventory": output_inventory,
            "chart": {
                "repository": spec["chart"]["repository"],
                "version": version,
                "packageSha256": package_sha,
                "ociDigest": "",
            },
            "runtimeImages": spec["runtimeImages"],
            "workflow": {"runId": "", "createdAt": ""},
            "receiptRequirements": spec["receipt"]["requiredFields"],
        },
    }
    write_text(args.output_dir / "receipt-draft.yaml", yaml.safe_dump(receipt, sort_keys=False))
    print(json.dumps({"chart": str(package_path), "version": version, "packageSha256": package_sha}))
    return 0


def finalize(args: argparse.Namespace) -> int:
    if not SHA256_RE.fullmatch(args.oci_digest):
        raise SystemExit("OCI digest must be a sha256 digest")
    receipt = load_yaml(args.draft)
    if receipt.get("kind") != "ClifePackageReceipt":
        raise SystemExit("unsupported receipt draft")
    created_at = args.created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    receipt["spec"]["chart"]["ociDigest"] = args.oci_digest
    receipt["spec"]["workflow"] = {"runId": str(args.workflow_run_id), "createdAt": created_at}
    for dotted_path in receipt["spec"].get("receiptRequirements", []):
        value: Any = receipt["spec"]
        for segment in dotted_path.split("."):
            if not isinstance(value, dict) or segment not in value:
                raise SystemExit(f"final receipt is missing required field: {dotted_path}")
            value = value[segment]
        if value in (None, "", [], {}):
            raise SystemExit(f"final receipt has an empty required field: {dotted_path}")
    write_text(args.output, yaml.safe_dump(receipt, sort_keys=False))
    checksum = sha256_bytes(args.output.read_bytes())
    write_text(args.output.parent / "receipt.sha256", f"{checksum}  {args.output.name}\n")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--contract", required=True, type=Path)
    build_parser.add_argument("--release-contract", required=True, type=Path)
    build_parser.add_argument("--release-contract-path", required=True)
    build_parser.add_argument("--contract-path", required=True)
    build_parser.add_argument("--contract-repository", required=True)
    build_parser.add_argument("--contract-revision", required=True)
    build_parser.add_argument("--executor-repository", required=True)
    build_parser.add_argument("--executor-revision", required=True)
    build_parser.add_argument("--archive", required=True, type=Path)
    build_parser.add_argument("--embedded-rootfs", required=True, type=Path)
    build_parser.add_argument("--helm-command", default="helm")
    build_parser.add_argument("--output-dir", required=True, type=Path)
    build_parser.set_defaults(handler=build)

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--draft", required=True, type=Path)
    finalize_parser.add_argument("--oci-digest", required=True)
    finalize_parser.add_argument("--workflow-run-id", required=True)
    finalize_parser.add_argument("--created-at")
    finalize_parser.add_argument("--output", required=True, type=Path)
    finalize_parser.set_defaults(handler=finalize)

    registry_parser = subparsers.add_parser("classify-registry-lookup")
    registry_parser.add_argument("--exit-code", required=True, type=int)
    registry_parser.add_argument("--result", required=True, type=Path)
    registry_parser.set_defaults(handler=classify_registry_command)

    return result


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
