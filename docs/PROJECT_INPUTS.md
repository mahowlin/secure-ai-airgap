# Independent and composed execution

## Standalone

Copy `environments/example` to your own environment profile and replace the inventory, service DNS/ports, runner owner/repository/scope and explicit tool pins. Supply private SSH known-hosts, registration/admin token and service auth through pipeline secrets. The example is synthetic; it does not select a real host. Org registration needs an org-authorized token; repository registration needs access to the selected repository. An ordinary workflow token is not assumed to have those permissions.

`Configure Runner VM` requires `environment`; `Sync Images` requires the same explicit profile. A pre-supplied short-lived registration token is a private `RUNNER_REGISTRATION_TOKEN` secret, not dispatch text. The configure pipeline runs the existing playbook with `-e env=<site>`. Actual host configuration must go through that pipeline. Local use is limited to syntax or isolated variable/assertion validation.

## Composed

Stage the exact generated `metadata.yaml` from secure-ai-datacenter as a private pipeline input, after artifact-manifest verification. Pass its path as `project_contract` to the existing workflows. The runner playbook receives `project_contract_file`; it uses the selected registry, NTP, OpenShift client release and optional object store over standalone profile defaults. It rejects internal mismatches and explicit overriding values. Shared DNS remains input to cluster/node configuration rather than an implicit runner resolver rewrite.

The image pipeline reads the same contract's registry and delivery mode. Its validated registry output feeds mirror, promotion and OCI packaging jobs. A legacy repository variable may agree, but cannot select a different destination. Explicit image inventories/digest references and mirror mappings must also be edited in canonical inputs when changing registries; there is no silent image-name rewrite. A connected installation does not request mirrored scopes.

Private credentials, SSH identity, runner image/labels, registry CA trust available to the runner/container, destination state and publication permissions remain operational enrichment. Hostname/endpoint propagation is not proof of a TLS certificate or reachable service. In particular the inherited RustFS host adapter serves its configured plaintext listener; an HTTPS S3 endpoint requires an existing reviewed TLS front end, not merely changing the endpoint string. Select only the service tags you intend this profile to own. No real service, image or host rollout was performed in local contract checks.
