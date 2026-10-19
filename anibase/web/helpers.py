# anibase/web/helpers.py
"""Web helper functions, decorators, context processors, and query utilities."""

import os
import sys
import re
import json
import sqlite3
import secrets
import functools
import ipaddress
import threading
from urllib.parse import unquote

from flask import request, jsonify, redirect, url_for, render_template
from werkzeug.exceptions import RequestEntityTooLarge

import anibase.constants as constants
from anibase.constants import (
    RESOURCE_DIR,
    MAX_REQUEST_BODY_BYTES,
    THEME_PRESETS,
    SHORTCUT_DEFINITIONS,
    DEFAULT_PLAYER_SHORTCUTS,
    CHARACTER_CACHE,
    DEFAULT_MAL_CLIENT_ID,
    VIDEO_EXTENSIONS,
)
from anibase.logging import app_log
from anibase.utils import (
    is_shutdown_requested,
    is_resolved_path_inside,
    safe_cache_name,
    clean_movie_title,
    safe_join_media_path,
)
from anibase.settings import (
    load_settings,
    get_default_settings,
    get_settings_library_paths,
    get_action_token,
    get_submitted_action_token,
    validate_action_token,
    is_setup_complete,
    get_effective_mal_client_id,
    apply_settings as core_apply_settings,
)
from anibase.db import db_connection
from anibase.watch import (
    load_watch_status,
    get_episode_watch_status,
)
from anibase.ffmpeg import get_media_dependency_diagnostics
from anibase.metadata import (
    get_cached_metadata_only,
    get_cached_anilist_info,
)
from anibase.integrations.mal import load_mal_auth
from anibase.integrations.discord import reset_discord_rpc
from anibase.scanner import is_configured_movie_folder_name

_app = None


def set_app(app):
    global _app
    _app = app


def json_error(error, message, status_code):
    return jsonify({
        "ok": False,
        "error": error,
        "message": message
    }), status_code


def handle_request_entity_too_large(_error):
    return json_error(
        "payload_too_large",
        "Request body is too large.",
        413
    )


def get_json_body():
    try:
        data = request.get_json(silent=False)
    except RequestEntityTooLarge:
        raise
    except Exception:
        return None, json_error(
            "invalid_json",
            "Request body must be valid JSON.",
            400
        )

    if not isinstance(data, dict):
        return None, json_error(
            "invalid_json",
            "Request body must be a JSON object.",
            400
        )

    return data, None


def require_action_token(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not validate_action_token():
            return json_error(
                "invalid_action_token",
                "Invalid or missing action token.",
                403
            )

        return func(*args, **kwargs)

    return wrapper


def inject_action_token():
    return {
        "action_token": get_action_token()
    }


def internal_url_for(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except RuntimeError:
        target_app = _app
        if target_app is None:
            import main
            target_app = getattr(main, "app", None)
        if target_app is not None:
            with target_app.test_request_context():
                return url_for(endpoint, **values)
        raise


def is_local_client_address(address):
    if not address:
        return False

    normalized_address = address.split("%", 1)[0]
    if normalized_address.lower() == "localhost":
        return True

    try:
        ip = ipaddress.ip_address(normalized_address)
    except ValueError:
        return False

    if ip.is_loopback:
        return True

    mapped_ip = getattr(ip, "ipv4_mapped", None)
    return bool(mapped_ip and mapped_ip.is_loopback)


def is_local_request():
    return is_local_client_address(request.remote_addr)


def host_only(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if is_local_request():
            return func(*args, **kwargs)

        if request.accept_mimetypes.accept_html and not request.accept_mimetypes.accept_json:
            return "This action is only available on the server device.", 403

        return json_error(
            "host_only",
            "This action is only available on the server device.",
            403
        )

    return wrapper


def is_lan_client_address(address):
    if is_local_client_address(address):
        return True

    if not address:
        return False

    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False

    return bool(ip.is_private or ip.is_link_local)


def reject_new_work_during_shutdown():
    if is_shutdown_requested():
        return json_error(
            "shutting_down",
            "AniBase is shutting down.",
            503
        )

    return None


def block_lan_access_when_disabled():
    if is_local_client_address(request.remote_addr):
        return None

    if load_settings().get("lan_access_enabled") and is_lan_client_address(request.remote_addr):
        return None

    return json_error(
        "lan_access_disabled",
        "LAN access is disabled on this server.",
        403
    )


def redirect_to_setup_when_needed():
    allowed_endpoints = {
        "static",
        "favicon",
        "setup_page",
        "setup_sync",
        "pick_settings_folder",
        "pick_settings_file",
    }

    if request.endpoint in allowed_endpoints:
        return None

    if not is_setup_complete():
        return redirect(url_for("setup_page"))

    return None


def apply_settings(settings):
    core_apply_settings(settings, reset_discord_rpc_fn=reset_discord_rpc)


def get_current_theme():
    theme = load_settings().get("theme_preset", "dark-blue")
    if theme not in THEME_PRESETS:
        return "dark-blue"
    return theme


def inject_theme():
    return {
        "current_theme": get_current_theme()
    }


def inject_shortcuts():
    settings = load_settings()
    return {
        "player_shortcuts": settings.get("shortcuts", DEFAULT_PLAYER_SHORTCUTS),
        "shortcut_definitions": SHORTCUT_DEFINITIONS
    }


def inject_mal_user():
    auth = load_mal_auth()
    is_authed = bool(auth.get("access_token"))
    return {
        "mal_authenticated": is_authed,
        "mal_username": auth.get("username", "") if is_authed else "",
        "mal_picture": auth.get("picture", "") if is_authed else "",
    }


def get_character_image_cache_path(anime_name, filename):
    safe_anime = safe_cache_name(anime_name)
    if not safe_anime or safe_anime in {".", ".."}:
        return None

    try:
        decoded_filename = unquote(str(filename or ""))
    except Exception:
        return None

    if (
        not decoded_filename
        or "\x00" in decoded_filename
        or decoded_filename in {".", ".."}
        or "/" in decoded_filename
        or "\\" in decoded_filename
        or os.path.isabs(decoded_filename)
        or re.match(r"^[a-zA-Z]:", decoded_filename)
    ):
        return None

    character_root = os.path.realpath(os.path.abspath(CHARACTER_CACHE))
    anime_dir = os.path.realpath(os.path.abspath(os.path.join(character_root, safe_anime)))
    if (
        not is_resolved_path_inside(character_root, anime_dir)
        or os.path.normcase(character_root) == os.path.normcase(anime_dir)
    ):
        return None

    candidate = os.path.realpath(os.path.abspath(os.path.join(anime_dir, decoded_filename)))
    if (
        not is_resolved_path_inside(character_root, candidate)
        or not is_resolved_path_inside(anime_dir, candidate)
    ):
        return None

    return candidate


def dependency_card_state(result):
    if result.get("available"):
        return "valid"

    if result.get("status") in {"not_configured", "not_found"}:
        return "off"

    return "invalid"


def dependency_card_text(result, available_label="Available"):
    if result.get("available"):
        return available_label

    status = result.get("status")
    if status == "not_configured":
        return "Not configured"
    if status == "not_found":
        return "Not found"
    if status == "path_invalid":
        return "Path invalid"

    return "Error"


def build_settings_status_cards(settings, media_diagnostics=None):
    anime_paths = get_settings_library_paths(settings)
    movie_path = constants.MOVIE_PATH
    media_diagnostics = media_diagnostics or get_media_dependency_diagnostics(settings)
    vlc_status = media_diagnostics["vlc"]

    valid_anime_paths = [
        path
        for path in anime_paths
        if os.path.isdir(path)
    ]

    if valid_anime_paths:
        anime_state = "valid"
        anime_text = "Valid"
        anime_detail = f"{len(valid_anime_paths)} of {len(anime_paths)} folder configured"
    else:
        anime_state = "invalid"
        anime_text = "Invalid"
        anime_detail = "Set at least one anime folder"

    if movie_path:
        movie_valid = os.path.isdir(movie_path)
        movie_state = "valid" if movie_valid else "invalid"
        movie_text = "Valid" if movie_valid else "Invalid"
        movie_detail = "Movie folder found" if movie_valid else "Movie folder not found"
    else:
        movie_state = "off"
        movie_text = "Not set"
        movie_detail = "Movies page will stay empty"

    auto_import_enabled = bool(settings.get("auto_import_enabled"))
    lan_access_enabled = bool(settings.get("lan_access_enabled"))
    discord_enabled = bool(settings.get("discord_rpc_enabled"))
    discord_client_id = str(settings.get("discord_client_id", "")).strip()

    if discord_enabled and discord_client_id:
        discord_state = "valid" if constants.rpc_connected else "off"
        if constants.Presence is None:
            discord_state, discord_text, discord_detail = "invalid", "Update required", "Install the project dependencies to enable Discord Presence."
        elif not discord_client_id.isdigit() or constants.RPC_CONNECTION_STATUS == "invalid_id":
            discord_state, discord_text, discord_detail = "invalid", "Invalid Client ID", "Copy the Application ID from the Discord Developer Portal."
        elif constants.rpc_connected:
            discord_text, discord_detail = "Connected", "Discord is connected; playback updates are enabled."
        elif constants.RPC_CONNECTION_STATUS == "unavailable":
            discord_text, discord_detail = "Discord not available", "Open Discord Desktop on this computer. AniBase retries automatically during playback."
        elif constants.RPC_CONNECTION_STATUS in {"connecting", "retrying"}:
            discord_text, discord_detail = "Reconnecting", "Trying to connect to Discord again automatically."
        else:
            discord_text, discord_detail = "Ready", "Start playback on this computer to connect to Discord."
    elif discord_enabled:
        discord_state = "invalid"
        discord_text = "Needs Client ID"
        discord_detail = "Add a Discord Client ID to connect"
    else:
        discord_state = "off"
        discord_text = "Off"
        discord_detail = "Rich Presence is disabled"

    mal_auth = load_mal_auth()
    mal_enabled = bool(settings.get("mal_scrobble_enabled"))
    mal_client_id = get_effective_mal_client_id(settings)
    if mal_enabled and mal_auth.get("access_token"):
        mal_state = "valid"
        mal_text = f"Connected ({mal_auth.get('username', 'User')})"
        mal_detail = f"Auto-scrobbles when {settings.get('mal_scrobble_threshold', 90)}% watched"
    elif mal_enabled and mal_client_id:
        mal_state = "invalid"
        mal_text = "Not Connected"
        mal_detail = "Click 'Connect MyAnimeList Account' in Settings"
    elif mal_enabled:
        mal_state = "invalid"
        mal_text = "Needs Client ID"
        mal_detail = "Configure MAL Client ID to connect"
    else:
        mal_state = "off"
        mal_text = "Off"
        mal_detail = "Auto-scrobble is disabled"

    return [
        {
            "label": "Anime Library",
            "state": anime_state,
            "text": anime_text,
            "detail": anime_detail
        },
        {
            "label": "Movie Path",
            "state": movie_state,
            "text": movie_text,
            "detail": movie_detail
        },
        {
            "label": "Player",
            "state": dependency_card_state(vlc_status),
            "text": dependency_card_text(vlc_status, "Available"),
            "detail": vlc_status["message"]
        },
        {
            "label": "Discord",
            "state": discord_state,
            "text": discord_text,
            "detail": discord_detail
        },
        {
            "label": "MyAnimeList",
            "state": mal_state,
            "text": mal_text,
            "detail": mal_detail
        },
        {
            "label": "Auto Import",
            "state": "valid" if auto_import_enabled else "off",
            "text": "Active" if auto_import_enabled else "Off",
            "detail": "Download folder scan is active" if auto_import_enabled else "Manual scan still available"
        },
        {
            "label": "LAN Access",
            "state": "valid" if lan_access_enabled else "off",
            "text": "On" if lan_access_enabled else "Off",
            "detail": "Same-network devices can open the app" if lan_access_enabled else "Only this device can open the app"
        }
    ]


def is_localhost_request():
    return is_local_request()


def pick_windows_path(picker_type):
    if not is_localhost_request():
        return jsonify({
            "ok": False,
            "path": "",
            "error": "host_only",
            "message": "Browse picker is only available on the server device. Type the path manually from LAN."
        }), 403

    root = None

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()

        if picker_type == "file":
            selected_path = filedialog.askopenfilename(
                parent=root,
                filetypes=[
                    ("Executable files", "*.exe"),
                    ("All files", "*.*")
                ]
            )
        else:
            selected_path = filedialog.askdirectory(parent=root)

        return jsonify({"ok": True, "path": selected_path or ""})

    except Exception as e:
        app_log(f"Path picker failed: {e}", "ERROR")
        return jsonify({
            "ok": False,
            "path": "",
            "error": "picker_failed",
            "message": "Unable to open picker. Type the path manually."
        }), 500

    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def get_anime_watch_summary(anime_name, total_episodes, anime_status=None, status_data=None):
    status_data = status_data if isinstance(status_data, dict) else load_watch_status()
    anime_status_data = status_data.get(anime_name, {})
    if not isinstance(anime_status_data, dict):
        anime_status_data = {}

    watched_count = sum(
        1
        for episode_status in anime_status_data.values()
        if isinstance(episode_status, dict) and episode_status.get("watched")
    )
    total_count = max(0, int(total_episodes or 0))
    watched_count = min(watched_count, total_count) if total_count else watched_count
    progress_percent = round((watched_count / total_count) * 100) if total_count else 0

    status_name = (anime_status or "").strip().upper()
    is_all_watched = bool(total_count and watched_count >= total_count)

    if total_count and watched_count == 0:
        watch_status_label = "NOT STARTED"
        watch_status_kind = "not_started"
    elif is_all_watched and status_name == "RELEASING":
        watch_status_label = "UP TO DATE"
        watch_status_kind = "up_to_date"
    elif is_all_watched and status_name == "FINISHED":
        watch_status_label = "COMPLETED"
        watch_status_kind = "completed"
    elif is_all_watched:
        watch_status_label = "COMPLETED"
        watch_status_kind = "completed"
    else:
        watch_status_label = "WATCHING"
        watch_status_kind = "watching"

    return {
        "watched_episodes": watched_count,
        "total_episodes": total_count,
        "watch_progress_percent": progress_percent,
        "watch_progress_label": f"{watched_count}/{total_count}" if total_count else "0/0",
        "watch_status_label": watch_status_label,
        "watch_status_kind": watch_status_kind,
        "all_watched": is_all_watched
    }


def get_anime():
    """Mengambil daftar anime dari database SQLite (Instan)."""
    anime_list = []
    status_data = load_watch_status()
    try:
        with db_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM anime_library ORDER BY name COLLATE NOCASE")
            for row in cursor:
                item = dict(row)
                if is_configured_movie_folder_name(item["name"]):
                    continue

                watch_summary = get_anime_watch_summary(
                    item["name"],
                    item["episodes"],
                    anime_status=item.get("status"),
                    status_data=status_data
                )
                metadata = get_cached_metadata_only(item["name"]) or {}
                if (
                    (item.get("status") or "").upper() == "RELEASING"
                    and not metadata.get("next_airing")
                ):
                    metadata = get_cached_anilist_info(item["name"]) or metadata

                anime_list.append({
                    "name": item["name"],
                    "episodes": item["episodes"],
                    "score": item["score"],
                    "year": item["year"],
                    "season": item["season"],
                    "status": item["status"],
                    "format": metadata.get("format"),
                    "next_airing": metadata.get("next_airing"),
                    **watch_summary
                })
    except Exception as e:
        app_log(f"Database query error: {e}", "ERROR")
    return anime_list


def get_movies():
    movie_path = constants.MOVIE_PATH
    movies = []

    if not movie_path or not os.path.isdir(movie_path):
        return movies

    for file in os.listdir(movie_path):
        if file.lower().endswith(VIDEO_EXTENSIONS):
            clean_title = clean_movie_title(file)
            movie_info = get_cached_anilist_info(clean_title)
            episode_status = get_episode_watch_status("Movies", file)
            progress = float(episode_status.get("progress", 0) or 0)
            watched = bool(
                episode_status.get("watched")
                or progress >= 90
            )
            watch_progress_label = "1/1" if watched else "0/1"
            if watched:
                watch_status_label = "COMPLETED"
                watch_status_kind = "completed"
            elif progress > 0:
                watch_status_label = "WATCHING"
                watch_status_kind = "watching"
            else:
                watch_status_label = "NOT STARTED"
                watch_status_kind = "not_started"

            movies.append({
                "title": clean_title,
                "file": file,
                "poster": internal_url_for("poster", anime_name=clean_title),
                "banner": movie_info.get("banner") if movie_info else None,
                "score": movie_info.get("score") if movie_info else None,
                "year": movie_info.get("year") if movie_info else None,
                "description": movie_info.get("description") if movie_info else None,
                "duration": movie_info.get("duration") if movie_info else None,
                "genres": (movie_info.get("genres") or []) if movie_info else [],
                "watch_progress_label": watch_progress_label,
                "watch_status_label": watch_status_label,
                "watch_status_kind": watch_status_kind
            })

    movies.sort(key=lambda item: item["title"].casefold())
    return movies


def get_watch_status_payload():
    data, error_response = get_json_body()
    if error_response:
        return None, None, None, error_response

    anime_name = data.get("anime_name")
    episode = data.get("episode")

    if not anime_name or not episode:
        return None, None, data, None

    return anime_name, episode, data, None


def get_spotlight_results(query="", limit=20):
    """Search anime, movies, and quick navigation actions for Spotlight Command Palette (Ctrl+K)."""
    norm_query = str(query or "").strip().casefold()

    actions = [
        {
            "type": "action",
            "id": "action_home",
            "title": "Home",
            "subtitle": "Go to Home page and continue watching",
            "url": internal_url_for("index"),
            "icon": "home",
            "keywords": ["home", "main", "dashboard", "index", "catalog"]
        },
        {
            "type": "action",
            "id": "action_movies",
            "title": "Movies",
            "subtitle": "Browse anime movie collection",
            "url": internal_url_for("movies"),
            "icon": "film",
            "keywords": ["movies", "movie", "films", "cinema", "theatrical"]
        },
        {
            "type": "action",
            "id": "action_schedule",
            "title": "Airing Schedule",
            "subtitle": "View anime release calendar and upcoming countdowns",
            "url": internal_url_for("schedule"),
            "icon": "calendar",
            "keywords": ["schedule", "airing", "calendar", "releases", "episodes"]
        },
        {
            "type": "action",
            "id": "action_settings",
            "title": "Settings",
            "subtitle": "Configure libraries, MAL connection, shortcuts, and cache",
            "url": internal_url_for("settings_page"),
            "icon": "gear",
            "keywords": ["settings", "preferences", "config", "configuration", "mal", "shortcuts", "options"]
        },
        {
            "type": "action",
            "id": "action_mal_profile",
            "title": "MyAnimeList Profile",
            "subtitle": "View your MAL statistics and watch profile",
            "url": internal_url_for("mal_profile_page"),
            "icon": "user",
            "keywords": ["mal", "profile", "myanimelist", "account", "stats", "statistics", "user"]
        },
        {
            "type": "action",
            "id": "action_refresh_library",
            "title": "Refresh Library Sync",
            "subtitle": "Scan local directories and synchronize newly added anime",
            "url": "#action:refresh_library",
            "icon": "refresh",
            "keywords": ["refresh", "sync", "rescan", "scan", "update", "library"]
        },
    ]

    matched_actions = []
    for action in actions:
        if not norm_query:
            matched_actions.append(action)
        else:
            words = [action["title"].casefold(), action["subtitle"].casefold()] + action["keywords"]
            if any(norm_query in w for w in words):
                matched_actions.append(action)

    all_anime = get_anime()
    matched_anime = []

    for item in all_anime:
        name = item["name"]
        metadata = get_cached_metadata_only(name) or {}
        display_title = metadata.get("title") or name
        score = item.get("score")
        genres = metadata.get("genres") or []
        if isinstance(genres, str):
            try:
                genres = json.loads(genres)
            except Exception:
                genres = [g.strip() for g in genres.split(",") if g.strip()]

        poster_url = internal_url_for("poster", anime_name=name)
        detail_url = internal_url_for("anime_detail", anime_name=name)

        if not norm_query:
            # Query kosong: prioritaskan anime yang sedang ditonton
            relevance = 10 if item.get("watch_status_kind") == "watching" else 5
            matched_anime.append({
                "type": "anime",
                "name": name,
                "title": display_title,
                "folder_name": name,
                "url": detail_url,
                "poster": poster_url,
                "year": item.get("year"),
                "score": f"{score:.1f}" if score else None,
                "episodes": item.get("episodes"),
                "genres": genres[:3],
                "watch_status_label": item.get("watch_status_label"),
                "watch_status_kind": item.get("watch_status_kind"),
                "watch_progress_label": item.get("watch_progress_label"),
                "relevance": relevance
            })
        else:
            searchable_text = f"{name} {display_title} {' '.join(genres)}".casefold()
            synonyms = metadata.get("synonyms") or []
            if isinstance(synonyms, list):
                searchable_text += " " + " ".join(str(s).casefold() for s in synonyms)

            if norm_query in searchable_text:
                relevance = 0
                if name.casefold().startswith(norm_query) or display_title.casefold().startswith(norm_query):
                    relevance += 100
                elif norm_query in display_title.casefold() or norm_query in name.casefold():
                    relevance += 50
                if any(norm_query in g.casefold() for g in genres):
                    relevance += 20
                if item.get("watch_status_kind") == "watching":
                    relevance += 10

                matched_anime.append({
                    "type": "anime",
                    "name": name,
                    "title": display_title,
                    "folder_name": name,
                    "url": detail_url,
                    "poster": poster_url,
                    "year": item.get("year"),
                    "score": f"{score:.1f}" if score else None,
                    "episodes": item.get("episodes"),
                    "genres": genres[:3],
                    "watch_status_label": item.get("watch_status_label"),
                    "watch_status_kind": item.get("watch_status_kind"),
                    "watch_progress_label": item.get("watch_progress_label"),
                    "relevance": relevance
                })

    matched_anime.sort(key=lambda x: x["relevance"], reverse=True)

    all_movies = get_movies()
    matched_movies = []
    for m in all_movies:
        m_title = m.get("title", "")
        m_genres = m.get("genres") or []
        m_file = m.get("file", "")
        player_url = internal_url_for("player", anime_name="Movies", episode=m_file) if m_file else internal_url_for("movies")
        if not norm_query:
            matched_movies.append({
                "type": "movie",
                "title": m_title,
                "file": m_file,
                "url": player_url,
                "detail_url": internal_url_for("movies"),
                "poster": m.get("poster"),
                "year": m.get("year"),
                "score": f"{m['score']:.1f}" if m.get("score") else None,
                "genres": m_genres[:3],
                "watch_status_label": m.get("watch_status_label"),
                "watch_status_kind": m.get("watch_status_kind"),
                "relevance": 5
            })
        else:
            m_text = f"{m_title} {m_file} {' '.join(m_genres)}".casefold()
            if norm_query in m_text:
                relevance = 50 if m_title.casefold().startswith(norm_query) else 25
                matched_movies.append({
                    "type": "movie",
                    "title": m_title,
                    "file": m_file,
                    "url": player_url,
                    "detail_url": internal_url_for("movies"),
                    "poster": m.get("poster"),
                    "year": m.get("year"),
                    "score": f"{m['score']:.1f}" if m.get("score") else None,
                    "genres": m_genres[:3],
                    "watch_status_label": m.get("watch_status_label"),
                    "watch_status_kind": m.get("watch_status_kind"),
                    "relevance": relevance
                })

    matched_movies.sort(key=lambda x: x["relevance"], reverse=True)

    return {
        "ok": True,
        "query": query,
        "results": {
            "anime": matched_anime[:limit],
            "movies": matched_movies[:5],
            "actions": matched_actions[:6]
        },
        "total": len(matched_anime[:limit]) + len(matched_movies[:5]) + len(matched_actions[:6])
    }

