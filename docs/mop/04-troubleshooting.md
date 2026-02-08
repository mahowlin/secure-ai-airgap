# Troubleshooting

## Common Issues and Solutions

### Workflow Stuck in Queue

**Symptoms:**
- Workflows show "Queued" status indefinitely
- No runners available

**Causes:**
1. All runners busy
2. Runners offline or crashed
3. Runner restart loop

**Solution:**

```bash
# Check runner status
gh api /orgs/mahowlin/actions/runners --jq '.runners[] | {name, status, busy}'

# If all offline, reconfigure runners
gh workflow run configure-runner-vm.yaml -f tags=runner --repo mahowlin/saif-sys-admin

# Check runner logs
ssh runner-vm "docker logs runner-1 2>&1 | tail -50"
```

---

### ImagePullBackOff on Cluster

**Symptoms:**
- Pods stuck in `ImagePullBackOff`
- Error: "image not found" or "connection refused"

**Causes:**
1. Image not mirrored
2. IDMS not applied
3. Registry down

**Solution:**

```bash
# 1. Check if image is mirrored
curl -s https://registry.example.com:5000/v2/<image-name>/tags/list

# 2. If not mirrored, add to image list and sync
gh workflow run sync-images.yaml -f dry_run=false --repo mahowlin/saif-sys-admin

# 3. Check IDMS is applied
oc get imagedigestmirrorset

# 4. If IDMS missing, sync from sys-admin to gitops-apps
# In saif-gitops:
./scripts/sync-idms-from-sys-admin.sh
git add apps/platform-idms/ && git commit -m "fix: Update IDMS" && git push

# 5. Check registry is accessible
curl -s https://registry.example.com:5000/v2/_catalog
```

---

### Registry Connection Refused

**Symptoms:**
- Cannot pull images from registry.example.com:5000
- `curl` to registry fails

**Causes:**
1. Registry container stopped
2. Docker daemon down
3. Network issue

**Solution:**

```bash
# SSH to runner VM
ssh runner-vm

# Check registry container
docker ps | grep registry

# If not running, restart
docker start registry

# If container doesn't exist, reconfigure
# (exit SSH, run from workstation)
gh workflow run configure-runner-vm.yaml -f tags=registry --repo mahowlin/saif-sys-admin

# Check disk space (registry may have filled disk)
df -h /data
```

---

### ISO Boot Fails (vMedia)

**Symptoms:**
- Intersight vMedia mount fails
- Server cannot boot from ISO

**Causes:**
1. nginx not running
2. ISO file missing
3. Wrong URL (Intersight strips colons)

**Solution:**

```bash
# Check nginx is running
ssh runner-vm "docker ps | grep nginx"

# Check ISO exists
ssh runner-vm "ls -la /data/www/iso/"

# Restart nginx if needed
ssh runner-vm "docker restart nginx"

# Verify URL works (must be port 80, not 8080)
curl -I http://10.0.0.10/iso/agent.x86_64.iso
```

---

### User Cannot Login

**Symptoms:**
- HTPasswd login fails
- "Invalid credentials" error

**Causes:**
1. User not created properly
2. HTPasswd secret not updated
3. OAuth pods unhealthy

**Solution:**

```bash
# Check OAuth pods
oc get pods -n openshift-authentication

# Check HTPasswd secret exists
oc get secret htpasswd-secret -n openshift-config

# List users in HTPasswd
oc extract secret/htpasswd-secret -n openshift-config --to=- | grep <username>

# Re-create user if needed
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=ai-pod-1 \
  -f users=username \
  -f emails=user@cisco.com \
  -f password='NewPassword123!' \
  -f role=cluster-admin \
  -f action=create \
  --repo mahowlin/saif-sys-admin
```

---

### Terraform State Locked

**Symptoms:**
- UCS workflows fail with "state locked"
- Cannot apply Terraform changes

**Causes:**
1. Previous workflow crashed
2. Terraform state backend down

**Solution:**

```bash
# Check terraform-backend container
ssh runner-vm "docker ps | grep terraform"

# If not running, restart
ssh runner-vm "docker restart terraform-backend"

# If state is truly locked, force unlock (use with caution)
cd saif-ai-pod/terraform
terraform force-unlock <lock-id>
```

---

### IDMS Not Taking Effect

**Symptoms:**
- IDMS applied but images still pull from original registry
- MachineConfigPool not updating

**Causes:**
1. IDMS syntax error
2. MCP update in progress
3. Nodes not rebooted

**Solution:**

```bash
# Check IDMS status
oc get imagedigestmirrorset
oc describe imagedigestmirrorset <name>

# Check MachineConfigPool status
oc get mcp
# Wait for UPDATING=False, DEGRADED=False

# Force catalog refresh after IDMS applies
oc delete pod -n openshift-marketplace -l olm.catalogSource=certified-operators-local
```

---

## Diagnostic Commands

### Runner VM Health

```bash
# All services status
ssh runner-vm "docker ps --format 'table {{.Names}}\t{{.Status}}'"

# Disk usage
ssh runner-vm "df -h /data"

# Docker disk usage
ssh runner-vm "docker system df"
```

### Registry Health

```bash
# List all repositories
curl -s https://registry.example.com:5000/v2/_catalog | jq

# List tags for specific image
curl -s https://registry.example.com:5000/v2/<image>/tags/list | jq

# Registry container logs
ssh runner-vm "docker logs registry 2>&1 | tail -50"
```

### Workflow Status

```bash
# List recent runs
gh run list --repo mahowlin/saif-sys-admin

# View specific run
gh run view <run-id> --repo mahowlin/saif-sys-admin

# Watch running workflow
gh run watch --repo mahowlin/saif-sys-admin
```

---

## Getting Help

1. Check workflow logs in GitHub Actions
2. SSH to runner VM and check container logs
3. Review repo documentation for known gotchas
4. Check [saif-platform troubleshooting index](https://github.com/mahowlin/saif-platform/blob/main/docs/runbooks/TROUBLESHOOTING.md)
