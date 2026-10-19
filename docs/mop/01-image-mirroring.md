# Select and deliver images

The maintained inventories describe actual source images, catalogs and exact release metadata. They are inputs to production, not proof of an installed site or a universally complete vendor catalog.

| Source selection | Maintained input | Needed for |
|---|---|---|
| OpenShift payload/channel and KubeVirt container disk | [mirror/platform-images.yaml](../../mirror/platform-images.yaml) | Management bootstrap; selected hosted release/disk dependencies |
| Selected recipe/contracts | [mirror/isovalent-selection.yaml](../../mirror/isovalent-selection.yaml) | One binding through source discovery, copy, destination verification and receipt hashes |
| CLife official archive, manager and runtime digests | [mirror/releases/clife-1.19.7.yaml](../../mirror/releases/clife-1.19.7.yaml), [Isovalent inventory](../../mirror/isovalent-images.yaml) | Exact selected Day 1 CNI and selected Isovalent components |
| Red Hat operator catalog packages/channels | [mirror/redhat-operators.yaml](../../mirror/redhat-operators.yaml) | Selected GitOps, virtualization, MCE and other operators |
| Other selected products/add-ons | [mirror/other-images.yaml](../../mirror/other-images.yaml), maintained product inputs | Only the features chosen for the composition |
| Mirror destination/source mappings | [mirror/mirror-policy-sources.yaml](../../mirror/mirror-policy-sources.yaml), environment/project contract | Proposed IDMS/ITMS and matching actual registry content |
| Hosted-cache complete selected closure | Customer-owned versioned inventory bound to Datacenter selection | Hosted release components, exact architecture/disk, guest CNI and selected add-ons |

## Production and consumer phases

Before management boot, the selected payload, installer/CNI images, official archive, DNS/time, entitlement, credentials and trusted registry must be available. A later management-hosted mirror operator copies selected content from the preceding registry to its hosted-image cache. For that composition the operator is part of the design, not the registry server itself. The cache cannot supply its own management bootstrap.

Hosted control-plane creation may resolve the release and import disks before any guest worker exists. The control-plane phase therefore needs its exact selected release/disk roots ready; the worker phase requires the remaining selected guest/add-on closure. Keep unknown dependencies explicit and refresh the reviewed inventory when releases/features change.

## Credentials and trust

Source entitlement, preceding-registry writer, cache writer and reader/pull identities have different roles. Supply those through the actual workflow/contract Secret references, not public input values. Trust uses normal system roots or the explicitly selected additional CA. Generated policy points at a registry; authenticated production must place the matching content there. The reader check verifies bytes and selected configuration on that verifier's path, not a cold guest pull or offline boot.

[AGENTS execution](../../AGENTS.md) links exact local policy generation, writing pipeline contracts and read-side checks. [The selected-cache lifecycle](https://github.com/mahowlin/secure-ai-datacenter/blob/main/docs/architectures/HCP_CACHE_LIFECYCLE.md) explains review/provenance and activation. [Registry maintenance](06-hcp-local-registry-trust.md) covers identities and trust rotation. Existing site registries and bare-metal-only installations need not select the hosted-cache composition.

The selected release binding checks only its explicitly listed release content; additional catalog/add-on inputs retain their own qualification. Destination verification uses the configured private auth file and normal client trust, as specified in [agent execution](../../agents/operations.md#authenticated-destination-verification). A source inventory or policy receipt is not evidence of anonymous access, cold pulls or a completed live rollout.
