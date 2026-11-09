#!/bin/bash
# Runs on the server over SSH. Env is supplied by deploy/ci-deploy.sh:
#   COMPOSE_B64  base64 of deploy/docker-compose.prod.yml
#   TOKEN_B64    base64 of the GHCR token (empty when the package is public)
#   GHCR_USER    registry username
set -eu

: "${COMPOSE_B64:?COMPOSE_B64 is required}"
TOKEN_B64="${TOKEN_B64-}"
: "${GHCR_USER:?GHCR_USER is required}"

command -v docker >/dev/null 2>&1 || {
  echo "docker is not installed or not on PATH for this user" >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "curl is required on the server for the health check" >&2
  exit 1
}
docker compose version >/dev/null

DEPLOY_DIR=/opt/transport-hack
if [ -d "$DEPLOY_DIR" ] && [ -w "$DEPLOY_DIR" ]; then
  :
elif mkdir -p "$DEPLOY_DIR" 2>/dev/null && [ -w "$DEPLOY_DIR" ]; then
  :
else
  DEPLOY_DIR="${HOME}/transport-hack"
  mkdir -p "$DEPLOY_DIR"
fi
cd "$DEPLOY_DIR"
echo "deploy dir: ${DEPLOY_DIR}"

umask 022
printf '%s' "$COMPOSE_B64" | base64 -d > docker-compose.yml

token=$(printf '%s' "$TOKEN_B64" | base64 -d)
if [ -n "$token" ]; then
  printf '%s' "$token" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
else
  echo "No GHCR token; pulling as a public package"
fi
unset token TOKEN_B64

docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
# The token is only needed for the pull. Do not leave it in ~/.docker/config.json.
docker logout ghcr.io >/dev/null 2>&1 || true

ok=0
i=0
while [ "$i" -lt 15 ]; do
  if body=$(curl -fsS --max-time 5 http://127.0.0.1:8095/api/health); then
    printf '%s\n' "$body"
    case "$body" in
      *'"ok": true'* | *'"ok":true'*)
        ok=1
        break
        ;;
    esac
  fi
  i=$((i + 1))
  sleep 3
done

if [ "$ok" -ne 1 ]; then
  echo "health check failed: http://127.0.0.1:8095/api/health" >&2
  docker compose ps >&2 || true
  docker compose logs --tail 100 >&2 || true
  exit 1
fi
