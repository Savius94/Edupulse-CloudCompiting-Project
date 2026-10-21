import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import psutil
import jwt
from passlib.context import CryptContext
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, func
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
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "cloudproject.mnbll.fr")
PUBLIC_PORT = os.getenv("PUBLIC_PORT", "8000")
SUPERADMIN_EMAIL = os.getenv("SUPERADMIN_EMAIL", "superadmin@cloudproject.mnbll.fr")
SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD", "SuperSecretPassword123!")
SUPERADMIN_NAME = os.getenv("SUPERADMIN_NAME", "Super-Admin Global")
TENANT_COMPOSE_PATH = os.getenv(
    "TENANT_COMPOSE_PATH", "/app/tenant-stack/docker-compose.tenant.yml"
)

ALLOWED_PLANS = {
    200: "Formule Essentiel (200 utilisateurs max)",
    500: "Formule Campus (500 utilisateurs max)",
    1000: "Formule Académie (1 000 utilisateurs max)",
}

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
    owner_id = Column(Integer, ForeignKey("portal_users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("PortalUser", back_populates="tenants")


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


# =============================================================================
# 5. SÉRIALISATION & ORCHESTRATEUR DOCKER COMPOSE
# =============================================================================
def serialize_tenant(t: TenantStack, owner_email: Optional[str] = None) -> dict:
    direct_port = 8100 + t.id
    resolved_email = owner_email or (t.owner.email if t.owner else "")
    return {
        "id": t.id,
        "slug": t.slug,
        "institution_name": t.institution_name,
        "domain": t.domain,
        "plan_max_users": t.plan_max_users,
        "plan_label": ALLOWED_PLANS.get(t.plan_max_users, f"{t.plan_max_users} utilisateurs"),
        "status": t.status,
        "owner_id": t.owner_id,
        "owner_email": resolved_email,
        "direct_port": direct_port,
        "domain_url": f"http://{t.slug}.{BASE_DOMAIN}:{PUBLIC_PORT}",
        "local_subdomain_url": f"http://{t.slug}.localhost:{PUBLIC_PORT}",
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
) -> dict:
    compose_path = Path(TENANT_COMPOSE_PATH).resolve()
    direct_port = 8100 + tenant_id

    env = os.environ.copy()
    env.update(
        {
            "TENANT_SLUG": slug,
            "INSTITUTION_NAME": institution_name,
            "MAX_USERS": str(plan_max_users),
            "BASE_DOMAIN": BASE_DOMAIN,
            "PUBLIC_PORT": str(PUBLIC_PORT),
            "TENANT_PORT": str(direct_port),
            "ADMIN_EMAIL": admin_email,
            "ADMIN_PASSWORD_HASH": admin_password_hash,
            "ADMIN_FULL_NAME": admin_name,
        }
    )
    for proxy_var in [
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy"
    ]:
        val = os.getenv(proxy_var)
        if val:
            env[proxy_var] = val

    base_cmd = ["docker", "compose", "-p", slug, "-f", str(compose_path)]
    if action == "up":
        cmd = base_cmd + ["up", "-d", "--build"]
    elif action == "stop":
        cmd = base_cmd + ["stop"]
    elif action == "start":
        cmd = base_cmd + ["start"]
    elif action == "down":
        cmd = base_cmd + ["down", "-v"]
    else:
        raise ValueError(f"Action Docker inconnue : {action}")

    if not compose_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Fichier introuvable : {compose_path}",
        )

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
            detail=f"Erreur lors du déploiement Docker : {res.stderr or res.stdout}",
        )
    return {"executed": True, "command": " ".join(cmd)}


# =============================================================================
# 6. APPLICATION FASTAPI & INITIALISATION DU SUPER-ADMIN GLOBAL
# =============================================================================
app = FastAPI(title="EduPulse SaaS - Control Plane API")


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
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
            # S'assure que le mot de passe correspond toujours à la variable d'environnement
            super_admin.password_hash = hash_password(SUPERADMIN_PASSWORD)
            super_admin.role = "SUPER_ADMIN"
        db.commit()
    finally:
        db.close()


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

    existing = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    if existing:
        # Si ce stack a déjà été créé par ce client (ex: uppa), on le met à jour et on le relance proprement
        if existing.owner_id == current_user.id or current_user.role == "SUPER_ADMIN":
            existing.institution_name = payload.institution_name.strip()
            existing.plan_max_users = payload.plan_max_users
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
        )
    except Exception as exc:
        db.delete(tenant)
        db.commit()
        raise exc

    return serialize_tenant(tenant, current_user.email)


# =============================================================================
# 9. ROUTES SUPER-ADMINISTRATEUR GLOBAL (MONITORING VM, STACKS & COMPTES)
# =============================================================================
@app.get("/api/superadmin/vm-stats")
def get_vm_resources(
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    vm_mem = psutil.virtual_memory()
    vm_disk = psutil.disk_usage("/")
    cpu_pct = psutil.cpu_percent(interval=0.2)

    running_stacks = (
        db.query(func.count(TenantStack.id))
        .filter(TenantStack.status == "RUNNING")
        .scalar()
        or 0
    )
    ram_total_mb = round(vm_mem.total / (1024 * 1024))
    ram_used_mb = round(vm_mem.used / (1024 * 1024))

    return {
        "cpu_percent": round(cpu_pct, 1),
        "ram_total_mb": ram_total_mb,
        "ram_used_mb": ram_used_mb,
        "ram_percent": round(vm_mem.percent, 1),
        "disk_total_gb": round(vm_disk.total / (1024**3), 1),
        "disk_used_gb": round(vm_disk.used / (1024**3), 1),
        "disk_percent": round(vm_disk.percent, 1),
        "running_stacks": running_stacks,
    }


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

    tenant = TenantStack(
        institution_name=payload.institution_name.strip(),
        slug=slug,
        domain=f"{slug}.{BASE_DOMAIN}",
        plan_max_users=payload.plan_max_users,
        status="RUNNING",
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
    elif action == "start":
        orchestrate_tenant_stack(action="start", slug=slug, tenant_id=tenant.id)
        tenant.status = "RUNNING"
    else:
        raise HTTPException(status_code=400, detail="Action invalide.")
    db.commit()
    return {"status": tenant.status}


@app.delete("/api/superadmin/tenants/{slug}")
def sa_delete_tenant(
    slug: str,
    _: PortalUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    tenant = db.query(TenantStack).filter(TenantStack.slug == slug).first()
    try:
        orchestrate_tenant_stack(action="down", slug=slug, tenant_id=tenant.id if tenant else 1)
    except Exception:
        pass
    if tenant:
        db.delete(tenant)
        db.commit()
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
