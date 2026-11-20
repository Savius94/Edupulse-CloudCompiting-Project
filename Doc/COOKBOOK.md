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

## Recette 7 — Ajouter des comptes utilisateurs (Admin d'Établissement)

Connecté en **Admin** sur l'URL de l'établissement (`http://<slug>.edupulse.mnbll.fr`) :

### Option A — Création manuelle d'un compte
1. Dans la section **Ajouter des utilisateurs**, rester sur l'onglet **Ajout manuel**.
2. Renseigner le nom complet, l'email, le mot de passe initial, le **rôle** (`ETUDIANT`, `ENSEIGNANT` ou `ADMIN`) et la **classe** (ex. `M1 Cloud & DevOps`).
3. Valider : le compteur de places restantes (`MAX_USERS`) est mis à jour en temps réel.

### Option B — Import groupé par fichier CSV
1. Basculer sur l'onglet **Import CSV**.
2. Cliquer sur **Télécharger le modèle CSV** pour obtenir le format attendu (`email,full_name,password,role,class_name`).
3. Sélectionner votre fichier `.csv` puis cliquer sur **Importer les comptes**.
4. L'API vérifie le quota restant avant insertion et affiche le détail des comptes créés (ou l'erreur si le quota `MAX_USERS` est dépassé).

### Supprimer un compte
* Dans le tableau **Utilisateurs enregistrés dans ce stack**, cliquer sur **Supprimer** sur la ligne concernée *(le compte **Admin Principal** est protégé contre la suppression)*.

---

## Recette 8 — Saisir une note (xAPI) et suivre ses résultats (Étudiant)

Connecté en **Étudiant** :

1. **Consulter ses résultats** :
   * Le haut de la page affiche la moyenne générale, les moyennes par matière et le graphique **Évolution chronologique de mes notes** (filtrable par matière via le menu déroulant).
   * L'encadré **💬 Appréciation personnalisée de votre enseignant** affiche le dernier message publié par votre professeur *(les alertes de décrochage et conseils IA internes restent strictement invisibles pour l'étudiant)*.
2. **Enregistrer une nouvelle note** :
   * Dans **Enregistrer un résultat de cours**, choisir la **Matière**, le **Verbe d'action** (ex. *a complété*, *a réussi*) et saisir une note sur 20 (avec jusqu'à 2 décimales, ex. `14.75`).
   * Valider : la trace est enregistrée au standard **ADL xAPI (`JSONB`)**, apparaît dans l'**Historique de mes Statements xAPI**, et met instantanément à jour les graphiques et indicateurs de risque côté enseignant.

---

## Recette 9 — Regarder la progression d'un élève ou d'une classe (Enseignant)

Connecté en **Enseignant**, dans le bloc **Évolution des notes & Comparateur de progressions** :

1. **Filtrer le périmètre** : utiliser les menus **Classe** et **Matière** pour cibler une promotion ou un cours précis.
2. **Choisir le mode d'affichage** parmi les 5 vues disponibles :
   * **Élève individuel** : sélectionner un étudiant précis dans le menu déroulant pour isoler sa courbe de notes. *(Raccourci : dans la liste des élèves en dessous, cliquer sur l'icône/bouton de focus courbe sur la ligne d'un élève pour ouvrir directement son graphique).*
   * **Comparateur** : cocher plusieurs élèves à comparer, ou utiliser les boutons rapides **Écarts extrêmes (Major vs Décrocheur)**, **Élèves en risque**, **Tout cocher / Tout décocher**.
   * **Tendance de classe** : visualiser la moyenne globale de la classe dans le temps.
   * **Par matière** / **Distribution** : comparer les résultats par cours ou la répartition des notes.
3. **Afficher la projection future (M+1)** :
   * Cliquer sur **🔮 Projection M+1 (30j)** pour tracer en pointillés l'évolution estimée sur les 30 prochains jours (`J+7`, `J+15`, `J+22`, `J+30`).

---

## Recette 10 — Lancer une analyse IA du score d'un élève (ou de toute la classe)

Dans le bloc **Conseils pédagogiques IA & Appréciations personnalisées** (vue **Enseignant**) :

1. **Analyser un seul élève** :
   * Sur la carte/ligne de l'élève souhaité, cliquer sur le bouton de génération IA unitaire.
   * Le serveur envoie un prompt compact **anonymisé** (`ETUD-01`) à Gemini, reçoit la recommandation et la **note future estimée (`/20` à M+1)**, désanonymise le nom pour l'affichage professeur et **écrase** l'ancien conseil de cet élève.
2. **Analyser tous les élèves d'un coup** :
   * Cliquer sur **Générer pour tout le monde / toute la classe** en haut du tableau de bord.
   * Une **requête groupée unique** (`ETUD-01`, `ETUD-02`...) est envoyée à Gemini ; les conseils et notes futures estimées sont ensuite répartis et sauvegardés pour chaque élève (écrasement des anciens conseils).

---

## Recette 11 — Mettre une appréciation personnalisée à un élève (Enseignant)

Sur la carte/ligne d'un élève dans la vue **Enseignant** :

1. Repérer le champ texte d'appréciation dédié à cet élève.
2. Rédiger le message pédagogique (encouragements, points à travailler, convocation en tutorat...).
3. Cliquer sur **Enregistrer / Publier l'appréciation**.
4. Le message est immédiatement visible par l'étudiant concerné sur son propre tableau de bord, avec le nom de l'auteur et la date de mise à jour.

---

## Recette 12 — Générer un Bilan IA de classe & un Rapport PDF complet (Enseignant)

Dans l'encadré **Bilan IA global de la classe & Génération de Rapport PDF Complet** :

1. Sélectionner la **Classe** cible dans le menu déroulant (ou *Toutes les classes*).
2. Cliquer sur **Bilan IA global de la classe** pour obtenir une synthèse collective (points forts, fragilités, moyenne future estimée de la classe et priorités d'action).
3. Cliquer sur **Générer le Rapport PDF Complet** :
   * Si certains élèves de la classe n'ont pas encore de conseil IA ou de note future estimée, le backend lance automatiquement les analyses IA anonymisées manquantes.
   * Un PDF multi-pages est téléchargé avec : les KPIs actuels et projetés M+1, le bilan IA de la classe, le graphique de trajectoire, les statistiques par matière, ainsi que la note future estimée et le conseil IA personnalisé de chaque élève.

---

## Recette 13 — Basculer rapidement de rôle en démonstration (`⚙️`)

Pour enchaîner une démonstration sans ressaisir d'identifiants :

* **Sur le Portail (`edupulse.mnbll.fr`)** : cliquer sur **`⚙️`** en haut à droite pour basculer entre **Client Démo** et **Super-Admin**.
* **Dans un Établissement (`<slug>.edupulse.mnbll.fr`)** : cliquer sur **`⚙️`** en haut à droite pour basculer instantanément entre **Étudiant**, **Enseignant** et **Admin d'Établissement**.

---

## Recette 14 — Dépannage Rapide (Troubleshooting)

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
