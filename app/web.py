"""API web do IG Follower Scraper - FastAPI."""
import asyncio
import csv
import json
import os
import traceback
import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse

from .config import OUTPUT_DIR, DATA_DIR, SCRAPE_DELAY, MAX_WORKERS, PAGE_TIMEOUT
from .scraper import (
    login_and_get_context,
    get_profile_info,
    get_followers_list,
    scrape_all_profiles,
)
from .phone import extract_phone

app = FastAPI(title="IG Follower Scraper")
JOBS: dict[str, dict] = {}

HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IG Follower Scraper</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f0f;color:#e2e2e2;min-height:100vh;display:flex;justify-content:center;padding:40px 16px}
.container{max-width:640px;width:100%}
h1{font-size:1.8rem;font-weight:700;margin-bottom:4px;display:flex;align-items:center;gap:10px}
h1 span{background:#6366f1;color:#fff;width:36px;height:36px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;font-size:1.2rem}
.subtitle{color:#9ca3af;font-size:.9rem;margin-bottom:32px}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:16px;padding:28px;margin-bottom:20px}
label{display:block;font-size:.85rem;color:#d1d5db;font-weight:500;margin-bottom:6px}
input{width:100%;padding:12px 16px;border-radius:10px;border:1px solid #374151;background:#111;color:#fff;font-size:1rem;outline:none;transition:border-color .2s}
input:focus{border-color:#6366f1}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
button{width:100%;margin-top:24px;padding:14px;border:none;border-radius:12px;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;font-size:1.05rem;font-weight:600;cursor:pointer;transition:opacity .2s}
button:hover{opacity:.9}
button:disabled{opacity:.5;cursor:not-allowed}
.progress-bar{height:6px;background:#333;border-radius:3px;overflow:hidden;margin-top:20px}
.progress-fill{height:100%;background:linear-gradient(90deg,#6366f1,#a78bfa);width:0%;transition:width .5s}
.stats{display:flex;gap:20px;margin-top:16px;font-size:.85rem;color:#9ca3af}
.stats b{color:#e2e2e2}
.error-box{margin-top:16px;padding:12px 16px;background:#1c1017;border:1px solid #7f1d1d;border-radius:10px;color:#fca5a5;font-size:.85rem;line-height:1.5;white-space:pre-wrap}
.result-box{margin-top:16px;padding:16px;background:#0f1a14;border:1px solid #166534;border-radius:10px}
.result-box a{color:#4ade80;text-decoration:none;font-weight:600}
.log-box{margin-top:16px;background:#111;border-radius:10px;padding:14px;font-family:'SF Mono','Fira Code',monospace;font-size:.75rem;color:#9ca3af;max-height:240px;overflow-y:auto;line-height:1.7}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:600;text-transform:uppercase}
.badge-running{background:#1e3a5f;color:#60a5fa}
.badge-done{background:#0f2d1a;color:#4ade80}
.badge-error{background:#2d1a1a;color:#fca5a5}
</style>
</head>
<body>
<div class="container">
<h1><span>&#9638;</span> IG Follower Scraper</h1>
<p class="subtitle">Digite o @ do influencer e extraia nome + telefone dos seguidores.</p>
<div class="card">
<form id="form">
<label>@ do Influencer</label>
<input type="text" id="username" placeholder="ex: heelder_" required>
<div class="row">
<div><label>Max. seguidores (opcional)</label><input type="number" id="max" placeholder="todos" min="1"></div>
<div><label>Pausa (seg)</label><input type="number" id="delay" value="3" min="1" max="15"></div>
</div>
<button type="submit" id="btn">Iniciar Extracao</button>
</form>
</div>
<div id="status-area" style="display:none">
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
<span id="status-badge" class="badge badge-running">Em andamento</span>
<span id="pct">0%</span>
</div>
<div class="progress-bar"><div class="progress-fill" id="bar"></div></div>
<div class="stats">
<span>&#128222; Telefones: <b id="s-phones">0</b></span>
<span>&#128274; Privados: <b id="s-private">0</b></span>
<span>&#9201; Tempo: <b id="s-time">0s</b></span>
</div>
<div class="log-box" id="log"></div>
<div id="error-area"></div>
<div id="result-area"></div>
</div>
</div>
</div>
<script>
let pollTimer=null,jobStart=null;
document.getElementById("form").addEventListener("submit",async(e)=>{
e.preventDefault();
const username=document.getElementById("username").value.replace(/^@/,"").trim();
const max=document.getElementById("max").value||0;
const delay=parseInt(document.getElementById("delay").value)||3;
const btn=document.getElementById("btn");
btn.disabled=true;btn.textContent="Enviando...";
document.getElementById("status-area").style.display="block";
document.getElementById("log").innerHTML="";
document.getElementById("error-area").innerHTML="";
document.getElementById("result-area").innerHTML="";
jobStart=Date.now();
try{
const res=await fetch("/api/scrape",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username,max_followers:max,delay})});
const data=await res.json();
if(data.job_id){pollStatus(data.job_id);}
else{showError(data.error||"Erro desconhecido");}
}catch(err){showError("Falha: "+err.message);}
btn.disabled=false;btn.textContent="Iniciar Extracao";
});
function pollStatus(id){
pollTimer=setInterval(async()=>{
try{
const r=await fetch("/api/status/"+id);
const d=await r.json();
updateUI(d);
if(d.status==="done"||d.status==="error"){clearInterval(pollTimer);if(d.status==="done")showResult(d);if(d.status==="error")showError(d.message);}
}catch(e){}
},2000);
}
function updateUI(d){
const pct=d.total?Math.round((d.progress/d.total)*100):0;
document.getElementById("pct").textContent=pct+"%";
document.getElementById("bar").style.width=pct+"%";
document.getElementById("s-phones").textContent=d.phones||0;
document.getElementById("s-private").textContent=d.private_count||0;
if(jobStart)document.getElementById("s-time").textContent=Math.floor((Date.now()-jobStart)/1000)+"s";
if(d.log)document.getElementById("log").textContent=d.log.slice(-2000);
const b=document.getElementById("status-badge");
if(d.status==="done"){b.textContent="Concluido";b.className="badge badge-done";}
if(d.status==="error"){b.textContent="Erro";b.className="badge badge-error";}
}
function showError(m){document.getElementById("error-area").innerHTML="<div class=\"error-box\">"+m.replace(/</g,"&lt;")+"</div>";}
function showResult(d){
let h="<div class=\"result-box\"><b>&#9989; Concluido!</b><br><br>";
if(d.csv_url)h+="<a href=\"" + d.csv_url + "\">Baixar CSV completo (" + d.total + " registros)</a>";
if(d.phone_csv_url)h+="<br><a href=\"" + d.phone_csv_url + "\">Baixar CSV somente telefone (" + d.phones + ")</a>";
h+="</div>";
document.getElementById("result-area").innerHTML=h;
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post("/api/scrape")
async def start_scrape(request: Request):
    data = await request.json()
    username = data.get("username", "").replace("@", "").strip()
    max_followers = int(data.get("max_followers") or 0)
    delay = float(data.get("delay") or SCRAPE_DELAY)
    if not username:
        return {"error": "Informe o @username."}
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "job_id": job_id, "username": username, "status": "running",
        "progress": 0, "total": 0, "phones": 0, "private_count": 0,
        "message": "Iniciando...", "csv_url": None, "phone_csv_url": None,
        "started_at": datetime.now().isoformat(), "log": "",
    }
    asyncio.create_task(run_job(job_id, username, max_followers, delay))
    return {"job_id": job_id}


async def run_job(job_id: str, username: str, max_followers: int, delay: float):
    """Task principal de scraping."""
    from playwright.async_api import async_playwright
    import app.scraper as scraper_mod
    
    job = JOBS[job_id]
    
    try:
        scraper_mod.SCRAPE_DELAY = delay
        
        job["log"] += f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando @{username}\n"
        
        async with async_playwright() as pw:
            job["message"] = "Fazendo login..."
            job["log"] += f"[{datetime.now().strftime('%H:%M:%S')}] Login...\n"
            
            browser, context = await login_and_get_context(pw)
            
            try:
                # Abre perfil do influencer
                job["message"] = f"Abrindo @{username}..."
                page = await context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT)
                
                info = await get_profile_info(page, username)
                job["log"] += f"  Influencer: {info['name']}\n"
                
                if not info["name"] or "not found" in info.get("name", "").lower():
                    raise RuntimeError(f"Perfil @{username} nao encontrado.")
                
                # Coleta seguidores
                job["message"] = "Coletando seguidores..."
                job["log"] += f"[{datetime.now().strftime('%H:%M:%S')}] Coletando seguidores...\n"
                
                followers = await get_followers_list(context, max_followers=max_followers)
                
                if not followers:
                    raise RuntimeError("Nenhum seguidor coletado.")
                
                usernames = [f["username"] for f in followers if f.get("username")]
                name_map = {f["username"]: f.get("name", "") for f in followers}
                
                job["total"] = len(usernames)
                job["log"] += f"  Total: {len(usernames)} seguidores\n"
                
                # Visita perfis
                def on_progress(done, total, phones):
                    job["progress"] = done
                    job["total"] = total
                    job["phones"] = phones
                
                results = await scrape_all_profiles(context, usernames, progress_cb=on_progress)
                
                # Aplica nomes da lista
                for r in results:
                    if r["username"] in name_map and name_map[r["username"]]:
                        r["name"] = name_map[r["username"]]
                
                privates = sum(1 for r in results if r.get("status") == "private")
                not_found = sum(1 for r in results if r.get("status") == "not_found")
                errors = sum(1 for r in results if r.get("status") == "error")
                
                # Gera CSVs
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                safe_name = username.replace("/", "_")
                
                csv_full = os.path.join(OUTPUT_DIR, f"resultado_{safe_name}.csv")
                with open(csv_full, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["Nome", "Telefone", "Bio", "Username"])
                    for r in results:
                        w.writerow([r["name"], r["phone"], r.get("bio",""), r["username"]])
                
                phones_list = [r for r in results if r.get("phone")]
                csv_phones = os.path.join(OUTPUT_DIR, f"resultado_{safe_name}_com_telefone.csv")
                with open(csv_phones, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["Nome", "Telefone", "Bio", "Username"])
                    for r in phones_list:
                        w.writerow([r["name"], r["phone"], r.get("bio",""), r["username"]])
                
                job["progress"] = job["total"]
                job["phones"] = len(phones_list)
                job["private_count"] = privates
                job["status"] = "done"
                job["message"] = f"{len(results)} visitados | {len(phones_list)} com telefone | {privates} privados | {errors} erros"
                job["csv_url"] = f"/api/download/{job_id}?file={safe_name}"
                job["phone_csv_url"] = f"/api/download/{job_id}?file={safe_name}_com_telefone"
                job["log"] += f"\n[{datetime.now().strftime('%H:%M:%S')}] CONCLUIDO: {job['message']}\n"
                print(f"DONE: {job['message']}")
                
            finally:
                await context.close()
                await browser.close()
                
    except Exception as e:
        tb = traceback.format_exc()
        job["status"] = "error"
        last_line = tb.strip().split("\n")[-1]
        file_line = ""
        for i in range(len(tb.strip().split("\n")) - 1, -1, -1):
            l = tb.strip().split("\n")[i]
            if "File" in l and "line" in l:
                file_line = l.strip()
                break
        job["message"] = f"{last_line}\n\n{file_line}"
        job["log"] += f"\nERRO: {last_line}\n"
        print(f"ERROR: {e}\n{tb}")


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"error": "Job nao encontrado"}
    return {k: job[k] for k in ("job_id","status","progress","total","phones","private_count","message","csv_url","phone_csv_url","log")}


@app.get("/api/download/{job_id}")
async def download(job_id: str, file: str = ""):
    path = os.path.join(OUTPUT_DIR, f"resultado_{file}.csv")
    if not os.path.exists(path):
        return {"error": "Arquivo nao encontrado"}
    return FileResponse(path=path, media_type="text/csv", filename=os.path.basename(path))
