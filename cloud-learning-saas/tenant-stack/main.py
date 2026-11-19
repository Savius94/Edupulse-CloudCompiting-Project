import os
import re
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
from typing import List, Optional, Dict, Any, Tuple

import jwt
from fastapi import FastAPI, Depends, HTTPException, Header, Response, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, func, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip().strip('"').strip("'") or "gemini-3.5-flash-lite"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

db_connect_args = {"connect_timeout": 3} if DATABASE_URL.startswith("postgresql") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=db_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

MAINTENANCE_MESSAGE = (
    "Désolé du dérangement, nos services de données sont momentanément en maintenance. "
    "Nous faisons tout notre possible pour régler la panne au plus vite."
)

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
    estimated_future_score = Column(Float, nullable=True)      # Note de performance future estimée (/20 à M+1)
    recommendation = Column(Text, nullable=False, default="")  # Message de coaching IA personnalisé (visible uniquement côté prof)
    ai_prompt = Column(Text, nullable=True)                    # Dernier prompt anonymisé envoyé à l'IA
    ai_raw_response = Column(Text, nullable=True)              # Réponse brute reçue de l'IA (JSON / Texte brut)
    ai_coaching_updated_at = Column(DateTime, nullable=True)
    teacher_appreciation = Column(Text, nullable=True)         # Appréciation personnalisée du prof (affichée côté étudiant)
    appreciation_author = Column(String(255), nullable=True)
    appreciation_updated_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ClassAIReview(Base):
    __tablename__ = "class_ai_reviews"
    id = Column(Integer, primary_key=True, index=True)
    class_name = Column(String(120), unique=True, index=True, nullable=False)  # "ALL" ou nom de la classe
    review_text = Column(Text, nullable=False, default="")
    estimated_class_future_avg = Column(Float, nullable=True)
    ai_prompt = Column(Text, nullable=True)
    ai_raw_response = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class TenantSetting(Base):
    __tablename__ = "tenant_settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)


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

class ClassActionRequest(BaseModel):
    class_name: Optional[str] = "ALL"
    force_regenerate: Optional[bool] = False

class MaintenanceToggleRequest(BaseModel):
    enabled: bool
    message: Optional[str] = None

class XAPIStatementInput(BaseModel):
    actor: Dict[str, Any]
    verb: Dict[str, Any]
    object: Dict[str, Any]
    result: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None


DEFAULT_ADMIN_MAINTENANCE_MESSAGE = (
    "Opération de maintenance en cours sur cet établissement. "
    "L'accès est temporairement suspendu pendant la mise à jour et sera rétabli d'ici quelques instants."
)


def get_maintenance_state(db: Optional[Session] = None) -> Tuple[bool, str]:
    """Lit l'état du mode maintenance administratif depuis tenant_settings (partagé entre réplicas)."""
    env_flag = os.getenv("MAINTENANCE_MODE", "0").strip() in ("1", "true", "True", "yes")
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        rows = db.query(TenantSetting).filter(TenantSetting.key.in_(["maintenance_mode", "maintenance_message"])).all()
        cfg = {r.key: (r.value or "") for r in rows}
        if "maintenance_mode" in cfg:
            is_enabled = cfg["maintenance_mode"] == "1"
        else:
            is_enabled = env_flag
        msg = cfg.get("maintenance_message") or DEFAULT_ADMIN_MAINTENANCE_MESSAGE
        return is_enabled, msg
    except Exception:
        return env_flag, DEFAULT_ADMIN_MAINTENANCE_MESSAGE
    finally:
        if close_db:
            db.close()


def set_maintenance_state(db: Session, enabled: bool, message: Optional[str] = None):
    TenantSetting.__table__.create(bind=engine, checkfirst=True)
    updates = {
        "maintenance_mode": "1" if enabled else "0",
        "maintenance_message": (message or DEFAULT_ADMIN_MAINTENANCE_MESSAGE).strip(),
    }
    for k, v in updates.items():
        row = db.query(TenantSetting).filter(TenantSetting.key == k).first()
        if not row:
            db.add(TenantSetting(key=k, value=v))
        else:
            row.value = v
    db.commit()


# ================= INITIALISATION DE L'APPLICATION =================
app = FastAPI(title=f"EduPulse Stack - {INSTITUTION_NAME}")


@app.middleware("http")
async def maintenance_guard_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/xapi/"):
        if path not in ("/api/health", "/api/info", "/api/internal/maintenance"):
            is_maint, maint_msg = get_maintenance_state()
            if is_maint:
                return JSONResponse(
                    status_code=503,
                    content={
                        "maintenance": True,
                        "detail": maint_msg,
                    },
                )
    return await call_next(request)


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_maintenance_handler(request: Request, exc: SQLAlchemyError):
    """Renvoie une erreur 503 propre lorsque la base de données est temporairement injoignable."""
    return JSONResponse(
        status_code=503,
        content={
            "maintenance": True,
            "detail": MAINTENANCE_MESSAGE,
        },
    )


@app.get("/api/health", include_in_schema=False)
def tenant_healthcheck():
    return {"status": "ok", "replica": socket.gethostname()}

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


def compute_default_estimated_future_score(avg_score: Optional[float], recent_trend: float) -> Optional[float]:
    """Calcule une estimation mathématique sur 20 de la performance future (M+1) à partir de la moyenne et de la tendance."""
    if avg_score is None:
        return None
    # Projection pondérée avec amortissement réaliste et léger rebond pédagogique si accompagnement
    delta = recent_trend * 0.55
    if avg_score < 10.0 and recent_trend >= -1.0:
        delta += 0.35
    val = max(0.0, min(20.0, avg_score + delta))
    return round(val, 2)


def compute_student_future_curve(
    records: List[XAPIStatementRecord],
    avg_score: Optional[float],
    recent_trend: float,
    estimated_future_score: Optional[float],
) -> List[Dict[str, Any]]:
    """
    Génère la courbe d'évolution estimée sur le mois prochain (30 jours : J+7, J+15, J+22, J+30)
    pour un étudiant à partir de sa dernière note, de sa dynamique et de sa note future estimée.
    """
    if not records or avg_score is None:
        return []

    last_rec = records[-1]
    base_dt = last_rec.timestamp
    start_score = last_rec.score_raw
    target_score = (
        estimated_future_score
        if estimated_future_score is not None
        else compute_default_estimated_future_score(avg_score, recent_trend)
    )
    if target_score is None:
        target_score = avg_score

    # Points d'ancrage : point 0 = dernière note réelle, puis +7j, +15j, +22j, +30j
    milestones = [
        (0, "Dernière note", 0.0),
        (7, "J+7", 0.28),
        (15, "J+15", 0.55),
        (22, "J+22", 0.78),
        (30, "J+30 (M+1)", 1.0),
    ]
    curve: List[Dict[str, Any]] = []
    for days_offset, label, progress in milestones:
        dt = base_dt + timedelta(days=days_offset)
        # Interpolation progressive avec légère courbure d'apprentissage
        interp = start_score + (target_score - start_score) * (progress ** 0.88 if progress > 0 else 0.0)
        score_val = round(max(0.0, min(20.0, interp)), 2)
        curve.append({
            "days_offset": days_offset,
            "label": label,
            "date": dt.strftime("%d/%m/%Y"),
            "iso_timestamp": dt.isoformat(),
            "score_raw": score_val,
        })
    return curve


def deanonymize_text(text: str, code_to_name: Dict[str, str]) -> str:
    """
    Remplace dans le texte reçu de l'IA toutes les occurrences des jetons anonymes (ex: ETUD-01, ETUD-1)
    par le vrai nom complet de l'étudiant correspondant.
    Ainsi, les données envoyées sur le réseau sont 100% anonymes mais l'affichage côté enseignant est personnalisé.
    """
    if not text or not code_to_name:
        return text or ""
    result = text
    # Trier par code le plus long d'abord (ex: ETUD-10 avant ETUD-1)
    sorted_items = sorted(code_to_name.items(), key=lambda kv: len(kv[0]), reverse=True)
    for code, full_name in sorted_items:
        # Supporte ETUD-01, [ETUD-01], (ETUD-01) et aussi la variante sans zéro initial ex: ETUD-1
        pattern = re.compile(r"\b" + re.escape(code) + r"\b", flags=re.IGNORECASE)
        result = pattern.sub(full_name, result)
        num_match = re.match(r"^ETUD-0*(\d+)$", code, flags=re.IGNORECASE)
        if num_match:
            num_str = num_match.group(1)
            alt_pattern = re.compile(rf"\bETUD-0*{num_str}\b", flags=re.IGNORECASE)
            result = alt_pattern.sub(full_name, result)
    return result


def extract_json_from_llm(raw_text: str) -> Any:
    """Extrait et parse un objet ou tableau JSON depuis la réponse texte d'un LLM (même entouré de ```json)."""
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Recherche du premier bloc [...] ou {...}
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        s_idx = cleaned.find(start_char)
        e_idx = cleaned.rfind(end_char)
        if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
            try:
                return json.loads(cleaned[s_idx : e_idx + 1])
            except Exception:
                continue
    return None


def call_gemini_generate(prompt: str, max_tokens: int = 2048) -> Tuple[str, str, str]:
    """
    Exécute un appel HTTP vers l'API Gemini si GEMINI_API_KEY est configurée.
    Retourne (extracted_text, raw_response_json_str, source).
    """
    if not GEMINI_API_KEY:
        return "", "", "simulation"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.55,
            "maxOutputTokens": max_tokens,
        },
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            raw_decoded = resp.read().decode("utf-8")
            resp_data = json.loads(raw_decoded)
            try:
                raw_response_str = json.dumps(resp_data, indent=2, ensure_ascii=False)
            except Exception:
                raw_response_str = raw_decoded

            candidates = resp_data.get("candidates") or []
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text_parts = [p.get("text", "") for p in parts if not p.get("thought", False)]
                if not text_parts:
                    text_parts = [p.get("text", "") for p in parts]
                extracted = "".join(text_parts).strip()
                if extracted:
                    return extracted, raw_response_str, "api_ia"
            return "", raw_response_str, "simulation (réponse vide)"
    except urllib.error.HTTPError as http_err:
        try:
            err_body = http_err.read().decode("utf-8")
            raw_err = json.dumps({
                "status_code": http_err.code,
                "reason": http_err.reason,
                "model_used": GEMINI_MODEL,
                "error_body": json.loads(err_body) if err_body.startswith("{") else err_body,
            }, indent=2, ensure_ascii=False)
        except Exception:
            raw_err = f"HTTP Error {http_err.code} ({GEMINI_MODEL}): {http_err.reason}"
        return "", raw_err, f"simulation (erreur HTTP {http_err.code})"
    except Exception as exc:
        raw_err = json.dumps({
            "error": str(exc),
            "type": type(exc).__name__,
            "model_used": GEMINI_MODEL,
        }, indent=2, ensure_ascii=False)
        return "", raw_err, f"simulation (secours: {type(exc).__name__})"


def build_compact_student_line(
    code: str,
    student: User,
    alert: StudentAlert,
    records: List[XAPIStatementRecord],
) -> str:
    """
    Construit une ligne ultra-compacte et anonymisée (ETUD-XX) résumant l'élève
    afin de minimiser la taille du prompt et la consommation de tokens.
    """
    course_scores: Dict[str, List[float]] = {}
    for r in records:
        course_scores.setdefault(r.course_name, []).append(r.score_raw)
    course_avgs_str = ", ".join(
        f"{c}={round(sum(v)/len(v), 1)}" for c, v in sorted(course_scores.items())
    )
    recent_notes_str = ", ".join(str(r.score_raw) for r in records[-4:])
    student_class = getattr(student, "class_name", "Générale") or "Générale"
    return (
        f"{code} | Classe:{student_class} | Moy:{alert.average_score}/20 | "
        f"Tend:{alert.recent_trend:+.2f} | Risque:{alert.risk_level} | "
        f"Fragile:{alert.weakest_course} | MoyMatières:[{course_avgs_str}] | "
        f"DernièresNotes:[{recent_notes_str}]"
    )


def build_fallback_student_coaching(
    code: str,
    student: User,
    alert: StudentAlert,
    records: List[XAPIStatementRecord],
) -> Tuple[str, float]:
    """
    Génère en local (fallback ou simulation) le texte de conseil avec le jeton `code` (ex: ETUD-01)
    ainsi que la note future estimée sur 20. Le texte sera ensuite désanonymisé par `deanonymize_text`.
    """
    course_scores: Dict[str, List[float]] = {}
    for r in records:
        course_scores.setdefault(r.course_name, []).append(r.score_raw)
    course_avgs = {c: round(sum(v) / len(v), 2) for c, v in course_scores.items()}
    weakest_course = alert.weakest_course or min(course_avgs, key=course_avgs.get)
    weakest_avg = course_avgs.get(weakest_course, alert.average_score)
    best_course = max(course_avgs, key=course_avgs.get)
    best_avg = course_avgs.get(best_course, alert.average_score)
    last_rec = records[-1]
    est_score = compute_default_estimated_future_score(alert.average_score, alert.recent_trend) or alert.average_score

    if alert.risk_level == "CRITIQUE":
        advice_anon = (
            f"{code} présente un décrochage préoccupant (moyenne {alert.average_score}/20, évolution {alert.recent_trend:+.2f} pts, "
            f"dernière note à {last_rec.score_raw}/20 en {last_rec.course_name}) avec une lacune marquée en {weakest_course} ({weakest_avg}/20). "
            f"Actions pour accompagner {code} : (1) organiser un point individuel rapide en s'appuyant sur {best_course} ({best_avg}/20) ; "
            f"(2) mettre en place un tutorat ciblé en {weakest_course} (projection estimée à M+1 : {est_score}/20)."
        )
    elif alert.risk_level == "VIGILANCE":
        advice_anon = (
            f"{code} se situe en zone de vigilance (moyenne {alert.average_score}/20, tendance {alert.recent_trend:+.2f} pts) "
            f"avec un écart entre {best_course} ({best_avg}/20) et {weakest_course} ({weakest_avg}/20). "
            f"Actions pour {code} : (1) attribuer des exercices progressifs en {weakest_course} avant le prochain contrôle ; "
            f"(2) vérifier la compréhension en fin de TP pour viser {est_score}/20 le mois prochain."
        )
    else:
        advice_anon = (
            f"{code} maintient une progression solide ({alert.average_score}/20 sur {alert.statements_count} notes, tendance {alert.recent_trend:+.2f} pts), "
            f"notamment en {best_course} ({best_avg}/20). "
            f"Actions pour {code} : (1) proposer un défi d'approfondissement en {best_course} ; "
            f"(2) consolider {weakest_course} ({weakest_avg}/20) ou impliquer {code} en binôme d'entraide (projection M+1 : {est_score}/20)."
        )
    return advice_anon, est_score


def generate_student_ai_coaching(db: Session, student: User) -> Dict[str, Any]:
    """
    Génération UNITAIRE (écrase uniquement l'élève concerné) :
    Envoie un prompt compact anonymisé avec `ETUD-01`, récupère le conseil + la note future estimée,
    et remplace `ETUD-01` par le vrai nom de l'élève à la réception.
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
            "estimated_future_score": None,
            "prompt_used": "",
            "raw_response": "",
        }

    code = "ETUD-01"
    code_to_name = {code: student.full_name}
    compact_line = build_compact_student_line(code, student, alert, records)

    prompt = (
        "Tu es conseiller pédagogique IA. Analyse l'élève anonymisé ci-dessous et réponds UNIQUEMENT avec un objet JSON strict :\n"
        '{"code":"ETUD-01","estimated_score":12.5,"advice":"2 à 3 phrases de coaching concret pour le professeur en citant explicitement ETUD-01 (sans formule de politesse)."}\n'
        f"Données : {compact_line}"
    )

    extracted_text, raw_response_str, source = call_gemini_generate(prompt, max_tokens=600)
    coaching_anon = ""
    est_score: Optional[float] = None

    if extracted_text:
        parsed = extract_json_from_llm(extracted_text)
        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        if isinstance(parsed, dict):
            coaching_anon = str(parsed.get("advice") or parsed.get("coaching") or "").strip()
            raw_est = parsed.get("estimated_score")
            if raw_est is not None:
                try:
                    est_score = round(max(0.0, min(20.0, float(raw_est))), 2)
                except Exception:
                    est_score = None
        if not coaching_anon:
            coaching_anon = extracted_text

    if not coaching_anon:
        coaching_anon, fallback_est = build_fallback_student_coaching(code, student, alert, records)
        if est_score is None:
            est_score = fallback_est
        if not raw_response_str:
            raw_response_str = json.dumps({
                "statut": "simulation_locale_active",
                "cle_api_presente": bool(GEMINI_API_KEY),
                "modele_configure": GEMINI_MODEL,
                "code_anonyme": code,
                "estimated_score": est_score,
                "advice_anonyme": coaching_anon,
            }, indent=2, ensure_ascii=False)

    if est_score is None:
        est_score = compute_default_estimated_future_score(alert.average_score, alert.recent_trend)

    # Désanonymisation à la réception : remplace ETUD-01 par le nom réel de l'étudiant
    personalized_coaching = deanonymize_text(coaching_anon, code_to_name)
    if student.full_name not in personalized_coaching:
        personalized_coaching = f"[{student.full_name}] {personalized_coaching}"

    # Overwrite des données de l'élève concerné
    alert.recommendation = personalized_coaching
    alert.estimated_future_score = est_score
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
        "ai_coaching": personalized_coaching,
        "estimated_future_score": est_score,
        "prompt_used": prompt,
        "raw_response": raw_response_str,
        "updated_at": alert.ai_coaching_updated_at.strftime("%d/%m/%Y à %H:%M"),
    }


def generate_batch_ai_coaching(db: Session, students: List[User]) -> Dict[str, Any]:
    """
    Génération GROUPÉE en UNE SEULE requête IA (écrase les anciens conseils de tous les élèves ciblés) :
    - Construit un unique prompt compact listant tous les élèves sous forme `ETUD-01`, `ETUD-02`...
    - Récupère dans une seule réponse JSON le conseil et la note future estimée pour chaque `ETUD-XX`
    - Répartit les résultats par élève et remplace `ETUD-XX` par le vrai nom de chaque étudiant à la réception.
    """
    eligible: List[Tuple[str, User, StudentAlert, List[XAPIStatementRecord]]] = []
    code_to_name: Dict[str, str] = {}
    compact_lines: List[str] = []

    idx = 1
    for s in students:
        recompute_student_analytics(db, s.id)
        records = (
            db.query(XAPIStatementRecord)
            .filter(XAPIStatementRecord.student_id == s.id)
            .order_by(XAPIStatementRecord.timestamp.asc())
            .all()
        )
        alert = db.query(StudentAlert).filter(StudentAlert.student_id == s.id).first()
        if not records or not alert:
            continue
        code = f"ETUD-{idx:02d}"
        idx += 1
        code_to_name[code] = s.full_name
        eligible.append((code, s, alert, records))
        compact_lines.append(build_compact_student_line(code, s, alert, records))

    if not eligible:
        return {
            "generated_count": 0,
            "total_students": len(students),
            "model": GEMINI_MODEL,
            "results": [],
        }

    prompt = (
        "Tu es conseiller pédagogique IA. Analyse chaque élève anonymisé ci-dessous.\n"
        "Réponds UNIQUEMENT avec un tableau JSON strict contenant 1 objet par élève :\n"
        '[{"code":"ETUD-01","estimated_score":12.5,"advice":"2 phrases de coaching concret pour le prof en citant explicitement ETUD-01."}, ...]\n'
        "Élèves :\n" + "\n".join(compact_lines)
    )

    extracted_text, raw_response_str, source = call_gemini_generate(prompt, max_tokens=2048)
    parsed_list = extract_json_from_llm(extracted_text) if extracted_text else None
    parsed_by_code: Dict[str, Dict[str, Any]] = {}
    if isinstance(parsed_list, dict):
        if "students" in parsed_list and isinstance(parsed_list["students"], list):
            parsed_list = parsed_list["students"]
        elif "code" in parsed_list:
            parsed_list = [parsed_list]
    if isinstance(parsed_list, list):
        for item in parsed_list:
            if isinstance(item, dict) and item.get("code"):
                norm_code = str(item["code"]).strip().upper()
                parsed_by_code[norm_code] = item

    now_dt = datetime.utcnow()
    results = []
    sim_items_for_raw = []

    for code, s, alert, records in eligible:
        item = parsed_by_code.get(code.upper())
        # Support variante sans zéro (ETUD-1 vs ETUD-01)
        if not item:
            short_code = re.sub(r"^ETUD-0*(\d+)$", r"ETUD-\1", code.upper())
            for k, v in parsed_by_code.items():
                if re.sub(r"^ETUD-0*(\d+)$", r"ETUD-\1", k) == short_code:
                    item = v
                    break

        coaching_anon = ""
        est_score: Optional[float] = None
        if item:
            coaching_anon = str(item.get("advice") or item.get("coaching") or "").strip()
            raw_est = item.get("estimated_score")
            if raw_est is not None:
                try:
                    est_score = round(max(0.0, min(20.0, float(raw_est))), 2)
                except Exception:
                    est_score = None

        if not coaching_anon:
            coaching_anon, fallback_est = build_fallback_student_coaching(code, s, alert, records)
            if est_score is None:
                est_score = fallback_est

        if est_score is None:
            est_score = compute_default_estimated_future_score(alert.average_score, alert.recent_trend)

        sim_items_for_raw.append({
            "code": code,
            "estimated_score": est_score,
            "advice_anonyme": coaching_anon,
        })

        # Désanonymisation : remplace ETUD-XX par le nom réel de l'étudiant
        personalized_coaching = deanonymize_text(coaching_anon, code_to_name)
        if s.full_name not in personalized_coaching:
            personalized_coaching = f"[{s.full_name}] {personalized_coaching}"

        per_student_raw = raw_response_str or json.dumps({
            "statut": "simulation_lot_active",
            "cle_api_presente": bool(GEMINI_API_KEY),
            "modele_configure": GEMINI_MODEL,
            "code_attribue": code,
            "estimated_score": est_score,
            "advice_anonyme": coaching_anon,
        }, indent=2, ensure_ascii=False)

        # Overwrite systématique de tous les élèves du lot
        alert.recommendation = personalized_coaching
        alert.estimated_future_score = est_score
        alert.ai_prompt = prompt
        alert.ai_raw_response = per_student_raw
        alert.ai_coaching_updated_at = now_dt
        alert.updated_at = now_dt

        results.append({
            "student_id": s.id,
            "full_name": s.full_name,
            "code_anonyme": code,
            "generated": True,
            "source": source,
            "ai_coaching": personalized_coaching,
            "estimated_future_score": est_score,
            "updated_at": now_dt.strftime("%d/%m/%Y à %H:%M"),
        })

    db.commit()
    return {
        "generated_count": len(results),
        "total_students": len(students),
        "model": GEMINI_MODEL,
        "source": source,
        "prompt_used": prompt,
        "results": results,
    }


def generate_class_ai_review(db: Session, class_name: str = "ALL") -> Dict[str, Any]:
    """
    Génère un Bilan IA global de la performance d'une classe (ou de toute la promotion si class_name == 'ALL').
    Envoie un prompt compact anonymisé (ETUD-01, ETUD-02...) et désanonymise les noms d'élèves à la réception.
    """
    norm_class = (class_name or "ALL").strip() or "ALL"
    stu_q = db.query(User).filter(User.role == "ETUDIANT")
    if norm_class != "ALL":
        stu_q = stu_q.filter(User.class_name == norm_class)
    students = stu_q.order_by(User.full_name.asc()).all()

    code_to_name: Dict[str, str] = {}
    compact_lines: List[str] = []
    avgs: List[float] = []
    est_scores: List[float] = []
    course_scores: Dict[str, List[float]] = {}
    risk_counts = {"CRITIQUE": 0, "VIGILANCE": 0, "STABLE": 0}

    idx = 1
    for s in students:
        recompute_student_analytics(db, s.id)
        records = (
            db.query(XAPIStatementRecord)
            .filter(XAPIStatementRecord.student_id == s.id)
            .order_by(XAPIStatementRecord.timestamp.asc())
            .all()
        )
        alert = db.query(StudentAlert).filter(StudentAlert.student_id == s.id).first()
        if not records or not alert:
            continue
        code = f"ETUD-{idx:02d}"
        idx += 1
        code_to_name[code] = s.full_name
        compact_lines.append(build_compact_student_line(code, s, alert, records))
        avgs.append(alert.average_score)
        est_val = (
            alert.estimated_future_score
            if alert.estimated_future_score is not None
            else compute_default_estimated_future_score(alert.average_score, alert.recent_trend)
        )
        if est_val is not None:
            est_scores.append(est_val)
        if alert.risk_level in risk_counts:
            risk_counts[alert.risk_level] += 1
        for r in records:
            course_scores.setdefault(r.course_name, []).append(r.score_raw)

    class_label = "Toutes les promotions" if norm_class == "ALL" else norm_class
    if not avgs:
        return {
            "class_name": norm_class,
            "class_label": class_label,
            "generated": False,
            "review_text": "Aucune donnée d'évaluation disponible pour cette classe.",
            "estimated_class_future_avg": None,
            "prompt_used": "",
            "raw_response": "",
            "updated_at": None,
        }

    current_class_avg = round(sum(avgs) / len(avgs), 2)
    default_future_avg = round(sum(est_scores) / len(est_scores), 2) if est_scores else current_class_avg
    course_avgs = {c: round(sum(v) / len(v), 2) for c, v in sorted(course_scores.items())}
    course_summary = ", ".join(f"{c}:{v}/20" for c, v in course_avgs.items())

    prompt = (
        f"Tu es conseiller pédagogique IA. Analyse la performance globale de la classe « {class_label} » ({len(avgs)} élèves évalués).\n"
        f"Stats classe : Moyenne={current_class_avg}/20 | Critique={risk_counts['CRITIQUE']} | Vigilance={risk_counts['VIGILANCE']} | Stable={risk_counts['STABLE']} | Matières=[{course_summary}]\n"
        "Profils anonymisés :\n" + "\n".join(compact_lines) + "\n\n"
        "Réponds UNIQUEMENT en JSON strict :\n"
        '{"estimated_class_average":12.4,"review":"Bilan structuré en 3-4 phrases : dynamique globale de la classe, matières fortes/fragiles, et actions prioritaires pour le mois prochain en citant les codes ETUD-XX des profils clés."}'
    )

    extracted_text, raw_response_str, source = call_gemini_generate(prompt, max_tokens=1024)
    review_anon = ""
    est_class_avg: Optional[float] = None

    if extracted_text:
        parsed = extract_json_from_llm(extracted_text)
        if isinstance(parsed, dict):
            review_anon = str(parsed.get("review") or parsed.get("summary") or "").strip()
            raw_est = parsed.get("estimated_class_average")
            if raw_est is not None:
                try:
                    est_class_avg = round(max(0.0, min(20.0, float(raw_est))), 2)
                except Exception:
                    est_class_avg = None
        if not review_anon:
            review_anon = extracted_text

    if not review_anon:
        weakest_c = min(course_avgs, key=course_avgs.get) if course_avgs else "-"
        strongest_c = max(course_avgs, key=course_avgs.get) if course_avgs else "-"
        crit_codes = [c for c, _, a, _ in [(f"ETUD-{i+1:02d}", st, db.query(StudentAlert).filter(StudentAlert.student_id == st.id).first(), []) for i, st in enumerate(students)] if a and a.risk_level == "CRITIQUE"]
        top_codes = [c for c, _, a, _ in [(f"ETUD-{i+1:02d}", st, db.query(StudentAlert).filter(StudentAlert.student_id == st.id).first(), []) for i, st in enumerate(students)] if a and a.risk_level == "STABLE" and a.average_score >= 15.0]
        crit_mention = f" (attention prioritaire sur {', '.join(crit_codes[:3])})" if crit_codes else ""
        top_mention = f" en s'appuyant sur des éléments moteurs comme {', '.join(top_codes[:2])}" if top_codes else ""

        review_anon = (
            f"La promotion « {class_label} » affiche une moyenne globale de {current_class_avg}/20 "
            f"avec {risk_counts['STABLE']} profil(s) stable(s), {risk_counts['VIGILANCE']} en vigilance et {risk_counts['CRITIQUE']} en situation critique{crit_mention}. "
            f"Les résultats sont particulièrement solides en {strongest_c} ({course_avgs.get(strongest_c, current_class_avg)}/20), "
            f"tandis que le module {weakest_c} ({course_avgs.get(weakest_c, current_class_avg)}/20) concentre les principales fragilités méthodologiques. "
            f"Plan d'action recommandé pour le mois prochain : organiser une séance de remédiation ciblée en {weakest_c} et déployer un tutorat par les pairs{top_mention} "
            f"afin de porter la moyenne de classe vers l'estimation de {default_future_avg}/20 à M+1."
        )
        est_class_avg = default_future_avg
        if not raw_response_str:
            raw_response_str = json.dumps({
                "statut": "simulation_bilan_classe_active",
                "classe": class_label,
                "estimated_class_average": est_class_avg,
                "review_anonyme": review_anon,
            }, indent=2, ensure_ascii=False)

    if est_class_avg is None:
        est_class_avg = default_future_avg

    personalized_review = deanonymize_text(review_anon, code_to_name)
    now_dt = datetime.utcnow()

    review_row = db.query(ClassAIReview).filter(ClassAIReview.class_name == norm_class).first()
    if not review_row:
        review_row = ClassAIReview(
            class_name=norm_class,
            review_text=personalized_review,
            estimated_class_future_avg=est_class_avg,
            ai_prompt=prompt,
            ai_raw_response=raw_response_str,
            updated_at=now_dt,
        )
        db.add(review_row)
    else:
        review_row.review_text = personalized_review
        review_row.estimated_class_future_avg = est_class_avg
        review_row.ai_prompt = prompt
        review_row.ai_raw_response = raw_response_str
        review_row.updated_at = now_dt
    db.commit()

    return {
        "class_name": norm_class,
        "class_label": class_label,
        "generated": True,
        "source": source,
        "review_text": personalized_review,
        "estimated_class_future_avg": est_class_avg,
        "current_class_avg": current_class_avg,
        "prompt_used": prompt,
        "raw_response": raw_response_str,
        "updated_at": now_dt.strftime("%d/%m/%Y à %H:%M"),
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
                    conn.execute(text("ALTER TABLE student_alerts ADD COLUMN IF NOT EXISTS estimated_future_score FLOAT;"))
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
    db_available = True
    total_users = 0
    total_statements = 0
    is_maint, maint_msg = False, ""
    try:
        total_users = db.query(func.count(User.id)).scalar() or 0
        total_statements = db.query(func.count(XAPIStatementRecord.id)).scalar() or 0
        is_maint, admin_maint_msg = get_maintenance_state(db)
        if is_maint:
            maint_msg = admin_maint_msg
    except Exception:
        db_available = False
        maint_msg = MAINTENANCE_MESSAGE

    return {
        "tenant_slug": TENANT_SLUG,
        "institution_name": INSTITUTION_NAME,
        "is_demo": is_demo_institution(INSTITUTION_NAME),
        "max_users": MAX_USERS,
        "current_users": total_users,
        "remaining_slots": max(0, MAX_USERS - total_users),
        "total_xapi_statements": total_statements,
        "admin_email": ADMIN_EMAIL,
        "replica_id": socket.gethostname(),
        "db_available": db_available,
        "maintenance_mode": is_maint,
        "maintenance_message": maint_msg,
    }


@app.post("/api/internal/maintenance", include_in_schema=False)
def internal_toggle_maintenance(payload: MaintenanceToggleRequest, db: Session = Depends(get_db)):
    """Endpoint interne piloté par le Control Plane (portal-api) pour activer/désactiver la maintenance instantanément."""
    set_maintenance_state(db, payload.enabled, payload.message)
    is_maint, maint_msg = get_maintenance_state(db)
    return {
        "tenant_slug": TENANT_SLUG,
        "maintenance_mode": is_maint,
        "maintenance_message": maint_msg,
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
        est_future = a.estimated_future_score if (a and a.estimated_future_score is not None) else None
        default_est = compute_default_estimated_future_score(a.average_score, a.recent_trend) if a else None
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
            "estimated_future_score": est_future,
            "default_estimated_future_score": default_est,
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
    est_vals = [
        (r["estimated_future_score"] if r["estimated_future_score"] is not None else r["default_estimated_future_score"])
        for r in student_rows
        if (r["estimated_future_score"] is not None or r["default_estimated_future_score"] is not None)
    ]
    est_class_avg = round(sum(est_vals) / len(est_vals), 2) if est_vals else 0.0

    # Bilans IA de classe existants
    class_reviews_map = {}
    try:
        for cr in db.query(ClassAIReview).all():
            class_reviews_map[cr.class_name] = {
                "class_name": cr.class_name,
                "review_text": cr.review_text,
                "estimated_class_future_avg": cr.estimated_class_future_avg,
                "prompt_used": cr.ai_prompt,
                "raw_response": cr.ai_raw_response,
                "updated_at": cr.updated_at.strftime("%d/%m/%Y à %H:%M") if cr.updated_at else None,
            }
    except Exception:
        pass

    available_classes = sorted(list({r["class_name"] for r in student_rows if r["class_name"]}))

    return {
        "ai_info": {
            "active": True,
            "label": "Assistant Pédagogique IA activé",
        },
        "classes": available_classes,
        "class_reviews": class_reviews_map,
        "summary": {
            "total_students": len(students),
            "class_average": class_avg,
            "estimated_future_class_average": est_class_avg,
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
def trigger_ai_coaching_all_students(
    req: Optional[ClassActionRequest] = None,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Déclenchement manuel global (en UNE SEULE requête groupée compacte) :
    Génère et écrase les recommandations IA + notes futures estimées pour tous les élèves
    (ou ceux de la classe choisie), avec anonymisation ETUD-XX à l'envoi et personnalisation à la réception.
    """
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    stu_q = db.query(User).filter(User.role == "ETUDIANT")
    if req and req.class_name and req.class_name != "ALL":
        stu_q = stu_q.filter(User.class_name == req.class_name)
    students = stu_q.order_by(User.full_name.asc()).all()
    return generate_batch_ai_coaching(db, students)


@app.post("/api/teacher/ai-class-review")
def trigger_ai_class_review(
    req: ClassActionRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Génère ou actualise la Review IA de la performance globale de la classe sélectionnée
    (ou de l'ensemble des promotions si class_name == 'ALL').
    """
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    target_class = (req.class_name or "ALL").strip() or "ALL"
    return generate_class_ai_review(db, target_class)


@app.post("/api/teacher/class-report")
def build_complete_class_report(
    req: ClassActionRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Prépare toutes les données pour le Rapport PDF Complet d'une classe :
    - Si des élèves de la classe n'ont pas encore de conseil IA ou de note future estimée,
      lance automatiquement la génération IA groupée pour ces élèves.
    - Si le bilan IA global de la classe n'est pas encore généré (ou si force_regenerate=True),
      le génère automatiquement.
    - Retourne l'intégralité des métriques, bilans par matière, projections à 30 jours,
      commentaires IA et appréciations enseignant pour construire le PDF.
    """
    if current.role not in ("ENSEIGNANT", "ADMIN"):
        raise HTTPException(status_code=403, detail="Accès réservé au corps enseignant")

    target_class = (req.class_name or "ALL").strip() or "ALL"
    stu_q = db.query(User).filter(User.role == "ETUDIANT")
    if target_class != "ALL":
        stu_q = stu_q.filter(User.class_name == target_class)
    students = stu_q.order_by(User.full_name.asc()).all()

    if not students:
        raise HTTPException(status_code=404, detail="Aucun étudiant trouvé dans cette classe.")

    # 1. Recalculer les stats de base et identifier les élèves sans conseil IA ou sans note future estimée
    missing_students: List[User] = []
    for s in students:
        recompute_student_analytics(db, s.id)
        a = db.query(StudentAlert).filter(StudentAlert.student_id == s.id).first()
        has_grades = db.query(func.count(XAPIStatementRecord.id)).filter(XAPIStatementRecord.student_id == s.id).scalar() or 0
        if has_grades > 0:
            ai_txt = (a.recommendation or "").strip() if a else ""
            if a and ai_txt and not a.ai_coaching_updated_at and ("Solution proposée :" in ai_txt):
                ai_txt = ""
            if req.force_regenerate or not a or not ai_txt or a.estimated_future_score is None:
                missing_students.append(s)

    if missing_students:
        generate_batch_ai_coaching(db, missing_students)

    # 2. Vérifier / générer la Review IA globale de la classe
    class_review_row = db.query(ClassAIReview).filter(ClassAIReview.class_name == target_class).first()
    if req.force_regenerate or not class_review_row or not (class_review_row.review_text or "").strip():
        class_review_data = generate_class_ai_review(db, target_class)
    else:
        class_review_data = {
            "class_name": class_review_row.class_name,
            "class_label": "Toutes les promotions" if target_class == "ALL" else target_class,
            "review_text": class_review_row.review_text,
            "estimated_class_future_avg": class_review_row.estimated_class_future_avg,
            "updated_at": class_review_row.updated_at.strftime("%d/%m/%Y à %H:%M") if class_review_row.updated_at else None,
        }

    # 3. Assembler toutes les données détaillées de la classe pour le PDF
    student_ids = [s.id for s in students]
    alerts_map = {
        a.student_id: a
        for a in db.query(StudentAlert).filter(StudentAlert.student_id.in_(student_ids)).all()
    }
    all_records = (
        db.query(XAPIStatementRecord)
        .filter(XAPIStatementRecord.student_id.in_(student_ids))
        .order_by(XAPIStatementRecord.timestamp.asc())
        .all()
    )
    records_by_student: Dict[int, List[XAPIStatementRecord]] = {}
    course_scores: Dict[str, List[float]] = {}
    all_raw_scores: List[float] = []

    for r in all_records:
        records_by_student.setdefault(r.student_id, []).append(r)
        course_scores.setdefault(r.course_name, []).append(r.score_raw)
        all_raw_scores.append(r.score_raw)

    course_stats = []
    for c_name, sc_list in sorted(course_scores.items()):
        c_avg = round(sum(sc_list) / len(sc_list), 2)
        c_pass = round((sum(1 for x in sc_list if x >= 10.0) / len(sc_list)) * 100, 1)
        course_stats.append({
            "course_name": c_name,
            "average": c_avg,
            "pass_rate": c_pass,
            "min": min(sc_list),
            "max": max(sc_list),
            "count": len(sc_list),
        })

    student_reports = []
    avgs_list: List[float] = []
    est_list: List[float] = []
    for s in students:
        a = alerts_map.get(s.id)
        s_recs = records_by_student.get(s.id, [])
        avg_val = a.average_score if a else None
        trend_val = a.recent_trend if a else 0.0
        est_val = (
            a.estimated_future_score
            if (a and a.estimated_future_score is not None)
            else compute_default_estimated_future_score(avg_val, trend_val)
        )
        if avg_val is not None:
            avgs_list.append(avg_val)
        if est_val is not None:
            est_list.append(est_val)

        stu_course_map: Dict[str, List[float]] = {}
        for r in s_recs:
            stu_course_map.setdefault(r.course_name, []).append(r.score_raw)
        stu_course_avgs = {c: round(sum(v) / len(v), 2) for c, v in sorted(stu_course_map.items())}
        future_curve = compute_student_future_curve(s_recs, avg_val, trend_val, est_val)

        student_reports.append({
            "student_id": s.id,
            "full_name": s.full_name,
            "email": s.email,
            "class_name": getattr(s, "class_name", "Générale") or "Générale",
            "average_score": avg_val,
            "recent_trend": trend_val,
            "statements_count": len(s_recs),
            "risk_level": a.risk_level if a else "SANS_DONNEES",
            "weakest_course": a.weakest_course if a else "-",
            "estimated_future_score": est_val,
            "ai_coaching": (a.recommendation if a and a.recommendation else "Aucune note enregistrée."),
            "teacher_appreciation": (a.teacher_appreciation if a and a.teacher_appreciation else ""),
            "course_averages": stu_course_avgs,
            "future_curve": future_curve,
        })

    priority = {"CRITIQUE": 0, "VIGILANCE": 1, "STABLE": 2, "SANS_DONNEES": 3}
    student_reports.sort(key=lambda x: (priority.get(x["risk_level"], 9), x["average_score"] or 99))

    class_avg = round(sum(avgs_list) / len(avgs_list), 2) if avgs_list else 0.0
    est_class_avg = (
        class_review_data.get("estimated_class_future_avg")
        or (round(sum(est_list) / len(est_list), 2) if est_list else class_avg)
    )
    pass_rate = round((sum(1 for sc in all_raw_scores if sc >= 10.0) / len(all_raw_scores)) * 100, 1) if all_raw_scores else 0.0

    return {
        "institution_name": INSTITUTION_NAME,
        "tenant_slug": TENANT_SLUG,
        "class_name": target_class,
        "class_label": "Toutes les promotions" if target_class == "ALL" else target_class,
        "generated_at": datetime.utcnow().strftime("%d/%m/%Y à %H:%M"),
        "generated_by": current.full_name,
        "summary": {
            "total_students": len(students),
            "evaluated_students": len(avgs_list),
            "total_evaluations": len(all_raw_scores),
            "class_average": class_avg,
            "estimated_future_class_average": est_class_avg,
            "expected_progression": round(est_class_avg - class_avg, 2),
            "pass_rate": pass_rate,
            "highest_score": max(all_raw_scores) if all_raw_scores else 0.0,
            "lowest_score": min(all_raw_scores) if all_raw_scores else 0.0,
            "critical_count": sum(1 for r in student_reports if r["risk_level"] == "CRITIQUE"),
            "vigilance_count": sum(1 for r in student_reports if r["risk_level"] == "VIGILANCE"),
            "stable_count": sum(1 for r in student_reports if r["risk_level"] == "STABLE"),
        },
        "class_ai_review": class_review_data,
        "course_stats": course_stats,
        "students": student_reports,
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

    # 3. Requête des statements xAPI triés par date chronologique
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
                "estimated_future_average": 0.0,
                "pass_rate": 0.0,
                "highest_score": 0.0,
                "lowest_score": 0.0,
                "risk_counts": {"CRITIQUE": 0, "VIGILANCE": 0, "STABLE": 0},
            }
        }

    stmt_q = (
        db.query(XAPIStatementRecord, User)
        .join(User, User.id == XAPIStatementRecord.student_id)
        .filter(XAPIStatementRecord.student_id.in_(student_ids))
    )
    if course_name and course_name != "ALL":
        stmt_q = stmt_q.filter(XAPIStatementRecord.course_name == course_name)

    records = stmt_q.order_by(XAPIStatementRecord.timestamp.asc()).all()
    records_by_student: Dict[int, List[XAPIStatementRecord]] = {}
    for rec, usr in records:
        records_by_student.setdefault(usr.id, []).append(rec)

    students_summary = []
    est_scores_all: List[float] = []
    for s in students:
        a = alerts_map.get(s.id)
        s_recs = records_by_student.get(s.id, [])
        avg_val = a.average_score if a else None
        trend_val = a.recent_trend if a else 0.0
        est_val = (
            a.estimated_future_score
            if (a and a.estimated_future_score is not None)
            else compute_default_estimated_future_score(avg_val, trend_val)
        )
        if est_val is not None:
            est_scores_all.append(est_val)
        future_curve = compute_student_future_curve(s_recs, avg_val, trend_val, est_val)
        students_summary.append({
            "id": s.id,
            "full_name": s.full_name,
            "class_name": s.class_name or "Générale",
            "average_score": avg_val,
            "recent_trend": trend_val,
            "estimated_future_score": est_val,
            "risk_level": a.risk_level if a else "SANS_DONNEES",
            "weakest_course": a.weakest_course if a else "-",
            "future_curve": future_curve,
        })

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
        "estimated_future_average": round(sum(est_scores_all) / len(est_scores_all), 2) if est_scores_all else 0.0,
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
