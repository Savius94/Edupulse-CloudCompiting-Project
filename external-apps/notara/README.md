# Notara — 2ᵉ app externe d’évaluations

Notara est une application **indépendante** (comme Pronote / Evalio) :
les élèves y passent des QCM, la note est calculée ici, puis poussée vers EduPulse via **xAPI**.

Style UI volontairement différent d’Evalio (atelier / cahier), même contrat d’interopérabilité.

## Lancer

```powershell
cd external-apps/notara
docker compose up -d --build
```

URL : **http://localhost:8241**

Compte démo : `emma.bernard@demo.fr` / `Demo123!`  
URL EduPulse : `http://host.docker.internal:8101`
