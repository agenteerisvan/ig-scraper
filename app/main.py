"""
Instagram Follower Scraper — CLI

Uso:
    python -m app.main @username
    python -m app.main @username --max 5000
    python -m app.main @username --delay 5
"""
import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

from .config import OUTPUT_DIR, MAX_WORKERS, SCRAPE_DELAY, IG_USERNAME, IG_PASSWORD
from .scraper import (
    get_authenticated_context,
    get_followers_list,
    scrape_all_profiles,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def progress_bar(done: int, total: int, info: dict) -> None:
    """Imprime barra de progresso no terminal."""
    if done % 10 == 0 or done == total:
        pct = done / total * 100
        bar_len = 40
        filled = int(bar_len * done / total)
        bar = "█" * filled + "░" * (bar_len - filled)
        phones_found = 0  # contagem global não acessível aqui sem closure
        status = ""
        if info.get("phone"):
            status = f" 📱 {info['phone']}"
        elif info.get("private"):
            status = " 🔒 privado"
        sys.stdout.write(f"\r  [{bar}] {pct:.0f}% ({done}/{total}){status}")
        sys.stdout.flush()


async def run(username: str, max_followers: int | None, delay: float | None, workers: int | None) -> None:
    if not IG_USERNAME or not IG_PASSWORD:
        logger.error("Defina IG_USERNAME e IG_PASSWORD no arquivo .env")
        sys.exit(1)

    username = username.lstrip("@")
    delay = delay or SCRAPE_DELAY
    workers = workers or MAX_WORKERS

    t0 = time.time()

    # ── Setup browser ──
    async with async_playwright() as pw:
        logger.info("Iniciando browser...")
        context = await get_authenticated_context(pw)

        # ── Step 1: Lista de seguidores ──
        logger.info(f"\n{'='*60}")
        logger.info(f"Puxando lista de seguidores de @{username}")
        logger.info(f"{'='*60}")
        
        followers = await get_followers_list(context, username, max_followers=max_followers)
        
        if not followers:
            logger.error("Nenhum seguidor encontrado. Verifique o @ ou se a conta é pública.")
            return

        # Salva lista bruta
        list_file = OUTPUT_DIR / f"{username}_followers.txt"
        list_file.write_text("\n".join(followers))
        logger.info(f"Lista salva em {list_file.name}")

        # ── Step 2: Scrape de perfis ──
        logger.info(f"\n{'='*60}")
        logger.info(f"Scrapeando {len(followers)} perfis (delay={delay}s, workers={workers})")
        logger.info(f"Estimativa: ~{len(followers)*delay/3600:.1f}h")
        logger.info(f"{'='*60}\n")

        phone_count = 0
        private_count = 0
        
        def on_progress(done: int, total: int, info: dict):
            nonlocal phone_count, private_count
            if info.get("phone"):
                phone_count += 1
            if info.get("private"):
                private_count += 1
            if done % 5 == 0 or done == total:
                pct = done / total * 100
                sys.stdout.write(f"\r  [{done:>5}/{total}] {pct:5.1f}%  |  📱 {phone_count}  |  🔒 {private_count}")
                sys.stdout.flush()

        results = await scrape_all_profiles(
            pw, context, followers,
            delay=delay,
            max_workers=workers,
            on_progress=on_progress,
        )
        
        # Fecha context
        await context.close()

    # ── Step 3: Output ──
    print()  # quebra de linha pós-progresso
    
    df = pd.DataFrame(results)
    
    # Reordena colunas
    cols = ["name", "username", "phone", "private", "bio_snippet"]
    df = df[[c for c in cols if c in df.columns]]
    
    # Filtra erros (mantém mas marca)
    output_file = OUTPUT_DIR / f"resultado_{username}.csv"
    df.to_csv(output_file, index=False)
    
    elapsed = time.time() - t0
    
    # ── Resumo ──
    total = len(df)
    has_phone = df["phone"].notna().sum() if "phone" in df.columns else 0
    is_private = df["private"].sum() if "private" in df.columns else 0
    has_name = (df["name"].notna() & (df["name"] != "")).sum() if "name" in df.columns else 0
    
    print(f"""
╔══════════════════════════════════════════╗
║           RESULTADO — @{username:<20}║
╠══════════════════════════════════════════╣
║  Seguidores processados:  {total:<6}         ║
║  Com nome:               {has_name:<6}         ║
║  Com telefone:           {has_phone:<6}  ({has_phone/total*100 if total else 0:.1f}%)    ║
║  Perfis privados:        {is_private:<6}         ║
║  Tempo total:            {elapsed/60:.1f} min              ║
╠══════════════════════════════════════════╣
║  Arquivo:                 {output_file.name:<24}║
╚══════════════════════════════════════════╝
""")
    
    # Também gera versão filtrada (só com telefone)
    if has_phone > 0:
        df_phone = df[df["phone"].notna()]
        phone_file = OUTPUT_DIR / f"resultado_{username}_com_telefone.csv"
        df_phone.to_csv(phone_file, index=False)
        print(f"  🔥 Versão só com telefone: {phone_file.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Instagram Follower Scraper — extrai nome e telefone dos seguidores",
        epilog="Exemplo: python -m app.main @heelder_ --max 5000",
    )
    parser.add_argument("username", help="@ do influencer (com ou sem @)")
    parser.add_argument("--max", type=int, default=None, help="Máximo de seguidores para processar")
    parser.add_argument("--delay", type=float, default=None, help="Pausa entre perfis (segundos)")
    parser.add_argument("--workers", type=int, default=None, help="Browsers simultâneos")
    args = parser.parse_args()

    asyncio.run(run(args.username, args.max, args.delay, args.workers))


if __name__ == "__main__":
    main()
