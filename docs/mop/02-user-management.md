# User Management

## Overview

Provision OpenShift cluster users with HTPasswd authentication and optional jump host SSH access.

## Prerequisites

- GitHub CLI authenticated (`gh auth login`)
- Access to mahowlin/secure-ai-airgap repository
- Target cluster operational

## Procedure: Create User

### Step 1: Gather User Information

Required:
- Username (no spaces, alphanumeric)
- Email address (for WebEx notification)
- Password (meets complexity requirements)
- Role (cluster-admin, admin, edit, view)

Optional:
- SSH public key (for jump host access)

### Step 2: Run Create Workflow

```bash
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=example-node-1 \
  -f users=johndoe \
  -f emails=johndoe@cisco.com \
  -f password='SecurePassword123!' \
  -f role=cluster-admin \
  -f ssh_pubkeys='ssh-ed25519 AAAA... johndoe@laptop' \
  -f action=create \
  --repo mahowlin/secure-ai-airgap
```

### Step 3: Monitor Workflow

```bash
gh run watch --repo mahowlin/secure-ai-airgap
```

### Step 4: Verify User Access

User will receive WebEx notification with:
- Cluster console URL
- Login credentials
- Jump host instructions (if SSH key provided)

---

## Procedure: Create Multiple Users

Provide comma-separated values:

```bash
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=example-node-1 \
  -f users='user1,user2,user3' \
  -f emails='user1@cisco.com,user2@cisco.com,user3@cisco.com' \
  -f password='SharedPassword123!' \
  -f role=edit \
  -f action=create \
  --repo mahowlin/secure-ai-airgap
```

---

## Procedure: List Users

```bash
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=example-node-1 \
  -f users=ignored \
  -f role=cluster-admin \
  -f action=list \
  --repo mahowlin/secure-ai-airgap
```

Check workflow output for user list.

---

## Procedure: Delete User

```bash
gh workflow run manage-cluster-users.yaml \
  -f cluster_name=example-node-1 \
  -f users=johndoe \
  -f role=cluster-admin \
  -f action=delete \
  --repo mahowlin/secure-ai-airgap
```

This removes:
- HTPasswd entry
- ClusterRoleBinding
- Jump host user (if exists)

---

## Available Roles

| Role | Permissions |
|------|-------------|
| `cluster-admin` | Full cluster access |
| `admin` | Namespace admin (all namespaces) |
| `edit` | Create/modify resources (not RBAC) |
| `view` | Read-only access |

---

## Troubleshooting

### Login Failed

```bash
# Check HTPasswd secret exists
oc get secret htpasswd-secret -n openshift-config

# Check OAuth configuration
oc get oauth cluster -o yaml
```

### WebEx Notification Not Received

- Verify email address is correct
- Check WEBEX_BOT_TOKEN is valid
- User may need to accept WebEx bot invitation

### SSH Access Denied

```bash
# Check user exists on jump host
ssh jump-host "grep johndoe /etc/passwd"

# Check SSH key is correct
ssh jump-host "cat /home/johndoe/.ssh/authorized_keys"
```

### User Cannot Access Console

1. Verify ClusterRoleBinding exists:
   ```bash
   oc get clusterrolebinding | grep johndoe
   ```

2. Check OAuth pods are running:
   ```bash
   oc get pods -n openshift-authentication
   ```
