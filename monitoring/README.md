# Monitoring — Loki + Grafana

Collecte **tous les logs Docker** du poste (portail EduPulse, tenant, Evalio, Notara, Traefik, …) via :

**Promtail → Loki → Grafana**

## Démarrer

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
{container=~".*portal.*"}
{compose_project="evalio"}
{compose_project="notara"}
{compose_project="demo"}
{compose_project=~"edupulse.*"}
{job="docker"} |= "error"
```

Dashboard provisionné : **EduPulse — Logs globaux**.
