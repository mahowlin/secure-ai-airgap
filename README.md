# SAIF System Administration

Infrastructure services for the SAIF Platform -- image mirroring, user management, and runner VM operations.

## Overview

This repository manages the foundational infrastructure that all clusters depend on:

- **Image Mirroring**: Syncs container images from public registries to an internal registry for air-gapped operation
- **User Management**: Automated OpenShift user provisioning with RBAC and SSH access
- **Runner VM**: Self-hosted GitHub Actions runners, container registry, web server, and git server

## Prerequisites

- GitHub Actions self-hosted runner with Docker-in-Docker
- Internal container registry (registry:2)
- Red Hat pull secret for registry authentication
- SSH access to target OpenShift clusters via jump host

## Usage

### Image Mirroring

```bash
# Dry run (preview what will be mirrored)
gh workflow run sync-images.yaml -f dry_run=true

# Mirror images to internal registry
gh workflow run sync-images.yaml -f dry_run=false

# Mirror with IDMS auto-apply and catalog refresh
gh workflow run sync-images.yaml \
  -f dry_run=false \
  -f apply_idms=true \
  -f cluster_name=ai-pod-1 \
  -f refresh_catalogs=true
```

### User Management

```bash
# Create a cluster user with SSH access
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=ai-pod-1 \
  -f users=username \
  -f emails=user@example.com \
  -f password='<PASSWORD>' \
  -f role=cluster-admin \
  -f ssh_pubkeys='ssh-ed25519 AAAA...' \
  -f action=create
```

### Runner VM Configuration

```bash
# Configure all services
gh workflow run configure-runner-vm.yaml -f tags=all

# Configure specific services
gh workflow run configure-runner-vm.yaml -f tags="registry,webserver"
```

## Required Secrets

| Secret | Purpose |
|--------|---------|
| `REDHAT_PULL_SECRET` | Red Hat registry authentication |
| `KUBECONFIG_REPO_TOKEN` | Access to cluster kubeconfigs |
| `WEBEX_BOT_TOKEN` | WebEx notifications (user management) |
| `JUMP_HOST_SSH_KEY` | SSH key for jump host access |

## Directory Structure

```
saif-sys-admin/
├── .github/workflows/          # GitHub Actions workflows
│   ├── sync-images.yaml        # Mirror container images
│   ├── manage-cluster-users.yaml  # User provisioning
│   ├── configure-runner-vm.yaml   # Runner VM setup
│   └── build-*.yaml            # Container image builds
├── mirror/                     # Image mirroring configuration
│   ├── platform-images.yaml    # Platform image list
│   ├── other-images.yaml       # Additional images
│   └── idms/                   # ImageDigestMirrorSet manifests
├── ansible/                    # Ansible playbooks
│   └── playbooks/
│       └── configure-runner.yaml  # Runner VM configuration
├── environments/               # Environment configs
│   └── example/                # Example environment
└── docs/                       # Documentation
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and integration points
- [MOPs](docs/mop/) - Operational procedures
  - [Image Mirroring](docs/mop/01-image-mirroring.md)
  - [User Management](docs/mop/02-user-management.md)
  - [Runner VM](docs/mop/03-runner-vm.md)
  - [Troubleshooting](docs/mop/04-troubleshooting.md)

## Related Repositories

| Repository | Relationship |
|------------|-------------|
| [saif-platform](https://github.com/mahowlin/saif-platform) | Platform orchestration |
| [saif-ai-pod](https://github.com/mahowlin/saif-ai-pod) | Consumes mirrored images during cluster deployment |
| [saif-gitops](https://github.com/mahowlin/saif-gitops) | References mirrored images, syncs IDMS |

## License

This project is licensed under the Cisco Sample Code License, Version 1.1. See [LICENSE](LICENSE) for details.
