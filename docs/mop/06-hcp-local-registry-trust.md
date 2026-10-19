# Hosted registry trust and credential maintenance

Use this procedure after selecting a management-side registry and its hosted-cluster consumers. The [registry lifecycle contract](../REGISTRY_CHAIN.md) describes the external sources → site bootstrap registry → management registry/cache → hosted image consumers. A customer-owned registry is also valid. The site bootstrap registry must be available before management OpenShift exists.

This is an operator handoff, with read-only inspection in the linked agent procedure. Changes run through the owning registry/runtime and source/GitOps pipelines after the site's change authorization. Actual rotation and readiness require the site owners and observed evidence.

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

See the [exact metadata and TLS inspection procedure](../../agents/registry-trust.md#2-inspect-references-without-exposing-credentials).

Compare the displayed live references with the generated selection; a difference is drift to resolve with the owner. The Secret command displays metadata/type only; it still requires authorized Secret API access. Do not dump Secret data, pull-secret JSON, private keys or kubeconfigs into a terminal capture, Git or a report. Use the site's private verification process for auth-host coverage and pull-only scope. Management mirror push credentials and guest pull credentials have separate roles.

Set the intended registry DNS name, endpoint and trusted CA file from the selected exposure/issuer. Where the site's normal read-only TLS check is appropriate:

See the [exact metadata and TLS inspection procedure](../../agents/registry-trust.md#2-inspect-references-without-exposing-credentials).

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
