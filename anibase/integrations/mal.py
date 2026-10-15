# anibase/integrations/mal.py
"""Integrasi MyAnimeList: autentikasi OAuth PKCE, profil, scrobbling, dan daftar tontonan."""

import os
import re
import json
import time
import threading
from urllib.parse import quote_plus
import logging
import requests
from flask import request, url_for
import anibase.constants as _constants
from anibase.logging import app_log
from anibase.utils import atomic_write_json_file
from anibase.settings import load_settings, get_effective_mal_client_id
from anibase.db import db_connection
from anibase.media import get_episode_number
from anibase.crypto import encrypt_auth_payload, decrypt_auth_payload


def load_mal_auth():
    with _constants.MAL_AUTH_LOCK:
        if not os.path.exists(_constants.MAL_AUTH_FILE):
            return {}
        try:
            with open(_constants.MAL_AUTH_FILE, "r", encoding="utf-8") as handle:
                raw_data = json.load(handle)
        except (OSError, ValueError):
            return {}

        if not isinstance(raw_data, dict):
            return {}

        decrypted = decrypt_auth_payload(raw_data)

        # Auto-migration: jika berkas di disk masih plaintext lama dan berisi token, segera upgrade ke ciphertext
        if raw_data.get("encrypted") is not True and decrypted.get("access_token"):
            try:
                save_mal_auth(decrypted)
            except Exception as e:
                app_log(f"Auto-migrasi enkripsi kredensial MAL gagal: {e}", level=logging.WARNING)

        return decrypted


def save_mal_auth(auth_data):
    with _constants.MAL_AUTH_LOCK:
        if not auth_data:
            atomic_write_json_file(_constants.MAL_AUTH_FILE, {}, "MyAnimeList Auth")
            return

        envelope = encrypt_auth_payload(auth_data)
        atomic_write_json_file(_constants.MAL_AUTH_FILE, envelope, "MyAnimeList Auth")


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
    from anibase.metadata import resolve_episode_metadata
    meta = resolve_episode_metadata(anime_name, episode)
    mal_id = meta.get("mal_id")
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


def get_user_anime_review(anime_name):
    """Mengambil rating dan review lokal untuk sebuah anime jika ada."""
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT mal_id, score, comments, updated_at FROM anime_user_reviews WHERE anime_name = ?",
                (anime_name,)
            ).fetchone()
            if row:
                return {
                    "anime_name": anime_name,
                    "mal_id": row[0],
                    "score": row[1],
                    "comments": row[2] or "",
                    "updated_at": row[3]
                }
    except Exception:
        pass
    return None


def save_user_anime_review(anime_name, mal_id=None, score=0, comments=""):
    """Menyimpan rating dan review lokal pengguna ke SQLite."""
    try:
        with db_connection() as conn:
            conn.execute("""
                INSERT INTO anime_user_reviews (anime_name, mal_id, score, comments, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(anime_name) DO UPDATE SET
                    mal_id = COALESCE(excluded.mal_id, anime_user_reviews.mal_id),
                    score = excluded.score,
                    comments = excluded.comments,
                    updated_at = excluded.updated_at
            """, (anime_name, int(mal_id) if mal_id else None, int(score or 0), str(comments or ""), time.time()))
            return True
    except Exception as e:
        app_log(f"Failed to save user review locally: {e}", "WARN")
        return False


_MAL_USER_ANIME_STATUS_CACHE = {}
_MAL_USER_ANIME_STATUS_CACHE_LOCK = threading.Lock()
_MAL_USER_ANIME_STATUS_CACHE_TTL = 3600  # 1 hour


def get_mal_user_anime_status(mal_id, token=None, force=False):
    """Mengecek status anime di daftar MyAnimeList pengguna (misal: 'completed', 'watching', dll).
    Menggunakan in-memory cache dan cache daftar anime lokal sebelum memanggil API.
    """
    if not mal_id:
        return None
    try:
        norm_id = int(mal_id)
    except (ValueError, TypeError):
        return None

    now = time.time()
    with _MAL_USER_ANIME_STATUS_CACHE_LOCK:
        cached = _MAL_USER_ANIME_STATUS_CACHE.get(norm_id)
        if not force and cached and (now - cached.get("cached_at", 0) < _MAL_USER_ANIME_STATUS_CACHE_TTL):
            return cached.get("data")

    # 1. Cek dari cache animelist 'completed' dan 'watching' lokal jika tersedia
    for list_type in ("completed", "watching"):
        try:
            cached_list = load_mal_animelist_cache(list_type)
            if cached_list and isinstance(cached_list.get("items"), list):
                for item in cached_list["items"]:
                    node = item.get("node") or {}
                    if node.get("id") == norm_id:
                        status_obj = item.get("list_status") or {"status": list_type}
                        with _MAL_USER_ANIME_STATUS_CACHE_LOCK:
                            _MAL_USER_ANIME_STATUS_CACHE[norm_id] = {"cached_at": now, "data": status_obj}
                        return status_obj
        except Exception:
            pass

    # 2. Query langsung ke API MyAnimeList v2: GET /anime/{mal_id}?fields=my_list_status
    if token is None:
        token = get_valid_mal_token()
    if not token:
        return None

    url = f"{_constants.MAL_API_BASE_URL}/anime/{norm_id}?fields=my_list_status"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            status_obj = data.get("my_list_status")
            with _MAL_USER_ANIME_STATUS_CACHE_LOCK:
                _MAL_USER_ANIME_STATUS_CACHE[norm_id] = {"cached_at": now, "data": status_obj}
            return status_obj
        elif resp.status_code == 404:
            with _MAL_USER_ANIME_STATUS_CACHE_LOCK:
                _MAL_USER_ANIME_STATUS_CACHE[norm_id] = {"cached_at": now, "data": None}
            return None
    except Exception as e:
        app_log(f"Failed to check MAL user status for anime {norm_id}: {e}", "DEBUG")

    return None


def update_mal_user_anime_status(mal_id, num_watched=None, score=None, status=None, comments=None, anime_name=None, is_rewatching=None):
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
    if is_rewatching is not None:
        payload["is_rewatching"] = "true" if is_rewatching else "false"
    if comments is not None:
        payload["comments"] = str(comments)

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
            with _MAL_USER_ANIME_STATUS_CACHE_LOCK:
                _MAL_USER_ANIME_STATUS_CACHE[int(mal_id)] = {
                    "cached_at": time.time(),
                    "data": resp.json()
                }
            if anime_name and (score is not None or comments is not None):
                save_user_anime_review(anime_name, mal_id=mal_id, score=score or 0, comments=comments or "")
            return {"ok": True, "data": resp.json()}
        return {"ok": False, "reason": f"http_{resp.status_code}", "text": resp.text}
    except Exception as e:
        return {"ok": False, "reason": "network_error", "error": str(e)}



def enqueue_pending_scrobble(anime_name, episode, ep_num=None, mal_id=None, status="watching", error_message=""):
    """Menyimpan permintaan scrobble yang gagal ke tabel antrean SQLite lokal."""
    from anibase.metadata import (
        get_cached_anilist_info,
        get_cached_metadata_only,
        normalize_provider_id,
        get_metadata_mapping,
    )
    if ep_num is None:
        ep_num = get_episode_number(os.path.basename(episode))
    if not ep_num or ep_num <= 0:
        return False

    ep_num = int(ep_num)

    if not mal_id:
        from anibase.metadata import resolve_episode_metadata
        meta = resolve_episode_metadata(anime_name, episode)
        mal_id = meta.get("mal_id")

    now = time.time()
    try:
        with db_connection() as conn:
            conn.execute("""
                INSERT INTO mal_pending_scrobbles (
                    anime_name, episode, ep_num, mal_id, status, attempts, last_attempt_at, last_error, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(anime_name, episode) DO UPDATE SET
                    ep_num = excluded.ep_num,
                    mal_id = COALESCE(excluded.mal_id, mal_pending_scrobbles.mal_id),
                    status = excluded.status,
                    attempts = mal_pending_scrobbles.attempts + 1,
                    last_attempt_at = excluded.last_attempt_at,
                    last_error = excluded.last_error
            """, (
                anime_name,
                episode,
                ep_num,
                int(mal_id) if mal_id else None,
                status,
                now,
                str(error_message)[:255] if error_message else None,
                now,
            ))
        app_log(f"Queued pending offline MAL scrobble for {anime_name} Ep {ep_num}.", "INFO")
        return True
    except Exception as e:
        app_log(f"Failed to enqueue pending MAL scrobble: {e}", "WARN")
        return False


def get_pending_scrobbles(limit=50):
    """Mengambil daftar antrean scrobble yang belum berhasil dikirim."""
    try:
        with db_connection() as conn:
            conn.row_factory = lambda cursor, row: {
                "id": row[0],
                "anime_name": row[1],
                "episode": row[2],
                "ep_num": row[3],
                "mal_id": row[4],
                "status": row[5],
                "attempts": row[6],
                "last_attempt_at": row[7],
                "last_error": row[8],
                "created_at": row[9],
            }
            cursor = conn.execute("""
                SELECT id, anime_name, episode, ep_num, mal_id, status, attempts, last_attempt_at, last_error, created_at
                FROM mal_pending_scrobbles
                ORDER BY created_at ASC
                LIMIT ?
            """, (max(1, int(limit)),))
            return cursor.fetchall()
    except Exception as e:
        app_log(f"Failed to query pending MAL scrobbles: {e}", "WARN")
        return []


def remove_pending_scrobble(item_id):
    """Menghapus item dari antrean pending scrobble setelah berhasil disinkronkan."""
    try:
        with db_connection() as conn:
            conn.execute("DELETE FROM mal_pending_scrobbles WHERE id = ?", (int(item_id),))
        return True
    except Exception as e:
        app_log(f"Failed to remove pending scrobble item {item_id}: {e}", "WARN")
        return False


_PENDING_FLUSH_LOCK = threading.Lock()


def flush_pending_scrobbles(max_items=25):
    """Menyinkronkan antrean scrobble offline ke MyAnimeList saat koneksi tersedia."""
    settings = load_settings()
    if not settings.get("mal_scrobble_enabled") or not is_mal_authenticated():
        return {"processed": 0, "succeeded": 0, "failed": 0, "remaining": 0}

    if not _PENDING_FLUSH_LOCK.acquire(blocking=False):
        return {"processed": 0, "succeeded": 0, "failed": 0, "reason": "already_running"}

    try:
        token = get_valid_mal_token()
        if not token:
            return {"processed": 0, "succeeded": 0, "failed": 0, "reason": "token_unavailable"}

        pending_items = get_pending_scrobbles(limit=max_items)
        if not pending_items:
            return {"processed": 0, "succeeded": 0, "failed": 0, "remaining": 0}

        app_log(f"Starting resync of {len(pending_items)} pending MAL scrobbles...", "INFO")
        succeeded = 0
        failed = 0

        for item in pending_items:
            res = scrobble_episode_to_mal(
                anime_name=item["anime_name"],
                episode=item["episode"],
                ep_num=item["ep_num"],
                force=True,
                allow_queue=False,
            )
            if res.get("ok"):
                remove_pending_scrobble(item["id"])
                succeeded += 1
            else:
                failed += 1
                try:
                    with db_connection() as conn:
                        conn.execute("""
                            UPDATE mal_pending_scrobbles
                            SET attempts = attempts + 1,
                                last_attempt_at = ?,
                                last_error = ?
                            WHERE id = ?
                        """, (time.time(), str(res.get("reason", "sync_failed"))[:255], item["id"]))
                except Exception:
                    pass

            time.sleep(0.5)

        remaining = len(get_pending_scrobbles(limit=100))
        app_log(f"Flushed pending MAL scrobbles: {succeeded} synced, {failed} failed, {remaining} remaining.", "INFO")
        return {
            "processed": len(pending_items),
            "succeeded": succeeded,
            "failed": failed,
            "remaining": remaining,
        }
    finally:
        _PENDING_FLUSH_LOCK.release()


def scrobble_episode_to_mal(anime_name, episode, ep_num=None, force=False, allow_queue=True):
    from anibase.metadata import (
        resolve_episode_metadata,
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
        if allow_queue:
            enqueue_pending_scrobble(anime_name, episode, ep_num=ep_num, error_message="token_unavailable")
        return {"ok": False, "reason": "token_unavailable"}

    meta = resolve_episode_metadata(anime_name, episode)
    info = meta.get("info") or {}
    mal_id = meta.get("mal_id")
    metadata_name = meta.get("metadata_name") or anime_name
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

    total_episodes = meta.get("total_episodes") or 0

    user_status_obj = get_mal_user_anime_status(mal_id, token=token)
    current_status = ""
    is_rewatching_flag = False
    if isinstance(user_status_obj, dict):
        current_status = str(user_status_obj.get("status") or "").strip().lower()
        is_rewatching_flag = bool(user_status_obj.get("is_rewatching"))

    local_review = get_user_anime_review(metadata_name)
    if not local_review and metadata_name != anime_name:
        local_review = get_user_anime_review(anime_name)
    was_completed_before = bool(
        current_status == "completed"
        or is_rewatching_flag
        or (local_review and local_review.get("score"))
    )

    rewatch_mode = str(settings.get("mal_rewatch_mode", "rewatch")).strip().lower()
    is_final_episode = bool(
        (anime_name == "Movies")
        or (not is_airing and total_episodes > 0 and ep_num >= total_episodes)
    )

    payload = {
        "num_watched_episodes": int(ep_num)
    }

    if was_completed_before:
        if rewatch_mode == "ignore":
            record_mal_scrobble(mal_id, ep_num, "completed")
            app_log(f"Scrobble skipped for {metadata_name} Ep {ep_num}: anime is already completed on MAL (rewatch_mode=ignore).", "INFO")
            return {"ok": True, "already_completed": True, "mal_id": mal_id, "ep_num": int(ep_num), "status": "completed"}
        elif rewatch_mode == "rewatch":
            mal_status = "completed"
            is_rewatching = not is_final_episode
            payload["status"] = "completed"
            payload["is_rewatching"] = "true" if is_rewatching else "false"
        else:  # "revert"
            mal_status = "completed" if is_final_episode else "watching"
            payload["status"] = mal_status
    else:
        if is_final_episode:
            mal_status = "completed"
        else:
            mal_status = "watching"
        payload["status"] = mal_status

    url = f"{_constants.MAL_API_BASE_URL}/anime/{int(mal_id)}/my_list_status"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.patch(url, data=payload, headers=headers, timeout=12)
            if resp.status_code == 200:
                with _MAL_USER_ANIME_STATUS_CACHE_LOCK:
                    _MAL_USER_ANIME_STATUS_CACHE[int(mal_id)] = {
                        "cached_at": time.time(),
                        "data": resp.json()
                    }
                record_mal_scrobble(mal_id, ep_num, mal_status)
                rewatch_label = " (Rewatching)" if (was_completed_before and mal_status == "completed" and not is_final_episode) else ""
                app_log(f"Scrobbled {metadata_name} Ep {ep_num} ({mal_status}{rewatch_label}) to MyAnimeList successfully.", "INFO")
                return {
                    "ok": True,
                    "mal_id": mal_id,
                    "ep_num": int(ep_num),
                    "status": mal_status,
                    "is_rewatching": payload.get("is_rewatching") == "true"
                }
            elif resp.status_code in {500, 502, 503, 504} and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            else:
                app_log(f"MAL scrobble failed ({resp.status_code}) for {anime_name} Ep {ep_num}: {resp.text}", "WARN")
                if allow_queue and resp.status_code in {408, 429, 500, 502, 503, 504}:
                    enqueue_pending_scrobble(anime_name, episode, ep_num=ep_num, mal_id=mal_id, status=mal_status, error_message=f"http_{resp.status_code}")
                return {"ok": False, "status_code": resp.status_code, "reason": resp.text}
        except (requests.RequestException, OSError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            app_log(f"MAL scrobble network error for {anime_name} Ep {ep_num}: {error}", "WARN")
            if allow_queue:
                enqueue_pending_scrobble(anime_name, episode, ep_num=ep_num, mal_id=mal_id, status=mal_status, error_message=str(error))
            return {"ok": False, "reason": str(error)}

    if allow_queue:
        enqueue_pending_scrobble(anime_name, episode, ep_num=ep_num, mal_id=mal_id, status=mal_status, error_message=str(last_error) if last_error else "unknown_error")
    return {"ok": False, "reason": str(last_error) if last_error else "unknown_error"}


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

