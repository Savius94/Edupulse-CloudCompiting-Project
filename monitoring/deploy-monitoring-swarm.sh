#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f ../cloud-learning-saas/.env ]; then
  TMP_ENV=$(mktemp)
  tr -d '\r' < ../cloud-learning-saas/.env > "$TMP_ENV"
  set -a
  # shellcheck disable=SC1090
  source "$TMP_ENV"
  set +a
  rm -f "$TMP_ENV"
fi

if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" != "active" ]; then
  echo "ERREUR : Docker Swarm n'est pas actif sur cette machine (lancez ce script sur le Manager)."
  exit 1
fi

echo "=== Déploiement de la stack Monitoring Swarm (Promtail global + Loki + Grafana) ==="
docker stack deploy --resolve-image=always -c docker-compose.swarm.yml monitoring

echo "=== État des services Monitoring ==="
docker stack services monitoring
echo ""
echo "Grafana accessible sur : http://grafana.${BASE_DOMAIN:-edupulse.mnbll.fr} (ou http://<IP_MANAGER>:3002)"
echo "Identifiants par défaut : admin / admin"
