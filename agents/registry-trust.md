# Hosted registry trust and credential maintenance

Use this procedure after selecting a management-side registry and its hosted-cluster consumers. The [registry lifecycle contract](../docs/REGISTRY_CHAIN.md) describes the external sources → site bootstrap registry → management registry/cache → hosted image consumers. A customer-owned registry is also valid. The site bootstrap registry must be available before management OpenShift exists.

This is an operator handoff, with read-only inspection commands below. Changes run through the owning registry/runtime and source/GitOps pipelines after the site's change authorization. Preparing these documents performed no credential access, rotation, restart, image pull or live change.

## 1. Recover the selected contract and owners

| Value | Source of truth | Verify against |
|---|---|---|
| Management cluster and selected release | `data/clusters/<management>/cluster.yaml`, `platform-release.yaml` | Installed cluster/operator versions and selected image receipt |
| Registry namespace, workload, pull/sync Services and listeners | `data/clusters/<management>/local-registry.yaml` | Actual Deployment, PVC, Services and served certificate |
| Hosted registry endpoint, exposure and consumers | Selected registry/exposure input and generated output | DNS, endpoint/port, certificate SANs and reachability from each selected worker attachment model |
| Hosted pull-secret and CA bundle references | Generated HostedCluster `spec.pullSecret.name` and `spec.additionalTrustBundle.name` | Same fields on the live HostedCluster and referenced object metadata in its namespace |
| Registry CA source, certificate/private-key and credential custody | Site's selected signer and runtime delivery owner | Actual issuer/served chain, credential scope and private delivery record |
| Management node registry trust | Live `image.config.openshift.io/cluster.spec.additionalTrustedCA` and its owning source | Referenced ConfigMap and effective node/MachineConfigPool reconciliation |
| Private CA/credential payloads and registration | Runtime delivery pipeline/private target | Required objects exist; credential contents stay private |

The current renderer wires ordinary hosted CA/pull-secret references in `scripts/gitops_renderer/hosted_clusters.py`. When external registry exposure is explicitly enabled, the selected overrides come from `local-registry.yaml` → `spec.externalPull.consumer.pullSecret` / `trustBundle`, with attachment kinds and deployment subject roles selecting consumers. `day2GitOps.hostedClusterLifecycle.localRegistryConsumer` is retired and rejected by the resolver. Do not use that former input path.

The public example declares `externalPull.enabled: true` and keeps `cacheProduction.mode: preceding-registry`. This prepares external exposure; it does not prove a reachable, populated hosted-worker cache. Select and validate the actual exposure or existing customer registry before using it. A management Service DNS name, namespace, trust-bundle name or tenant list must not be copied from another site.

For a selected generated lifecycle overlay, `registry-ca-bundle.yaml` is a target-owned runtime handoff; the renderer does not create its payload. The secret overlays and CA aggregate identified in the GitOps preparation likewise require private enrichment. Complete instructions can be prepared without real customer secrets; rollout waits for those objects and API destinations.

## 2. Inspect references without exposing credentials

Set these paths/names from the selected canonical input and generated HostedCluster file, using the reviewed `secure-ai-datacenter` checkout. `yq` below is Mike Farah yq v4. Keep the cluster kubeconfig in the site's private runtime location.

```bash
set -euo pipefail
: "${KUBECONFIG:?Set the private management-cluster kubeconfig path}"
oc config current-context
: "${REGISTRY_INPUT:?Set the selected local-registry.yaml path}"
: "${HOSTED_MANIFEST:?Set the selected generated HostedCluster YAML path}"
REGISTRY_NAMESPACE=$(yq -er '.spec.namespace' "$REGISTRY_INPUT")
REGISTRY_WORKLOAD=$(yq -er '.spec.workloadName' "$REGISTRY_INPUT")
HC_NAMESPACE=$(yq -er '.metadata.namespace' "$HOSTED_MANIFEST")
HC_NAME=$(yq -er '.metadata.name' "$HOSTED_MANIFEST")
TRUST_BUNDLE_NAME=$(yq -er '.spec.additionalTrustBundle.name' "$HOSTED_MANIFEST")
PULL_SECRET_NAME=$(yq -er '.spec.pullSecret.name' "$HOSTED_MANIFEST")

oc -n "$REGISTRY_NAMESPACE" get deployment "$REGISTRY_WORKLOAD"
oc -n "$REGISTRY_NAMESPACE" get services,persistentvolumeclaims
oc -n "$HC_NAMESPACE" get hostedcluster "$HC_NAME" \
  -o jsonpath='{.spec.release.image}{"\n"}{.spec.additionalTrustBundle.name}{"\n"}{.spec.pullSecret.name}{"\n"}'
oc -n "$HC_NAMESPACE" get configmap "$TRUST_BUNDLE_NAME" \
  -o jsonpath='{.metadata.name}{" "}{.metadata.uid}{" "}{.metadata.resourceVersion}{"\n"}'
oc -n "$HC_NAMESPACE" get secret "$PULL_SECRET_NAME" \
  -o jsonpath='{.metadata.name}{" "}{.type}{"\n"}'
oc get image.config.openshift.io cluster \
  -o jsonpath='{.spec.additionalTrustedCA.name}{"\n"}'
oc get machineconfigpools
oc -n "$HC_NAMESPACE" get nodepools
```

Compare the displayed live references with the generated selection; a difference is drift to resolve with the owner. The Secret command displays metadata/type only; it still requires authorized Secret API access. Do not dump Secret data, pull-secret JSON, private keys or kubeconfigs into a terminal capture, Git or a report. Use the site's private verification process for auth-host coverage and pull-only scope. Management mirror push credentials and guest pull credentials have separate roles.

Set the intended registry DNS name, endpoint and trusted CA file from the selected exposure/issuer. Where the site's normal read-only TLS check is appropriate:

```bash
: "${REGISTRY_ENDPOINT:?Set selected DNS-host:port}"
: "${REGISTRY_DNS:?Set the certificate DNS name}"
: "${TRUSTED_CA_FILE:?Set the privately supplied trusted CA PEM path}"
openssl s_client -connect "$REGISTRY_ENDPOINT" -servername "$REGISTRY_DNS" \
  -verify_hostname "$REGISTRY_DNS" -verify_return_error \
  -CAfile "$TRUSTED_CA_FILE" </dev/null
```

TLS from the workstation proves only that client's path and trust. It does not prove a management process or guest CRI-O daemon trusts the certificate, nor that an authenticated image pull succeeds. Check the selected image endpoint and return routes from each worker model; Multus and Private Networks have different paths.

## 3. Prepare and deliver a bounded rotation

1. **Record the baseline privately.** Capture selected source/receipt hashes, current object identities/versions, CA fingerprint/expiry, registry/PVC health and consumer NodePool/Node health. Identify every shared trust owner and the rollback references. Establish the current signer and served chain; a Service CA and a site/external CA are different possible inputs, not interchangeable defaults.
2. **Prepare runtime material with its custodian.** Maintain pull-only guest access and separate registry push authority. Preserve other auth hosts and other trusted CA entries. Stage overlapping old/new trust when the selected issuer rotation requires it. Keep private keys and credential values out of public source; deliver the selected CA payload through its private target/runtime owner.
3. **Update canonical references and review generated output.** For an enabled external-pull selection, update its `consumer` references in `local-registry.yaml`, render with the existing datacenter renderer, and check the exact HostedCluster, source/generated/target hashes and private payload handoff. For other selected registry models, use their existing source contract and the actual generated HostedCluster references. Never hand-patch generated output or revive the retired lifecycle field.
4. **Reconcile through the owners.** The registry/runtime pipeline owns certificates and credentials. Datacenter/GitOps owns the selected declarative consumer references. Preserve the existing shared Image trust reference and all other registry keys; coordinate with that ConfigMap's owner. OpenShift registry trust keys use `host..port` for a non-default port; derive the key from the actual selected endpoint. Inspect the actual Argo Application's field ownership before delivery; no lab-specific ignore rule is assumed. A runtime-owned CA payload still needs an explicit owner and a source-consistent reference.
5. **Verify effective consumers before retiring old material.** Observe registry certificate and workload readiness, management trust/MCP convergence, HostedCluster and NodePool conditions, guest desired/current configuration and an authenticated pull of the selected digest through the intended endpoint. Distinguish a cached image from a transfer that exercised the registry. Retain the previous valid bundle/credential references and recovery image source until the site's acceptance is met. Record timestamps, certificate fingerprint, exact source/target/image identity and the coverage/limits of each check without secret values.

This procedure does not prescribe a HyperShift rollout, node command, CRI-O restart or traffic-deny test. If the selected build needs an additional refresh action, qualify its supported controller/runtime procedure and change scope with the owner first. Registry maintenance does not authorize replacing another cluster's trust or disrupting retained workers.

## 4. Failure and recovery

On an unexpected certificate chain, missing private object, source/live mismatch, failed pull or unhealthy consumer, stop the next rollout step. Preserve the registry PVC and old trust/credential material. Restore the last accepted source references through the owning pipelines and verify the selected recovery path. A shared trust rollback must preserve other registry keys and other owners' references. Never repair a trust failure by disabling TLS verification or printing credentials.

A successful local pull does not establish complete offline independence. Record exactly which endpoints and image layers were exercised; restricted or untested upstreams remain unverified.

## Historical selected-build evidence — not a generic procedure

The retained 2026-09-29 record for HyperShift revision `8317d4cce771d994fc1d37732c849be1c4bb968f` reported that its NodePool rollout hash used the **bundle name**, not bundle contents (`hypershift-operator/controllers/nodepool/config.go` and `token.go`). A content-only ConfigMap update did not establish a worker rollout in that build. Review the selected current controller source and supported rollout behavior before carrying this assumption to another release.

That record also reported a retained worker requiring `update-ca-trust extract` and a CRI-O restart before its running daemon accepted the changed CA. Host curl success alone did not prove daemon trust. Those interventions belong to that dated configuration; they are not automatic steps for a new deployment. The record also described an Application ignoring the original bundle data while a versioned runtime bundle had separate ownership; that is not a default Argo exemption. Its bounded upstream-deny experiment did not prove full offline independence. The former tenant names, dated bundle names and Application ignore behavior are not portable defaults or requirements.

## Producer contracts and execution details

Agent/operator procedure reference. Source review does not authorize a live run.

### Selected Isovalent mirror inputs and receipts

`mirror/isovalent-selection.yaml` selects the copy recipe, release contracts and
verification modes, the real destination authority/CA bundle, and the Day 2
runtime image roots. The public selection covers the management Timescape images and the separately selected hosted exporter image. Read exact pins from that input and the selected GitOps Applications; never substitute another repository's release choices. Change the selection and copy recipe together in a reviewed change. Historical/engineering images in the recipe are not automatically qualified selected consumers.

The resolver checks that every selected image is in the actual copy recipe.
`sync-images.yaml` uses that resolved recipe for copying and generating mirror
policy. Discovery and destination verification use the same selection; policy
bundle identity hashes the selection, recipe, release contracts and CA bytes.
Promotion refuses a registry that differs from the selected destination.

The verifier uses Skopeo's authenticated, normal TLS transport. It verifies
release-pinned destination manifests and compares raw source/destination
manifest hashes for the selected tag-based Day 2 images. Its receipt records
the actual observed digests and selected-input identity. A missing image,
authentication error, CA error or mismatched manifest fails the job. This is
manifest identity evidence, **not** full blob, catalog relatedImages, cold-pull,
or disconnected workload qualification. No successful historical receipt is
inferred from the current source configuration. Changes in upstream tags after
copying fail the comparison and require a new reviewed sync.

`prepare-isovalent-auth.py` creates a unique mode0700 job directory with a
mode0600 Docker auth file. `REDHAT_PULL_SECRET` must include a credential entry
for the selected destination (and any private source registry); existing
entries are preserved while Isovalent credentials are added from runtime env.
No credentials go into selection inputs or command arguments. The job exports
`DOCKER_CONFIG`, `REGISTRY_AUTH_FILE`, `HELM_REGISTRY_CONFIG`, Skopeo cert dir
and combined system-root/site-CA trust for Go clients. Both selected sync and
CLIFE publication remove this directory in an `always()` cleanup step, including
on failure. No shared runner `~/.docker/config.json` is overwritten by these jobs.
Older, unrelated workflows still have separate auth/TLS cleanup follow-up work.

### Optional CLIFE package input promotion

Official archive/manual installation remains independent of this optional
OCI Helm packaging path. Do not make publication or its receipt an installation
prerequisite. Datacenter owns the package contract, schema and semantic validator;
Airgap executes publication from a reviewed exact four-file promotion.

To produce a **new** bundle, obtain and verify the actual merged public Datacenter source revision and its `origin/main` reference. Use the caller's exact repository identity and contract version:

```bash
: "${DATACENTER_SOURCE:?Set the local public Datacenter checkout}"
: "${DATACENTER_REPOSITORY:?Set its actual owner/repository}"
: "${MERGED_REVISION:?Set the full merged source commit}"
: "${CLIFE_VERSION:?Set the selected contract version}"
.venv/bin/python scripts/promote-clife-package-input.py \
  --source "$DATACENTER_SOURCE" --repository "$DATACENTER_REPOSITORY" \
  --revision "$MERGED_REVISION" \
  --contract-path "data/package-contracts/clife-install-$CLIFE_VERSION.yaml" \
  --output "mirror/package-inputs/clife-install/$CLIFE_VERSION"
```

The local promoter checks origin identity and merged lineage, reads committed
Git blobs (never dirty working bytes), validates the original schema/semantics
and provenance hashes, and refuses to overwrite an existing bundle. It performs
no fetch, registry access, push or publication. Commit the new four-file bundle
in a normal reviewed change. Existing promoted artifacts and provenance are
immutable; do not hand-edit or fabricate receipts for them. The publisher
rechecks every byte before release/registry credentials are consumed.

Public package contracts and their canonical schema are in Datacenter. A changed destination requires a matching schema and a new honest promotion from the actual merged public source. No prior private provenance may attest changed public bytes. Bundled unpromoted contracts are source inputs, not publication receipts. Package publishing remains a separately authorized operation; source inspection and promotion preparation do not authorize registry writes.

## Operator inspection inputs

The Platform repository owns the dependency selection in
`data/package-contracts/oc-mirror-operator-v0.1.7.json` and the local-registry
`installation.artifactContractPath` / `artifactDirectory`. Airgap owns acquisition
and import. The contract pins upstream commit, bundle image digest, CSV and two
RoleBindings, bundle annotations and Apache-2.0 license hashes. No upstream
source/bundle directory is retained as Platform site code.

Before resolving an enabled operator integration, supply the declared prepared
input. From the Platform input root, using the selected requirements environment:

```bash
: "${AIRGAP_ROOT:?Set selected Airgap/sys-admin checkout}"
# Disconnected import: SOURCE_CACHE contains the declared upstream file layout.
: "${SOURCE_CACHE:?Set verified prepared-artifact-layout artifact/cache directory}"
python "$AIRGAP_ROOT/scripts/prepare-oc-mirror-artifacts.py" \
  --contract data/package-contracts/oc-mirror-operator-v0.1.7.json \
  --source-root "$SOURCE_CACHE" \
  --output .artifacts/oc-mirror-operator/v0.1.7
```

For explicit connected acquisition, replace `--source-root "$SOURCE_CACHE"` with
`--fetch`. The helper uses the existing pinned Crane client to read the immutable published
bundle and fetches LICENSE at the declared Git commit, with normal TLS. It
checks every selected checksum before writing; it does not use stale generated
CSV templates from Git in place of the published bundle.
It preserves conflicting cache files. Retain `artifact-receipt.json` and LICENSE
with the imported files. An unchanged prepared directory can be copied into a
disconnected build with the same declared path. The resolver rechecks the
contract/release join and all file hashes locally; it never downloads anything.

The existing Airgap `sync-hcp-oc-mirror-operator.yaml` workflow can prepare and
upload `oc-mirror-operator-inputs` with `artifacts_only=true`; the registry-writing
job is skipped in that mode. `artifact_source_path` selects a pre-staged cache
for disconnected preparation. Neither local preparation nor that mode installs
an operator or proves registry content. CI/build callers must stage this input
before renderer tests; a clean checkout alone no longer contains third-party
inspection bytes. Run only the selected artifact preparation under the site's
existing pipeline authority.
