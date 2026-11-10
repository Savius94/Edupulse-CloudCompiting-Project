# Document d'Architecture et Plan du Projet Cloud Computing
## Plateforme SaaS Multi-Tenant d'Analyse de l'Apprentissage (xAPI & Détection du Décrochage)

---

## 1. Vision et Objectifs du Projet

Ce projet consiste à concevoir et déployer une plateforme **SaaS (Software as a Service) Multi-Tenant en silo** destinée aux établissements d'enseignement. Son objectif métier est de **centraliser les traces d'apprentissage au standard xAPI**, d'évaluer automatiquement la progression des apprenants et d'**identifier les élèves en difficulté** afin de proposer des solutions de remédiation aux enseignants.

L'architecture est structurée en **deux niveaux d'isolation (2-Tier Multi-Tenancy)** optimisés pour fonctionner sur une machine virtuelle (VM) aux ressources limitées :

1. **Niveau 1 — Le Control Plane (Portail Public : `edupulse.mnbll.fr`)** :
   * **Espace Administrateur d'Abonnement (Client)** : Permet à un représentant d'école de créer un compte, puis de provisionner un ou plusieurs établissements en choisissant un nom d'établissement, un sous-domaine DNS (`school1.edupulse.mnbll.fr`) et un plan de capacité (`200`, `500` ou `1000` utilisateurs maximum).
   * **Espace Super-Administrateur Global (Cloud Operator)** : Permet de surveiller en temps réel les ressources de la VM (CPU, RAM, Disque), de superviser tous les stacks Docker déployés, de modifier les quotas, et d'ajouter, suspendre ou supprimer des stacks et des comptes clients.
2. **Niveau 2 — Le Data/App Plane (Stack Établissement Isolé : `<slug>.edupulse.mnbll.fr`)** :
   * Chaque établissement dispose de sa propre pile applicative Docker isolée (1 conteneur API/Frontend + 1 conteneur PostgreSQL dédié).
   * Le compte ayant créé l'établissement sur le portail devient automatiquement le **compte Administrateur principal** de ce stack.
   * Le stack expose trois vues métier : **Vue Administrateur d'école** (gestion des comptes et des rôles sous contrôle du quota), **Vue Étudiant** (saisie de notes générant des statements xAPI) et **Vue Enseignant** (tableau de bord analytique, scores de risque et recommandations pédagogiques).

---

## 2. Pile Technologique (Tech Stack)

Les technologies ont été sélectionnées pour minimiser l'empreinte mémoire ($< 90\text{ Mo}$ de RAM par établissement) tout en respectant les standards industriels du Cloud Computing :

| Couche | Technologie / Image Docker | Rôle et Justification |
| :--- | :--- | :--- |
| **DNS & Réseau** | Enregistrements DNS `A` + Wildcard | `edupulse.mnbll.fr` et `*.edupulse.mnbll.fr` pointent vers l'IP publique de la VM. |
| **Reverse Proxy** | **Traefik v3** (`traefik:v3`) | Découverte automatique des nouveaux stacks via le socket Docker et routage par sous-domaine (`Host()`). Compatible Docker 29+ (API 1.44+). |
| **Backend Portail** | **Python 3.11 + FastAPI** (`python:3.11-slim`) | API REST du portail, monitoring système (`psutil`) et orchestration Docker (`docker compose` / SDK). |
| **BDD Portail** | **PostgreSQL 16 Alpine** (`postgres:16-alpine`) | Stocke les comptes du portail (`SUPER_ADMIN`, `SUBSCRIPTION_ADMIN`) et le registre des stacks déployés. |
| **Backend Stack École** | **Python 3.11 + FastAPI** (`python:3.11-slim`) | Serveur métier de l'école, endpoint LRS xAPI (`/xapi/statements`), contrôle du quota et moteur d'analyse. |
| **BDD & LRS École** | **PostgreSQL 16 Alpine** (`postgres:16-alpine`) | 1 conteneur SQL par stack : tables relationnelles (`users`, `courses`) + stockage natif `JSONB` pour les statements xAPI. |
| **Frontend (Portail & École)** | **HTML5 / Tailwind CSS / Vanilla JS (SPA)** | Interfaces réactives servies directement par FastAPI sans surcharge Node.js en production. |
| **Sécurité & Auth** | **JWT (PyJWT) + Bcrypt (`passlib`)** | Authentification stateless par jeton et partage du hash de mot de passe lors de l'initialisation du stack. |

---

## 3. Arborescence Complète du Projet

```text
cloud-learning-saas/
├── docker-compose.yml                 # Socle principal : Traefik + Portail API + Portail SQL
├── .env                               # Variables globales (Domaine, identifiants Super-Admin)
├── ARCHITECTURE_ET_PLAN_PROJET.md     # Documentation technique du projet
│
├── portal/                            # NIVEAU 1 : CONTROL PLANE (edupulse.mnbll.fr)
│   ├── Dockerfile                     # Image Python + Docker CLI pour orchestrer les stacks
│   ├── requirements.txt               # fastapi, uvicorn, sqlalchemy, psycopg2-binary, psutil...
│   ├── main.py                        # API FastAPI : Auth, Super-Admin, VM Stats & Provisioning
│   └── static/
│       └── index.html                 # Interface Web du Portail (Accueil, Espace Client, Super-Admin)
│
└── tenant-stack/                      # NIVEAU 2 : TEMPLATE DE STACK ÉTABLISSEMENT (<slug>.edupulse.mnbll.fr)
    ├── docker-compose.tenant.yml      # Modèle instancié par école (school1-app + school1-db)
    ├── Dockerfile                     # Image Python de l'application établissement
    ├── requirements.txt               # Dépendances de l'application école
    ├── main.py                        # API École : Auto-init Admin, Quota MAX_USERS, LRS xAPI, Analyse
    └── static/
        └── index.html                 # Interface Web École (Vues Admin, Enseignant, Étudiant)
```

---

## 4. Modèle de Données SQL (Double Niveau)

### 4.1. Base SQL du Portail Public (`portal-db`)

1. **Table `portal_users`** :
   * `id` (PK, Integer)
   * `email` (String, Unique)
   * `full_name` (String)
   * `password_hash` (String — Hash Bcrypt réutilisé pour initialiser le compte admin du stack)
   * `role` (String : `'SUPER_ADMIN'` ou `'SUBSCRIPTION_ADMIN'`)
   * `created_at` (DateTime)

2. **Table `tenant_stacks`** :
   * `id` (PK, Integer)
   * `institution_name` (String — ex: *"Lycée Jean Moulin"*)
   * `slug` (String, Unique — ex: `school1`)
   * `domain` (String — ex: `school1.edupulse.mnbll.fr`)
   * `plan_max_users` (Integer : `200`, `500` ou `1000`)
   * `status` (String : `'RUNNING'`, `'STOPPED'`, `'ERROR'`)
   * `owner_id` (FK $\rightarrow$ `portal_users.id`)
   * `created_at` (DateTime)

### 4.2. Base SQL Dédiée par Stack Établissement (`<slug>-db`)

1. **Table `users`** (Soumise à la contrainte $\text{COUNT}(*) < \text{MAX\_USERS}$) :
   * `id` (PK), `email` (Unique), `full_name`, `password_hash`, `role` (`'ADMIN'`, `'ENSEIGNANT'`, `'ETUDIANT'`), `class_name` (String — promotion/classe), `created_at`.
2. **Table `xapi_statements` (LRS intégré)** :
   * `id` (UUID), `student_id` (FK), `course_name` (String), `verb` (String — ex: `http://adlnet.gov/expapi/verbs/completed`), `score_raw` (Float sur $20$), `score_scaled` (Float entre $0$ et $1$), `raw_statement` (**`JSONB`** — Statement xAPI complet), `timestamp` (DateTime).
3. **Table `student_analytics`** :
   * `student_id` (PK), `average_score` (Float), `trend_slope` (Float), `risk_level` (`'CRITIQUE'`, `'MODERE'`, `'FAIBLE'`), `recommended_solution` (Text).

---

## 5. Formule d'Évaluation Analytique et Empreinte Mémoire

### 5.1. Dimensionnement RAM de la VM
La consommation mémoire totale $M_{\text{totale}}$ (en Mo) sur la machine virtuelle pour $N$ établissements actifs suit l'équation :

$$M_{\text{totale}} = M_{\text{OS}} + M_{\text{Traefik}} + M_{\text{Portail}} + \sum_{i=1}^{N} \left( M_{\text{App}, i} + M_{\text{SQL}, i} \right)$$

Avec des images Alpine et Slim ($M_{\text{App}} \approx 48\text{ Mo}$ et $M_{\text{SQL}} \approx 37\text{ Mo}$), chaque nouveau stack ne consomme qu'environ $85\text{ Mo}$, permettant de faire tourner jusqu'à $15$ stacks sur une VM de $2\text{ Go}$ de RAM.

### 5.2. Algorithme de détection des élèves en difficulté
À chaque réception d'un Statement xAPI, le moteur Python calcule un **Indice de Risque de Décrochage** $R \in [0, 100]$ combinant la moyenne générale $\bar{x}$ (sur $20$), l'écart entre la dernière note $x_n$ et la moyenne, et le taux de réussite :

$$R = \min\left(100, \max\left(0, \left(1 - \frac{\bar{x}}{20}\right) \times 70 + \max\left(0, \frac{\bar{x} - x_n}{20}\right) \times 30 \right)\right)$$

* Si $\bar{x} < 8/20$ ou $R \ge 65$ $\rightarrow$ **Risque Critique** : *Tutorat individuel prioritaire et reprise des fondamentaux*.
* Si $8 \le \bar{x} < 11/20$ ou $40 \le R < 65$ $\rightarrow$ **Risque Modéré** : *Exercices de remédiation ciblés et quiz d'auto-évaluation*.
* Si $\bar{x} \ge 11/20$ et $R < 40$ $\rightarrow$ **Suivi Normal** : *Progression régulière, maintien du rythme d'apprentissage*.

---

## 6. Plan de Réalisation par Étapes

* **Étape 1 (Réalisée ci-dessous) — Le Portail d'Accueil & Control Plane** :
  * Configuration de Traefik et de la base PostgreSQL du portail (`docker-compose.yml`).
  * Backend FastAPI du portail (`portal/main.py`) avec création automatique du `SUPER_ADMIN`, authentification JWT, monitoring VM (`psutil` + Docker) et orchestration `docker compose -p <slug>`.
  * Interface Web complète du portail (`portal/static/index.html`) incluant la page d'accueil, l'espace Client (création de stack avec plan `200`/`500`/`1000`) et le Dashboard Super-Admin.
* **Étape 2 (Prochaine étape) — Le Template de Stack Établissement** :
  * Fichier `tenant-stack/docker-compose.tenant.yml`.
  * Backend FastAPI de l'école (auto-création du compte Admin initial, route `/xapi/statements`, contrôle du quota `MAX_USERS`, calcul des élèves en difficulté).
  * Interface Web de l'école avec ses 3 vues (Étudiant, Enseignant, Administrateur).