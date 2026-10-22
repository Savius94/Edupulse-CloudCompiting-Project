"""
=============================================================================
EduPulse — Données et scénarios du Mode Démonstration
=============================================================================
Ce fichier contient l'intégralité des données injectées dans un établissement
dont le nom officiel commence par « demo » (ex: « Demo Lycée », « demo-campus »).

Vous pouvez facilement modifier :
- Les mots de passe par défaut des comptes de démo
- La liste des enseignants et leurs départements
- Les promotions / classes (ex: M1 Cloud & DevOps, L3 Informatique)
- Les étudiants, leurs profils et leurs notes
- L'étalement dans le temps (sur plusieurs mois : 120 jours d'historique)
=============================================================================
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple

# =============================================================================
# 1. MOTS DE PASSE DU MODE DÉMONSTRATION (MODIFIABLES ICI)
# =============================================================================
# Ces mots de passe sont appliqués à tous les profs et étudiants créés pour la démo
DEMO_TEACHER_PASSWORD = "Demo123!"
DEMO_STUDENT_PASSWORD = "Demo123!"

# =============================================================================
# 2. LISTE DES COURS / MATIÈRES DISPONIBLES
# =============================================================================
DEMO_COURSES = [
    "Architecture Cloud",
    "Algorithmique & Python",
    "Bases de Données SQL",
    "Réseaux & Sécurité",
    "DevOps & Conteneurisation",
]

# =============================================================================
# 3. ÉQUIPE PÉDAGOGIQUE (3 ENSEIGNANTS)
# =============================================================================
DEMO_TEACHERS = [
    {
        "email_prefix": "prof.laurent",
        "full_name": "Pr. Sophie Laurent",
        "class_name": "Département Informatique & Cloud",
        "specialties": ["Architecture Cloud", "Algorithmique & Python"],
    },
    {
        "email_prefix": "prof.dupont",
        "full_name": "Dr. Marc Dupont",
        "class_name": "Département Systèmes & Réseaux",
        "specialties": ["Bases de Données SQL", "Réseaux & Sécurité"],
    },
    {
        "email_prefix": "prof.moreau",
        "full_name": "Mme Claire Moreau",
        "class_name": "Département Génie Logiciel & DevOps",
        "specialties": ["DevOps & Conteneurisation", "Génie Logiciel"],
    },
]

# =============================================================================
# 4. ÉTUDIANTS & HISTORIQUE DES NOTES SUR PLUSIEURS MOIS (~120 JOURS)
# =============================================================================
# Chaque note est un tuple :
#   (Matière, Note sur 20, Jours en arrière, Verbe xAPI)
# Les jours en arrière s'échelonnent sur 4 à 5 mois :
#   - ~115-120 jours (Mois -4 : début de semestre / rentrée)
#   - ~85-92 jours   (Mois -3 : premiers contrôles continus)
#   - ~55-65 jours   (Mois -2 : partiels mi-semestre)
#   - ~25-32 jours   (Mois -1 : projets et TP notés)
#   - ~1-8 jours     (Mois  0 : dernières évaluations récentes)
# =============================================================================
DEMO_STUDENTS = [
    # -------------------------------------------------------------------------
    # CLASSE A : M1 Cloud & DevOps (6 étudiants)
    # -------------------------------------------------------------------------
    {
        "email_prefix": "lucas.martin",
        "full_name": "Lucas Martin",
        "class_name": "M1 Cloud & DevOps",
        "profile": "Décrochage continu et sévère (Moyenne < 8.5/20) -> Risque CRITIQUE",
        "grades": [
            ("Architecture Cloud", 6.0, 118, "completed"),
            ("Algorithmique & Python", 7.5, 90, "attempted"),
            ("Bases de Données SQL", 7.0, 62, "completed"),
            ("Architecture Cloud", 5.0, 55, "completed"),
            ("DevOps & Conteneurisation", 6.5, 28, "attempted"),
            ("Algorithmique & Python", 6.0, 8, "completed"),
            ("Architecture Cloud", 4.5, 2, "completed"),
        ],
    },
    {
        "email_prefix": "chloe.dubois",
        "full_name": "Chloé Dubois",
        "class_name": "M1 Cloud & DevOps",
        "profile": "Chute brutale récente de fin de semestre (15 -> 6/20) -> Risque CRITIQUE",
        "grades": [
            ("Architecture Cloud", 15.5, 115, "passed"),
            ("DevOps & Conteneurisation", 15.0, 88, "completed"),
            ("Algorithmique & Python", 16.0, 80, "passed"),
            ("Bases de Données SQL", 14.5, 60, "completed"),
            ("Architecture Cloud", 14.0, 52, "completed"),
            ("DevOps & Conteneurisation", 7.5, 25, "attempted"),
            ("Algorithmique & Python", 7.0, 6, "completed"),
            ("Bases de Données SQL", 6.0, 1, "completed"),
        ],
    },
    {
        "email_prefix": "emma.bernard",
        "full_name": "Emma Bernard",
        "class_name": "M1 Cloud & DevOps",
        "profile": "Fragilité sous la moyenne (~10.1/20, tendance descendante) -> Risque VIGILANCE",
        "grades": [
            ("Architecture Cloud", 11.5, 116, "completed"),
            ("Bases de Données SQL", 11.0, 87, "completed"),
            ("Algorithmique & Python", 10.5, 63, "attempted"),
            ("DevOps & Conteneurisation", 10.0, 54, "completed"),
            ("Architecture Cloud", 9.5, 26, "completed"),
            ("Bases de Données SQL", 9.5, 9, "attempted"),
            ("DevOps & Conteneurisation", 9.0, 2, "completed"),
        ],
    },
    {
        "email_prefix": "alexandre.mercier",
        "full_name": "Alexandre Mercier",
        "class_name": "M1 Cloud & DevOps",
        "profile": "Belle dynamique de progression mois après mois (10 -> 16.5) -> Risque STABLE",
        "grades": [
            ("Algorithmique & Python", 10.0, 120, "attempted"),
            ("Bases de Données SQL", 11.5, 92, "completed"),
            ("Architecture Cloud", 13.0, 66, "completed"),
            ("DevOps & Conteneurisation", 14.0, 58, "passed"),
            ("Algorithmique & Python", 15.0, 31, "completed"),
            ("Architecture Cloud", 15.5, 8, "passed"),
            ("DevOps & Conteneurisation", 16.5, 2, "passed"),
        ],
    },
    {
        "email_prefix": "hugo.petit",
        "full_name": "Hugo Petit",
        "class_name": "M1 Cloud & DevOps",
        "profile": "Excellente régularité académique (~16.4/20) -> Risque STABLE",
        "grades": [
            ("Architecture Cloud", 16.5, 116, "passed"),
            ("DevOps & Conteneurisation", 16.0, 88, "completed"),
            ("Algorithmique & Python", 15.5, 62, "completed"),
            ("Bases de Données SQL", 16.0, 55, "completed"),
            ("Architecture Cloud", 17.0, 28, "passed"),
            ("DevOps & Conteneurisation", 16.5, 7, "completed"),
            ("Algorithmique & Python", 17.0, 2, "passed"),
        ],
    },
    {
        "email_prefix": "ines.benali",
        "full_name": "Inès Benali",
        "class_name": "M1 Cloud & DevOps",
        "profile": "Major de promotion (~18.4/20 constant sur 5 mois) -> Risque STABLE",
        "grades": [
            ("Architecture Cloud", 18.0, 117, "passed"),
            ("DevOps & Conteneurisation", 18.5, 89, "passed"),
            ("Bases de Données SQL", 17.5, 64, "completed"),
            ("Algorithmique & Python", 19.0, 56, "passed"),
            ("Architecture Cloud", 18.5, 29, "passed"),
            ("DevOps & Conteneurisation", 18.0, 8, "passed"),
            ("Algorithmique & Python", 19.5, 1, "passed"),
        ],
    },

    # -------------------------------------------------------------------------
    # CLASSE B : L3 Informatique (6 étudiants)
    # -------------------------------------------------------------------------
    {
        "email_prefix": "maxime.leroy",
        "full_name": "Maxime Leroy",
        "class_name": "L3 Informatique",
        "profile": "Difficultés persistantes sur toutes les matières (~7.0/20) -> Risque CRITIQUE",
        "grades": [
            ("Algorithmique & Python", 7.5, 117, "attempted"),
            ("Bases de Données SQL", 8.0, 89, "completed"),
            ("Réseaux & Sécurité", 6.5, 64, "completed"),
            ("Algorithmique & Python", 7.0, 56, "attempted"),
            ("Bases de Données SQL", 6.0, 27, "completed"),
            ("Réseaux & Sécurité", 7.5, 7, "attempted"),
            ("Algorithmique & Python", 6.5, 3, "completed"),
        ],
    },
    {
        "email_prefix": "thomas.roux",
        "full_name": "Thomas Roux",
        "class_name": "L3 Informatique",
        "profile": "Tendance baissière continue sur 5 mois (14 -> 9/20) -> Risque VIGILANCE",
        "grades": [
            ("Réseaux & Sécurité", 14.0, 114, "passed"),
            ("Bases de Données SQL", 13.0, 86, "completed"),
            ("Algorithmique & Python", 12.0, 61, "completed"),
            ("Réseaux & Sécurité", 11.5, 53, "attempted"),
            ("Bases de Données SQL", 10.0, 29, "completed"),
            ("Algorithmique & Python", 9.5, 8, "attempted"),
            ("Réseaux & Sécurité", 9.0, 2, "completed"),
        ],
    },
    {
        "email_prefix": "lea.fontaine",
        "full_name": "Léa Fontaine",
        "class_name": "L3 Informatique",
        "profile": "Bonne en Réseaux/SQL mais fragilité marquée en Algorithmique -> Risque VIGILANCE",
        "grades": [
            ("Réseaux & Sécurité", 13.5, 119, "completed"),
            ("Bases de Données SQL", 12.5, 91, "completed"),
            ("Algorithmique & Python", 8.5, 65, "attempted"),
            ("Réseaux & Sécurité", 13.0, 57, "passed"),
            ("Algorithmique & Python", 9.0, 30, "attempted"),
            ("Bases de Données SQL", 12.0, 7, "completed"),
            ("Algorithmique & Python", 9.5, 3, "attempted"),
        ],
    },
    {
        "email_prefix": "camille.girard",
        "full_name": "Camille Girard",
        "class_name": "L3 Informatique",
        "profile": "Travail régulier, constant et solide (~13.6/20) -> Risque STABLE",
        "grades": [
            ("Algorithmique & Python", 13.0, 115, "completed"),
            ("Bases de Données SQL", 13.5, 87, "completed"),
            ("Réseaux & Sécurité", 14.0, 63, "passed"),
            ("Algorithmique & Python", 13.0, 54, "completed"),
            ("Bases de Données SQL", 14.5, 27, "passed"),
            ("Réseaux & Sécurité", 13.5, 9, "completed"),
            ("Bases de Données SQL", 14.0, 3, "passed"),
        ],
    },
    {
        "email_prefix": "nathan.simon",
        "full_name": "Nathan Simon",
        "class_name": "L3 Informatique",
        "profile": "Moyen régulier équilibré sur toutes les matières (~12.1/20) -> Risque STABLE",
        "grades": [
            ("Réseaux & Sécurité", 12.0, 118, "completed"),
            ("Bases de Données SQL", 12.5, 90, "completed"),
            ("Algorithmique & Python", 11.5, 65, "attempted"),
            ("Réseaux & Sécurité", 12.5, 57, "completed"),
            ("Bases de Données SQL", 12.0, 30, "completed"),
            ("Algorithmique & Python", 12.5, 8, "completed"),
            ("Réseaux & Sécurité", 12.0, 2, "completed"),
        ],
    },
    {
        "email_prefix": "sarah.lambert",
        "full_name": "Sarah Lambert",
        "class_name": "L3 Informatique",
        "profile": "Très bon niveau académique régulier (~16.1/20) -> Risque STABLE",
        "grades": [
            ("Algorithmique & Python", 15.0, 114, "passed"),
            ("Réseaux & Sécurité", 16.0, 86, "passed"),
            ("Bases de Données SQL", 15.5, 60, "completed"),
            ("Algorithmique & Python", 16.5, 52, "passed"),
            ("Réseaux & Sécurité", 16.0, 26, "passed"),
            ("Bases de Données SQL", 16.5, 7, "passed"),
            ("Algorithmique & Python", 17.0, 1, "passed"),
        ],
    },
]


# =============================================================================
# 5. FONCTION D'INJECTION DU JEU DE DÉMO DANS LA BASE POSTGRESQL
# =============================================================================
def seed_demo_dataset(
    db,
    pwd_context,
    tenant_slug: str,
    institution_name: str,
    recompute_analytics_fn,
    user_model,
    statement_model,
):
    """
    Injecte les 3 enseignants, les 12 étudiants et l'historique complet des
    ~85 statements xAPI sur plusieurs mois, puis calcule l'analyse pédagogique.
    """
    # 1. Création des 3 Enseignants
    for t_data in DEMO_TEACHERS:
        email = f"{t_data['email_prefix']}@{tenant_slug}.fr"
        teacher = user_model(
            email=email,
            full_name=t_data["full_name"],
            password_hash=pwd_context.hash(DEMO_TEACHER_PASSWORD),
            role="ENSEIGNANT",
            class_name=t_data["class_name"],
        )
        db.add(teacher)
    db.commit()

    # 2. Création des 12 Étudiants
    created_students = []
    for s_data in DEMO_STUDENTS:
        email = f"{s_data['email_prefix']}@{tenant_slug}.fr"
        student = user_model(
            email=email,
            full_name=s_data["full_name"],
            password_hash=pwd_context.hash(DEMO_STUDENT_PASSWORD),
            role="ETUDIANT",
            class_name=s_data["class_name"],
        )
        db.add(student)
        created_students.append((student, s_data["grades"]))
    db.commit()

    # 3. Injection des Statements xAPI historiques étalés sur plusieurs mois
    now = datetime.utcnow()
    verb_display_map = {
        "completed": "a complété",
        "passed": "a validé",
        "attempted": "a tenté",
    }

    for student_user, grades in created_students:
        db.refresh(student_user)
        for course, note, days_ago, verb in grades:
            stmt_uuid = str(uuid.uuid4())
            # Calcul du timestamp échelonné dans le passé (plusieurs mois)
            timestamp_dt = now - timedelta(
                days=days_ago,
                hours=int((student_user.id * 3 + days_ago) % 18) + 8,
                minutes=int(student_user.id * 13) % 60,
            )
            course_slug = course.lower().replace(" ", "-").replace("&", "et")
            verb_display = verb_display_map.get(verb, "a complété")

            stmt_payload = {
                "id": stmt_uuid,
                "actor": {
                    "name": student_user.full_name,
                    "mbox": f"mailto:{student_user.email}",
                },
                "verb": {
                    "id": f"http://adlnet.gov/expapi/verbs/{verb}",
                    "display": {"fr-FR": verb_display},
                },
                "object": {
                    "id": f"http://{tenant_slug}.edupulse.mnbll.fr/courses/{course_slug}",
                    "definition": {
                        "name": {"fr-FR": course},
                        "type": "http://adlnet.gov/expapi/activities/assessment",
                    },
                },
                "result": {
                    "score": {
                        "raw": note,
                        "min": 0,
                        "max": 20,
                        "scaled": round(note / 20.0, 4),
                    },
                    "success": note >= 10.0,
                    "completion": True,
                },
                "context": {
                    "platform": institution_name,
                    "tenant": tenant_slug,
                    "class": student_user.class_name,
                },
                "timestamp": timestamp_dt.isoformat(),
            }

            rec = statement_model(
                statement_uuid=stmt_uuid,
                student_id=student_user.id,
                course_name=course,
                verb=verb,
                score_raw=note,
                score_max=20.0,
                score_scaled=round(note / 20.0, 4),
                statement_json=stmt_payload,
                timestamp=timestamp_dt,
            )
            db.add(rec)

    db.commit()

    # 4. Recalcul analytique initial pour chaque étudiant
    for student_user, _ in created_students:
        recompute_analytics_fn(db, student_user.id)

