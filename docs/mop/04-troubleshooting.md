# Diagnose site-service and image delivery failures

| Failure | Check | Owning action |
|---|---|---|
| Workflow queued | Selected runner labels/image, runner registration and repository access | Runner/runtime configuration |
| Input/registry conflict | Environment profile and staged project/selected-cache contract | Canonical input, then pipeline preparation |
| Source access denied | Actual entitlement, selected image/catalog ref and source auth | Private source credentials; do not substitute an image |
| Registry connection/TLS failure | Declared endpoint/port, normal trust chain and service health | Registry owner, DNS/network and selected CA |
| ImagePullBackOff | Workload's exact image, consumer pull identity/CA and actual destination content | Image production or consumer runtime references |
| Cache transition blocked | Selected inventory/provenance, current reader/config identity and phase coverage | Refresh reviewed source/reader evidence, not a bypass |
| Policy matches but pull fails | Effective IDMS/ITMS, digest at destination and actual node path | Observe consumer state; policy render alone is insufficient |
| Host configuration drift | Pipeline input, inventory/profile and generated project contract | Owning Ansible rollout, not an SSH patch |

Exact offline generation and runtime checks are linked from [AGENTS](../../AGENTS.md). Keep credentials and service logs in private operational handling. Do not disable TLS or delete registry content to recover an unrelated validation failure.
