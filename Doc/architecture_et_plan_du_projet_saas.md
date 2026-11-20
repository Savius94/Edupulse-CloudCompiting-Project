# Document d'Architecture et Plan du Projet Cloud Computing
## Plateforme SaaS Multi-Tenant d'Analyse de l'Apprentissage (xAPI, Docker Swarm & IA Anonymisée)

> 📚 **Documents associés** : [README.md](../README.md) • [FONCTIONNALITES.md](FONCTIONNALITES.md) • [SCHEMA_BDD.md](SCHEMA_BDD.md) • [COOKBOOK.md](COOKBOOK.md)

---

## 1. Vision et Objectifs du Projet

Ce projet consiste à concevoir et déployer une plateforme **SaaS (Software as a Service) Multi-Tenant en silo** destinée aux établissements d'enseignement. Son objectif métier est de **centraliser les traces d'apprentissage au standard xAPI**, d'évaluer automatiquement la progression des apprenants, d'**identifier les élèves en difficulté** et d'assister les enseignants grâce à une **IA pédagogique anonymisée**, des **courbes de projection à M+1** et des **rapports PDF par classe**.

L'architecture est structurée en **deux niveaux d'isolation (2-Tier Multi-Tenancy)** orchestrés sur un cluster **Docker Swarm de 3 VMs** (2 Go RAM / 1 vCPU chacune, interconnectées via **Tailscale**) :

1. **Niveau 1 — Le Control Plane (Portail Public : `edupulse.mnbll.fr`)** :
   * **Espace Administrateur d'Abonnement (Client)** : Permet à un responsable d'école de créer un compte, de provisionner un ou plusieurs établissements (nom, sous-domaine `<slug>.edupulse.mnbll.fr`, forfait `200`, `500` ou `1000` utilisateurs max) et de faire évoluer son forfait à chaud (*Upgrade / Downgrade*).
   * **Espace Super-Administrateur Global (Cloud Operator)** : Permet de superviser les ressources du cluster (`psutil` + Docker Swarm), de gérer les stacks déployés (scaling manuel `- / +`, auto-scaling dynamique avec simulateur de charge, *Rolling Updates* unitaires/globaux sans coupure, mode Maintenance unitaire/global) et de configurer la clé API IA de manière centralisée (Manager → Workers).
2. **Niveau 2 — Le Data/App Plane (Stack Établissement Isolé : `<slug>.edupulse.mnbll.fr`)** :
   * Chaque établissement dispose de sa propre pile applicative Docker Swarm isolée : réplicas `tenant-app` répartis sur les **Workers** + conteneur PostgreSQL `tenant-db` dédié avec volume persistant sur le **Manager** et réseau overlay privé `<slug>_tenant-internal-net`.
   * Le stack expose trois vues métier : **Vue Administrateur d'école** (gestion des comptes, import CSV sous contrôle du quota `MAX_USERS`), **Vue Étudiant** (saisie de notes générant des statements xAPI, graphique filtrable, appréciation de l'enseignant) et **Vue Enseignant** (détection du décrochage, coaching IA anonymisé, bilan de classe, projection M+1 et export PDF).
3. **Écosystème Externe Simulé (`external-apps/`)** :
   * Une **4ᵉ VM séparée** héberge **Evalio** et **Notara** (applications de simulation d'examens), sans aucun lien direct avec EduPulse, afin de simuler un environnement multi-applications externe.

---

## 2. Pile Technologique (Tech Stack)

Les technologies ont été sélectionnées pour minimiser l'empreinte mémoire tout en exploitant les standards industriels du Cloud Computing et de l'orchestration :

| Couche | Technologie / Image Docker | Rôle et Justification |
| :--- | :--- | :--- |
| **DNS & Réseau** | DNS `A` + Wildcard (`*.edupulse.mnbll.fr`) + **Tailscale** | Interconnexion privée (`100.x.x.x`) entre le Manager, les 2 Workers et le VPS frontal (Caddy HA). |
| **Orchestration** | **Docker Swarm** (`deploy` / `service` / `stack`) + repli Compose | Répartition des services entre **1 Manager** (BDDs, Registry, Socket-Proxy, Monitoring) et **2 Workers** (Traefik, `portal-api`, `tenant-app`). |
| **Registry & Sécurité API** | `registry:2` + `tecnativa/docker-socket-proxy` | Diffusion des images sur les Workers et exposition sécurisée du socket Docker du Manager. |
| **Reverse Proxy** | **Traefik v3** (`traefik:v3`, mode `global`) | Découverte automatique des stacks Swarm via labels Docker et routage par sous-domaine (`Host()`). |
| **Backend Portail** | **Python 3.11 + FastAPI** | API REST du portail, auth JWT, monitoring (`psutil`), auto-scaling et pilotage Swarm. |
| **Backend Stack École** | **Python 3.11 + FastAPI** | Serveur métier de l'école, LRS xAPI (`/xapi/statements`), analytics, anonymisation IA (`ETUD-XX`) et préparation des rapports PDF. |
| **Bases de Données** | **PostgreSQL 16 Alpine** (`postgres:16-alpine`) | `portal-db` pour le Control Plane + 1 base `<slug>_tenant-db` isolée par école avec stockage natif `JSONB`. |
| **IA Générative** | **Google Gemini API** (`gemini-3.5-flash-lite`) | Coaching pédagogique (unitaire & groupé), estimation de note future à M+1 et bilan global de classe, avec repli local si absent. |
| **Observabilité** | **Grafana + Loki + Promtail** | Collecte centralisée des logs Docker sur le Manager et les 2 Workers. |
| **Frontend** | **HTML5 / Tailwind CSS / Vanilla JS / Chart.js / jsPDF** | SPA légères sans build Node.js en production, graphiques interactifs et génération PDF côté client. |

---

## 3. Arborescence Complète du Projet

```text
Edupulse-CloudCompiting-Project/
├── README.md                                  # Architecture Swarm détaillée & guide opérationnel (racine GitHub)
├── Doc/
│   ├── FONCTIONNALITES.md                     # Liste des fonctionnalités (User & Fonctionnel)
│   ├── SCHEMA_BDD.md                          # Diagrammes ER et dictionnaire des tables SQL
│   ├── COOKBOOK.md                            # Recettes pratiques de déploiement & exploitation
│   └── architecture_et_plan_du_projet_saas.md # Ce document
├── cloud-learning-saas/
│   ├── docker-compose.yml                     # Socle Swarm : Registry, Socket-Proxy, Portal-DB, Traefik, Portal-API
│   ├── deploy-swarm.sh                        # Build, push Registry et déploiement Swarm
│   ├── Caddyfile.vps.example                  # Configuration Haute Disponibilité du VPS relais
│   ├── portal/                                # NIVEAU 1 : CONTROL PLANE
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py                            # API Portail : Auth, Super-Admin, Auto-Scaling, Rolling Update, Maintenance
│   │   └── static/index.html                  # Interface Web du Portail
│   └── tenant-stack/                          # NIVEAU 2 : STACK ÉTABLISSEMENT ISOLÉ
│       ├── docker-compose.tenant.yml          # Modèle Swarm/Compose instancié par école
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── demo_data.py                       # Jeu de données de démonstration
│       ├── main.py                            # API École : Quota, LRS xAPI, Analytique, IA anonymisée, PDF
│       └── static/index.html                  # Interface Web École (Admin, Enseignant, Étudiant)
├── monitoring/                                # Stack Grafana + Loki + Promtail (Local & Swarm)
└── external-apps/                             # VM externe séparée : Evalio & Notara
```

---

## 4. Modèle de Données SQL (Double Niveau — Synthèse)

*(Voir le schéma complet avec diagrammes ER dans [SCHEMA_BDD.md](SCHEMA_BDD.md)).*

### 4.1. Base SQL du Portail Public (`portal-db`)
1. **`portal_users`** : `id`, `email` (Unique), `full_name`, `password_hash` (Bcrypt), `role` (`SUPER_ADMIN` | `SUBSCRIPTION_ADMIN`), `created_at`.
2. **`tenant_stacks`** : `id`, `institution_name`, `slug` (Unique), `domain` (Unique), `plan_max_users` (`200` | `500` | `1000`), `status` (`RUNNING` | `STOPPED`), `replicas`, `min_replicas`, `max_replicas`, `autoscale_enabled`, `orchestrator`, `maintenance_mode`, `owner_id` (FK $\rightarrow$ `portal_users.id`), `created_at`.
3. **`cloud_settings`** & **`cloud_events`** : Paramètres globaux (`gemini_api_key`, `gemini_model`, `simulated_cpu_override`) et journal des événements Swarm.

### 4.2. Base SQL Dédiée par Stack Établissement (`<slug>_tenant-db`)
1. **`users`** (soumise à `COUNT(*) < MAX_USERS`) : `id`, `email` (Unique), `full_name`, `password_hash`, `role` (`ADMIN` | `ENSEIGNANT` | `ETUDIANT`), `class_name`, `created_at`.
2. **`xapi_statements` (LRS intégré)** : `id`, `statement_uuid` (Unique), `student_id` (FK), `course_name`, `verb`, `score_raw` (`/20`), `score_max`, `score_scaled`, `statement_json` (**`JSONB`**), `timestamp`.
3. **`student_alerts`** : `id`, `student_id` (Unique FK), `average_score`, `recent_trend`, `statements_count`, `risk_level` (`CRITIQUE` | `VIGILANCE` | `STABLE`), `weakest_course`, `estimated_future_score`, `recommendation` (conseil IA désanonymisé), `ai_prompt` (anonymisé `ETUD-XX`), `ai_raw_response`, `teacher_appreciation` (affiché côté étudiant), `appreciation_author`, `updated_at`.
4. **`class_ai_reviews`** & **`tenant_settings`** : Bilans IA globaux par classe et état du mode maintenance local.

---

## 5. Dimensionnement, Forfaits & Évaluation Analytique

### 5.1. Dimensionnement des Forfaits & Contraintes Swarm
Chaque VM du cluster disposant de **2 Go de RAM / 1 vCPU**, les limites de ressources par réplica `tenant-app` sont calibrées selon le forfait souscrit (`PLAN_SCALING_RULES` dans `portal/main.py`) :

| Forfait | Quota `MAX_USERS` | Réplicas initiaux | Plage Auto-Scale | Limite CPU / réplica | Limite RAM / réplica |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Essentiel** | 200 | 2 | 1 ↔ 3 | `0.35` vCPU | `200M` |
| **Campus** | 500 | 2 | 2 ↔ 5 | `0.45` vCPU | `256M` |
| **Académie** | 1 000 | 3 | 2 ↔ 8 | `0.55` vCPU | `320M` |

### 5.2. Détection du Décrochage, Anonymisation IA & Projection M+1
À chaque insertion d'un statement xAPI, `recompute_student_analytics()` met à jour la moyenne générale, la pente d'évolution récente (`recent_trend`), la matière la plus fragile et le niveau de risque (`CRITIQUE`, `VIGILANCE`, `STABLE`).
* **Confidentialité IA** : Avant tout appel externe à Gemini, les identités sont remplacées par des pseudonymes (`ETUD-01`, `ETUD-02`...) ; la réponse est ensuite personnalisée localement via `deanonymize_text()`.
* **Prévisions M+1** : L'IA (ou le moteur de repli) estime une note future à 30 jours (`estimated_future_score`) et alimente la courbe de projection (`J+7`, `J+15`, `J+22`, `J+30`) ainsi que les rapports PDF complets par classe.

---

## 6. État d'Avancement (Plan Réalisé)

* **Étape 1 (Réalisée) — Portail d'Accueil & Control Plane** : Socle `docker-compose.yml`, API `portal/main.py` (auth JWT, monitoring `psutil`, forfaits, auto-scaling, rolling update unitaire/global, maintenance unitaire/globale, configuration IA centralisée) et interface `portal/static/index.html`.
* **Étape 2 (Réalisée) — Template de Stack Établissement** : `tenant-stack/docker-compose.tenant.yml`, API `tenant-stack/main.py` (LRS xAPI `JSONB`, import CSV, quotas, analytics, IA anonymisée, appréciations, projection M+1, préparation PDF), jeu de démo `demo_data.py` et interface `tenant-stack/static/index.html`.
* **Étape 3 (Réalisée) — Cluster Docker Swarm, Monitoring & Haute Disponibilité** : Déploiement multi-VMs (1 Manager + 2 Workers via Tailscale, Registry privé, `docker-socket-proxy`, `deploy-swarm.sh`), centralisation des logs (`monitoring/` avec Grafana, Loki, Promtail) et apps externes simulées (`external-apps/` : Evalio & Notara).
