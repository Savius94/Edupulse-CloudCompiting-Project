# Applications externes d’évaluation

Ces apps **ne sont pas intégrées** dans EduPulse. Elles simulent des outils tiers (type Pronote) :
l’élève passe un QCM ici, puis la note est poussée vers le LRS du tenant via **xAPI**.

| App | Style | Port local | Dossier |
|-----|-------|------------|---------|
| **Evalio** | Borne / scantron sombre | http://localhost:8240 | `evalio/` |
| **Notara** | Cahier / atelier papier | http://localhost:8241 | `notara/` |

## Contrat d’interopérabilité commun

1. `POST {tenant}/api/auth/login` — JWT élève  
2. Correction locale du QCM → note `/20`  
3. `POST {tenant}/xapi/statements` — statement Experience API  

## Démarrage rapide

```powershell
# Tenant démo EduPulse (port 8101)
cd cloud-learning-saas
docker compose -f docker-compose.demo-tenant.yml -p demo up -d

# Apps externes
cd ../external-apps/evalio
docker compose up -d --build
cd ../notara
docker compose up -d --build
```

Compte élève démo : `lucas.martin@demo.fr` / `Demo123!`  
URL EduPulse à saisir : `http://host.docker.internal:8101`
