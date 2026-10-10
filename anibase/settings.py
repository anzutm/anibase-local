# anibase/settings.py
"""Manajemen konfigurasi AniBase: membaca, menyimpan, menerapkan,
dan memvalidasi settings.json."""

import os
import json
import secrets
from flask import request

import anibase.constants as constants
from anibase.utils import (
    normalize_bool_setting,
    normalize_int_setting,
    normalize_screenshot_filename_prefix,
    normalize_library_path,
    normalize_library_paths,
    normalize_shortcuts,
    atomic_write_json_file,
)

def get_default_settings():
    return {
        "setup_completed": False,
        "library_paths": [],
        "watchlist_path": "",
        "ongoing_path": "",
        "movie_path": "",
        "vlc_path": "",
        "screenshot_folder": "",
        "screenshot_filename_prefix": "vlcsnap",
        "discord_rpc_enabled": False,
        "discord_client_id": "",
        "discord_timer_mode": "remaining",
        "lan_access_enabled": False,
        "theme_preset": "dark-blue",
        "auto_import_enabled": False,
        "auto_import_downloads_path": "",
        "auto_import_destination_root": "",
        "auto_import_interval_seconds": 15,
        "auto_import_stable_seconds": 60,
        "auto_import_create_ongoing_folders": False,
        "auto_import_mappings": {},
        "auto_import_recent_imports": [],
        "auto_import_unmatched": [],
        "anilist_mappings": {},
        "episode_display_names": {},
        "action_token": "",
        "mal_scrobble_enabled": False,
        "mal_client_id": "",
        "mal_scrobble_threshold": 90,
        "shortcuts": constants.DEFAULT_PLAYER_SHORTCUTS.copy()
    }

def get_settings_library_paths(settings):
    if isinstance(settings.get("library_paths"), list):
        return normalize_library_paths(settings.get("library_paths"))

    return normalize_library_paths([
        settings.get("watchlist_path", ""),
        settings.get("ongoing_path", "")
    ])

def get_configured_auto_import_destination(settings):
    destination_root = normalize_library_path(
        settings.get("auto_import_destination_root", "")
    )
    if not destination_root:
        return ""

    destination_key = os.path.normcase(destination_root)
    library_paths = get_settings_library_paths(settings)
    library_keys = {
        os.path.normcase(path)
        for path in library_paths
    }
    if destination_key not in library_keys:
        return ""

    return destination_root

def is_setup_complete(settings=None):
    settings = settings if isinstance(settings, dict) else load_settings()
    return bool(settings.get("setup_completed")) or bool(get_settings_library_paths(settings))

def load_settings():
    defaults = get_default_settings()

    with constants.SETTINGS_LOCK:
        if not os.path.exists(constants.SETTINGS_FILE):
            return defaults

        try:
            with open(constants.SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            return defaults

    if not isinstance(settings, dict):
        return defaults

    merged = defaults.copy()
    merged.update(settings)
    merged["library_paths"] = get_settings_library_paths(merged)
    merged["watchlist_path"] = merged["library_paths"][0] if merged["library_paths"] else ""
    merged["ongoing_path"] = merged["library_paths"][1] if len(merged["library_paths"]) > 1 else ""
    merged["setup_completed"] = normalize_bool_setting(
        merged.get("setup_completed"),
        bool(merged["library_paths"])
    )
    if merged.get("theme_preset") not in constants.THEME_PRESETS:
        merged["theme_preset"] = defaults["theme_preset"]
    merged["lan_access_enabled"] = normalize_bool_setting(
        merged.get("lan_access_enabled"),
        defaults["lan_access_enabled"]
    )
    merged["screenshot_folder"] = normalize_library_path(
        merged.get("screenshot_folder")
    )
    merged["screenshot_filename_prefix"] = normalize_screenshot_filename_prefix(
        merged.get("screenshot_filename_prefix"),
        defaults["screenshot_filename_prefix"]
    )
    merged["auto_import_enabled"] = normalize_bool_setting(
        merged.get("auto_import_enabled"),
        defaults["auto_import_enabled"]
    )
    merged["auto_import_downloads_path"] = normalize_library_path(
        merged.get("auto_import_downloads_path")
    )
    merged["auto_import_destination_root"] = normalize_library_path(
        merged.get("auto_import_destination_root")
    )
    merged["auto_import_interval_seconds"] = normalize_int_setting(
        merged.get("auto_import_interval_seconds"),
        defaults["auto_import_interval_seconds"],
        5,
        3600
    )
    merged["auto_import_stable_seconds"] = normalize_int_setting(
        merged.get("auto_import_stable_seconds"),
        defaults["auto_import_stable_seconds"],
        10,
        86400
    )
    merged["auto_import_create_ongoing_folders"] = normalize_bool_setting(
        merged.get("auto_import_create_ongoing_folders"),
        defaults["auto_import_create_ongoing_folders"]
    )
    if not isinstance(merged.get("auto_import_mappings"), dict):
        merged["auto_import_mappings"] = {}
    if not isinstance(merged.get("auto_import_recent_imports"), list):
        merged["auto_import_recent_imports"] = []
    if not isinstance(merged.get("auto_import_unmatched"), list):
        merged["auto_import_unmatched"] = []
    if not isinstance(merged.get("anilist_mappings"), dict):
        merged["anilist_mappings"] = {}
    if not isinstance(merged.get("episode_display_names"), dict):
        merged["episode_display_names"] = {}
    if not isinstance(merged.get("action_token"), str):
        merged["action_token"] = ""
    merged["mal_scrobble_enabled"] = normalize_bool_setting(
        merged.get("mal_scrobble_enabled"),
        defaults["mal_scrobble_enabled"]
    )
    merged["mal_client_id"] = str(merged.get("mal_client_id", "")).strip()
    merged["mal_scrobble_threshold"] = normalize_int_setting(
        merged.get("mal_scrobble_threshold"),
        defaults["mal_scrobble_threshold"],
        50,
        100
    )
    merged["shortcuts"] = normalize_shortcuts(merged.get("shortcuts"))
    return merged

def save_settings(settings):
    with constants.SETTINGS_LOCK:
        atomic_write_json_file(constants.SETTINGS_FILE, settings, "Settings")

def get_action_token():
    settings = load_settings()
    token = settings.get("action_token", "")

    if isinstance(token, str) and token:
        return token

    token = secrets.token_urlsafe(32)
    settings["action_token"] = token
    save_settings(settings)
    return token

def get_submitted_action_token():
    token = request.headers.get("X-AniBase-Action-Token", "") or request.headers.get("X-Action-Token", "")

    if token:
        return token

    if request.form:
        return request.form.get("action_token", "")

    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data.get("action_token", "")

    return ""

def validate_action_token():
    expected = get_action_token()
    submitted = get_submitted_action_token()

    return (
        isinstance(submitted, str)
        and bool(submitted)
        and secrets.compare_digest(submitted, expected)
    )

def get_effective_mal_client_id(settings=None):
    if settings is None:
        settings = load_settings()
    custom_id = str(settings.get("mal_client_id", "")).strip() if isinstance(settings, dict) else ""
    return custom_id or constants.DEFAULT_MAL_CLIENT_ID

def apply_settings(settings, reset_discord_rpc_fn=None):
    defaults = get_default_settings()
    merged = defaults.copy()
    merged.update(settings or {})
    constants.DISCORD_TIMER_MODE = "elapsed" if merged.get("discord_timer_mode") == "elapsed" else "remaining"

    constants.ANIME_PATHS = get_settings_library_paths(merged)
    constants.MOVIE_PATH = normalize_library_path(merged["movie_path"])
    constants.VLC_PATH = normalize_library_path(merged["vlc_path"])

    if reset_discord_rpc_fn is None:
        try:
            import main
            reset_discord_rpc_fn = getattr(main, "reset_discord_rpc", None)
        except Exception:
            reset_discord_rpc_fn = None

    discord_client_id = str(merged.get("discord_client_id", "")).strip()
    if discord_client_id != constants.DISCORD_CLIENT_ID:
        if callable(reset_discord_rpc_fn):
            reset_discord_rpc_fn()
        constants.DISCORD_CLIENT_ID = discord_client_id

    constants.DISCORD_RPC_ENABLED = normalize_bool_setting(
        merged.get("discord_rpc_enabled"),
        defaults["discord_rpc_enabled"]
    )
    if not constants.DISCORD_RPC_ENABLED and callable(reset_discord_rpc_fn):
        reset_discord_rpc_fn()
