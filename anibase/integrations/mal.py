# anibase/integrations/mal.py
"""Integrasi MyAnimeList: autentikasi OAuth PKCE, profil, scrobbling, dan daftar tontonan."""

import os
import re
import json
import time
import threading
from urllib.parse import quote_plus
import requests
from flask import request, url_for
import anibase.constants as _constants
from anibase.logging import app_log
from anibase.utils import atomic_write_json_file
from anibase.settings import load_settings, get_effective_mal_client_id
from anibase.db import db_connection
from anibase.media import get_episode_number


def load_mal_auth():
    with _constants.MAL_AUTH_LOCK:
        if not os.path.exists(_constants.MAL_AUTH_FILE):
            return {}
        try:
            with open(_constants.MAL_AUTH_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def save_mal_auth(auth_data):
    with _constants.MAL_AUTH_LOCK:
        atomic_write_json_file(_constants.MAL_AUTH_FILE, auth_data or {}, "MyAnimeList Auth")


def clear_mal_auth():
    with _constants.MAL_AUTH_LOCK:
        if os.path.exists(_constants.MAL_AUTH_FILE):
            try:
                os.remove(_constants.MAL_AUTH_FILE)
            except OSError:
                pass


def is_mal_authenticated():
    return bool(load_mal_auth().get("access_token"))


def load_mal_scrobble_history():
    with _constants.MAL_SCROBBLE_LOCK:
        if not os.path.exists(_constants.MAL_SCROBBLE_CACHE):
            return {}
        try:
            with open(_constants.MAL_SCROBBLE_CACHE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def record_mal_scrobble(mal_id, ep_num, status="watching"):
    with _constants.MAL_SCROBBLE_LOCK:
        history = load_mal_scrobble_history()
        history[f"{mal_id}_{int(ep_num)}"] = {
            "timestamp": time.time(),
            "status": status
        }
        atomic_write_json_file(_constants.MAL_SCROBBLE_CACHE, history, "MAL Scrobble History")


def clear_mal_scrobble_record(anime_name, episode):
    from anibase.metadata import get_cached_anilist_info, normalize_provider_id, get_metadata_mapping
    info = get_cached_anilist_info(anime_name) or {}
    mal_id = normalize_provider_id(info.get("mal_id"))
    if not mal_id:
        mapping = get_metadata_mapping(anime_name)
        if mapping:
            mal_id = normalize_provider_id(mapping.get("mal_id"))
    if not mal_id:
        return
    ep_num = get_episode_number(os.path.basename(episode))
    if ep_num and ep_num > 0:
        with _constants.MAL_SCROBBLE_LOCK:
            history = load_mal_scrobble_history()
            key = f"{mal_id}_{int(ep_num)}"
            if key in history:
                history.pop(key, None)
                atomic_write_json_file(_constants.MAL_SCROBBLE_CACHE, history, "MAL Scrobble History")


def cleanup_mal_oauth_sessions():
    now = time.time()
    expired = [k for k, v in _constants.MAL_OAUTH_SESSIONS.items() if now - v.get("timestamp", 0) > 600]
    for k in expired:
        _constants.MAL_OAUTH_SESSIONS.pop(k, None)


def get_valid_mal_token():
    with _constants.MAL_AUTH_LOCK:
        auth = load_mal_auth()
        if not auth or not auth.get("access_token"):
            return None

        expires_at = auth.get("expires_at", 0)
        if expires_at - time.time() > 300:
            return auth.get("access_token")

        refresh_token = auth.get("refresh_token")
        if not refresh_token:
            return auth.get("access_token")

        settings = load_settings()
        client_id = get_effective_mal_client_id(settings)
        if not client_id:
            return auth.get("access_token")

        try:
            resp = requests.post(
                _constants.MAL_OAUTH_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10
            )
            if resp.status_code == 200:
                token_data = resp.json()
                auth["access_token"] = token_data["access_token"]
                auth["refresh_token"] = token_data.get("refresh_token", refresh_token)
                auth["expires_at"] = time.time() + float(token_data.get("expires_in", 2592000))
                save_mal_auth(auth)
                app_log(f"MyAnimeList token refreshed successfully for {auth.get('username')}.", "INFO")
                return auth["access_token"]
            else:
                app_log(f"Failed to refresh MAL token: {resp.status_code} {resp.text}", "WARN")
                return None
        except Exception as e:
            app_log(f"MAL token refresh network error: {e}", "WARN")
            return auth.get("access_token")


def fetch_mal_user_profile(access_token, fields="id,name,picture"):
    try:
        resp = requests.get(
            f"{_constants.MAL_API_BASE_URL}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        app_log(f"Failed to fetch MAL user profile: {e}", "WARN")
    return None


def load_mal_profile_cache():
    with _constants.MAL_PROFILE_LOCK:
        if not os.path.exists(_constants.MAL_PROFILE_CACHE):
            return None
        try:
            with open(_constants.MAL_PROFILE_CACHE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return None


def save_mal_profile_cache(profile_data):
    with _constants.MAL_PROFILE_LOCK:
        atomic_write_json_file(_constants.MAL_PROFILE_CACHE, profile_data or {}, "MAL Profile Cache")


def clear_mal_profile_cache():
    with _constants.MAL_PROFILE_LOCK:
        if os.path.exists(_constants.MAL_PROFILE_CACHE):
            try:
                os.remove(_constants.MAL_PROFILE_CACHE)
            except OSError:
                pass
    with _constants.MAL_ANIMELIST_LOCK:
        try:
            for fname in os.listdir(_constants.CACHE_DIR):
                if fname.startswith("mal_animelist_") and fname.endswith(".json"):
                    try:
                        os.remove(os.path.join(_constants.CACHE_DIR, fname))
                    except OSError:
                        pass
        except OSError:
            pass


def get_mal_user_full_profile(force=False):
    now = time.time()
    cached = load_mal_profile_cache()
    if not force and cached and (now - cached.get("cached_at", 0) < _constants.MAL_PROFILE_CACHE_TTL):
        return cached

    token = get_valid_mal_token()
    if not token:
        if cached:
            cached["is_stale"] = True
            return cached
        auth = load_mal_auth()
        return {
            "cached_at": now,
            "is_stale": True,
            "user": {
                "name": auth.get("username", "MAL User"),
                "picture": auth.get("picture", ""),
                "id": auth.get("user_id"),
                "anime_statistics": {}
            }
        }

    try:
        resp = requests.get(
            f"{_constants.MAL_API_BASE_URL}/users/@me",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "id,name,picture,gender,birthday,location,joined_at,anime_statistics,time_zone,is_supporter"},
            timeout=12
        )
        if resp.status_code == 200:
            user_data = resp.json()
            payload = {
                "cached_at": now,
                "is_stale": False,
                "user": user_data
            }
            save_mal_profile_cache(payload)
            auth = load_mal_auth()
            pic = user_data.get("picture")
            uname = user_data.get("name")
            if (pic and pic != auth.get("picture")) or (uname and uname != auth.get("username")):
                auth["picture"] = pic
                auth["username"] = uname
                save_mal_auth(auth)
            return payload
    except Exception as e:
        app_log(f"Failed to fetch MAL full profile: {e}", "WARN")

    if cached:
        cached["is_stale"] = True
        return cached

    auth = load_mal_auth()
    return {
        "cached_at": now,
        "is_stale": True,
        "user": {
            "name": auth.get("username", "MAL User"),
            "picture": auth.get("picture", ""),
            "id": auth.get("user_id"),
            "anime_statistics": {}
        }
    }


def get_mal_animelist_cache_path(status):
    safe_status = "".join(c for c in (status or "all") if c.isalnum() or c in "_-")
    return os.path.join(_constants.CACHE_DIR, f"mal_animelist_{safe_status}.json")


def load_mal_animelist_cache(status):
    cache_path = get_mal_animelist_cache_path(status)
    with _constants.MAL_ANIMELIST_LOCK:
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return None


def save_mal_animelist_cache(status, items_data):
    cache_path = get_mal_animelist_cache_path(status)
    with _constants.MAL_ANIMELIST_LOCK:
        atomic_write_json_file(cache_path, items_data or {}, "MAL Animelist Cache")


def get_mal_user_animelist(status="watching", limit=100, offset=0, force=False):
    now = time.time()
    cached = load_mal_animelist_cache(status)
    if not force and cached and (now - cached.get("cached_at", 0) < _constants.MAL_ANIMELIST_CACHE_TTL):
        return cached.get("items", [])

    token = get_valid_mal_token()
    if not token:
        return cached.get("items", []) if cached else []

    params = {
        "limit": min(max(1, limit), 100),
        "offset": max(0, offset),
        "sort": "list_updated_at",
        "fields": "list_status{score,num_episodes_watched,is_rewatching,updated_at,status},num_episodes,mean,media_type,status,genres,main_picture,alternative_titles"
    }
    if status and status != "all":
        params["status"] = status

    try:
        resp = requests.get(
            f"{_constants.MAL_API_BASE_URL}/users/@me/animelist",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", [])
            save_mal_animelist_cache(status, {
                "cached_at": now,
                "items": items,
                "paging": data.get("paging", {})
            })
            return items
        else:
            app_log(f"MAL animelist API error {resp.status_code}: {resp.text}", "WARN")
    except Exception as e:
        app_log(f"MAL animelist API exception: {e}", "WARN")

    return cached.get("items", []) if cached else []


def enrich_mal_list_with_local_library(items, local_index=None):
    import main
    from anibase.metadata import (
        get_cached_anilist_info,
        normalize_provider_id,
        get_metadata_mapping,
        normalize_anime_match_name,
        build_local_anime_match_index,
    )
    local_anime = main.get_anime()
    if local_index is None:
        local_index = build_local_anime_match_index(local_anime)

    mal_id_to_local = {}
    for anim in local_anime:
        name = anim.get("name")
        if not name:
            continue
        cached_info = get_cached_anilist_info(name) or {}
        m_id = normalize_provider_id(cached_info.get("mal_id"))
        if not m_id:
            m_map = get_metadata_mapping(name) or {}
            m_id = normalize_provider_id(m_map.get("mal_id"))
        if m_id:
            mal_id_to_local[str(m_id)] = anim

    enriched = []
    for item in (items or []):
        node = item.get("node", {})
        list_status = item.get("list_status", {})
        mal_id = str(node.get("id")) if node.get("id") else ""

        local_match = mal_id_to_local.get(mal_id) if mal_id else None
        if not local_match:
            alt_titles = node.get("alternative_titles") or {}
            candidates = [
                node.get("title"),
                alt_titles.get("en"),
                alt_titles.get("ja"),
            ]
            for syn in alt_titles.get("synonyms") or []:
                candidates.append(syn)

            for cand in candidates:
                if not cand:
                    continue
                norm = normalize_anime_match_name(cand)
                if norm and norm in local_index:
                    local_match = local_index[norm]
                    break

        local_url = None
        play_url = None
        if local_match:
            try:
                local_url = url_for("anime_detail", anime_name=local_match["name"])
                play_url = url_for("player", anime=local_match["name"])
            except Exception:
                local_url = f"/anime/{quote_plus(local_match['name'])}"
                play_url = f"/play/{quote_plus(local_match['name'])}"

        try:
            return_to = request.full_path if request else "/profile"
            external_url = url_for("external_anime_detail", mal_id=mal_id, return_to=return_to) if mal_id else None
        except Exception:
            external_url = f"/anime/external/mal/{mal_id}" if mal_id else None

        enriched.append({
            "node": node,
            "list_status": list_status,
            "in_library": local_match is not None,
            "local_anime": local_match,
            "local_url": local_url,
            "external_url": external_url,
            "play_url": play_url,
        })
    return enriched


def update_mal_user_anime_status(mal_id, num_watched=None, score=None, status=None):
    from anibase.metadata import normalize_provider_id
    token = get_valid_mal_token()
    if not token:
        return {"ok": False, "reason": "not_authenticated"}

    mal_id = normalize_provider_id(mal_id)
    if not mal_id:
        return {"ok": False, "reason": "invalid_mal_id"}

    payload = {}
    if num_watched is not None:
        try:
            payload["num_watched_episodes"] = max(0, int(num_watched))
        except (ValueError, TypeError):
            pass
    if score is not None:
        try:
            payload["score"] = max(0, min(10, int(score)))
        except (ValueError, TypeError):
            pass
    if status is not None and status in ["watching", "completed", "on_hold", "dropped", "plan_to_watch"]:
        payload["status"] = status

    if not payload:
        return {"ok": False, "reason": "no_updates"}

    url = f"{_constants.MAL_API_BASE_URL}/anime/{int(mal_id)}/my_list_status"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        resp = requests.patch(url, data=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            clear_mal_profile_cache()
            return {"ok": True, "data": resp.json()}
        return {"ok": False, "reason": f"http_{resp.status_code}", "text": resp.text}
    except Exception as e:
        return {"ok": False, "reason": "network_error", "error": str(e)}


def scrobble_episode_to_mal(anime_name, episode, ep_num=None, force=False):
    from anibase.metadata import (
        get_cached_anilist_info,
        get_cached_metadata_only,
        normalize_provider_id,
        get_metadata_mapping,
    )
    settings = load_settings()
    if not settings.get("mal_scrobble_enabled") or not is_mal_authenticated():
        return {"ok": False, "reason": "not_enabled"}

    token = get_valid_mal_token()
    if not token:
        return {"ok": False, "reason": "token_unavailable"}

    info = get_cached_anilist_info(anime_name) or {}
    if not info:
        info = get_cached_metadata_only(anime_name) or {}
    mal_id = normalize_provider_id(info.get("mal_id"))
    if not mal_id:
        manual_mapping = get_metadata_mapping(anime_name)
        if manual_mapping:
            mal_id = normalize_provider_id(manual_mapping.get("mal_id"))
    if not mal_id:
        app_log(f"Cannot scrobble {anime_name}: MAL ID not found in metadata.", "INFO")
        return {"ok": False, "reason": "no_mal_id"}

    if ep_num is None:
        ep_num = get_episode_number(os.path.basename(episode))
    if not ep_num or ep_num <= 0:
        app_log(f"Cannot scrobble {anime_name}/{episode}: episode number could not be determined.", "INFO")
        return {"ok": False, "reason": "invalid_episode_number"}

    cache_key = f"{mal_id}_{int(ep_num)}"
    history = load_mal_scrobble_history()
    if not force and cache_key in history:
        return {"ok": True, "already_scrobbled": True, "mal_id": mal_id, "ep_num": int(ep_num)}

    anime_status = (info.get("status") or "").strip().upper()
    if not anime_status:
        try:
            with db_connection() as conn:
                row = conn.execute("SELECT status FROM anime_library WHERE name = ?", (anime_name,)).fetchone()
                if row and row[0]:
                    anime_status = str(row[0]).strip().upper()
        except Exception:
            pass

    is_airing = bool(
        anime_status in {"RELEASING", "CURRENTLY_AIRING", "AIRING", "NOT_YET_RELEASED"}
        or info.get("next_airing")
    )

    total_episodes = 0
    if info.get("episodes"):
        try:
            total_episodes = int(info["episodes"])
        except (ValueError, TypeError):
            total_episodes = 0

    if not is_airing and total_episodes > 0 and ep_num >= total_episodes:
        mal_status = "completed"
    else:
        mal_status = "watching"

    url = f"{_constants.MAL_API_BASE_URL}/anime/{int(mal_id)}/my_list_status"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = {
        "num_watched_episodes": int(ep_num),
        "status": mal_status
    }
    try:
        resp = requests.patch(url, data=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            record_mal_scrobble(mal_id, ep_num, mal_status)
            app_log(f"Scrobbled {anime_name} Ep {ep_num} ({mal_status}) to MyAnimeList successfully.", "INFO")
            return {"ok": True, "mal_id": mal_id, "ep_num": int(ep_num), "status": mal_status}
        else:
            app_log(f"MAL scrobble failed ({resp.status_code}) for {anime_name} Ep {ep_num}: {resp.text}", "WARN")
            return {"ok": False, "status_code": resp.status_code, "reason": resp.text}
    except Exception as error:
        app_log(f"MAL scrobble network error for {anime_name} Ep {ep_num}: {error}", "WARN")
        return {"ok": False, "reason": str(error)}


def trigger_mal_scrobble_if_enabled(anime_name, episode):
    settings = load_settings()
    if settings.get("mal_scrobble_enabled") and is_mal_authenticated():
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', anime_name)[:20]
        threading.Thread(
            target=scrobble_episode_to_mal,
            args=(anime_name, episode),
            name=f"mal-scrobble-{safe_name}",
            daemon=True
        ).start()
        return True
    return False
