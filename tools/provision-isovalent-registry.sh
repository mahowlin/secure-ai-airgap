#!/usr/bin/env bash
set -euo pipefail

umask 077

GITHUB_REPO="mahowlin/secure-ai-airgap"
ISOVALENT_USERNAME=""
GITHUB_ONLY=false
DRY_RUN=false
TEST_AUTH=false
AUTH_OUTPUT=""
VAULT_HEADER_FILE=""
AUTH_EVIDENCE_FILE=""
REGISTRY_TOKEN=""
vault_root_url=""
TOOL_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPOSITORY_ROOT=$(cd -- "$TOOL_DIRECTORY/.." && pwd)

usage() {
  cat <<'EOF'
Usage:
  provision-isovalent-registry.sh --username EMAIL [options]

Options:
  --username EMAIL   Isovalent Registry username/email.
  --repo OWNER/REPO  GitHub repository (default: mahowlin/secure-ai-airgap).
  --github-only      Skip the Vault write and provision GitHub only.
  --test-auth        Test IEP 26.05 access without writing Vault or GitHub.
  --output FILE      Retain credential-free auth evidence (with --test-auth).
  --dry-run          Validate inputs and show target names without mutations.
  -h, --help         Show this help.

The script reads the Isovalent reference/access token from a hidden prompt when
run interactively, or from standard input when run non-interactively. It never
accepts the token as a command-line argument.

For Vault plus GitHub provisioning, set these environment variables first:
  VAULT_URL
  VAULT_NAMESPACE
  VAULT_TOKEN
  VAULT_ARTIFACTORY_SECRET_PATH
  VAULT_ARTIFACTORY_SECRET_KEY
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_environment() {
  local name=$1
  [[ -n ${!name:-} ]] || fail "required environment variable is not set: $name"
}

cleanup() {
  REGISTRY_TOKEN=""
  if [[ -n $AUTH_EVIDENCE_FILE && -f $AUTH_EVIDENCE_FILE ]]; then
    rm -f -- "$AUTH_EVIDENCE_FILE"
  fi
  if [[ -n $VAULT_HEADER_FILE && -f $VAULT_HEADER_FILE ]]; then
    rm -f -- "$VAULT_HEADER_FILE"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

while (($#)); do
  case "$1" in
    --username)
      (($# >= 2)) || fail "--username requires a value"
      ISOVALENT_USERNAME=$2
      shift 2
      ;;
    --repo)
      (($# >= 2)) || fail "--repo requires a value"
      GITHUB_REPO=$2
      shift 2
      ;;
    --github-only)
      GITHUB_ONLY=true
      shift
      ;;
    --test-auth)
      TEST_AUTH=true
      shift
      ;;
    --output)
      (($# >= 2)) || fail "--output requires a value"
      AUTH_OUTPUT=$2
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n $ISOVALENT_USERNAME ]] || fail "--username is required"
[[ $GITHUB_REPO == */* ]] || fail "--repo must be OWNER/REPO"
if [[ $TEST_AUTH == true && $DRY_RUN == true ]]; then
  fail "--test-auth and --dry-run are mutually exclusive"
fi
if [[ $TEST_AUTH == true && $GITHUB_ONLY == true ]]; then
  fail "--test-auth and --github-only are mutually exclusive"
fi
if [[ $TEST_AUTH == false && -n $AUTH_OUTPUT ]]; then
  fail "--output requires --test-auth"
fi

if [[ -t 0 ]]; then
  IFS= read -r -s -p "Isovalent reference/access token: " REGISTRY_TOKEN
  printf '\n'
else
  IFS= read -r REGISTRY_TOKEN
fi
[[ -n $REGISTRY_TOKEN ]] || fail "the Isovalent registry token is empty"

if [[ $TEST_AUTH == true ]]; then
  require_command python3
  require_command curl
  inspector="$REPOSITORY_ROOT/scripts/inspect-isovalent-release.py"
  contract="$REPOSITORY_ROOT/mirror/releases/iep-26.05.yaml"
  [[ -r $inspector ]] || fail "release inspector not found: $inspector"
  [[ -r $contract ]] || fail "release contract not found: $contract"

  if [[ -n $AUTH_OUTPUT ]]; then
    evidence_output=$AUTH_OUTPUT
  else
    AUTH_EVIDENCE_FILE=$(mktemp "${TMPDIR:-/tmp}/isovalent-auth-evidence.XXXXXX")
    evidence_output=$AUTH_EVIDENCE_FILE
  fi
  ISOVALENT_REGISTRY_USERNAME=$ISOVALENT_USERNAME \
    ISOVALENT_REGISTRY_TOKEN=$REGISTRY_TOKEN \
    python3 "$inspector" \
      --contract "$contract" \
      --output "$evidence_output" \
      --discovery-only
  printf '%s\n' \
    'Authenticated access verified for the IEP 26.05 chart, Helm index, CLIFE bundle, controller, and operand images.'
  exit 0
fi

require_command gh
if [[ $GITHUB_ONLY == false ]]; then
  require_command curl
  require_command jq
  require_environment VAULT_URL
  require_environment VAULT_NAMESPACE
  require_environment VAULT_TOKEN
  require_environment VAULT_ARTIFACTORY_SECRET_PATH
  require_environment VAULT_ARTIFACTORY_SECRET_KEY
  vault_root_url=$(printenv VAULT_URL)
  [[ $vault_root_url == https://* ]] || fail "VAULT_URL must use https://"
  [[ $VAULT_ARTIFACTORY_SECRET_PATH != *..* ]] || fail "Vault secret path must not contain '..'"
fi

if [[ $DRY_RUN == true ]]; then
  printf 'Dry run: would provision GitHub variable ISOVALENT_REGISTRY_USERNAME in %s.\n' "$GITHUB_REPO"
  printf 'Dry run: would provision GitHub secret ISOVALENT_REGISTRY_TOKEN in %s.\n' "$GITHUB_REPO"
  if [[ $GITHUB_ONLY == false ]]; then
    printf 'Dry run: would update the configured Vault secret path and key first.\n'
  fi
  exit 0
fi

gh auth status --hostname github.com >/dev/null

if [[ $GITHUB_ONLY == false ]]; then
  VAULT_HEADER_FILE=$(mktemp "${TMPDIR:-/tmp}/isovalent-vault-headers.XXXXXX")
  chmod 600 "$VAULT_HEADER_FILE"
  {
    printf 'X-Vault-Namespace: %s\n' "$VAULT_NAMESPACE"
    printf 'X-Vault-Token: %s\n' "$VAULT_TOKEN"
  } >"$VAULT_HEADER_FILE"

  vault_path=${VAULT_ARTIFACTORY_SECRET_PATH#/}
  vault_url=${vault_root_url%/}/v1/secret/data/${vault_path}
  printf '%s' "$REGISTRY_TOKEN" |
    jq -Rs --arg key "$VAULT_ARTIFACTORY_SECRET_KEY" '{data: {($key): .}}' |
    curl -fsS -X PATCH "$vault_url" \
      -H "@$VAULT_HEADER_FILE" \
      -H 'Content-Type: application/merge-patch+json' \
      --data-binary @- >/dev/null
  printf 'Updated the configured Vault secret path and key.\n'
fi

gh variable set ISOVALENT_REGISTRY_USERNAME \
  --repo "$GITHUB_REPO" \
  --body "$ISOVALENT_USERNAME"

printf '%s' "$REGISTRY_TOKEN" |
  gh secret set ISOVALENT_REGISTRY_TOKEN --repo "$GITHUB_REPO"

gh variable list --repo "$GITHUB_REPO" --json name --jq '.[].name' |
  grep -Fxq ISOVALENT_REGISTRY_USERNAME || fail "GitHub variable verification failed"
gh secret list --repo "$GITHUB_REPO" --json name --jq '.[].name' |
  grep -Fxq ISOVALENT_REGISTRY_TOKEN || fail "GitHub secret verification failed"

printf 'Provisioned ISOVALENT_REGISTRY_USERNAME and ISOVALENT_REGISTRY_TOKEN in %s.\n' "$GITHUB_REPO"
