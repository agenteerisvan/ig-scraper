"""Core do scraper: login, lista de seguidores, perfil individual."""
import asyncio
import json
import logging
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
#  SESSÃO / COOKIES
# ──────────────────────────────────────────────

async def create_context(context: BrowserContext) -> None:
    """Aplica user-agent e viewport padrão ao context."""
    await context.set_default_timeout(PAGE_TIMEOUT)


async def login_ig(pw) -> BrowserContext:
    """Faz login no Instagram e retorna um context autenticado."""
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="pt-BR",
    )
    await create_context(context)
    page = await context.new_page()

    logger.info("Navegando para login...")
    await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Preenche credenciais
    username_input = page.locator('input[name="username"]')
    password_input = page.locator('input[name="password"]')
    await username_input.fill(IG_USERNAME)
    await password_input.fill(IG_PASSWORD)
    await page.locator('button[type="submit"]').click()

    # Aguarda redirecionamento
    await page.wait_for_url("**/instagram.com/**", timeout=30000)
    await page.wait_for_timeout(3000)

    # Fecha popup "Not now" se aparecer
    try:
        btn = page.locator('button:has-text("Not now"), button:has-text("Agora não")').first
        if await btn.is_visible(timeout=3000):
            await btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # Salva cookies
    cookies = await context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logger.info(f"Sessão salva em {COOKIES_FILE}")

    return context


async def restore_context(pw) -> BrowserContext:
    """Restaura sessão a partir de cookies salvos."""
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="pt-BR",
    )
    await create_context(context)

    with open(COOKIES_FILE) as f:
        cookies = json.load(f)
    await context.add_cookies(cookies)

    # Valida: abre instagram.com e confere se está logado
    page = await context.new_page()
    await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Se aparecer o botão de login, a sessão expirou
    if page.locator('a[href="/accounts/login/"]').count() > 0:
        await browser.close()
        raise RuntimeError("Sessão expirada. Remova data/ig_cookies.json para re-fazer login.")

    await page.close()
    logger.info("Sessão restaurada com sucesso.")
    return context


async def get_authenticated_context(pw) -> BrowserContext:
    """Retorna contexto autenticado (login ou restore)."""
    if COOKIES_FILE.exists():
        try:
            return await restore_context(pw)
        except RuntimeError:
            logger.warning("Cookies inválidos, refazendo login...")
            COOKIES_FILE.unlink(missing_ok=True)
    return await login_ig(pw)


# ──────────────────────────────────────────────
#  LISTA DE SEGUIDORES
# ──────────────────────────────────────────────

async def get_followers_list(context: BrowserContext, username: str, max_followers: int | None = None) -> list[str]:
    """
    Abre o perfil do influencer, clica em 'Seguidores',
    e coleta todos os @usernames via scroll.
    """
    page = await context.new_page()
    profile_url = f"https://www.instagram.com/{username.lstrip('@')}/"
    
    logger.info(f"Abrindo perfil: {profile_url}")
    await page.goto(profile_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Fecha popup "Not now" se aparecer
    try:
        btn = page.locator('button:has-text("Not now"), button:has-text("Agora não")').first
        if await btn.is_visible(timeout=2000):
            await btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # Clica no link de seguidores
    followers_link = page.locator('a[href*="/followers"] span, span:has-text("seguidores") >> nth=0')
    try:
        await followers_link.first.click(timeout=5000)
    except Exception:
        # Tenta pelo texto diretamente
        await page.locator('text=/\d+\s*(seguidores|followers)/i').first.click(timeout=5000)
    
    await page.wait_for_selector('[role="dialog"]', timeout=10000)
    await page.wait_for_timeout(2000)

    followers: list[str] = []
    seen: set[str] = set()
    stagnant_count = 0

    while True:
        # Coleta usernames visíveis
        items = page.locator('[role="dialog"] a[href^="/"]:not([href="/"])')
        count = await items.count()
        
        for i in range(count):
            href = await items.nth(i).get_attribute("href")
            if href:
                tag = href.strip("/").split("/")[0]
                if tag and tag != username.lower() and tag not in seen:
                    seen.add(tag)
                    followers.append(tag)
        
        # Verifica se atingiu o máximo
        if max_followers and len(followers) >= max_followers:
            followers = followers[:max_followers]
            break
        
        # Scroll dentro do dialog
        before = len(followers)
        dialog = page.locator('[role="dialog"]')
        await dialog.evaluate('el => el.scrollTo(0, el.scrollHeight)')
        await page.wait_for_timeout(2000)
        
        after = len(followers)
        if after == before:
            stagnant_count += 1
            if stagnant_count >= 3:
                break
        else:
            stagnant_count = 0
        
        # Progress
        if len(followers) % 500 == 0:
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
        "bio_snippet": "",
        "private": False,
        "error": None,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await page.wait_for_timeout(2000)

        # Detecta perfil privado
        private_indicator = page.locator('text=/Esta conta é privada|This account is private/i')
        if await private_indicator.count() > 0:
            result["private"] = True
            # Mesmo em perfil privado tenta pegar nome
            name_el = page.locator('h1, [data-test-id="web-profile-name"]')
            if await name_el.count() > 0:
                result["name"] = (await name_el.first.inner_text()).strip()
        
        else:
            # Nome: h1 do perfil
            name_el = page.locator('h1, [data-test-id="web-profile-name"]')
            if await name_el.count() > 0:
                result["name"] = (await name_el.first.inner_text()).strip()
            
            # Bio: seção abaixo do nome
            # Tenta múltiplos seletores (Instagram muda HTML com frequência)
            bio_selectors = [
                'div[style*="white-space: pre-wrap"]',           # bio com quebras de linha
                '[data-test-id="web-profile-bio-field"]',          # test-id
                'section > div > div:nth-child(2) > div > div',   # fallback estrutural
            ]
            
            bio_text = ""
            for sel in bio_selectors:
                el = page.locator(sel)
                if await el.count() > 0:
                    bio_text = (await el.first.inner_text()).strip()
                    if bio_text:
                        break
            
            # Fallback: meta description (sempre presente)
            if not bio_text:
                meta = page.locator('meta[property="og:description"]')
                if await meta.count() > 0:
                    bio_text = (await meta.get_attribute("content")) or ""
                    # og:description tem formato "X followers, Y following, Z posts - Bio text"
                    parts = bio_text.split(" - ", 1)
                    if len(parts) > 1:
                        bio_text = parts[1]
            
            result["bio_snippet"] = bio_text[:200]
            result["phone"] = extract_phone(bio_text)

    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        await page.close()

    return result


# ──────────────────────────────────────────────
#  ORQUESTRAÇÃO DE WORKERS
# ──────────────────────────────────────────────

async def scrape_all_profiles(
    pw, 
    context: BrowserContext, 
    usernames: list[str],
    delay: float | None = None,
    max_workers: int | None = None,
    on_progress=None,
) -> list[dict]:
    """
    Processa todos os perfis com controle de concorrência.
    Usa múltiplos contexts (browsers) para parallelizar.
    """
    delay = delay or SCRAPE_DELAY
    max_workers = max_workers or MAX_WORKERS
    results: list[dict] = []
    semaphore = asyncio.Semaphore(max_workers)
    total = len(usernames)
    done = 0

    async def process_one(username: str) -> dict:
        nonlocal done
        async with semaphore:
            ctx = context
            info = await get_profile_info(ctx, username)
            done += 1
            if on_progress:
                on_progress(done, total, info)
            # Rate limit entre requests
            await asyncio.sleep(delay)
            return info

    tasks = [process_one(u) for u in usernames]
    results = await asyncio.gather(*tasks)
    return list(results)
