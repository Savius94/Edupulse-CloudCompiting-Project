import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

import jwt
from fastapi import FastAPI, Depends, HTTPException, Header
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
    recommendation = Column(Text, nullable=False)
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
    """Analyse les statements xAPI d'un étudiant et met à jour son diagnostic pédagogique."""
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
    weakest_val = round(course_avgs[weakest_course], 1)

    # Évaluation du niveau de difficulté et proposition de solution
    if avg_score < 8.5 or (avg_score < 10.5 and trend <= -2.0):
        risk_level = "CRITIQUE"
        recommendation = (
            f"Décrochage détecté (moyenne {avg_score}/20, point faible en {weakest_course} à {weakest_val}/20). "
            f"Solution proposée : Entretien individuel d'urgence, inscription au tutorat encadré en {weakest_course} "
            f"et activation d'un parcours de remédiation hebdomadaire."
        )
    elif avg_score < 11.5 or trend <= -1.5:
        risk_level = "VIGILANCE"
        recommendation = (
            f"Fragilité identifiée en {weakest_course} ({weakest_val}/20, tendance {trend:+.1f} pts). "
            f"Solution proposée : Attribution de fiches d'exercices ciblés, quiz d'auto-évaluation "
            f"et binôme d'entraide avec un pair."
        )
    else:
        risk_level = "STABLE"
        recommendation = (
            f"Progression régulière ({avg_score}/20). "
            f"Solution proposée : Maintenir le rythme actuel et proposer des exercices d'approfondissement."
        )

    alert = db.query(StudentAlert).filter(StudentAlert.student_id == student_id).first()
    if not alert:
        alert = StudentAlert(
            student_id=student_id,
            average_score=avg_score,
            recent_trend=trend,
            statements_count=len(scores),
            risk_level=risk_level,
            weakest_course=weakest_course,
            recommendation=recommendation,
            updated_at=datetime.utcnow(),
        )
        db.add(alert)
    else:
        alert.average_score = avg_score
        alert.recent_trend = trend
        alert.statements_count = len(scores)
        alert.risk_level = risk_level
        alert.weakest_course = weakest_course
        alert.recommendation = recommendation
        alert.updated_at = datetime.utcnow()

    db.commit()


def seed_initial_data():
    Base.metadata.create_all(bind=engine)
    # Migration défensive : ajout de class_name si le volume DB existait déjà
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS class_name VARCHAR(120) DEFAULT 'Générale';"))
            conn.commit()
    except Exception:
        pass

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
        "admin_email": ADMIN_EMAIL
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


# ================= VUE 2 : LRS xAPI & ESPACE ÉTUDIANT =================
@app.post("/xapi/statements")
def post_xapi_statement(
    stmt: XAPIStatementInput,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Endpoint LRS standardisé : reçoit un statement xAPI JSON, le stocke en JSONB et recalcule le score de risque."""
    stmt_uuid = str(uuid.uuid4())
    raw_score = float(stmt.result.get("score", {}).get("raw", 0.0))
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
            "recommendation": alert.recommendation if alert else "Saisissez vos premières notes pour obtenir un bilan personnalisé."
        },
        "statements": [
            {
                "id": r.statement_uuid,
                "course_name": r.course_name,
                "score_raw": r.score_raw,
                "score_max": r.score_max,
                "timestamp": r.timestamp.strftime("%d/%m/%Y %H:%M"),
                "raw_xapi": r.statement_json
            }
            for r in records
        ]
    }


# ================= VUE 3 : ESPACE ENSEIGNANT (ANALYSE & ÉLÈVES EN DIFFICULTÉ) =================
@app.get("/api/teacher/analytics")
def get_teacher_analytics(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    students = db.query(User).filter(User.role == "ETUDIANT").order_by(User.full_name.asc()).all()
    alerts_map = {a.student_id: a for a in db.query(StudentAlert).all()}

    student_rows = []
    for s in students:
        a = alerts_map.get(s.id)
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
            "recommendation": a.recommendation if a else "Aucune note saisie pour le moment."
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


# ================= SERVEUR DE FICHIERS STATIQUES (FRONTEND ÉCOLE) =================
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
