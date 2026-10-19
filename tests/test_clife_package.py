from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package-clife-chart.py"
spec = importlib.util.spec_from_file_location("package_clife_chart", SCRIPT)
package = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(package)


def identity(api_version, kind, name, namespace=""):
    value = {"apiVersion": api_version, "kind": kind, "name": name}
    if namespace:
        value["namespace"] = namespace
    return value


RESOURCES = [
    identity("apps/v1", "Deployment", "clife-controller-manager", "cilium"),
    identity("cilium.io/v1alpha1", "CiliumConfig", "ciliumconfig"),
    identity("operators.coreos.com/v1alpha1", "Subscription", "clife", "cilium"),
    identity("rbac.authorization.k8s.io/v1", "ClusterRole", "clife-manager-role"),
    identity("v1", "Namespace", "cilium"),
]


def resource_document(item):
    metadata = {"name": item["name"]}
    if item.get("namespace"):
        metadata["namespace"] = item["namespace"]
    document = {"apiVersion": item["apiVersion"], "kind": item["kind"], "metadata": metadata}
    if item["kind"] == "Deployment":
        document["spec"] = {
            "selector": {"matchLabels": {"control-plane": "controller-manager"}},
            "template": {
                "metadata": {"labels": {"control-plane": "controller-manager"}},
                "spec": {
                    "containers": [
                        {
                            "name": "manager",
                            "image": "containers.isovalent.com/clife:test",
                            "env": [{"name": "EXISTING", "value": "preserved"}],
                        }
                    ]
                }
            }
        }
    elif item["kind"] == "ClusterRole":
        document["rules"] = []
    elif item["kind"] == "CiliumConfig":
        document["spec"] = {"cluster": {"name": "source-default"}}
    elif item["kind"] == "Subscription":
        document["spec"] = {"channel": "1.19"}
    return document


def archive_bytes(resources=RESOURCES, member_prefix="manifests"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for index, item in enumerate(resources):
            data = yaml.safe_dump(resource_document(item), sort_keys=False).encode()
            info = tarfile.TarInfo(f"{member_prefix}/{index:02d}.yaml")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def rootfs_bytes(version="1.19.5"):
    chart = yaml.safe_dump(
        {"apiVersion": "v2", "name": "cilium", "version": version, "appVersion": version},
        sort_keys=False,
    ).encode()
    values = b"selectedImages: []\n"
    template = b'''{{- range $index, $image := .Values.selectedImages }}
---
apiVersion: v1
kind: Pod
metadata:
  name: closure-{{ $index }}
spec:
  containers:
    - name: operand
      image: {{ $image | quote }}
{{- end }}
'''
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in (
            ("manifests/Chart.yaml", chart),
            ("manifests/values.yaml", values),
            ("manifests/templates/closure.yaml", template),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def synthetic_cilium_values(name, cidr, images):
    return {
        "cluster": {"name": name},
        "ipam": {
            "mode": "cluster-pool",
            "operator": {
                "clusterPoolIPv4PodCIDRList": [cidr],
                "clusterPoolIPv4MaskSize": 23,
            },
        },
        "k8sServiceHost": f"api.{name}.example.com",
        "k8sServicePort": 443,
        "kubeProxyReplacement": True,
        "routingMode": "tunnel",
        "tunnelProtocol": "vxlan",
        "tunnelPort": 4788,
        "selectedImages": images,
    }


def contract_document(source_sha):
    return {
        "apiVersion": "packaging.saif.example.com/v1alpha1",
        "kind": "ClifePackageContract",
        "metadata": {"name": "clife-install-1.19.5"},
        "spec": {
            "release": {"iep": "26.05", "cilium": "1.19.5", "clife": "1.19.5"},
            "source": {
                "url": "https://artifactory.isovalent.com/clife.tar.gz",
                "sha256": source_sha,
            },
            "chart": {
                "name": "clife-install",
                "repository": "registry.example.com:5000/charts/clife-install",
                "versionTemplate": "1.19.5-clife.{sourceSha12}.{contractSha12}",
                "appVersion": "1.19.5",
            },
            "resources": {
                "exactSourceInventory": copy.deepcopy(RESOURCES),
                "requiredOutputInventory": [
                    copy.deepcopy(RESOURCES[0]),
                    copy.deepcopy(RESOURCES[3]),
                ],
            },
            "modes": {
                "management": {
                    "exclude": [
                        copy.deepcopy(RESOURCES[1]),
                        copy.deepcopy(RESOURCES[2]),
                        copy.deepcopy(RESOURCES[4]),
                    ],
                    "requiredValues": ["clife.image"],
                },
                "hosted": {
                    "exclude": [copy.deepcopy(RESOURCES[2])],
                    "requiredValues": ["clife.image", "ciliumConfig", "networkOperator"],
                },
            },
            "transformations": {
                "controller": {
                    "resource": copy.deepcopy(RESOURCES[0]),
                    "container": "manager",
                    "imageValue": "clife.image",
                    "environmentValues": {
                        "KUBERNETES_SERVICE_HOST": "clife.kubernetesServiceHost",
                        "KUBERNETES_SERVICE_PORT": "clife.kubernetesServicePort",
                        "DEVELOPMENT_MODE": "clife.developmentMode",
                    },
                },
                "managerRole": {
                    "resource": copy.deepcopy(RESOURCES[3]),
                    "admissionResources": [
                        "mutatingwebhookconfigurations",
                        "validatingadmissionpolicies",
                        "validatingadmissionpolicybindings",
                        "validatingwebhookconfigurations",
                    ],
                },
                "ciliumConfig": {
                    "mode": "hosted",
                        "serviceNetwork": ["172.20.0.0/16"],
                    "resource": copy.deepcopy(RESOURCES[1]),
                    "specValue": "ciliumConfig",
                },
                "generatedResources": [
                    {
                        "mode": "hosted",
                        "serviceNetwork": ["172.20.0.0/16"],
                        "apiVersion": "operator.openshift.io/v1",
                        "kind": "Network",
                        "name": "cluster",
                        "specValue": "networkOperator",
                    }
                ],
            },
            "values": {
                "clife": {
                    "requiredKeys": ["image", "kubernetesServiceHost", "kubernetesServicePort"],
                    "allowedKeys": ["image", "kubernetesServiceHost", "kubernetesServicePort", "developmentMode"],
                },
                "ciliumConfig": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "cluster",
                            "ipam",
                            "k8sServiceHost",
                            "k8sServicePort",
                            "kubeProxyReplacement",
                            "routingMode",
                            "tunnelProtocol",
                            "tunnelPort",
                            "selectedImages",
                        ],
                        "properties": {
                            "cluster": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name"],
                                "properties": {"name": {"type": "string"}},
                            },
                            "ipam": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["mode", "operator"],
                                "properties": {
                                    "mode": {"const": "cluster-pool"},
                                    "operator": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "clusterPoolIPv4PodCIDRList",
                                            "clusterPoolIPv4MaskSize",
                                        ],
                                        "properties": {
                                            "clusterPoolIPv4PodCIDRList": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "clusterPoolIPv4MaskSize": {"type": "integer"},
                                        },
                                    },
                                },
                            },
                            "k8sServiceHost": {"type": "string"},
                            "k8sServicePort": {"const": 443},
                            "kubeProxyReplacement": {"const": True},
                            "routingMode": {"const": "tunnel"},
                            "tunnelProtocol": {"const": "vxlan"},
                            "tunnelPort": {"const": 4788},
                            "selectedImages": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    }
                },
                "networkOperator": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "managementState",
                            "clusterNetwork",
                            "serviceNetwork",
                            "defaultNetwork",
                            "deployKubeProxy",
                        ],
                        "properties": {
                            "managementState": {"const": "Managed"},
                            "clusterNetwork": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["cidr", "hostPrefix"],
                                    "properties": {
                                        "cidr": {"type": "string"},
                                        "hostPrefix": {"type": "integer"},
                                    },
                                },
                            },
                            "serviceNetwork": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "defaultNetwork": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["type"],
                                "properties": {"type": {"const": "Cilium"}},
                            },
                            "deployKubeProxy": {"const": False},
                        },
                    }
                },
            },
            "runtimeImages": [
                {
                    "name": "clife",
                    "source": "containers.isovalent.com/clife:v1.19.5-cee.1",
                    "digest": "sha256:" + "1" * 64,
                    "mirror": "registry.example.com:5000/clife",
                },
                {
                    "name": "cilium",
                    "source": "containers.isovalent.com/cilium-ubi:v1.19.5-cee.1",
                    "digest": "sha256:" + "2" * 64,
                    "mirror": "registry.example.com:5000/cilium-ubi",
                },
                {
                    "name": "operator-generic",
                    "source": "containers.isovalent.com/operator-generic-ubi:v1.19.5-cee.1",
                    "digest": "sha256:" + "3" * 64,
                    "mirror": "registry.example.com:5000/operator-generic-ubi",
                },
            ],
            "embeddedChartVerification": {
                "scenarios": [
                    {
                        "name": "management",
                        "mode": "management",
                        "values": synthetic_cilium_values(
                            "management",
                            "10.192.0.0/14",
                            [
                                "containers.isovalent.com/cilium-ubi:v1.19.5-cee.1@sha256:"
                                + "2" * 64,
                                "containers.isovalent.com/operator-generic-ubi:v1.19.5-cee.1@sha256:"
                                + "3" * 64,
                            ],
                        ),
                        "renderOverrides": {},
                        "expectedRuntimeImages": ["cilium", "operator-generic"],
                    },
                    {
                        "name": "tenant-a",
                        "mode": "hosted",
                        "serviceNetwork": ["172.20.0.0/16"],
                        "values": synthetic_cilium_values(
                            "tenant-a",
                            "10.196.0.0/14",
                            [
                                "containers.isovalent.com/cilium-ubi:v1.19.5-cee.1@sha256:"
                                + "2" * 64
                            ],
                        ),
                        "renderOverrides": {},
                        "expectedRuntimeImages": ["cilium"],
                    },
                    {
                        "name": "shared-ab",
                        "mode": "hosted",
                        "serviceNetwork": ["172.20.0.0/16"],
                        "values": synthetic_cilium_values(
                            "shared-ab",
                            "10.200.0.0/14",
                            [
                                "containers.isovalent.com/operator-generic-ubi:v1.19.5-cee.1@sha256:"
                                + "3" * 64
                            ],
                        ),
                        "renderOverrides": {},
                        "expectedRuntimeImages": ["operator-generic"],
                    },
                ]
            },
            "receipt": {
                "requiredFields": [
                    "source.url",
                    "source.sha256",
                    "contract.repository",
                    "contract.revision",
                    "embeddedChart.scenarios",
                    "chart.repository",
                    "chart.version",
                    "chart.packageSha256",
                    "chart.ociDigest",
                    "runtimeImages",
                    "workflow.runId",
                    "workflow.createdAt",
                ]
            },
        },
    }


class ClifePackageTests(unittest.TestCase):
    def build_once(self, directory, source=None, contract_mutator=None):
        root = Path(directory)
        source = source if source is not None else archive_bytes()
        archive = root / "clife.tar.gz"
        archive.write_bytes(source)
        rootfs = root / "rootfs.tar"
        rootfs.write_bytes(rootfs_bytes())
        contract = contract_document(hashlib.sha256(source).hexdigest())
        if contract_mutator:
            contract_mutator(contract)
        contract_path = root / "contract.yaml"
        contract_path.write_text(yaml.safe_dump(contract, sort_keys=False))
        clife_image = contract["spec"]["runtimeImages"][0]
        runtime_images = contract["spec"]["runtimeImages"][1:]
        release_contract = root / "iep-release.yaml"
        release_contract.write_text(
            yaml.safe_dump(
                {
                    "release": "26.05",
                    "version": "1.19.5",
                    "clifeBundle": {
                        "url": contract["spec"]["source"]["url"],
                        "sha256": contract["spec"]["source"]["sha256"],
                        "controllerImage": f"{clife_image['source']}@{clife_image['digest']}",
                        "controllerDigest": clife_image["digest"],
                        "controllerMirrorRepository": "clife",
                    },
                    "runtimeImages": [
                        {
                            "name": image["name"],
                            "image": image["source"],
                            "digest": image["digest"],
                            "mirrorRepository": image["mirror"].removeprefix(
                                "registry.example.com:5000/"
                            ),
                        }
                        for image in runtime_images
                    ],
                },
                sort_keys=False,
            )
        )
        output = root / "output"
        arguments = Namespace(
            contract=contract_path,
            release_contract=release_contract,
            release_contract_path="mirror/releases/iep-26.05.yaml",
            contract_path="data/package-contracts/clife-install-1.19.5.yaml",
            contract_repository="mahowlin/secure-ai-datacenter",
            contract_revision="a" * 40,
            executor_repository="mahowlin/secure-ai-airgap",
            executor_revision="b" * 40,
            archive=archive,
            embedded_rootfs=rootfs,
            helm_command="helm",
            output_dir=output,
        )
        package.build(arguments)
        return output

    def test_build_is_byte_reproducible_and_mode_aware(self):
        source = archive_bytes()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_output = self.build_once(first, source=source)
            second_output = self.build_once(second, source=source)
            first_chart = next(first_output.glob("*.tgz"))
            second_chart = next(second_output.glob("*.tgz"))
            self.assertEqual(first_chart.name, second_chart.name)
            self.assertEqual(first_chart.read_bytes(), second_chart.read_bytes())
            self.assertEqual(
                (first_output / "receipt-draft.yaml").read_bytes(),
                (second_output / "receipt-draft.yaml").read_bytes(),
            )

            with tarfile.open(first_chart, "r:gz") as archive:
                members = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
            joined = b"\n".join(members.values())
            self.assertIn(b"operator.openshift.io/v1", joined)
            self.assertIn(b".Values.networkOperator", joined)
            self.assertIn(b".Values.ciliumConfig", joined)
            self.assertNotIn(b"kind: Subscription", joined)
            self.assertIn(b"KUBERNETES_SERVICE_HOST", joined)
            self.assertIn(b"required \"clife.image is required\"", joined)

            receipt = yaml.safe_load((first_output / "receipt-draft.yaml").read_text())
            self.assertNotIn("publicationEnvironment", receipt["spec"])
            self.assertEqual(receipt["spec"]["embeddedChart"]["version"], "1.19.5")
            self.assertEqual(
                {item["name"] for item in receipt["spec"]["embeddedChart"]["scenarios"]},
                {"management", "tenant-a", "shared-ab"},
            )
            for scenario in receipt["spec"]["embeddedChart"]["scenarios"]:
                self.assertIn("normalizedRenderedManifestSha256", scenario)
                self.assertNotIn("renderedManifestSha256", scenario)
            self.assertEqual(receipt["spec"]["chart"]["ociDigest"], "")
            self.assertEqual(len(receipt["spec"]["inputInventory"]), len(RESOURCES))

    def test_render_hash_masks_only_declared_non_idempotent_secret_values(self):
        stable = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "stable"},
            "data": {"setting": "same"},
        }
        generated = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "generated",
                "labels": {"cilium.io/helm-template-non-idempotent": "true"},
            },
            "data": {"ca.crt": "first", "ca.key": "first"},
        }
        changed_generated = copy.deepcopy(generated)
        changed_generated["data"] = {"ca.crt": "second", "ca.key": "second"}
        first = package.normalized_rendered_manifest_sha256([stable, generated])
        second = package.normalized_rendered_manifest_sha256([stable, changed_generated])
        self.assertEqual(first, second)

        changed_keys = copy.deepcopy(generated)
        changed_keys["data"]["tls.crt"] = "new key"
        self.assertNotEqual(
            first,
            package.normalized_rendered_manifest_sha256([stable, changed_keys]),
        )

        changed_stable = copy.deepcopy(stable)
        changed_stable["data"]["setting"] = "changed"
        self.assertNotEqual(
            first,
            package.normalized_rendered_manifest_sha256([changed_stable, generated]),
        )

    def test_receipt_hashes_raw_input_before_transforming_emitted_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_once(directory)
            receipt = yaml.safe_load((output / "receipt-draft.yaml").read_text())["spec"]
            inputs = {
                (item["apiVersion"], item["kind"], item.get("namespace", ""), item["name"]): item
                for item in receipt["inputInventory"]
            }
            for resource in RESOURCES:
                key = (
                    resource["apiVersion"],
                    resource["kind"],
                    resource.get("namespace", ""),
                    resource["name"],
                )
                expected = hashlib.sha256(
                    yaml.safe_dump(resource_document(resource), sort_keys=True).encode()
                ).hexdigest()
                self.assertEqual(inputs[key]["normalizedSha256"], expected)

            transformed_key = ("apps/v1", "Deployment", "cilium", "clife-controller-manager")
            emitted = next(
                item
                for item in receipt["outputInventory"]
                if (
                    item["apiVersion"],
                    item["kind"],
                    item.get("namespace", ""),
                    item["name"],
                )
                == transformed_key
            )
            emitted_bytes = (output / "clife-install" / emitted["template"]).read_bytes()
            self.assertEqual(emitted["sha256"], hashlib.sha256(emitted_bytes).hexdigest())
            self.assertNotEqual(emitted["sha256"], inputs[transformed_key]["normalizedSha256"])

    def test_validation_values_follow_each_contract_scenario_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_once(directory)
            contract = yaml.safe_load((Path(directory) / "contract.yaml").read_text())
            scenarios = {
                item["name"]: item
                for item in contract["spec"]["embeddedChartVerification"]["scenarios"]
            }
            management = yaml.safe_load((output / "management-values.yaml").read_text())
            self.assertEqual(management["mode"], "management")
            self.assertEqual(management["ciliumConfig"], {})
            self.assertEqual(
                management["clife"]["kubernetesServiceHost"],
                scenarios["management"]["values"]["k8sServiceHost"],
            )
            for target in ("tenant-a", "shared-ab"):
                values = yaml.safe_load((output / f"{target}-values.yaml").read_text())
                self.assertEqual(values["mode"], "hosted")
                self.assertEqual(values["ciliumConfig"], scenarios[target]["values"])
                expected_cidrs = scenarios[target]["values"]["ipam"]["operator"][
                    "clusterPoolIPv4PodCIDRList"
                ]
                self.assertEqual(
                    [item["cidr"] for item in values["networkOperator"]["clusterNetwork"]],
                    expected_cidrs,
                )

    def test_embedded_chart_rejects_missing_or_unexpected_images(self):
        def mutate(contract):
            contract["spec"]["embeddedChartVerification"]["scenarios"][0][
                "expectedRuntimeImages"
            ] = ["cilium"]

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "image closure mismatch"):
                self.build_once(directory, contract_mutator=mutate)

    def test_extra_archive_resource_is_rejected(self):
        extra = identity("v1", "ConfigMap", "unexpected", "cilium")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "inventory mismatch"):
                self.build_once(directory, archive_bytes([*RESOURCES, extra]))

    @unittest.skipUnless(shutil.which("helm"), "helm is required for package render validation")
    def test_helm_lints_and_renders_closed_management_and_hosted_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_once(directory)
            chart = output / "clife-install"
            rendered = {}
            for target in ("management", "tenant-a", "shared-ab"):
                values = output / f"{target}-values.yaml"
                lint = subprocess.run(
                    ["helm", "lint", str(chart), "-f", str(values)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
                template = subprocess.run(
                    ["helm", "template", f"clife-{target}", str(chart), "-f", str(values)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(template.returncode, 0, template.stderr)
                rendered[target] = template.stdout
            self.assertNotIn("kind: Network", rendered["management"])
            self.assertNotIn("kind: CiliumConfig", rendered["management"])
            for target in ("tenant-a", "shared-ab"):
                self.assertIn("kind: Network", rendered[target])
                self.assertIn("deployKubeProxy: false", rendered[target])
                self.assertIn("kubeProxyReplacement: true", rendered[target])
                self.assertIn(f"name: {target}", rendered[target])

            for target in ("tenant-a", "shared-ab"):
                hosted_values = yaml.safe_load((output / f"{target}-values.yaml").read_text())
                for path in (("ciliumConfig", "cluster"), ("networkOperator", "defaultNetwork")):
                    invalid = copy.deepcopy(hosted_values)
                    invalid[path[0]][path[1]]["unreviewedDeployField"] = True
                    invalid_path = output / f"invalid-{target}-{path[0]}.yaml"
                    invalid_path.write_text(yaml.safe_dump(invalid, sort_keys=False))
                    lint = subprocess.run(
                        ["helm", "lint", str(chart), "-f", str(invalid_path)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_archive_hash_mismatch_is_rejected(self):
        def mutate(contract):
            contract["spec"]["source"]["sha256"] = "0" * 64

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "sha256 mismatch"):
                self.build_once(directory, contract_mutator=mutate)

    def test_unsafe_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "unsafe CLIFE archive member"):
                self.build_once(directory, archive_bytes([RESOURCES[0]], "../escape"))

    def test_finalize_writes_receipt_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_once(directory)
            receipt_path = Path(directory) / "receipt" / "receipt.yaml"
            package.finalize(
                Namespace(
                    draft=output / "receipt-draft.yaml",
                    oci_digest="sha256:" + "2" * 64,
                    workflow_run_id="1234",
                    created_at="2026-09-01T12:00:00Z",
                    output=receipt_path,
                )
            )
            receipt = yaml.safe_load(receipt_path.read_text())
            self.assertEqual(receipt["spec"]["chart"]["ociDigest"], "sha256:" + "2" * 64)
            self.assertEqual(receipt["spec"]["workflow"]["runId"], "1234")
            checksum = (receipt_path.parent / "receipt.sha256").read_text()
            self.assertTrue(checksum.endswith("  receipt.yaml\n"))

    def test_registry_lookup_only_accepts_digest_or_confirmed_absence(self):
        self.assertEqual(package.classify_registry_lookup(0, "sha256:" + "a" * 64), "present")
        self.assertEqual(
            package.classify_registry_lookup(1, "MANIFEST_UNKNOWN: manifest unknown"),
            "absent",
        )
        for output in ("unauthorized: authentication required", "dial tcp: network unreachable", "404"):
            with self.subTest(output=output), self.assertRaisesRegex(SystemExit, "ambiguous"):
                package.classify_registry_lookup(1, output)

    def test_workflow_rejects_non_main_or_stale_executor_before_publish(self):
        workflow = (ROOT / ".github" / "workflows" / "publish-clife-package.yaml").read_text()
        publish_job = workflow.index("\n  publish:")
        non_main_guard = workflow.index('test "${{ github.ref }}" = "refs/heads/main"')
        stale_executor_guard = workflow.index('test "$executor_revision" = "$main_revision"')
        self.assertLess(non_main_guard, publish_job)
        self.assertLess(stale_executor_guard, publish_job)
        self.assertIn("needs: validate-executor-lineage", workflow)
        self.assertNotIn("iep-package-publication", workflow)
        self.assertNotIn("PUBLICATION_ENVIRONMENT", workflow)
        self.assertNotIn("--publication-environment", workflow)
        self.assertNotIn(".contract-source", workflow)
        self.assertNotIn("repository: ${{ env.CONTRACT_REPOSITORY }}", workflow)
        promoted_input_guard = workflow.index("python3 scripts/verify-clife-package-input.py")
        credentialed_release = workflow.index("ISOVALENT_REGISTRY_TOKEN")
        self.assertLess(promoted_input_guard, credentialed_release)
        self.assertIn('--target "$RUNNER_TEMP/clife-package-python"', workflow)
        self.assertIn('echo "PYTHONPATH=$RUNNER_TEMP/clife-package-python" >> "$GITHUB_ENV"', workflow)
        self.assertNotIn("--break-system-packages", workflow)
        self.assertIn("CRANE_VERSION: 0.22.0", workflow)
        self.assertIn(
            "CRANE_LINUX_X86_64_SHA256: edb74d53fad9a596860f59d1c5d04a43dfb5f441dc71f57060dd0bf39483c833",
            workflow,
        )
        self.assertIn('sha256sum --check --strict', workflow)
        self.assertLess(workflow.index("Install pinned crane"), promoted_input_guard)
        self.assertNotIn("crane digest --insecure \"$chart_reference\" 2>/dev/null", workflow)
        self.assertEqual(workflow.count('helm pull "oci://${chart_repository}"'), 1)
        self.assertLess(workflow.index("helm push"), workflow.index('helm pull "oci://${chart_repository}"'))


if __name__ == "__main__":
    unittest.main()
