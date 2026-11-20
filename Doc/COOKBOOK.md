# Cookbook Opérationnel — EduPulse SaaS (Docker Swarm)

Ce *cookbook* regroupe les recettes pratiques et prêtes à l'emploi pour installer, déployer, exploiter, démontrer et dépanner la plateforme **EduPulse** sur votre cluster Docker Swarm (**1 Manager + 2 Workers**, réseau Tailscale) et en environnement local.

---

## Recette 1 — Préparer la configuration (`.env`)

1. Copier le modèle d'environnement dans `cloud-learning-saas/` :
   ```bash
   cd cloud-learning-saas
   cp .env.example .env
   ```
2. Renseigner les variables essentielles dans `cloud-learning-saas/.env` :
   ```env
   NETWORK_DRIVER=overlay
   ORCHESTRATOR_MODE=auto
   PORTAL_REPLICAS=2
   REGISTRY_HOST=100.85.14.115:5000

   BASE_DOMAIN=edupulse.mnbll.fr
   PUBLIC_PORT=80
   SUPERADMIN_EMAIL=superadmin@edupulse.mnbll.fr
   SUPERADMIN_PASSWORD=SuperSecretPassword123!

   GEMINI_API_KEY=votre_cle_gemini
   GEMINI_MODEL=gemini-3.5-flash-lite
   ```
   *(Laisser `HTTP_PROXY` / `HTTPS_PROXY` vides hors réseau universitaire UPPA).*

---

## Recette 2 — Déployer ou mettre à jour le socle EduPulse (Swarm)

À exécuter sur le **nœud Manager** après un `git pull` ou une modification du code :

```bash
cd cloud-learning-saas
chmod +x deploy-swarm.sh
./deploy-swarm.sh
```

**Ce que fait ce script automatiquement :**
1. Charge `.env` (en supprimant les retours Windows `\r`).
2. Vérifie que Docker Swarm est actif (`docker info`).
3. Déploie le socle `edupulse` (`registry`, `docker-socket-proxy`, `portal-db`, `traefik`, `portal-api`).
4. Attend que le Registry privé (`100.85.14.115:5000`) soit prêt.
5. Construit et pousse `edupulse-portal-api:latest` et `edupulse-tenant-app:latest` dans le Registry.
6. Met à jour `edupulse_portal-api` et propage `GEMINI_API_KEY` / `GEMINI_MODEL` à tous les services `*_tenant-app` déjà déployés.

**Vérification :**
```bash
docker node ls
docker service ls
```

*(Optionnel — Déploiement local mono-machine sans Swarm : `docker compose -f docker-compose.local.yml up -d --build`).*

---

## Recette 3 — Déployer le Monitoring centralisé (Grafana + Loki + Promtail)

À exécuter sur le **nœud Manager** :

```bash
cd monitoring
chmod +x deploy-monitoring-swarm.sh
./deploy-monitoring-swarm.sh
```

* **Accès Grafana** : `http://<IP_MANAGER>:3002` (ou `http://grafana.edupulse.mnbll.fr`) — identifiants par défaut : `admin` / `admin`.
* **Accès Loki API** : `http://<IP_MANAGER>:3100`.
* **Requêtes LogQL prêtes à l'emploi (Grafana → Explore → Loki)** :
  * Tous les conteneurs : `{job="docker"}`
  * Services établissements : `{container_name=~".*tenant-app.*"}`
  * Erreurs applicatives / proxy : `{job="docker"} |~ "(?i)(error|traceback|exception|502|503)"`

---

## Recette 4 — Créer et tester un établissement (Vierge vs Démo)

1. Ouvrir le portail : `http://edupulse.mnbll.fr` (ou `http://localhost`).
2. Cliquer sur **`⚙️`** (en haut à droite) pour basculer rapidement en **Client Démo** (`direction@lycee-moulin.fr` / `ClientPass123!`) ou en **Super-Admin** (`superadmin@edupulse.mnbll.fr` / `SuperSecretPassword123!`), ou créer un nouveau compte client.
3. Créer un établissement :
   * **Établissement de démonstration** : saisir un nom commençant par **`demo`** ou **`démo`** (ex: `Demo Campus`) → pré-rempli automatiquement avec 3 enseignants, 2 classes (`M1 Cloud & DevOps`, `L3 Informatique`), 12 étudiants (`Demo123!`) et ~85 traces xAPI.
   * **Établissement standard** : tout autre nom → base vierge contenant uniquement le compte `ADMIN` du créateur.
4. Accéder à l'école via `http://<slug>.edupulse.mnbll.fr` (ou port direct de secours `:<8100 + id>`).

---

## Recette 5 — Configurer ou changer la clé API Gemini à chaud

Si la clé API Gemini n'était pas dans `.env` ou doit être changée sans redéployer tout le cluster :

1. Se connecter en **Super-Admin** sur le portail.
2. Dans la section **Configuration IA (Manager → Workers)**, saisir la clé `GEMINI_API_KEY` et vérifier le modèle (`gemini-3.5-flash-lite`).
3. Enregistrer : `portal-api` persiste la clé dans `portal_settings` (prioritaire sur `.env`) et met à jour les variables d'environnement de tous les services `*_tenant-app` sur les Workers.
4. Utiliser le bouton **Synchroniser vers les Workers** si une nouvelle école vient d'être déployée.

---

## Recette 6 — Exploiter le Cluster (Scaling, Auto-Scaling, Rolling Update, Maintenance)

Depuis le tableau de bord **Super-Admin** du portail :

* **Scaler manuellement une école** : cliquer sur `-` ou `+` sur la ligne de l'établissement (exécute `docker service scale <slug>_tenant-app=<N>`).
* **Tester l'Auto-Scaling (Élasticité)** : activer `Auto-Scale` sur l'école puis cliquer sur **Simuler un pic de charge** (déclenche le *Scale-Out* à `CPU >= 70%` / `RAM >= 85%` jusqu'à `max_replicas`, puis le *Scale-In* à `CPU <= 30%`).
* **Changer de forfait (Upgrade / Downgrade)** : sélectionner `200`, `500` ou `1000` utilisateurs et valider *(un downgrade est bloqué si l'école dépasse déjà le nouveau quota d'utilisateurs)*.
* **Mettre à jour sans coupure (Zero-Downtime Rolling Update)** :
  * Unitaire : bouton **🔄 Rolling Update** sur une école.
  * Global : bouton **🔄 Rolling Update Global (Tous)** (avec confirmation).
* **Couper proprement l'accès (Mode Maintenance)** :
  * Unitaire : **🛠️ Maintenance** / **✅ Fin Maint.** sur une école.
  * Global : **🛠️ Maintenance Globale** / **✅ Fin Maintenance Globale** (renvoie un `HTTP 503` et affiche l'écran de maintenance sur les stacks ciblés).

---

## Recette 7 — Parcours Pédagogique & IA Anonymisée (Dans un Établissement)

1. **Vue Admin École** : importer une promotion complète via **Import CSV** (modèle téléchargeable sur l'interface), sous contrôle strict du quota `MAX_USERS`.
2. **Vue Étudiant** : saisir une note (jusqu'à 2 décimales, ex: `14.75`) → enregistrement xAPI au format `JSONB` et affichage de l'appréciation personnalisée du professeur.
3. **Vue Enseignant** :
   * Filtrer par **Classe**, **Matière** ou **Élève**.
   * Activer **🔮 Projection M+1 (30j)** pour afficher les courbes d'évolution estimées en pointillés.
   * Cliquer sur **Générer pour toute la classe** (1 seule requête groupée anonymisée `ETUD-01`, `ETUD-02`... avec écrasement des anciens conseils et calcul des notes futures estimées `/20`) ou générer pour **un seul élève**.
   * Générer le **Bilan IA Global de la Classe**.
   * Cliquer sur **Télécharger le Rapport PDF de la Classe** (si des conseils ou notes futures manquent, l'API les génère automatiquement avant de produire le PDF multi-pages).

---

## Recette 8 — Dépannage Rapide (Troubleshooting)

* **Vérifier l'état des réplicas d'une école qui ne répond pas** :
  ```bash
  docker service ls
  docker service ps --no-trunc <slug>_tenant-app
  docker service logs --tail 100 <slug>_tenant-app
  ```
* **Vérifier l'isolation des bases de données par stack** :
  ```bash
  docker volume ls | grep tenant_db_data
  ```
  *(Chaque école possède son propre service `<slug>_tenant-db`, son propre volume `<slug>_tenant_db_data` et son réseau `<slug>_tenant-internal-net`).*
* **Erreur `Permission denied` sur `./deploy-swarm.sh` après un `git pull`** :
  ```bash
  chmod +x cloud-learning-saas/deploy-swarm.sh monitoring/deploy-monitoring-swarm.sh
  ```
* **Tester le routage Traefik / Haute Disponibilité Caddy** :
  * Vérifier les logs Traefik : `docker service logs --tail 100 edupulse_traefik`
  * Consulter la configuration de référence du VPS relais dans [Caddyfile.vps.example](file:///c:/Users/yoan/OneDrive/Documents/MASTER/Cloud%20computing/V2/cloud-learning-saas/Caddyfile.vps.example).
