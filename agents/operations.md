# Image production and site services

## Generate mirror policy locally

Use the requirements environment in [AGENTS](../AGENTS.md). From this checkout root, choose the actual registry; output goes to a fresh temporary directory. This renders IDMS/ITMS policy and contacts no registry.

```bash
: "${LOCAL_REGISTRY:?Set selected DNS-host:port}"
POLICY_DIR="$(mktemp -d)"
.venv/bin/python scripts/generate-mirror-policy.py \
  --config mirror/isovalent-images.yaml \
  --source-config mirror/mirror-policy-sources.yaml \
  --local-registry "$LOCAL_REGISTRY" --name isovalent-selected --output-dir "$POLICY_DIR"
```

Repeat with identical inputs to compare bytes. A changed destination must change references. The policy is not evidence that image content exists.

## Source production and bundle handoff

The single maintained [isovalent-selection.yaml](../mirror/isovalent-selection.yaml) binds the copy ImageSet to its exact release contracts/modes. The existing job resolves it before authenticated discovery, uses the same recipe for copying/policy generation and verifies those contracts at the destination. Source/destination evidence includes the same selection identity; the existing bundle promoter hashes the selection, recipe and contract inputs. Change that input coherently for another inventory, not a version literal in workflow code. Multiple explicitly selected contracts can cover an inventory intentionally retaining multiple releases.

Local binding inspection contacts no registry:

```bash
.venv/bin/python scripts/inspect-isovalent-release.py \
  --selection mirror/isovalent-selection.yaml --selection-only \
  --output selected-isovalent-inputs.json
```


Review the actual inventory inputs in [the human guide](../docs/mop/01-image-mirroring.md) and the declared inputs in [`sync-images.yaml`](../.github/workflows/sync-images.yaml). The workflow requires `environment`; optional `project_contract` is a staged generated metadata path. Select `sync_target` and matching `promote_scope`. There is no general `dry_run`: `validate_only` is implemented only for its declared Portworx/selected-cache cases with `promote_scope=none`.

A normal mirror run authenticates to selected sources/destination, writes registry content and may promote immutable policy bundles. Supply source entitlement and separate destination writer credentials via the owning runtime references. Do not invoke a writing workflow to validate this documentation. The exact selected-cache source/read commands and phase ordering are maintained in [Datacenter's selected-cache procedure](https://github.com/mahowlin/secure-ai-datacenter/blob/main/agents/hosted-cache.md).

A site-authorized invocation of the existing selected Day 1 product producer is:

```bash
: "${AIRGAP_REPOSITORY:?Set the actual repository}"
: "${REVIEWED_BRANCH:?Set reviewed source ref}"
: "${ENVIRONMENT:?Set configured environment profile}"
gh workflow run sync-images.yaml --repo "$AIRGAP_REPOSITORY" \
  --ref "$REVIEWED_BRANCH" -f environment="$ENVIRONMENT" \
  -f sync_target=isovalent-images -f promote_scope=cilium-day1
```

If using composed execution, also pass `project_contract` with the staged metadata path. For selected-cache production choose `sync_target=hcp-cache`, `promote_scope=none` and the staged `hcp_cache_contract` path; its complete inventory/receipt sequence is linked above. These are real registry-writing operations, not local validation examples.

Pinned operator bootstrap uses [`sync-hcp-oc-mirror-operator.yaml`](../.github/workflows/sync-hcp-oc-mirror-operator.yaml) and its exported producer contract; that workflow does not seed the hosted production cache. The production `hcp-cache` target reuses the existing selected inventory and bundle promoter. Read-only mode of `scripts/hcp-cache-images.py` verifies raw graph content and cannot copy. Native pull/bootstrap remains a separate qualification.

## Configure site services

[`configure-runner-vm.yaml`](../.github/workflows/configure-runner-vm.yaml) owns persistent Ansible rollout of the selected `environments/<site>/hosts.ini` and `group_vars.yml`. Runtime references include host SSH trust, runner registration, registry auth and object-store credentials. `project_contract` is staged and passed as `project_contract_file`; conflicting canonical settings fail.

The configured runner/toolchain must supply [ansible/requirements.yml](../ansible/requirements.yml): community.general12.2.0, ansible.posix2.1.0 and community.docker5.0.4. In an environment where dependency installation is part of the site's toolchain preparation, the existing requirements file is consumed with:

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

This acquires tools, not host configuration. Do not infer that a workstation's bare ansible-core has these collections. A missing collection blocks the syntax check; it is not evidence of a rollout failure.

Local syntax check only, with an actual reviewed inventory/profile available:

```bash
: "${ENVIRONMENT:?Set environments profile name}"
ansible-playbook -i "environments/$ENVIRONMENT/hosts.ini" \
  ansible/playbooks/configure-runner.yaml -e "env=$ENVIRONMENT" --syntax-check
```

Syntax validation changes no host, but input loading still needs the site's normal runtime environment. VM provisioning is a separate selected pipeline and not a portable OpenShift prerequisite. See [runner ownership](../docs/mop/03-runner-vm.md).

## Read-only registry inspection

Use a normal trusted endpoint; never suppress TLS checks. Supply auth privately through the site's tooling when required. Registry content listing is not a complete digest/architecture/cold-pull proof. For consumer identities/CA and endpoint checks use [registry trust](registry-trust.md); it owns those commands once.

## Authenticated destination verification

The selected sync job uses `prepare-isovalent-auth.py` to create a unique mode0700 directory with a mode0600 auth file. It never changes runner HOME. Supply `REDHAT_PULL_SECRET`, `ISOVALENT_REGISTRY_USERNAME` and `ISOVALENT_REGISTRY_TOKEN` through the approved runtime environment. The pull secret needs its own exact destination authority entry; entitlement to the source is not destination authorization.

The same selected CA, auth and destination bind copy, verification and OCI chart publication. Empty `destination.caBundle` means system trust; otherwise stage the reviewed public CA at its declared path. The certificate directory is required even for system trust. The runner must already provide Skopeo. From the checkout root in the requirements environment:

```bash
set -euo pipefail
umask 077
VERIFY_PRIVATE_DIR="$(mktemp -d)"
trap 'rm -rf -- "$VERIFY_PRIVATE_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
.venv/bin/python scripts/prepare-isovalent-auth.py \
  --selection mirror/isovalent-selection.yaml --output-root "$VERIFY_PRIVATE_DIR" \
  --github-env "$VERIFY_PRIVATE_DIR/client.env"
# Read generated assignments as data, not executable shell source.
while IFS='=' read -r key value; do export "$key=$value"; done < "$VERIFY_PRIVATE_DIR/client.env"
REGISTRY_URL="$(.venv/bin/python -c 'import yaml; print("https://" + yaml.safe_load(open("mirror/isovalent-selection.yaml"))["destination"]["registry"])')"
.venv/bin/python scripts/verify-isovalent-mirror.py \
  --selection mirror/isovalent-selection.yaml --registry "$REGISTRY_URL" \
  --auth-file "$REGISTRY_AUTH_FILE" --cert-dir "$ISOVALENT_CERT_DIR" \
  --output selected-mirror-verified.json
```

The non-secret receipt checks selected manifest identities on this client path, not full blobs, signatures or native/cold pulls. Keep credential-bearing diagnostics out of logs/artifacts. Cleanup covers normal completion and shell errors/signals; abrupt runner loss remains operator-owned.
