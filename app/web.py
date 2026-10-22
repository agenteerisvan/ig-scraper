"""API web do IG Follower Scraper - FastAPI."""
import asyncio
import csv
import json
import os
import time
import traceback
import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse

from .config import OUTPUT_DIR, DATA_DIR, SCRAPE_DELAY
from .scraper import (
    login_and_get_context,
    get_profile_info,
    get_followers_list,
    scrape_all_profiles,
)
from .phone import extract_phone

app = FastAPI(title="IG Follower Scraper")

JOBS: dict[str, dict] = {}


def _ts():
    return datetime.now().strftime("%H:%M:%S")


HTML_PAGE = '<!DOCTYPE html>\n<html lang="pt-BR">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>IG Follower Scraper</title>\n<style>\n*{margin:0;padding:0;box-sizing:border-box}\nbody{font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;background:#0f0f14;color:#e0e0e0;min-height:100vh;display:flex;justify-content:center;padding:40px 16px}\n.container{width:100%;max-width:600px}\n.header{display:flex;align-items:center;gap:12px;margin-bottom:8px}\n.icon{width:40px;height:40px;background:#6366f1;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px}\nh1{font-size:1.5rem;font-weight:700}\n.subtitle{color:#888;font-size:0.9rem;margin-bottom:32px}\n.card{background:#1a1a24;border-radius:16px;padding:24px;margin-bottom:20px}\nlabel{display:block;color:#aaa;font-size:0.85rem;margin-bottom:6px}\ninput[type="text"],input[type="number"]{width:100%;background:#12121a;border:1px solid #2a2a3a;border-radius:8px;padding:12px 14px;color:#fff;font-size:1rem;margin-bottom:16px;outline:none}\ninput:focus{border-color:#6366f1}\n.row{display:flex;gap:12px}\n.row>div{flex:1}\n.btn{width:100%;background:#6366f1;color:#fff;border:none;border-radius:10px;padding:14px;font-size:1rem;font-weight:600;cursor:pointer;transition:background .2s}\n.btn:hover{background:#4f46e5}\n.btn:disabled{background:#444;cursor:not-allowed}\n.status-card .badge{display:inline-block;padding:4px 10px;border-radius:6px;font-size:0.75rem;font-weight:700;text-transform:uppercase}\n.badge.pending{background:#333;color:#999}\n.badge.running{background:#6366f1;color:#fff}\n.badge.done{background:#10b981;color:#fff}\n.badge.error{background:#ef4444;color:#fff}\n.status-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}\n.progress-bar{height:6px;background:#2a2a3a;border-radius:3px;overflow:hidden;margin-bottom:16px}\n.progress-fill{height:100%;background:#6366f1;border-radius:3px;transition:width .5s}\n.stats{display:flex;gap:16px;flex-wrap:wrap}\n.stat{display:flex;align-items:center;gap:6px;font-size:0.85rem;color:#aaa}\n.stat span:first-child{font-size:1rem}\n.log-box{background:#12121a;border-radius:8px;padding:12px;font-family:\'Courier New\',monospace;font-size:0.75rem;max-height:200px;overflow-y:auto;color:#888;line-height:1.6}\n.error-box{margin-top:12px;background:#1a0a0a;border:1px solid #ef4444;border-radius:8px;padding:12px;font-family:monospace;font-size:0.75rem;color:#ef4444}\n.download-btns{display:flex;gap:12px;margin-top:16px}\n.dl-btn{flex:1;text-align:center;padding:10px;background:#1a1a24;border:1px solid #10b981;border-radius:8px;color:#10b981;text-decoration:none;font-size:0.85rem;font-weight:500}\n</style>\n</head>\n<body>\n<div class="container">\n<div class="header"><div class="icon">&#128241;</div><h1>IG Follower Scraper</h1></div>\n<p class="subtitle">Digite o @ do influencer e extraia nome + telefone dos seguidores.</p>\n\n<div class="card">\n<label>@ do Influencer</label>\n<input type="text" id="username" placeholder="ex: cr7" />\n<div class="row">\n<div><label>Max. seguidores (opcional)</label><input type="number" id="max" placeholder="todos" min="1" /></div>\n<div><label>Pausa (seg)</label><input type="number" id="delay" value="3" min="1" max="30" /></div>\n</div>\n<button class="btn" id="startBtn" onclick="startScrape()">Iniciar Extracao</button>\n</div>\n\n<div class="card status-card" id="statusCard" style="display:none">\n<div class="status-row"><span class="badge pending" id="badge">AGUARDANDO</span><span id="pct">0%</span></div>\n<div class="progress-bar"><div class="progress-fill" id="fill" style="width:0%"></div></div>\n<div class="stats">\n<div class="stat"><span>&#128222;</span><span>Telefones: <b id="phoneCount">0</b></span></div>\n<div class="stat"><span>&#128274;</span><span>Privados: <b id="privateCount">0</b></span></div>\n<div class="stat"><span>&#9201;</span><span>Tempo: <b id="timer">0s</b></span></div>\n</div>\n<div class="log-box" id="logBox"></div>\n<div class="error-box" id="errorBox" style="display:none"></div>\n<div class="download-btns" id="dlBtns" style="display:none">\n<a class="dl-btn" id="dlAll" href="#">&#11015;&#65039; CSV Completo</a>\n<a class="dl-btn" id="dlPhone" href="#">&#128222; So c/ Telefone</a>\n</div>\n</div>\n</div>\n\n<script>\nvar jobId = null;\nvar pollId = null;\nvar startTime = 0;\n\nfunction startScrape() {\n    var u = document.getElementById(\'username\').value.trim().replace(/^@/, \'\');\n    if (!u) { alert(\'Digite o @ do influencer\'); return; }\n    var max = parseInt(document.getElementById(\'max\').value) || 0;\n    var delay = parseFloat(document.getElementById(\'delay\').value) || 3;\n    \n    fetch(\'/api/scrape\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({username: u, max_followers: max, delay: delay})\n    })\n    .then(function(r){ return r.json(); })\n    .then(function(d) {\n        jobId = d.job_id;\n        startTime = Date.now();\n        document.getElementById(\'startBtn\').disabled = true;\n        document.getElementById(\'statusCard\').style.display = \'block\';\n        pollId = setInterval(pollStatus, 2000);\n        setInterval(updateTimer, 1000);\n    })\n    .catch(function(e){ alert(\'Erro: \' + e.message); });\n}\n\nfunction pollStatus() {\n    if (!jobId) return;\n    fetch(\'/api/status/\' + jobId)\n    .then(function(r){ return r.json(); })\n    .then(function(d) {\n        var badge = document.getElementById(\'badge\');\n        var pct = document.getElementById(\'pct\');\n        var fill = document.getElementById(\'fill\');\n        \n        var p = d.total > 0 ? Math.round(d.progress / d.total * 100) : 0;\n        pct.textContent = p + \'%\';\n        fill.style.width = p + \'%\';\n        \n        document.getElementById(\'phoneCount\').textContent = d.phones || 0;\n        document.getElementById(\'privateCount\').textContent = d.privates || 0;\n        document.getElementById(\'logBox\').textContent = d.log || \'\';\n        document.getElementById(\'logBox\').scrollTop = 99999;\n        \n        if (d.status === \'running\') {\n            badge.textContent = d.message || \'RODANDO\';\n            badge.className = \'badge running\';\n        } else if (d.status === \'done\') {\n            badge.textContent = \'CONCLUIDO\';\n            badge.className = \'badge done\';\n            clearInterval(pollId);\n            document.getElementById(\'dlBtns\').style.display = \'flex\';\n            document.getElementById(\'dlAll\').href = \'/api/download/\' + jobId + \'?file=\' + encodeURIComponent(d.csv || \'\');\n            document.getElementById(\'dlPhone\').href = \'/api/download/\' + jobId + \'?file=\' + encodeURIComponent(d.csv_phone || \'\');\n            document.getElementById(\'startBtn\').disabled = false;\n        } else if (d.status === \'error\') {\n            badge.textContent = \'ERRO\';\n            badge.className = \'badge error\';\n            clearInterval(pollId);\n            var msg = d.error || \'Erro desconhecido\';\n            if (d.log_last_traceback) {\n                msg += \'\\n\' + d.log_last_traceback.join(\'\\n\');\n            }\n            document.getElementById(\'errorBox\').style.display = \'block\';\n            document.getElementById(\'errorBox\').textContent = msg;\n            document.getElementById(\'startBtn\').disabled = false;\n        }\n    })\n    .catch(function(){ clearInterval(pollId); });\n}\n\nfunction updateTimer() {\n    if (!startTime) return;\n    var s = Math.floor((Date.now() - startTime) / 1000);\n    document.getElementById(\'timer\').textContent = s + \'s\';\n}\n</script>\n</body>\n</html>'


async def run_job(job_id: str, username: str, max_followers: int, delay: float):
    job = JOBS[job_id]
    
    try:
        import app.scraper as scraper_mod
        scraper_mod.SCRAPE_DELAY = delay
        
        job["log"] += "[" + _ts() + "] Iniciando @" + username + "\n"
        
        # Login
        job["message"] = "Fazendo login..."
        job["log"] += "[" + _ts() + "] Login..." + "\n"
        cl = await login_and_get_context()
        
        # Perfil do influencer
        job["message"] = "Abrindo perfil..."
        info = await get_profile_info(cl, username)
        job["log"] += "  Influencer: " + info["name"] + " (@" + username + ")\n"
        
        if not info.get("name"):
            raise RuntimeError("Perfil @" + username + " nao encontrado.")
        
        # Coleta seguidores
        job["message"] = "Coletando seguidores..."
        job["log"] += "[" + _ts() + "] Coletando seguidores..." + "\n"
        
        followers = await get_followers_list(cl, username, max_followers=max_followers)
        
        if not followers:
            raise RuntimeError("Nenhum seguidor coletado.")
        
        job["total"] = len(followers)
        job["log"] += "  Total: " + str(len(followers)) + " seguidores\n"
        
        # Visita perfis e extrai telefones
        job["message"] = "Extraindo dados..."
        
        results_list = []
        for i, f in enumerate(followers):
            r = await asyncio.to_thread(scraper_mod._scrape_one, cl, i, f)
            results_list.append(r)
            
            if (i + 1) % 5 == 0 or i == 0:
                job["progress"] = i + 1
                job["phones"] = sum(1 for r in results_list if r.get("telefone"))
                job["privates"] = sum(1 for r in results_list if r.get("privado"))
                job["message"] = "Perfil " + str(i+1) + "/" + str(len(followers))
        
        # Gera CSVs
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        csv_name = "resultado_" + username + ".csv"
        csv_path = os.path.join(OUTPUT_DIR, csv_name)
        
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=["posicao", "nome", "usuario", "telefone", "privado"])
            writer.writeheader()
            for r in results_list:
                writer.writerow(r)
        
        csv_phone_name = "resultado_" + username + "_com_telefone.csv"
        csv_phone_path = os.path.join(OUTPUT_DIR, csv_phone_name)
        
        with open(csv_phone_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=["posicao", "nome", "usuario", "telefone"])
            writer.writeheader()
            for r in results_list:
                if r.get("telefone"):
                    writer.writerow({k: r[k] for k in ["posicao", "nome", "usuario", "telefone"]})
        
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
    return HTML_PAGE


@app.post("/api/scrape")
async def start_scrape(request: Request):
    data = await request.json()
    username = data.get("username", "").strip().lstrip("@")
    max_followers = int(data.get("max_followers") or 0)
    delay = float(data.get("delay") or 3)
    
    if not username:
        return {"error": "username vazio"}
    
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
    
    asyncio.create_task(run_job(job_id, username, max_followers, delay))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"error": "job nao encontrado"}
    
    result = {}
    for k in ["status","message","progress","total","phones","privates","log","error","csv","csv_phone","log_last_traceback"]:
        if k in job:
            result[k] = job[k]
    result["elapsed"] = int(time.time() - job["start_time"])
    return result


@app.get("/api/download/{job_id}")
async def download(job_id: str, file: str = ""):
    job = JOBS.get(job_id)
    if not job:
        return {"error": "job nao encontrado"}
    
    fname = file or job.get("csv") or ""
    path = os.path.join(OUTPUT_DIR, fname)
    if not os.path.exists(path):
        return {"error": "arquivo nao existe"}
    
    return FileResponse(path, filename=fname, media_type="text/csv")
