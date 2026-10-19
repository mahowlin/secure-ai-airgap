# Cisco Secure AI Airgap

Run the shared infrastructure services and image-delivery layer for [Cisco Secure AI Datacenter](https://github.com/mahowlin/secure-ai-datacenter). This repository configures the external services host, mirrors vendor images into its registry, and supplies the image configuration consumed by OpenShift installation and nested tenant clusters.

[![Vendor images enter the external site registry. Management OpenShift boots from that registry; its mirror operator copies selected images into an in-cluster cache for tenant control-plane pods and KubeVirt worker VMs. The external host also supplies runners, installer media, state and S3 services.](docs/diagrams/airgap-overview.svg)](docs/diagrams/airgap-overview.svg)

## Services this repository provides

| Service | Its place in the platform | Where to start |
|---|---|---|
| **Container registry** | Holds mirrored OpenShift, Isovalent, storage and selected AI images for installation and runtime pulls | [Image mirroring](docs/mop/01-image-mirroring.md) |
| **GitHub Actions runners** | Execute the Datacenter, Compute, Fabric and service pipelines | [Runner/services setup](docs/mop/03-runner-vm.md) |
| **Installer-media hosting** | nginx/WebDAV serves the Agent ISO produced by Compute for server boot | [Service-host architecture](docs/ARCHITECTURE.md#service-host) |
| **S3 object storage** | RustFS stores Hubble flow exports consumed by Timescape | [Environment settings](environments/example/group_vars.yml) |
| **Terraform state service** | Stores and locks infrastructure execution state | [Environment settings](environments/example/group_vars.yml) |

[Environment settings](environments/example/group_vars.yml) define the service ports, storage, runner capacity and enabled components; [hosts.ini](environments/example/hosts.ini) selects the host. The [configure-runner playbook](ansible/playbooks/configure-runner.yaml) applies that profile through the owning pipeline. Gitea remains a legacy cache; GitHub is the platform's source and workflow system.

## Vendor images → YAML definitions

| Vendor / image family | What the inventory contains | Maintained YAML |
|---|---|---|
| **Red Hat: OpenShift** | Release payload and RHCOS container disks for KubeVirt workers | [platform-images.yaml](mirror/platform-images.yaml) |
| **Red Hat: operators** | OpenShift GitOps, Virtualization, MCE, NMState and selected platform operators | [redhat-operators.yaml](mirror/redhat-operators.yaml) |
| **Isovalent (Cisco)** | CLife, Cilium, Hubble/Timescape and Tetragon selections | [isovalent-selection.yaml](mirror/isovalent-selection.yaml) → [isovalent-images.yaml](mirror/isovalent-images.yaml) |
| **Pure Storage / Portworx** | Storage operator, storage engine, CSI and related components | [portworx-images.yaml](mirror/portworx-images.yaml) |
| **NVIDIA** | GPU Operator, NIM and selected CUDA images | [other-images.yaml](mirror/other-images.yaml) |
| **Supporting observability/tools** | Selected ClickHouse, Splunk/OpenTelemetry and utility images | [other-images.yaml](mirror/other-images.yaml) |

Isovalent's selection links the exact [CLife release](mirror/releases/clife-1.19.7.yaml) and [IEP release](mirror/releases/iep-26.05.yaml) contracts. [mirror-policy-sources.yaml](mirror/mirror-policy-sources.yaml) supplies additional source-prefix mappings for generated IDMS/ITMS policy. Change the maintained inventories to change the selected images.

## Follow the image architecture

1. **Vendor sources → external registry.** The [Sync Images workflow](.github/workflows/sync-images.yaml) reads the YAML inventories below and mirrors their selected content into the site registry.
2. **External registry → management OpenShift.** Compute uses the selected release, CNI and operator content to install the physical management cluster.
3. **External registry → mirror operator → in-cluster registry/cache.** The Kubernetes mirror operator copies its selected image inventory into persistent storage on the management cluster.
4. **In-cluster images → nested tenant clusters.** Tenant control-plane pods and KubeVirt worker VMs consume the selected image repositories through their configured mirror mappings.

This is **nested Kubernetes (“Kubernetes in Kubernetes”)**: HyperShift control planes run as pods on management OpenShift, while tenant worker nodes run inside KubeVirt VMs. Airgap supplies the external registry and services; Datacenter composes the cluster configuration, and GitOps delivers the in-cluster registry, mirror-operator and tenant resources.

[Registry-chain architecture](docs/REGISTRY_CHAIN.md) expands these connections. [Cache trust and credentials](docs/mop/06-hcp-local-registry-trust.md) explains the source-pull, destination-write and workload-read paths.

## Configure and use it

1. Copy the [example environment](environments/example/) and set your service host, endpoints, storage and runner inputs. [Project inputs](docs/PROJECT_INPUTS.md) explains how Datacenter's generated contract supplies shared values.
2. Configure the services through the [runner pipeline](docs/mop/03-runner-vm.md), then run the [image mirroring procedure](docs/mop/01-image-mirroring.md) for the selected inventories.
3. Pass the resulting image policies and content receipts into the Datacenter, Compute and GitOps handoffs.

[AGENTS.md](AGENTS.md) links exact checks and execution procedures. [Troubleshooting](docs/mop/04-troubleshooting.md) covers service and mirroring failures; [operator artifact preparation](.github/actions/prepare-operator-inputs/action.yaml) provides Datacenter's pinned build-time inspection inputs.

## License and help

See [LICENSE](LICENSE) and the shared [contribution/help guide](https://github.com/mahowlin/secure-ai-datacenter/blob/main/CONTRIBUTING.md). Product images and archives require the appropriate vendor entitlement; private credentials remain in the site's pipeline custody.
