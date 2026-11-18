# Evalio & Notara

Deux applications d’évaluations scolaires (QCM notés sur 20).

| App | URL locale | Dossier |
|-----|------------|---------|
| Evalio | http://localhost:8240 | `evalio/` |
| Notara | http://localhost:8241 | `notara/` |

## Démarrage

```powershell
cd cloud-learning-saas
docker compose -f docker-compose.demo-tenant.yml -p demo up -d

cd ../external-apps/evalio
docker compose up -d --build

cd ../notara
docker compose up -d --build
```

Comptes et jeux de données : voir `TEST_ACCOUNTS.md`  
Script de peuplement : `python seed_demo_attempts.py`
