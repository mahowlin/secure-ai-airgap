# Maintenance execution procedures

Read `AGENTS.md` and the approved change record. Source acceptance is separate from an
actual runner, account, VM or registry operation. This document does not grant
live authority. Use declared inventory and runtime references; never paste
credential values into commands, the change record or diagnostics.

## Runner host

`configure-runner-vm.yaml` keeps service selection through Ansible tags.
`HTTP_SERVER_PASSWORD` is required for `webserver`; `GITEA_ADMIN_PASSWORD` for
`gitea`; `RUNNER_TOKEN` and the actual `RUNNER_NAME` for `runner`. `all` and
`tagged` include those requirements. The workflow obtains the registration token
when needed and passes it through the environment. Gitea password arguments use
Ansible argv and `no_log`; URI calls also use `no_log`.

All workflow SSH access requires `JUMP_HOST_SSH_KEY` and a separately verified
`JUMP_HOST_KNOWN_HOSTS`. Keys and known-host records live in a private temporary
directory, use normal strict host verification and are removed in `always()`
cleanup. Missing references fail before Ansible host changes. Manual Ansible
execution uses the same environment references or a privately provided variable
source; normal host verification remains required.

Runner maintenance must use an independent executor outside the target managed
set. Configure `RUNNER_MAINTENANCE_LABELS` with its GitHub runner-label JSON array.
The existing pool remains the fallback for service-only work, but replacement
refuses its executing runner by both desired names and actual container env.
Drain existing jobs before an authorized pool maintenance operation.

Runner names/socket/ownership are declared in environment group vars. Before
host changes, the playbook inspects only the selected containers at the declared
Docker socket and refuses missing identity, the executor, another organization,
or missing ownership labels. It never interprets unavailable Docker as an empty
host. For a fresh host, bootstrap non-runner services first, then run `runner`
from the independent executor. Manual `--skip-tags runner` excludes replacement
from an initial all-services bootstrap. Do not use a fake `RUNNER_NAME`.

Legacy unlabelled containers are retained and cause a clear refusal. Establish
real ownership through the normal source/owner review before any separate
adoption; this patch does not silently label or replace them. New managed
containers receive the declared owner and organization labels. A changed runner
count does not authorize deleting surplus or unrelated containers.

## Account revocation

`manage-cluster-users.yaml` validates identities before effects. Kubernetes
revocation removes only the exact `kind: User` subject from bindings labelled
`managed-by: manage-cluster-users-workflow`. JSON Patch tests both resourceVersion
and the observed subjects before replacement. Group/ServiceAccount names,
other users, unrelated bindings and shared User/Identity objects survive.
An empty owned binding is retained. Concurrent changes stop the patch; inspect
partial results before retrying. Exact htpasswd comparisons treat dots literally;
the temporary files are private and removed on failure/success. Before revocation,
one successful `--ignore-not-found` JSON observation is validated and decoded;
Forbidden, transport/API failure or malformed metadata/data stops before mutations.
Actual successful absence may continue owned-only revocation. An existing Secret
is updated successfully before the workflow reports overall success; failed
updates require inspection of any already-revoked grants before retrying.

Jump-host accounts require the workflow GECOS ownership marker. Creation refuses
to take over an existing unmarked account; deletion refuses it too. Explicit
site SSH public keys are delivered as stdin data, never remote shell source.
Accounts created by older code have no ownership proof and remain untouched
until reviewed by their owner. Optional jump-host changes still skip when no
jump-host key is configured; a configured key requires normal known-host trust.
Global identity deletion is deliberately excluded because another IDP may use it.

## Endpoint template build

The inventory contains no reusable password, hash or authorized principal.
Provide `ENDPOINT_BUILD_PASSWORD` and `ENDPOINT_SSH_AUTHORIZED_KEYS` as approved
private runtime references. The latter is a nonempty JSON array of explicit site
public keys. The helper checks their syntax with local OpenSSH and derives the
temporary build hash through stdin, then writes one exclusive mode0600 Packer
variable file in a mode0700 job directory. Both validation and build consume it;
`always()` cleanup removes it. Do not print Packer debug logs containing build
data. No existing credentials are rotated by these source changes.

Password SSH and passwordless sudo serve the temporary build session. Explicit
site keys remain the intended administrative principals. The last file
provisioner uploads the finalizer **after** temporary-file cleanup. Packer invokes
it in its final authenticated shutdown session: the password hash is replaced
with a locked marker, installer/cloud-init copies are removed, and final SSH
configuration disables password and keyboard-interactive authentication before
poweroff/conversion. Earlier cleanup would break Packer reconnects. The existing
site key/sudo access remains; keys are never inherited from shipped examples.
A failed finalizer/build must not be treated as a successful reusable image.

Offline checks cover missing/malformed references and unprivileged/invalid-user
failure. Actual build, boot and final authentication qualification still require
an authorized disposable build and are not implied by source checks.

## Failed optional VM deployment

Post-provision validation failure retains the VM set and its Terraform state.
Inspect existing ownership and failure evidence before any recovery operation.
The existing explicit `action=destroy` remains separate; a failed test never
invokes it automatically. Packer's own temporary build-VM cleanup is independent
of the vSphere VM-set state and is unchanged.
