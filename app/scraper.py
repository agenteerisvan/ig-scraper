"""Scraper de seguidores Instagram - instagrapi 3.1.0."""
import os
import time
import asyncio
from typing import Optional

from .config import DATA_DIR, SCRAPE_DELAY
from .phone import extract_phone

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
    
    # Se ha codigo de verificacao, ativa challenge flow com handler
    if challenge_code:
        cl.with_challenge_flow = True
        cl.challenge_code_handler = lambda *args: challenge_code

    try:
        # O parametro verification_code ja existe na API 3.1.0
        result = cl.login(IG_USERNAME, IG_PASSWORD)
    except ChallengeRequired:
        if challenge_code:
            raise RuntimeError(
                f"Codigo {challenge_code} nao foi aceito. "
                "Pede um novo codigo no Gmail e tenta de novo."
            )
        raise RuntimeError(
            "Instagram pediu codigo de verificacao. "
            "Olha o Gmail do bot por um codigo e digita no campo 'Codigo de verificacao'."
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


async def get_followers_list(cl, target_username: str, max_followers: int = 0):
    """
    Puxa lista de seguidores do alvo.
    Retorna list[UserShort] com: pk, username, full_name, is_private.
    """
    # Checa se perfil e privado
    profile = await asyncio.to_thread(cl.user_info_by_username, target_username)
    if profile.is_private:
        raise RuntimeError(
            f"Perfil @{target_username} e PRIVADO. Nao da p/ ver seguidores."
        )

    # Pega user_id
    user_id = await asyncio.to_thread(cl.user_id_from_username, target_username)
    
    # Puxa seguidores
    # amount=0 significa todos (iteracao completa)
    amount = max_followers if max_followers > 0 else 0
    followers = await asyncio.to_thread(cl.user_followers_v1, user_id, amount)
    
    return followers or []


async def _scrape_one(cl, i: int, follower) -> dict:
    """
    Pega info completa de UM seguidor e extrai telefone.
    follower: UserShort (tem pk, username, full_name, is_private)
    """
    try:
        # Pega perfil completo (com biography)
        user = await asyncio.to_thread(cl.user_info, follower.pk)
        
        bio = user.biography or ""
        name = user.full_name or follower.full_name or ""
        
        # Extrai telefone: primeiro tenta campos diretos, depois regex na bio
        phone = None
        
        # Campos diretos do User model
        if user.contact_phone_number:
            phone = user.contact_phone_number
        elif user.public_phone_number:
            cc = user.public_phone_country_code or "55"
            phone = f"+{cc} {user.public_phone_number}"
        
        # Fallback: regex na biografia
        if not phone:
            phone = extract_phone(bio)
        
        return {
            "posicao": i + 1,
            "nome": name,
            "usuario": follower.username,
            "telefone": phone or "",
            "privado": user.is_private,
        }
    except Exception as e:
        return {
            "posicao": i + 1,
            "nome": follower.full_name or "",
            "usuario": follower.username,
            "telefone": "",
            "privado": getattr(follower, "is_private", False),
            "_erro": str(e)[:100],
        }
    finally:
        # Pausa entre requisições para evitar rate limit
        time.sleep(SCRAPE_DELAY * 0.2)
