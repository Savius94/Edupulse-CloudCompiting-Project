# Liste des Fonctionnalités — EduPulse (De la Création d'Établissement aux Rapports Anonymisés)

Ce document recense de manière simple et chronologique toutes les fonctionnalités d'EduPulse, en précisant à chaque fois **ce que fait l'utilisateur (niveau User)** et **comment la plateforme l'exécute (niveau Fonctionnel / Technique)**.

---

## 1. Portail SaaS & Cycle de Vie d'un Établissement (Control Plane)

### 1.1 Inscription, Connexion & Bascule Démo (`⚙️`)
* **Niveau User** : Un responsable d'école crée un compte client (ou bascule en un clic sur le compte client ou Super-Admin via le menu `⚙️` en haut à droite) pour accéder à son espace de gestion.
* **Niveau Fonctionnel** : `POST /api/auth/register` et `POST /api/auth/login` (ou `POST /api/auth/demo-switch`) dans `portal/main.py` vérifient les identifiants avec `bcrypt` dans `portal-db` (`portal_users`) et délivrent un jeton JWT (`HS256`).

### 1.2 Création & Provisionnement d'un Établissement
* **Niveau User** : Le client renseigne le nom de son école, choisit un sous-domaine (`slug`) et un forfait (`200`, `500` ou `1000` utilisateurs max). En quelques secondes, son espace dédié est prêt et accessible via `<slug>.edupulse.mnbll.fr`. *(Astuce : un nom commençant par `demo` ou `démo` pré-remplit l'école avec des données de test).*
* **Niveau Fonctionnel** : `POST /api/tenants` enregistre l'école dans `tenant_stacks`, puis `orchestrate_tenant_stack()` génère un descripteur Swarm avec les variables (`TENANT_SLUG`, `MAX_USERS`, `ADMIN_PASSWORD_HASH`, `GEMINI_API_KEY`, etc.) et lance `docker stack deploy` via `docker-socket-proxy` :
  * La base `<slug>_tenant-db` est placée sur le **Manager** (persistance des données).
  * L'application `<slug>_tenant-app` est répartie sur les **2 Workers** et exposée automatiquement par **Traefik** via ses labels Docker.

### 1.3 Changement de Forfait & Quotas
* **Niveau User** : Le client ou le Super-Admin peut faire évoluer l'abonnement d'une école (`200` ↔ `500` ↔ `1000` utilisateurs) depuis le tableau de bord.
* **Niveau Fonctionnel** : `POST /api/tenants/{slug}/change-plan` met à jour `plan_max_users`, les bornes de réplicas (`min_replicas`, `max_replicas`) et les limites CPU/RAM dans `portal-db`, puis redéploie/ajuste le service Swarm correspondant.

### 1.4 Démarrage, Arrêt & Suppression d'Établissement
* **Niveau User** : Le Super-Admin peut stopper temporairement un établissement, le redémarrer ou le supprimer définitivement.
* **Niveau Fonctionnel** : `POST /api/superadmin/tenants/{slug}/{action}` scale le service à `0` (`stop`), rétablit ses réplicas (`start`), ou exécute `docker stack rm <slug>` et supprime le volume/entrée BDD (`down` / `DELETE`).

### 1.5 Scalabilité Manuelle (`- / +`) & Auto-Scaling Élastique
* **Niveau User** : Le Super-Admin peut ajuster à chaud le nombre de réplicas d'une école (`-` / `+`) ou activer l'Auto-Scale. Un bouton de simulation de charge permet aussi de tester la montée en charge en direct.
* **Niveau Fonctionnel** :
  * Manuel : `POST /api/superadmin/tenants/{slug}/scale` exécute `docker service scale <slug>_tenant-app=<N>`.
  * Automatique : `evaluate_autoscale()` lit CPU/RAM via `psutil` : si `CPU >= 70%` ou `RAM >= 85%`, elle augmente le nombre de réplicas jusqu'à `max_replicas` ; si `CPU <= 30%`, elle le réduit jusqu'à `min_replicas`.

### 1.6 Rolling Update Unitaire & Global (Zero-Downtime)
* **Niveau User** : Le Super-Admin clique sur **🔄 Rolling Update** sur une école ou sur **🔄 Rolling Update Global (Tous)** (avec fenêtre d'avertissement) pour déployer la dernière image applicative sans couper l'accès aux utilisateurs.
* **Niveau Fonctionnel** : `orchestrate_tenant_stack(action="rolling_update")` exécute `docker service update --force --image <TENANT_IMAGE> --update-parallelism 1 --update-delay 3s --update-order start-first <slug>_tenant-app`, renouvelant les conteneurs un par un sur les Workers.

### 1.7 Mode Maintenance Unitaire & Global
* **Niveau User** : Le Super-Admin peut activer ou lever la maintenance sur une école ou sur tout le cluster. Les utilisateurs de l'école voient alors un écran de maintenance bloquant l'accès pendant l'intervention.
* **Niveau Fonctionnel** : `POST /api/superadmin/tenants/{slug}/maintenance` et `/maintenance-all` enregistrent l'état dans `portal-db` et appellent `POST /api/internal/maintenance` sur la stack école. Le middleware `maintenance_guard_middleware` de `tenant-stack/main.py` intercepte alors `/api/*` et `/xapi/*` et renvoie un `HTTP 503` tant que la maintenance est active.

### 1.8 Configuration IA Centralisée (Manager → Workers)
* **Niveau User** : Le Super-Admin saisit ou met à jour la clé API Gemini et le modèle (ex: `gemini-3.5-flash-lite`) depuis le panneau Manager, puis synchronise les Workers en un clic.
* **Niveau Fonctionnel** : `POST /api/superadmin/ai-config` persiste la configuration dans `portal_settings` (prioritaire sur `.env`) et `propagate_ai_config_to_workers()` met à jour `GEMINI_API_KEY` et `GEMINI_MODEL` sur tous les services `*_tenant-app` du cluster.

---

## 2. Gestion des Comptes & Isolation (Admin d'Établissement)

### 2.1 Authentification & Gestion des Rôles
* **Niveau User** : L'administrateur de l'école se connecte sur l'URL de son établissement avec le même mot de passe que sur le portail et accède à l'annuaire des comptes (`ADMIN`, `ENSEIGNANT`, `ETUDIANT`).
* **Niveau Fonctionnel** : Au premier boot du stack, `seed_initial_data()` crée le compte `ADMIN` à partir des variables `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH`. L'API vérifie le JWT et le rôle sur chaque route protégée.

### 2.2 Création Unitaire, Import CSV & Contrôle de Quota (`MAX_USERS`)
* **Niveau User** : L'admin d'école crée des utilisateurs un par un ou importe un fichier CSV complet (modèle téléchargeable fourni), avec un compteur de places restantes affiché en temps réel.
* **Niveau Fonctionnel** : `POST /api/admin/users` et `POST /api/admin/users/import-csv` hachent les mots de passe (`bcrypt`), vérifient la limite `COUNT(users) < MAX_USERS` avant insertion, et refusent tout dépassement de forfait (`HTTP 403`).

---

## 3. Suivi des Notes & LRS xAPI (Étudiant)

### 3.1 Saisie de Notes & Enregistrement xAPI
* **Niveau User** : L'étudiant sélectionne une matière, une action (ex: *a complété*, *a réussi*) et saisit sa note sur 20 (avec jusqu'à 2 décimales, ex: `14.75`).
* **Niveau Fonctionnel** : Le frontend construit un statement conforme au standard **ADL xAPI** (`actor`, `verb`, `object`, `result`) et l'envoie à `POST /xapi/statements`. Le backend le stocke en `JSONB` (`statement_json`) et en colonnes indexées dans `xapi_statements`, puis déclenche immédiatement le recalcul analytique de l'élève.

### 3.2 Tableau de Bord Étudiant & Appréciation Enseignant
* **Niveau User** : L'étudiant consulte ses moyennes par matière, son graphique d'évolution filtrable par cours, ainsi que le message d'appréciation personnalisé rédigé par son professeur (sans jamais voir les alertes internes ni le coaching IA destiné aux professeurs).
* **Niveau Fonctionnel** : `GET /api/student/me` agrège les statements xAPI de l'étudiant connecté et retourne `teacher_appreciation` depuis `student_alerts` en filtrant les champs réservés aux enseignants.

---

## 4. Analytique, Détection du Décrochage & Prévisions (Enseignant)

### 4.1 Calcul Automatique des Risques & Tendance
* **Niveau User** : Le professeur visualise la liste des élèves classés par niveau de vigilance (`CRITIQUE`, `VIGILANCE`, `STABLE`), leur moyenne générale, leur tendance récente et leur matière la plus fragile.
* **Niveau Fonctionnel** : `recompute_student_analytics()` calcule la moyenne globale, compare la pente des dernières notes (`recent_trend`), identifie la matière la plus faible et met à jour `student_alerts`.

### 4.2 Filtres Multi-Critères & Courbe de Projection M+1 (30 jours)
* **Niveau User** : Le professeur peut filtrer le tableau et le graphique par **Classe**, par **Matière** ou par **Élève**, et activer le bouton **« 🔮 Projection M+1 (30j) »** pour afficher en pointillés l'évolution estimée sur le mois suivant.
* **Niveau Fonctionnel** : `GET /api/teacher/chart-data` renvoie l'historique réel et appelle `compute_student_future_curve()` qui projette 4 jalons (`J+7`, `J+15`, `J+22`, `J+30`) à partir de la tendance et de la note future estimée.

### 4.3 Publication d'une Appréciation Personnalisée
* **Niveau User** : Le professeur rédige et publie un retour personnalisé pour un élève donné afin qu'il apparaisse sur le tableau de bord de ce dernier.
* **Niveau Fonctionnel** : `POST /api/teacher/appreciation/{student_id}` enregistre le texte, l'auteur et la date dans `student_alerts` (`teacher_appreciation`, `appreciation_author`, `appreciation_updated_at`).

---

## 5. Intelligence Artificielle, Anonymisation & Rapports PDF

### 5.1 Anonymisation à l'Envoi & Personnalisation à la Réception
* **Niveau User** : Les conseils générés par l'IA citent naturellement le vrai prénom/nom de l'élève dans l'interface professeur et les PDF, tout en garantissant qu'aucun nom réel d'élève n'a quitté le serveur vers l'API externe.
* **Niveau Fonctionnel** : Avant tout appel à Gemini, les identités sont remplacées par des pseudonymes (`ETUD-01`, `ETUD-02`...) avec des prompts compacts contenant uniquement les indicateurs chiffrés. À la réception de la réponse, `deanonymize_text()` remplace chaque `ETUD-XX` par le nom réel de l'élève en base avant stockage et affichage.

### 5.2 Conseil IA & Note Future Estimée (Unitaire vs Groupé / Overwrite)
* **Niveau User** : L'enseignant peut générer un conseil IA et une note future estimée (`/20`) soit pour **un seul élève**, soit pour **tous les élèves en un clic** (remplaçant les anciens conseils concernés).
* **Niveau Fonctionnel** :
  * Unitaire (`POST /api/teacher/ai-coaching/{student_id}`) : envoie 1 prompt compact (`ETUD-01`) et écrase (*overwrite*) le conseil et `estimated_future_score` de cet élève.
  * Groupé (`POST /api/teacher/ai-coaching/all`) : envoie **une seule requête compacte** regroupant tous les élèves (`ETUD-01`, `ETUD-02`...), reçoit un JSON structuré par code `ETUD-XX`, désanonymise et écrase les anciens conseils en base. *(Si aucune clé API valide n'est configurée, un moteur de repli local prend le relais).*

### 5.3 Bilan IA Global de la Classe
* **Niveau User** : En choisissant une classe et en cliquant sur le bouton dédié, le professeur obtient une synthèse globale de la classe (points forts, fragilités collectives, moyenne future estimée et priorités pédagogiques).
* **Niveau Fonctionnel** : `POST /api/teacher/ai-class-review` agrège les statistiques anonymisées de la classe, interroge Gemini et sauvegarde le bilan dans `class_ai_reviews`.

### 5.4 Génération de Rapport PDF Complet par Classe
* **Niveau User** : Le professeur sélectionne une classe et télécharge un rapport PDF complet multi-pages contenant les KPIs actuels et projetés M+1, le bilan IA de la classe, le graphique d'évolution, les stats par matière, ainsi que la note future estimée et le conseil IA personnalisé de chaque élève.
* **Niveau Fonctionnel** : `POST /api/teacher/class-report` vérifie d'abord si tous les élèves de la classe (et la classe elle-même) possèdent un conseil IA et une note future estimée : si certains manquent, l'API déclenche automatiquement leur génération anonymisée avant de retourner l'ensemble des données structurées au générateur PDF (`jsPDF` côté navigateur).

---

## 6. Observabilité & Écosystème Externe

### 6.1 Supervision Cluster & Journal d'Événements Cloud
* **Niveau User** : Le Super-Admin suit en direct la charge CPU/RAM/Disque, le nombre de services Swarm actifs et l'historique des actions (`AUTOSCALE`, `AGILITY`, `SWARM`).
* **Niveau Fonctionnel** : `GET /api/superadmin/vm-stats` interroge `psutil`, `docker service ls` et `cloud_events` dans `portal-db`.

### 6.2 Centralisation des Logs (Promtail + Loki + Grafana)
* **Niveau User** : L'administrateur infrastructure consulte dans Grafana (`:3002`) les journaux temps réel de tous les conteneurs (Portail, Traefik, Établissements sur les Workers et Bases).
* **Niveau Fonctionnel** : Promtail (déployé en mode `global` sur Manager et Workers) lit les logs Docker et les pousse vers Loki (`:3100`) sur le Manager.

### 6.3 Applications Externes Simulées (Evalio & Notara)
* **Niveau User** : Deux plateformes de simulation d'examens fonctionnent en parallèle pour illustrer un environnement multi-applications réaliste.
* **Niveau Fonctionnel** : Déployées dans `external-apps/` sur une **VM séparée** sans aucun lien direct avec EduPulse.
