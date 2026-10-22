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
    sel = "input[name=\"" + name_attr + "\"]"
    try:
        el = page.locator(sel).first
        if await el.is_visible(timeout=8000):
            await el.fill(value)
            return True
    except Exception:
        pass
    try:
        filled = await page.evaluate("""(args) => {
            const inp = document.querySelector(args[0]);
            if (inp) { inp.value = args[1]; inp.dispatchEvent(new Event('input', {bubbles:true})); return true; }
            return false;
        }""", [sel, value])
        if filled:
            return True
    except Exception:
        pass
    # Fallback: primeiro input visivel
    try:
        inputs = page.locator("input[type='text'], input[type='email'], input[type='tel']")
        count = await inputs.count()
        for i in range(count):
            inp = inputs.nth(i)
            if await inp.is_visible():
                await inp.fill(value)
                return True
    except Exception:
        pass
    return False


async def login_and_get_context(pw) -> tuple[Browser, BrowserContext]:
    """Recebe Playwright instance e retorna (browser, context) autenticados."""
    # 1) Cookies salvos
    if os.path.exists(COOKIES_FILE):
        print("  Restaurando cookies...")
        browser = None
        try:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            await _close_overlays(page)
            if "accounts/login" not in page.url:
                print("  Sessao valida!")
                await page.close()
                return browser, context
            await page.close()
            await browser.close()
            browser = None
            print("  Cookie expirado.")
        except Exception as e:
            print("  Erro cookies: " + str(e))
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
    
    # 2) Login automatico
    if not IG_PASSWORD:
        raise RuntimeError("Sem IG_PASSWORD e sem cookies validos. Configure nas Settings do Render.")
    
    print("  Login automatico: @" + IG_USERNAME)
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    page.set_default_timeout(30000)
    
    try:
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        await _close_overlays(page)
        
        # Ja logado?
        if "accounts/login" not in page.url:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(COOKIES_FILE, "w") as f:
                json.dump(await context.cookies(), f)
            await page.close()
            print("  Ja estava logado!")
            return browser, context
        
        # Preenche login
        user_ok = await _fill_input(page, "username", IG_USERNAME)
        if not user_ok:
            desc = await _describe_page(page)
            raise RuntimeError("Campo usuario nao encontrado. Pagina: " + desc)
        
        pass_ok = await _fill_input(page, "password", IG_PASSWORD)
        if not pass_ok:
            desc = await _describe_page(page)
            raise RuntimeError("Campo senha nao encontrado. Pagina: " + desc)
        
        
        # Submit
        submitted = False
        for sel in ['button[type="submit"]', 'button:has-text("Log in")', 'button:has-text("Login")', 'button:has-text("Entrar")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted:
            await page.keyboard.press("Enter")
        
        await asyncio.sleep(5)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(3)
        
        current_url = page.url
        if "accounts/login" in current_url or "challenge" in current_url:
            desc = await _describe_page(page)
            raise RuntimeError("Login falhou/checkpoint. " + desc)
        
        await _close_overlays(page)
        
        # Checkpoint/2FA
        if "checkpoint" in page.url or "two-factor" in page.url:
            code = os.getenv("IG_2FA_CODE", "")
            if code:
                try:
                    inp = page.locator("input[autocomplete='one-time-code']").first
                    await inp.fill(code)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                except Exception:
                    pass
            else:
                desc = await _describe_page(page)
                raise RuntimeError("Checkpoint/SMS necessario. Configure IG_2FA_CODE. " + desc)
        
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(await context.cookies(), f)
        await page.close()
        print("  Login ok! Cookies salvos.")
        return browser, context
    except Exception:
        await browser.close()
        raise


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
    page = await context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    followers = []
    try:
        await _close_overlays(page)
        await asyncio.sleep(2)
        clicked = False
        for sel in ['span:has-text("Seguidores")', 'span:has-text("followers")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=4000):
                    await btn.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            await page.evaluate("""() => {
                const spans = document.querySelectorAll('span');
                for (const s of spans) {
                    const t = s.textContent.trim();
                    if (t === 'Seguidores' || t === 'followers') {
                        let p = s.parentElement;
                        for (let i = 0; i < 6; i++) { if (!p) break; p = p.parentElement; }
                        (p || s).click(); return;
                    }
                }
            }""")
        await asyncio.sleep(4)
        
        stall_count = 0
        while True:
            items = await page.evaluate("""() => {
                const results = [];
                const links = document.querySelectorAll('a[href^="/"]');
                for (const a of links) {
                    const href = a.getAttribute('href');
                    if (!href) continue;
                    const parts = href.split('/').filter(Boolean);
                    if (parts.length !== 1) continue;
                    const user = parts[0];
                    if (['p','reel','reels','stories','explore','accounts','direct','home','about','threads','ads'].includes(user)) continue;
                    let name = '';
                    const spans = a.querySelectorAll('span[dir="auto"]');
                    if (spans.length > 0) name = spans[spans.length-1].textContent.trim();
                    if (!name) name = user;
                    results.push({username: user, name: name});
                }
                return results;
            }""")
            existing = set(f["username"] for f in followers)
            added = 0
            for item in items:
                u = item.get("username","")
                n = item.get("name","")
                if u and n and u not in existing:
                    followers.append(item)
                    added += 1
            print("  Coletados: " + str(len(followers)) + " (+" + str(added) + ")")
            if max_followers and len(followers) >= max_followers:
                break
            if added == 0:
                stall_count += 1
                if stall_count >= 5:
                    break
            else:
                stall_count = 0
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