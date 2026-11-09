#!/bin/bash
# SSH deploy used by .github/workflows/ci.yml.
# Required: DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS, DEPLOY_HOST, DEPLOY_USER.
# Optional: GHCR_TOKEN (otherwise GITHUB_TOKEN), DEPLOY_PORT (default 22).
set -euo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_USER:?DEPLOY_USER is required}"
: "${DEPLOY_SSH_KEY:?secret DEPLOY_SSH_KEY is required}"
: "${DEPLOY_KNOWN_HOSTS:?secret DEPLOY_KNOWN_HOSTS is required}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
GHCR_USER="${GHCR_USER:-${GITHUB_REPOSITORY_OWNER:-nikitagricanuk}}"

token="${GHCR_TOKEN:-${GITHUB_TOKEN:-}}"
root=$(unset CDPATH; cd -- "$(dirname -- "$0")/.." && pwd)
compose_b64=$(base64 -w0 "$root/deploy/docker-compose.prod.yml")
token_b64=$(printf '%s' "$token" | base64 -w0)

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
chmod 700 "$workdir"

# Keep the secret bytes exactly, including a trailing newline OpenSSH expects.
printf '%s\n' "$DEPLOY_SSH_KEY" | tr -d '\r' > "$workdir/id"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" | tr -d '\r' > "$workdir/known_hosts"
chmod 600 "$workdir/id" "$workdir/known_hosts"

if ! grep -q '[^[:space:]]' "$workdir/known_hosts"; then
  echo "DEPLOY_KNOWN_HOSTS is empty" >&2
  exit 1
fi

echo "Deploying to ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PORT}"
ssh -i "$workdir/id" \
  -p "$DEPLOY_PORT" \
  -T \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$workdir/known_hosts" \
  -o IdentitiesOnly=yes \
  -o ConnectTimeout=20 \
  "${DEPLOY_USER}@${DEPLOY_HOST}" \
  "COMPOSE_B64='${compose_b64}' TOKEN_B64='${token_b64}' GHCR_USER='${GHCR_USER}' bash -s" \
  < "$root/deploy/remote-deploy.sh"
