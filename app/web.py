"""API web do IG Follower Scraper - FastAPI."""
import asyncio
import csv
import json
import os
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from .config import OUTPUT_DIR, DATA_DIR, SCRAPE_DELAY
from .scraper import (
    login_and_get_context,
    get_profile_info,
    get_followers_list,
    scrape_all_profiles,
)

app = FastAPI(title="IG Follower Scraper")
JOBS: dict[str, dict] = {}

HTML_FILE = Path(__file__).parent / "index.html"


def _ts():
    return datetime.now().strftime("%H:%M:%S")


async def run_job(job_id: str, username: str, max_followers: int, delay: float, challenge_code: str = ""):
    job = JOBS[job_id]
    try:
        job["log"] += f"[{_ts()}] Iniciando @{username}\n"

        # LOGIN
        job["message"] = "Fazendo login..."
        job["log"] += f"[{_ts()}] Login...\n"
        cl = await login_and_get_context(challenge_code=challenge_code)

        # PERFIL DO INFLUENCER
        job["message"] = "Abrindo perfil..."
        info = await get_profile_info(cl, username)
        job["log"] += f"  Influencer: {info['name']} (@{username})\n"

        if not info.get("name"):
            raise RuntimeError(f"Perfil @{username} nao encontrado.")

        # COLETAR SEGUIDORES (paginado)
        job["message"] = "Coletando seguidores..."
        job["log"] += f"[{_ts()}] Coletando seguidores...\n"

        def _collect_progress(count):
            job["message"] = f"Coletando... {count}"
        
        followers = await get_followers_list(cl, username, max_followers=max_followers, progress_cb=_collect_progress)

        if not followers:
            raise RuntimeError("Nenhum seguidor coletado.")

        job["total"] = len(followers)
        job["log"] += f"  Total: {len(followers)} seguidores\n"

        # EXTRAI DADOS (paralelo)
        job["message"] = "Extraindo dados..."
        job["log"] += f"[{_ts()}] Extraindo dados ({len(followers)} perfis, paralelo)...\n"

        # Counter p/ progresso sem closure issue
        _counters = {"done": 0, "phones": 0, "emails": 0, "privates": 0}
        
        def _scrape_progress(done):
            job["progress"] = done
            job["message"] = f"Perfil {done}/{len(followers)}"

        results_list = await scrape_all_profiles(cl, followers, delay=delay, progress_cb=_scrape_progress)

        # GERA CSV
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        csv_name = f"resultado_{username}.csv"
        csv_path = os.path.join(OUTPUT_DIR, csv_name)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["posicao", "nome", "usuario", "telefone", "email", "privado"])
            w.writeheader()
            for r in results_list:
                w.writerow({k: r.get(k, "") for k in w.fieldnames})

        # CSV so com contatos
        csv_contact_name = f"resultado_{username}_contatos.csv"
        csv_contact_path = os.path.join(OUTPUT_DIR, csv_contact_name)
        with open(csv_contact_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["posicao", "nome", "usuario", "telefone", "email"])
            w.writeheader()
            for r in results_list:
                if r.get("telefone") or r.get("email"):
                    w.writerow({k: r.get(k, "") for k in w.fieldnames})

        # FINALIZA
        job["status"] = "done"
        job["csv"] = csv_name
        job["csv_contact"] = csv_contact_name
        job["total"] = len(results_list)
        job["phones"] = sum(1 for r in results_list if r.get("telefone"))
        job["emails"] = sum(1 for r in results_list if r.get("email"))
        job["privates"] = sum(1 for r in results_list if r.get("privado"))
        job["progress"] = len(results_list)
        job["message"] = "Concluido!"
        elapsed = int(time.time() - job["start_time"])
        job["log"] += f"[{_ts()}] Concluido! {elapsed}s | Tel: {job['phones']} | Email: {job['emails']}\n"

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        tb = traceback.format_exc()
        lines = tb.strip().split("\n")
        job["log_last_traceback"] = lines[-3:] if len(lines) >= 3 else [str(e)]
        job["log"] += f"[{_ts()}] ERRO: {str(e)[:200]}\n"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_FILE.read_text(encoding="utf-8")


@app.post("/api/scrape")
async def start_scrape(request: Request):
    data = await request.json()
    username = data.get("username", "").strip().lstrip("@")
    max_followers = int(data.get("max_followers") or 0)
    delay = float(data.get("delay") or 3)
    challenge_code = data.get("challenge_code", "")

    if not username:
        return JSONResponse({"error": "username vazio"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "status": "running",
        "message": "Iniciando...",
        "progress": 0,
        "total": 0,
        "phones": 0,
        "emails": 0,
        "privates": 0,
        "log": "",
        "error": None,
        "log_last_traceback": None,
        "csv": None,
        "csv_contact": None,
        "start_time": time.time(),
        "username": username,
    }

    asyncio.create_task(run_job(job_id, username, max_followers, delay, challenge_code))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    result = {k: job.get(k) for k in [
        "status", "message", "progress", "total",
        "phones", "emails", "privates",
        "log", "error", "csv", "csv_contact", "log_last_traceback"
    ]}
    result["elapsed"] = int(time.time() - job["start_time"])
    return result


@app.get("/api/download/{job_id}")
async def download(job_id: str, file: str = ""):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    fname = file or job.get("csv") or ""
    path = os.path.join(OUTPUT_DIR, fname)
    if not os.path.exists(path):
        return JSONResponse({"error": "arquivo nao existe"}, status_code=404)
    return FileResponse(path, filename=fname, media_type="text/csv")
