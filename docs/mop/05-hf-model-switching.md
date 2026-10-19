# Hugging Face Model Download and vLLM Switching

This runbook stages Hugging Face models on server one and publishes local registry artifacts for OpenShift vLLM deployments.

## Architecture

- Server one: `saif-github-runner` at `10.0.0.10`
- Local registry: `registry.example.com:5000`
- HTTP/WebDAV model cache: `http://10.0.0.10/hf-models/`
- OpenShift pulls the vLLM runtime image and model artifacts from the local registry only.

## Prerequisites

1. The vLLM actions container image exists in the local registry.
2. `HF_TOKEN` is configured as a GitHub Actions secret.
3. `HTTP_SERVER_USER` and `HTTP_SERVER_PASSWORD` are configured for WebDAV marker verification.
4. The Hugging Face account behind `HF_TOKEN` has accepted any gated model licenses.

Do not commit Hugging Face tokens to git, workflow YAML, docs, logs, or container images.

## Download and publish a model

Dispatch the workflow from `secure-ai-airgap`:

```bash
gh workflow run download-hf-model.yaml \
  -f hf_repo=google/gemma-4-E4B-it \
  -f model_name=gemma-4-e4b-it \
  -f tag=r1
```

The workflow:

1. Downloads weights to `/data/images/hf-models/<hf-repo-slug>` on server one.
2. Writes `.cache-ready` under the HTTP/WebDAV model path.
3. Builds a ModelCar-style image containing `/models/`.
4. Pushes it to `registry.example.com:5000/models/<model_name>:<tag>`.
5. Verifies both the HTTP marker and local registry artifact.

Re-run without setting `force_download=true` to test idempotency; completed models should skip. Use `force_download=true` only when replacing the local cache/artifact intentionally.

## Switch vLLM models in OpenShift

Update the OpenShift GitOps values for the vLLM workload:

- Runtime image: local vLLM image in `registry.example.com:5000`
- Model artifact: `registry.example.com:5000/models/<model_name>:<tag>` or its digest
- Served model name: the API-facing model name exposed by vLLM

After merge, ArgoCD reconciles the workload and OpenShift pulls only from the local registry.

## Verification

- The workflow summary shows the local registry model image and digest.
- `skopeo inspect --tls-verify=false docker://registry.example.com:5000/models/<model_name>:<tag>` succeeds on server one.
- The `.cache-ready` marker is reachable from `http://10.0.0.10/hf-models/<hf-repo-slug>/.cache-ready` with HTTP basic auth.
- The vLLM pod starts in OpenShift and `/v1/models` returns the served model name.
