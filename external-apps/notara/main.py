"""
Notara — Cahier d'évaluations scolaires (QCM notés /20).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SEED_PATH = Path(__file__).resolve().parent / "quizzes_seed.json"
DB_URL = f"sqlite:///{DATA_DIR / 'notara.db'}"
DEFAULT_EDUPULSE_URL = os.getenv("DEFAULT_EDUPULSE_URL", "http://host.docker.internal:8101").rstrip("/")
BRAND = os.getenv("NOTARA_BRAND", "Notara")

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Attempt(Base):
    __tablename__ = "attempts"
    id = Column(String(64), primary_key=True)
    quiz_id = Column(String(64), nullable=False, index=True)
    student_email = Column(String(255), nullable=False, index=True)
    student_name = Column(String(255), nullable=False)
    course_name = Column(String(120), nullable=False)
    score_raw = Column(Float, nullable=False)
    score_max = Column(Float, default=20.0)
    correct_count = Column(Integer, nullable=False)
    total_questions = Column(Integer, nullable=False)
    answers_json = Column(Text, nullable=False)
    edupulse_url = Column(String(512), nullable=False)
    sync_status = Column(String(40), default="pending")  # pending | synced | error
    sync_detail = Column(Text, nullable=True)
    xapi_statement_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

app = FastAPI(title=f"{BRAND} — Évaluations", version="1.0.0")


def load_quizzes() -> List[Dict[str, Any]]:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_quiz(quiz_id: str) -> Dict[str, Any]:
    for q in load_quizzes():
        if q["id"] == quiz_id:
            return q
    raise HTTPException(status_code=404, detail="Évaluation introuvable")


def public_quiz(quiz: Dict[str, Any]) -> Dict[str, Any]:
    """Version sans les bonnes réponses (pour l'élève)."""
    return {
        "id": quiz["id"],
        "title": quiz["title"],
        "course_name": quiz["course_name"],
        "duration_min": quiz["duration_min"],
        "description": quiz["description"],
        "questions": [
            {
                "id": q["id"],
                "prompt": q["prompt"],
                "choices": q["choices"],
            }
            for q in quiz["questions"]
        ],
    }


def normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise HTTPException(status_code=400, detail="Adresse de l'établissement obligatoire")
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "http://" + u
    # Depuis un conteneur Docker Windows/Mac, localhost pointe sur le conteneur, pas l'hôte.
    u = u.replace("://localhost", "://host.docker.internal")
    u = u.replace("://127.0.0.1", "://host.docker.internal")
    return u.rstrip("/")


def jwt_role_unverified(token: str) -> Optional[str]:
    """Lit le rôle dans le JWT EduPulse (sans re-vérifier la signature — fait côté LRS)."""
    try:
        import base64

        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part.encode("utf-8")))
        return data.get("role")
    except Exception:
        return None


def score_to_20(correct: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((correct / total) * 20.0, 2)


def build_xapi_statement(
    *,
    full_name: str,
    email: str,
    course_name: str,
    quiz_title: str,
    quiz_id: str,
    score_raw: float,
    edupulse_host: str,
) -> Dict[str, Any]:
    """Format strictement compatible avec le LRS EduPulse (tenant-stack)."""
    return {
        "actor": {
            "name": full_name,
            "mbox": f"mailto:{email}",
        },
        "verb": {
            "id": "http://adlnet.gov/expapi/verbs/completed",
            "display": {"fr-FR": "completed"},
        },
        "object": {
            "id": f"http://notara.local/quizzes/{quiz_id}",
            "definition": {
                "name": {"fr-FR": course_name},
                "description": {"fr-FR": quiz_title},
                "type": "http://adlnet.gov/expapi/activities/assessment",
            },
        },
        "result": {
            "score": {
                "raw": score_raw,
                "min": 0,
                "max": 20,
                "scaled": round(score_raw / 20.0, 4),
            },
            "success": score_raw >= 10,
            "completion": True,
        },
        "context": {
            "platform": BRAND,
            "language": "fr-FR",
            "extensions": {
                "http://notara.local/xapi/source": BRAND,
                "http://notara.local/xapi/quiz_id": quiz_id,
                "http://notara.local/xapi/target_lrs": edupulse_host,
            },
        },
    }


class LoginBody(BaseModel):
    edupulse_url: str = Field(..., description="URL du stack établissement EduPulse")
    email: str
    password: str


class SubmitBody(BaseModel):
    edupulse_url: str
    access_token: str
    student_email: str
    student_name: str
    quiz_id: str
    answers: Dict[str, int]  # question_id -> choice index


@app.get("/api/health")
def health():
    return {"status": "ok", "app": BRAND, "role": "external-assessment"}


@app.get("/api/config")
def config():
    return {
        "brand": BRAND,
        "default_edupulse_url": DEFAULT_EDUPULSE_URL,
    }


@app.get("/api/quizzes")
def list_quizzes():
    quizzes = load_quizzes()
    return [
        {
            "id": q["id"],
            "title": q["title"],
            "course_name": q["course_name"],
            "duration_min": q["duration_min"],
            "description": q["description"],
            "question_count": len(q["questions"]),
        }
        for q in quizzes
    ]


@app.get("/api/quizzes/{quiz_id}")
def quiz_detail(quiz_id: str):
    return public_quiz(get_quiz(quiz_id))


@app.post("/api/auth/login")
async def login(body: LoginBody):
    """Authentifie l'élève auprès de l'établissement (proxy serveur)."""
    base = normalize_base_url(body.edupulse_url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{base}/api/auth/login",
                json={
                    "email": body.email.strip().lower(),
                    "password": body.password.strip(),
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Impossible de joindre l'établissement ({base}). Vérifiez l'adresse. ({exc})",
        ) from exc

    if res.status_code == 401:
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Erreur établissement ({res.status_code})")

    data = res.json()
    user = data.get("user") or {}
    if user.get("role") not in ("ETUDIANT", "ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Compte non reconnu")

    return {
        "access_token": data["access_token"],
        "user": user,
        "edupulse_url": base,
        "message": "Connexion réussie." if user.get("role") == "ETUDIANT" else "Compte connecté.",
    }


@app.post("/api/attempts/submit")
async def submit_attempt(body: SubmitBody):
    """Corrige le test et enregistre la note."""
    if not body.access_token:
        raise HTTPException(status_code=401, detail="Session manquante")

    role = jwt_role_unverified(body.access_token)
    if role and role != "ETUDIANT":
        raise HTTPException(
            status_code=403,
            detail="Seuls les comptes élèves peuvent soumettre une évaluation.",
        )

    quiz = get_quiz(body.quiz_id)
    base = normalize_base_url(body.edupulse_url)

    correct = 0
    details = []
    for q in quiz["questions"]:
        chosen = body.answers.get(q["id"])
        is_ok = chosen is not None and int(chosen) == int(q["answer_index"])
        if is_ok:
            correct += 1
        details.append(
            {
                "question_id": q["id"],
                "chosen": chosen,
                "correct_index": q["answer_index"],
                "is_correct": is_ok,
            }
        )

    total = len(quiz["questions"])
    score_raw = score_to_20(correct, total)
    attempt_id = str(uuid.uuid4())

    statement = build_xapi_statement(
        full_name=body.student_name,
        email=body.student_email.strip().lower(),
        course_name=quiz["course_name"],
        quiz_title=quiz["title"],
        quiz_id=quiz["id"],
        score_raw=score_raw,
        edupulse_host=base,
    )

    sync_status = "pending"
    sync_detail = None
    xapi_id = None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                f"{base}/xapi/statements",
                headers={
                    "Authorization": f"Bearer {body.access_token}",
                    "Content-Type": "application/json",
                    "X-Experience-API-Version": "1.0.3",
                },
                json=statement,
            )
        if res.status_code < 300:
            payload = res.json()
            sync_status = "synced"
            xapi_id = payload.get("id")
            sync_detail = "Note enregistrée."
        else:
            sync_status = "error"
            sync_detail = f"Erreur d'enregistrement ({res.status_code})."
    except httpx.RequestError as exc:
        sync_status = "error"
        sync_detail = f"Échec réseau: {exc}"

    db = SessionLocal()
    try:
        attempt = Attempt(
            id=attempt_id,
            quiz_id=quiz["id"],
            student_email=body.student_email.strip().lower(),
            student_name=body.student_name,
            course_name=quiz["course_name"],
            score_raw=score_raw,
            score_max=20.0,
            correct_count=correct,
            total_questions=total,
            answers_json=json.dumps(details, ensure_ascii=False),
            edupulse_url=base,
            sync_status=sync_status,
            sync_detail=sync_detail,
            xapi_statement_id=xapi_id,
        )
        db.add(attempt)
        db.commit()
    finally:
        db.close()

    if sync_status == "error":
        # La note est calculée/stockée dans Notara, mais l'élève doit savoir que le sync a échoué
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Test corrigé, mais l'enregistrement distant a échoué.",
                "attempt_id": attempt_id,
                "score_raw": score_raw,
                "correct_count": correct,
                "total_questions": total,
                "sync_status": sync_status,
                "sync_detail": sync_detail,
            },
        )

    return {
        "attempt_id": attempt_id,
        "quiz_id": quiz["id"],
        "course_name": quiz["course_name"],
        "score_raw": score_raw,
        "score_max": 20,
        "correct_count": correct,
        "total_questions": total,
        "sync_status": sync_status,
        "sync_detail": sync_detail,
        "xapi_statement_id": xapi_id,
        "details": details,
    }


@app.get("/api/attempts")
def list_attempts(email: Optional[str] = None, limit: int = 50):
    db = SessionLocal()
    try:
        q = db.query(Attempt).order_by(Attempt.created_at.desc())
        if email:
            q = q.filter(Attempt.student_email == email.strip().lower())
        rows = q.limit(max(1, min(limit, 200))).all()
        return [
            {
                "id": r.id,
                "quiz_id": r.quiz_id,
                "student_email": r.student_email,
                "student_name": r.student_name,
                "course_name": r.course_name,
                "score_raw": r.score_raw,
                "correct_count": r.correct_count,
                "total_questions": r.total_questions,
                "sync_status": r.sync_status,
                "sync_detail": r.sync_detail,
                "xapi_statement_id": r.xapi_statement_id,
                "edupulse_url": r.edupulse_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parent / "static" / "index.html")


app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
