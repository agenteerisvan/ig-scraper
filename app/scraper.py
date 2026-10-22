"""Scraper de seguidores do Instagram via Playwright (browser headless)."""
import asyncio
import json
import os
import re
import time
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .config import (
    IG_USERNAME, IG_PASSWORD, COOKIES_FILE, DATA_DIR,
    SCRAPE_DELAY, MAX_WORKERS, PAGE_TIMEOUT,
)
from .phone import extract_phone


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def _handle_checkpoint(page: Page) -> bool:
    """Tenta resolver checkpoint de login (2FA por email / confirmação)."""
    try:
        # Verifica se há uma tela de confirmação de login
        confirm_btn = page.locator("button:has-text('Confirm')").first
        if await confirm_btn.is_visible(timeout=3000):
            await confirm_btn.click()
            await asyncio.sleep(3)
            return True
        
        # 2FA por código enviado
        if_code = page.locator("input[autocomplete='one-time-code']").first
        if await if_code.is_visible(timeout=3000):
            print("⚠️  Instagram pediu código 2FA. Verifique seu email.")
            # Espera o usuário fornecer via env var (se configurado)
            code = os.getenv("IG_2FA_CODE", "")
            if code:
                await if_code.fill(code)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)
                return True
        return False
    except Exception:
        return False


async def _close_overlays(page: Page):
    """Fecha popups comuns do Instagram (cookies, notifications, etc)."""
    for selector in [
        'div[role="presentation"] button:has-text("Not now")',
        'div[role="presentation"] button:has-text("Agora não")',
        'div[role="presentation"] button:has-text("Allow")',
        'div[role="presentation"] button:has-text("Permitir")',
        '[data-testid="cookie-dialog"] [data-testid="cookie-dialog-accept-consent"]',
        'button:has-text("Deny all cookies")',
        'button:has-text("Recusar todos")',
        'button:has-text("No, thanks")',
        'button:has-text("Não, obrigado")',
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass


async def _login_interactive() -> tuple[Browser, BrowserContext]:
    """Login interativo — abre browser com GUI pra usuário digitar a senha."""
    print(f"\n🔐 Abrindo navegador para login manual em: {IG_USERNAME}")
    print("   → Digite sua senha na janela que abriu e aguarde até ver seu perfil.\n")
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        
        # Preenche só o username (senha deixa pro usuário digitar)
        await page.wait_for_selector('input[name="username"]', timeout=PAGE_TIMEOUT)
        await page.fill('input[name="username"]', IG_USERNAME)
        
        # Aguarda usuário digitar senha e clicar (até ver a home)
        await page.wait_for_selector(
            'a[href="/"] [class*="-home-"], main article, div[role="feed"]',
            timeout=120000,  # 2 min pros usuários
        )
        await asyncio.sleep(2)
        
        # Salva cookies
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(await context.cookies(), f)
        print("✅ Login feito! Cookies salvos.")
        
        return browser, context


async def get_authenticated_context() -> tuple[Browser, BrowserContext]:
    """Retorna um Browser + Context autenticado. Tenta cookies → 2FA → manual."""
    
    # 1) Tenta restaurar cookies
    if os.path.exists(COOKIES_FILE):
        print("📂 Restaurando sessão dos cookies...")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
                with open(COOKIES_FILE) as f:
                    await context.add_cookies(json.load(f))
                
                page = await context.new_page()
                await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(3)
                
                # Verifica se a página é a de login (session expirou)
                current_url = page.url
                title = await page.title()
                
                if "login" not in current_url.lower() and "Login" not in title:
                    print("✅ Sessão válida pelos cookies!")
                    return browser, context
                
                # Cookie inválido
                await browser.close()
                print("⚠️  Cookie expirado, tentando novamente...")
        except Exception as e:
            print(f"⚠️  Erro ao restaurar cookies: {e}")
    
    # 2) Tenta login headless com username + password
    if IG_PASSWORD:
        print("🔑 Tentando login automático (headless)...")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1280, "height": 900})
                page = await context.new_page()
                
                await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await page.fill('input[name="username"]', IG_USERNAME)
                await page.fill('input[name="password"]', IG_PASSWORD)
                await page.click('button[type="submit"]')
                
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(3)
                
                # Fecha overlays (popup de cookie, notificações)
                await _close_overlays(page)
                
                # Verifica se passou pra home
                if "accounts/login" not in page.url:
                    # Trata checkpoint
                    await _handle_checkpoint(page)
                    
                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(COOKIES_FILE, "w") as f:
                        json.dump(await context.cookies(), f)
                    
                    print("✅ Login automático ok! Cookies salvos.")
                    return browser, context
                else:
                    await browser.close()
                    print("⚠️  Login automático falhou (talvez senha errada ou checkpoint).")
        except Exception as e:
            print(f"⚠️  Erro no login automático: {e}")
    
    # 3) Fallback: login interativo (headful)
    return await _login_interactive()


# ---------------------------------------------------------------------------
# Dados do perfil (nome, bio, telefone)
# ---------------------------------------------------------------------------

async def get_profile_info(page: Page, username: str) -> dict:
    """Abre o perfil do influencer e extrai nome, bio e telefone."""
    url = f"https://www.instagram.com/{username}/"
    print(f"📄 Abriendo perfil: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    await asyncio.sleep(2)
    await _close_overlays(page)
    
    info: dict = {"username": username, "name": "", "bio": "", "phone": ""}
    
    # Nome: meta tag og:title ou h1
    try:
        title = await page.eval_on_selector("meta[property='og:title']", "el => el.content")
        if title:
            info["name"] = title.strip()
    except Exception:
        pass
    
    if not info["name"]:
        try:
            h1 = await page.eval_on_selector("h1", "el => el.textContent")
            info["name"] = h1.strip() if h1 else username
        except Exception:
            info["name"] = username
    
    # Bio: tenta múltiplas estratégias
    bio_text = ""
    
    # Estratégia 1: section com span
    for sel in [
        'section[style*="margin-top"] span[dir="auto"]',
        'article section span[dir="auto"]',
        'div[class*="-bio-"] span[dir="auto"]',
        'meta[property="og:description"]',
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                bio_text = await el.inner_text() if "meta" not in sel else await el.get_attribute("content")
                if bio_text:
                    break
        except Exception:
            continue
    
    # Estratégia 2: busca por texto perto de "seguidores"
    if not bio_text:
        try:
            bio_text = await page.evaluate("""() => {
                const spans = document.querySelectorAll('span[dir="auto"]');
                for (const s of spans) {
                    const t = s.textContent.trim();
                    if (t.length > 10 && t.length < 500 && !t.includes('seguidor') && !t.includes('posts'))
                        return t;
                }
                return '';
            }""")
        except Exception:
            pass
    
    info["bio"] = bio_text.strip()
    info["phone"] = extract_phone(info["name"] + " " + info["bio"])
    
    print(f"   Nome: {info['name']}")
    print(f"   Bio:  {info['bio'][:60]}{'...' if len(info['bio']) > 60 else ''}")
    if info["phone"]:
        print(f"   📱 Telefone: {info['phone']}")
    
    return info


# ---------------------------------------------------------------------------
# Lista de seguidores
# ---------------------------------------------------------------------------

async def _find_followers_dialog(page: Page):
    """Encontra o dialog/modal de seguidores usando múltiplas estratégias."""
    # Seletores possíveis para o botão "Seguidores"
    follower_selectors = [
        'div[role="button"]:has(span:text-is("Seguidores"))',
        'span:has-text("Seguidores") >> xpath=ancestor::div[contains(@class,"-profile-count")]',
        'article div:has(> span:has-text("Seguidores"))',
        'div[class*="-profile-"] div:has(span)',
    ]
    
    # Seletores possíveis para o modal aberto
    dialog_selectors = [
        'div[role="dialog"]',
        'div[class*="-follower-"]',
        'div[class*="-modal-"]',
        'div[class*="-carousel-"]',
        'div[aria-label*="Seguidores"]',
        'div[aria-label*="followers"]',
        'div[tabindex="-1"] div[class*="grid"]',
    ]
    
    # Click no botão de seguidores
    clicked = False
    for sel in follower_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                clicked = True
                break
        except Exception:
            continue
    
    if not clicked:
        # Fallback: clica no texto diretamente
        try:
            await page.click('span:has-text("Seguidores")', timeout=5000)
            clicked = True
        except Exception:
            pass
    
    if not clicked:
        raise TimeoutError("Não consegui clicar no botão 'Seguidores'. O layout pode ter mudado.")
    
    await asyncio.sleep(2)
    
    # Encontra o modal
    for dsel in dialog_selectors:
        try:
            dlg = page.locator(dsel).first
            if await dlg.is_visible(timeout=3000):
                return dlg
        except Exception:
            continue
    
    # Último recurso: qualquer div grande que apareceu
    try:
        dlg = page.locator('div[style*="overflow"] div[style*="grid"]').first
        if await dlg.is_visible(timeout=3000):
            return dlg
    except Exception:
        pass
    
    # Se nada funcionou, tenta usar o body inteiro como "container"
    print("   ⚠️  Dialog padrão não encontrado, usando fallback genérico...")
    return None  # Will use body-level approach


async def _extract_profiles_from_page(page: Page, max_items: int = 100) -> list[dict]:
    """Extrai dados de seguidores visíveis na página atual."""
    items = []
    
    # Tenta múltiplas estruturas de item
    item_selectors = [
        'a[href^="/"][role="link"]',
        'a[href*="/"][dir="auto"]',
        'div[dir="auto"] a[href]',
        'a[href] span[dir="auto"]',
    ]
    
    seen_usernames = set()
    
    for isel in item_selectors:
        try:
            anchors = page.locator(isel)
            count = await anchors.count()
            
            for i in range(min(count, max_items)):
                try:
                    a = anchors.nth(i)
                    href = await a.get_attribute("href", timeout=1000)
                    if not href or not href.startswith("/"):
                        continue
                    # Extrai username da URL
                    parts = [p for p in href.split("/") if p]
                    if len(parts) < 1:
                        continue
                    user = parts[0]
                    # Filtra links que não são perfis
                    if user in ("p", "reel", "stories", "explore", "accounts", "direct"):
                        continue
                    if user in seen_usernames:
                        continue
                    
                    # Obtém nome visível
                    name = ""
                    try:
                        spans = a.locator('span[dir="auto"]')
                        if await spans.count() > 0:
                            name = (await spans.first.inner_text(timeout=500)).strip()
                    except Exception:
                        pass
                    
                    if not name:
                        try:
                            name = (await a.inner_text(timeout=500)).strip().split("\n")[0]
                        except Exception:
                            pass
                    
                    if not name:
                        name = user
                    
                    seen_usernames.add(user)
                    items.append({"username": user, "name": name})
                except Exception:
                    continue
            
            if items:
                break  # Este seletor funcionou
            
        except Exception:
            continue
    
    return items


async def get_followers_list(context: BrowserContext, max_followers: int = 0) -> list[dict]:
    """Clica em Seguidores, rola o dialog até coletar todos (ou até max)."""
    page = await context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    
    followers: list[dict] = []
    
    try:
        dialog = await _find_followers_dialog(page)
        
        # Estratégia de rolagem
        prev_count = 0
        stall_count = 0
        
        while True:
            # Extrai itens visíveis
            new_items = await _extract_profiles_from_page(page, max_items=50)
            
            existing = {f["username"] for f in followers}
            added = 0
            for item in new_items:
                if item["username"] not in existing:
                    followers.append(item)
                    added += 1
            
            print(f"   👥 Coletados: {len(followers)} ({added} novos nesta rolagem)")
            
            if max_followers and len(followers) >= max_followers:
                break
            if len(followers) == 0:
                break
            
            # Rola o conteúdo
            if dialog:
                try:
                    await dialog.evaluate('el => el.scrollTop += el.scrollHeight * 0.8')
                except Exception:
                    await page.mouse.wheel(0, 800)
            else:
                await page.mouse.wheel(0, 800)
            
            await asyncio.sleep(SCRAPE_DELAY)
            
            # Detecta fim da lista
            if added == 0:
                stall_count += 1
                if stall_count >= 3:
                    print("   ✅ Fim da lista de seguidores detectado.")
                    break
            else:
                stall_count = 0
            
            # Limite de segurança
            if len(followers) >= 10000:
                print("   ⚠️  Limite de segurança (10k) atingido.")
                break
                
    finally:
        await page.close()
    
    return followers


# ---------------------------------------------------------------------------
# Scraping em massa
# ---------------------------------------------------------------------------

async def scrape_all_profiles(context: BrowserContext, usernames: list[str], progress_cb=None) -> list[dict]:
    """Visita cada perfil e extrai nome + telefone. Usa workers paralelos."""
    results: list[dict] = []
    sem = asyncio.Semaphore(MAX_WORKERS)
    done_count = 0
    total = len(usernames)
    
    async def _scrape_one(username: str) -> dict:
        nonlocal done_count
        result = {"username": username, "name": "", "bio": "", "phone": "", "status": "ok"}
        
        async with sem:
            page = await context.new_page()
            try:
                url = f"https://www.instagram.com/{username}/"
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(1.5)
                
                # Verifica se é privado ou não existe
                try:
                    body_text = await page.eval_on_selector("body", "el => el.innerText", timeout=5000)
                except Exception:
                    body_text = ""
                
                lower = body_text.lower()
                if "não é uma conta" in lower or "isn't a private account" in lower.lower() or "page not found" in lower or "não está disponível" in lower:
                    result["status"] = "not_found"
                elif "conta privada" in lower or "private account" in lower:
                    result["status"] = "private"
                elif "confirme sua senha" in lower or "log in" in lower.lower() or "to see this" in lower.lower():
                    result["status"] = "login_needed"
                else:
                    # Extrai dados
                    try:
                        title = await page.eval_on_selector("meta[property='og:title']", "el => el.content")
                        result["name"] = title.strip() if title else username
                    except Exception:
                        result["name"] = username
                    
                    # Bio
                    bio = ""
                    for sel in [
                        'section[style*="margin-top"] span[dir="auto"]',
                        'article section span[dir="auto"]',
                        'div[class*="-bio-"] span[dir="auto"]',
                    ]:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=1500):
                                bio = await el.inner_text()
                                break
                        except Exception:
                            continue
                    
                    if not bio:
                        try:
                            bio = await page.evaluate("""() => {
                                const spans = document.querySelectorAll('span[dir="auto"]');
                                for (const s of spans) {
                                    const t = s.textContent.trim();
                                    if (t.length > 5 && t.length < 500 && !t.includes('seguidor') && !t.includes('posts') && !t.includes('segui'))
                                        return t;
                                }
                                return '';
                            }""")
                        except Exception:
                            pass
                    
                    result["bio"] = bio.strip()
                    result["phone"] = extract_phone(result["name"] + " " + result["bio"])
                
            except Exception as e:
                result["status"] = "error"
                result["error"] = str(e)[:200]
            finally:
                done_count += 1
                if progress_cb and (done_count % 10 == 0 or done_count == total):
                    progress_cb(done_count, total, len([r for r in results if r.get("phone")]))
                await page.close()
        
        return result
    
    # Processa em lotes
    batch_size = 50
    for i in range(0, total, batch_size):
        batch = usernames[i:i+batch_size]
        tasks = [_scrape_one(u) for u in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        
        if progress_cb:
            progress_cb(len(results), total, len([r for r in results if r.get("phone")]))
    
    return results
