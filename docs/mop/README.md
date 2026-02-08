# Infrastructure Operations (MOPs)

Step-by-step procedures for saif-sys-admin operations.

## Procedures

| Procedure | Description | When to Use |
|-----------|-------------|-------------|
| [01-image-mirroring.md](01-image-mirroring.md) | Mirror container images | Adding operators, updating images |
| [02-user-management.md](02-user-management.md) | Provision cluster users | New user access, role changes |
| [03-runner-vm.md](03-runner-vm.md) | Configure runner VM services | Service issues, upgrades |
| [04-troubleshooting.md](04-troubleshooting.md) | Common issues and solutions | When things break |

## Quick Reference

### Mirror Images (Most Common)

```bash
# Preview what will be mirrored
gh workflow run sync-images.yaml -f dry_run=true --repo mahowlin/saif-sys-admin

# Execute mirroring
gh workflow run sync-images.yaml -f dry_run=false --repo mahowlin/saif-sys-admin
```

### Create User

```bash
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=ai-pod-1 \
  -f users=username \
  -f emails=user@cisco.com \
  -f password='SecurePassword123!' \
  -f role=cluster-admin \
  -f action=create \
  --repo mahowlin/saif-sys-admin
```

### Fix Runner Issues

```bash
# Reconfigure runners (fixes restart loops)
gh workflow run configure-runner-vm.yaml -f tags=runner --repo mahowlin/saif-sys-admin
```

## Workflow Status

Check workflow status:
```bash
gh run list --repo mahowlin/saif-sys-admin
```

Watch running workflow:
```bash
gh run watch --repo mahowlin/saif-sys-admin
```
