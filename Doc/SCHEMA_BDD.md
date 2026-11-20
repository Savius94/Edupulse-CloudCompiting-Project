# Schéma des Bases de Données — EduPulse (Multi-Tenant en Silo)

EduPulse repose sur une architecture **PostgreSQL 16 Alpine** à **deux niveaux strictement isolés** :
1. **Base du Control Plane (`portal-db`)** : unique pour tout le SaaS, elle gère les comptes du portail, le registre des établissements, les réglages globaux et le journal d'orchestration Swarm.
2. **Bases du Data Plane (`<slug>_tenant-db`)** : **une base PostgreSQL dédiée, avec son propre conteneur, son propre volume (`<slug>_tenant_db_data`) et son propre réseau interne (`<slug>_tenant-internal-net`) pour chaque établissement**. Aucune donnée pédagogique n'est partagée entre deux écoles.

---

## 1. Base du Control Plane (`portal-db`)

Définie dans [portal/main.py](file:///c:/Users/yoan/OneDrive/Documents/MASTER/Cloud%20computing/V2/cloud-learning-saas/portal/main.py#L77-L132).

```mermaid
erDiagram
    portal_users ||--o{ tenant_stacks : "possède (owner_id)"

    portal_users {
        int id PK
        varchar(255) email UK "Unique, indexé"
        varchar(255) full_name
        varchar(255) password_hash "Hash Bcrypt"
        varchar(50) role "SUPER_ADMIN | SUBSCRIPTION_ADMIN"
        datetime created_at
    }

    tenant_stacks {
        int id PK
        varchar(255) institution_name
        varchar(63) slug UK "Identifiant réseau & sous-domaine"
        varchar(255) domain UK "<slug>.edupulse.mnbll.fr"
        int plan_max_users "200 | 500 | 1000"
        varchar(50) status "RUNNING | STOPPED"
        int replicas "Réplicas actifs"
        int min_replicas
        int max_replicas
        int autoscale_enabled "1 = actif, 0 = inactif"
        varchar(30) orchestrator "SWARM | COMPOSE"
        int maintenance_mode "1 = maintenance, 0 = normal"
        int owner_id FK "-> portal_users.id (CASCADE)"
        datetime created_at
    }

    cloud_settings {
        varchar(100) key PK "ex: gemini_api_key, gemini_model, simulated_cpu_override"
        varchar(255) value
    }

    cloud_events {
        int id PK
        varchar(20) timestamp
        varchar(30) pillar "AUTOSCALE | AGILITY | SCALABILITY | SWARM"
        varchar(63) slug "Établissement ou cluster-all"
        varchar(500) message
        float cpu_percent "Nullable"
        datetime created_at
    }
```

### Rôle de chaque table (`portal-db`)
* **`portal_users`** : Stocke les comptes d'accès au portail (clients `SUBSCRIPTION_ADMIN` et opérateur `SUPER_ADMIN`). Le `password_hash` Bcrypt du client créateur est transmis au stack de son école pour initialiser son compte `ADMIN` local avec le même mot de passe.
* **`tenant_stacks`** : Registre de toutes les écoles provisionnées (forfait, bornes de réplicas, état `RUNNING`/`STOPPED`, mode maintenance, et lien vers le propriétaire).
* **`cloud_settings`** : Stockage clé/valeur partagé entre tous les réplicas `portal-api` (clé API Gemini, modèle IA actif, override de simulation CPU).
* **`cloud_events`** : Journal d'audit des opérations Cloud (création, scaling, auto-scaling, rolling updates, maintenance).

---

## 2. Base Isolée par Établissement (`<slug>_tenant-db`)

Définie dans [tenant-stack/main.py](file:///c:/Users/yoan/OneDrive/Documents/MASTER/Cloud%20computing/V2/cloud-learning-saas/tenant-stack/main.py#L55-L128). Chaque établissement possède sa propre copie indépendante de ces 5 tables.

```mermaid
erDiagram
    users ||--o{ xapi_statements : "génère (student_id)"
    users ||--o| student_alerts : "possède (student_id)"

    users {
        int id PK
        varchar(255) email UK "Unique, indexé"
        varchar(255) full_name
        varchar(255) password_hash "Hash Bcrypt"
        varchar(50) role "ADMIN | ENSEIGNANT | ETUDIANT"
        varchar(120) class_name "ex: M1 Cloud & DevOps"
        datetime created_at
    }

    xapi_statements {
        int id PK
        varchar(64) statement_uuid UK "UUID ADL xAPI"
        int student_id FK "-> users.id (CASCADE)"
        varchar(120) course_name "Matière"
        varchar(120) verb "Verbe xAPI"
        float score_raw "Note sur 20"
        float score_max "Défaut 20.0"
        float score_scaled "Score normalisé 0.0 à 1.0"
        jsonb statement_json "Statement xAPI complet (JSONB)"
        datetime timestamp
    }

    student_alerts {
        int id PK
        int student_id FK "UK -> users.id (CASCADE)"
        float average_score "Moyenne générale"
        float recent_trend "Pente d'évolution récente"
        int statements_count
        varchar(30) risk_level "CRITIQUE | VIGILANCE | STABLE"
        varchar(120) weakest_course "Matière la plus fragile"
        float estimated_future_score "Note future estimée (/20 à M+1)"
        text recommendation "Conseil IA désanonymisé (prof uniquement)"
        text ai_prompt "Dernier prompt anonymisé (ETUD-XX)"
        text ai_raw_response "Réponse brute Gemini"
        datetime ai_coaching_updated_at
        text teacher_appreciation "Appréciation visible côté étudiant"
        varchar(255) appreciation_author
        datetime appreciation_updated_at
        datetime updated_at
    }

    class_ai_reviews {
        int id PK
        varchar(120) class_name UK "ALL ou nom de la classe"
        text review_text "Bilan IA global de la classe"
        float estimated_class_future_avg "Moyenne future estimée"
        text ai_prompt "Prompt anonymisé envoyé"
        text ai_raw_response "Réponse brute Gemini"
        datetime updated_at
    }

    tenant_settings {
        varchar(100) key PK "ex: maintenance_mode, maintenance_message"
        text value
    }
```

### Rôle de chaque table (`<slug>_tenant-db`)
* **`users`** : Annuaire interne de l'école. Avant toute insertion (création manuelle ou import CSV), l'API vérifie strictement `COUNT(users) < MAX_USERS`.
* **`xapi_statements`** : Learning Record Store (LRS) hybride. Conserve à la fois les colonnes relationnelles indexées pour des requêtes rapides (`student_id`, `course_name`, `score_raw`, `timestamp`) et le document JSON complet au standard ADL xAPI dans la colonne **`statement_json` (`JSONB`)**.
* **`student_alerts`** : Synthèse analytique et IA par étudiant (`1-to-1` avec `users`).
  * **Séparation des visibilités & traçabilité IA** :
    * `ai_prompt` garde la trace du prompt **anonymisé** (`ETUD-01`...) envoyé à Gemini.
    * `recommendation` et `estimated_future_score` stockent le coaching IA **désanonymisé** et la prévision M+1, **strictement réservés à la vue Enseignant** et aux rapports PDF.
    * `teacher_appreciation` stocke le message rédigé manuellement par l'enseignant, seul retour affiché dans la vue Étudiant.
* **`class_ai_reviews`** : Mémorise le dernier bilan IA global généré pour chaque classe (ou `"ALL"`), réutilisé dans l'interface professeur et dans l'assemblage des rapports PDF de classe.
* **`tenant_settings`** : Stocke les paramètres persistés de l'école, notamment l'état et le message du mode maintenance (`maintenance_mode`, `maintenance_message`), partagés entre tous les réplicas `tenant-app` de l'établissement.
