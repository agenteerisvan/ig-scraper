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
)
from .phone import extract_phone

app = FastAPI(title="IG Follower Scraper")
JOBS: dict[str, dict] = {}

HTML_FILE = Path(__file__).parent / "index.html"


def _ts():
    return datetime.now().strftime("%H:%M:%S")


async def run_job(job_id: str, username: str, max_followers: int, delay: float, challenge_code: str = ""):
    job = JOBS[job_id]
    try:
        import app.scraper as scraper_mod
        scraper_mod.SCRAPE_DELAY = delay

        job["log"] += "[" + _ts() + "] Iniciando @" + username + "\n"

        job["message"] = "Fazendo login..."
        job["log"] += "[" + _ts() + "] Login..." + "\n"
        cl = await login_and_get_context(challenge_code=challenge_code)

        job["message"] = "Abrindo perfil..."
        info = await get_profile_info(cl, username)
        job["log"] += "  Influencer: " + info["name"] + " (@" + username + ")\n"

        if not info.get("name"):
            raise RuntimeError("Perfil @" + username + " nao encontrado.")

        job["message"] = "Coletando seguidores..."
        job["log"] += "[" + _ts() + "] Coletando seguidores..." + "\n"

        followers = await get_followers_list(cl, username, max_followers=max_followers)

        if not followers:
            raise RuntimeError("Nenhum seguidor coletado.")

        job["total"] = len(followers)
        job["log"] += "  Total: " + str(len(followers)) + " seguidores\n"

        job["message"] = "Extraindo dados..."
        results_list = []
        for i, f in enumerate(followers):
            r = await scraper_mod._scrape_one(cl, i, f)
            results_list.append(r)
            if (i + 1) % 5 == 0 or i == 0:
                job["progress"] = i + 1
                job["phones"] = sum(1 for r in results_list if r.get("telefone"))
                job["privates"] = sum(1 for r in results_list if r.get("privado"))
                job["message"] = "Perfil " + str(i + 1) + "/" + str(len(followers))

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        csv_name = "resultado_" + username + ".csv"
        csv_path = os.path.join(OUTPUT_DIR, csv_name)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["posicao", "nome", "usuario", "telefone", "privado"])
            w.writeheader()
            for r in results_list:
                w.writerow(r)

        csv_phone_name = "resultado_" + username + "_com_telefone.csv"
        csv_phone_path = os.path.join(OUTPUT_DIR, csv_phone_name)
        with open(csv_phone_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["posicao", "nome", "usuario", "telefone"])
            w.writeheader()
            for r in results_list:
                if r.get("telefone"):
                    w.writerow({k: r[k] for k in ["posicao", "nome", "usuario", "telefone"]})

        job["status"] = "done"
        job["csv"] = csv_name
        job["csv_phone"] = csv_phone_name
        job["total"] = len(results_list)
        job["phones"] = sum(1 for r in results_list if r.get("telefone"))
        job["privates"] = sum(1 for r in results_list if r.get("privado"))
        job["progress"] = len(results_list)
        job["message"] = "Concluido!"
        elapsed = int(time.time() - job["start_time"])
        job["log"] += "[" + _ts() + "] Concluido! " + str(elapsed) + "s | Telefones: " + str(job["phones"]) + "\n"

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        tb = traceback.format_exc()
        lines = tb.strip().split("\n")
        job["log_last_traceback"] = lines[-3:] if len(lines) >= 3 else [str(e)]
        job["log"] += "[" + _ts() + "] ERRO: " + str(e)[:200] + "\n"


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
        "privates": 0,
        "log": "",
        "error": None,
        "log_last_traceback": None,
        "csv": None,
        "csv_phone": None,
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
    result = {k: job.get(k) for k in ["status","message","progress","total","phones","privates","log","error","csv","csv_phone","log_last_traceback"]}
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
