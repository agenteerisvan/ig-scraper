"""Core do scraper: login, lista de seguidores, perfil individual."""
import asyncio
import json
import logging
import traceback
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .config import (
    IG_USERNAME, IG_PASSWORD, SCRAPE_DELAY, MAX_WORKERS,
    PAGE_TIMEOUT, COOKIES_FILE,
)
from .phone import extract_phone

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


# ──────────────────────────────────────────────
#  SESSÃO / LOGIN
# ──────────────────────────────────────────────

async def _dismiss_popup(page: Page) -> None:
    """Fecha popups comuns do Instagram (Not now, cookies, etc)."""
    for selector in [
        'button:has-text("Not now")',
        'button:has-text("Agora n\xe3o")',
        'button:has-text("\xd8\x62\xcc\xac\xb0\x80 \xcc\x87\xcc\xb0")',  # fallback
        '[aria-label="Dismiss"]',
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(500)
                break
        except Exception:
            pass


async def _ensure_logged_in(page: Page) -> bool:
    """Verifica se estamos logados olhando elementos da home."""
    try:
        # Se aparecer link de login, não estamos logados
        login_link = page.locator('a[href="/accounts/login/"]').first
        if await login_link.is_visible(timeout=3000):
            return False
        # Se aparecer o grid de posts ou sidebar, estamos ok
        home_el = page.locator('nav[aria-label="Feed"], div[data-test-id="react-root"] >> nth=0')
        if await home_el.count() > 0:
            return True
        return True  # Sem erro, assume logado
    except Exception:
        return True


async def login_ig(pw) -> BrowserContext:
    """Faz login no Instagram e retorna um context autenticado."""
    logger.info("Lançando browser...")
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="pt-BR",
    )
    await context.set_default_timeout(PAGE_TIMEOUT)
    page = await context.new_page()

    logger.info("Navegando para instagram.com/accounts/login...")
    await page.goto(
        "https://www.instagram.com/accounts/login/",
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    await page.wait_for_timeout(3000)

    # Verifica se já estava logado (cookies persistidos no browser)
    if await _ensure_logged_in(page):
        logger.info("Já autenticado (sessão existente no browser).")
        await _dismiss_popup(page)
        cookies = await context.cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)
        return context

    # Preenche formulário de login
    logger.info("Preencrendo credenciais...")
    await page.wait_for_selector('input[name="username"]', timeout=10000)
    await page.fill('input[name="username"]', IG_USERNAME)
    await page.wait_for_timeout(500)
    await page.fill('input[name="password"]', IG_PASSWORD)
    await page.wait_for_timeout(500)

    logger.info("Clicando em Login...")
    await page.click('button[type="submit"]')

    # Aguarda navegação pós-login
    await page.wait_for_load_state("networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    # Fecha popup "Not now" se aparecer
    await _dismiss_popup(page)

    # Verifica se funcionou
    current_url = page.url
    logger.info(f"URL após login: {current_url}")
    
    if "login" in current_url or "challenge" in current_url or "checkpoint" in current_url:
        # Pode ter dado challenge (verificação por SMS/código)
        page_content = await page.content()
        raise RuntimeError(
            f"Login incompleto. URL: {current_url}. "
            "Pode ser necessário verificação extra (SMS/código). "
            "Tente novamente ou troque de conta."
        )

    cookies = await context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logger.info(f"Login OK. Cookies salvos ({len(cookies)} itens).")
    return context


async def restore_from_cookies(pw) -> BrowserContext | None:
    """Tenta restaurar sessão dos cookies salvos. Retorna None se falhar."""
    if not COOKIES_FILE.exists():
        return None
    
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="pt-BR",
        )
        await context.set_default_timeout(PAGE_TIMEOUT)
        
        with open(COOKIES_FILE) as f:
            cookies = json.load(f)
        
        # Formata cookies pro formato que Playwright espera
        formatted_cookies = []
        for c in cookies:
            formatted_cookies.append({
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ".instagram.com"),
                "path": c.get("path", "/"),
                "secure": c.get("secure", True),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "Lax"),
            })
        await context.add_cookies(formatted_cookies)
        
        page = await context.new_page()
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await page.wait_for_timeout(3000)
        
        if await _ensure_logged_in(page):
            logger.info("Sessão restaurada de cookies.")
            return context
        
        # Não logado → fecha e retorna None
        await browser.close()
        return None
        
    except Exception as e:
        logger.warning(f"Falha ao restaurar cookies: {e}")
        try:
            await browser.close()
        except:
            pass
        return None


async def get_authenticated_context(pw) -> BrowserContext:
    """Retorna contexto autenticado (restaura cookies ou faz login)."""
    # Tenta restaurar primeiro
    context = await restore_from_cookies(pw)
    if context:
        return context
    
    # Senão, faz login
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink(missing_ok=True)
    return await login_ig(pw)


# ──────────────────────────────────────────────
#  LISTA DE SEGUIDORES
# ──────────────────────────────────────────────

async def get_followers_list(context: BrowserContext, username: str, max_followers: int | None = None) -> list[str]:
    """Abre perfil, clica em seguidores, coleta todos via scroll."""
    page = await context.new_page()
    profile_url = f"https://www.instagram.com/{username.lstrip('@')}/"
    
    logger.info(f"Abrindo perfil: {profile_url}")
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    await page.wait_for_timeout(3000)
    await _dismiss_popup(page)

    # Tenta clicar no link de seguidores
    clicked = False
    for selector in [
        'a[href$="/followers"] span',
        'span:has-text("seguidores")',
        'span:has-text("followers")',
        'a[href*="/followers"]',
    ]:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=3000):
                await el.click()
                clicked = True
                break
        except Exception:
            continue
    
    if not clicked:
        # Fallback: pega o número e tenta por texto
        logger.warning("Não encontrou link de seguidores pelo seletor. Tentando texto...")
        await page.locator('text=/\\d+\\s*(seguidores|followers)/i').first.click(timeout=5000)
        clicked = True
    
    if not clicked:
        raise RuntimeError("Não conseguiu abrir a lista de seguidores. Perfil pode estar privado ou bloqueado.")

    # Aguarda dialog abrir
    await page.wait_for_selector('[role="dialog"]', timeout=10000)
    await page.wait_for_timeout(2000)

    followers: list[str] = []
    seen: set[str] = set()
    stagnant_count = 0

    while True:
        # Coleta usernames dos links visíveis no dialog
        links = page.locator('[role="dialog"] a[href^="/"]:not([href="/"]):not([href*="/explore"])')
        count = await links.count()
        
        for i in range(count):
            try:
                href = await links.nth(i).get_attribute("href")
                if href:
                    tag = href.strip("/").split("/")[0].split("?")[0]
                    if tag and tag.lower() != username.lower() and tag not in seen and len(tag) > 1:
                        seen.add(tag)
                        followers.append(tag)
            except Exception:
                continue
        
        # Check max
        if max_followers and len(followers) >= max_followers:
            followers = followers[:max_followers]
            break
        
        # Scroll dentro do dialog
        before = len(followers)
        try:
            dialog_el = page.locator('[role="dialog"]')
            await dialog_el.evaluate('el => el.scrollTo(0, el.scrollHeight)')
        except Exception:
            await page.mouse.wheel(0, 1500)
        
        await page.wait_for_timeout(2000)
        
        after = len(followers)
        if after == before:
            stagnant_count += 1
            if stagnant_count >= 5:
                break
        else:
            stagnant_count = 0
        
        if len(followers) % 500 == 0 and len(followers) > 0:
            logger.info(f"  ... {len(followers)} seguidores coletados")

    await page.close()
    logger.info(f"✅ {len(followers)} seguidores encontrados para @{username}")
    return followers


# ──────────────────────────────────────────────
#  PERFIL INDIVIDUAL
# ──────────────────────────────────────────────

async def get_profile_info(context: BrowserContext, username: str) -> dict:
    """Abre um perfil público e extrai nome + bio + telefone."""
    page = await context.new_page()
    url = f"https://www.instagram.com/{username}/"
    result = {
        "username": username,
        "name": "",
        "phone": None,
        "private": False,
        "bio_snippet": "",
        "error": None,
    }

    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await page.wait_for_timeout(2000)

        # Detecta perfil privado
        body_text = ""
        try:
            body_text = await page.evaluate("document.body.innerText || ''")
        except Exception:
            pass
        
        is_private = bool(
            "conta é privada" in body_text.lower() or
            "this account is private" in body_text.lower()
        )
        
        if is_private:
            result["private"] = True
            # Tenta pegar pelo menos o nome
            try:
                h1 = page.locator("h1").first
                if await h1.count() > 0:
                    result["name"] = (await h1.inner_text()).strip()
            except Exception:
                pass
        
        else:
            # Nome
            try:
                name_el = page.locator("h1").first
                if await name_el.count() > 0:
                    result["name"] = (await name_el.inner_text()).strip()
            except Exception:
                pass
            
            # Bio — múltiplas estratégias
            bio_text = ""
            
            # Estratégia 1: meta og:description
            try:
                meta = page.locator('meta[property="og:description"]')
                if await meta.count() > 0:
                    content = await meta.get_attribute("content")
                    if content and " - " in content:
                        bio_text = content.split(" - ", 1)[1]
                    elif content:
                        bio_text = content
            except Exception:
                pass
            
            # Estratégia 2: seletor direto
            if not bio_text:
                for sel in ['div[style*="white-space"]', '[data-test-id*="bio"]']:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            txt = (await el.inner_text()).strip()
                            if txt:
                                bio_text = txt
                                break
                    except Exception:
                        continue
            
            # Estratégia 3: texto do corpo (busca seção entre nome e botões)
            if not bio_text and body_text:
                lines = body_text.split("\n")
                # Bio geralmente aparece nas linhas após o username
                in_bio = False
                bio_lines = []
                for line in lines:
                    stripped = line.strip()
                    if stripped == username:
                        in_bio = True
                        continue
                    if in_bio:
                        if stripped in ("Seguir", "Follow", "Following", ""):
                            break
                        if stripped and not stripped.startswith("Follow"):
                            bio_lines.append(stripped)
                bio_text = " ".join(bio_lines)
            
            result["bio_snippet"] = bio_text[:200]
            result["phone"] = extract_phone(bio_text)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    finally:
        await page.close()

    return result


# ──────────────────────────────────────────────
#  ORQUESTRAÇÃO
# ──────────────────────────────────────────────

async def scrape_all_profiles(
    context: BrowserContext,
    usernames: list[str],
    delay: float = None,
    max_workers: int = None,
    on_progress=None,
) -> list[dict]:
    """Processa perfis com controle de concorrência."""
    delay = delay or SCRAPE_DELAY
    max_workers = max_workers or MAX_WORKERS
    semaphore = asyncio.Semaphore(max_workers)
    total = len(usernames)
    done = 0
    results = []

    async def process_one(username: str) -> dict:
        nonlocal done
        async with semaphore:
            info = await get_profile_info(context, username)
            done += 1
            if on_progress:
                try:
                    on_progress(done, total, info)
                except Exception:
                    pass
            await asyncio.sleep(delay)
            return info

    # Processa em lotes pra não criar milhares de tasks de uma vez
    batch_size = 50
    for i in range(0, total, batch_size):
        batch = usernames[i:i + batch_size]
        batch_results = await asyncio.gather(*[process_one(u) for u in batch])
        results.extend(batch_results)

    return results
