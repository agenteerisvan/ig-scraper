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
    """Fecha popups comuns (cookies, notifications)."""
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


async def login_and_get_context(pw) -> tuple[Browser, BrowserContext]:
    """Recebe Playwright instance e retorna (browser, context) autenticados."""
    # 1) Tenta restaurar cookies salvos
    if os.path.exists(COOKIES_FILE):
        print("  Restaurando sessao dos cookies...")
        try:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await asyncio.sleep(3)
            await _close_overlays(page)
            if "accounts/login" not in page.url:
                print("  Sessao valida pelos cookies!")
                await page.close()
                return browser, context
            await page.close()
            await browser.close()
            print("  Cookie expirado, tentando login automatico...")
        except Exception as e:
            print(f"  Erro ao restaurar cookies: {e}")
            try:
                await browser.close()
            except Exception:
                pass

    # 2) Login automatico com username + password
    if not IG_PASSWORD:
        raise RuntimeError(
            "Sem IG_PASSWORD no environment e sem cookies validos. ",
            "Configure IG_PASSWORD nas Settings do Render.",
        )

    print(f"  Tentando login automatico: {IG_USERNAME}")
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()

    try:
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await asyncio.sleep(2)
        await page.fill('input[name="username"]', IG_USERNAME)
        await page.fill('input[name="password"]', IG_PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        current_url = page.url
        if "accounts/login" in current_url:
            raise RuntimeError(
                "Login falhou. Verifique usuario/senha ou resova checkpoint manualmente."
            )
        await _close_overlays(page)
        # Trata checkpoint
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
                raise RuntimeError(
                    "Instagram pediu confirmacao (SMS/email). Configure IG_2FA_CODE no Render."
                )
        # Salva cookies
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(await context.cookies(), f)
        await page.close()
        print("  Login automatico ok! Cookies salvos.")
        return browser, context
    except Exception:
        await browser.close()
        raise


async def get_profile_info(page: Page, username: str) -> dict:
    """Extrai nome, bio e telefone do perfil."""
    await page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    await asyncio.sleep(2)
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
    print(f"  Influencer: {info['name']}")
    return info


async def get_followers_list(context: BrowserContext, max_followers: int = 0) -> list[dict]:
    """Abre perfil atual, clica Seguidores, rola e coleta."""
    page = await context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    followers = []
    try:
        await _close_overlays(page)
        await asyncio.sleep(1)
        # Clica em Seguidores
        clicked = False
        for sel in ['span:has-text("Seguidores")', 'span:has-text("followers")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
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
                    if (t === 'Seguidores' || t === 'followers' || (t.match(/^\d+[KM]?$/) && s.closest('div'))) {
                        let p = s.parentElement;
                        for (let i = 0; i < 5; i++) {
                            if (p && p.onclick) break;
                            p = p ? p.parentElement : null;
                        }
                        (p || s.closest('div[class]')).click();
                        return;
                    }
                }
            }""")
        await asyncio.sleep(3)
        # Rola e coleta
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
                    if (['p','reel','reels','stories','explore','accounts','direct','home','about','reels','threads'].includes(user)) continue;
                    let name = '';
                    const spans = a.querySelectorAll('span[dir="auto"]');
                    if (spans.length > 0) name = spans[spans.length-1].textContent.trim();
                    if (!name) name = a.textContent.trim().split('\n')[0];
                    if (!name) name = user;
                    results.push({username: user, name: name});
                }
                return results;
            }""")
            existing = {f["username"] for f in followers}
            added = 0
            for item in items:
                if item["username"] not in existing and item.get("name"):
                    followers.append(item)
                    added += 1
            print(f"  Coletados: {len(followers)} (+{added})")
            if max_followers and len(followers) >= max_followers:
                break
            if added == 0:
                stall_count += 1
                if stall_count >= 4:
                    print("  Fim da lista detectado.")
                    break
            else:
                stall_count = 0
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(SCRAPE_DELAY)
            if len(followers) >= 10000:
                print("  Limite de seguranca (10k).")
                break
    finally:
        await page.close()
    return followers


async def scrape_all_profiles(context: BrowserContext, usernames: list[str], progress_cb=None) -> list[dict]:
    """Visita cada perfil e extrai nome + telefone."""
    results = []
    sem = asyncio.Semaphore(MAX_WORKERS)
    total = len(usernames)

    async def _scrape_one(username):
        result = {"username": username, "name": "", "bio": "", "phone": "", "status": "ok"}
        async with sem:
            page = await context.new_page()
            try:
                await page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(1.5)
                body_text = ""
                try:
                    body_text = await page.eval_on_selector("body", "el => el.innerText", timeout=5000)
                except Exception:
                    pass
                lower = body_text.lower()
                if "nao e uma conta" in lower or "page not found" in lower or "page isn't available" in lower:
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
                                if (t.length > 5 && t.length < 500 && !t.match(/seguidor|post|segui/i))
                                    return t;
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