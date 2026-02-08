# Runner VM Management

## Overview

Configure and maintain the runner VM (10.0.0.10) which hosts critical infrastructure services.

## Prerequisites

- GitHub CLI authenticated (`gh auth login`)
- Access to mahowlin/saif-sys-admin repository
- SSH access to runner VM (for manual troubleshooting)

## Services Overview

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| Container Registry | registry:2 | 5000 | Image storage |
| Web Server | nginx | 80 | ISO hosting |
| Terraform State | nimbolus/terraform-backend | 8081 | UCS state |
| GitHub Runner 1 | DinD | N/A | CI/CD execution |
| GitHub Runner 2 | DinD | N/A | CI/CD execution |
| GitHub Runner 3 | DinD | N/A | CI/CD execution |
| Gitea | gitea | 3000 | Internal git |

---

## Procedure: Reconfigure All Services

When runner VM needs full reconfiguration:

```bash
gh workflow run configure-runner-vm.yaml \
  -f tags=all \
  --repo mahowlin/saif-sys-admin
```

---

## Procedure: Fix Runner Restart Loop

GitHub runners sometimes enter restart loops. To fix:

```bash
gh workflow run configure-runner-vm.yaml \
  -f tags=runner \
  --repo mahowlin/saif-sys-admin
```

This will:
1. Stop runner containers
2. Clean up runner workspaces
3. Generate fresh runner tokens
4. Restart runners with correct configuration

---

## Procedure: Restart Registry

When registry is unresponsive:

```bash
gh workflow run configure-runner-vm.yaml \
  -f tags=registry \
  --repo mahowlin/saif-sys-admin
```

Or manually via SSH:
```bash
ssh runner-vm
docker restart registry
```

---

## Procedure: Restart Web Server

When ISO hosting fails:

```bash
gh workflow run configure-runner-vm.yaml \
  -f tags=webserver \
  --repo mahowlin/saif-sys-admin
```

Or manually:
```bash
ssh runner-vm
docker restart nginx
```

---

## Procedure: Manual Ansible Execution

For debugging or custom tag combinations:

```bash
cd saif-sys-admin/ansible/playbooks

# Generate fresh runner token
export RUNNER_TOKEN=$(gh api -X POST /orgs/mahowlin/actions/runners/registration-token --jq '.token')

# Run specific tags
ansible-playbook -i ../../environments/example/hosts.ini \
  configure-runner.yaml \
  --extra-vars "runner_token=${RUNNER_TOKEN}" \
  --tags runner,registry
```

Available tags:
- `system` - System packages, firewall
- `docker` - Docker CE installation
- `storage` - Data disk setup
- `registry` - Container registry
- `webserver` - nginx
- `runner` - GitHub Actions runners
- `gitea` - Gitea git server

---

## Critical Gotchas

### 1. DinD Externals Symlink

GitHub Actions require `/actions-runner/externals` directory. The playbook creates:
```
/actions-runner/externals → /data/externals
```

If this symlink is missing, runners fail with "externals not found".

### 2. Runner Workspace Path

The `RUNNER_WORKDIR` must match on host and container:
```
/data/runner-1  (host path)
/data/runner-1  (container path)
```

Mismatch causes "directory not found" errors.

### 3. Runner Token Validity

Runner registration tokens expire after 1 hour. The workflow generates fresh tokens automatically.

### 4. Port 80 Required

Intersight vMedia strips colons from URLs, so the web server MUST use port 80 (not 8080).

### 5. Auto-Update Disabled

Runners have `DISABLE_AUTO_UPDATE=true` to prevent restart loops during workflow execution.

---

## Troubleshooting

### Workflows Not Running

Check runner status:
```bash
ssh runner-vm
docker ps | grep runner
docker logs runner-1 2>&1 | tail -20
```

Check GitHub runner registration:
```bash
gh api /orgs/mahowlin/actions/runners --jq '.runners[] | {name, status}'
```

### Registry Connection Refused

```bash
ssh runner-vm
docker ps | grep registry
docker logs registry 2>&1 | tail -20

# Check disk space
df -h /data
```

### ISO Not Accessible

```bash
# Check nginx is running
ssh runner-vm
docker ps | grep nginx
docker logs nginx 2>&1 | tail -20

# Check ISO files exist
ls -la /data/www/iso/
```

### Terraform State Unavailable

```bash
ssh runner-vm
docker ps | grep terraform
docker logs terraform-backend 2>&1 | tail -20
```

---

## Disaster Recovery

If runner VM is completely down:

1. **Restore VM** from backup or rebuild
2. **Run full Ansible playbook**:
   ```bash
   gh workflow run configure-runner-vm.yaml -f tags=all
   ```
3. **Verify services**:
   ```bash
   ssh runner-vm
   docker ps
   curl -s http://localhost:5000/v2/_catalog
   ```
4. **Re-run sync-images** to ensure registry is populated:
   ```bash
   gh workflow run sync-images.yaml -f dry_run=false
   ```
