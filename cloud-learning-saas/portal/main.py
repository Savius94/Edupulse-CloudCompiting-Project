import os
import re
import time
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import psutil
import jwt
from passlib.context import CryptContext
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, ForeignKey, func, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# =============================================================================
# 1. CONFIGURATION & VARIABLES D'ENVIRONNEMENT
# =============================================================================
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://portal_user:portal_password@portal-db:5432/portal_db"
)
JWT_SECRET = os.getenv("JWT_SECRET", "super_secret_cloud_jwt_key_2026")
JWT_ALGORITHM = "HS256"
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "edupulse.mnbll.fr")
PUBLIC_PORT = os.getenv("PUBLIC_PORT", "80")
SUPERADMIN_EMAIL = os.getenv("SUPERADMIN_EMAIL", "superadmin@edupulse.mnbll.fr")
SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD", "SuperSecretPassword123!")
SUPERADMIN_NAME = os.getenv("SUPERADMIN_NAME", "Super-Admin Global")
TENANT_COMPOSE_PATH = os.getenv(
    "TENANT_COMPOSE_PATH", "/app/tenant-stack/docker-compose.tenant.yml"
)
ORCHESTRATOR_MODE = os.getenv("ORCHESTRATOR_MODE", "auto").lower()
REGISTRY_HOST = os.getenv("REGISTRY_HOST", "").strip()
TENANT_IMAGE = os.getenv(
    "TENANT_IMAGE",
    f"{REGISTRY_HOST}/edupulse-tenant-app:latest" if REGISTRY_HOST else "edupulse-tenant-app:latest",
).strip()

ALLOWED_PLANS = {
    200: "Formule Essentiel (200 utilisateurs max)",
    500: "Formule Campus (500 utilisateurs max)",
    1000: "Formule Académie (1 000 utilisateurs max)",
}

# Configuration Scalabilité & Élasticité calibrée pour VMs 2 Go RAM / 1 vCPU
# Chaque établissement démarre avec >= 2 réplicas (répartis sur Swarm-VM1 & Swarm-VM2)
PLAN_SCALING_RULES = {
    200: {"initial_replicas": 2, "min_replicas": 1, "max_replicas": 4, "cpu_res": "0.05", "mem_res": "64M", "cpu_limit": "0.40", "mem_limit": "256M"},
    500: {"initial_replicas": 2, "min_replicas": 1, "max_replicas": 6, "cpu_res": "0.05", "mem_res": "64M", "cpu_limit": "0.45", "mem_limit": "256M"},
    1000: {"initial_replicas": 3, "min_replicas": 2, "max_replicas": 8, "cpu_res": "0.08", "mem_res": "64M", "cpu_limit": "0.50", "mem_limit": "320M"},
}

AUTOSCALE_CPU_HIGH = 70.0
AUTOSCALE_CPU_LOW = 30.0
TENANT_IMAGE_BUILT = False


# =============================================================================
# 2. INITIALISATION BASE DE DONNÉES SQL (PORTAL-DB)
# =============================================================================
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class PortalUser(Base):
    __tablename__ = "portal_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="SUBSCRIPTION_ADMIN")
    created_at = Column(DateTime, default=datetime.utcnow)

    tenants = relationship(
        "TenantStack", back_populates="owner", cascade="all, delete-orphan"
    )


class TenantStack(Base):
    __tablename__ = "tenant_stacks"

    id = Column(Integer, primary_key=True, index=True)
    institution_name = Column(String(255), nullable=False)
    slug = Column(String(63), unique=True, index=True, nullable=False)
    domain = Column(String(255), unique=True, nullable=False)
    plan_max_users = Column(Integer, nullable=False, default=200)
    status = Column(String(50), nullable=False, default="RUNNING")
    replicas = Column(Integer, nullable=False, default=2)
    min_replicas = Column(Integer, nullable=False, default=1)
    max_replicas = Column(Integer, nullable=False, default=4)
    autoscale_enabled = Column(Integer, nullable=False, default=1)
    orchestrator = Column(String(30), nullable=False, default="SWARM")
    owner_id = Column(Integer, ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("PortalUser", back_populates="tenants")


class CloudSetting(Base):
    """Paramètres partagés du cluster (stockés en BDD pour cohérence entre tous les réplicas portal-api)."""
    __tablename__ = "cloud_settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=True)


class CloudEventLog(Base):
    """Journal d'orchestration Swarm partagé entre tous les réplicas portal-api."""
    __tablename__ = "cloud_events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String(20), nullable=False)
    pillar = Column(String(30), nullable=False)
    slug = Column(String(63), nullable=False)
    message = Column(String(500), nullable=False)
    cpu_percent = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def get_simulated_cpu_override(db: Optional[Session] = None) -> Optional[float]:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        row = db.query(CloudSetting).filter(CloudSetting.key == "simulated_cpu_override").first()
        if row and row.value is not None and row.value != "":
            return float(row.value)
        return None
    except Exception:
        return None
    finally:
        if close_db:
            db.close()


def set_simulated_cpu_override(cpu_val: Optional[float], db: Optional[Session] = None):
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        row = db.query(CloudSetting).filter(CloudSetting.key == "simulated_cpu_override").first()
        str_val = str(cpu_val) if cpu_val is not None else ""
        if not row:
            row = CloudSetting(key="simulated_cpu_override", value=str_val)
            db.add(row)
        else:
            row.value = str_val
        db.commit()
    except Exception:
        pass
    finally:
        if close_db:
            db.close()


def record_cloud_event(pillar: str, slug: str, message: str, cpu_pct: Optional[float] = None, db: Optional[Session] = None):
    """Enregistre un événement d'orchestration Swarm en base de données (partagé entre réplicas)."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        ev = CloudEventLog(
            timestamp=datetime.utcnow().strftime("%H:%M:%S"),
            pillar=pillar.upper(),
            slug=slug,
            message=message[:490],
            cpu_percent=round(cpu_pct, 1) if cpu_pct is not None else None,
        )
        db.add(ev)
        db.commit()
    except Exception:
        pass
    finally:
        if close_db:
            db.close()


def get_recent_cloud_events(db: Session, limit: int = 15) -> List[Dict[str, Any]]:
    try:
        rows = db.query(CloudEventLog).order_by(CloudEventLog.id.desc()).limit(limit).all()
        return [
            {
                "timestamp": r.timestamp,
                "pillar": r.pillar,
                "slug": r.slug,
                "message": r.message,
                "cpu_percent": r.cpu_percent,
            }
            for r in rows
        ]
    except Exception:
        return []


# =============================================================================
# 3. SÉCURITÉ, HACHAGE BCRYPT & TOKENS JWT
# =============================================================================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user: PortalUser) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(hours=12),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> PortalUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Veuillez vous connecter.")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée.")
    user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")
    return user


def require_superadmin(current_user: PortalUser = Depends(get_current_user)) -> PortalUser:
    if current_user.role != "SUPER_ADMIN":
        raise HTTPException(
            status_code=403,
            detail="Accès réservé au Super-Administrateur global de la plateforme.",
        )
    return current_user


# =============================================================================
# 4. SCHÉMAS PYDANTIC
# =============================================================================
class AuthRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = "Administrateur Établissement"


class CreateTenantRequest(BaseModel):
    institution_name: str
    slug: str
    plan_max_users: int = 200
    owner_email: Optional[str] = None
    replicas: Optional[int] = None


class ScaleTenantRequest(BaseModel):
    replicas: Optional[int] = None
    delta: Optional[int] = None


class SimulateLoadRequest(BaseModel):
    cpu_load: Optional[float] = None


# =============================================================================
# 5. ORCHESTRATEUR DOCKER SWARM & DOCKER COMPOSE (SCALABILITY, ELASTICITY, AGILITY)
# =============================================================================
def is_swarm_active() -> bool:
    """Détecte si le démon Docker hôte est actuellement un noeud Docker Swarm actif."""
    if ORCHESTRATOR_MODE == "compose":
        return False
    try:
        res = subprocess.run(
            ["docker", "info", "--format", "{{.Swarm.LocalNodeState}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0 and res.stdout.strip().lower() == "active"
    except Exception:
        return False


def get_swarm_cluster_details() -> Dict[str, Any]:
    """Récupère les métriques temps réel du cluster Docker Swarm et la liste des noeuds."""
    swarm_active = is_swarm_active()
    nodes_count = 1
    managers_count = 1
    swarm_services_count = 0
    nodes_list: List[Dict[str, str]] = []

    if swarm_active:
        try:
            n_res = subprocess.run(
                ["docker", "info", "--format", "{{.Swarm.Nodes}}|{{.Swarm.Managers}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if n_res.returncode == 0 and "|" in n_res.stdout:
                parts = n_res.stdout.strip().split("|")
                nodes_count = int(parts[0] or 1)
                managers_count = int(parts[1] or 1)
        except Exception:
            pass

        try:
            nls_res = subprocess.run(
                ["docker", "node", "ls", "--format", "{{.Hostname}}|{{.Status}}|{{.Availability}}|{{.ManagerStatus}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if nls_res.returncode == 0:
                for line in nls_res.stdout.strip().splitlines():
                    if "|" in line:
                        h, st, av, mgr = line.split("|", 3)
                        nodes_list.append({
                            "hostname": h.strip(),
                            "status": st.strip(),
                            "availability": av.strip(),
                            "role": "Manager" if mgr.strip() else "Worker",
                        })
        except Exception:
            pass

        try:
            s_res = subprocess.run(
                ["docker", "service", "ls", "-q"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if s_res.returncode == 0:
                lines = [l for l in s_res.stdout.strip().splitlines() if l.strip()]
                swarm_services_count = len(lines)
        except Exception:
            pass

    return {
        "swarm_active": swarm_active,
        "orchestrator_mode": "SWARM" if swarm_active else "COMPOSE",
        "nodes_count": nodes_count,
        "managers_count": managers_count,
        "swarm_services_count": swarm_services_count,
        "nodes": nodes_list,
        "served_by_replica": socket.gethostname(),
    }


def ensure_tenant_image_built(context_dir: Path, env: dict, force_rebuild: bool = False):
    """
    Si un Registry centralisé est configuré (ou si portal-api tourne sur un Worker via tcp://docker-socket-proxy),
    les Workers téléchargent directement l'image depuis le Registry du Manager sans build local.
    """
    if REGISTRY_HOST or os.getenv("DOCKER_HOST", "").startswith("tcp://"):
        return

    global TENANT_IMAGE_BUILT
    if TENANT_IMAGE_BUILT and not force_rebuild:
        check = subprocess.run(
            ["docker", "image", "inspect", TENANT_IMAGE],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if check.returncode == 0:
            return

    build_cmd = ["docker", "build", "-t", TENANT_IMAGE]
    for proxy_key in ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"]:
        if env.get(proxy_key):
            build_cmd.extend(["--build-arg", f"{proxy_key}={env[proxy_key]}"])
    build_cmd.append(str(context_dir))

    res = subprocess.run(
        build_cmd,
        cwd=str(context_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if res.returncode == 0:
        TENANT_IMAGE_BUILT = True


def serialize_tenant(t: TenantStack, owner_email: Optional[str] = None) -> dict:
    direct_port = 8100 + t.id
    resolved_email = owner_email or (t.owner.email if t.owner else "")
    port_str = f":{PUBLIC_PORT}" if PUBLIC_PORT not in ("80", "443", "") else ""
    rules = PLAN_SCALING_RULES.get(t.plan_max_users, PLAN_SCALING_RULES[200])
    return {
        "id": t.id,
        "slug": t.slug,
        "institution_name": t.institution_name,
        "domain": t.domain,
        "plan_max_users": t.plan_max_users,
        "plan_label": ALLOWED_PLANS.get(t.plan_max_users, f"{t.plan_max_users} utilisateurs"),
        "status": t.status,
        "replicas": getattr(t, "replicas", None) or rules["initial_replicas"],
        "min_replicas": getattr(t, "min_replicas", None) or rules["min_replicas"],
        "max_replicas": getattr(t, "max_replicas", None) or rules["max_replicas"],
        "autoscale_enabled": bool(getattr(t, "autoscale_enabled", 1)),
        "orchestrator": "SWARM" if is_swarm_active() else (getattr(t, "orchestrator", "COMPOSE") or "COMPOSE"),
        "owner_id": t.owner_id,
        "owner_email": resolved_email,
        "direct_port": direct_port,
        "domain_url": f"http://{t.slug}.{BASE_DOMAIN}",
        "local_subdomain_url": f"http://{t.slug}.localhost{port_str}",
        "direct_local_url": f"http://localhost:{direct_port}",
        "created_at": t.created_at.strftime("%d/%m/%Y à %H:%M") if t.created_at else "Aujourd'hui",
    }


def orchestrate_tenant_stack(
    action: str,
    slug: str,
    tenant_id: int = 1,
    institution_name: str = "",
    plan_max_users: int = 200,
    admin_email: str = "",
    admin_password_hash: str = "",
    admin_name: str = "",
    replicas: Optional[int] = None,
) -> dict:
    compose_path = Path(TENANT_COMPOSE_PATH).resolve()
    if not compose_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Fichier d'orchestration introuvable : {compose_path}",
        )

    direct_port = 8100 + tenant_id
    rules = PLAN_SCALING_RULES.get(plan_max_users, PLAN_SCALING_RULES[200])
    target_replicas = replicas if replicas is not None else rules["initial_replicas"]
    swarm_mode = is_swarm_active()

    env = os.environ.copy()
    env.update(
        {
            "TENANT_SLUG": slug,
            "TENANT_IMAGE": TENANT_IMAGE,
            "INSTITUTION_NAME": institution_name,
            "MAX_USERS": str(plan_max_users),
            "BASE_DOMAIN": BASE_DOMAIN,
            "PUBLIC_PORT": str(PUBLIC_PORT),
            "TENANT_PORT": str(direct_port),
            "ADMIN_EMAIL": admin_email,
            "ADMIN_PASSWORD_HASH": admin_password_hash,
            "ADMIN_FULL_NAME": admin_name,
            "REPLICAS": str(target_replicas),
            "CPU_RES": rules["cpu_res"],
            "MEM_RES": rules["mem_res"],
            "CPU_LIMIT": rules["cpu_limit"],
            "MEM_LIMIT": rules["mem_limit"],
            "TENANT_NET_DRIVER": "overlay" if swarm_mode else "bridge",
        }
    )
    for proxy_var in [
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy",
        "GEMINI_API_KEY", "GEMINI_MODEL"
    ]:
        val = os.getenv(proxy_var)
        if val:
            env[proxy_var] = val

    # -------------------------------------------------------------------------
    # MODE 1 : ORCHESTRATION NATIVE DOCKER SWARM (docker stack / docker service)
    # -------------------------------------------------------------------------
    if swarm_mode:
        # Génère un fichier stack Swarm pré-interpolé (version 3.8, replicas entier, hash bcrypt protégé)
        raw_yaml = compose_path.read_text(encoding="utf-8")
        safe_hash = (admin_password_hash or "").replace("$", "$$")
        rendered_yaml = (
            raw_yaml
            .replace("${TENANT_IMAGE:-100.85.14.115:5000/edupulse-tenant-app:latest}", TENANT_IMAGE)
            .replace("${REPLICAS:-2}", str(int(target_replicas)))
            .replace("${REPLICAS:-1}", str(int(target_replicas)))
            .replace("${TENANT_SLUG}", slug)
            .replace("${INSTITUTION_NAME}", institution_name.replace('"', '\\"'))
            .replace("${MAX_USERS:-200}", str(plan_max_users))
            .replace("${ADMIN_EMAIL}", admin_email)
            .replace("${ADMIN_PASSWORD_HASH}", safe_hash)
            .replace("${ADMIN_FULL_NAME:-Administrateur Principal}", (admin_name or "Administrateur Principal").replace('"', '\\"'))
            .replace("${JWT_SECRET:-super-secret-tenant-key}", env.get("JWT_SECRET", "super-secret-tenant-key"))
            .replace("${GEMINI_API_KEY:-}", env.get("GEMINI_API_KEY", ""))
            .replace("${GEMINI_MODEL:-gemini-3.5-flash-lite}", env.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))
            .replace("${HTTP_PROXY:-}", env.get("HTTP_PROXY", ""))
            .replace("${HTTPS_PROXY:-}", env.get("HTTPS_PROXY", ""))
            .replace("${NO_PROXY:-}", env.get("NO_PROXY", ""))
            .replace("${http_proxy:-}", env.get("http_proxy", ""))
            .replace("${https_proxy:-}", env.get("https_proxy", ""))
            .replace("${no_proxy:-}", env.get("no_proxy", ""))
            .replace("${TENANT_PORT:-8101}", str(direct_port))
            .replace("${BASE_DOMAIN:-edupulse.mnbll.fr}", BASE_DOMAIN)
            .replace("${CPU_RES:-0.05}", rules["cpu_res"])
            .replace("${MEM_RES:-64M}", rules["mem_res"])
            .replace("${CPU_LIMIT:-0.40}", rules["cpu_limit"])
            .replace("${MEM_LIMIT:-256M}", rules["mem_limit"])
            .replace("${CPU_LIMIT:-0.50}", rules["cpu_limit"])
            .replace("${MEM_LIMIT:-512M}", rules["mem_limit"])
            .replace("${TENANT_NET_DRIVER:-overlay}", "overlay")
        )
        rendered_stack_path = Path(f"/tmp/stack-{slug}.yml")
        try:
            rendered_stack_path.write_text(rendered_yaml, encoding="utf-8")
        except Exception:
            rendered_stack_path = compose_path

        if action == "up":
            ensure_tenant_image_built(compose_path.parent, env)
            cmd = ["docker", "stack", "deploy", "--resolve-image=never", "-c", str(rendered_stack_path), slug]
            res = subprocess.run(cmd, cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=180)
            if res.returncode == 0:
                record_cloud_event("AGILITY", slug, f"Déploiement Stack Swarm '{slug}' ({target_replicas} réplica(s) répartis sur les Workers).")
                return {"executed": True, "mode": "SWARM", "command": " ".join(cmd)}
            raise HTTPException(
                status_code=500,
                detail=f"Erreur Docker Swarm deploy : {res.stderr or res.stdout}",
            )

        elif action == "scale":
            # Vérifie si le service Swarm existe
            svc_name = f"{slug}_tenant-app"
            cmd = ["docker", "service", "scale", f"{svc_name}={target_replicas}"]
            res = subprocess.run(cmd, cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                return {"executed": True, "mode": "SWARM", "command": " ".join(cmd)}
            # Fallback si le stack avait été lancé via docker compose
            fallback_cmd = ["docker", "compose", "-p", slug, "-f", str(compose_path), "up", "-d", "--no-recreate", "--scale", f"tenant-app={target_replicas}"]
            subprocess.run(fallback_cmd, cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=120)
            return {"executed": True, "mode": "COMPOSE_FALLBACK", "command": " ".join(fallback_cmd)}

        elif action == "rolling_update":
            ensure_tenant_image_built(compose_path.parent, env, force_rebuild=True)
            svc_name = f"{slug}_tenant-app"
            cmd = [
                "docker", "service", "update",
                "--force",
                "--image", TENANT_IMAGE,
                "--update-parallelism", "1",
                "--update-delay", "3s",
                "--update-order", "start-first",
                svc_name
            ]
            res = subprocess.run(cmd, cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                deploy_cmd = ["docker", "stack", "deploy", "--resolve-image=never", "-c", str(rendered_stack_path), slug]
                subprocess.run(deploy_cmd, cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=180)
                return {"executed": True, "mode": "SWARM", "command": " ".join(deploy_cmd)}
            return {"executed": True, "mode": "SWARM", "command": " ".join(cmd)}

        elif action == "stop":
            svc_check = subprocess.run(["docker", "service", "scale", f"{slug}_tenant-app=0"], capture_output=True, text=True)
            if svc_check.returncode != 0:
                subprocess.run(["docker", "stack", "rm", slug], capture_output=True, text=True, timeout=30)
                subprocess.run(["docker", "compose", "-p", slug, "-f", str(compose_path), "stop"], cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=30)
            return {"executed": True, "mode": "SWARM", "command": f"docker service scale {slug}_tenant-app=0"}

        elif action == "start":
            svc_scale = subprocess.run(["docker", "service", "scale", f"{slug}_tenant-app={target_replicas}"], capture_output=True, text=True)
            if svc_scale.returncode != 0:
                ensure_tenant_image_built(compose_path.parent, env)
                subprocess.run(["docker", "stack", "deploy", "--resolve-image=never", "-c", str(rendered_stack_path), slug], cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=180)
            return {"executed": True, "mode": "SWARM", "command": f"docker service scale {slug}_tenant-app={target_replicas}"}

        elif action == "down":
            subprocess.run(["docker", "stack", "rm", slug], capture_output=True, text=True, timeout=30)
            subprocess.run(["docker", "compose", "-p", slug, "-f", str(compose_path), "down", "-v", "--remove-orphans"], cwd=str(compose_path.parent), env=env, capture_output=True, text=True, timeout=30)
            subprocess.run(["docker", "rm", "-f", f"{slug}-app", f"{slug}-db"], capture_output=True, text=True, timeout=15)
            subprocess.run(["docker", "volume", "rm", "-f", f"{slug}_tenant_db_data"], capture_output=True, text=True, timeout=15)
            return {"executed": True, "mode": "SWARM", "command": f"docker stack rm {slug}"}

    # -------------------------------------------------------------------------
    # MODE 2 : COMPATIBILITÉ DOCKER COMPOSE (Fallback si Swarm inactif)
    # -------------------------------------------------------------------------
    base_cmd = ["docker", "compose", "-p", slug, "-f", str(compose_path)]
    if action == "up":
        cmd = base_cmd + ["up", "-d", "--build"]
    elif action == "scale":
        cmd = base_cmd + ["up", "-d", "--no-recreate", "--scale", f"tenant-app={target_replicas}"]
    elif action == "rolling_update":
        cmd = base_cmd + ["up", "-d", "--build"]
    elif action == "stop":
        cmd = base_cmd + ["stop"]
    elif action == "start":
        cmd = base_cmd + ["start"]
    elif action == "down":
        cmd = base_cmd + ["down", "-v"]
    else:
        raise ValueError(f"Action Docker inconnue : {action}")

    res = subprocess.run(
        cmd,
        cwd=str(compose_path.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if res.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de l'orchestration Docker : {res.stderr or res.stdout}",
        )
    return {"executed": True, "mode": "COMPOSE", "command": " ".join(cmd)}


def evaluate_autoscale(db: Session) -> Dict[str, Any]:
    """
    Moteur d'ÉLASTICITÉ (Auto-Scaling) :
    Analyse la charge CPU/RAM (réelle ou simulée) et ajuste automatiquement le nombre
    de réplicas de chaque établissement actif entre `min_replicas` et `max_replicas`.
    """
    real_cpu = psutil.cpu_percent(interval=0.1)
    sim_override = get_simulated_cpu_override(db)
    effective_cpu = sim_override if sim_override is not None else real_cpu
    ram_pct = psutil.virtual_memory().percent

    running_tenants = (
        db.query(TenantStack)
        .filter(TenantStack.status == "RUNNING", TenantStack.autoscale_enabled == 1)
        .all()
    )

    actions_taken = []
    for t in running_tenants:
        current_rep = t.replicas or 1
        min_rep = t.min_replicas or 1
        max_rep = t.max_replicas or 4

        # 1. Scale-Out automatique en cas de pic de charge (CPU >= 70% ou RAM >= 85%)
        if (effective_cpu >= AUTOSCALE_CPU_HIGH or ram_pct >= 85.0) and current_rep < max_rep:
            step = 2 if effective_cpu >= 85.0 else 1
            new_rep = min(max_rep, current_rep + step)
            try:
                orchestrate_tenant_stack(
                    action="scale",
                    slug=t.slug,
                    tenant_id=t.id,
                    plan_max_users=t.plan_max_users,
                    replicas=new_rep,
                )
            except Exception:
                pass
            t.replicas = new_rep
            msg = f"Scale-Out automatique : {t.slug} augmenté de {current_rep} → {new_rep} réplicas (CPU: {round(effective_cpu, 1)}%)"
            record_cloud_event("ELASTICITY", t.slug, msg, effective_cpu, db=db)
            actions_taken.append({"slug": t.slug, "from": current_rep, "to": new_rep, "type": "SCALE_OUT"})

        # 2. Scale-In automatique en cas de retour au calme (CPU <= 30% et RAM < 75%)
        elif effective_cpu <= AUTOSCALE_CPU_LOW and ram_pct < 75.0 and current_rep > min_rep:
            new_rep = max(min_rep, current_rep - 1)
            try:
                orchestrate_tenant_stack(
                    action="scale",
                    slug=t.slug,
                    tenant_id=t.id,
                    plan_max_users=t.plan_max_users,
                    replicas=new_rep,
                )
            except Exception:
                pass
            t.replicas = new_rep
            msg = f"Scale-In automatique : {t.slug} réduit de {current_rep} → {new_rep} réplicas (Charge basse CPU: {round(effective_cpu, 1)}%)"
            record_cloud_event("ELASTICITY", t.slug, msg, effective_cpu, db=db)
            actions_taken.append({"slug": t.slug, "from": current_rep, "to": new_rep, "type": "SCALE_IN"})

    if actions_taken:
        db.commit()

    return {
        "effective_cpu_percent": round(effective_cpu, 1),
        "real_cpu_percent": round(real_cpu, 1),
        "simulated_mode": sim_override is not None,
        "actions_taken": actions_taken,
    }


# =============================================================================
# 6. APPLICATION FASTAPI & INITIALISATION DU SUPER-ADMIN GLOBAL
# =============================================================================
app = FastAPI(title="EduPulse SaaS - Control Plane & Docker Swarm Orchestrator API")


@app.get("/api/health", include_in_schema=False)
def healthcheck():
    return {"status": "ok", "replica": socket.gethostname()}


@app.on_event("startup")
def on_startup():
    # Attente résiliente de PostgreSQL (indispensable en mode Docker Swarm)
    for attempt in range(20):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception:
            if attempt == 19:
                raise
            time.sleep(1.5)

    # Verrou consultatif PostgreSQL pour éviter les conflits quand 2 réplicas portal-api démarrent ensemble
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(101010);"))
        try:
            Base.metadata.create_all(bind=engine)
            try:
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE tenant_stacks ADD COLUMN IF NOT EXISTS replicas INTEGER DEFAULT 2;"))
                    conn.execute(text("ALTER TABLE tenant_stacks ADD COLUMN IF NOT EXISTS min_replicas INTEGER DEFAULT 1;"))
                    conn.execute(text("ALTER TABLE tenant_stacks ADD COLUMN IF NOT EXISTS max_replicas INTEGER DEFAULT 4;"))
                    conn.execute(text("ALTER TABLE tenant_stacks ADD COLUMN IF NOT EXISTS autoscale_enabled INTEGER DEFAULT 1;"))
                    conn.execute(text("ALTER TABLE tenant_stacks ADD COLUMN IF NOT EXISTS orchestrator VARCHAR(30) DEFAULT 'SWARM';"))
                    conn.commit()
            except Exception:
                pass

            db = SessionLocal()
            try:
                super_admin = (
                    db.query(PortalUser)
                    .filter(PortalUser.email == SUPERADMIN_EMAIL.lower())
                    .first()
                )
                if not super_admin:
                    super_admin = PortalUser(
                        email=SUPERADMIN_EMAIL.lower(),
                        full_name=SUPERADMIN_NAME,
                        password_hash=hash_password(SUPERADMIN_PASSWORD),
                        role="SUPER_ADMIN",
                    )
                    db.add(super_admin)
                else:
                    super_admin.password_hash = hash_password(SUPERADMIN_PASSWORD)
                    super_admin.role = "SUPER_ADMIN"
                db.commit()
                swarm_state = "Docker Swarm Actif" if is_swarm_active() else "Mode Hybride Swarm/Compose Prêt"
                record_cloud_event(
                    "SWARM",
                    "portal-api",
                    f"Réplica {socket.gethostname()} démarré ({swarm_state}).",
                    db=db,
                )
            finally:
                db.close()
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(101010);"))


# =============================================================================
# 7. ROUTES D'AUTHENTIFICATION (INSCRIPTION & CONNEXION)
# =============================================================================
@app.post("/api/auth/register", status_code=201)
def register_subscription_admin(payload: AuthRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    existing = db.query(PortalUser).filter(PortalUser.email == email).first()
    if existing:
        raise HTTPException(
            status_code=400, detail="Un compte existe déjà avec cette adresse e-mail."
        )

    new_user = PortalUser(
        email=email,
        full_name=(payload.full_name or "Administrateur Établissement").strip(),
        password_hash=hash_password(payload.password),
        role="SUBSCRIPTION_ADMIN",
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(new_user)
    return {
        "access_token": token,
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "role": new_user.role,
        },
    }


@app.post("/api/auth/login")
def login(payload: AuthRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    user = db.query(PortalUser).filter(PortalUser.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")

    token = create_access_token(user)
    return {
        "access_token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        },
    }


# =============================================================================
# 8. ROUTES CLIENT (CRÉATION & LISTE DES ÉTABLISSEMENTS)
# =============================================================================
@app.get("/api/tenants/me")
@app.get("/api/tenants/my")
def list_my_tenants(
    current_user: PortalUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tenants = (
        db.query(TenantStack)
        .filter(TenantStack.owner_id == current_user.id)
        .order_by(TenantStack.id.desc())
        .all()
    )
    return [serialize_tenant(t, current_user.email) for t in tenants]


@app.post("/api/tenants", status_code=201)
def create_tenant_stack(
    payload: CreateTenantRequest,
    current_user: PortalUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    slug = re.sub(r"[^a-z0-9-]", "", payload.slug.strip().lower())
    if len(slug) < 2:
        raise HTTPException(
            status_code=400,
            detail="L'identifiant d'accès doit contenir au moins 2 caractères (ex: uppa, school1).",
        )
    if slug in {"www", "api", "admin", "traefik", "portal", "mail"}:
        raise HTTPException(status_code=400, detail="Ce sous-domaine système est réservé.")

    if payload.plan_max_users not in ALLOWED_PLANS:
        raise HTTPException(
            status_code=400,
            detail="Plan invalide. Choisissez 200, 500 ou 1000 utilisateurs max.",
        )

    rules = PLAN_SCALING_RULES.get(payload.plan_max_users, PLAN_SCALING_RULES[200])
    init_replicas = payload.replicas or rules["initial_replicas"]

    existing = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if existing:
        if existing.owner_id == current_user.id or current_user.role == "SUPER_ADMIN":
            existing.institution_name = payload.institution_name.strip()
            existing.plan_max_users = payload.plan_max_users
            existing.replicas = init_replicas
            existing.min_replicas = rules["min_replicas"]
            existing.max_replicas = rules["max_replicas"]
            existing.status = "RUNNING"
            db.commit()
            db.refresh(existing)
            orchestrate_tenant_stack(
                action="up",
                slug=existing.slug,
                tenant_id=existing.id,
                institution_name=existing.institution_name,
                plan_max_users=existing.plan_max_users,
                admin_email=current_user.email,
                admin_password_hash=current_user.password_hash,
                admin_name=current_user.full_name,
                replicas=existing.replicas,
            )
            return serialize_tenant(existing, current_user.email)

        raise HTTPException(
            status_code=409,
            detail=f"L'adresse '{slug}.{BASE_DOMAIN}' est déjà utilisée par un autre établissement.",
        )

    full_domain = f"{slug}.{BASE_DOMAIN}"
    tenant = TenantStack(
        institution_name=payload.institution_name.strip(),
        slug=slug,
        domain=full_domain,
        plan_max_users=payload.plan_max_users,
        status="RUNNING",
        replicas=init_replicas,
        min_replicas=rules["min_replicas"],
        max_replicas=rules["max_replicas"],
        autoscale_enabled=1,
        orchestrator="SWARM" if is_swarm_active() else "COMPOSE",
        owner_id=current_user.id,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    try:
        orchestrate_tenant_stack(
            action="up",
            slug=tenant.slug,
            tenant_id=tenant.id,
            institution_name=tenant.institution_name,
            plan_max_users=tenant.plan_max_users,
            admin_email=current_user.email,
            admin_password_hash=current_user.password_hash,
            admin_name=current_user.full_name,
            replicas=tenant.replicas,
        )
    except Exception as exc:
        db.delete(tenant)
        db.commit()
        raise exc

    return serialize_tenant(tenant, current_user.email)


# =============================================================================
# 9. ROUTES SUPER-ADMINISTRATEUR GLOBAL (SWARM, ELASTICITY, SCALABILITY, AGILITY)
# =============================================================================
@app.get("/api/superadmin/vm-stats")
def get_vm_resources(
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    # Évalue automatiquement les règles d'élasticité lors du rafraîchissement Super-Admin
    autoscale_result = evaluate_autoscale(db)

    vm_mem = psutil.virtual_memory()
    vm_disk = psutil.disk_usage("/")
    real_cpu_pct = psutil.cpu_percent(interval=0.1)
    sim_override = get_simulated_cpu_override(db)
    effective_cpu = sim_override if sim_override is not None else real_cpu_pct

    running_tenants = (
        db.query(TenantStack)
        .filter(TenantStack.status == "RUNNING")
        .all()
    )
    running_stacks = len(running_tenants)
    total_replicas = sum((t.replicas or 1) for t in running_tenants)

    ram_total_mb = round(vm_mem.total / (1024 * 1024))
    ram_used_mb = round(vm_mem.used / (1024 * 1024))
    swarm_info = get_swarm_cluster_details()

    return {
        "cpu_percent": round(effective_cpu, 1),
        "real_cpu_percent": round(real_cpu_pct, 1),
        "simulated_cpu": sim_override is not None,
        "ram_total_mb": ram_total_mb,
        "ram_used_mb": ram_used_mb,
        "ram_percent": round(vm_mem.percent, 1),
        "disk_total_gb": round(vm_disk.total / (1024**3), 1),
        "disk_used_gb": round(vm_disk.used / (1024**3), 1),
        "disk_percent": round(vm_disk.percent, 1),
        "running_stacks": running_stacks,
        "total_replicas": total_replicas,
        "swarm": swarm_info,
        "autoscale_thresholds": {
            "cpu_high": AUTOSCALE_CPU_HIGH,
            "cpu_low": AUTOSCALE_CPU_LOW,
        },
        "events": get_recent_cloud_events(db, 15),
        "autoscale_actions": autoscale_result["actions_taken"],
    }


@app.post("/api/superadmin/swarm/init")
def sa_init_swarm(
    _: PortalUser = Depends(require_superadmin),
):
    """Initialise Docker Swarm sur le noeud hôte s'il n'est pas encore actif."""
    if is_swarm_active():
        return {"swarm_active": True, "message": "Docker Swarm est déjà actif sur ce cluster."}

    res = subprocess.run(
        ["docker", "swarm", "init"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res.returncode != 0 and "--advertise-addr" in (res.stderr or ""):
        for iface in ["tailscale0", "eth0", "ens18", "enp0s3", "127.0.0.1"]:
            res = subprocess.run(
                ["docker", "swarm", "init", "--advertise-addr", iface],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode == 0 or is_swarm_active():
                break

    if not is_swarm_active():
        raise HTTPException(
            status_code=500,
            detail=f"Impossible d'initialiser Docker Swarm : {res.stderr or res.stdout}",
        )

    record_cloud_event("SWARM", "cluster", "Cluster Docker Swarm initialisé avec succès (Manager actif).")
    return {"swarm_active": True, "message": "Cluster Docker Swarm initialisé avec succès."}


@app.post("/api/superadmin/autoscale/simulate-load")
def sa_simulate_load(
    payload: SimulateLoadRequest,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """
    Permet de simuler un pic de charge CPU (ex: 85%) ou un retour à la normale (ex: 15% ou None)
    afin de démontrer l'Élasticité (Auto-Scaling Docker Swarm) en direct lors d'une soutenance.
    """
    set_simulated_cpu_override(payload.cpu_load, db)
    if payload.cpu_load is not None:
        record_cloud_event(
            "ELASTICITY",
            "cluster-sensor",
            f"Simulation de charge activée : CPU fixé à {round(payload.cpu_load, 1)}% (Seuil Scale-Out: {AUTOSCALE_CPU_HIGH}% / Scale-In: {AUTOSCALE_CPU_LOW}%).",
            payload.cpu_load,
            db=db,
        )
    else:
        record_cloud_event(
            "ELASTICITY",
            "cluster-sensor",
            "Fin de la simulation : retour à la sonde matérielle CPU réelle de la VM.",
            db=db,
        )

    eval_res = evaluate_autoscale(db)
    return {
        "simulated_cpu": payload.cpu_load,
        "evaluation": eval_res,
        "events": get_recent_cloud_events(db, 15),
    }


@app.post("/api/superadmin/tenants/{slug}/scale")
def sa_scale_tenant(
    slug: str,
    payload: ScaleTenantRequest,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """
    SCALABILITÉ HORIZONTALE : Ajuste à la demande le nombre de conteneurs réplicas
    d'un établissement (`docker service scale <slug>_tenant-app=<N>`).
    """
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Stack introuvable.")

    old_replicas = tenant.replicas or 1
    if payload.replicas is not None:
        target = payload.replicas
    elif payload.delta is not None:
        target = old_replicas + payload.delta
    else:
        target = old_replicas

    target = max(1, min(10, target))
    orchestrate_tenant_stack(
        action="scale",
        slug=tenant.slug,
        tenant_id=tenant.id,
        plan_max_users=tenant.plan_max_users,
        replicas=target,
    )
    tenant.replicas = target
    if target > tenant.max_replicas:
        tenant.max_replicas = target
    db.commit()

    direction = "Scale-Out (+)" if target > old_replicas else "Scale-In (-)"
    record_cloud_event(
        "SCALABILITY",
        tenant.slug,
        f"{direction} manuel : {tenant.slug} ajusté de {old_replicas} → {target} réplica(s) actifs.",
    )
    return serialize_tenant(tenant)


@app.post("/api/superadmin/tenants/{slug}/autoscale-toggle")
def sa_toggle_autoscale(
    slug: str,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Active ou désactive l'Élasticité (Auto-Scaling automatique) sur un stack."""
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Stack introuvable.")

    tenant.autoscale_enabled = 0 if getattr(tenant, "autoscale_enabled", 1) else 1
    db.commit()
    state_str = "activé" if tenant.autoscale_enabled else "désactivé"
    record_cloud_event(
        "ELASTICITY",
        tenant.slug,
        f"Auto-Scaling {state_str} pour '{tenant.slug}' (bornes : min={tenant.min_replicas}, max={tenant.max_replicas} réplicas).",
    )
    return serialize_tenant(tenant)


@app.post("/api/superadmin/tenants/{slug}/rolling-update")
def sa_rolling_update_tenant(
    slug: str,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """
    AGILITÉ (Zero-Downtime Rolling Update) : Met à jour les réplicas du service un par un
    avec la politique `start-first` sans aucune coupure de service pour les élèves/enseignants.
    """
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Stack introuvable.")

    res = orchestrate_tenant_stack(
        action="rolling_update",
        slug=tenant.slug,
        tenant_id=tenant.id,
        institution_name=tenant.institution_name,
        plan_max_users=tenant.plan_max_users,
        replicas=tenant.replicas or 1,
    )
    record_cloud_event(
        "AGILITY",
        tenant.slug,
        f"Rolling Update Zero-Downtime exécuté sur '{tenant.slug}' ({tenant.replicas or 1} réplica(s), politique start-first).",
    )
    return {"status": "UPDATED", "details": res, "tenant": serialize_tenant(tenant)}


@app.get("/api/superadmin/tenants")
def sa_list_tenants(
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TenantStack, PortalUser)
        .join(PortalUser, PortalUser.id == TenantStack.owner_id)
        .order_by(TenantStack.id.desc())
        .all()
    )
    return [serialize_tenant(t, u.email) for t, u in rows]


@app.post("/api/superadmin/tenants", status_code=201)
def sa_create_tenant(
    payload: CreateTenantRequest,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    slug = re.sub(r"[^a-z0-9-]", "", payload.slug.strip().lower())
    if db.query(TenantStack).filter(TenantStack.slug == slug).first():
        raise HTTPException(status_code=400, detail="Ce sous-domaine existe déjà.")

    owner_email = (payload.owner_email or "direction@ecole.fr").lower().strip()
    owner = db.query(PortalUser).filter(PortalUser.email == owner_email).first()
    if not owner:
        owner = PortalUser(
            email=owner_email,
            full_name=f"Direction {payload.institution_name.strip()}",
            password_hash=hash_password("ClientPass123!"),
            role="SUBSCRIPTION_ADMIN",
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)

    rules = PLAN_SCALING_RULES.get(payload.plan_max_users, PLAN_SCALING_RULES[200])
    init_replicas = payload.replicas or rules["initial_replicas"]

    tenant = TenantStack(
        institution_name=payload.institution_name.strip(),
        slug=slug,
        domain=f"{slug}.{BASE_DOMAIN}",
        plan_max_users=payload.plan_max_users,
        status="RUNNING",
        replicas=init_replicas,
        min_replicas=rules["min_replicas"],
        max_replicas=rules["max_replicas"],
        autoscale_enabled=1,
        orchestrator="SWARM" if is_swarm_active() else "COMPOSE",
        owner_id=owner.id,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    orchestrate_tenant_stack(
        action="up",
        slug=tenant.slug,
        tenant_id=tenant.id,
        institution_name=tenant.institution_name,
        plan_max_users=tenant.plan_max_users,
        admin_email=owner.email,
        admin_password_hash=owner.password_hash,
        admin_name=owner.full_name,
        replicas=tenant.replicas,
    )
    return serialize_tenant(tenant, owner.email)


@app.post("/api/superadmin/tenants/{slug}/{action}")
def sa_control_tenant(
    slug: str,
    action: str,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Stack introuvable.")
    if action == "stop":
        orchestrate_tenant_stack(action="stop", slug=slug, tenant_id=tenant.id)
        tenant.status = "STOPPED"
        record_cloud_event("AGILITY", slug, f"Stack '{slug}' mis en pause (0 réplica actif, données persistées).")
    elif action == "start":
        owner = tenant.owner
        orchestrate_tenant_stack(
            action="start",
            slug=slug,
            tenant_id=tenant.id,
            institution_name=tenant.institution_name,
            plan_max_users=tenant.plan_max_users,
            admin_email=owner.email if owner else "",
            admin_password_hash=owner.password_hash if owner else "",
            admin_name=owner.full_name if owner else "",
            replicas=tenant.replicas or 1,
        )
        tenant.status = "RUNNING"
        record_cloud_event("AGILITY", slug, f"Stack '{slug}' relancé ({tenant.replicas or 1} réplica(s) actifs).")
    else:
        raise HTTPException(status_code=400, detail="Action invalide.")
    db.commit()
    return {"status": tenant.status}


@app.delete("/api/tenants/{slug}")
def delete_my_tenant(
    slug: str,
    current_user: PortalUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permet à un client (ou au Super-Admin) de supprimer directement son propre établissement."""
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Établissement introuvable.")
    if tenant.owner_id != current_user.id and current_user.role != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Vous n'êtes pas propriétaire de cet établissement.")

    tenant_id = tenant.id
    # Suppression immédiate en base de données (évite tout affichage fantôme si déjà supprimé dans Portainer)
    db.delete(tenant)
    db.commit()

    try:
        orchestrate_tenant_stack(action="down", slug=slug, tenant_id=tenant_id)
    except Exception:
        pass
    record_cloud_event("AGILITY", slug, f"Stack '{slug}' supprimé par le client ({current_user.email}).")
    return {"deleted": slug}


@app.delete("/api/superadmin/tenants/{slug}")
def sa_delete_tenant(
    slug: str,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    tenant_id = tenant.id if tenant else 1
    # Suppression immédiate en base de données (même si le conteneur a déjà été détruit via Portainer)
    if tenant:
        db.delete(tenant)
        db.commit()
    try:
        orchestrate_tenant_stack(action="down", slug=slug, tenant_id=tenant_id)
    except Exception:
        pass
    record_cloud_event("AGILITY", slug, f"Stack '{slug}' et ses volumes supprimés définitivement.")
    return {"deleted": slug}


@app.get("/api/superadmin/users")
def sa_list_users(
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    users = db.query(PortalUser).order_by(PortalUser.id.asc()).all()
    result = []
    for u in users:
        cnt = (
            db.query(func.count(TenantStack.id))
            .filter(TenantStack.owner_id == u.id)
            .scalar()
            or 0
        )
        result.append(
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "tenant_count": cnt,
            }
        )
    return result


@app.delete("/api/superadmin/users/{user_id}")
def sa_delete_user(
    user_id: int,
    current_admin: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Impossible de supprimer votre propre compte.")
    user = db.query(PortalUser).filter(PortalUser.id == user_id).first()
    if not user or user.role == "SUPER_ADMIN":
        raise HTTPException(status_code=400, detail="Compte protégé ou introuvable.")

    for t in user.tenants:
        try:
            orchestrate_tenant_stack(action="down", slug=t.slug, tenant_id=t.id)
        except Exception:
            pass
    db.delete(user)
    db.commit()
    return {"deleted_user": user.email}


# =============================================================================
# 10. SERVEUR DE L'INTERFACE FRONTEND DU PORTAIL
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@app.get("/", include_in_schema=False)
def serve_portal_ui():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
