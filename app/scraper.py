"""Scraper de seguidores do Instagram via Playwright (browser headless)."""
import asyncio
import json
import os
import time
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .config import (
    IG_USERNAME, IG_PASSWORD, COOKIES_FILE, DATA_DIR,
    SCRAPE_DELAY, MAX_WORKERS, PAGE_TIMEOUT,
)
from .phone import extract_phone

# Configuracoes para evitar deteccao de headless
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--window-size=1280,900",
]

async def _new_context(pw):
    """Cria browser + context anti-deteccao (headed via Xvfb)."""
    import os as _os
    display = _os.environ.get("DISPLAY", ":99")
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
            "--disable-blink-features=AutomationControlled",
        ],
        env={**_os.environ, "DISPLAY": display},
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=UA,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = {runtime: {}};
    """)
    return browser, context




async def _close_overlays(page: Page):
    selectors = [
        'div[role="presentation"] button:has-text("Not now")',
        'div[role="presentation"] button:has-text("Agora nao")',
        'div[role="presentation"] button:has-text("Allow")',
        'div[role="presentation"] button:has-text("Permitir")',
        '[data-testid="cookie-dialog"] [data-testid="cookie-dialog-accept-consent"]',
        'button:has-text("Deny all cookies")',
        'button:has-text("Recusar todos")',
        'button:has-text("No, thanks")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass


async def _describe_page(page: Page) -> str:
    try:
        title = await page.title()
        url = page.url
        body_text = ""
        try:
            body_text = await page.eval_on_selector("body", "el => el.innerText.substring(0, 200)")
        except Exception:
            try:
                body_text = await page.evaluate("() => document.body.innerText.substring(0, 200)")
            except Exception:
                body_text = "(nao consegui ler body)"
        return "URL=" + url + " | Title=" + title + " | Body=" + body_text[:150]
    except Exception as e:
        return "Erro ao descrever: " + str(e)


async def _fill_input(page: Page, name_attr: str, value: str) -> bool:
    """Preenche campo usando click + type (mais confiavel que fill)."""
    # Seletores especificos para cada campo
    if name_attr == "username":
        selectors = [
            'input[name="username"]',
            'input[type="text"]',
            'input[autocomplete="username"]',
            'input[placeholder*="mobile"]',
            'input[placeholder*="email"]',
        ]
    else:
        selectors = [
            'input[name="password"]',
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ]
    
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=5000):
                await el.click()
                await asyncio.sleep(0.3)
                await el.press_sequentially(value, delay=50)
                await asyncio.sleep(0.3)
                return True
        except Exception:
            continue
    
    # Ultimo recurso: evaluate direto
    try:
        sel_js = 'input[name="' + name_attr + '"]'
        ok = await page.evaluate("""(args) => {
            const inp = document.querySelector(args[0]);
            if (!inp) return false;
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, args[1]);
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }""", [sel_js, value])
        if ok:
            return True
    except Exception:
        pass
    
    return False


async def login_and_get_context(pw) -> tuple[Browser, BrowserContext]:
    """Login no Instagram via headed+Xvfb. Retorna (browser, context)."""
    
    # --- 1) Tenta cookies salvos ---
    if os.path.exists(COOKIES_FILE):
        print("  Restaurando cookies...")
        try:
            browser, context = await _new_context(pw)
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            page = await context.new_page()
            try:
                await page.goto("https://www.instagram.com/", timeout=45000)
                await asyncio.sleep(5)
            except Exception:
                pass
            
            current_url = ""
            try:
                current_url = page.url
            except Exception:
                pass
            
            if current_url and "accounts/login" not in current_url and "login" not in current_url:
                print(f"  Sessao valida! ({current_url[:60]})")
                await page.close()
                return browser, context
            else:
                print(f"  Cookie expirado. URL={current_url[:80]}")
            try:
                await browser.close()
            except Exception:
                pass
        except Exception as e:
            print(f"  Erro cookies: {str(e)[:100]}")
    
    # --- 2) Login automatico ---
    if not IG_PASSWORD:
        raise RuntimeError("Sem IG_PASSWORD e sem cookies validos.")
    
    last_error = "desconhecido"
    
    for attempt in range(1, 4):
        print(f"  [{attempt}/3] Login @{IG_USERNAME}...")
        browser = None
        try:
            browser, context = await _new_context(pw)
            page = await context.new_page()
            
            # Navega - ignora erros de navegacao (redirects do Instagram)
            try:
                await page.goto("https://www.instagram.com/accounts/login/", timeout=45000)
            except Exception:
                # Reload, ERR_ABORTED, net::ERR... tudo ok, a pagina provavelmente ja mudou
                await asyncio.sleep(2)
            
            # Se redirecionou pra home (ja logado), pronto
            try:
                cur = page.url
                if "accounts/login" not in cur and "checkpoint" not in cur:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(COOKIES_FILE, "w") as f:
                        json.dump(await context.cookies(), f)
                    print("  Ja estava logado!")
                    await page.close()
                    return browser, context
            except Exception:
                pass
            
            # Espera ate 40s pelo campo username APARECER
            input_found = False
            for sel in ['input[name="username"]', 'input[type="text"]:visible']:
                try:
                    await page.wait_for_selector(sel, state="visible", timeout=40000)
                    input_found = True
                    break
                except Exception:
                    continue
            
            if not input_found:
                # Log do que a pagina mostra
                info = "desconhecido"
                try:
                    t = await page.title()
                    u = page.url
                    b = await page.evaluate("() => (document.body || {}).innerText || ''") or ''
                    info = f"URL={u} Title='{t}' Body='{b[:200]}'"
                except Exception as de:
                    info = f"desc falhou: {str(de)[:80]}"
                
                last_error = f"Form nao apareceu. {info}"
                print(f"  [{attempt}] {last_error}")
                
                # Tenta screenshot p/ debug
                try:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    await page.screenshot(path=os.path.join(DATA_DIR, f"debug_{attempt}.png"))
                except Exception:
                    pass
                continue
            
            # Fecha overlays
            try:
                await _close_overlays(page)
            except Exception:
                pass
            
            # Preenche username
            try:
                inp = page.locator('input[name="username"]').first
                await inp.click()
                await asyncio.sleep(0.3)
                await inp.press_sequentially(IG_USERNAME, delay=50)
            except Exception:
                inp = page.locator('input[type="text"]').first
                await inp.click()
                await asyncio.sleep(0.3)
                await inp.press_sequentially(IG_USERNAME, delay=50)
            
            await asyncio.sleep(0.5)
            
            # Preenche password
            try:
                ppass = page.locator('input[name="password"]').first
                await ppass.click()
                await asyncio.sleep(0.3)
                await ppass.press_sequentially(IG_PASSWORD, delay=50)
            except Exception as pe:
                last_error = f"Campo senha: {str(pe)[:100]}"
                print(f"  [{attempt}] {last_error}")
                continue
            
            await asyncio.sleep(0.5)
            
            # Submit
            try:
                btn = page.locator('button[type="submit"]').first
                await btn.click(timeout=5000)
            except Exception:
                await page.keyboard.press("Enter")
            
            # Aguarda redirecionamento (sucesso) ou fica na mesma pagina (erro)
            login_done = False
            for i in range(12):  # 12 * 3s = 36s max
                await asyncio.sleep(3)
                try:
                    cur = page.url
                    if "accounts/login" not in cur and "login" not in cur.lower():
                        login_done = True
                        break
                except Exception:
                    continue
            
            if not login_done:
                body_txt = ""
                try:
                    body_txt = await page.evaluate("() => (document.body||{}).innerText||''") or ''
                except Exception:
                    pass
                last_error = f"Submit nao funcionou. Body='{body_txt[:150]}'"
                print(f"  [{attempt}] {last_error}")
                continue
            
            # Sucesso!
            await asyncio.sleep(3)
            try:
                await _close_overlays(page)
            except Exception:
                pass
            
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(COOKIES_FILE, "w") as f:
                json.dump(await context.cookies(), f)
            
            print("  Login OK! Cookies salvos.")
            await page.close()
            return browser, context
        
        except Exception as e:
            last_error = str(e)[:200]
            print(f"  [{attempt}] Excecao: {last_error}")
        
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            await asyncio.sleep(2)
    
    raise RuntimeError(f"Login falhou apos 3 tentativas. Ultimo: {last_error}")


async def get_profile_info(page: Page, username: str) -> dict:
    await page.goto("https://www.instagram.com/" + username + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    await asyncio.sleep(3)
    await _close_overlays(page)
    info = {"username": username, "name": "", "bio": "", "phone": ""}
    try:
        title = await page.eval_on_selector("meta[property='og:title']", "el => el.content")
        info["name"] = title.strip() if title else username
    except Exception:
        info["name"] = username
    bio_text = ""
    try:
        bio_text = await page.evaluate("""() => {
            const spans = document.querySelectorAll('span[dir="auto"]');
            for (const s of spans) {
                const t = s.textContent.trim();
                if (t.length > 10 && t.length < 500 && !t.match(/seguidor|post|segui/i))
                    return t;
            }
            return '';
        }""") or ""
    except Exception:
        pass
    info["bio"] = bio_text.strip() if bio_text else ""
    info["phone"] = extract_phone(info["name"] + " " + info["bio"])
    nm = info["name"]
    print("  Influencer: " + nm)
    return info


async def get_followers_list(context: BrowserContext, max_followers: int = 0) -> list[dict]:
    """Clica Seguidores no perfil atual e coleta a lista."""
    page = await context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    followers = []
    
    try:
        await asyncio.sleep(2)
        
        # --- Estrategia 1: Clica direto em span com texto "Seguidores" ---
        clicked = False
        for sel in [
            'span:has-text("Seguidores")',
            'span:has-text("seguidores")', 
            'span:has-text("followers")',
        ]:
            try:
                els = page.locator(sel)
                count = await els.count()
                for i in range(count):
                    el = els.nth(i)
                    if await el.is_visible():
                        txt = (await el.inner_text()).strip()
                        # Confere que é o botao de seguidores (proximo a um numero)
                        print(f"  Encontrou candidato: '{txt}' ({sel})")
                        await el.click()
                        clicked = True
                        break
                if clicked:
                    break
            except Exception:
                continue
        
        # --- Estrategia 2: JS generico - procura div com numero + palavra seguidores ---
        if not clicked:
            print("  Tentando click via JS...")
            result = await page.evaluate("""() => {
                // Procura spans com texto exato "Seguidores" ou numeros
                const allSpans = document.querySelectorAll('span');
                let followersSpan = null;
                
                for (const s of allSpans) {
                    const t = s.textContent.trim();
                    if (t === 'Seguidores' || t === 'seguidores' || t === 'followers') {
                        followersSpan = s;
                        break;
                    }
                }
                
                if (!followersSpan) return 'nao_encontrado';
                
                // Subi ate achar o container clicavel
                let el = followersSpan;
                for (let i = 0; i < 8; i++) {
                    if (!el.parentElement) break;
                    el = el.parentElement;
                    // Se tem cursor:pointer ou e um div com padding, provavelmente e o botao
                    const style = window.getComputedStyle(el);
                    if (style.cursor === 'pointer') break;
                    if (el.className && el.className.includes && el.className.includes('profile')) break;
                }
                
                el.click();
                return 'clicado: ' + el.tagName + '.' + (el.className || '').substring(0, 50);
            }""")
            print(f"  JS result: {result}")
        
        # Aguarda modal abrir
        await asyncio.sleep(4)
        
        # --- Diagnostico: o que apareceu na pagina? ---
        try:
            body_snippet = await page.evaluate("() => document.body.innerText.substring(0, 500)")
            print(f"  Pagina apos click (snippet): {body_snippet[:200]}")
        except Exception:
            pass
        
        # --- Extrai seguidores: procura TODOS os links /username ---
        stall_count = 0
        
        while True:
            items = await page.evaluate("""() => {
                const results = [];
                const seen = new Set();
                
                // Estrategia A: Links diretos a perfis
                const allLinks = document.querySelectorAll('a[href]');
                for (const a of allLinks) {
                    let href = a.getAttribute('href') || '';
                    
                    // Formato: /username/ ou /username
                    const m = href.match(/^\/([^\/?#]+)\/?$/);
                    if (!m) continue;
                    
                    const user = m[1];
                    
                    // Ignora rotas nao-perfil
                    if (['p','reel','reels','stories','explore','accounts','direct','home',
                        'about','threads','ads','developer','legal','help','creator',
                        'developers','s','topics','tags'].includes(user)) continue;
                    if (user.startsWith('_u')) continue;
                    if (user.length < 2) continue;
                    if (seen.has(user)) continue;
                    seen.add(user);
                    
                    // Nome: ultimo span[dir="auto"] dentro do link
                    let name = '';
                    const spans = a.querySelectorAll('span[dir="auto"]');
                    if (spans.length > 0) {
                        name = spans[spans.length - 1].textContent.trim();
                    }
                    if (!name) {
                        const allText = a.textContent.trim().split('\n').filter(Boolean);
                        if (allText.length > 0) name = allText[allText.length - 1];
                    }
                    if (!name) name = user;
                    
                    results.push({username: user, name: name});
                }
                
                return results;
            }""")
            
            existing = set(f["username"] for f in followers)
            added = 0
            for item in items:
                u = item.get("username", "")
                n = item.get("name", "")
                if u and n and u not in existing:
                    followers.append(item)
                    added += 1
            
            print(f"  Iteracao: {len(followers)} total (+{added} novos) | itens na pagina: {len(items)}")
            
            if max_followers and len(followers) >= max_followers:
                break
            
            if len(items) == 0 and stall_count >= 3:
                print("  Nenhum link encontrado na pagina.")
                break
            
            if added == 0:
                stall_count += 1
                if stall_count >= 5:
                    print("  Sem novos itens - fim da lista.")
                    break
            else:
                stall_count = 0
            
            # Rola a pagina (o modal de seguidores geralmente tem scroll interno)
            # Tenta rolar um container com overflow
            scrolled = await page.evaluate("""() => {
                // Procura o container scrollavel
                const allDivs = document.querySelectorAll('div');
                for (const d of allDivs) {
                    const style = window.getComputedStyle(d);
                    if ((style.overflowY === 'scroll' || style.overflowY === 'auto') && 
                        d.scrollHeight > d.clientHeight + 50) {
                        d.scrollTop += d.clientHeight * 0.8;
                        return true;
                    }
                }
                // Fallback: rola a window
                window.scrollBy(0, 1000);
                return true;
            }""")
            
            if not scrolled:
                await page.mouse.wheel(0, 1200)
            
            await asyncio.sleep(SCRAPE_DELAY)
            
            if len(followers) >= 10000:
                break
    
    finally:
        await page.close()
    
    return followers


async def scrape_all_profiles(context: BrowserContext, usernames: list[str], progress_cb=None) -> list[dict]:
    results = []
    sem = asyncio.Semaphore(MAX_WORKERS)
    total = len(usernames)
    
    async def _scrape_one(username):
        result = {"username": username, "name": "", "bio": "", "phone": "", "status": "ok"}
        async with sem:
            page = await context.new_page()
            try:
                await page.goto("https://www.instagram.com/" + username + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(1.5)
                body_text = ""
                try:
                    body_text = await page.eval_on_selector("body", "el => el.innerText", timeout=5000)
                except Exception:
                    pass
                lower = body_text.lower()
                if "nao e uma conta" in lower or "page not found" in lower or "isn't available" in lower:
                    result["status"] = "not_found"
                elif "conta privada" in lower or "private account" in lower or "to see this profile" in lower:
                    result["status"] = "private"
                else:
                    try:
                        title = await page.eval_on_selector("meta[property='og:title']", "el => el.content")
                        result["name"] = title.strip() if title else username
                    except Exception:
                        result["name"] = username
                    try:
                        bio = await page.evaluate("""() => {
                            const spans = document.querySelectorAll('span[dir="auto"]');
                            for (const s of spans) {
                                const t = s.textContent.trim();
                                if (t.length > 5 && t.length < 500 && !t.match(/seguidor|post|segui/i)) return t;
                            }
                            return '';
                        }""") or ""
                        result["bio"] = bio.strip()
                    except Exception:
                        pass
                    result["phone"] = extract_phone(result["name"] + " " + result["bio"])
            except Exception as e:
                result["status"] = "error"
                result["error"] = str(e)[:200]
            finally:
                await page.close()
        return result
    
    batch_size = 50
    done = 0
    for i in range(0, total, batch_size):
        batch = usernames[i:i+batch_size]
        tasks = [_scrape_one(u) for u in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        done += len(batch)
        if progress_cb:
            phones = sum(1 for r in results if r.get("phone"))
            progress_cb(done, total, phones)
    return results