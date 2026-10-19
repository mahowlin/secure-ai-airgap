# Runner and registry host

`ansible/playbooks/configure-runner.yaml` consumes `environments/<site>/group_vars.yml` and `hosts.ini`. The example preserves the source's service input shape: runner capacity, container images, registry/web/state/S3 ports and volumes, retention and health settings. `rustfs_access_key` and `rustfs_secret_key` remain environment-secret references; the repository contains no actual values.

The owning live rollout is `.github/workflows/configure-runner-vm.yaml`. Supply an approved inventory, explicit `environment` profile, runner label/container image, SSH trust and runtime secrets. For composed execution, stage the generated metadata contract and pass its path as `project_contract`; the existing pipeline passes `project_contract_file` to Ansible. See [PROJECT_INPUTS](../PROJECT_INPUTS.md) for precedence and conflict checks. Do not run the documentation inventory against a real host. Local syntax checks are permitted; persistent rollout is pipeline-only.

If the site already has a build workstation and registry, this VM/host is optional for the third-party OpenShift install. The helper does not install OpenShift or create a cluster. It supplies external dependencies that must already be reachable when `secure-ai-compute` builds and boots the Agent ISO. DNS/registry trust and NTP remain site facts passed to `secure-ai-datacenter`.

Use the original `packer/` and `tf/vsphere-vms/` implementation only when the site chooses VM provisioning; it is not a hardware prerequisite. No host/VM changes were made by preparing this tree.
