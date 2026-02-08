# Image Mirroring

## Overview

Mirror container images from public registries to the internal registry for air-gapped cluster deployments.

## Prerequisites

- GitHub CLI authenticated (`gh auth login`)
- Access to mahowlin/saif-sys-admin repository
- `REDHAT_PULL_SECRET` configured in GitHub Secrets

## Procedure: Add New Images

### Step 1: Identify Required Images

Determine what images need to be mirrored:
- Operator images → `mirror/redhat-operators.yaml`
- Platform images → `mirror/platform-images.yaml`
- Community images → `mirror/other-images.yaml`

### Step 2: Update Image List

Edit the appropriate file in `mirror/`:

```yaml
# Example: Adding a new operator
- name: registry.redhat.io/my-operator/controller
  tag: v1.0.0
```

### Step 3: Preview Changes

```bash
gh workflow run sync-images.yaml \
  -f dry_run=true \
  --repo mahowlin/saif-sys-admin
```

Watch the workflow:
```bash
gh run watch --repo mahowlin/saif-sys-admin
```

Review the output to confirm expected images will be mirrored.

### Step 4: Execute Mirroring

```bash
gh workflow run sync-images.yaml \
  -f dry_run=false \
  --repo mahowlin/saif-sys-admin
```

### Step 5: Sync IDMS to GitOps

After mirroring completes, IDMS manifests are updated in `mirror/idms/`. These need to be synced to saif-gitops:

```bash
# In saif-gitops repo
./scripts/sync-idms-from-sys-admin.sh
git add apps/platform-idms/
git commit -m "fix: Update IDMS from sys-admin"
git push
```

### Step 6: Verify

```bash
# Check image exists in registry
curl -s https://registry.example.com:5000/v2/<image>/tags/list

# Check IDMS applied to cluster
oc get imagedigestmirrorset
```

---

## Procedure: Full Resync

When many images need updating or registry corruption suspected:

```bash
# Full resync (takes longer)
gh workflow run sync-images.yaml \
  -f dry_run=false \
  -f force_resync=true \
  --repo mahowlin/saif-sys-admin
```

---

## Image List Files

### redhat-operators.yaml

Red Hat certified operators from registry.redhat.io:
- GPU Operator
- NIM Operator
- LVMS Operator
- Sealed Secrets

### platform-images.yaml

OpenShift platform images:
- OpenShift release images
- Machine config operator
- Console images

### other-images.yaml

Community and third-party images:
- Cilium/Tetragon (quay.io/isovalent)
- Vector (docker.io/timberio)
- ClickHouse (ghcr.io)

---

## Troubleshooting

### Image Pull Fails

```bash
# Check if image is mirrored
curl -s https://registry.example.com:5000/v2/<image>/tags/list

# If not found, add to image list and re-run sync-images
```

### IDMS Not Working

```bash
# Check IDMS exists
oc get imagedigestmirrorset

# Check MachineConfigPool status (IDMS triggers reboot)
oc get mcp

# Check catalog pods restarted
oc get pods -n openshift-marketplace
```

### Registry Connection Refused

```bash
# SSH to runner VM and check registry
ssh runner-vm
docker ps | grep registry
docker logs registry 2>&1 | tail -20
```
