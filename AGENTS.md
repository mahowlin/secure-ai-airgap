# Secure AI Airgap: agent entry point

Prepare site services, verified upstream inputs and selected registry content for platform consumers. See [README](README.md) for the human composition.

## Route the task

| Goal | Authoritative editable input | Procedure | Actual output | Relevant check | Next owner |
|---|---|---|---|---|---|
| Produce mirror policy / selected images | `mirror/` selections, recipes, release contracts; `environments/<site>/` | [operations](agents/operations.md) | IDMS/ITMS, verification receipts and immutable bundles | selected recipe/contract tests; repeat local policy render | Datacenter / cluster consumer |
| Acquire/import operator inspection artifacts | Platform dependency contract; existing operator package-input contract | [operator inputs](agents/registry-trust.md#operator-inspection-inputs) | five verified files, license and provenance receipt | `test_operator_artifact_preparation.py` | Datacenter offline resolver |
| Inspect trust or promote optional packages | selected registry/CA contract; exact merged package inputs | [registry and packages](agents/registry-trust.md) | private auth context / byte-checked package | normal TLS, expected authority and selected hashes | selected registry / GitOps |
| Maintain runner, accounts or endpoints | `environments/<site>/`, Ansible and workflow inputs | [maintenance](agents/maintenance.md) | reviewed site service configuration | local syntax checks; pipeline rollout evidence | site operations owner |

## Toolchain and execution boundary

Use Python/requirements in the existing environment. Mirroring clients, Skopeo and Ansible collections come from the selected toolchain; see `ansible/requirements.yml`. Offline policy rendering and supplied-artifact import do not contact hosts/registries. Explicit upstream fetch acquires only pinned files; mirroring/package publication writes registry content. All live Ansible rollout uses the pipeline. Runtime auth, entitlement, kubeconfigs and trust remain private references. All procedures live under `agents/`.

Keep exact executable sequences in the linked procedure; update its input/output/check handoff when an interface changes. Record unknown runtime facts as unknown rather than inferring them from local success.
