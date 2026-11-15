import os
import csv
import io
import json
import time
import uuid
import socket
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

import jwt
from fastapi import FastAPI, Depends, HTTPException, Header, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, func, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from demo_data import seed_demo_dataset

# ================= CONFIGURATION ENVIRONNEMENT DU STACK =================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tenant_user:tenant_password@tenant-db:5432/tenant_db")
TENANT_SLUG = os.getenv("TENANT_SLUG", "school1")
INSTITUTION_NAME = os.getenv("INSTITUTION_NAME", "Établissement Scolaire")
MAX_USERS = int(os.getenv("MAX_USERS", "200"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@ecole.fr")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
ADMIN_FULL_NAME = os.getenv("ADMIN_FULL_NAME", "Administrateur Principal")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-tenant-key")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-2.5-flash-lite"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ================= MODÈLES SQL (ISOLÉS PAR ÉTABLISSEMENT) =================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # ADMIN, ENSEIGNANT, ETUDIANT
    class_name = Column(String(120), nullable=True, default="Générale")
    created_at = Column(DateTime, default=datetime.utcnow)


def is_demo_institution(name: str) -> bool:
    """
    Vérifie si l'établissement doit être pré-rempli avec le jeu de données de démonstration.
    Règle : le 'Nom officiel de l'établissement' (INSTITUTION_NAME) doit débuter
    par 'demo' ou 'démo' (insensible à la casse, espaces éliminés).
    Tous les autres établissements arrivent entièrement vierges (seul l'administrateur de base).
    """
    if not name:
        return False
    clean = name.strip().lower()
    return clean.startswith("demo") or clean.startswith("démo")


class XAPIStatementRecord(Base):
    __tablename__ = "xapi_statements"
    id = Column(Integer, primary_key=True, index=True)
    statement_uuid = Column(String(64), unique=True, index=True, nullable=False)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_name = Column(String(120), nullable=False, index=True)
    verb = Column(String(120), nullable=False)
    score_raw = Column(Float, nullable=False)     # Note sur 20
    score_max = Column(Float, default=20.0)
    score_scaled = Column(Float, nullable=False)  # Entre 0.0 et 1.0
    statement_json = Column(JSONB, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class StudentAlert(Base):
    __tablename__ = "student_alerts"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    average_score = Column(Float, nullable=False)
    recent_trend = Column(Float, default=0.0)
    statements_count = Column(Integer, default=0)
    risk_level = Column(String(30), nullable=False)  # CRITIQUE, VIGILANCE, STABLE
    weakest_course = Column(String(120), nullable=True)
    recommendation = Column(Text, nullable=False, default="")  # Message de coaching IA (visible uniquement côté prof)
    ai_prompt = Column(Text, nullable=True)                    # Dernier prompt envoyé à l'IA
    ai_raw_response = Column(Text, nullable=True)              # Réponse brute reçue de l'IA (JSON / Texte brut)
    ai_coaching_updated_at = Column(DateTime, nullable=True)
    teacher_appreciation = Column(Text, nullable=True)         # Appréciation personnalisée du prof (affichée côté étudiant)
    appreciation_author = Column(String(255), nullable=True)
    appreciation_updated_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ================= SCHÉMAS PYDANTIC =================
class LoginRequest(BaseModel):
    email: str
    password: str

class CreateUserRequest(BaseModel):
    email: str
    full_name: str
    password: str
    role: str  # ADMIN, ENSEIGNANT, ETUDIANT
    class_name: Optional[str] = "Générale"

class ImportCSVRequest(BaseModel):
    csv_content: str

class TeacherAppreciationRequest(BaseModel):
    appreciation: str

class XAPIStatementInput(BaseModel):
    actor: Dict[str, Any]
    verb: Dict[str, Any]
    object: Dict[str, Any]
    result: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None


# ================= INITIALISATION DE L'APPLICATION =================
app = FastAPI(title=f"EduPulse Stack - {INSTITUTION_NAME}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def recompute_student_analytics(db: Session, student_id: int):
    """
    Calcule uniquement les indicateurs statistiques d'un étudiant à partir de ses statements xAPI
    (moyenne, tendance récente, matière la plus fragile, niveau de vigilance).
    Ne génère PAS le message de coaching IA : celui-ci est déclenché manuellement par l'enseignant
    via l'API Gemini afin d'économiser des tokens.
    """
    records = (
        db.query(XAPIStatementRecord)
        .filter(XAPIStatementRecord.student_id == student_id)
        .order_by(XAPIStatementRecord.timestamp.asc())
        .all()
    )
    if not records:
        return

    scores = [r.score_raw for r in records]
    avg_score = round(sum(scores) / len(scores), 2)

    # Calcul de la tendance récente (comparaison des 2 dernières notes avec la moyenne précédente)
    if len(scores) >= 3:
        recent_avg = sum(scores[-2:]) / 2.0
        older_avg = sum(scores[:-2]) / len(scores[:-2])
        trend = round(recent_avg - older_avg, 2)
    elif len(scores) == 2:
        trend = round(scores[-1] - scores[0], 2)
    else:
        trend = 0.0

    # Matière la plus fragile
    course_scores: Dict[str, List[float]] = {}
    for r in records:
        course_scores.setdefault(r.course_name, []).append(r.score_raw)
    course_avgs = {c: sum(vals) / len(vals) for c, vals in course_scores.items()}
    weakest_course = min(course_avgs, key=course_avgs.get)

    # Évaluation statistique du niveau de vigilance (sans génération de texte automatique)
    if avg_score < 8.5 or (avg_score < 10.5 and trend <= -2.0):
        risk_level = "CRITIQUE"
    elif avg_score < 11.5 or trend <= -1.5:
        risk_level = "VIGILANCE"
    else:
        risk_level = "STABLE"

    alert = db.query(StudentAlert).filter(StudentAlert.student_id == student_id).first()
    if not alert:
        alert = StudentAlert(
            student_id=student_id,
            average_score=avg_score,
            recent_trend=trend,
            statements_count=len(scores),
            risk_level=risk_level,
            weakest_course=weakest_course,
            recommendation="",  # Généré uniquement à la demande de l'enseignant via Gemini
            updated_at=datetime.utcnow(),
        )
        db.add(alert)
    else:
        alert.average_score = avg_score
        alert.recent_trend = trend
        alert.statements_count = len(scores)
        alert.risk_level = risk_level
        alert.weakest_course = weakest_course
        alert.updated_at = datetime.utcnow()

    db.commit()


def build_gemini_coaching_prompt(
    student: User,
    alert: StudentAlert,
    records: List[XAPIStatementRecord],
) -> str:
    """
    Construit le prompt pédagogique envoyé au modèle d'IA en injectant en variables
    le profil de l'étudiant et le détail de ses notes.
    """
    course_scores: Dict[str, List[float]] = {}
    for r in records:
        course_scores.setdefault(r.course_name, []).append(r.score_raw)

    course_summary_lines = [
        f"  - {course} : moyenne {round(sum(vals)/len(vals), 2)}/20 ({len(vals)} évaluation(s), min {min(vals)}/20, max {max(vals)}/20)"
        for course, vals in sorted(course_scores.items())
    ]
    recent_records = records[-8:]
    grades_history_lines = [
        f"  - [{r.timestamp.strftime('%d/%m/%Y')}] {r.course_name} ({r.verb}) : {r.score_raw}/20"
        for r in recent_records
    ]

    student_class = getattr(student, "class_name", "Générale") or "Générale"
    prompt = (
        f"Tu es un conseiller pédagogique expert en Learning Analytics pour l'établissement « {INSTITUTION_NAME} ».\n"
        f"Rédige une recommandation de coaching pédagogique personnalisée destinée à l'enseignant pour l'aider à accompagner son étudiant.\n\n"
        f"=== VARIABLES ÉTUDIANT ===\n"
        f"- Nom complet : {student.full_name}\n"
        f"- Promotion / Classe : {student_class}\n"
        f"- Moyenne générale : {alert.average_score}/20 (sur {alert.statements_count} notes)\n"
        f"- Évolution récente : {alert.recent_trend:+.2f} pt(s)\n"
        f"- Niveau de risque statistique : {alert.risk_level}\n"
        f"- Matière la plus fragile : {alert.weakest_course}\n\n"
        f"=== MOYENNES PAR MATIÈRE ===\n"
        + "\n".join(course_summary_lines)
        + "\n\n=== DERNIÈRES NOTES ENREGISTRÉES ===\n"
        + "\n".join(grades_history_lines)
        + "\n\n=== CONSIGNE DE RÉDACTION ===\n"
        "Rédige une analyse concise et directe en 3 à 4 phrases (en français, ton professionnel et bienveillant destiné au professeur). "
        "Synthétise la trajectoire de l'élève en citant ses chiffres clés et propose 2 actions concrètes de coaching à mettre en place dès cette semaine. "
        "Important : Ne commence PAS par des formules de politesse ou de salutation (pas de 'Bonjour', 'Cher enseignant', etc.), entre directement dans le diagnostic pédagogique."
    )
    return prompt


def generate_student_ai_coaching(db: Session, student: User) -> Dict[str, Any]:
    """
    Génère une recommandation de coaching pédagogique via l'IA pour un étudiant donné
    et sauvegarde le conseil ainsi que le prompt et la réponse brute en base.
    """
    recompute_student_analytics(db, student.id)
    records = (
        db.query(XAPIStatementRecord)
        .filter(XAPIStatementRecord.student_id == student.id)
        .order_by(XAPIStatementRecord.timestamp.asc())
        .all()
    )
    alert = db.query(StudentAlert).filter(StudentAlert.student_id == student.id).first()

    if not records or not alert:
        return {
            "student_id": student.id,
            "full_name": student.full_name,
            "generated": False,
            "source": "none",
            "ai_coaching": "Aucune note disponible pour analyser cet élève.",
            "prompt_used": "",
            "raw_response": "",
        }

    prompt = build_gemini_coaching_prompt(student, alert, records)
    coaching_text = ""
    raw_response_str = ""
    source = "simulation"

    # 1. Appel réel à l'API IA si GEMINI_API_KEY est renseignée
    if GEMINI_API_KEY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 2048,
            },
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_bytes = resp.read()
                raw_decoded = resp_bytes.decode("utf-8")
                resp_data = json.loads(raw_decoded)
                try:
                    raw_response_str = json.dumps(resp_data, indent=2, ensure_ascii=False)
                except Exception:
                    raw_response_str = raw_decoded

                candidates = resp_data.get("candidates") or []
                if candidates:
                    candidate = candidates[0]
                    parts = candidate.get("content", {}).get("parts", [])
                    # Dans les modèles avec pensée (thinking), filtrer les parts thought: True
                    text_parts = [p.get("text", "") for p in parts if not p.get("thought", False)]
                    if not text_parts:
                        text_parts = [p.get("text", "") for p in parts]
                    extracted = "".join(text_parts).strip()
                    if extracted:
                        coaching_text = extracted
                        source = "api_ia"
        except urllib.error.HTTPError as http_err:
            try:
                err_body = http_err.read().decode("utf-8")
                raw_response_str = json.dumps({
                    "status_code": http_err.code,
                    "reason": http_err.reason,
                    "error_body": json.loads(err_body) if err_body.startswith("{") else err_body,
                }, indent=2, ensure_ascii=False)
            except Exception:
                raw_response_str = f"HTTP Error {http_err.code}: {http_err.reason}"
            source = f"simulation (erreur HTTP {http_err.code})"
        except Exception as exc:
            raw_response_str = json.dumps({
                "error": str(exc),
                "type": type(exc).__name__,
            }, indent=2, ensure_ascii=False)
            source = f"simulation (secours: {type(exc).__name__})"

    # 2. Simulation pédagogique contextualisée (si pas de clé ou si échec API)
    if not coaching_text:
        course_scores: Dict[str, List[float]] = {}
        for r in records:
            course_scores.setdefault(r.course_name, []).append(r.score_raw)
        course_avgs = {c: round(sum(v) / len(v), 2) for c, v in course_scores.items()}
        weakest_course = alert.weakest_course or min(course_avgs, key=course_avgs.get)
        weakest_avg = course_avgs.get(weakest_course, alert.average_score)
        best_course = max(course_avgs, key=course_avgs.get)
        best_avg = course_avgs.get(best_course, alert.average_score)
        last_rec = records[-1]
        student_class = getattr(student, "class_name", "Générale") or "Générale"

        if alert.risk_level == "CRITIQUE":
            coaching_text = (
                f"Alerte de décrochage prononcée pour {student.full_name} ({student_class}) avec une moyenne globale de {alert.average_score}/20 "
                f"(évolution récente de {alert.recent_trend:+.2f} pts, dernière note à {last_rec.score_raw}/20 en {last_rec.course_name}) "
                f"et une lacune critique en {weakest_course} ({weakest_avg}/20). "
                f"Actions de coaching recommandées : (1) planifier un entretien individuel de remédiation cette semaine en s'appuyant sur "
                f"ses acquis relatifs en {best_course} ({best_avg}/20) pour restaurer la confiance ; "
                f"(2) découper le module {weakest_course} en micro-objectifs progressifs avec un suivi hebdomadaire en binôme."
            )
        elif alert.risk_level == "VIGILANCE":
            coaching_text = (
                f"Profil en zone de vigilance pour {student.full_name} ({student_class}) (moyenne {alert.average_score}/20, dynamique récente de {alert.recent_trend:+.2f} pts) "
                f"marqué par un écart entre {best_course} ({best_avg}/20) et {weakest_course} ({weakest_avg}/20, dernière note : {last_rec.score_raw}/20 en {last_rec.course_name}). "
                f"Actions de coaching recommandées : (1) proposer à {student.full_name} une fiche d'entraînement ciblée sur {weakest_course} "
                f"avant la prochaine évaluation ; (2) faire un point méthodologique de 10 minutes en fin de séance pour consolider les notions clés."
            )
        else:
            coaching_text = (
                f"Excellente maîtrise globale pour {student.full_name} ({student_class}) ({alert.average_score}/20 sur {alert.statements_count} évaluations, tendance {alert.recent_trend:+.2f} pts), "
                f"portée par de très bons résultats en {best_course} ({best_avg}/20) et une dernière note de {last_rec.score_raw}/20 en {last_rec.course_name}. "
                f"Actions de coaching recommandées : (1) valoriser sa régularité et lui proposer un sujet d'approfondissement ou défi avancé en {best_course} ; "
                f"(2) consolider {weakest_course} ({weakest_avg}/20) ou l'inviter à parrainer un camarade de promotion."
            )

        if not raw_response_str:
            raw_response_str = json.dumps({
                "statut": "simulation_locale_active",
                "message": "Génération pédagogique locale basée sur les notes et les variables du prompt.",
                "etudiant": student.full_name,
                "classe": student_class,
                "moyenne": alert.average_score,
                "matiere_fragile": weakest_course,
                "coaching": coaching_text,
            }, indent=2, ensure_ascii=False)

    alert.recommendation = coaching_text
    alert.ai_prompt = prompt
    alert.ai_raw_response = raw_response_str
    alert.ai_coaching_updated_at = datetime.utcnow()
    alert.updated_at = datetime.utcnow()
    db.commit()

    return {
        "student_id": student.id,
        "full_name": student.full_name,
        "generated": True,
        "source": source,
        "ai_coaching": coaching_text,
        "prompt_used": prompt,
        "raw_response": raw_response_str,
        "updated_at": alert.ai_coaching_updated_at.strftime("%d/%m/%Y à %H:%M"),
    }


def seed_initial_data():
    # Attente résiliente de PostgreSQL (indispensable en mode Docker Swarm où tenant-db et tenant-app démarrent en parallèle)
    for attempt in range(20):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception:
            if attempt == 19:
                raise
            time.sleep(1.5)

    # Verrou consultatif PostgreSQL pour éviter toute condition de concurrence si plusieurs réplicas Swarm démarrent en même temps
    try:
        with engine.connect() as lock_conn:
            lock_conn.execute(text("SELECT pg_advisory_lock(424242);"))
            try:
                Base.metadata.create_all(bind=engine)
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS class_name VARCHAR(120) DEFAULT 'Générale';"))
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS ai_prompt TEXT;"))
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS ai_raw_response TEXT;"))
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS ai_coaching_updated_at TIMESTAMP;"))
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS teacher_appreciation TEXT;"))
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS appreciation_author VARCHAR(255);"))
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS appreciation_updated_at TIMESTAMP;"))
                    conn.commit()

                db = SessionLocal()
                try:
                    # 1. Créer le compte Administrateur Principal issu du Portail d'accueil
                    admin = db.query(User).filter(User.email == ADMIN_EMAIL).first()
                    if not admin:
                        password_hash = ADMIN_PASSWORD_HASH if ADMIN_PASSWORD_HASH else pwd_context.hash("AdminPass123!")
                        admin = User(
                            email=ADMIN_EMAIL,
                            full_name=ADMIN_FULL_NAME,
                            password_hash=password_hash,
                            role="ADMIN",
                            class_name="Direction / Admin"
                        )
                        db.add(admin)
                        db.commit()

                    # 2. SEEDING DE DÉMONSTRATION STRICTEMENT CONDITIONNEL :
                    # Uniquement si le nom officiel de l'établissement débute par "demo" (ex: "Demo School", "demo-ecole"),
                    # que la base est neuve (au plus 1 utilisateur, l'admin) et que le quota le permet.
                    # Tout établissement dont le nom ne débute pas par "demo" arrive ENTIÈREMENT VIDE (uniquement l'admin de base).
                    # Les données et scénarios de démonstration sont entièrement configurables dans demo_data.py
                    if is_demo_institution(INSTITUTION_NAME) and db.query(User).count() <= 1 and MAX_USERS >= 16:
                        seed_demo_dataset(
                            db=db,
                            pwd_context=pwd_context,
                            tenant_slug=TENANT_SLUG,
                            institution_name=INSTITUTION_NAME,
                            recompute_analytics_fn=recompute_student_analytics,
                            user_model=User,
                            statement_model=XAPIStatementRecord,
                        )
                finally:
                    db.close()
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(424242);"))
    except Exception:
        pass


@app.on_event("startup")
def on_startup():
    seed_initial_data()


# ================= AUTHENTIFICATION JWT =================
def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(hours=12)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token manquant")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Session expirée ou invalide")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")
    return user


# ================= ROUTES D'INFORMATIONS & CONNEXION =================
@app.get("/api/info")
def get_stack_info(db: Session = Depends(get_db)):
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_statements = db.query(func.count(XAPIStatementRecord.id)).scalar() or 0
    return {
        "tenant_slug": TENANT_SLUG,
        "institution_name": INSTITUTION_NAME,
        "is_demo": is_demo_institution(INSTITUTION_NAME),
        "max_users": MAX_USERS,
        "current_users": total_users,
        "remaining_slots": max(0, MAX_USERS - total_users),
        "total_xapi_statements": total_statements,
        "admin_email": ADMIN_EMAIL,
        "replica_id": socket.gethostname()
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email.lower().strip()).first()
    if not user or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    return {
        "access_token": create_token(user),
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "class_name": getattr(user, "class_name", "Générale") or "Générale"
        }
    }


@app.post("/api/auth/demo-switch/{target_role}")
def demo_switch_role(target_role: str, db: Session = Depends(get_db)):
    """Permet au bouton Paramètres (⚙️) de basculer instantanément entre Admin, Enseignant et Étudiant."""
    role_upper = target_role.upper()
    user = db.query(User).filter(User.role == role_upper).order_by(User.id.asc()).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Aucun compte avec le rôle '{role_upper}' n'est disponible. "
                f"Cet établissement n'a pas été créé avec le préfixe 'demo' ou aucun utilisateur de ce type n'a encore été ajouté."
            )
        )
    return {
        "access_token": create_token(user),
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "class_name": getattr(user, "class_name", "Générale") or "Générale"
        }
    }


# ================= VUE 1 : ADMINISTRATION DE L'ÉTABLISSEMENT (QUOTA DU PLAN) =================
@app.get("/api/admin/users")
def list_users(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur de l'établissement")
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "class_name": getattr(u, "class_name", "Générale") or "Générale",
            "is_primary_admin": u.email == ADMIN_EMAIL,
            "created_at": u.created_at.strftime("%d/%m/%Y %H:%M") if u.created_at else "Récemment"
        }
        for u in users
    ]


@app.post("/api/admin/users")
def create_user(req: CreateUserRequest, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")

    # VÉRIFICATION STRICTE DU QUOTA D'UTILISATEURS DU PLAN (200 / 500 / 1000)
    current_count = db.query(func.count(User.id)).scalar() or 0
    if current_count >= MAX_USERS:
        raise HTTPException(
            status_code=403,
            detail=f"Quota atteint ({current_count}/{MAX_USERS} utilisateurs). Veuillez passer au plan supérieur sur le portail."
        )

    clean_email = req.email.lower().strip()
    if db.query(User).filter(User.email == clean_email).first():
        raise HTTPException(status_code=400, detail="Cet email existe déjà dans l'établissement")

    role_upper = req.role.upper()
    if role_upper not in ("ADMIN", "ENSEIGNANT", "ETUDIANT"):
        raise HTTPException(status_code=400, detail="Rôle invalide (ADMIN, ENSEIGNANT, ETUDIANT)")

    new_user = User(
        email=clean_email,
        full_name=req.full_name.strip(),
        password_hash=pwd_context.hash(req.password),
        role=role_upper,
        class_name=(req.class_name or "Générale").strip()
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "id": new_user.id,
        "email": new_user.email,
        "full_name": new_user.full_name,
        "role": new_user.role,
        "class_name": new_user.class_name
    }


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if target.email == ADMIN_EMAIL:
        raise HTTPException(status_code=400, detail="Impossible de supprimer le compte administrateur principal du stack")
    db.delete(target)
    db.commit()
    return {"status": "deleted"}


def normalize_role_str(role_raw: str) -> Optional[str]:
    clean = (role_raw or "").strip().lower()
    if clean in ("etudiant", "étudiant", "student", "eleve", "élève", "apprenant"):
        return "ETUDIANT"
    if clean in ("enseignant", "prof", "professeur", "teacher", "professeure", "enseignante"):
        return "ENSEIGNANT"
    if clean in ("admin", "administrateur", "administratrice", "direction"):
        return "ADMIN"
    return None


def normalize_csv_header(h: str) -> str:
    clean = h.strip().lower().replace(" ", "_").replace("-", "_")
    if clean in ("nom", "nom_complet", "full_name", "fullname", "nom_prenom", "name", "prenom_nom"):
        return "full_name"
    if clean in ("email", "courriel", "mail", "adresse_email"):
        return "email"
    if clean in ("role", "statut", "fonction", "type"):
        return "role"
    if clean in ("classe", "class_name", "classname", "promotion", "filiere", "groupe", "departement"):
        return "class_name"
    if clean in ("password", "mot_de_passe", "mdp", "pass"):
        return "password"
    return clean


@app.post("/api/admin/users/import-csv")
def import_users_csv(
    req: ImportCSVRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")

    raw_text = req.csv_content.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="Le fichier CSV est vide.")

    # Nettoyage BOM UTF-8 éventuel
    if raw_text.startswith("\ufeff"):
        raw_text = raw_text[1:]

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="Aucune ligne exploitable dans le fichier CSV.")

    first_line = lines[0]
    delimiter = ';' if first_line.count(';') > first_line.count(',') else ','

    reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
    all_rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not all_rows:
        raise HTTPException(status_code=400, detail="Aucune donnée trouvée dans le fichier CSV.")

    # Détection automatique de l'en-tête
    first_row = [c.strip() for c in all_rows[0]]
    normalized_headers = [normalize_csv_header(c) for c in first_row]
    has_header = any(h in ("full_name", "email", "role", "class_name") for h in normalized_headers)

    col_map = {}
    data_rows = []
    if has_header:
        for idx, h in enumerate(normalized_headers):
            if h in ("full_name", "email", "role", "class_name", "password"):
                col_map[h] = idx
        data_rows = all_rows[1:]
    else:
        col_map = {
            "full_name": 0,
            "email": 1,
            "role": 2,
            "class_name": 3,
            "password": 4
        }
        data_rows = all_rows

    if "full_name" not in col_map or "email" not in col_map or "role" not in col_map:
        raise HTTPException(
            status_code=400,
            detail="Colonnes requises manquantes dans le CSV. Le fichier doit comporter au minimum : Nom complet, Email, Rôle."
        )

    # Récupérer les emails existants dans la base
    existing_emails = set(
        e[0].lower() for e in db.query(User.email).all()
    )
    seen_in_csv = set()

    users_to_add = []
    skipped_duplicates = []
    invalid_rows = []

    for row_idx, row in enumerate(data_rows, start=2 if has_header else 1):
        if not row or not any(c.strip() for c in row):
            continue

        def get_val(key: str, default: str = "") -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return default

        full_name = get_val("full_name")
        email = get_val("email").lower()
        role_raw = get_val("role")
        class_name = get_val("class_name", "Générale") or "Générale"
        password = get_val("password", "Ecole123!") or "Ecole123!"

        if not full_name:
            invalid_rows.append({"line": row_idx, "email": email, "reason": "Nom complet manquant"})
            continue

        if not email or "@" not in email or "." not in email:
            invalid_rows.append({"line": row_idx, "email": email, "reason": f"Format d'email invalide : '{email}'"})
            continue

        role_norm = normalize_role_str(role_raw)
        if not role_norm:
            invalid_rows.append({
                "line": row_idx,
                "email": email,
                "reason": f"Rôle invalide '{role_raw}' (attendu : ETUDIANT, ENSEIGNANT ou ADMIN)"
            })
            continue

        if email in existing_emails:
            skipped_duplicates.append({"line": row_idx, "email": email, "full_name": full_name, "reason": "Déjà existant dans l'établissement"})
            continue

        if email in seen_in_csv:
            skipped_duplicates.append({"line": row_idx, "email": email, "full_name": full_name, "reason": "Présent plusieurs fois dans le fichier CSV"})
            continue

        seen_in_csv.add(email)
        users_to_add.append({
            "full_name": full_name,
            "email": email,
            "role": role_norm,
            "class_name": class_name,
            "password": password
        })

    # VÉRIFICATION STRICTE DU QUOTA D'UTILISATEURS DU PLAN
    current_count = db.query(func.count(User.id)).scalar() or 0
    remaining_slots = MAX_USERS - current_count

    if len(users_to_add) > remaining_slots:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dépassement du quota souscrit : vous tentez d'ajouter {len(users_to_add)} utilisateur(s) valide(s), "
                f"mais votre formule n'autorise plus que {remaining_slots} place(s) disponible(s) "
                f"({current_count}/{MAX_USERS} comptes occupés). Veuillez réduire le fichier ou passer au plan supérieur sur le portail."
            )
        )

    # Insertion des utilisateurs validés
    created_list = []
    for u in users_to_add:
        new_u = User(
            full_name=u["full_name"],
            email=u["email"],
            password_hash=pwd_context.hash(u["password"]),
            role=u["role"],
            class_name=u["class_name"]
        )
        db.add(new_u)
        created_list.append(new_u)

    db.commit()

    return {
        "success": True,
        "imported_count": len(created_list),
        "skipped_duplicates_count": len(skipped_duplicates),
        "skipped_duplicates": skipped_duplicates,
        "invalid_count": len(invalid_rows),
        "invalid_rows": invalid_rows,
        "total_rows_processed": len(data_rows),
        "current_users": current_count + len(created_list),
        "max_users": MAX_USERS,
        "remaining_slots": MAX_USERS - (current_count + len(created_list)),
    }


@app.get("/api/admin/users/csv-template")
def get_csv_template(current: User = Depends(get_current_user)):
    if current.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")

    csv_data = (
        "full_name,email,role,class_name,password\n"
        "Jean Dupont,jean.dupont@ecole.fr,ETUDIANT,M1 Cloud & DevOps,Ecole123!\n"
        "Sophie Martin,sophie.martin@ecole.fr,ENSEIGNANT,Département Informatique,Prof123!\n"
        "Claire Moreau,claire.moreau@ecole.fr,ETUDIANT,L3 Informatique,Ecole123!\n"
        "Lucas Bernard,lucas.bernard@ecole.fr,ETUDIANT,M1 Cloud & DevOps,Ecole123!\n"
        "Marc Lefevre,marc.lefevre@ecole.fr,ADMIN,Direction / Scolarité,AdminPass123!\n"
    )
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=modele_utilisateurs_edupulse.csv"
        }
    )


# ================= VUE 2 : LRS xAPI & ESPACE ÉTUDIANT =================
@app.post("/xapi/statements")
def post_xapi_statement(
    stmt: XAPIStatementInput,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Endpoint LRS standardisé : reçoit un statement xAPI JSON, le stocke en JSONB et recalcule le score de risque."""
    stmt_uuid = str(uuid.uuid4())
    raw_score = round(float(stmt.result.get("score", {}).get("raw", 0.0)), 2)
    max_score = float(stmt.result.get("score", {}).get("max", 20.0))
    scaled_score = round(raw_score / max_score, 4) if max_score > 0 else 0.0

    course_name = (
        stmt.object.get("definition", {})
        .get("name", {})
        .get("fr-FR", "Matière Générale")
    )
    verb_display = (
        stmt.verb.get("display", {}).get("fr-FR")
        or stmt.verb.get("id", "completed").split("/")[-1]
    )

    full_payload = {
        "id": stmt_uuid,
        "actor": stmt.actor,
        "verb": stmt.verb,
        "object": stmt.object,
        "result": stmt.result,
        "context": stmt.context or {"platform": INSTITUTION_NAME, "tenant": TENANT_SLUG},
        "timestamp": datetime.utcnow().isoformat()
    }

    record = XAPIStatementRecord(
        statement_uuid=stmt_uuid,
        student_id=current.id,
        course_name=course_name,
        verb=verb_display,
        score_raw=raw_score,
        score_max=max_score,
        score_scaled=scaled_score,
        statement_json=full_payload
    )
    db.add(record)
    db.commit()

    # Mise à jour immédiate de l'évaluation de difficulté de l'étudiant
    recompute_student_analytics(db, current.id)

    return {"id": stmt_uuid, "stored": True, "statement": full_payload}


@app.get("/api/student/me")
def get_student_dashboard(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    records = (
        db.query(XAPIStatementRecord)
        .filter(XAPIStatementRecord.student_id == current.id)
        .order_by(XAPIStatementRecord.timestamp.desc())
        .all()
    )
    alert = db.query(StudentAlert).filter(StudentAlert.student_id == current.id).first()

    # Moyennes personnelles par matière
    course_notes: Dict[str, List[float]] = {}
    for r in records:
        course_notes.setdefault(r.course_name, []).append(r.score_raw)
    course_averages = {
        c: round(sum(vals) / len(vals), 2) for c, vals in course_notes.items()
    }

    return {
        "student": {
            "id": current.id,
            "full_name": current.full_name,
            "email": current.email,
            "class_name": getattr(current, "class_name", "Générale") or "Générale"
        },
        "analytics": {
            "average_score": alert.average_score if alert else None,
            "recent_trend": alert.recent_trend if alert else 0.0,
            "risk_level": alert.risk_level if alert else "SANS_DONNEES",
            "weakest_course": alert.weakest_course if alert else None,
            # IMPORTANT : Le message de coaching IA (alert.recommendation) est strictement réservé à l'enseignant.
            # L'étudiant ne reçoit et ne voit que l'appréciation personnalisée rédigée par son enseignant :
            "teacher_appreciation": (alert.teacher_appreciation if alert and alert.teacher_appreciation else None),
            "appreciation_author": (alert.appreciation_author if alert and alert.appreciation_author else None),
            "appreciation_updated_at": (
                alert.appreciation_updated_at.strftime("%d/%m/%Y à %H:%M")
                if alert and alert.appreciation_updated_at else None
            ),
        },
        "course_averages": course_averages,
        "statements": [
            {
                "id": r.statement_uuid,
                "course_name": r.course_name,
                "score_raw": r.score_raw,
                "score_max": r.score_max,
                "verb": r.verb,
                "timestamp": r.timestamp.strftime("%d/%m/%Y %H:%M"),
                "date": r.timestamp.strftime("%d/%m/%Y"),
                "iso_timestamp": r.timestamp.isoformat(),
                "raw_xapi": r.statement_json
            }
            for r in records
        ]
    }


# ================= VUE 3 : ESPACE ENSEIGNANT (ANALYSE, COACHING IA GEMINI & APPRÉCIATIONS) =================
@app.get("/api/teacher/analytics")
def get_teacher_analytics(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    students = db.query(User).filter(User.role == "ETUDIANT").order_by(User.full_name.asc()).all()
    alerts_map = {a.student_id: a for a in db.query(StudentAlert).all()}

    student_rows = []
    for s in students:
        a = alerts_map.get(s.id)
        ai_text = (a.recommendation or "").strip() if a else ""
        # Nettoyage rétrocompatible : si une ancienne phrase statique d'avant la mise à jour Gemini est présente sans tag ni date IA
        if a and ai_text and not a.ai_coaching_updated_at and ("Solution proposée :" in ai_text):
            ai_text = ""
        student_rows.append({
            "student_id": s.id,
            "full_name": s.full_name,
            "class_name": getattr(s, "class_name", "Générale") or "Générale",
            "email": s.email,
            "average_score": a.average_score if a else None,
            "recent_trend": a.recent_trend if a else 0.0,
            "statements_count": a.statements_count if a else 0,
            "risk_level": a.risk_level if a else "SANS_DONNEES",
            "weakest_course": a.weakest_course if a else "-",
            "ai_coaching": ai_text,
            "has_ai_coaching": bool(ai_text),
            "ai_prompt": (a.ai_prompt if a and a.ai_prompt else None),
            "ai_raw_response": (a.ai_raw_response if a and a.ai_raw_response else None),
            "ai_coaching_updated_at": (
                a.ai_coaching_updated_at.strftime("%d/%m/%Y à %H:%M")
                if a and a.ai_coaching_updated_at else None
            ),
            "teacher_appreciation": (a.teacher_appreciation if a and a.teacher_appreciation else ""),
            "appreciation_author": (a.appreciation_author if a and a.appreciation_author else None),
            "appreciation_updated_at": (
                a.appreciation_updated_at.strftime("%d/%m/%Y à %H:%M")
                if a and a.appreciation_updated_at else None
            ),
        })

    # Tri par niveau d'urgence (CRITIQUE en premier, puis VIGILANCE, puis STABLE)
    priority = {"CRITIQUE": 0, "VIGILANCE": 1, "STABLE": 2, "SANS_DONNEES": 3}
    student_rows.sort(key=lambda x: (priority.get(x["risk_level"], 9), x["average_score"] or 99))

    recent_xapi = (
        db.query(XAPIStatementRecord, User)
        .join(User, User.id == XAPIStatementRecord.student_id)
        .order_by(XAPIStatementRecord.timestamp.desc())
        .limit(15)
        .all()
    )

    scored_vals = [r["average_score"] for r in student_rows if r["average_score"] is not None]
    class_avg = round(sum(scored_vals) / len(scored_vals), 2) if scored_vals else 0.0

    return {
        "ai_info": {
            "active": True,
            "label": "Assistant Pédagogique IA activé",
        },
        "summary": {
            "total_students": len(students),
            "class_average": class_avg,
            "critical_count": sum(1 for r in student_rows if r["risk_level"] == "CRITIQUE"),
            "vigilance_count": sum(1 for r in student_rows if r["risk_level"] == "VIGILANCE"),
            "stable_count": sum(1 for r in student_rows if r["risk_level"] == "STABLE"),
        },
        "students": student_rows,
        "recent_statements": [
            {
                "uuid": rec.statement_uuid,
                "student_name": usr.full_name,
                "course_name": rec.course_name,
                "score_raw": rec.score_raw,
                "timestamp": rec.timestamp.strftime("%d/%m/%Y %H:%M"),
                "statement_json": rec.statement_json
            }
            for rec, usr in recent_xapi
        ]
    }


@app.post("/api/teacher/ai-coaching/all")
def trigger_ai_coaching_all_students(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Déclenchement manuel global : génère les recommandations de coaching IA Gemini
    pour tous les élèves disposant de notes dans l'établissement.
    """
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    students = db.query(User).filter(User.role == "ETUDIANT").order_by(User.full_name.asc()).all()
    results = []
    generated_count = 0
    for s in students:
        res = generate_student_ai_coaching(db, s)
        results.append(res)
        if res.get("generated"):
            generated_count += 1

    return {
        "generated_count": generated_count,
        "total_students": len(students),
        "model": GEMINI_MODEL,
        "results": results,
    }


@app.post("/api/teacher/ai-coaching/{student_id}")
def trigger_ai_coaching_single_student(
    student_id: int,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Déclenchement manuel unitaire (pour économiser des tokens) : génère la recommandation
    de coaching IA Gemini uniquement pour l'étudiant ciblé.
    """
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    student = db.query(User).filter(User.id == student_id, User.role == "ETUDIANT").first()
    if not student:
        raise HTTPException(status_code=404, detail="Étudiant introuvable")

    return generate_student_ai_coaching(db, student)


@app.post("/api/teacher/appreciation/{student_id}")
def save_teacher_appreciation(
    student_id: int,
    req: TeacherAppreciationRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permet à l'enseignant d'enregistrer ou modifier une appréciation personnalisée
    qui sera affichée sur le tableau de bord de l'étudiant.
    """
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    student = db.query(User).filter(User.id == student_id, User.role == "ETUDIANT").first()
    if not student:
        raise HTTPException(status_code=404, detail="Étudiant introuvable")

    alert = db.query(StudentAlert).filter(StudentAlert.student_id == student_id).first()
    if not alert:
        alert = StudentAlert(
            student_id=student_id,
            average_score=0.0,
            recent_trend=0.0,
            statements_count=0,
            risk_level="SANS_DONNEES",
            weakest_course="-",
            recommendation="",
            updated_at=datetime.utcnow(),
        )
        db.add(alert)

    clean_msg = (req.appreciation or "").strip()
    alert.teacher_appreciation = clean_msg if clean_msg else None
    alert.appreciation_author = current.full_name if clean_msg else None
    alert.appreciation_updated_at = datetime.utcnow() if clean_msg else None
    db.commit()

    return {
        "student_id": student.id,
        "full_name": student.full_name,
        "teacher_appreciation": alert.teacher_appreciation or "",
        "appreciation_author": alert.appreciation_author,
        "appreciation_updated_at": (
            alert.appreciation_updated_at.strftime("%d/%m/%Y à %H:%M")
            if alert.appreciation_updated_at else None
        ),
    }


@app.get("/api/teacher/chart-data")
def get_teacher_chart_data(
    class_name: Optional[str] = None,
    course_name: Optional[str] = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    # 1. Classes & Matières existantes pour les filtres
    all_classes_tuples = (
        db.query(User.class_name)
        .filter(User.role == "ETUDIANT", User.class_name.isnot(None))
        .distinct()
        .all()
    )
    available_classes = sorted([c[0] for c in all_classes_tuples if c[0]])

    all_courses_tuples = (
        db.query(XAPIStatementRecord.course_name)
        .distinct()
        .all()
    )
    available_courses = sorted([c[0] for c in all_courses_tuples if c[0]])

    # 2. Requête des étudiants (filtrés optionnellement par classe)
    stu_q = db.query(User).filter(User.role == "ETUDIANT")
    if class_name and class_name != "ALL":
        stu_q = stu_q.filter(User.class_name == class_name)
    students = stu_q.order_by(User.full_name.asc()).all()
    student_ids = [s.id for s in students]

    alerts_map = {
        a.student_id: a
        for a in db.query(StudentAlert).filter(StudentAlert.student_id.in_(student_ids)).all()
    } if student_ids else {}

    students_summary = []
    for s in students:
        a = alerts_map.get(s.id)
        students_summary.append({
            "id": s.id,
            "full_name": s.full_name,
            "class_name": s.class_name or "Générale",
            "average_score": a.average_score if a else None,
            "recent_trend": a.recent_trend if a else 0.0,
            "risk_level": a.risk_level if a else "SANS_DONNEES",
            "weakest_course": a.weakest_course if a else "-",
        })

    if not student_ids:
        return {
            "classes": available_classes,
            "courses": available_courses,
            "students": [],
            "statements": [],
            "course_stats": {},
            "grade_distribution": {},
            "overall_stats": {
                "total_notes": 0,
                "overall_average": 0.0,
                "pass_rate": 0.0,
                "highest_score": 0.0,
                "lowest_score": 0.0,
                "risk_counts": {"CRITIQUE": 0, "VIGILANCE": 0, "STABLE": 0},
            }
        }

    # 3. Requête des statements xAPI triés par date chronologique
    stmt_q = (
        db.query(XAPIStatementRecord, User)
        .join(User, User.id == XAPIStatementRecord.student_id)
        .filter(XAPIStatementRecord.student_id.in_(student_ids))
    )
    if course_name and course_name != "ALL":
        stmt_q = stmt_q.filter(XAPIStatementRecord.course_name == course_name)

    records = stmt_q.order_by(XAPIStatementRecord.timestamp.asc()).all()

    formatted_statements = []
    course_scores: Dict[str, List[float]] = {}
    all_scores: List[float] = []

    for rec, usr in records:
        all_scores.append(rec.score_raw)
        course_scores.setdefault(rec.course_name, []).append(rec.score_raw)
        formatted_statements.append({
            "id": rec.statement_uuid,
            "student_id": usr.id,
            "student_name": usr.full_name,
            "class_name": usr.class_name or "Générale",
            "course_name": rec.course_name,
            "score_raw": rec.score_raw,
            "score_max": rec.score_max,
            "verb": rec.verb,
            "timestamp": rec.timestamp.strftime("%d/%m/%Y"),
            "iso_timestamp": rec.timestamp.isoformat(),
        })

    # Statistiques par matière
    course_stats = {}
    for c_name, scores in course_scores.items():
        c_avg = round(sum(scores) / len(scores), 2)
        c_pass = round((sum(1 for sc in scores if sc >= 10.0) / len(scores)) * 100, 1)
        course_stats[c_name] = {
            "average": c_avg,
            "count": len(scores),
            "pass_rate": c_pass,
            "min": min(scores),
            "max": max(scores),
        }

    # Distribution des tranches de notes
    dist = {
        "< 8": sum(1 for s in all_scores if s < 8.0),
        "8 - 9.9": sum(1 for s in all_scores if 8.0 <= s < 10.0),
        "10 - 11.9": sum(1 for s in all_scores if 10.0 <= s < 12.0),
        "12 - 13.9": sum(1 for s in all_scores if 12.0 <= s < 14.0),
        "14 - 15.9": sum(1 for s in all_scores if 14.0 <= s < 16.0),
        "16 - 20": sum(1 for s in all_scores if s >= 16.0),
    }

    # Comptages de risques
    risk_counts = {
        "CRITIQUE": sum(1 for s in students_summary if s["risk_level"] == "CRITIQUE"),
        "VIGILANCE": sum(1 for s in students_summary if s["risk_level"] == "VIGILANCE"),
        "STABLE": sum(1 for s in students_summary if s["risk_level"] == "STABLE"),
    }

    overall_stats = {
        "total_notes": len(all_scores),
        "overall_average": round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0,
        "pass_rate": round((sum(1 for sc in all_scores if sc >= 10.0) / len(all_scores)) * 100, 1) if all_scores else 0.0,
        "highest_score": max(all_scores) if all_scores else 0.0,
        "lowest_score": min(all_scores) if all_scores else 0.0,
        "risk_counts": risk_counts,
    }

    return {
        "classes": available_classes,
        "courses": available_courses,
        "students": students_summary,
        "statements": formatted_statements,
        "course_stats": course_stats,
        "grade_distribution": dist,
        "overall_stats": overall_stats,
    }


# ================= SERVEUR DE FICHIERS STATIQUES (FRONTEND ÉCOLE) =================
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
