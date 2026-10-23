"""Scraper de seguidores do Instagram via instagrapi (HTTP puro, sem browser)."""
import asyncio
import json
import os
import time
from typing import Optional

from .config import IG_USERNAME, IG_PASSWORD, DATA_DIR, COOKIES_FILE, SCRAPE_DELAY
from .phone import extract_phone


def _load_client(challenge_code: str = ""):
    """Cria ou restaura Client instagrapi."""
    from instagrapi import Client
    from instagrapi.exceptions import ChallengeRequired, LoginRequired, PleaseWaitFewMinutes
    
    # Sem challenge flow por padrao; se for necessario, da erro com mensagem clara
    cl = Client(with_challenge_flow=False)
    cl.delay_range = [2, 5]
    
    # Se usuario forneceu codigo, registra handler ANTES de desativar o flow
    if challenge_code:
        cl.challenge_code_handler = lambda *args: challenge_code
        cl.with_challenge_flow = True
        print(f"  Codigo de verificacao fornecido.")
    
    # Tenta restaurar sessao salva
    session_file = os.path.join(DATA_DIR, f"ig_session_{IG_USERNAME}.json")
    if os.path.exists(session_file):
        try:
            cl.load_session_from_file(session_file)
            if cl.isLoggedIn:
                print(f"  Sessao restaurada para @{IG_USERNAME}")
                return cl
        except Exception as e:
            print(f"  Sessao invalida: {str(e)[:80]}")
    
    # Login novo
    print(f"  Login @{IG_USERNAME}...")
    
    try:
        cl.login(IG_USERNAME, IG_PASSWORD)
    except ChallengeRequired:
        if not challenge_code:
            raise RuntimeError(
                "Instagram pediu verificacao. Olha teu celular/e-mail por um codigo "
                "(SMS, WhatsApp ou e-mail do Instagram). Digita o codigo no campo "
                "'Codigo de verificacao' e clica Iniciar de novo."
            )
        else:
            raise RuntimeError(
                f"Codigo fornecido ({challenge_code}) nao funcionou. Confere se o codigo "
                "esta certo e tenta de novo."
            )
    except LoginRequired as e:
        raise RuntimeError(f"Credenciais invalidas: {str(e)[:100]}")
    except PleaseWaitFewMinutes:
        raise RuntimeError(
            "Muitas tentativas rapidas. Espera uns minutos e tenta de novo."
        )
    
    # Salva sessao (compatibilidade com instagrapi 3.x)
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        cl.save_session_to_file(session_file)
    except AttributeError:
        # Fallback: salva o dict do client manualmente
        import json as _json
        state = {}
        for k, v in cl.__dict__.items():
            try:
                _json.dumps(v)
                state[k] = v
            except (TypeError, ValueError):
                pass
        with open(session_file, "w") as _f:
            _json.dump(state, _f)
    print("  Login OK! Sessao salva.")
    return cl


async def login_and_get_context(pw=None, challenge_code: str = ""):
    """Retorna o client instagrapi autenticado."""
    return await asyncio.to_thread(_load_client, challenge_code)


async def get_profile_info(cl, username: str) -> dict:
    """Busca nome e bio do perfil."""
    def _fetch():
        user = cl.user_info_by_username(username)
        if not user:
            raise ValueError(f"Usuario @{username} nao encontrado.")
        return {
            "name": user.full_name or username,
            "bio": user.biography or "",
        }
    
    return await asyncio.to_thread(_fetch)


async def get_followers_list(cl, target_username: str, max_followers: int = 0) -> list[dict]:
    """Coleta seguidores do perfil alvo."""
    def _fetch():
        user = cl.user_info_by_username(target_username)
        if not user:
            raise ValueError(f"Usuario @{target_username} nao encontrado.")
        
        if user.is_private:
            raise ValueError(f"@{target_username} e privado.")
        
        followers = []
        count = 0
        
        for follower in cl.account.followers(user.pk, amount=max_followers or 10000):
            followers.append({
                "username": follower.username,
                "name": follower.full_name or follower.username,
            })
            count += 1
            print(f"  Seguidor {count}: @{follower.username}")
            
            # Delay p/ nao dar rate limit
            time.sleep(SCRAPE_DELAY * 0.3)  # delay menor que o original
            
            if max_followers and count >= max_followers:
                break
        
        return followers
    
    return await asyncio.to_thread(_fetch)


async def scrape_all_profiles(cl, followers: list[dict], progress_cb=None) -> list[dict]:
    """Visita o perfil de cada seguidor e extrai telefone da bio."""
    results = []
    total = len(followers)
    
    def _fetch(i: int, follower: dict) -> dict:
        try:
            user = cl.user_info_by_username(follower["username"])
            bio = user.biography or "" if user else ""
            name = user.full_name or follower.get("name") or follower["username"] if user else follower.get("name")
            private = user.is_private if user else False
            
            phone = extract_phone(bio) if bio else ""
            
            result = {
                "posicao": i + 1,
                "nome": name,
                "usuario": follower["username"],
                "telefone": phone or "",
                "privado": private,
            }
            
            # Delay entre requests
            time.sleep(SCRAPE_DELAY * 0.2)
            return result
            
        except Exception as e:
            print(f"  [{i+1}] Erro @{follower['username']}: {str(e)[:60]}")
            time.sleep(2)
            return {
                "posicao": i + 1,
                "nome": follower.get("name", ""),
                "usuario": follower["username"],
                "telefone": "",
                "privado": False,
            }
    
    for i, follower in enumerate(followers):
        result = await asyncio.to_thread(_fetch, i, follower)
        results.append(result)
        
        if progress_cb:
            await progress_cb(i + 1, total)
    
    return results


def _scrape_one(cl, i: int, follower: dict) -> dict:
    """Scrapes um unico seguidor (nome + telefone). Usado com asyncio.to_thread."""
    import time as _t
    from .phone import extract_phone
    
    try:
        user = cl.user_info_by_username(follower["username"])
        bio = (user.biography or "") if user else ""
        name = (user.full_name or follower.get("name") or follower["username"]) if user else follower.get("name", "")
        private = user.is_private if user else False
        phone = extract_phone(bio) if bio else ""
        
        _t.sleep(SCRAPE_DELAY * 0.2)
        
        return {
            "posicao": i + 1,
            "nome": name,
            "usuario": follower["username"],
            "telefone": phone,
            "privado": private,
        }
    except Exception as e:
        print(f"  [{i+1}] Erro @{follower['username']}: {str(e)[:60]}")
        _t.sleep(2)
        return {
            "posicao": i + 1,
            "nome": follower.get("name", ""),
            "usuario": follower["username"],
            "telefone": "",
            "privado": False,
        }
