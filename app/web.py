"""
Interface web: digita o @username e acompanha o progresso.
"""
import asyncio
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from playwright.async_api import async_playwright

from .config import OUTPUT_DIR, MAX_WORKERS, SCRAPE_DELAY
from .scraper import get_authenticated_context, get_followers_list, scrape_all_profiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

app = FastAPI(title="IG Follower Scraper")
jobs: dict[str, dict] = {}


INDEX_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IG Follower Scraper</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f0f; color: #e4e4e7; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .container { width: 100%; max-width: 560px; padding: 2rem; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #fff; }
    p.subtitle { color: #71717a; margin-bottom: 2rem; font-size: 0.9rem; }
    .form-group { margin-bottom: 1rem; }
    label { display: block; margin-bottom: 0.4rem; font-size: 0.85rem; color: #a1a1aa; }
    input[type="text"], input[type="number"] { width: 100%; padding: 0.75rem 1rem; border-radius: 8px; border: 1px solid #3f3f46; background: #18181b; color: #fff; font-size: 1rem; outline: none; transition: border-color 0.2s; }
    input:focus { border-color: #6366f1; }
    .row { display: flex; gap: 1rem; } .row > * { flex: 1; }
    button { width: 100%; padding: 0.85rem; border-radius: 8px; border: none; background: #6366f1; color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background 0.2s; margin-top: 0.5rem; }
    button:hover { background: #4f46e5; }
    button:disabled { background: #3730a3; cursor: not-allowed; opacity: 0.7; }
    .progress-section { margin-top: 1.5rem; display: none; }
    .progress-bar-bg { height: 8px; background: #27272a; border-radius: 4px; overflow: hidden; margin: 0.5rem 0; }
    .progress-bar-fill { height: 100%; background: #6366f1; border-radius: 4px; transition: width 0.5s; width: 0%; }
    .stats { display: flex; gap: 1.5rem; margin-top: 1rem; font-size: 0.85rem; color: #a1a1aa; }
    .stats span strong { color: #e4e4e7; }
    .result-section { margin-top: 1.5rem; display: none; }
    .result-card { background: #18181b; border: 1px solid #27272a; border-radius: 8px; padding: 1.25rem; }
    .result-card h3 { font-size: 1rem; margin-bottom: 0.75rem; color: #fff; }
    .error-msg { background: #1c1114; border: 1px solid #7f1d1d; border-radius: 8px; padding: 1rem; margin-top: 1rem; font-size: 0.85rem; color: #fca5a5; word-break: break-word; white-space: pre-wrap; font-family: monospace; }
    .download-link { display: inline-block; margin-top: 1rem; padding: 0.6rem 1.2rem; background: #059669; color: #fff; text-decoration: none; border-radius: 6px; font-size: 0.9rem; font-weight: 500; }
    .download-link:hover { background: #047857; }
    .history { margin-top: 2rem; }
    .history h3 { font-size: 0.9rem; color: #71717a; margin-bottom: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .history-item { display: flex; justify-content: space-between; align-items: center; padding: 0.6rem 0; border-bottom: 1px solid #1f1f23; font-size: 0.85rem; }
    .history-item a { color: #6366f1; text-decoration: none; }
  </style>
</head>
<body>
  <div class="container">
    <h1>📱 IG Follower Scraper</h1>
    <p class="subtitle">Digite o @ do influencer e extraia nome + telefone dos seguidores.</p>
    
    <form id="scrape-form">
      <div class="form-group">
        <label for="username">@ do Influencer</label>
        <input type="text" id="username" name="username" placeholder="ex: heelder_" required>
      </div>
      <div class="row">
        <div class="form-group">
          <label for="max">Máx. seguidores (opcional)</label>
          <input type="number" id="max" name="max" placeholder="todos" min="10">
        </div>
        <div class="form-group">
          <label for="delay">Pausa (seg)</label>
          <input type="number" id="delay" name="delay" value="3" min="1" max="30" step="0.5">
        </div>
      </div>
      <button type="submit" id="btn-submit">Iniciar Extração</button>
    </form>
    
    <div class="progress-section" id="progress-section">
      <div style="display:flex;justify-content:space-between;font-size:0.85rem;color:#a1a1aa;">
        <span id="progress-label">Inicializando...</span>
        <span id="progress-pct">0%</span>
      </div>
      <div class="progress-bar-bg"><div class="progress-bar-fill" id="progress-fill"></div></div>
      <div class="stats">
        <span>📱 Telefones: <strong id="stat-phones">0</strong></span>
        <span>🔒 Privados: <strong id="stat-private">0</strong></span>
        <span>⏱️ Tempo: <strong id="stat-time">0s</strong></span>
      </div>
      <div class="error-msg" id="error-msg" style="display:none;"></div>
    </div>
    
    <div class="result-section" id="result-section">
      <div class="result-card">
        <h3>✅ Conclusão</h3>
        <div id="result-text"></div>
        <a id="download-link" class="download-link" href="#">⬇️ Baixar CSV (com telefone)</a>
      </div>
    </div>
    
    <div class="history" id="history-section" style="display:none;">
      <h3>Histórico</h3>
      <div id="history-list"></div>
    </div>
  </div>
  
  <script>
    const form = document.getElementById('scrape-form');
    const btn = document.getElementById('btn-submit');
    let pollInterval = null;
    
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('username').value.trim().replace(/^@/, '');
      const max = document.getElementById('max').value || null;
      const delay = parseFloat(document.getElementById('delay').value) || 3;
      
      btn.disabled = true;
      btn.textContent = 'Enviando...';
      document.getElementById('error-msg').style.display = 'none';
      
      try {
        const res = await fetch('/api/scrape', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({username, max: max ? parseInt(max) : null, delay})
        });
        const data = await res.json();
        
        if (data.error) {
          showError(data.error);
          return;
        }
        
        const jobId = data.job_id;
        document.getElementById('progress-section').style.display = 'block';
        document.getElementById('result-section').style.display = 'none';
        document.getElementById('progress-label').textContent = 'Iniciando...';
        let start = Date.now();
        
        pollInterval = setInterval(async () => {
          try {
            const status = await fetch(`/api/status/${jobId}`).then(r => r.json());
            const pct = status.total ? Math.round(status.done / status.total * 100) : 0;
            document.getElementById('progress-label').textContent = status.message || 'Processando...';
            document.getElementById('progress-pct').textContent = pct + '%';
            document.getElementById('progress-fill').style.width = pct + '%';
            document.getElementById('stat-phones').textContent = status.phones || 0;
            document.getElementById('stat-private').textContent = status.private || 0;
            document.getElementById('stat-time').textContent = Math.round((Date.now() - start) / 1000) + 's';
            
            if (status.status === 'completed') {
              clearInterval(pollInterval);
              btn.disabled = false;
              btn.textContent = 'Iniciar Extração';
              document.getElementById('result-text').innerHTML = 
                `<p>${status.done} seguidores processados.</p>
                 <p>📱 Com telefone: <strong>${status.phones}</strong> (${(status.phones/Math.max(status.done,1)*100).toFixed(1)}%)</p>
                 <p>🔒 Perfis privados: ${status.private}</p>`;
              document.getElementById('download-link').href = `/api/download/${jobId}`;
              document.getElementById('result-section').style.display = 'block';
              loadHistory();
            } else if (status.status === 'error') {
              clearInterval(pollInterval);
              btn.disabled = false;
              btn.textContent = 'Iniciar Extração';
              showError(status.message || 'Erro desconhecido');
            }
          } catch(err) {
            // Server might be restarting
            console.log('Poll error:', err);
          }
        }, 2000);
      } catch(err) {
        showError('Erro de conexão: ' + err.message);
      }
    });
    
    function showError(msg) {
      btn.disabled = false;
      btn.textContent = 'Iniciar Extração';
      document.getElementById('progress-section').style.display = 'block';
      document.getElementById('progress-label').textContent = '❌ Erro';
      document.getElementById('progress-pct').textContent = '0%';
      const el = document.getElementById('error-msg');
      el.textContent = msg;
      el.style.display = 'block';
    }
    
    async function loadHistory() {
      try {
        const res = await fetch('/api/history');
        const items = await res.json();
        if (items.length === 0) return;
        document.getElementById('history-section').style.display = 'block';
        document.getElementById('history-list').innerHTML = items.map(i => 
          `<div class="history-item"><span>@${i.username} — ${i.date}</span><a href="/api/download/${i.id}">CSV</a></div>`
        ).join('');
      } catch(e) {}
    }
    loadHistory();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.post("/api/scrape")
async def start_scrape(request: Request):
    body = await request.json()
    username = body.get("username", "").strip().lstrip("@")
    max_followers = body.get("max")
    delay = body.get("delay", SCRAPE_DELAY)
    
    if not username:
        return JSONResponse({"error": "Informe um @username"}, status_code=400)
    
    job_id = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{username}"
    jobs[job_id] = {
        "id": job_id,
        "username": username,
        "status": "starting",
        "done": 0,
        "total": 0,
        "phones": 0,
        "private": 0,
        "message": "Inicializando...",
        "date": datetime.utcnow().strftime("%d/%m %H:%M"),
    }
    
    asyncio.create_task(run_job(job_id, username, max_followers, float(delay)))
    return {"job_id": job_id}


async def run_job(job_id: str, username: str, max_followers: int | None, delay: float):
    job = jobs[job_id]
    
    try:
        async with async_playwright() as pw:
            job["message"] = "Lançando browser..."
            context = await get_authenticated_context(pw)
            
            job["message"] = f"Coletando seguidores de @{username}..."
            followers = await get_followers_list(context, username, max_followers=max_followers)
            
            if not followers:
                job["status"] = "error"
                job["message"] = "Nenhum seguidor encontrado. Verifique se o perfil é público."
                return
            
            job["total"] = len(followers)
            job["status"] = "running"
            job["message"] = f"Scrapeando {len(followers)} perfis..."
            
            phone_count = {"n": 0}
            private_count = {"n": 0}
            
            def on_progress(done, total, info):
                job["done"] = done
                if info.get("phone"):
                    phone_count["n"] += 1
                    job["phones"] = phone_count["n"]
                if info.get("private"):
                    private_count["n"] += 1
                    job["private"] = private_count["n"]
                job["message"] = f"Perfil {done}/{total}"
            
            results = await scrape_all_profiles(
                context, followers,
                delay=delay,
                max_workers=MAX_WORKERS,
                on_progress=on_progress,
            )
            
            await context.close()
        
        # Salva CSV
        df = pd.DataFrame(results)
        cols = [c for c in ["name", "username", "phone", "private", "bio_snippet"] if c in df.columns]
        df = df[cols]
        
        csv_file = OUTPUT_DIR / f"resultado_{username}.csv"
        df.to_csv(csv_file, index=False)
        
        df_phone = df[df["phone"].notna()] if "phone" in df.columns else pd.DataFrame()
        phone_file = OUTPUT_DIR / f"resultado_{username}_com_telefone.csv"
        df_phone.to_csv(phone_file, index=False)
        
        job["status"] = "completed"
        job["message"] = "Concluído!"
        job["done"] = len(df)
        job["csv_file"] = str(csv_file)
        job["phone_file"] = str(phone_file)
        
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Job {job_id} erro:\n{tb}")
        job["status"] = "error"
        # Mostra a última linha útil do traceback
        lines = tb.strip().split("\n")
        last_line = lines[-1] if lines else str(e)
        # Encontra a linha do "File ... line ..." anterior pra contexto
        file_line = ""
        for i in range(len(lines)-1, -1, -1):
            if "File "" in lines[i] or "File '" in lines[i]:
                file_line = lines[i].strip() + " → " + (lines[i+1].strip() if i+1 < len(lines) else "")
                break
        job["message"] = f"{last_line}\n\n{file_line}"


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job não encontrado"}, status_code=404)
    return {
        "id": job["id"],
        "username": job["username"],
        "status": job["status"],
        "done": job["done"],
        "total": job["total"],
        "phones": job["phones"],
        "private": job["private"],
        "message": job["message"],
    }


@app.get("/api/download/{job_id}")
async def download_csv(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job não encontrado"}, status_code=404)
    
    phone_file = Path(job.get("phone_file", ""))
    csv_file = Path(job.get("csv_file", ""))
    
    target = phone_file if phone_file.exists() else csv_file
    if not target.exists():
        return JSONResponse({"error": "Arquivo ainda não disponível"}, status_code=404)
    
    return FileResponse(target, media_type="text/csv", filename=target.name)


@app.get("/api/history")
async def history():
    items = sorted(jobs.values(), key=lambda j: j["id"], reverse=True)[:20]
    return [
        {"id": j["id"], "username": j["username"], "date": j["date"], "status": j["status"]}
        for j in items if j["status"] == "completed"
    ]
