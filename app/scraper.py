"""Scraper de seguidores Instagram - instagrapi 3.1.0."""
import os
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .config import DATA_DIR, SCRAPE_DELAY, MAX_WORKERS
from .phone import extract_phone, extract_email

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

SESSION_FILE = os.path.join(DATA_DIR, "ig_session.txt")


def _create_client():
    from instagrapi import Client
    cl = Client(with_challenge_flow=False)
    cl.delay_range = [2, 5]
    return cl


def _load_client(challenge_code: str = ""):
    """Cria ou restaura Client instagrapi."""
    from instagrapi.exceptions import ChallengeRequired, LoginRequired, PleaseWaitFewMinutes

    cl = _create_client()

    # --- Tenta restaurar sessao salva ---
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                sessionid = f.read().strip()
            if sessionid:
                ok = cl.login_by_sessionid(sessionid)
                if ok and cl.user_id:
                    print(f"  Sessao restaurada (user_id={cl.user_id})")
                    return cl
        except Exception as e:
            print(f"  Sessao invalida: {str(e)[:80]}")

    # --- Login novo ---
    print(f"  Login @{IG_USERNAME}...")
    
    if challenge_code:
        cl.with_challenge_flow = True
        cl.challenge_code_handler = lambda *args: challenge_code

    try:
        result = cl.login(IG_USERNAME, IG_PASSWORD)
    except ChallengeRequired:
        if challenge_code:
            raise RuntimeError(
                f"Codigo {challenge_code} nao foi aceito. "
                "Pede um novo codigo no Gmail e tenta de novo."
            )
        raise RuntimeError(
            "Instagram pediu codigo de verificacao. "
            "Olha o Gmail do bot por um codigo e digita no campo."
        )
    except LoginRequired as e:
        raise RuntimeError(f"Credenciais invalidas: {str(e)[:100]}")
    except PleaseWaitFewMinutes:
        raise RuntimeError("Muitas tentativas em pouco tempo. Espera 5 min e tenta de novo.")

    if not cl.user_id:
        raise RuntimeError("Login falhou - nao conseguiu autenticar.")

    # --- Salva sessao ---
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        f.write(cl.sessionid or "")
    print(f"  Login OK! user_id={cl.user_id}, sessao salva.")
    return cl


async def login_and_get_context(challenge_code: str = ""):
    """Wrapper async para _load_client."""
    return await asyncio.to_thread(_load_client, challenge_code)


async def get_profile_info(cl, username: str) -> dict:
    """Retorna info do perfil (nome + bio)."""
    user = await asyncio.to_thread(cl.user_info_by_username, username)
    return {
        "name": user.full_name or "",
        "bio": user.biography or "",
        "private": user.is_private,
    }


async def get_followers_list(cl, target_username: str, max_followers: int = 0, progress_cb=None):
    """
    Puxa lista de seguidores com paginação usando iter_user_followers_v1.
    """
    # Checa se perfil e privado
    profile = await asyncio.to_thread(cl.user_info_by_username, target_username)
    if profile.is_private:
        raise RuntimeError(
            f"Perfil @{target_username} e PRIVADO. Nao da p/ ver seguidores."
        )

    # Pega user_id
    user_id = await asyncio.to_thread(cl.user_id_from_username, target_username)
    
    def _fetch():
        results = []
        iterator = cl.iter_user_followers_v1(user_id, amount=max_followers)
        for user in iterator:
            results.append(user)
            if max_followers and len(results) >= max_followers:
                break
            if progress_cb and len(results) % 200 == 0:
                progress_cb(len(results))
        return results
    
    followers = await asyncio.to_thread(_fetch)
    
    if not followers:
        raise RuntimeError(
            "Nenhum seguidor retornado pela API. "
            "O Instagram pode estar limitando o acesso."
        )
    
    return followers


def _scrape_one_sync(cl, i: int, follower) -> dict:
    """
    Pega info completa de UM seguidor (versao SYNC p/ thread pool).
    follower: UserShort (tem pk, username, full_name, is_private)
    """
    try:
        user = cl.user_info(follower.pk)
        
        bio = user.biography or ""
        name = user.full_name or follower.full_name or ""
        
        # Telefone: campos diretos + regex na bio
        phone = None
        if user.contact_phone_number:
            phone = user.contact_phone_number
        elif user.public_phone_number:
            cc = user.public_phone_country_code or "55"
            phone = f"+{cc} {user.public_phone_number}"
        if not phone:
            phone = extract_phone(bio)
        
        # Email: campo direto + regex na bio
        email = None
        if user.public_email:
            email = user.public_email
        if not email:
            email = extract_email(bio)
        
        return {
            "posicao": i + 1,
            "nome": name,
            "usuario": follower.username,
            "telefone": phone or "",
            "email": email or "",
            "privado": user.is_private,
        }
    except Exception as e:
        return {
            "posicao": i + 1,
            "nome": follower.full_name or "",
            "usuario": follower.username,
            "telefone": "",
            "email": "",
            "privado": getattr(follower, "is_private", False),
            "_erro": str(e)[:100],
        }


async def scrape_all_profiles(cl, followers: list, delay: float = 3.0, progress_cb=None) -> list:
    """
    Processa todos os seguidores em paralelo usando ThreadPoolExecutor.
    progress_cb(count_done) chamado a cada lote.
    """
    workers = max(1, int(MAX_WORKERS))
    results = [None] * len(followers)
    done_count = 0
    total = len(followers)
    last_reported = 0
    
    # Dividi em lotes
    batch_size = workers * 2
    
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        idx = 0
        
        for i, follower in enumerate(followers):
            if i >= (len(followers) - workers):
                break
            future = pool.submit(_scrape_one_sync, cl, i, follower)
            futures[future] = i
        
        while futures:
            # Espera pelo menos uma completar
            from concurrent.futures import wait, FIRST_COMPLETED
            done_set, _ = wait(list(futures.keys()), return_when=FIRST_COMPLETED)
            
            for fut in done_set:
                i = futures.pop(fut)
                try:
                    results[i] = fut.result()
                except Exception as e:
                    f = followers[i]
                    results[i] = {
                        "posicao": i + 1,
                        "nome": f.full_name or "",
                        "usuario": f.username,
                        "telefone": "",
                        "email": "",
                        "privado": getattr(f, "is_private", False),
                        "_erro": str(e)[:80],
                    }
                done_count += 1
                
                # Pausa curta entre batches para nao estourar rate limit
                time.sleep(delay * 0.1 / max(workers, 1))
                
                # Reporta progresso a cada 10
                if done_count - last_reported >= 10:
                    last_reported = done_count
                    if progress_cb:
                        progress_cb(done_count)
                
                # Adiciona novo futuro se ainda ha seguidores para processar
                next_idx = i + workers
                if next_idx < total and next_idx < len(followers):
                    new_future = pool.submit(_scrape_one_sync, cl, next_idx, followers[next_idx])
                    futures[new_future] = next_idx
    
    # Garante que tudo foi processado
    for i in range(total):
        if results[i] is None:
            f = followers[i]
            results[i] = {
                "posicao": i + 1,
                "nome": f.full_name or "",
                "usuario": f.username,
                "telefone": "",
                "email": "",
                "privado": getattr(f, "is_private", False),
                "_erro": "nao processado",
            }
    
    if progress_cb:
        progress_cb(total)
    
    return results
