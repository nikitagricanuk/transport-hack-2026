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
# Rewrite only docker-compose.yml. app.env (ORGANIZER_TOKEN, TEAM_NAME) lives
# in this directory and must survive every deploy — do not delete or truncate it.
printf '%s' "$COMPOSE_B64" | base64 -d > docker-compose.yml

# Credentials are written only under this directory, never ~/.docker.
# The trap runs on every exit, including a failed pull, up, or prune.
docker_cfg=""
cleanup_docker_cfg() {
  status=$?
  trap - EXIT
  if [ -n "$docker_cfg" ] && [ -d "$docker_cfg" ]; then
    DOCKER_CONFIG="$docker_cfg" docker logout ghcr.io >/dev/null 2>&1 || true
    rm -rf "$docker_cfg"
  fi
  exit "$status"
}

docker_cfg=$(mktemp -d)
trap cleanup_docker_cfg EXIT
chmod 700 "$docker_cfg"

token=$(printf '%s' "$TOKEN_B64" | base64 -d)
if [ -n "$token" ]; then
  printf '%s' "$token" | DOCKER_CONFIG="$docker_cfg" docker login ghcr.io -u "$GHCR_USER" --password-stdin
else
  echo "No GHCR token; pulling as a public package"
fi
unset token TOKEN_B64

DOCKER_CONFIG="$docker_cfg" docker compose pull
# The image is already in the daemon. --pull never keeps `up` from contacting
# the registry (Compose otherwise refreshes the :latest tag), so this does
# not need the token. The trap still drops the temp config afterwards.
docker compose up -d --remove-orphans --pull never
docker image prune -f

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
