#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
  TMP_ENV=$(mktemp)
  tr -d '\r' < .env > "$TMP_ENV"
  set -a
  # shellcheck disable=SC1090
  source "$TMP_ENV"
  set +a
  rm -f "$TMP_ENV"
fi

if [ -z "${GEMINI_MODEL:-}" ]; then
  GEMINI_MODEL="gemini-3.5-flash-lite"
  export GEMINI_MODEL
fi

REGISTRY_HOST="${REGISTRY_HOST:-100.85.14.115:5000}"

echo "=== 1. Vérification de Docker Swarm ==="
if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" != "active" ]; then
  echo "ERREUR : Docker Swarm n'est pas actif sur cette machine."
  exit 1
fi

echo "=== 2. Déploiement du socle Swarm (Registry, Socket-Proxy, Portal-DB, Traefik, Portal-API) ==="
docker stack deploy --resolve-image=never -c docker-compose.yml edupulse --with-registry-auth

echo "=== 3. Attente du Registry privé ($REGISTRY_HOST) ==="
for i in $(seq 1 30); do
  if curl -fsS "http://${REGISTRY_HOST}/v2/" >/dev/null 2>&1 || curl -fsS "http://127.0.0.1:5000/v2/" >/dev/null 2>&1; then
    echo "Registry prêt !"
    break
  fi
  sleep 2
done

echo "=== 4. Construction & Publication des images sur le Registry ($REGISTRY_HOST) ==="
docker build -t "${REGISTRY_HOST}/edupulse-portal-api:latest" -f portal/Dockerfile .
docker push "${REGISTRY_HOST}/edupulse-portal-api:latest"

docker build -t "${REGISTRY_HOST}/edupulse-tenant-app:latest" ./tenant-stack
docker push "${REGISTRY_HOST}/edupulse-tenant-app:latest"

echo "=== 5. Déploiement / Mise à jour sans coupure (Rolling Update) sur les Workers ==="
docker service update --force --with-registry-auth \
  --image "${REGISTRY_HOST}/edupulse-portal-api:latest" \
  --env-add "GEMINI_API_KEY=${GEMINI_API_KEY:-}" \
  --env-add "GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash-lite}" \
  edupulse_portal-api || true

if [ -n "${GEMINI_API_KEY:-}" ]; then
  echo "=== 5b. Propagation de la clé API IA aux établissements déjà déployés sur les Workers ==="
  for svc in $(docker service ls --format '{{.Name}}' | grep '_tenant-app$' || true); do
    echo " -> Mise à jour des variables IA pour $svc..."
    docker service update -d --with-registry-auth \
      --image "${REGISTRY_HOST}/edupulse-tenant-app:latest" \
      --env-add "GEMINI_API_KEY=${GEMINI_API_KEY}" \
      --env-add "GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash-lite}" \
      "$svc" >/dev/null || true
  done
fi

echo "=== 6. État du Cluster Swarm ==="
docker node ls
docker service ls
