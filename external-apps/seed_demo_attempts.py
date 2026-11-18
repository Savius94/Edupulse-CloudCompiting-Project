"""
Peuple Evalio + Notara avec des tentatives réalistes
pour les élèves du tenant démo (emails @demo.fr).
"""

from __future__ import annotations

import json
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
EDU_URL = "http://host.docker.internal:8101"
PASSWORD = "Demo123!"
SLUG = "demo"

STUDENTS = [
    "lucas.martin",
    "chloe.dubois",
    "emma.bernard",
    "alexandre.mercier",
    "hugo.petit",
    "ines.benali",
    "maxime.leroy",
    "thomas.roux",
    "lea.fontaine",
    "camille.girard",
    "nathan.simon",
    "sarah.lambert",
]

# Profil de réussite par élève (0.0 = faible, 1.0 = excellent)
SKILL = {
    "lucas.martin": 0.25,
    "chloe.dubois": 0.35,
    "emma.bernard": 0.45,
    "alexandre.mercier": 0.55,
    "hugo.petit": 0.70,
    "ines.benali": 0.85,
    "maxime.leroy": 0.30,
    "thomas.roux": 0.40,
    "lea.fontaine": 0.50,
    "camille.girard": 0.70,
    "nathan.simon": 0.60,
    "sarah.lambert": 0.90,
}

# Qui passe quoi (évite que tout le monde fasse tout)
EVALIO_ASSIGN = {
    "lucas.martin": ["quiz-cloud-01"],
    "chloe.dubois": ["quiz-python-01", "quiz-cloud-01"],
    "alexandre.mercier": ["quiz-sql-01"],
    "ines.benali": ["quiz-devops-01", "quiz-python-01"],
    "thomas.roux": ["quiz-cloud-01"],
    "camille.girard": ["quiz-sql-01", "quiz-devops-01"],
    "sarah.lambert": ["quiz-python-01"],
}

NOTARA_ASSIGN = {
    "emma.bernard": ["notara-reseau-01"],
    "hugo.petit": ["notara-algo-01", "notara-cloud-01"],
    "maxime.leroy": ["notara-sql-01"],
    "lea.fontaine": ["notara-reseau-01", "notara-sql-01"],
    "nathan.simon": ["notara-cloud-01"],
    "sarah.lambert": ["notara-algo-01"],
}


def http_json(method: str, url: str, payload: dict | None = None, token: str | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = res.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> {exc.code}: {detail[:400]}") from exc


def load_quizzes(app_dir: Path) -> dict:
    quizzes = json.loads((app_dir / "quizzes_seed.json").read_text(encoding="utf-8"))
    return {q["id"]: q for q in quizzes}


def build_answers(quiz: dict, skill: float, rng: random.Random) -> dict:
    answers = {}
    for q in quiz["questions"]:
        if rng.random() <= skill:
            answers[q["id"]] = int(q["answer_index"])
        else:
            wrong = [i for i in range(len(q["choices"])) if i != int(q["answer_index"])]
            answers[q["id"]] = rng.choice(wrong)
    return answers


def run_app(app_name: str, app_url: str, assignments: dict, quizzes: dict) -> list[dict]:
    results = []
    for prefix, quiz_ids in assignments.items():
        email = f"{prefix}@{SLUG}.fr"
        skill = SKILL.get(prefix, 0.5)
        rng = random.Random(f"{app_name}-{prefix}")
        login = http_json(
            "POST",
            f"{app_url}/api/auth/login",
            {"edupulse_url": EDU_URL, "email": email, "password": PASSWORD},
        )
        user = login["user"]
        for quiz_id in quiz_ids:
            quiz = quizzes[quiz_id]
            answers = build_answers(quiz, skill, rng)
            out = http_json(
                "POST",
                f"{app_url}/api/attempts/submit",
                {
                    "edupulse_url": EDU_URL,
                    "access_token": login["access_token"],
                    "student_email": user["email"],
                    "student_name": user["full_name"],
                    "quiz_id": quiz_id,
                    "answers": answers,
                },
            )
            row = {
                "app": app_name,
                "email": email,
                "quiz_id": quiz_id,
                "course": out.get("course_name"),
                "score": out.get("score_raw"),
                "sync": out.get("sync_status"),
            }
            results.append(row)
            print(f"[{app_name}] {email} · {quiz_id} -> {row['score']}/20 ({row['sync']})")
    return results


def main() -> int:
    evalio_q = load_quizzes(BASE / "evalio")
    notara_q = load_quizzes(BASE / "notara")

    # sanity health
    for name, url in (("evalio", "http://127.0.0.1:8240"), ("notara", "http://127.0.0.1:8241")):
        health = http_json("GET", f"{url}/api/health")
        if health.get("status") != "ok":
            print(f"FAIL health {name}: {health}", file=sys.stderr)
            return 1

    all_rows = []
    all_rows += run_app("Evalio", "http://127.0.0.1:8240", EVALIO_ASSIGN, evalio_q)
    all_rows += run_app("Notara", "http://127.0.0.1:8241", NOTARA_ASSIGN, notara_q)

    out_path = BASE / "seed_results.json"
    out_path.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(all_rows)} tentatives écrites dans {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
