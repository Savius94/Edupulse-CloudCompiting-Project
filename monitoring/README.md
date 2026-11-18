# Monitoring — Loki + Grafana

Collecte **tous les logs Docker** du poste (portail EduPulse, tenant, Evalio, Notara, Traefik, …) via :

**Promtail → Loki → Grafana**

## Démarrer en mode Docker Swarm (Manager + Workers — Recommandé en prod)

Déploie **Promtail en mode `global`** sur le Manager **et** tous les Workers (aucun fichier à copier sur les Workers), centralise les logs dans **Loki** sur le Manager et expose **Grafana** via Traefik (`*.edupulse.mnbll.fr`).

```bash
cd monitoring
./deploy-monitoring-swarm.sh
```

| Service | URL |
|---------|-----|
| Grafana (via Caddy + Traefik) | `http://grafana.edupulse.mnbll.fr` |
| Grafana (accès direct Manager) | `http://<IP_MANAGER>:3002` |

*(Si vous aviez lancé la stack en `docker compose` simple avant, faites d'abord `docker compose down` pour libérer le port `3002`.)*

---

## Démarrer en local (Docker Compose mono-machine)

```powershell
cd monitoring
docker compose up -d
```

| Service | URL |
|---------|-----|
| Grafana | http://localhost:3002 |
| Loki API | http://localhost:3100 |

Login Grafana : `admin` / `admin` (consultation anonyme Viewer activée).

## Requêtes utiles (Explore → Loki)

```logql
{job="docker"}
{swarm_service=~".*portal.*"}
{swarm_service=~".*tenant-app.*"}
{swarm_stack="demo"}
{container=~".*portal.*"}
{compose_project="evalio"}
{compose_project="notara"}
{compose_project="demo"}
{compose_project=~"edupulse.*"}
{job="docker"} |= "error"
```

Dashboard provisionné : **EduPulse — Logs globaux**.
