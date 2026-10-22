# README — EduPulse (Cloud Learning SaaS Multi-Tenant)

EduPulse est une plateforme SaaS multi-tenant en silo dédiée à la centralisation des traces d'apprentissage (xAPI), à l'évaluation analytique des résultats scolaires et à la détection précoce des élèves en difficulté[cite: 1, 8]. Ce document sert de référence d'architecture et de guide opérationnel pour les développeurs et les assistants IA amenés à maintenir ou faire évoluer le projet.

---

## 1. Architecture Globale (Modèle Multi-Tenant en Silo)

L'application est divisée en deux plans séparés qui communiquent avec le démon Docker de la machine hôte[cite: 3, 7] :

| Plan architectural | Dossier | Rôle et responsabilités | Base de données |
| :--- | :--- | :--- | :--- |
| **1. Control Plane (Portail SaaS Public & Super-Admin)** | `portal/` | Vitrine commerciale, inscription des administrateurs d'abonnement, choix du plan (200, 500 ou 1000 utilisateurs), supervision des ressources de la VM (`psutil`) et orchestration Docker Compose des stacks clients[cite: 7, 8]. | Conteneur PostgreSQL dédié (`portal-db`) contenant les tables `portal_users` et `tenant_stacks`[cite: 7]. |
| **2. Data / App Plane (Stack Isolé par Établissement)** | `tenant-stack/` | Instance dédiée à une école regroupant un serveur FastAPI, un Learning Record Store (LRS) xAPI et 3 vues métiers (Étudiant, Enseignant, Admin d'école)[cite: 1, 3, 4]. | 1 conteneur `postgres:16-alpine` isolé par école (`${TENANT_SLUG}-db`) avec tables SQL et stockage `JSONB`[cite: 1, 3]. |

### Mécanisme de provisionnement dynamique
Lorsqu'un client (`SUBSCRIPTION_ADMIN`) ou le `SUPER_ADMIN` crée un établissement depuis le portail (`POST /api/tenants` ou `POST /api/superadmin/tenants`), la fonction `orchestrate_tenant_stack()` de `portal/main.py` exécute la commande système suivante[cite: 7] :

docker compose -p <slug> -f /app/tenant-stack/docker-compose.tenant.yml up -d --build

Elle injecte dynamiquement les variables d'environnement propres au client dans la nouvelle stack Docker[cite: 3, 7] :
* `TENANT_SLUG` : Identifiant réseau unique servant de sous-domaine et de préfixe de conteneurs (`<slug>-app` et `<slug>-db`)[cite: 3, 7].
* `INSTITUTION_NAME` : Nom officiel de l'établissement affiché sur l'interface[cite: 3, 4, 7]. **Règle de peuplement :** si ce nom commence par « demo » ou « démo » (insensible à la casse), le stack s'initialise avec un jeu complet de données de démonstration (3 profs, 2 classes, 12 élèves, ~50 traces xAPI). Dans tous les autres cas, le conteneur arrive **entièrement vide** (seul le compte administrateur principal est créé).
* `MAX_USERS` : Quota strict de comptes autorisés dans la base SQL de l'établissement (`200`, `500` ou `1000`)[cite: 1, 7].
* `ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH`, `ADMIN_FULL_NAME` : Identifiants du compte client sur le portail, utilisés au premier démarrage du stack pour créer automatiquement le compte Administrateur Principal de l'école avec le même mot de passe haché (Bcrypt)[cite: 1, 3, 7].
* `TENANT_PORT` : Port hôte direct calculé selon la formule `8100 + tenant.id` (ex: `8101` pour le premier stack, `8102` pour le second)[cite: 7].

---

## 2. Arborescence du Projet

cloud-learning-saas/
├── docker-compose.yml                  # Socle principal : Traefik + portal-db + portal-api
├── README.md                           # Documentation technique
├── portal/                             # CONTROL PLANE (Site d'accueil & Super-Admin)
│   ├── Dockerfile                      # Image Python du portail
│   ├── requirements.txt                # Dépendances du portail
│   ├── main.py                         # API REST du portail, auth JWT, monitoring VM & orchestration Docker[cite: 7]
│   └── static/
│       └── index.html                  # Interface web (Vitrine commerciale, Espace Client, Super-Admin)[cite: 8]
└── tenant-stack/                       # DATA/APP PLANE (Modèle de stack isolé par établissement)
    ├── docker-compose.tenant.yml       # Template Docker Compose instancié avec -p <slug>[cite: 3, 7]
    ├── Dockerfile                      # Image Python du serveur d'établissement
    ├── requirements.txt                # Dépendances du stack école (FastAPI, SQLAlchemy, psycopg2, bcrypt, PyJWT, Pydantic)[cite: 2]
    ├── demo_data.py                    # Scénarios et données du mode démo (profs, élèves, traces sur 4-5 mois)
    ├── main.py                         # API École : Quota utilisateurs, LRS xAPI JSONB & Algo analytique[cite: 1]
    └── static/
        └── index.html                  # Interface web de l'école (Vues Étudiant, Enseignant, Admin École)[cite: 4]

---

## 3. Routage Réseau, DNS & Ports d'Accès

Le projet centralise le trafic HTTP public sur le port `8000` (ou le port standard `80` configurable via `PUBLIC_PORT` dans `.env`). Chaque stack d'établissement dispose d'un routage sous-domaine automatique via Traefik ainsi que d'un accès par port direct :

* **Configuration DNS Officielle :**
  * **Domaine principal (`edupulse.mnbll.fr`)** : Pointe vers l'IP publique du serveur/VPS. Traefik route automatiquement vers le portail d'accueil public et le panneau Super-Admin (`saas-portal-api`).
  * **Wildcard (`*.edupulse.mnbll.fr`)** : Pointe vers la même IP publique du serveur/VPS. Traefik route dynamiquement tout sous-domaine `http://<slug>.edupulse.mnbll.fr:8000` vers le conteneur applicatif du stack correspondant (`<slug>-app`).
* **Résolution Dynamique des Redirections & Liens :**
  * Toutes les redirections et liens (bouton « Ouvrir l'espace » après création d'un établissement, liens dans les tableaux de bord client et super-admin, liens de retour vers le portail depuis un établissement) s'adaptent dynamiquement au contexte du navigateur :
    * Sur le domaine de production -> URL de sous-domaine `http://<slug>.edupulse.mnbll.fr:8000`.
    * En développement local (`localhost`) -> URL de sous-domaine local `http://<slug>.localhost:8000` (ou port direct `:810X` si accédé sur `:8001`).
    * Par IP directe -> URL sur port dédié `http://<IP>:${8100 + id}`.
* **URLs d'accès direct :**
  * **Portail Public & Super-Admin :** `http://edupulse.mnbll.fr:8000` ou `http://localhost:8000` (et `http://localhost:8001` en accès conteneur direct).
  * **Stack Établissement #1 :** `http://<slug>.edupulse.mnbll.fr:8000` ou `http://<slug>.localhost:8000` via Traefik, ou `http://localhost:8101` en port direct (`8100 + id`).
* **Isolation réseau des bases de données :** Les conteneurs PostgreSQL (`${TENANT_SLUG}-db`) sont uniquement rattachés à leur réseau interne (`tenant-internal-net`) sans port exposé sur l'hôte, tandis que les conteneurs applicatifs (`${TENANT_SLUG}-app`) sont connectés à la fois à `saas-public-net` (pour Traefik) et à `tenant-internal-net`.

---

## 4. Schéma des Bases de Données & LRS xAPI

### A. Base SQL du Portail (`portal-db` — `portal/main.py`)
* **`portal_users`** : Stocke les utilisateurs du portail (`id`, `email`, `full_name`, `password_hash`, `role` valant `SUPER_ADMIN` ou `SUBSCRIPTION_ADMIN`, `created_at`)[cite: 7].
* **`tenant_stacks`** : Référence les stacks déployés (`id`, `institution_name`, `slug`, `domain`, `plan_max_users`, `status` valant `RUNNING` ou `STOPPED`, `owner_id` clé étrangère vers `portal_users`, `created_at`)[cite: 7].

### B. Base SQL d'un Établissement (`${TENANT_SLUG}-db` — `tenant-stack/main.py`)
* **`users`** : Utilisateurs internes à l'établissement (`id`, `email`, `full_name`, `password_hash`, `role` valant `ADMIN`, `ENSEIGNANT` ou `ETUDIANT`, `class_name` pour le rattachement de classe ou filière, `created_at`)[cite: 1]. Avant chaque création (`POST /api/admin/users`), l'API vérifie que le nombre total de comptes est strictement inférieur à `MAX_USERS`[cite: 1].
* **`xapi_statements` (Brique LRS xAPI)** :
  * Lorsqu'un étudiant saisit une note depuis son interface, le frontend construit un Statement xAPI conforme au standard ADL (`actor`, `verb`, `object`, `result`, `context`) et l'envoie à `POST /xapi/statements`[cite: 1, 4].
  * Le statement complet est stocké nativement au format `JSONB` de PostgreSQL dans la colonne `statement_json`, accompagné des colonnes relationnelles `statement_uuid`, `student_id`, `course_name`, `verb`, `score_raw`, `score_max`, `score_scaled` et `timestamp`[cite: 1].
* **`student_alerts` (Diagnostic analytique)** :
  * Table mise à jour après chaque insertion de statement xAPI via la fonction `recompute_student_analytics(db, student_id)`[cite: 1].
  * Calcule la moyenne générale (`average_score`), l'évolution récente (`recent_trend`, différence entre la moyenne des 2 dernières notes et les notes précédentes) et identifie la matière la plus faible (`weakest_course`)[cite: 1].
  * Attribue un niveau de risque (`risk_level`) et génère une recommandation pédagogique (`recommendation`) selon les seuils suivants[cite: 1] :
    * `CRITIQUE` : `avg_score < 8.5` ou (`avg_score < 10.5` et `trend <= -2.0`) -> Entretien individuel d'urgence, inscription au tutorat encadré dans la matière faible et parcours de remédiation hebdomadaire[cite: 1].
    * `VIGILANCE` : `avg_score < 11.5` ou `trend <= -1.5` -> Fiches d'exercices ciblés, quiz d'auto-évaluation et binôme d'entraide avec un pair[cite: 1].
    * `STABLE` : Autres cas -> Maintien du rythme actuel et exercices d'approfondissement[cite: 1].

---

## 5. Comptes par Défaut, Provisionnement Vierge & Mode Démonstration (⚙️)

Le portail d'accueil et chaque stack d'établissement intègrent un bouton de paramètres (`⚙️`) en haut à droite permettant de basculer instantanément entre les rôles[cite: 4, 8].

### Niveau 1 — Portail d'Accueil (`portal/`)
* **Super-Admin Global (`SUPER_ADMIN`)** : Email `superadmin@edupulse.mnbll.fr` / Mot de passe `SuperSecretPassword123!` (auto-initialisé au démarrage de `portal-api`)[cite: 7, 8].
* **Client Démo (`SUBSCRIPTION_ADMIN`)** : Email `direction@lycee-moulin.fr` / Mot de passe `ClientPass123!` (créé ou connecté automatiquement via le menu `⚙️`)[cite: 8].

### Niveau 2 — À l'intérieur de chaque Stack Établissement (`tenant-stack/`)

Au premier démarrage d'un stack (`seed_initial_data`), la logique d'initialisation dépend du **nom officiel de l'établissement** (`INSTITUTION_NAME`) :

#### A. Établissement Standard (Nom ne commençant pas par `demo`)
* **Arrive 100% vierge** : Aucun professeur, aucun élève, aucune trace xAPI.
* **Admin Principal unique (`ADMIN`)** : Initialisé automatiquement avec l'email du client créateur (`ADMIN_EMAIL`) et son mot de passe haché Bcrypt (`ADMIN_PASSWORD_HASH`). Il dispose de tous les droits pour configurer l'école et créer ses utilisateurs dans la limite de son quota `MAX_USERS`.

#### B. Établissement de Démonstration (Nom commençant par `demo` ou `démo`, ex: `Demo Campus`, `demo-school`)
Si la base est neuve (`count <= 1`) et que `MAX_USERS >= 16`, l'établissement est automatiquement pré-rempli avec un jeu de données riche et réaliste.

> [!TIP]
> **Fichier de configuration dédié :** L'intégralité des données et scénarios de démonstration est isolée dans **[`tenant-stack/demo_data.py`](file:///c:/Users/yoan/OneDrive/Documents/MASTER/Cloud%20computing/V2/cloud-learning-saas/tenant-stack/demo_data.py)**.
> Vous pouvez y modifier directement les mots de passe de test (`DEMO_TEACHER_PASSWORD`, `DEMO_STUDENT_PASSWORD`), les listes d'enseignants, de classes, d'étudiants, ainsi que leurs notes et leur historique temporel sans avoir à toucher au code principal de l'API.
* **Admin Principal (`ADMIN`)** : `ADMIN_EMAIL` (même mot de passe que sur le portail).
* **3 Professeurs (`ENSEIGNANT`) — Mot de passe commun : `Demo123!`** :
  * `prof.laurent@<slug>.fr` : **Pr. Sophie Laurent** (Dép. Informatique & Cloud)
  * `prof.dupont@<slug>.fr` : **Dr. Marc Dupont** (Dép. Systèmes & Réseaux)
  * `prof.moreau@<slug>.fr` : **Mme Claire Moreau** (Dép. Génie Logiciel)
* **2 Classes / Promotions** :
  * `M1 Cloud & DevOps` (6 étudiants)
  * `L3 Informatique` (6 étudiants)
* **12 Étudiants (`ETUDIANT`) — Mot de passe commun : `Demo123!`** :
  * **Profils en décrochage critique (`CRITIQUE`)** :
    * *Lucas Martin* (`lucas.martin@...` — M1) : Moyenne 6.0/20, point faible en Architecture Cloud.
    * *Chloé Dubois* (`chloe.dubois@...` — M1) : Chute brutale récente de -7.5 pts (notes passées de 15.0 à 7.5 et 6.5).
    * *Maxime Leroy* (`maxime.leroy@...` — L3) : Difficultés persistantes en programmation (moyenne 7.1/20).
  * **Profils sous surveillance (`VIGILANCE`)** :
    * *Emma Bernard* (`emma.bernard@...` — M1) : Moyenne fragile à 10.1/20.
    * *Thomas Roux* (`thomas.roux@...` — L3) : Tendance baissière continue (-2.5 pts).
    * *Léa Fontaine* (`lea.fontaine@...` — L3) : Point faible marqué en Algorithmique.
  * **Profils réguliers et performants (`STABLE`)** :
    * *Alexandre Mercier* (`alexandre.mercier@...` — M1) : Dynamique de progression positive (+3.5 pts).
    * *Hugo Petit* (`hugo.petit@...` — M1) : Très solide (~16.2/20).
    * *Inès Benali* (`ines.benali@...` — M1) : Major de promotion (~18.25/20).
    * *Camille Girard* (`camille.girard@...` — L3) : Régulier (~13.5/20).
    * *Nathan Simon* (`nathan.simon@...` — L3) : Moyen stable (~12.0/20).
    * *Sarah Lambert* (`sarah.lambert@...` — L3) : Excellent niveau (~15.75/20).
* **~85 Statements xAPI au standard ADL (JSONB) étalés sur 4 à 5 mois** :
  * Répartis sur 5 matières : *Algorithmique & Python*, *Bases de Données SQL*, *Architecture Cloud*, *Réseaux & Sécurité*, *DevOps & Conteneurisation*.
  * **Étalement temporel réaliste sur ~120 jours (4 à 5 mois)** :
    * *Mois -4 (~115-120 jours)* : Début de semestre, rentrée et premières évaluations
    * *Mois -3 (~85-92 jours)* : Contrôles continus et premiers TPs
    * *Mois -2 (~55-65 jours)* : Partiels de mi-semestre
    * *Mois -1 (~25-32 jours)* : Projets semestriels et devoirs de synthèse
    * *Mois 0 (~1-8 jours)* : Dernières évaluations récentes permettant à l'algorithme de calculer la pente d'évolution (`recent_trend`) et le diagnostic de décrochage scolaire.

---

## 6. Consignes Techniques pour la Maintenance (IA & Développeurs)

* **Réseau externe `saas-public-net`** : Ne jamais supprimer ce réseau externe dans `tenant-stack/docker-compose.tenant.yml`, car il permet à Traefik de router les requêtes vers chaque conteneur `${TENANT_SLUG}-app`[cite: 3].
* **Syntaxe Traefik** : Conserver la règle `Host(`${TENANT_SLUG}.${BASE_DOMAIN:-edupulse.mnbll.fr}`) || Host(`${TENANT_SLUG}.edupulse.mnbll.fr`) || Host(`${TENANT_SLUG}.localhost`)` sur l'entrypoint `web` et le port interne `8000`[cite: 3].
* **Routes API du Portail (`portal/main.py`)** :
  * Authentification : `POST /api/auth/register`, `POST /api/auth/login`[cite: 7, 8].
  * Espace Client : `GET /api/tenants/me` (et `/api/tenants/my`), `POST /api/tenants`[cite: 7, 8].
  * Super-Admin : `GET /api/superadmin/vm-stats`, `GET /api/superadmin/tenants`, `POST /api/superadmin/tenants`, `POST /api/superadmin/tenants/{slug}/{action}`, `DELETE /api/superadmin/tenants/{slug}`, `GET /api/superadmin/users`, `DELETE /api/superadmin/users/{user_id}`[cite: 7, 8].
* **Routes API d'un Stack Établissement (`tenant-stack/main.py`)** :
  * Informations & Auth : `GET /api/info`, `POST /api/auth/login`, `POST /api/auth/demo-switch/{target_role}`[cite: 1, 4].
  * Admin École : `GET /api/admin/users`, `POST /api/admin/users`, `DELETE /api/admin/users/{user_id}`, `POST /api/admin/users/import-csv` (import groupé de comptes avec contrôle strict du quota MAX_USERS), `GET /api/admin/users/csv-template` (modèle CSV téléchargeable)[cite: 1, 4].
  * Étudiant & LRS xAPI : `POST /xapi/statements`, `GET /api/student/me` (avec moyennes par matière et timestamps pour le graphique temporel)[cite: 1, 4].
  * Enseignant : `GET /api/teacher/analytics`, `GET /api/teacher/chart-data` (séries temporelles pour comparateur d'élèves, tendance de la promotion, moyennes par matière et distribution des notes)[cite: 1, 4].