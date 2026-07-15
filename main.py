from flask import Flask, render_template, redirect, jsonify, send_file, request, abort, url_for
import flask.cli
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import subprocess
import requests
import mimetypes
import re
import json
import sqlite3
import hashlib
import random
import base64
import shutil
import threading
import ipaddress
import secrets
import functools
import html
from contextlib import contextmanager
from urllib.parse import unquote, urlparse
from difflib import SequenceMatcher
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
from datetime import datetime, timedelta
 
try:
    from pypresence import Presence
except ImportError:
    Presence = None

DISCORD_CLIENT_ID = ""
rpc = None
rpc_connected = False
RPC_LOCK = threading.RLock()

RPC_START_TIME = None
CURRENT_RPC_ANIME = None
CURRENT_RPC_OWNER = None

APP_DATA_DIR_NAME = "AniBase"
LEGACY_APP_DATA_DIR_NAMES = ("".join(("Anzu", "Anime Server")),)
PROJECT_RUNTIME_ENV_NAMES = (
    "ANIBASE_USE_PROJECT_RUNTIME",
)

def hidden_subprocess_kwargs():
    if os.name != "nt":
        return {}

    kwargs = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window

    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs

def with_hidden_subprocess_window(kwargs):
    merged = dict(kwargs)
    for key, value in hidden_subprocess_kwargs().items():
        if key == "creationflags":
            merged[key] = merged.get(key, 0) | value
        else:
            merged.setdefault(key, value)
    return merged

def run_hidden_subprocess(args, **kwargs):
    return subprocess.run(args, **with_hidden_subprocess_window(kwargs))

def popen_hidden_subprocess(args, **kwargs):
    return subprocess.Popen(args, **with_hidden_subprocess_window(kwargs))

def get_application_dir():
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))

def get_resource_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.abspath(sys._MEIPASS)
    return get_application_dir()

def get_media_tool_path(name):
    executable = f"{name}.exe" if os.name == "nt" else name
    bundled_path = os.path.join(get_application_dir(), "tools", executable)
    if getattr(sys, "frozen", False) and os.path.isfile(bundled_path):
        return bundled_path
    return shutil.which(name) or name

def get_local_app_data_dir(app_data_dir_name):
    if os.name != "nt":
        xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
        if not xdg_data_home:
            xdg_data_home = os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.abspath(os.path.join(xdg_data_home, app_data_dir_name))

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return os.path.abspath(os.path.join(local_app_data, app_data_dir_name))

    return os.path.abspath(
        os.path.join(os.path.expanduser("~"), "AppData", "Local", app_data_dir_name)
    )

def get_user_data_dir():
    enabled_values = {"1", "true", "yes", "on"}
    use_project_runtime = any(
        os.environ.get(env_name, "").strip().lower() in enabled_values
        for env_name in PROJECT_RUNTIME_ENV_NAMES
    )
    if use_project_runtime and not getattr(sys, "frozen", False):
        return get_application_dir()

    return get_local_app_data_dir(APP_DATA_DIR_NAME)

APP_DIR = get_application_dir()
RESOURCE_DIR = get_resource_dir()
USER_DATA_DIR = get_user_data_dir()

# Backward-compatible alias for existing resource-path checks.
BASE_DIR = APP_DIR

app = Flask(
    __name__,
    template_folder=os.path.join(RESOURCE_DIR, "templates"),
    static_folder=os.path.join(RESOURCE_DIR, "static"),
)

LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "anibase.log")
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s:%(threadName)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 3 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOGGER_NAME = "anibase"
LOGGER = logging.getLogger(LOGGER_NAME)
LOGGING_CONFIGURED = False
LOGGING_CONFIG = None
STARTUP_SUMMARY_LOGGED = False

DISCORD_RPC_ENABLED = True

CACHE_DIR = os.path.join(USER_DATA_DIR, "cache")
RUNTIME_DIR = os.path.join(USER_DATA_DIR, "runtime")
TEMP_DIR = os.path.join(USER_DATA_DIR, "temp")
POSTER_CACHE = os.path.join(CACHE_DIR, "posters")
BANNER_CACHE = os.path.join(CACHE_DIR, "banners")
THUMBNAIL_CACHE = os.path.join(CACHE_DIR, "thumbnails")
METADATA_CACHE = os.path.join(CACHE_DIR, "metadata")
CHARACTER_CACHE = os.path.join(CACHE_DIR, "characters")
EPISODE_CACHE = os.path.join(CACHE_DIR, "episodes")
SUBTITLE_CACHE = os.path.join(CACHE_DIR, "subtitles")
SEIYUU_CACHE = os.path.join(CACHE_DIR, "seiyuu")
DB_PATH = os.path.join(CACHE_DIR, "library.db")
WATCH_HISTORY_FILE = os.path.join(CACHE_DIR, "watch_history.json")
WATCH_STATUS_FILE = os.path.join(CACHE_DIR, "watch_status.json")
SETTINGS_FILE = os.path.join(CACHE_DIR, "settings.json")
WATCH_DATA_LOCK = threading.RLock()
SETTINGS_LOCK = threading.RLock()
EPISODE_CACHE_LOCK = threading.RLock()
SCHEDULE_CACHE_LOCK = threading.RLock()
SCHEDULE_CACHE = {
    "expires_at": 0,
    "airing_list": [],
    "error": None
}
SCHEDULE_CACHE_TTL_SECONDS = 300
SCHEDULE_LOOKAHEAD_DAYS = 3
STUDIO_PROJECT_CACHE_TTL_SECONDS = 86400
STUDIO_PROJECT_LIMIT = 80
SEIYUU_CACHE_TTL_SECONDS = 86400
SEIYUU_ROLE_PAGE_LIMIT = 3
JIKAN_BASE_URL = "https://api.jikan.moe/v4"
LIBRARY_OBSERVER = None
LIBRARY_OBSERVER_LOCK = threading.RLock()
LIBRARY_SYNC_LOCK = threading.Lock()
SETUP_SYNC_STATE_LOCK = threading.Lock()
SETUP_SYNC_STATE = {
    "running": False,
    "done": False,
    "error": "",
    "stage": "idle",
    "current": 0,
    "total": 0,
    "anime_count": 0,
}
LIBRARY_SYNC_DEBOUNCE_SECONDS = 3
LIBRARY_SYNC_DEBOUNCE_LOCK = threading.RLock()
LIBRARY_SYNC_TIMERS = {}
AUTO_IMPORT_THREAD = None
AUTO_IMPORT_THREAD_LOCK = threading.RLock()
AUTO_IMPORT_STATE_LOCK = threading.RLock()
AUTO_IMPORT_FILE_STATE = {}
AUTO_IMPORT_LOG_STATE = {}
AUTO_IMPORT_LOG_TTL_SECONDS = 300
SHUTDOWN_EVENT = threading.Event()
VERBOSE_LOGS = os.environ.get("ANIBASE_VERBOSE_LOGS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on"
}
THEME_PRESETS = {
    "dark-blue",
    "dark-orange"
}

def get_protected_cache_files():
    return {
        os.path.abspath(DB_PATH),
        os.path.abspath(SETTINGS_FILE),
        os.path.abspath(WATCH_HISTORY_FILE),
        os.path.abspath(WATCH_STATUS_FILE),
        os.path.abspath(f"{WATCH_HISTORY_FILE}.bak"),
        os.path.abspath(f"{WATCH_STATUS_FILE}.bak"),
    }

def get_active_cache_dir():
    return os.path.abspath(os.path.dirname(DB_PATH) or CACHE_DIR)

@contextmanager
def db_connection(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def request_shutdown():
    SHUTDOWN_EVENT.set()

def is_shutdown_requested():
    return SHUTDOWN_EVENT.is_set()

def wait_for_shutdown(timeout):
    return SHUTDOWN_EVENT.wait(max(0, timeout or 0))

def configure_logging(log_dir=None, max_bytes=LOG_MAX_BYTES, backup_count=LOG_BACKUP_COUNT, level=None):
    global LOGGING_CONFIGURED, LOGGING_CONFIG

    log_dir = os.path.abspath(log_dir or LOG_DIR)
    log_file = os.path.join(log_dir, "anibase.log")
    level_name = (level or os.environ.get("ANIBASE_LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    config = (log_file, int(max_bytes), int(backup_count), log_level)

    if LOGGING_CONFIGURED and LOGGING_CONFIG == config:
        return LOGGER

    os.makedirs(log_dir, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    for handler in list(LOGGER.handlers):
        if getattr(handler, "_anibase_managed", False):
            LOGGER.removeHandler(handler)
            handler.close()

    console_handler = logging.StreamHandler(stream=sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    console_handler._anibase_managed = True

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    file_handler._anibase_managed = True

    LOGGER.setLevel(log_level)
    LOGGER.propagate = False
    LOGGER.addHandler(console_handler)
    LOGGER.addHandler(file_handler)

    LOGGING_CONFIGURED = True
    LOGGING_CONFIG = config
    return LOGGER

def app_log(message, level="INFO"):
    logger = LOGGER if LOGGING_CONFIGURED else configure_logging()
    log_method = {
        "DEBUG": logger.debug,
        "INFO": logger.info,
        "WARN": logger.warning,
        "WARNING": logger.warning,
        "ERROR": logger.error,
        "EXCEPTION": logger.exception,
    }.get(str(level or "INFO").upper(), logger.info)
    log_method(str(message))

def debug_log(message):
    if VERBOSE_LOGS:
        if not LOGGING_CONFIGURED:
            configure_logging()
        LOGGER.debug(str(message))

def get_runtime_directories():
    return [
        USER_DATA_DIR,
        CACHE_DIR,
        LOG_DIR,
        RUNTIME_DIR,
        TEMP_DIR,
        POSTER_CACHE,
        BANNER_CACHE,
        THUMBNAIL_CACHE,
        METADATA_CACHE,
        CHARACTER_CACHE,
        EPISODE_CACHE,
        SUBTITLE_CACHE,
        SEIYUU_CACHE,
    ]

def ensure_runtime_directories():
    for directory in get_runtime_directories():
        os.makedirs(directory, exist_ok=True)

def copy_file_if_missing(source, destination, label):
    if not os.path.isfile(source):
        return None
    if os.path.exists(destination):
        return None

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(source, destination)
    return f"Migration copied {label}."

def copy_tree_missing(source_dir, destination_dir, label):
    if not os.path.isdir(source_dir):
        return None

    copied = 0
    skipped = 0
    for root, _dirs, files in os.walk(source_dir):
        relative_root = os.path.relpath(root, source_dir)
        target_root = (
            destination_dir
            if relative_root == "."
            else os.path.join(destination_dir, relative_root)
        )
        os.makedirs(target_root, exist_ok=True)

        for filename in files:
            source = os.path.join(root, filename)
            destination = os.path.join(target_root, filename)
            if os.path.exists(destination):
                skipped += 1
                continue
            shutil.copy2(source, destination)
            copied += 1

    if copied:
        return f"Migration processed {label}: copied={copied}; skipped_existing={skipped}."

    return None

def migrate_legacy_user_data_dirs():
    if os.path.abspath(USER_DATA_DIR) == os.path.abspath(APP_DIR):
        return []

    messages = []
    for legacy_name in LEGACY_APP_DATA_DIR_NAMES:
        legacy_dir = get_local_app_data_dir(legacy_name)
        if os.path.abspath(legacy_dir) == os.path.abspath(USER_DATA_DIR):
            continue

        message = copy_tree_missing(
            legacy_dir,
            USER_DATA_DIR,
            f"legacy LocalAppData/{legacy_name}"
        )
        if message:
            messages.append(message)

    return messages

def migrate_legacy_runtime_data():
    legacy_cache_dir = os.path.join(APP_DIR, "cache")
    legacy_log_dir = os.path.join(APP_DIR, "logs")
    messages = []

    file_migrations = (
        (os.path.join(legacy_cache_dir, "settings.json"), SETTINGS_FILE, "settings.json"),
        (os.path.join(legacy_cache_dir, "library.db"), DB_PATH, "library.db"),
        (os.path.join(legacy_cache_dir, "library.db-wal"), f"{DB_PATH}-wal", "library.db-wal"),
        (os.path.join(legacy_cache_dir, "library.db-shm"), f"{DB_PATH}-shm", "library.db-shm"),
        (os.path.join(legacy_cache_dir, "watch_history.json"), WATCH_HISTORY_FILE, "watch_history.json"),
        (os.path.join(legacy_cache_dir, "watch_history.json.bak"), f"{WATCH_HISTORY_FILE}.bak", "watch_history.json.bak"),
        (os.path.join(legacy_cache_dir, "watch_status.json"), WATCH_STATUS_FILE, "watch_status.json"),
        (os.path.join(legacy_cache_dir, "watch_status.json.bak"), f"{WATCH_STATUS_FILE}.bak", "watch_status.json.bak"),
    )

    for source, destination, label in file_migrations:
        message = copy_file_if_missing(source, destination, label)
        if message:
            messages.append(message)

    cache_migrations = (
        ("posters", POSTER_CACHE),
        ("banners", BANNER_CACHE),
        ("metadata", METADATA_CACHE),
        ("characters", CHARACTER_CACHE),
        ("thumbnails", THUMBNAIL_CACHE),
        ("subtitles", SUBTITLE_CACHE),
        ("episodes", EPISODE_CACHE),
        ("seiyuu", SEIYUU_CACHE),
    )
    for dirname, destination in cache_migrations:
        message = copy_tree_missing(
            os.path.join(legacy_cache_dir, dirname),
            destination,
            f"cache/{dirname}"
        )
        if message:
            messages.append(message)

    message = copy_tree_missing(legacy_log_dir, LOG_DIR, "logs")
    if message:
        messages.append(message)

    return messages

ensure_runtime_directories()
RUNTIME_MIGRATION_LOG_MESSAGES = (
    migrate_legacy_user_data_dirs()
    + migrate_legacy_runtime_data()
)
configure_logging()
for migration_message in RUNTIME_MIGRATION_LOG_MESSAGES:
    app_log(migration_message)

def get_log_file_path():
    if LOGGING_CONFIG and LOGGING_CONFIG[0]:
        return LOGGING_CONFIG[0]

    return LOG_FILE

def summarize_dependency_status(result):
    status = result.get("status", "unknown")
    if result.get("available"):
        return "available"
    return status

def log_startup_summary(mode, host, port, scanner_enabled, periodic_sync_enabled, auto_import_worker_enabled):
    global STARTUP_SUMMARY_LOGGED

    if STARTUP_SUMMARY_LOGGED:
        return

    STARTUP_SUMMARY_LOGGED = True
    settings = load_settings()
    diagnostics = get_media_dependency_diagnostics(settings)
    app_log(
        "Startup summary: "
        f"mode={mode}; "
        f"bind={host}:{port}; "
        f"lan={'enabled' if settings.get('lan_access_enabled') else 'disabled'}; "
        f"scanner={'enabled' if scanner_enabled else 'disabled'}; "
        f"periodic_sync={'enabled' if periodic_sync_enabled else 'disabled'}; "
        f"auto_import_worker={'enabled' if auto_import_worker_enabled else 'disabled'}; "
        f"ffmpeg={summarize_dependency_status(diagnostics['ffmpeg'])}; "
        f"ffprobe={summarize_dependency_status(diagnostics['ffprobe'])}; "
        f"vlc={summarize_dependency_status(diagnostics['vlc'])}; "
        f"log_file={get_log_file_path()}",
        "INFO"
    )

def get_default_settings():
    return {
        "setup_completed": False,
        "library_paths": [],
        "watchlist_path": "",
        "ongoing_path": "",
        "movie_path": "",
        "vlc_path": "",
        "discord_rpc_enabled": False,
        "discord_client_id": "",
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
        "action_token": ""
    }

def load_settings():
    defaults = get_default_settings()

    with SETTINGS_LOCK:
        if not os.path.exists(SETTINGS_FILE):
            return defaults

        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
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
    if merged.get("theme_preset") not in THEME_PRESETS:
        merged["theme_preset"] = defaults["theme_preset"]
    merged["lan_access_enabled"] = normalize_bool_setting(
        merged.get("lan_access_enabled"),
        defaults["lan_access_enabled"]
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
    if not isinstance(merged.get("action_token"), str):
        merged["action_token"] = ""
    return merged

def save_settings(settings):
    with SETTINGS_LOCK:
        atomic_write_json_file(SETTINGS_FILE, settings, "Settings")

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
    token = request.headers.get("X-AniBase-Action-Token", "")

    if token:
        return token

    if request.form:
        return request.form.get("action_token", "")

    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data.get("action_token", "")

    return ""

def json_error(error, message, status_code):
    return jsonify({
        "ok": False,
        "error": error,
        "message": message
    }), status_code

def get_json_body():
    try:
        data = request.get_json(silent=False)
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

def validate_action_token():
    expected = get_action_token()
    submitted = get_submitted_action_token()

    return (
        isinstance(submitted, str)
        and bool(submitted)
        and secrets.compare_digest(submitted, expected)
    )

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

@app.context_processor
def inject_action_token():
    return {
        "action_token": get_action_token()
    }

def internal_url_for(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except RuntimeError:
        with app.test_request_context():
            return url_for(endpoint, **values)

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

@app.before_request
def reject_new_work_during_shutdown():
    if is_shutdown_requested():
        return json_error(
            "shutting_down",
            "AniBase is shutting down.",
            503
        )

    return None

@app.before_request
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

@app.before_request
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

def normalize_bool_setting(value, default=False):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False

    if value is None:
        return default

    return default

def normalize_int_setting(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, number))

def normalize_library_path(path):
    if not path:
        return ""

    path = str(path).strip()
    if not path:
        return ""

    normalized = os.path.abspath(os.path.expanduser(path))

    if normalized == BASE_DIR:
        return ""

    return normalized

def normalize_library_paths(paths):
    if isinstance(paths, str):
        raw_paths = [paths]
    elif isinstance(paths, (list, tuple)):
        raw_paths = paths
    else:
        raw_paths = []

    normalized_paths = []
    seen_paths = set()
    for path in raw_paths:
        normalized_path = normalize_library_path(path)
        if not normalized_path:
            continue

        path_key = os.path.normcase(normalized_path)
        if path_key in seen_paths:
            continue

        seen_paths.add(path_key)
        normalized_paths.append(normalized_path)

    return normalized_paths

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

def get_valid_anime_paths():
    valid_paths = []

    for base_path in ANIME_PATHS:
        normalized_path = normalize_library_path(base_path)
        if normalized_path and os.path.isdir(normalized_path):
            valid_paths.append(normalized_path)

    return valid_paths

def get_configured_anime_paths():
    configured_paths = []
    seen_paths = set()

    for path in ANIME_PATHS:
        normalized_path = normalize_library_path(path)
        if not normalized_path:
            continue

        path_key = os.path.normcase(normalized_path)
        if path_key in seen_paths:
            continue

        seen_paths.add(path_key)
        configured_paths.append(normalized_path)

    return configured_paths

def count_library_rows():
    try:
        with db_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM anime_library").fetchone()[0]
    except sqlite3.Error as e:
        app_log(f"Unable to count library rows before sync: {e}", "WARN")
        return 0

def collect_library_scan_roots(configured_paths):
    scan_roots = []
    failed_roots = []

    for base_path in configured_paths:
        if not os.path.isdir(base_path):
            failed_roots.append({
                "path": base_path,
                "reason": "folder is not available"
            })
            continue

        try:
            entries = os.listdir(base_path)
        except OSError as e:
            failed_roots.append({
                "path": base_path,
                "reason": str(e)
            })
            continue

        scan_roots.append({
            "path": base_path,
            "entries": entries
        })

    return scan_roots, failed_roots

def is_configured_movie_folder(path):
    movie_path = normalize_library_path(globals().get("MOVIE_PATH", ""))
    candidate = normalize_library_path(path)

    if not movie_path or not candidate:
        return False

    return os.path.normcase(candidate) == os.path.normcase(movie_path)

def is_anime_library_folder(path):
    if not os.path.isdir(path):
        return False

    folder_name = os.path.basename(os.path.normpath(path))
    if folder_name.lower() == "_unmatched downloads":
        return False

    return not is_configured_movie_folder(path)

def is_configured_movie_folder_name(folder_name):
    if not folder_name:
        return False

    for base_path in get_valid_anime_paths():
        if is_configured_movie_folder(os.path.join(base_path, folder_name)):
            return True

    return False

def reset_discord_rpc():
    global rpc, rpc_connected, RPC_START_TIME, CURRENT_RPC_ANIME, CURRENT_RPC_OWNER

    with RPC_LOCK:
        if rpc is not None:
            try:
                if rpc_connected:
                    rpc.clear()
                rpc.close()
            except Exception:
                pass

        rpc = None
        rpc_connected = False
        RPC_START_TIME = None
        CURRENT_RPC_ANIME = None
        CURRENT_RPC_OWNER = None

def get_discord_rpc_client():
    global rpc

    if Presence is None or not DISCORD_CLIENT_ID:
        return None

    if rpc is None:
        rpc = Presence(DISCORD_CLIENT_ID)

    return rpc

def apply_settings(settings):
    global ANIME_PATHS, MOVIE_PATH, VLC_PATH, DISCORD_RPC_ENABLED, DISCORD_CLIENT_ID

    defaults = get_default_settings()
    merged = defaults.copy()
    merged.update(settings or {})

    ANIME_PATHS = get_settings_library_paths(merged)
    MOVIE_PATH = normalize_library_path(merged["movie_path"])
    VLC_PATH = normalize_library_path(merged["vlc_path"])
    discord_client_id = str(merged.get("discord_client_id", "")).strip()
    if discord_client_id != DISCORD_CLIENT_ID:
        reset_discord_rpc()
        DISCORD_CLIENT_ID = discord_client_id

    DISCORD_RPC_ENABLED = normalize_bool_setting(
        merged.get("discord_rpc_enabled"),
        defaults["discord_rpc_enabled"]
    )
    if not DISCORD_RPC_ENABLED:
        reset_discord_rpc()

def get_current_theme():
    theme = load_settings().get("theme_preset", "dark-blue")
    if theme not in THEME_PRESETS:
        return "dark-blue"
    return theme

@app.context_processor
def inject_theme():
    return {
        "current_theme": get_current_theme()
    }

def get_existing_anime_names():
    existing_names = set()

    for base_path in get_valid_anime_paths():
        try:
            for name in os.listdir(base_path):
                full_path = os.path.join(base_path, name)
                if is_anime_library_folder(full_path):
                    existing_names.add(name)
        except OSError:
            continue

    return existing_names

def safe_cache_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name or "")

def is_resolved_path_inside(base_path, candidate_path):
    if not base_path or not candidate_path:
        return False

    base_real = os.path.normcase(os.path.realpath(os.path.abspath(base_path)))
    candidate_real = os.path.normcase(os.path.realpath(os.path.abspath(candidate_path)))

    try:
        return os.path.commonpath([base_real, candidate_real]) == base_real
    except ValueError:
        return False

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

def is_path_inside_cache(path):
    cache_root = get_active_cache_dir()
    candidate = os.path.abspath(path)

    try:
        return os.path.commonpath([cache_root, candidate]) == cache_root
    except ValueError:
        return False

def is_protected_cache_file(path):
    candidate = os.path.abspath(path)
    if candidate in get_protected_cache_files():
        return True

    cache_root = get_active_cache_dir()
    try:
        if os.path.commonpath([cache_root, candidate]) != cache_root:
            return False
    except ValueError:
        return False

    filename = os.path.basename(candidate)
    return (
        filename.startswith("watch_history.json.")
        or filename.startswith("watch_status.json.")
        or filename.startswith(".watch_history.json.tmp-")
        or filename.startswith(".watch_status.json.tmp-")
        or filename.startswith("library.db-")
    )

def make_cache_cleanup_summary():
    return {
        "removed_files": 0,
        "removed_dirs": 0,
        "removed_watch_entries": 0,
        "skipped": 0,
        "details": []
    }

def remove_cache_file_if_safe(summary, path, label):
    if is_protected_cache_file(path):
        summary["skipped"] += 1
        summary["details"].append(f"Skipped protected cache file: {label}")
        debug_log(f"Cache cleanup skipped protected file: {path}")
        return

    if not is_path_inside_cache(path) or not os.path.isfile(path):
        summary["skipped"] += 1
        summary["details"].append(f"Skipped unsafe file: {label}")
        return

    try:
        os.remove(path)
        summary["removed_files"] += 1
        summary["details"].append(f"Removed file: {label}")
    except OSError as e:
        summary["skipped"] += 1
        summary["details"].append(f"Skipped file {label}: {e}")

def remove_cache_dir_if_safe(summary, path, label):
    if not is_path_inside_cache(path) or not os.path.isdir(path):
        summary["skipped"] += 1
        summary["details"].append(f"Skipped unsafe folder: {label}")
        return

    try:
        shutil.rmtree(path)
        summary["removed_dirs"] += 1
        summary["details"].append(f"Removed folder: {label}")
    except OSError as e:
        summary["skipped"] += 1
        summary["details"].append(f"Skipped folder {label}: {e}")

def load_json_dict_if_exists(path):
    data, error = read_json_dict_file(path, os.path.basename(path))
    if error is not None:
        return {}
    return data

def add_thumbnail_candidate_paths(candidates, anime_name, episode):
    if not anime_name or not episode:
        return

    normalized_episode = str(episode).replace("/", os.sep).replace("\\", os.sep)

    for base_path in get_valid_anime_paths():
        candidates.add(os.path.abspath(os.path.join(base_path, anime_name, normalized_episode)))

def collect_cached_episode_candidates(anime_name, safe_name):
    candidates = set()

    history = load_history_data()
    for history_key, data in history.items():
        if not isinstance(data, dict):
            continue

        media_name = data.get("media_name") or history_key
        if media_name == anime_name or safe_cache_name(media_name) == safe_name:
            add_thumbnail_candidate_paths(candidates, anime_name, data.get("episode"))

    status_data = load_watch_status()
    for status_key, episodes in status_data.items():
        if status_key != anime_name and safe_cache_name(status_key) != safe_name:
            continue
        if not isinstance(episodes, dict):
            continue
        for episode in episodes.keys():
            add_thumbnail_candidate_paths(candidates, anime_name, episode)

    if os.path.isdir(EPISODE_CACHE):
        for filename in os.listdir(EPISODE_CACHE):
            if not filename.lower().endswith(".json"):
                continue

            stem = os.path.splitext(filename)[0]
            if stem != safe_name and not stem.startswith(f"{safe_name}_"):
                continue

            season_name = None
            if stem.startswith(f"{safe_name}_"):
                season_name = stem[len(safe_name) + 1:].replace("_", os.sep)

            cache_data = load_json_dict_if_exists(os.path.join(EPISODE_CACHE, filename))
            for episode_file in cache_data.keys():
                add_thumbnail_candidate_paths(candidates, anime_name, episode_file)
                if season_name:
                    add_thumbnail_candidate_paths(
                        candidates,
                        anime_name,
                        os.path.join(season_name, episode_file)
                    )

    return candidates

def cleanup_watch_data_for_anime(anime_name, safe_name, summary):
    history = load_history_data()
    cleaned_history = {}
    removed_history = 0

    for history_key, data in history.items():
        media_name = data.get("media_name") if isinstance(data, dict) else None
        matches = (
            history_key == anime_name
            or safe_cache_name(history_key) == safe_name
            or media_name == anime_name
            or safe_cache_name(media_name) == safe_name
        )

        if matches:
            removed_history += 1
            continue

        cleaned_history[history_key] = data

    if removed_history:
        save_watch_history(cleaned_history)
        summary["removed_watch_entries"] += removed_history
        summary["details"].append(
            f"Removed {removed_history} watch history entries for {anime_name}"
        )

    status_data = load_watch_status()
    removed_status = 0
    for status_key in list(status_data.keys()):
        if status_key == anime_name or safe_cache_name(status_key) == safe_name:
            status_data.pop(status_key, None)
            removed_status += 1

    if removed_status:
        save_watch_status(status_data)
        summary["removed_watch_entries"] += removed_status
        summary["details"].append(
            f"Removed {removed_status} watch status entries for {anime_name}"
        )

def cleanup_thumbnails_for_video_paths(video_paths, summary):
    for video_path in video_paths:
        filename = hashlib.md5(video_path.encode("utf-8")).hexdigest() + ".jpg"
        thumbnail_path = os.path.join(THUMBNAIL_CACHE, filename)
        if os.path.isfile(thumbnail_path):
            remove_cache_file_if_safe(summary, thumbnail_path, f"thumbnails/{filename}")

def cleanup_anime_cache(anime_name, summary=None, include_watch_data=False):
    summary = summary or make_cache_cleanup_summary()
    safe_name = safe_cache_name(anime_name)

    if not safe_name:
        summary["skipped"] += 1
        summary["details"].append("Skipped anime cache cleanup because anime name is empty.")
        return summary

    thumbnail_candidates = collect_cached_episode_candidates(anime_name, safe_name)

    for cache_dir, label, extension in (
        (POSTER_CACHE, "posters", ".jpg"),
        (BANNER_CACHE, "banners", ".jpg"),
        (METADATA_CACHE, "metadata", ".json"),
    ):
        remove_cache_file_if_safe(
            summary,
            os.path.join(cache_dir, f"{safe_name}{extension}"),
            f"{label}/{safe_name}{extension}"
        )

    for cache_dir, label in (
        (CHARACTER_CACHE, "characters"),
        (SUBTITLE_CACHE, "subtitles"),
    ):
        remove_cache_dir_if_safe(
            summary,
            os.path.join(cache_dir, safe_name),
            f"{label}/{safe_name}"
        )

    if os.path.isdir(EPISODE_CACHE):
        for filename in os.listdir(EPISODE_CACHE):
            if not filename.lower().endswith(".json"):
                continue

            stem = os.path.splitext(filename)[0]
            if stem == safe_name or stem.startswith(f"{safe_name}_"):
                remove_cache_file_if_safe(
                    summary,
                    os.path.join(EPISODE_CACHE, filename),
                    f"episodes/{filename}"
                )

    cleanup_thumbnails_for_video_paths(thumbnail_candidates, summary)
    if include_watch_data:
        cleanup_watch_data_for_anime(anime_name, safe_name, summary)

    return summary

def cleanup_orphan_cache():
    summary = make_cache_cleanup_summary()

    existing_anime_names = get_existing_anime_names()
    existing_safe_names = {
        safe_cache_name(name)
        for name in existing_anime_names
    }
    available_base_paths = [
        path
        for path in get_valid_anime_paths()
    ]

    if not available_base_paths:
        summary["skipped"] += 1
        summary["details"].append(
            "Skipped cleanup because no configured anime folders are available."
        )
        return summary

    def clean_named_files(cache_dir, label):
        if not os.path.isdir(cache_dir):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped missing cache folder: {label}")
            return

        for filename in os.listdir(cache_dir):
            path = os.path.join(cache_dir, filename)
            if not os.path.isfile(path):
                summary["skipped"] += 1
                continue

            stem, _ = os.path.splitext(filename)
            if stem not in existing_safe_names:
                remove_cache_file_if_safe(summary, path, f"{label}/{filename}")

    def clean_named_dirs(cache_dir, label):
        if not os.path.isdir(cache_dir):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped missing cache folder: {label}")
            return

        for folder_name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, folder_name)
            if not os.path.isdir(path):
                summary["skipped"] += 1
                continue

            if folder_name not in existing_safe_names:
                remove_cache_dir_if_safe(summary, path, f"{label}/{folder_name}")

    def clean_episode_files():
        if not os.path.isdir(EPISODE_CACHE):
            summary["skipped"] += 1
            summary["details"].append("Skipped missing cache folder: episodes")
            return

        for filename in os.listdir(EPISODE_CACHE):
            path = os.path.join(EPISODE_CACHE, filename)
            if not os.path.isfile(path) or not filename.lower().endswith(".json"):
                summary["skipped"] += 1
                continue

            stem = os.path.splitext(filename)[0]
            matches_existing = any(
                stem == safe_name or stem.startswith(f"{safe_name}_")
                for safe_name in existing_safe_names
            )

            if not matches_existing:
                remove_cache_file_if_safe(summary, path, f"episodes/{filename}")

    def clean_orphan_thumbnails():
        candidate_paths = set()
        history = load_history_data()
        status_data = load_watch_status()

        for history_key, data in history.items():
            if not isinstance(data, dict):
                continue

            media_name = data.get("media_name") or history_key
            if safe_cache_name(media_name) in existing_safe_names:
                continue

            add_thumbnail_candidate_paths(candidate_paths, media_name, data.get("episode"))

        for status_key, episodes in status_data.items():
            if safe_cache_name(status_key) in existing_safe_names or not isinstance(episodes, dict):
                continue

            for episode in episodes.keys():
                add_thumbnail_candidate_paths(candidate_paths, status_key, episode)

        if os.path.isdir(EPISODE_CACHE):
            for filename in os.listdir(EPISODE_CACHE):
                if not filename.lower().endswith(".json"):
                    continue

                stem = os.path.splitext(filename)[0]
                if any(stem == safe_name or stem.startswith(f"{safe_name}_") for safe_name in existing_safe_names):
                    continue

                anime_safe_name = stem.split("_", 1)[0]
                anime_name = next(
                    (name for name in existing_anime_names if safe_cache_name(name) == anime_safe_name),
                    anime_safe_name
                )
                cache_data = load_json_dict_if_exists(os.path.join(EPISODE_CACHE, filename))
                for episode_file in cache_data.keys():
                    add_thumbnail_candidate_paths(candidate_paths, anime_name, episode_file)

        cleanup_thumbnails_for_video_paths(candidate_paths, summary)

    clean_named_files(POSTER_CACHE, "posters")
    clean_named_files(BANNER_CACHE, "banners")
    clean_named_files(METADATA_CACHE, "metadata")
    clean_named_dirs(CHARACTER_CACHE, "characters")
    clean_named_dirs(SUBTITLE_CACHE, "subtitles")
    clean_orphan_thumbnails()
    clean_episode_files()
    summary["skipped"] += 1
    summary["details"].append(
        "Skipped protected settings/database files, watch data, and watch data backups."
    )

    return summary

def init_db():
    os.makedirs(
        POSTER_CACHE,
        exist_ok=True
    )
    os.makedirs(
        THUMBNAIL_CACHE,
        exist_ok=True
    )
    os.makedirs(
        METADATA_CACHE,
        exist_ok=True
    )
    os.makedirs(
        EPISODE_CACHE,
        exist_ok=True
    )
    os.makedirs(
        BANNER_CACHE,
        exist_ok=True
    )
    os.makedirs(
        SUBTITLE_CACHE,
        exist_ok=True
    )
    os.makedirs(
        CHARACTER_CACHE,
        exist_ok=True
    )
    os.makedirs(
        SEIYUU_CACHE,
        exist_ok=True
    )

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anime_library (
                name TEXT PRIMARY KEY,
                episodes INTEGER,
                score REAL,
                genres TEXT,
                year INTEGER,
                season TEXT,
                status TEXT
            )
        """)

        # Sinkronisasi skema database: Tambahkan kolom jika menggunakan database versi lama
        cursor = conn.execute("PRAGMA table_info(anime_library)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'genres' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN genres TEXT")
        if 'year' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN year INTEGER")
        if 'season' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN season TEXT")
        if 'status' not in columns:
            conn.execute("ALTER TABLE anime_library ADD COLUMN status TEXT")

init_db()
apply_settings(load_settings())

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".avi",
    ".webm",
    ".mov",
    ".wmv"
)

DOWNLOAD_TEMP_EXTENSIONS = (
    ".crdownload",
    ".part",
    ".tmp",
    ".download",
    ".opdownload",
    ".partial"
)
MEDIA_PROBE_TIMEOUT_SECONDS = 15
THUMBNAIL_GENERATION_TIMEOUT_SECONDS = 30
SUBTITLE_GENERATION_TIMEOUT_SECONDS = 45
MAX_SCREENSHOT_DATA_URL_BYTES = 25 * 1024 * 1024
FFMPEG_MAX_CONCURRENT_PROCESSES = 2
FFMPEG_MEDIA_LOCK_TIMEOUT_SECONDS = 10
FFMPEG_SEMAPHORE_TIMEOUT_SECONDS = 1
FFMPEG_FAILURE_CACHE_TTL_SECONDS = 60
FFMPEG_SEMAPHORE = threading.BoundedSemaphore(FFMPEG_MAX_CONCURRENT_PROCESSES)
FFMPEG_MEDIA_LOCKS = {}
FFMPEG_MEDIA_LOCKS_GUARD = threading.RLock()
FFMPEG_FAILURE_CACHE = {}
FFMPEG_FAILURE_CACHE_LOCK = threading.RLock()
MEDIA_DIAGNOSTIC_TIMEOUT_SECONDS = 2
MEDIA_DIAGNOSTIC_CACHE_TTL_SECONDS = 15
MEDIA_DIAGNOSTIC_CACHE_LOCK = threading.RLock()
MEDIA_DIAGNOSTIC_CACHE = {}

def safe_join_media_path(base_path, relative_path):

    if not base_path:
        return None

    base_path = os.path.abspath(base_path)

    candidate_path = os.path.abspath(
        os.path.normpath(
            os.path.join(
                base_path,
                relative_path
            )
        )
    )

    try:

        if os.path.commonpath(
            [
                base_path,
                candidate_path
            ]
        ) != base_path:

            return None

    except ValueError:

        return None

    return candidate_path

def is_valid_cache_file(path):
    try:
        return bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
    except OSError:
        return False

def media_source_fingerprint(path):
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None

def make_media_generation_result(ok=False, path="", status="failed", message=""):
    return {
        "ok": bool(ok),
        "path": path or "",
        "status": status,
        "message": message
    }

def acquire_ffmpeg_media_lock(key, timeout=FFMPEG_MEDIA_LOCK_TIMEOUT_SECONDS):
    with FFMPEG_MEDIA_LOCKS_GUARD:
        entry = FFMPEG_MEDIA_LOCKS.get(key)
        if entry is None:
            entry = {
                "lock": threading.Lock(),
                "users": 0
            }
            FFMPEG_MEDIA_LOCKS[key] = entry
        entry["users"] += 1
        lock = entry["lock"]

    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        release_ffmpeg_media_lock(key, lock, acquired=False)
        return None

    return lock

def release_ffmpeg_media_lock(key, lock, acquired=True):
    if acquired and lock:
        lock.release()

    with FFMPEG_MEDIA_LOCKS_GUARD:
        entry = FFMPEG_MEDIA_LOCKS.get(key)
        if not entry:
            return

        entry["users"] = max(0, entry.get("users", 1) - 1)
        if entry["users"] == 0 and not entry["lock"].locked():
            FFMPEG_MEDIA_LOCKS.pop(key, None)

def get_ffmpeg_failure_cache(key):
    now = time.monotonic()
    with FFMPEG_FAILURE_CACHE_LOCK:
        expired = [
            cache_key
            for cache_key, value in FFMPEG_FAILURE_CACHE.items()
            if value.get("expires_at", 0) <= now
        ]
        for cache_key in expired:
            FFMPEG_FAILURE_CACHE.pop(cache_key, None)

        value = FFMPEG_FAILURE_CACHE.get(key)
        if value and value.get("expires_at", 0) > now:
            return value.get("result")

    return None

def set_ffmpeg_failure_cache(key, result):
    with FFMPEG_FAILURE_CACHE_LOCK:
        FFMPEG_FAILURE_CACHE[key] = {
            "expires_at": time.monotonic() + FFMPEG_FAILURE_CACHE_TTL_SECONDS,
            "result": result
        }

def clear_ffmpeg_failure_cache(key):
    with FFMPEG_FAILURE_CACHE_LOCK:
        FFMPEG_FAILURE_CACHE.pop(key, None)

def remove_file_quietly(path):
    if not path:
        return

    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def temporary_media_cache_path(final_path, suffix):
    directory = os.path.dirname(final_path)
    base = os.path.basename(final_path)
    return os.path.join(
        directory,
        f".{base}.{secrets.token_hex(8)}{suffix}"
    )

def run_ffmpeg_command(args, timeout):
    if is_shutdown_requested():
        return make_media_generation_result(
            False,
            "",
            "shutting_down",
            "Media generation skipped because the server is shutting down."
        )

    acquired = FFMPEG_SEMAPHORE.acquire(timeout=FFMPEG_SEMAPHORE_TIMEOUT_SECONDS)
    if not acquired:
        return make_media_generation_result(
            False,
            "",
            "busy",
            "Media generation is busy."
        )

    try:
        command = list(args)
        if command:
            command[0] = get_media_tool_path("ffmpeg")
        result = run_hidden_subprocess(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
    except FileNotFoundError:
        return make_media_generation_result(
            False,
            "",
            "ffmpeg_unavailable",
            "FFmpeg is not available."
        )
    except PermissionError:
        return make_media_generation_result(
            False,
            "",
            "ffmpeg_unavailable",
            "FFmpeg cannot be executed."
        )
    except subprocess.TimeoutExpired:
        return make_media_generation_result(
            False,
            "",
            "timeout",
            "FFmpeg operation timed out."
        )
    except Exception as e:
        app_log(f"FFmpeg command failed: {e}", "ERROR")
        return make_media_generation_result(
            False,
            "",
            "error",
            "FFmpeg operation failed."
        )
    finally:
        FFMPEG_SEMAPHORE.release()

    if result.returncode != 0:
        return make_media_generation_result(
            False,
            "",
            "failed",
            result.stderr.strip() or f"FFmpeg return code: {result.returncode}"
        )

    return make_media_generation_result(True, "", "generated", "")

def media_generation_error_response(result):
    if result.get("status") in {"busy", "ffmpeg_unavailable", "timeout", "error"}:
        return "", 503

    return "", 404

def parse_executable_version(output):
    for line in (output or "").splitlines():
        line = line.strip()
        if line:
            return line[:160]

    return ""

def make_dependency_result(available, path, version, status, message):
    return {
        "available": bool(available),
        "path": path or "",
        "version": version or "",
        "status": status,
        "message": message
    }

def diagnose_path_executable(label, executable, run_version=True):
    candidate = get_media_tool_path(executable)
    path = candidate if os.path.isfile(candidate) else shutil.which(candidate)
    if not path:
        return make_dependency_result(
            False,
            "",
            "",
            "not_found",
            f"{label} was not found on PATH."
        )

    if not run_version:
        return make_dependency_result(
            True,
            path,
            "",
            "available",
            f"{label} executable was found."
        )

    try:
        result = run_hidden_subprocess(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=MEDIA_DIAGNOSTIC_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        return make_dependency_result(False, "", "", "not_found", f"{label} was not found.")
    except PermissionError:
        return make_dependency_result(False, path, "", "error", f"{label} cannot be executed due to permissions.")
    except subprocess.TimeoutExpired:
        return make_dependency_result(False, path, "", "error", f"{label} version check timed out.")
    except OSError:
        return make_dependency_result(False, path, "", "error", f"{label} could not be executed.")

    version = parse_executable_version((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode != 0:
        return make_dependency_result(
            False,
            path,
            version,
            "error",
            f"{label} returned exit code {result.returncode} during diagnostics."
        )

    return make_dependency_result(
        True,
        path,
        version,
        "available",
        f"{label} is available."
    )

def diagnose_vlc_path(vlc_path):
    path = normalize_library_path(vlc_path)
    if not path:
        return make_dependency_result(
            False,
            "",
            "",
            "not_configured",
            "Media Player path is not configured."
        )

    if os.path.isdir(path):
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player path points to a folder, not an executable file."
        )

    if not os.path.exists(path):
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player executable was not found."
        )

    if not os.path.isfile(path):
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player path is not a regular file."
        )

    if os.name == "nt" and os.path.splitext(path)[1].lower() not in {".exe", ".bat", ".cmd"}:
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player path should point to a Windows executable."
        )

    return make_dependency_result(
        True,
        path,
        "",
        "available",
        "Media Player executable path is valid."
    )

def get_media_dependency_diagnostics(settings=None, force=False):
    settings = settings if isinstance(settings, dict) else load_settings()
    vlc_path = normalize_library_path(settings.get("vlc_path", ""))
    cache_key = (
        "media-dependencies",
        vlc_path
    )
    now = time.monotonic()

    with MEDIA_DIAGNOSTIC_CACHE_LOCK:
        cached = MEDIA_DIAGNOSTIC_CACHE.get(cache_key)
        if (
            not force
            and cached
            and cached.get("expires_at", 0) > now
        ):
            return cached["value"]

    diagnostics = {
        "ffmpeg": diagnose_path_executable("FFmpeg", "ffmpeg"),
        "ffprobe": diagnose_path_executable("FFprobe", "ffprobe"),
        "vlc": diagnose_vlc_path(vlc_path)
    }

    with MEDIA_DIAGNOSTIC_CACHE_LOCK:
        MEDIA_DIAGNOSTIC_CACHE.clear()
        MEDIA_DIAGNOSTIC_CACHE[cache_key] = {
            "expires_at": now + MEDIA_DIAGNOSTIC_CACHE_TTL_SECONDS,
            "value": diagnostics
        }

    return diagnostics

def clean_movie_title(filename):

    title = os.path.splitext(
        filename
    )[0]

    title = re.sub(
        r"\[.*?\]",
        "",
        title
    )

    title = re.sub(
        r"\b1080p\b",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\bBD\b",
        "",
        title,
        flags=re.I
    )

    return " ".join(
        title.split()
    )

def get_anilist_poster(anime_name):

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    banner_path = os.path.join(
        BANNER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return poster_path

    # Gunakan info dari metadata cache jika tersedia untuk menghindari API call ganda
    info = get_cached_anilist_info(anime_name)
    if not info:
        return None

    try:
        poster_url = info.get("poster")
        banner_url = info.get("banner")

        if not poster_url:
            return None

        # Download Poster
        poster_image = requests.get(
            poster_url,
            timeout=15
        )

        with open(
            poster_path,
            "wb"
        ) as f:

            f.write(
                poster_image.content
            )

        if banner_url:

            banner_image = requests.get(
                banner_url,
                timeout=15
            )

            with open(
                banner_path,
                "wb"
            ) as f:

                f.write(
                    banner_image.content
                )

        return poster_path

    except Exception as e:

        app_log(f"AniList poster error for {anime_name}: {e}", "WARN")

        return None

def get_anilist_info(anime_name):

    query = """
    query ($search: String) {
      Media(
        search: $search,
        type: ANIME
      ) {
        title {
          romaji
          english
        }
        description
        episodes
        duration
        format
        season
        seasonYear
        status
        genres
        averageScore
        nextAiringEpisode {
          airingAt
          episode
          timeUntilAiring
        }
        bannerImage
        coverImage {
          extraLarge
        }
        studios(
          isMain: true
        ) {
          nodes {
            name
          }
        }
        characters(sort: [ROLE, FAVOURITES_DESC], perPage: 6) {
          edges {
            role
            node {
              name { full }
              image { large }
            }
            voiceActors(language: JAPANESE) {
              id
              name { full }
              image { large }
            }
          }
        }
        relations {
          edges {
            relationType
            node {
              title {
                romaji
                english
              }
              coverImage {
                extraLarge
              }
              type
              status
            }
          }
        }
        recommendations(sort: [RATING_DESC, ID_DESC], perPage: 10) {
          nodes {
            mediaRecommendation {
              title {
                romaji
                english
              }
              coverImage {
                extraLarge
              }
              type
              status
            }
          }
        }
      }
    }
    """

    try:

        response = requests.post(
            "https://graphql.anilist.co",
            json={
                "query": query,
                "variables": {
                    "search": anime_name
                }
            },
            timeout=20
        )

        data = response.json()

        media = data["data"]["Media"]

        if not media:
            return None

        studio = None

        # Akses nama studio secara aman
        studios = media.get("studios", {})
        nodes = studios.get("nodes", [])
        if nodes and nodes[0]:
            studio = nodes[0].get("name")

        chars_list = []
        for edge in media.get("characters", {}).get("edges", []):
            char_node = edge.get("node")
            if not char_node: continue
            va = edge.get("voiceActors", [])
            va_node = va[0] if va else None
            
            chars_list.append({
                "name": char_node["name"]["full"],
                "image_url": char_node["image"]["large"],
                "role": edge.get("role"),
                "va_name": va_node["name"]["full"] if va_node else None,
                "va_image_url": va_node["image"]["large"] if va_node else None,
                "va_staff_id": va_node.get("id") if va_node else None
            })

        relations_list = []
        for edge in media.get("relations", {}).get("edges", []):
            rel_node = edge.get("node")
            if not rel_node or rel_node.get("type") != "ANIME":
                continue
            relations_list.append({
                "title": rel_node["title"]["english"] or rel_node["title"]["romaji"],
                "poster": rel_node["coverImage"]["extraLarge"],
                "type": edge.get("relationType").replace("_", " ").title(),
                "status": rel_node.get("status")
            })

        recommendations_list = []
        for node in media.get("recommendations", {}).get("nodes", []):
            rec_media = node.get("mediaRecommendation")
            if not rec_media or rec_media.get("type") != "ANIME":
                continue
            recommendations_list.append({
                "title": rec_media["title"]["english"] or rec_media["title"]["romaji"],
                "poster": rec_media["coverImage"]["extraLarge"],
                "type": rec_media.get("type").replace("_", " ").title(),
                "status": rec_media.get("status")
            })

        return {
            "title": media["title"]["english"] or media["title"]["romaji"],
            "description": media["description"],
            "episodes": media["episodes"],
            "duration": media["duration"],
            "format": media["format"],
            "season": media["season"],
            "year": media["seasonYear"],
            "status": media["status"],
            "studio": studio,
            "genres": media["genres"],
            "score": media["averageScore"],
            "next_airing": media.get("nextAiringEpisode"),
            "poster": media["coverImage"]["extraLarge"],
            "banner": media["bannerImage"],
            "characters": chars_list,
            "relations": relations_list,
            "recommendations": recommendations_list
        }

    except Exception as e:

        app_log(f"AniList metadata error for {anime_name}: {e}", "WARN")

        return None
    
def get_video_duration_seconds(video_path):
    try:
        result = run_hidden_subprocess(
            [
                get_media_tool_path("ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=MEDIA_PROBE_TIMEOUT_SECONDS
        )

        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]))
    except subprocess.TimeoutExpired:
        app_log(f"Duration detection timed out for {video_path}", "WARN")
        return 0
    except Exception:
        return 0

def format_timestamp(seconds):
    seconds = max(0, int(seconds or 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def get_thumbnail_seek_points(video_path):
    duration_seconds = get_video_duration_seconds(video_path)

    if duration_seconds <= 0:
        return [60, 30, 10]

    candidates = [
        int(duration_seconds * 0.20),
        int(duration_seconds * 0.35),
        min(300, int(duration_seconds * 0.12)),
        60,
        30,
        10
    ]

    seek_points = []
    for point in candidates:
        if 1 <= point < duration_seconds - 1 and point not in seek_points:
            seek_points.append(point)

    return seek_points or [10]

def get_thumbnail_cache_path(video_path):
    filename = hashlib.md5(
        video_path.encode("utf-8")
    ).hexdigest() + "_v2"

    return os.path.join(
        THUMBNAIL_CACHE,
        filename + ".jpg"
    )

def get_thumbnail_result(video_path):
    thumbnail_path = get_thumbnail_cache_path(video_path)

    if is_valid_cache_file(thumbnail_path):
        return make_media_generation_result(
            True,
            thumbnail_path,
            "cached",
            "Thumbnail cache found."
        )

    fingerprint = media_source_fingerprint(video_path)
    if fingerprint is None:
        return make_media_generation_result(
            False,
            "",
            "source_not_found",
            "Media source was not found."
        )

    failure_key = ("thumbnail", thumbnail_path, fingerprint)
    cached_failure = get_ffmpeg_failure_cache(failure_key)
    if cached_failure:
        return cached_failure

    lock = acquire_ffmpeg_media_lock(("thumbnail", thumbnail_path))
    if lock is None:
        result = make_media_generation_result(
            False,
            "",
            "busy",
            "Thumbnail generation is already running."
        )
        return result

    last_error = None
    last_status = "failed"
    temp_path = ""

    try:
        if is_valid_cache_file(thumbnail_path):
            return make_media_generation_result(
                True,
                thumbnail_path,
                "cached",
                "Thumbnail cache found."
            )

        cached_failure = get_ffmpeg_failure_cache(failure_key)
        if cached_failure:
            return cached_failure

        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        seek_points = get_thumbnail_seek_points(video_path)
        app_log(f"Starting thumbnail generation for {os.path.basename(video_path)}", "INFO")

        for seek_point in seek_points:
            temp_path = temporary_media_cache_path(thumbnail_path, ".jpg")
            result = run_ffmpeg_command(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    format_timestamp(seek_point),
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    temp_path
                ],
                timeout=THUMBNAIL_GENERATION_TIMEOUT_SECONDS
            )

            if result["ok"] and is_valid_cache_file(temp_path):
                os.replace(temp_path, thumbnail_path)
                clear_ffmpeg_failure_cache(failure_key)
                return make_media_generation_result(
                    True,
                    thumbnail_path,
                    "generated",
                    "Thumbnail generated."
                )

            last_error = result["message"]
            last_status = result["status"]
            remove_file_quietly(temp_path)

            if result["status"] in {"busy", "ffmpeg_unavailable", "timeout", "error"}:
                break

        if last_error:
            app_log(f"Thumbnail generation failed for {os.path.basename(video_path)}: {last_error}", "WARN")
        else:
            app_log(f"Thumbnail generation failed for {os.path.basename(video_path)}", "WARN")

    except Exception as e:
        app_log(f"Thumbnail generation error: {e}", "ERROR")
        last_error = "Thumbnail generation failed."
        last_status = "error"
    finally:
        remove_file_quietly(temp_path)
        release_ffmpeg_media_lock(("thumbnail", thumbnail_path), lock)

    result = make_media_generation_result(
        False,
        "",
        last_status,
        last_error or "Thumbnail was not generated."
    )
    if result["status"] != "busy":
        set_ffmpeg_failure_cache(failure_key, result)
    return result

def get_thumbnail(video_path):
    result = get_thumbnail_result(video_path)
    if result.get("ok"):
        return result.get("path")

    return None

def get_video_resolution(video_path):

    try:

        result = run_hidden_subprocess(
            [
                get_media_tool_path("ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=MEDIA_PROBE_TIMEOUT_SECONDS
        )

        debug_log(f"ffprobe streams for {video_path}: {result.stdout}")

        data = json.loads(result.stdout)

        for stream in data["streams"]:

            if stream["codec_type"] == "video":

                debug_log(f"Video height for {video_path}: {stream['height']}")

                return f'{stream["height"]}p'

    except subprocess.TimeoutExpired:

        app_log(f"Resolution detection timed out for {video_path}", "WARN")

    except Exception as e:

        app_log(f"Resolution detection failed for {video_path}: {e}", "WARN")

    return ""

def load_episode_cache_file(cache_file):
    if not os.path.exists(cache_file):
        return {}

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}

def save_episode_cache_file(cache_file, cache_data):
    atomic_write_json_file(
        cache_file,
        cache_data,
        "Episode cache",
        ensure_ascii=False
    )

def get_episode_cache(
    anime_name,
    video_path,
    season_name=None
):

    cache_name = anime_name

    if season_name:

        cache_name += (
            "_" +
            season_name
        )

    cache_name = (
        cache_name
        .replace("\\", "_")
        .replace("/", "_")
        .replace(":", "")
    )

    cache_file = os.path.join(
        EPISODE_CACHE,
        f"{cache_name}.json"
    )

    filename = os.path.basename(
        video_path
    )

    with EPISODE_CACHE_LOCK:
        cache_data = load_episode_cache_file(cache_file)

    if filename in cache_data:

        return cache_data[
            filename
        ]

    duration = get_video_duration(
        video_path
    )

    resolution = get_video_resolution(
        video_path
    )

    episode_cache_entry = {

        "duration":
            duration,

        "resolution":
            resolution

    }

    with EPISODE_CACHE_LOCK:
        cache_data = load_episode_cache_file(cache_file)

        if filename in cache_data:
            return cache_data[
                filename
            ]

        cache_data[
            filename
        ] = episode_cache_entry

        save_episode_cache_file(cache_file, cache_data)

    return cache_data[
        filename
    ]

def normalize_episode_number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    if number <= 0:
        return None

    if number.is_integer():
        return int(number)

    return number

def format_episode_number(value):
    number = normalize_episode_number(value)
    if number is None:
        return ""

    if isinstance(number, int):
        return str(number)

    return f"{number:g}"

def get_episode_number(filename):
    name = os.path.splitext(
        os.path.basename(filename)
    )[0]

    normalized_name = name.replace("_", " ")
    normalized_name = re.sub(r"\s+", " ", normalized_name).strip()

    patterns = [
        r"(?i)\bS\d{1,2}\s*E\s*(\d+(?:\.\d+)?)\b",
        r"(?i)\b(?:EP|EPS|Episode)\s*[-_. #]*(\d+(?:\.\d+)?)\b",
        r"(?i)(?:^|[-\s._])(\d+(?:\.\d+)?)(?=[-_.\s]+(?:480|576|720|1080|1440|2160)p(?:[-_.\s]|$))",
        r"(?i)(?:^|[\s.\[\(])-\s*(\d+(?:\.\d+)?)(?=\s*(?:$|[\]\)\[\(]|[A-Za-z]))",
        r"(?i)(?:^|[\s._-])(\d+(?:\.\d+)?)(?=\s*(?:$|[\]\)\[\(]))",
    ]

    for pattern_index, pattern in enumerate(patterns):
        matches = list(re.finditer(pattern, normalized_name))
        for match in reversed(matches):
            number = normalize_episode_number(match.group(1))
            if (
                pattern_index >= 2
                and
                isinstance(number, int)
                and
                1900 <= number <= 2099
            ):
                continue
            if number is not None:
                return number

    app_log(f"Could not parse episode number from filename: {filename}", "WARN")
    return 0

def get_episode_display_label(filename):
    episode_number = get_episode_number(filename)
    episode_label = format_episode_number(episode_number)
    if episode_label:
        return episode_label

    return os.path.splitext(
        os.path.basename(filename)
    )[0]

def get_episode_sort_key(filename):
    episode_number = get_episode_number(filename)
    if episode_number:
        return (0, float(episode_number), os.path.basename(filename).lower())

    return (1, os.path.basename(filename).lower())

def get_video_duration(video_path):

    try:

        result = run_hidden_subprocess(
            [
                get_media_tool_path("ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=MEDIA_PROBE_TIMEOUT_SECONDS
        )

        data = json.loads(
            result.stdout
        )

        seconds = int(
            float(
                data["format"]["duration"]
            )
        )

        minutes = seconds // 60

        return f"{minutes} min"

    except subprocess.TimeoutExpired:
        app_log(f"Duration detection timed out for {video_path}", "WARN")
        return ""
    except Exception:
        return ""

def find_anime_path(anime_name):
    for base_path in get_valid_anime_paths():
        anime_path = os.path.join(base_path, anime_name)

        if is_anime_library_folder(anime_path):
            return anime_path

    return None

def find_media_path(library_name):

    if library_name == "Movies":
        if MOVIE_PATH and os.path.isdir(MOVIE_PATH):
            return MOVIE_PATH

        return None

    return find_anime_path(library_name)

def get_season_anilist_info(anime_name, season_name):

    search_name = season_name

    if anime_name.lower() not in season_name.lower():

        search_name = f"{anime_name} {season_name}"

    info = get_cached_anilist_info(
        search_name
    )

    if info:
        info["character_cache_name"] = search_name
        return info

    info = get_cached_anilist_info(
        anime_name
    )
    if info:
        info["character_cache_name"] = anime_name
    return info

def get_airing_schedule():
    local_tz = datetime.now().astimezone().tzinfo
    now = datetime.now(local_tz)
    start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS)
    start_of_day = int(start_dt.timestamp())
    end_of_day = int(end_dt.timestamp())

    query = """
    query ($start: Int, $end: Int, $page: Int) {
      Page(page: $page, perPage: 50) {
        pageInfo {
          total
          currentPage
          lastPage
          hasNextPage
          perPage
        }
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
          airingAt
          episode
          media {
            title {
              romaji
              english
            }
            coverImage {
              extraLarge
            }
            format
          }
        }
      }
    }
    """

    schedules = []
    page = 1

    try:
        while True:
            response = requests.post(
                "https://graphql.anilist.co",
                json={
                    "query": query,
                    "variables": {
                        "start": start_of_day,
                        "end": end_of_day,
                        "page": page
                    }
                },
                timeout=10
            )

            debug_log(
                "Schedule API status "
                f"{response.status_code}, page {page}, "
                f"window {start_dt.isoformat()} to {end_dt.isoformat()}, "
                f"rate_remaining {response.headers.get('X-RateLimit-Remaining')}"
            )

            try:
                data = response.json()
            except ValueError as e:
                app_log(f"Schedule API JSON error: {e}", "ERROR")
                return [], "AniList returned an invalid JSON response."

            if response.status_code >= 400:
                app_log(f"Schedule API HTTP {response.status_code}: {data}", "ERROR")
                return [], f"AniList schedule request failed with HTTP {response.status_code}."

            errors = data.get("errors")
            if errors:
                app_log(f"Schedule API GraphQL errors: {errors}", "ERROR")
                return [], "AniList returned GraphQL errors for the schedule request."

            page_data = data.get("data", {}).get("Page")
            if not page_data:
                app_log(f"Schedule API missing page data: {data}", "ERROR")
                return [], "AniList schedule response did not include page data."

            page_info = page_data.get("pageInfo") or {}
            page_schedules = page_data.get("airingSchedules") or []
            schedules.extend(page_schedules)

            debug_log(
                f"Schedule API page summary: {page_info}, items: {len(page_schedules)}"
            )

            if not page_info.get("hasNextPage"):
                break

            page += 1

        return schedules, None

    except requests.RequestException as e:
        app_log(f"Schedule API request error: {e}", "ERROR")
        return [], "Unable to reach AniList schedule API."

def get_cached_airing_schedule():
    now_monotonic = time.time()

    with SCHEDULE_CACHE_LOCK:
        if SCHEDULE_CACHE["expires_at"] > now_monotonic:
            return list(SCHEDULE_CACHE["airing_list"]), SCHEDULE_CACHE["error"]

    airing_list, schedule_error = get_airing_schedule()

    with SCHEDULE_CACHE_LOCK:
        SCHEDULE_CACHE["airing_list"] = airing_list
        SCHEDULE_CACHE["error"] = schedule_error
        SCHEDULE_CACHE["expires_at"] = time.time() + SCHEDULE_CACHE_TTL_SECONDS

    return list(airing_list), schedule_error

def find_schedule_library_match(title_candidates, existing_anime_names):
    best_name = None
    best_score = 0

    for candidate in title_candidates:
        if not candidate:
            continue

        if candidate in existing_anime_names:
            return candidate, 1

        for anime_name in existing_anime_names:
            score = get_title_similarity(candidate, anime_name)
            if score > best_score:
                best_score = score
                best_name = anime_name

    if best_name and best_score >= 0.72:
        return best_name, best_score

    return None, best_score

def build_schedule_items(airing_list, local_tz, now_ts):
    existing_anime_names = get_existing_anime_names()
    today_dt = datetime.fromtimestamp(now_ts, local_tz).date()
    processed = []

    for item in airing_list:
        media = item.get("media") or {}
        title_data = media.get("title") or {}
        title = title_data.get("english") or title_data.get("romaji")
        title_candidates = [
            title_data.get("english"),
            title_data.get("romaji")
        ]

        if not title or not item.get("airingAt"):
            continue

        airing_dt = datetime.fromtimestamp(item["airingAt"], local_tz)
        airing_time = airing_dt.strftime("%H:%M")
        airing_date = airing_dt.date()
        day_delta = (airing_date - today_dt).days

        if day_delta == 0:
            date_label = "Today"
        elif day_delta == 1:
            date_label = "Tomorrow"
        elif day_delta == 2:
            date_label = "Day After Tomorrow"
        else:
            date_label = airing_dt.strftime("%A")

        if item["airingAt"] <= now_ts < item["airingAt"] + 1800:
            airing_status = "Airing now"
        elif item["airingAt"] > now_ts:
            airing_status = "Upcoming"
        else:
            airing_status = "Aired"

        cover_image = media.get("coverImage") or {}
        local_anime_name, local_match_score = find_schedule_library_match(
            title_candidates,
            existing_anime_names
        )

        processed.append({
            "title": title,
            "poster": cover_image.get("extraLarge") or url_for("static", filename="arcana.jpg"),
            "episode": item.get("episode"),
            "time": airing_time,
            "date_key": airing_dt.strftime("%Y-%m-%d"),
            "date_label": date_label,
            "date_display": airing_dt.strftime("%A, %d %B %Y"),
            "airing_at": item["airingAt"],
            "airing_iso": airing_dt.isoformat(),
            "format": media.get("format"),
            "status": airing_status,
            "is_in_library": bool(local_anime_name),
            "local_anime_name": local_anime_name,
            "local_match_score": round(local_match_score, 3) if local_match_score else 0,
            "detail_url": url_for("anime_detail", anime_name=local_anime_name) if local_anime_name else None
        })

    return processed

def format_schedule_alert_countdown(seconds):
    total_minutes = max(0, int((seconds + 59) // 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours and minutes:
        return f"Starts in {hours}h {minutes}m"

    if hours:
        return f"Starts in {hours}h"

    return f"Starts in {minutes}m"

def get_schedule_alert_payload(processed, now_ts, now_iso, timezone_offset_minutes):
    current_items = [
        item for item in processed
        if item["airing_at"] <= now_ts < item["airing_at"] + 1800
    ]
    upcoming_items = [
        item for item in processed
        if item["airing_at"] > now_ts
    ]
    alert_items = current_items + upcoming_items
    badge_count = len(current_items) + len([
        item for item in upcoming_items
        if item["airing_at"] <= now_ts + 7200
    ])

    payload_items = []
    for item in alert_items:
        if item in current_items:
            status_mode = "live"
            status_label = "LIVE NOW"
        else:
            status_mode = "upcoming"
            status_label = format_schedule_alert_countdown(item["airing_at"] - now_ts)

        payload_items.append({
            "title": item["title"],
            "poster": item["poster"],
            "episode": item["episode"],
            "time": item["time"],
            "airing_at": item["airing_at"],
            "airing_iso": item["airing_iso"],
            "format": item["format"],
            "detail_url": item["detail_url"],
            "status_mode": status_mode,
            "status_label": status_label
        })

    return {
        "items": payload_items,
        "badge_count": badge_count,
        "summary": f"{len(current_items)} airing now · {len(upcoming_items)} upcoming",
        "now_iso": now_iso,
        "timezone_offset_minutes": timezone_offset_minutes
    }

def update_discord_rpc(anime_name, episode_num, time_str=None, owner_id=None):
    global rpc, rpc_connected, RPC_START_TIME, CURRENT_RPC_ANIME, CURRENT_RPC_OWNER
    if not DISCORD_RPC_ENABLED:
        return

    with RPC_LOCK:
        rpc_client = get_discord_rpc_client()
        if rpc_client is None:
            return

        try:
            if CURRENT_RPC_ANIME != anime_name or CURRENT_RPC_OWNER != owner_id:
                RPC_START_TIME = int(time.time())
            CURRENT_RPC_ANIME = anime_name
            CURRENT_RPC_OWNER = owner_id

            if not rpc_connected:
                rpc_client.connect()
                rpc_connected = True

            episode_label = format_episode_number(episode_num) or str(episode_num)
            state_text = f"Episode {episode_label}"
            if time_str:
                state_text += f" ({time_str})"

            rpc_client.update(
                details=anime_name,
                state=state_text,
                start=RPC_START_TIME,
                large_image="anibase_logo",
                buttons=[{"label": "Open AniBase", "url": "http://127.0.0.1:5000"}]
            )
        except Exception as e:
            app_log(f"Discord RPC error: {e}", "WARN")
            try:
                rpc_client.close()
            except Exception:
                pass
            rpc = None
            rpc_connected = False

def clear_discord_rpc(owner_id=None):
    """Menghapus status Discord Rich Presence."""
    global rpc, rpc_connected, RPC_START_TIME, CURRENT_RPC_ANIME, CURRENT_RPC_OWNER
    if not DISCORD_RPC_ENABLED:
        return

    with RPC_LOCK:
        if owner_id is not None and owner_id != CURRENT_RPC_OWNER:
            return False

        if rpc is not None and rpc_connected:
            try:
                rpc.clear()
                debug_log("Discord RPC cleared")
            except Exception as e:
                app_log(f"Discord RPC clear error: {e}", "WARN")
                try:
                    rpc.close()
                except Exception:
                    pass
                rpc = None
                rpc_connected = False

        RPC_START_TIME = None
        CURRENT_RPC_ANIME = None
        CURRENT_RPC_OWNER = None
        return True

def clear_discord_rpc_when_process_exits(process, owner_id):
    try:
        process.wait()
    except Exception as e:
        app_log(f"Media player monitor error: {e}", "WARN")
    finally:
        clear_discord_rpc(owner_id)

def get_cached_anilist_info(
    anime_name
):

    cache_file = os.path.join(
        METADATA_CACHE,
        f"{anime_name}.json"
    )
    info = None

    # 1. Coba muat dari cache metadata
    if os.path.exists(
        cache_file
    ):
        try:
            with open(
                cache_file,
                "r",
                encoding="utf-8"
            ) as f:
                info = json.load(f)
                # Jika metadata ditemukan tapi tidak punya informasi karakter atau relations, paksa ambil ulang
                if "characters" not in info or "relations" not in info or "recommendations" not in info:
                    info = None
                # Jika ada character dengan va_name tapi tidak ada va_staff_id, upgrade metadata
                elif "characters" in info and any(char.get("va_name") and "va_staff_id" not in char for char in info.get("characters", [])):
                    info = None
                # Releasing anime needs next episode metadata for the airing countdown.
                elif (info.get("status") or "").upper() == "RELEASING" and "next_airing" not in info:
                    info = None
        except:
            info = None

    # 2. Jika tidak ada di cache atau data tidak lengkap, ambil dari AniList API
    if not info:
        info = get_anilist_info(anime_name)
        if info:
            with open(
                cache_file,
                "w",
                encoding="utf-8"
            ) as f:
                json.dump(
                    info,
                    f,
                    ensure_ascii=False,
                    indent=4
                )

    # 3. Proses gambar karakter dan poster relations (Harus dijalankan meskipun metadata sudah ada di cache)
    if info:
        # Download Gambar Karakter & Seiyuu
        if "characters" in info:
            safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
            anime_char_dir = os.path.join(CHARACTER_CACHE, safe_anime)
            
            # Pastikan folder karakter ada (terutama jika baru dihapus)
            os.makedirs(anime_char_dir, exist_ok=True)
            
            for char in info["characters"]:
                # Gambar Karakter
                if char.get("image_url"):
                    clean_name = re.sub(r'[<>:"/\\|?*]', '_', char['name'])
                    fname = f"{clean_name}_char.jpg"
                    fpath = os.path.join(anime_char_dir, fname)
                    # Unduh jika file belum ada
                    if not os.path.exists(fpath):
                        try:
                            r = requests.get(char["image_url"], timeout=15)
                            if r.status_code == 200:
                                with open(fpath, "wb") as f_img:
                                    f_img.write(r.content)
                        except: pass
                    char["image_local"] = fname
                
                # Gambar Seiyuu
                if char.get("va_image_url") and char.get("va_name"):
                    clean_va = re.sub(r'[<>:"/\\|?*]', '_', char['va_name'])
                    fname = f"{clean_va}_va.jpg"
                    fpath = os.path.join(anime_char_dir, fname)
                    # Unduh jika file belum ada
                    if not os.path.exists(fpath):
                        try:
                            r = requests.get(char["va_image_url"], timeout=15)
                            if r.status_code == 200:
                                with open(fpath, "wb") as f_img:
                                    f_img.write(r.content)
                        except: pass
                    char["va_image_local"] = fname

        # Download Poster Relations ke Cache
        if "relations" in info:
            for rel in info["relations"]:
                rel_title = rel.get("title")
                rel_poster_url = rel.get("poster")
                if rel_title and rel_poster_url and rel_poster_url.startswith("http"):
                    safe_rel_title = re.sub(r'[<>:"/\\|?*]', '_', rel_title)
                    rel_poster_path = os.path.join(POSTER_CACHE, f"{safe_rel_title}.jpg")
                    if not os.path.exists(rel_poster_path):
                        try:
                            r = requests.get(rel_poster_url, timeout=15)
                            if r.status_code == 200:
                                with open(rel_poster_path, "wb") as f:
                                    f.write(r.content)
                        except: pass

        # Download Poster Recommendations ke Cache
        if "recommendations" in info:
            for rec in info["recommendations"]:
                rec_title = rec.get("title")
                rec_poster_url = rec.get("poster")
                if rec_title and rec_poster_url and rec_poster_url.startswith("http"):
                    safe_rec_title = re.sub(r'[<>:"/\\|?*]', '_', rec_title)
                    rec_poster_path = os.path.join(POSTER_CACHE, f"{safe_rec_title}.jpg")
                    if not os.path.exists(rec_poster_path):
                        try:
                            r = requests.get(rec_poster_url, timeout=15)
                            if r.status_code == 200:
                                with open(rec_poster_path, "wb") as f:
                                    f.write(r.content)
                        except: pass

    return info

def get_subtitle_vtt_path(anime_name, episode_path):
    # Membuat nama folder dan file yang aman untuk sistem file Windows
    safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    safe_episode = re.sub(r'[<>:"|?*]', '_', episode_path).replace('/', '_').replace('\\', '_')
    
    vtt_filename = f"{safe_episode}.vtt"
    return os.path.join(SUBTITLE_CACHE, safe_anime, vtt_filename)

def make_library_sync_skipped_result(trigger_label):
    return {
        "ok": False,
        "skipped": True,
        "reason": "sync_in_progress",
        "message": f"{trigger_label} skipped because another library sync is already running."
    }

def acquire_library_sync(trigger_label):
    if is_shutdown_requested():
        app_log(
            f"{trigger_label} skipped because shutdown is in progress.",
            "WARN"
        )
        return False

    if LIBRARY_SYNC_LOCK.acquire(blocking=False):
        return True

    app_log(
        f"{trigger_label} skipped because another library sync is already running.",
        "WARN"
    )
    return False

def is_non_subtitle_text(text):
    plain_text = re.sub(r'<[^>]*>', '', text or "")
    plain_text = re.sub(r'\{.*?\}', '', plain_text).strip()
    normalized = " ".join(plain_text.casefold().split())

    if not normalized:
        return True

    if re.search(r'\b(?:https?://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b', normalized):
        return True

    numeric_tokens = re.findall(r'-?\d+(?:\.\d+)?', normalized)
    starts_like_ass_drawing = bool(
        re.match(r'^(?:m|n|l|b|s|p|c)\s+-?\d', normalized)
    )

    if starts_like_ass_drawing and len(numeric_tokens) >= 4:
        return True

    return False

def _vtt_timestamp_seconds(value):
    parts = value.strip().replace(',', '.').split(':')
    try:
        if len(parts) == 3:
            return (float(parts[0]) * 3600) + (float(parts[1]) * 60) + float(parts[2])
        if len(parts) == 2:
            return (float(parts[0]) * 60) + float(parts[1])
    except (TypeError, ValueError):
        pass
    return None

def _subtitle_text_key(text):
    plain_text = re.sub(r'<[^>]*>', '', text or "")
    plain_text = re.sub(r'\{.*?\}', '', plain_text)
    return " ".join(plain_text.casefold().split())

def clean_generated_subtitle_vtt(vtt_path):
    with open(vtt_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    cues = []
    i = 0
    while i < len(lines):
        timestamp = lines[i].strip()
        if "-->" not in timestamp:
            i += 1
            continue

        start_raw, end_raw = (part.strip().split()[0] for part in timestamp.split("-->", 1))
        start = _vtt_timestamp_seconds(start_raw)
        end = _vtt_timestamp_seconds(end_raw)
        cue_text = []
        i += 1
        while i < len(lines) and lines[i].strip() != "" and "-->" not in lines[i]:
            text_line = re.sub(r'\{.*?\}', '', lines[i].strip())
            if not is_non_subtitle_text(text_line):
                cue_text.append(text_line)
            i += 1
        cues.append({"timestamp": timestamp, "start": start, "end": end, "text": cue_text})

    # Fansub watermarks are commonly one very long cue, or the same line copied
    # into many cues. Remove only that line so dialogue sharing its cue survives.
    video_end = max((cue["end"] or 0 for cue in cues), default=0)
    occurrences = {}
    for cue in cues:
        for text_line in cue["text"]:
            key = _subtitle_text_key(text_line)
            if key:
                occurrences.setdefault(key, []).append(cue)

    watermark_keys = set()
    for key, matching_cues in occurrences.items():
        durations = sum(
            max(0, cue["end"] - cue["start"])
            for cue in matching_cues
            if cue["start"] is not None and cue["end"] is not None
        )
        first_start = min((cue["start"] for cue in matching_cues if cue["start"] is not None), default=0)
        last_end = max((cue["end"] for cue in matching_cues if cue["end"] is not None), default=0)
        spans_episode = video_end >= 60 and first_start <= 15 and last_end >= video_end * .9
        if video_end >= 60 and durations >= video_end * .8:
            watermark_keys.add(key)
        elif len(matching_cues) >= 8 and spans_episode:
            watermark_keys.add(key)

    cleaned_lines = ["WEBVTT\n"]
    for cue in cues:
        cue_text = [line for line in cue["text"] if _subtitle_text_key(line) not in watermark_keys]
        if cue_text:
            cleaned_lines.append("\n" + cue["timestamp"] + "\n")
            cleaned_lines.append("\n".join(cue_text) + "\n")

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)

def generate_subtitle_vtt_result(video_path, vtt_path):
    if is_valid_cache_file(vtt_path):
        return make_media_generation_result(
            True,
            vtt_path,
            "cached",
            "Subtitle cache found."
        )

    fingerprint = media_source_fingerprint(video_path)
    if fingerprint is None:
        return make_media_generation_result(
            False,
            "",
            "source_not_found",
            "Media source was not found."
        )

    failure_key = ("subtitle", vtt_path, fingerprint)
    cached_failure = get_ffmpeg_failure_cache(failure_key)
    if cached_failure:
        return cached_failure

    lock = acquire_ffmpeg_media_lock(("subtitle", vtt_path))
    if lock is None:
        return make_media_generation_result(
            False,
            "",
            "busy",
            "Subtitle generation is already running."
        )

    temp_path = ""
    last_result = None

    try:
        if is_valid_cache_file(vtt_path):
            return make_media_generation_result(
                True,
                vtt_path,
                "cached",
                "Subtitle cache found."
            )

        cached_failure = get_ffmpeg_failure_cache(failure_key)
        if cached_failure:
            return cached_failure

        os.makedirs(os.path.dirname(vtt_path), exist_ok=True)
        temp_path = temporary_media_cache_path(vtt_path, ".vtt")
        app_log(f"Starting subtitle generation for {os.path.basename(video_path)}", "INFO")

        last_result = run_ffmpeg_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-map",
                "0:s:0",
                temp_path
            ],
            timeout=SUBTITLE_GENERATION_TIMEOUT_SECONDS
        )

        if last_result["ok"] and is_valid_cache_file(temp_path):
            try:
                clean_generated_subtitle_vtt(temp_path)
            except Exception as clean_err:
                app_log(f"Unable to clean subtitle cache: {clean_err}", "WARN")

            if is_valid_cache_file(temp_path):
                os.replace(temp_path, vtt_path)
                clear_ffmpeg_failure_cache(failure_key)
                return make_media_generation_result(
                    True,
                    vtt_path,
                    "generated",
                    "Subtitle generated."
                )

            last_result = make_media_generation_result(
                False,
                "",
                "failed",
                "Subtitle output was empty."
            )

        remove_file_quietly(temp_path)
        if last_result:
            app_log(f"Subtitle generation failed for {os.path.basename(video_path)}: {last_result['message']}", "WARN")
        else:
            app_log(f"Subtitle generation failed for {os.path.basename(video_path)}", "WARN")

    except Exception as e:
        app_log(f"Error while creating subtitle: {e}", "ERROR")
        last_result = make_media_generation_result(
            False,
            "",
            "error",
            "Subtitle generation failed."
        )
    finally:
        remove_file_quietly(temp_path)
        release_ffmpeg_media_lock(("subtitle", vtt_path), lock)

    last_result = last_result or make_media_generation_result(
        False,
        "",
        "failed",
        "Subtitle was not generated."
    )
    if last_result["status"] != "busy":
        set_ffmpeg_failure_cache(failure_key, last_result)
    return last_result

def generate_subtitle_vtt(video_path, vtt_path):
    return generate_subtitle_vtt_result(video_path, vtt_path).get("ok", False)

def get_watch_backup_file(path):
    return f"{path}.bak"

def get_corrupt_watch_file(path):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    corrupt_path = f"{path}.corrupt-{timestamp}"
    index = 1

    while os.path.exists(corrupt_path):
        corrupt_path = f"{path}.corrupt-{timestamp}-{index}"
        index += 1

    return corrupt_path

def read_json_dict_file(path, label):
    if not os.path.exists(path):
        return None, "missing"

    try:
        if os.path.getsize(path) == 0:
            return None, "empty"

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    except OSError as e:
        return None, f"OS error: {e}"

    if not isinstance(data, dict):
        return None, "JSON root is not an object"

    return data, None

def atomic_write_json_file(path, data, label, ensure_ascii=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    tmp_path = os.path.join(
        os.path.dirname(path),
        f".{os.path.basename(path)}.tmp-{os.getpid()}-{threading.get_ident()}"
    )

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=ensure_ascii)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        debug_log(f"{label} saved atomically: {path}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def preserve_corrupt_watch_file(path, label, reason):
    if not os.path.exists(path):
        return

    corrupt_path = get_corrupt_watch_file(path)

    try:
        os.replace(path, corrupt_path)
        app_log(f"{label} preserved as corrupt file: {corrupt_path} ({reason})", "WARN")
    except OSError as e:
        app_log(f"{label} could not preserve corrupt file {path}: {e}", "ERROR")

def load_watch_json_file(path, label):
    with WATCH_DATA_LOCK:
        data, error = read_json_dict_file(path, label)

        if error is None:
            return data

        backup_path = get_watch_backup_file(path)

        if error == "missing":
            debug_log(f"{label} file missing, checking backup: {backup_path}")
        else:
            app_log(f"{label} file could not be loaded ({error}), checking backup.", "WARN")
            preserve_corrupt_watch_file(path, label, error)

        backup_data, backup_error = read_json_dict_file(backup_path, f"{label} backup")

        if backup_error is None:
            app_log(f"{label} recovered from backup: {backup_path}", "WARN")
            try:
                atomic_write_json_file(path, backup_data, label)
            except OSError as e:
                app_log(f"{label} recovery loaded backup but could not restore main file: {e}", "ERROR")
            return backup_data

        debug_log(f"{label} backup unavailable or invalid ({backup_error}); using empty data.")
        return {}

def save_watch_json_file(path, data, label):
    if not isinstance(data, dict):
        raise ValueError(f"{label} data must be a dictionary")

    with WATCH_DATA_LOCK:
        atomic_write_json_file(path, data, label)

        backup_path = get_watch_backup_file(path)
        try:
            atomic_write_json_file(backup_path, data, f"{label} backup")
        except OSError as e:
            app_log(f"{label} saved, but backup write failed: {e}", "WARN")

def save_watch_history(history):
    save_watch_json_file(WATCH_HISTORY_FILE, history, "Watch history")

MOVIE_HISTORY_PREFIX = "movie::"

def get_watch_history_key(anime_name, episode):
    if anime_name == "Movies":
        return f"{MOVIE_HISTORY_PREFIX}{episode}"

    return anime_name

def get_watch_history_display_name(anime_name, episode):
    if anime_name == "Movies":
        return clean_movie_title(episode)

    return anime_name

def get_continue_progress_percent(data):
    try:
        current_seconds = float(data.get("last_seconds", 0) or 0)
        duration = float(data.get("duration", 0) or 0)
    except (TypeError, ValueError):
        return 0

    if duration <= 0 or duration != duration or current_seconds != current_seconds:
        return 0

    progress = (current_seconds / duration) * 100
    return max(0, min(100, round(progress)))

def update_watch_history(
    history_key,
    episode,
    episode_num,
    time_str=None,
    last_seconds=0,
    duration=0,
    media_name=None,
    display_name=None
):
    with WATCH_DATA_LOCK:
        history = load_history_data()

        history[history_key] = {
            "episode": episode,
            "episode_num": episode_num,
            "updated_at": datetime.now().isoformat(),
            "time_str": time_str,
            "last_seconds": last_seconds,
            "duration": duration,
            "media_name": media_name or history_key,
            "display_name": display_name or history_key
        }

        save_watch_history(history)

def remove_watch_history_entry(history_key):
    with WATCH_DATA_LOCK:
        history = load_history_data()

        if history_key not in history:
            return False

        history.pop(history_key, None)
        save_watch_history(history)
        return True

def load_watch_status():
    return load_watch_json_file(WATCH_STATUS_FILE, "Watch status")

def save_watch_status(status_data):
    save_watch_json_file(WATCH_STATUS_FILE, status_data, "Watch status")

def get_episode_watch_status(anime_name, episode):
    status_data = load_watch_status()

    return (
        status_data
        .get(anime_name, {})
        .get(episode, {})
    )

def update_episode_watch_status(anime_name, episode, data):
    with WATCH_DATA_LOCK:
        status_data = load_watch_status()
        anime_status = status_data.setdefault(anime_name, {})
        episode_status = anime_status.setdefault(episode, {
            "watched": False,
            "progress": 0,
            "duration": 0
        })

        episode_status.setdefault("watched", False)
        episode_status.setdefault("progress", 0)
        episode_status.setdefault("duration", 0)
        episode_status.update(data or {})
        episode_status["updated_at"] = datetime.now().isoformat()

        save_watch_status(status_data)
        return episode_status

def mark_episode_watched(anime_name, episode):
    return update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": True,
            "progress": 100
        }
    )

def mark_episode_unwatched(anime_name, episode):
    return update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": False,
            "progress": 0
        }
    )

def sync_anime_to_db(anime_name, trigger_label=None):
    """Memindai satu folder anime dan memperbarui database SQLite.
       Fungsi ini dipanggil oleh background scanner."""
    trigger_label = trigger_label or f"Anime sync for {anime_name}"
    if not acquire_library_sync(trigger_label):
        return make_library_sync_skipped_result(trigger_label)

    try:
        anime_path = find_anime_path(anime_name)
        if not anime_path:
            configured_paths = get_configured_anime_paths()
            _, failed_roots = collect_library_scan_roots(configured_paths)
            if failed_roots:
                root_list = "; ".join(
                    f"{item['path']} ({item['reason']})"
                    for item in failed_roots
                )
                app_log(
                    f"Skipped removal for missing anime {anime_name} because "
                    f"not every configured library root is safely scannable: {root_list}",
                    "WARN"
                )
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "library_roots_untrusted",
                    "anime_name": anime_name,
                    "failed_roots": failed_roots
                }

            with db_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL") # Optimasi konkurensi
                conn.execute("DELETE FROM anime_library WHERE name = ?", (anime_name,))
            summary = cleanup_anime_cache(anime_name)
            app_log(
                f"Cleaned cache for deleted anime {anime_name}: "
                f"{summary['removed_files']} files, {summary['removed_dirs']} folders, "
                "watch data preserved."
            )
            return {"ok": True, "skipped": False, "anime_name": anime_name}

        episode_count = 0
        for root, dirs, files in os.walk(anime_path):
            for file in files:
                if file.lower().endswith(VIDEO_EXTENSIONS):
                    episode_count += 1

        info = get_cached_anilist_info(anime_name)
        
        with db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO anime_library (name, episodes, score, genres, year, season, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                anime_name,
                episode_count,
                info.get("score") if info else None,
                json.dumps(info.get("genres")) if info else None,
                info.get("year") if info else None,
                info.get("season") if info else None,
                info.get("status") if info else None
            ))
        return {"ok": True, "skipped": False, "anime_name": anime_name}
    finally:
        LIBRARY_SYNC_LOCK.release()

def sync_all_library(trigger_label="Full library sync", enrich_metadata=True, progress_callback=None):
    """Pemindaian penuh seluruh folder anime di latar belakang."""
    if not acquire_library_sync(trigger_label):
        return make_library_sync_skipped_result(trigger_label)

    try:
        debug_log("Syncing library...")
        found_data = []
        found_names = []
        configured_paths = get_configured_anime_paths()
        previous_row_count = count_library_rows()
        scan_roots, failed_roots = collect_library_scan_roots(configured_paths)
        scan_errors = []

        total_anime = sum(
            1
            for root_info in scan_roots
            for name in root_info["entries"]
            if is_anime_library_folder(os.path.join(root_info["path"], name))
        )
        processed_anime = 0
        if progress_callback:
            progress_callback("metadata" if enrich_metadata else "scan", 0, total_anime)

        if not scan_roots:
            app_log("Sync skipped. No valid anime folders configured.", "WARN")
            return {"ok": True, "skipped": True, "reason": "no_valid_library_paths"}

        can_delete_stale = not failed_roots

        for root_info in scan_roots:
            if is_shutdown_requested():
                break
            base_path = root_info["path"]
            for name in root_info["entries"]:
                if is_shutdown_requested():
                    break
                full_path = os.path.join(base_path, name)
                if is_anime_library_folder(full_path):
                    found_names.append(name)
                    
                    # Hitung episode
                    episode_count = 0
                    walk_errors = []
                    def record_walk_error(error):
                        walk_errors.append(error)

                    for root, dirs, files in os.walk(full_path, onerror=record_walk_error):
                        for file in files:
                            if file.lower().endswith(VIDEO_EXTENSIONS):
                                episode_count += 1

                    if walk_errors:
                        for error in walk_errors:
                            scan_errors.append({
                                "path": getattr(error, "filename", full_path) or full_path,
                                "reason": str(error)
                            })
                        continue
                    
                    if enrich_metadata:
                        # Batasi laju permintaan AniList untuk metadata yang belum di-cache.
                        cache_file = os.path.join(METADATA_CACHE, f"{name}.json")
                        info = get_cached_anilist_info(name)
                        if not os.path.exists(cache_file) and wait_for_shutdown(0.7):
                            break
                    else:
                        # Setup pertama hanya perlu mengindeks file lokal. Metadata yang
                        # sudah ada tetap dipakai tanpa melakukan permintaan jaringan.
                        info = get_cached_metadata_only(name)

                    found_data.append((
                        name,
                        episode_count,
                        info.get("score") if info else None,
                        json.dumps(info.get("genres")) if info else None,
                        info.get("year") if info else None,
                        info.get("season") if info else None,
                        info.get("status") if info else None
                    ))
                    processed_anime += 1
                    if progress_callback:
                        progress_callback(
                            "metadata" if enrich_metadata else "scan",
                            processed_anime,
                            total_anime
                        )

        if scan_errors:
            can_delete_stale = False

        if previous_row_count > 0 and not found_names:
            can_delete_stale = False
            app_log(
                "Full sync found zero anime while existing library data is present. "
                "Stale database/cache cleanup was skipped to protect temporary drive outages.",
                "WARN"
            )

        if failed_roots:
            root_list = "; ".join(
                f"{item['path']} ({item['reason']})"
                for item in failed_roots
            )
            app_log(
                f"Full sync could not scan every configured root. "
                f"Stale database/cache cleanup was skipped: {root_list}",
                "WARN"
            )

        if scan_errors:
            error_list = "; ".join(
                f"{item['path']} ({item['reason']})"
                for item in scan_errors
            )
            app_log(
                f"Full sync encountered filesystem errors. "
                f"Stale database/cache cleanup was skipped: {error_list}",
                "WARN"
            )
        
        stale_names = []

        # Update database secara batch dalam satu koneksi
        with db_connection() as conn:
            if can_delete_stale:
                if found_names:
                    placeholders = ','.join(['?'] * len(found_names))
                    stale_rows = conn.execute(
                        f"SELECT name FROM anime_library WHERE name NOT IN ({placeholders})",
                        found_names
                    ).fetchall()
                else:
                    stale_rows = conn.execute("SELECT name FROM anime_library").fetchall()

                stale_names = [
                    row[0]
                    for row in stale_rows
                    if row and row[0]
                ]

            # Update/Insert data yang ditemukan
            if found_data:
                conn.executemany("""
                    INSERT OR REPLACE INTO anime_library (name, episodes, score, genres, year, season, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, found_data)

            # Hapus entri di DB jika foldernya sudah tidak ada di disk
            if can_delete_stale and found_names:
                placeholders = ','.join(['?'] * len(found_names))
                conn.execute(f"DELETE FROM anime_library WHERE name NOT IN ({placeholders})", found_names)
            elif can_delete_stale:
                conn.execute("DELETE FROM anime_library")

        if not can_delete_stale:
            stale_names = []

        for stale_name in stale_names:
            summary = cleanup_anime_cache(stale_name)
            app_log(
                f"Cleaned stale cache for {stale_name}: "
                f"{summary['removed_files']} files, {summary['removed_dirs']} folders, "
                "watch data preserved."
            )
                
        app_log(f"Sync complete. Detected {len(found_names)} anime.")
        return {
            "ok": True,
            "skipped": False,
            "anime_count": len(found_names),
            "stale_count": len(stale_names),
            "stale_cleanup_skipped": not can_delete_stale,
            "failed_roots": failed_roots,
            "scan_errors": scan_errors
        }
    finally:
        LIBRARY_SYNC_LOCK.release()

def get_anime_folder_index():
    folders = []

    for base_path in get_valid_anime_paths():
        try:
            for name in os.listdir(base_path):
                full_path = os.path.join(base_path, name)
                if is_anime_library_folder(full_path):
                    folders.append({
                        "name": name,
                        "path": full_path,
                        "base_path": base_path
                    })
        except OSError:
            continue

    return folders

def normalize_title_for_match(value):
    value = os.path.splitext(os.path.basename(value or ""))[0]
    value = re.sub(r'[\[\(【].*?[\]\)】]', ' ', value)
    value = re.sub(r'(?i)\b(?:lendrive|kusonime|samehadaku|dualsubs?|multi subs?|hevc|x264|x265|h264|h265|aac|flac|web-dl|webdl|bd|bluray|1080p|720p|480p|2160p|4k)\b', ' ', value)
    value = re.sub(r'(?i)\b(?:episode|episodes|eps?|e)\s*\d+\b', ' ', value)
    value = re.sub(r'(?i)(?:^|[\s._-])(?:s\d{1,2})?\d{1,3}(?:v\d+)?(?:$|[\s._-])', ' ', value)
    value = re.sub(r'[_\-.]+', ' ', value)
    value = re.sub(r'[^a-zA-Z0-9\s]', ' ', value)
    return " ".join(value.split())

def get_match_key(value):
    return re.sub(r'[^a-z0-9]+', '', (value or "").casefold())

def clean_auto_import_folder_name(clean_title):
    folder_name = re.sub(r'[<>:"/\\|?*]', ' ', clean_title or "")
    folder_name = re.sub(r'\s+', ' ', folder_name).strip(" .")
    return folder_name[:120].strip(" .")

def get_title_similarity(short_title, folder_title):
    short_key = get_match_key(short_title)
    folder_key = get_match_key(folder_title)

    if not short_key or not folder_key:
        return 0

    if short_key == folder_key:
        return 1

    if short_key in folder_key:
        return 0.94

    short_words = set(normalize_title_for_match(short_title).casefold().split())
    folder_words = set(normalize_title_for_match(folder_title).casefold().split())
    word_score = 0
    if short_words and folder_words:
        word_score = len(short_words & folder_words) / len(short_words)

    ratio = SequenceMatcher(None, short_key, folder_key).ratio()
    return max(ratio, word_score)

def get_auto_import_target_root(settings=None):
    settings = settings if isinstance(settings, dict) else load_settings()
    destination_root = get_configured_auto_import_destination(settings)
    if destination_root and os.path.isdir(destination_root):
        return destination_root

    return ""

def get_auto_import_candidate_folders(settings):
    target_root = get_auto_import_target_root(settings)
    if not target_root:
        return []

    target_key = os.path.normcase(os.path.realpath(os.path.abspath(target_root)))
    return [
        folder
        for folder in get_anime_folder_index()
        if os.path.normcase(
            os.path.realpath(os.path.abspath(folder.get("base_path", "")))
        ) == target_key
    ]

def resolve_auto_import_target(clean_title, settings):
    mappings = settings.get("auto_import_mappings", {})
    mapped_title = mappings.get(clean_title)
    if not mapped_title:
        clean_key = get_match_key(clean_title)
        for mapping_key, mapping_value in mappings.items():
            if get_match_key(mapping_key) == clean_key:
                mapped_title = mapping_value
                break

    folders = get_auto_import_candidate_folders(settings)
    if mapped_title:
        for folder in folders:
            if folder["name"] == mapped_title:
                return folder, 1, "manual_mapping"

    best_folder = None
    best_score = 0
    for folder in folders:
        score = get_title_similarity(clean_title, folder["name"])
        if score > best_score:
            best_score = score
            best_folder = folder

    if best_folder and best_score >= 0.72:
        return best_folder, best_score, "similarity"

    return None, best_score, "unmatched"

def create_auto_import_ongoing_target(clean_title, settings):
    if not settings.get("auto_import_create_ongoing_folders"):
        return None

    target_root = get_auto_import_target_root(settings)
    if not target_root:
        return None

    folder_name = clean_auto_import_folder_name(clean_title)
    if len(get_match_key(folder_name)) < 3:
        return None

    target_path = os.path.join(target_root, folder_name)
    os.makedirs(target_path, exist_ok=True)
    return {
        "name": folder_name,
        "path": target_path,
        "base_path": target_root
    }

def unique_destination_path(folder_path, filename):
    stem, ext = os.path.splitext(filename)
    destination = os.path.join(folder_path, filename)
    index = 2

    while os.path.exists(destination):
        destination = os.path.join(folder_path, f"{stem} ({index}){ext}")
        index += 1

    return destination

def update_auto_import_settings_state(recent=None, unmatched=None):
    with AUTO_IMPORT_STATE_LOCK:
        settings = load_settings()

        if recent:
            existing_recent = settings.get("auto_import_recent_imports", [])
            settings["auto_import_recent_imports"] = (recent + existing_recent)[:25]

        if unmatched:
            existing_unmatched = settings.get("auto_import_unmatched", [])
            unmatched_by_path = {
                item.get("source_path"): item
                for item in existing_unmatched
                if item.get("source_path")
            }
            for item in unmatched:
                source_path = item.get("source_path")
                if source_path:
                    unmatched_by_path[source_path] = item
            settings["auto_import_unmatched"] = list(unmatched_by_path.values())[-50:]

        save_settings(settings)

def remove_auto_import_unmatched(source_path):
    with AUTO_IMPORT_STATE_LOCK:
        settings = load_settings()
        settings["auto_import_unmatched"] = [
            item
            for item in settings.get("auto_import_unmatched", [])
            if item.get("source_path") != source_path
        ]
        save_settings(settings)

def is_download_temp_file(path):
    lowered = path.lower()
    return lowered.endswith(DOWNLOAD_TEMP_EXTENSIONS)

def should_log_auto_import(path, event_key, fingerprint=None):
    now = time.time()
    state_key = (path, event_key)
    state = AUTO_IMPORT_LOG_STATE.get(state_key)

    if state and state.get("fingerprint") == fingerprint:
        return False

    AUTO_IMPORT_LOG_STATE[state_key] = {
        "fingerprint": fingerprint,
        "logged_at": now
    }

    for key, value in list(AUTO_IMPORT_LOG_STATE.items()):
        if now - value.get("logged_at", now) > AUTO_IMPORT_LOG_TTL_SECONDS:
            AUTO_IMPORT_LOG_STATE.pop(key, None)

    return True

def has_related_temp_download(path):
    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    stem, _ = os.path.splitext(filename)

    try:
        for item in os.listdir(directory):
            item_lower = item.lower()
            if item.startswith(stem) and item_lower.endswith(DOWNLOAD_TEMP_EXTENSIONS):
                return True
    except OSError:
        return True

    return False

def is_file_stable_for_import(path, stable_seconds):
    try:
        size = os.path.getsize(path)
        modified_at = os.path.getmtime(path)
    except OSError:
        return False, "file is not readable yet"

    now = time.time()
    state = AUTO_IMPORT_FILE_STATE.get(path)
    if not state or state.get("size") != size:
        AUTO_IMPORT_FILE_STATE[path] = {
            "size": size,
            "modified_at": modified_at,
            "last_change": now,
        }
        return False, "waiting for file size to become stable"

    stable_for = now - state.get("last_change", now)
    if stable_for < stable_seconds:
        if should_log_auto_import(
            path,
            "stable_wait",
            (size, state.get("modified_at"), int(state.get("last_change", now)))
        ):
            debug_log(f"Auto import waiting for stable file: {os.path.basename(path)}")
        return False, f"file stable for {int(stable_for)}s"

    try:
        with open(path, "rb"):
            pass
    except OSError as e:
        return False, f"file is locked: {e}"

    return True, "ready"

def record_auto_import_unmatched(path, clean_title, reason):
    item = {
        "filename": os.path.basename(path),
        "source_path": path,
        "clean_title": clean_title,
        "reason": reason,
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    update_auto_import_settings_state(unmatched=[item])

def move_auto_import_file(source_path, target_folder, clean_title, match_score=None):
    os.makedirs(target_folder, exist_ok=True)
    destination = unique_destination_path(target_folder, os.path.basename(source_path))
    shutil.move(source_path, destination)
    AUTO_IMPORT_FILE_STATE.pop(source_path, None)
    for key in list(AUTO_IMPORT_LOG_STATE.keys()):
        if key[0] == source_path:
            AUTO_IMPORT_LOG_STATE.pop(key, None)

    anime_name = os.path.basename(target_folder)
    recent_item = {
        "filename": os.path.basename(destination),
        "from": source_path,
        "to": destination,
        "anime": anime_name,
        "clean_title": clean_title,
        "match_score": match_score,
        "imported_at": datetime.now().isoformat(timespec="seconds")
    }
    update_auto_import_settings_state(recent=[recent_item])
    remove_auto_import_unmatched(source_path)
    sync_anime_to_db(anime_name, trigger_label=f"Auto import sync for {anime_name}")
    app_log(f"Auto import moved: {os.path.basename(destination)} -> {target_folder}")
    return destination

def move_auto_import_to_unmatched(source_path, clean_title, reason):
    root = get_auto_import_target_root()
    if not root:
        record_auto_import_unmatched(source_path, clean_title, "No anime destination folder configured.")
        app_log(f"Auto import unmatched without target root: {os.path.basename(source_path)}", "WARN")
        return None

    unmatched_folder = os.path.join(root, "_Unmatched Downloads")
    destination = move_auto_import_file(
        source_path,
        unmatched_folder,
        clean_title,
        None
    )
    record_auto_import_unmatched(destination, clean_title, reason)
    sync_all_library("Auto import unmatched sync")
    app_log(f"Auto import unmatched: {os.path.basename(destination)} ({reason})", "WARN")
    return destination

def auto_import_candidate_files(downloads_path):
    try:
        names = os.listdir(downloads_path)
    except OSError as e:
        app_log(f"Auto import cannot read downloads folder: {e}", "ERROR")
        return []

    candidates = []
    for name in names:
        path = os.path.join(downloads_path, name)
        if not os.path.isfile(path):
            continue
        lowered = name.lower()
        if is_download_temp_file(path):
            continue
        if not lowered.endswith(VIDEO_EXTENSIONS):
            continue
        candidates.append(path)

    return candidates

def is_real_path_inside(base_path, candidate_path):
    if not base_path or not candidate_path:
        return False

    base_real = os.path.normcase(os.path.realpath(os.path.abspath(base_path)))
    candidate_real = os.path.normcase(os.path.realpath(os.path.abspath(candidate_path)))

    try:
        return os.path.commonpath([base_real, candidate_real]) == base_real
    except ValueError:
        return False

def is_valid_auto_import_resolve_source(source_path, settings):
    if not source_path:
        return False

    source_real = os.path.realpath(os.path.abspath(source_path))

    if not os.path.isfile(source_real):
        return False

    if not source_real.lower().endswith(VIDEO_EXTENSIONS):
        return False

    downloads_path = normalize_library_path(settings.get("auto_import_downloads_path"))
    if downloads_path and os.path.isdir(downloads_path):
        if is_real_path_inside(downloads_path, source_real):
            return True

    for item in settings.get("auto_import_unmatched", []):
        if not isinstance(item, dict):
            continue

        unmatched_path = item.get("source_path")
        if not unmatched_path:
            continue

        unmatched_real = os.path.realpath(os.path.abspath(unmatched_path))
        if os.path.normcase(unmatched_real) == os.path.normcase(source_real):
            return os.path.isfile(unmatched_real)

    return False

def auto_import_scan_once(force_enabled=False):
    settings = load_settings()

    if not force_enabled and not settings.get("auto_import_enabled"):
        return {"processed": 0, "moved": 0, "unmatched": 0, "errors": 0}

    downloads_path = normalize_library_path(settings.get("auto_import_downloads_path"))
    if not downloads_path or not os.path.isdir(downloads_path):
        if should_log_auto_import(
            "auto_import_settings",
            "invalid_downloads_path",
            downloads_path
        ):
            app_log("Auto import inactive. Downloads folder is invalid.", "WARN")
            debug_log(f"Invalid auto-import downloads folder: {downloads_path}")
        return {"processed": 0, "moved": 0, "unmatched": 0, "errors": 1}

    stable_seconds = settings.get("auto_import_stable_seconds", 60)
    summary = {"processed": 0, "moved": 0, "unmatched": 0, "errors": 0}

    for path in auto_import_candidate_files(downloads_path):
        summary["processed"] += 1
        filename = os.path.basename(path)
        try:
            file_fingerprint = (os.path.getsize(path), os.path.getmtime(path))
        except OSError:
            file_fingerprint = None

        if should_log_auto_import(path, "detected", file_fingerprint):
            debug_log(f"Auto import detected: {filename}")

        if has_related_temp_download(path):
            if should_log_auto_import(path, "temp_wait", file_fingerprint):
                debug_log(f"Auto import waiting for temp download to finish: {filename}")
            continue

        is_stable, stable_reason = is_file_stable_for_import(path, stable_seconds)
        if not is_stable:
            continue

        clean_title = normalize_title_for_match(filename)
        target, score, match_method = resolve_auto_import_target(clean_title, settings)
        if not target:
            target = create_auto_import_ongoing_target(clean_title, settings)
            if target:
                score = 1
                match_method = "created_ongoing_folder"

        try:
            if target:
                move_auto_import_file(path, target["path"], clean_title, round(score, 3))
                summary["moved"] += 1
            else:
                reason = f"No confident match for '{clean_title}' (best score {score:.2f})."
                move_auto_import_to_unmatched(path, clean_title, reason)
                summary["unmatched"] += 1
        except OSError as e:
            summary["errors"] += 1
            record_auto_import_unmatched(path, clean_title, str(e))
            app_log(f"Auto import move error for {filename}: {e}", "ERROR")

    return summary

def auto_import_worker():
    last_enabled_state = None

    while not is_shutdown_requested():
        settings = load_settings()
        enabled = settings.get("auto_import_enabled", False)

        if enabled != last_enabled_state:
            state_label = "enabled" if enabled else "disabled"
            app_log(f"Auto import {state_label}.")
            last_enabled_state = enabled

        if enabled:
            auto_import_scan_once()
            sleep_seconds = settings.get("auto_import_interval_seconds", 15)
        else:
            sleep_seconds = 5

        if wait_for_shutdown(max(5, sleep_seconds)):
            break

def start_auto_import_worker():
    global AUTO_IMPORT_THREAD

    if is_shutdown_requested():
        return

    with AUTO_IMPORT_THREAD_LOCK:
        if AUTO_IMPORT_THREAD is not None and AUTO_IMPORT_THREAD.is_alive():
            return

        AUTO_IMPORT_THREAD = threading.Thread(
            target=auto_import_worker,
            name="auto-import",
            daemon=True
        )
        AUTO_IMPORT_THREAD.start()

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
    movie_path = normalize_library_path(settings.get("movie_path", ""))
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
        discord_state = "valid"
        discord_text = "Active"
        discord_detail = "Rich Presence is enabled"
    elif discord_enabled:
        discord_state = "invalid"
        discord_text = "Needs Client ID"
        discord_detail = "Add a Discord Client ID to connect"
    else:
        discord_state = "off"
        discord_text = "Off"
        discord_detail = "Rich Presence is disabled"

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

def build_auto_import_overview(settings, anime_folders):
    downloads_path = normalize_library_path(
        settings.get("auto_import_downloads_path", "")
    )
    recent_imports = settings.get("auto_import_recent_imports", [])
    unmatched_items = settings.get("auto_import_unmatched", [])

    overview = {
        "enabled": bool(settings.get("auto_import_enabled")),
        "downloads_path": downloads_path,
        "downloads_exists": bool(downloads_path and os.path.isdir(downloads_path)),
        "destination_root": get_auto_import_target_root(settings) if downloads_path else "",
        "interval_seconds": settings.get("auto_import_interval_seconds", 15),
        "stable_seconds": settings.get("auto_import_stable_seconds", 60),
        "recent_imports": [],
        "unmatched": []
    }

    if isinstance(recent_imports, list):
        for item in recent_imports[:6]:
            if not isinstance(item, dict):
                continue
            overview["recent_imports"].append({
                "filename": item.get("filename") or os.path.basename(item.get("to", "")),
                "source": item.get("from", ""),
                "target": item.get("to", ""),
                "anime": item.get("anime", ""),
                "imported_at": item.get("imported_at", ""),
                "match_score": item.get("match_score")
            })

    if isinstance(unmatched_items, list):
        for index, item in enumerate(unmatched_items[:8]):
            if not isinstance(item, dict):
                continue

            clean_title = item.get("clean_title", "")
            suggested_target, score, match_method = resolve_auto_import_target(clean_title, settings)
            source_path = item.get("source_path", "")
            overview["unmatched"].append({
                "index": index,
                "filename": item.get("filename") or os.path.basename(source_path),
                "source_path": source_path,
                "clean_title": clean_title,
                "reason": item.get("reason", "No confident match."),
                "created_at": item.get("created_at", ""),
                "exists": bool(source_path and os.path.isfile(source_path)),
                "suggested_anime": suggested_target["name"] if suggested_target else "",
                "suggested_path": suggested_target["path"] if suggested_target else "",
                "suggested_score": round(score * 100) if score else 0,
                "match_method": match_method
            })

    return overview

def build_auto_import_mappings_from_pairs(sources, targets):
    return {
        str(source).strip(): str(target).strip()
        for source, target in zip(sources or [], targets or [])
        if str(source).strip() and str(target).strip()
    }

@app.route("/setup", methods=["GET", "POST"])
@host_only
def setup_page():
    settings = load_settings()
    if request.method == "GET" and is_setup_complete(settings):
        return redirect(url_for("index"))

    error = ""
    if request.method == "POST":
        if not validate_action_token():
            return json_error(
                "invalid_action_token",
                "Invalid or missing action token.",
                403
            )

        library_paths = normalize_library_paths(request.form.getlist("library_paths"))
        if not library_paths:
            error = "Add at least one anime library folder to continue."
        else:
            theme_preset = request.form.get("theme_preset", "dark-blue").strip()
            if theme_preset not in THEME_PRESETS:
                theme_preset = "dark-blue"

            existing_settings = load_settings()
            setup_settings = get_default_settings()
            setup_settings.update(existing_settings)
            setup_settings.update({
                "setup_completed": True,
                "library_paths": library_paths,
                "watchlist_path": library_paths[0] if library_paths else "",
                "ongoing_path": library_paths[1] if len(library_paths) > 1 else "",
                "lan_access_enabled": False,
                "theme_preset": theme_preset,
                "auto_import_enabled": False,
                "auto_import_downloads_path": "",
                "auto_import_destination_root": "",
                "auto_import_create_ongoing_folders": False,
                "action_token": existing_settings.get("action_token", "")
            })

            save_settings(setup_settings)
            apply_settings(setup_settings)
            reconfigure_library_observer()
            start_auto_import_worker()
            return render_template("setup_loading.html")

    return render_template(
        "setup.html",
        settings=settings,
        error=error
    )

def update_setup_sync_state(**changes):
    with SETUP_SYNC_STATE_LOCK:
        SETUP_SYNC_STATE.update(changes)

def get_setup_sync_state():
    with SETUP_SYNC_STATE_LOCK:
        return dict(SETUP_SYNC_STATE)

def run_setup_metadata_job(anime_count):
    try:
        def report_progress(stage, current, total):
            update_setup_sync_state(
                stage=stage,
                current=current,
                total=total
            )

        sync_all_library(
            trigger_label="Setup metadata sync",
            enrich_metadata=True,
            progress_callback=report_progress
        )
        update_setup_sync_state(
            running=False,
            done=True,
            stage="complete",
            current=anime_count,
            total=anime_count,
            anime_count=anime_count
        )
    except Exception as e:
        app_log(f"Setup sync failed: {e}", "ERROR")
        update_setup_sync_state(
            running=False,
            done=True,
            stage="error",
            error="Setup sync failed. Check the server log and try again."
        )

@app.route("/setup/sync", methods=["GET", "POST"])
@host_only
@require_action_token
def setup_sync():
    if request.method == "POST":
        state = get_setup_sync_state()
        if not state["running"]:
            update_setup_sync_state(
                running=True,
                done=False,
                error="",
                stage="scan",
                current=0,
                total=0,
                anime_count=0
            )
            try:
                sync_result = sync_all_library(
                    "Setup local library sync",
                    enrich_metadata=False,
                    progress_callback=lambda stage, current, total: update_setup_sync_state(
                        stage=stage,
                        current=current,
                        total=total
                    )
                )
                if sync_result.get("reason") == "sync_in_progress":
                    update_setup_sync_state(running=False)
                    return jsonify({
                        "ok": False,
                        "error": "sync_in_progress",
                        "message": sync_result.get("message")
                    }), 409

                with db_connection() as conn:
                    anime_count = conn.execute("SELECT COUNT(*) FROM anime_library").fetchone()[0]
                reconfigure_library_observer()
                update_setup_sync_state(
                    stage="metadata",
                    current=0,
                    total=anime_count,
                    anime_count=anime_count
                )
                threading.Thread(
                    target=run_setup_metadata_job,
                    args=(anime_count,),
                    name="setup-metadata-sync",
                    daemon=True
                ).start()
            except Exception as e:
                app_log(f"Setup sync failed: {e}", "ERROR")
                update_setup_sync_state(running=False, done=True, stage="error")
                return json_error(
                    "setup_sync_failed",
                    "Setup sync failed. Check the server log and try again.",
                    500
                )

    state = get_setup_sync_state()
    return jsonify({"ok": not bool(state["error"]), **state})

@app.route("/settings")
@host_only
def settings_page():
    settings = load_settings()
    media_diagnostics = get_media_dependency_diagnostics(
        settings,
        force=request.args.get("diagnostics_refreshed") == "1"
    )
    anime_folders = get_anime_folder_index()
    auto_import_overview = build_auto_import_overview(settings, anime_folders)
    auto_import_destination_paths = get_settings_library_paths(settings)
    auto_import_mappings = settings.get("auto_import_mappings", {})
    auto_import_mapping_rows = []
    if isinstance(auto_import_mappings, dict):
        auto_import_mapping_rows = [
            {"source": str(source), "target": str(target)}
            for source, target in auto_import_mappings.items()
            if str(source).strip() and str(target).strip()
        ]

    return render_template(
        "settings.html",
        settings=settings,
        settings_status_cards=build_settings_status_cards(settings, media_diagnostics),
        media_diagnostics=media_diagnostics,
        anime_folders=anime_folders,
        auto_import_overview=auto_import_overview,
        auto_import_destination_paths=auto_import_destination_paths,
        auto_import_mapping_rows=auto_import_mapping_rows,
        saved=request.args.get("saved") == "1",
        auto_import_scanned=request.args.get("auto_import_scanned") == "1",
        auto_import_processed=request.args.get("processed", "0"),
        auto_import_moved=request.args.get("moved", "0"),
        auto_import_unmatched_count=request.args.get("unmatched", "0"),
        auto_import_errors=request.args.get("errors", "0"),
        auto_import_resolved=request.args.get("auto_import_resolved") == "1",
        cache_cleaned=request.args.get("cache_cleaned") == "1",
        cache_removed_files=request.args.get("removed_files", "0"),
        cache_removed_dirs=request.args.get("removed_dirs", "0"),
        cache_removed_watch_entries=request.args.get("removed_watch_entries", "0"),
        cache_skipped=request.args.get("skipped", "0"),
        sync_busy=request.args.get("sync_busy") == "1",
        diagnostics_refreshed=request.args.get("diagnostics_refreshed") == "1"
    )

@app.route("/settings/media-diagnostics/check", methods=["POST"])
@host_only
@require_action_token
def refresh_media_diagnostics_settings():
    get_media_dependency_diagnostics(load_settings(), force=True)
    return redirect("/settings?diagnostics_refreshed=1")

@app.route("/settings", methods=["POST"])
@host_only
@require_action_token
def update_settings():
    existing_settings = load_settings()
    mapping_sources = request.form.getlist("auto_import_mapping_source")
    mapping_targets = request.form.getlist("auto_import_mapping_target")
    auto_import_mappings = build_auto_import_mappings_from_pairs(
        mapping_sources,
        mapping_targets
    )

    library_paths = normalize_library_paths(request.form.getlist("library_paths"))

    settings = {
        "setup_completed": True,
        "library_paths": library_paths,
        "watchlist_path": library_paths[0] if library_paths else "",
        "ongoing_path": library_paths[1] if len(library_paths) > 1 else "",
        "movie_path": request.form.get("movie_path", "").strip(),
        "vlc_path": request.form.get("vlc_path", "").strip(),
        "discord_rpc_enabled": "discord_rpc_enabled" in request.form,
        "discord_client_id": request.form.get("discord_client_id", "").strip(),
        "lan_access_enabled": "lan_access_enabled" in request.form,
        "theme_preset": request.form.get("theme_preset", "dark-blue").strip(),
        "auto_import_enabled": "auto_import_enabled" in request.form,
        "auto_import_downloads_path": request.form.get("auto_import_downloads_path", "").strip(),
        "auto_import_destination_root": request.form.get("auto_import_destination_root", "").strip(),
        "auto_import_interval_seconds": normalize_int_setting(
            request.form.get("auto_import_interval_seconds"),
            15,
            5,
            3600
        ),
        "auto_import_stable_seconds": normalize_int_setting(
            request.form.get("auto_import_stable_seconds"),
            60,
            10,
            86400
        ),
        "auto_import_create_ongoing_folders": "auto_import_create_ongoing_folders" in request.form,
        "auto_import_mappings": auto_import_mappings,
        "auto_import_recent_imports": existing_settings.get("auto_import_recent_imports", []),
        "auto_import_unmatched": existing_settings.get("auto_import_unmatched", []),
        "action_token": existing_settings.get("action_token", "")
    }

    if settings["theme_preset"] not in THEME_PRESETS:
        settings["theme_preset"] = "dark-blue"

    save_settings(settings)
    apply_settings(settings)
    reconfigure_library_observer()
    start_auto_import_worker()
    sync_busy = LIBRARY_SYNC_LOCK.locked()
    if sync_busy:
        app_log("Settings save sync skipped because another library sync is already running.", "WARN")
    else:
        threading.Thread(
            target=sync_all_library,
            kwargs={"trigger_label": "Settings save sync"},
            name="settings-sync",
            daemon=True
        ).start()

    if sync_busy:
        return redirect("/settings?saved=1&sync_busy=1")

    return redirect("/settings?saved=1")

@app.route("/settings/auto-import/scan", methods=["POST"])
@host_only
@require_action_token
def scan_auto_import_settings():
    summary = auto_import_scan_once(force_enabled=True)

    return redirect(
        "/settings?auto_import_scanned=1"
        f"&processed={summary['processed']}"
        f"&moved={summary['moved']}"
        f"&unmatched={summary['unmatched']}"
        f"&errors={summary['errors']}"
    )

@app.route("/settings/auto-import/resolve", methods=["POST"])
@host_only
@require_action_token
def resolve_auto_import_settings():
    source_path = request.form.get("source_path", "").strip()
    target_anime = request.form.get("target_anime", "").strip()
    add_mapping = "add_mapping" in request.form
    settings = load_settings()

    target_path = find_anime_path(target_anime)
    if (
        not target_path
        or not is_valid_auto_import_resolve_source(source_path, settings)
    ):
        app_log(f"Auto import manual resolve rejected unsafe source: {source_path}", "WARN")
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return json_error(
                "invalid_auto_import_source",
                "Auto import source or target is not valid.",
                422
            )
        return redirect("/settings?auto_import_scanned=1&processed=0&moved=0&unmatched=0&errors=1")

    source_path = os.path.realpath(os.path.abspath(source_path))
    clean_title = normalize_title_for_match(os.path.basename(source_path))

    try:
        move_auto_import_file(source_path, target_path, clean_title, 1)
        if add_mapping and clean_title:
            with AUTO_IMPORT_STATE_LOCK:
                settings = load_settings()
                mappings = settings.setdefault("auto_import_mappings", {})
                mappings[clean_title] = target_anime
                save_settings(settings)
        return redirect("/settings?auto_import_resolved=1")
    except OSError as e:
        record_auto_import_unmatched(source_path, clean_title, str(e))
        app_log(f"Auto import manual resolve failed: {e}", "ERROR")
        return redirect("/settings?auto_import_scanned=1&processed=0&moved=0&unmatched=0&errors=1")

@app.route("/settings/cleanup-cache", methods=["POST"])
@host_only
@require_action_token
def cleanup_cache_settings():
    summary = cleanup_orphan_cache()

    return redirect(
        "/settings?cache_cleaned=1"
        f"&removed_files={summary['removed_files']}"
        f"&removed_dirs={summary['removed_dirs']}"
        f"&removed_watch_entries={summary['removed_watch_entries']}"
        f"&skipped={summary['skipped']}"
    )

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

@app.route("/settings/pick-folder")
@host_only
def pick_settings_folder():
    return pick_windows_path("folder")

@app.route("/settings/pick-file")
@host_only
def pick_settings_file():
    return pick_windows_path("file")

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
                    and "next_airing" not in metadata
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
    movie_path = MOVIE_PATH
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
                "score": movie_info.get("score") if movie_info else None,
                "year": movie_info.get("year") if movie_info else None,
                "description": movie_info.get("description") if movie_info else None,
                "watch_progress_label": watch_progress_label,
                "watch_status_label": watch_status_label,
                "watch_status_kind": watch_status_kind
            })

    movies.sort(key=lambda item: item["title"].casefold())
    return movies

def normalize_studio_name(studio_name):
    return " ".join((studio_name or "").casefold().split())

def normalize_anime_match_name(value):
    normalized = (value or "").casefold()
    normalized = re.sub(r"[\s:;,_'\"`´‘’“”()\[\]{}.!?\\/|+-]+", " ", normalized)
    return " ".join(normalized.split())

def get_cached_metadata_only(anime_name):
    cache_file = os.path.join(
        METADATA_CACHE,
        f"{anime_name}.json"
    )

    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None

def get_studio_project_cache_file(studio_name):
    return os.path.join(
        METADATA_CACHE,
        f"studio_projects_{safe_cache_name(studio_name)}.json"
    )

def read_studio_project_cache(studio_name, allow_stale=False):
    cache_file = get_studio_project_cache_file(studio_name)

    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(payload, dict):
        return None

    fetched_at = payload.get("fetched_at", 0)
    is_fresh = (time.time() - fetched_at) < STUDIO_PROJECT_CACHE_TTL_SECONDS

    if allow_stale or is_fresh:
        return payload.get("data")

    return None

def write_studio_project_cache(studio_name, data):
    os.makedirs(METADATA_CACHE, exist_ok=True)
    cache_file = get_studio_project_cache_file(studio_name)

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "fetched_at": time.time(),
                    "data": data
                },
                f,
                ensure_ascii=False,
                indent=4
            )
    except OSError as e:
        app_log(f"Studio project cache write failed for {studio_name}: {e}", "WARN")

def fetch_anilist_studio_projects(studio_name, max_projects=STUDIO_PROJECT_LIMIT):
    cached = read_studio_project_cache(studio_name)
    if cached:
        return cached, None

    query = """
    query ($search: String, $page: Int, $perPage: Int) {
      Studio(search: $search) {
        id
        name
        isAnimationStudio
        media(isMain: true, sort: POPULARITY_DESC, page: $page, perPage: $perPage) {
          pageInfo {
            total
            currentPage
            lastPage
            hasNextPage
            perPage
          }
          nodes {
            id
            title {
              romaji
              english
              native
            }
            coverImage {
              extraLarge
            }
            format
            status
            season
            seasonYear
            episodes
            averageScore
            popularity
            description
          }
        }
      }
    }
    """

    projects = []
    studio_info = None
    page = 1
    per_page = min(50, max_projects)

    try:
        while len(projects) < max_projects:
            response = requests.post(
                "https://graphql.anilist.co",
                json={
                    "query": query,
                    "variables": {
                        "search": studio_name,
                        "page": page,
                        "perPage": min(per_page, max_projects - len(projects))
                    }
                },
                timeout=15
            )

            try:
                data = response.json()
            except ValueError:
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "AniList returned an invalid studio response."

            if response.status_code >= 400:
                app_log(f"Studio AniList HTTP {response.status_code}: {data}", "ERROR")
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, f"AniList studio request failed with HTTP {response.status_code}."

            errors = data.get("errors")
            if errors:
                app_log(f"Studio AniList GraphQL errors: {errors}", "ERROR")
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "AniList returned GraphQL errors for the studio request."

            studio_data = data.get("data", {}).get("Studio")
            if not studio_data:
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "Studio was not found on AniList."

            if studio_info is None:
                studio_info = {
                    "id": studio_data.get("id"),
                    "name": studio_data.get("name"),
                    "isAnimationStudio": studio_data.get("isAnimationStudio")
                }

            media_data = studio_data.get("media") or {}
            page_info = media_data.get("pageInfo") or {}
            projects.extend(media_data.get("nodes") or [])

            if not page_info.get("hasNextPage") or page >= page_info.get("lastPage", page):
                break

            page += 1

        payload = {
            "studio_info": studio_info,
            "projects": projects[:max_projects]
        }
        write_studio_project_cache(studio_name, payload)
        return payload, None

    except requests.RequestException as e:
        app_log(f"Studio AniList request error: {e}", "ERROR")
        stale = read_studio_project_cache(studio_name, allow_stale=True)
        return stale, "Unable to reach AniList studio API."

def get_seiyuu_cache_file(staff_id):
    return os.path.join(SEIYUU_CACHE, f"staff_{staff_id}.json")

def read_seiyuu_cache(staff_id, allow_stale=False):
    cache_file = get_seiyuu_cache_file(staff_id)
    
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    
    if not isinstance(payload, dict):
        return None
    
    fetched_at = payload.get("fetched_at", 0)
    is_fresh = (time.time() - fetched_at) < SEIYUU_CACHE_TTL_SECONDS
    
    if allow_stale or is_fresh:
        return payload.get("data")
    
    return None

def write_seiyuu_cache(staff_id, data):
    os.makedirs(SEIYUU_CACHE, exist_ok=True)
    cache_file = get_seiyuu_cache_file(staff_id)
    
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "fetched_at": time.time(),
                    "data": data
                },
                f,
                ensure_ascii=False,
                indent=4
            )
    except OSError as e:
        app_log(f"Seiyuu cache write failed for staff {staff_id}: {e}", "WARN")

def clean_person_description(text):
    if not text:
        return None
    text = html.unescape(str(text))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def sanitize_external_url(value):
    url = str(value or "").strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url

def build_person_bio_content(text):
    """Convert the small AniList Markdown subset into safe template data."""
    cleaned = clean_person_description(text)
    if not cleaned:
        return {"paragraphs": [], "social_links": []}

    cleaned = cleaned.replace("~!", "").replace("!~", "")
    social_links = []
    social_keys = set()

    def collect_social(match):
        label = match.group(1).strip()
        url = sanitize_external_url(match.group(2))
        key = label.casefold()
        host = (urlparse(url).netloc.casefold() if url else "")
        social_name = None
        if "twitter" in key or host.endswith("twitter.com") or host.endswith("x.com"):
            social_name = "Twitter"
        elif "instagram" in key or host.endswith("instagram.com"):
            social_name = "Instagram"
        if social_name and url:
            social_key = (social_name, url)
            if social_key not in social_keys:
                social_links.append({"label": social_name, "url": url})
                social_keys.add(social_key)
            return ""
        return match.group(0)

    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", collect_social, cleaned)
    token_pattern = re.compile(
        r"\[([^\]]+)\]\(([^)]+)\)|__(.+?)__|\*\*(.+?)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)"
    )
    paragraphs = []
    for block in re.split(r"\n\s*\n|\n", cleaned):
        block = block.strip()
        if not block:
            continue
        segments = []
        cursor = 0
        for match in token_pattern.finditer(block):
            if match.start() > cursor:
                segments.append({"kind": "text", "text": block[cursor:match.start()]})
            if match.group(1) is not None:
                url = sanitize_external_url(match.group(2))
                if url:
                    segments.append({"kind": "link", "text": match.group(1), "url": url})
                else:
                    segments.append({"kind": "text", "text": match.group(1)})
            elif match.group(3) is not None or match.group(4) is not None:
                segments.append({"kind": "strong", "text": match.group(3) or match.group(4)})
            else:
                segments.append({"kind": "em", "text": match.group(5)})
            cursor = match.end()
        if cursor < len(block):
            segments.append({"kind": "text", "text": block[cursor:]})
        if any(segment.get("text", "").strip() for segment in segments):
            paragraphs.append(segments)

    return {"paragraphs": paragraphs, "social_links": social_links}

def get_nested_image(images, size="large_image_url"):
    if not isinstance(images, dict):
        return None

    for image_type in ("jpg", "webp"):
        image_data = images.get(image_type) or {}
        if image_data.get(size):
            return image_data.get(size)
        if image_data.get("image_url"):
            return image_data.get("image_url")

    return None

def build_seiyuu_role_from_anilist(edge):
    media_node = edge.get("node") or {}
    if media_node.get("type") != "ANIME":
        return None

    characters = [character for character in (edge.get("characters") or []) if character]
    character_node = characters[0] if characters else {}
    character_name = (
        (character_node.get("name") or {}).get("full")
        or edge.get("characterName")
        or "Unknown"
    )
    media_title = media_node.get("title") or {}
    role_notes = edge.get("roleNotes") or edge.get("characterRole")

    return {
        "character_id": character_node.get("id"),
        "character_name": character_name,
        "character_image": (character_node.get("image") or {}).get("large"),
        "media_id": media_node.get("id"),
        "media_title": media_title.get("english") or media_title.get("romaji") or "Unknown",
        "media_title_romaji": media_title.get("romaji"),
        "media_poster": (media_node.get("coverImage") or {}).get("extraLarge"),
        "media_type": media_node.get("type"),
        "media_status": media_node.get("status"),
        "media_year": media_node.get("seasonYear"),
        "role_notes": role_notes.replace("_", " ").title() if isinstance(role_notes, str) else role_notes,
        "source": "AniList"
    }

def build_seiyuu_role_from_jikan(voice):
    anime = voice.get("anime") or {}
    character = voice.get("character") or {}
    if not anime:
        return None

    return {
        "character_id": character.get("mal_id"),
        "character_name": character.get("name") or "Unknown",
        "character_image": get_nested_image(character.get("images")),
        "media_id": anime.get("mal_id"),
        "media_title": anime.get("title") or "Unknown",
        "media_title_romaji": anime.get("title"),
        "media_poster": get_nested_image(anime.get("images")),
        "media_type": "ANIME",
        "media_status": None,
        "media_year": None,
        "role_notes": voice.get("role"),
        "source": "MyAnimeList"
    }

def fetch_jikan_seiyuu_detail(staff_name, anilist_staff_id=None):
    if not staff_name:
        return None, "MyAnimeList fallback needs a seiyuu name."

    try:
        search_response = requests.get(
            f"{JIKAN_BASE_URL}/people",
            params={"q": staff_name, "limit": 1},
            timeout=15
        )

        try:
            search_data = search_response.json()
        except ValueError:
            return None, "MyAnimeList returned invalid seiyuu search response."

        if search_response.status_code >= 400:
            return None, f"MyAnimeList seiyuu search failed with HTTP {search_response.status_code}."

        results = search_data.get("data") or []
        if not results:
            return None, "Seiyuu was not found on MyAnimeList."

        person_id = results[0].get("mal_id")
        if not person_id:
            return None, "MyAnimeList seiyuu search did not include a person id."

        detail_response = requests.get(
            f"{JIKAN_BASE_URL}/people/{person_id}/full",
            timeout=15
        )

        try:
            detail_data = detail_response.json()
        except ValueError:
            return None, "MyAnimeList returned invalid seiyuu detail response."

        if detail_response.status_code >= 400:
            return None, f"MyAnimeList seiyuu detail failed with HTTP {detail_response.status_code}."

        person = detail_data.get("data") or results[0]
        voice_roles = []
        seen_roles = set()

        for voice in person.get("voices") or []:
            role = build_seiyuu_role_from_jikan(voice)
            if not role:
                continue
            role_key = (role.get("media_id"), role.get("character_id"), role.get("character_name"))
            if role_key in seen_roles:
                continue
            seen_roles.add(role_key)
            voice_roles.append(role)

        birthday = person.get("birthday")
        birth_date = birthday[:10] if isinstance(birthday, str) and len(birthday) >= 10 else None
        native_name = " ".join(
            part for part in [person.get("family_name"), person.get("given_name")] if part
        ) or None

        payload = {
            "staff_id": anilist_staff_id,
            "mal_id": person.get("mal_id"),
            "name_full": person.get("name") or staff_name,
            "name_native": native_name,
            "name_alternative": person.get("alternate_names") or None,
            "image": get_nested_image(person.get("images"), size="image_url"),
            "description": clean_person_description(person.get("about")),
            "date_of_birth": birth_date,
            "age": None,
            "gender": None,
            "blood_type": None,
            "home_town": None,
            "language": "Japanese",
            "site_url": person.get("url"),
            "voice_roles": voice_roles,
            "source": "MyAnimeList"
        }

        return payload, None

    except requests.RequestException as e:
        app_log(f"Seiyuu MyAnimeList request error for {staff_name}: {e}", "ERROR")
        return None, "Unable to reach MyAnimeList seiyuu API."

def fetch_anilist_seiyuu_detail(staff_id, fallback_name=None):
    """Fetch seiyuu profile and voice roles from AniList"""
    cached = read_seiyuu_cache(staff_id)
    if cached:
        return cached, None
    
    query = """
    query ($id: Int, $page: Int) {
      Staff(id: $id) {
        id
        name {
          full
          native
        }
        image {
          large
        }
        description
        dateOfBirth {
          year
          month
          day
        }
        age
        gender
        bloodType
        homeTown
        language
        siteUrl
        characterMedia(page: $page, perPage: 50, sort: [POPULARITY_DESC]) {
          pageInfo {
            total
            currentPage
            lastPage
          }
          edges {
            characterRole
            characterName
            roleNotes
            characters {
              id
              name {
                full
              }
              image {
                large
              }
            }
            node {
              id
              title {
                english
                romaji
              }
              coverImage {
                extraLarge
              }
              type
              status
              seasonYear
            }
          }
        }
      }
    }
    """
    
    try:
        staff_data = None
        voice_roles = []
        page = 1
        last_page = 1

        while page <= min(last_page, SEIYUU_ROLE_PAGE_LIMIT):
            response = requests.post(
                "https://graphql.anilist.co",
                json={
                    "query": query,
                    "variables": {
                        "id": staff_id,
                        "page": page
                    }
                },
                timeout=15
            )
        
            try:
                data = response.json()
            except ValueError:
                stale = read_seiyuu_cache(staff_id, allow_stale=True)
                if stale:
                    return stale, "AniList returned invalid seiyuu response."
                mal_payload, mal_error = fetch_jikan_seiyuu_detail(fallback_name, staff_id)
                return mal_payload, mal_error or "AniList returned invalid seiyuu response."
            
            if response.status_code >= 400:
                app_log(f"Seiyuu AniList HTTP {response.status_code}: {data}", "ERROR")
                stale = read_seiyuu_cache(staff_id, allow_stale=True)
                if stale:
                    return stale, f"AniList seiyuu request failed with HTTP {response.status_code}."
                mal_payload, mal_error = fetch_jikan_seiyuu_detail(fallback_name, staff_id)
                return mal_payload, mal_error or f"AniList seiyuu request failed with HTTP {response.status_code}."
            
            errors = data.get("errors")
            if errors:
                app_log(f"Seiyuu AniList GraphQL errors: {errors}", "ERROR")
                stale = read_seiyuu_cache(staff_id, allow_stale=True)
                if stale:
                    return stale, "AniList returned GraphQL errors for seiyuu request."
                mal_payload, mal_error = fetch_jikan_seiyuu_detail(fallback_name, staff_id)
                return mal_payload, mal_error or "AniList returned GraphQL errors for seiyuu request."
            
            page_staff_data = data.get("data", {}).get("Staff")
            if not page_staff_data:
                stale = read_seiyuu_cache(staff_id, allow_stale=True)
                if stale:
                    return stale, "Seiyuu was not found on AniList."
                mal_payload, mal_error = fetch_jikan_seiyuu_detail(fallback_name, staff_id)
                return mal_payload, mal_error or "Seiyuu was not found on AniList."

            if staff_data is None:
                staff_data = page_staff_data

            character_media_data = page_staff_data.get("characterMedia") or {}
            page_info = character_media_data.get("pageInfo") or {}
            last_page = page_info.get("lastPage") or 1

            for edge in character_media_data.get("edges") or []:
                role = build_seiyuu_role_from_anilist(edge)
                if role:
                    voice_roles.append(role)

            page += 1

        if not staff_data:
            stale = read_seiyuu_cache(staff_id, allow_stale=True)
            if stale:
                return stale, "Seiyuu was not found on AniList."
            mal_payload, mal_error = fetch_jikan_seiyuu_detail(fallback_name, staff_id)
            return mal_payload, mal_error or "Seiyuu was not found on AniList."

        if not voice_roles:
            anilist_name = (staff_data.get("name") or {}).get("full") or fallback_name
            mal_payload, mal_error = fetch_jikan_seiyuu_detail(anilist_name, staff_id)
            if mal_payload and mal_payload.get("voice_roles"):
                write_seiyuu_cache(staff_id, mal_payload)
                return mal_payload, "AniList did not return voice roles, using MyAnimeList fallback."
        
        # Get date of birth info
        dob = staff_data.get("dateOfBirth", {})
        birth_date = None
        if dob:
            year = dob.get("year")
            month = dob.get("month")
            day = dob.get("day")
            if year and month and day:
                birth_date = f"{year}-{month:02d}-{day:02d}"
        
        payload = {
            "staff_id": staff_data.get("id"),
            "name_full": staff_data.get("name", {}).get("full"),
            "name_native": staff_data.get("name", {}).get("native"),
            "name_alternative": None,
            "image": staff_data.get("image", {}).get("large"),
            "description": clean_person_description(staff_data.get("description")),
            "date_of_birth": birth_date,
            "age": staff_data.get("age"),
            "gender": staff_data.get("gender"),
            "blood_type": staff_data.get("bloodType"),
            "home_town": staff_data.get("homeTown"),
            "language": staff_data.get("language"),
            "site_url": staff_data.get("siteUrl"),
            "voice_roles": voice_roles,
            "source": "AniList"
        }
        
        write_seiyuu_cache(staff_id, payload)
        return payload, None
    
    except requests.RequestException as e:
        app_log(f"Seiyuu AniList request error: {e}", "ERROR")
        stale = read_seiyuu_cache(staff_id, allow_stale=True)
        if stale:
            return stale, "Unable to reach AniList seiyuu API."
        mal_payload, mal_error = fetch_jikan_seiyuu_detail(fallback_name, staff_id)
        if mal_payload:
            write_seiyuu_cache(staff_id, mal_payload)
            return mal_payload, "Unable to reach AniList seiyuu API, using MyAnimeList fallback."
        return None, mal_error or "Unable to reach AniList seiyuu API."

def build_local_anime_match_index(local_anime):
    match_index = {}

    for anime in local_anime:
        anime_name = anime.get("name")
        if not anime_name:
            continue

        names = {anime_name}
        info = get_cached_metadata_only(anime_name) or {}
        cached_title = info.get("title")
        if cached_title:
            names.add(cached_title)

        for name in names:
            normalized = normalize_anime_match_name(name)
            if normalized:
                match_index.setdefault(normalized, anime)

    return match_index

def get_project_title_options(project):
    title = project.get("title") or {}
    return [
        title.get("english"),
        title.get("romaji"),
        title.get("native")
    ]

def build_studio_project_item(project, local_match=None):
    title_options = get_project_title_options(project)
    display_title = next((title for title in title_options if title), "Untitled")
    title = project.get("title") or {}

    item = {
        "id": project.get("id"),
        "name": display_title,
        "title_romaji": title.get("romaji"),
        "title_english": title.get("english"),
        "title_native": title.get("native"),
        "poster": (project.get("coverImage") or {}).get("extraLarge") or url_for("static", filename="arcana.jpg"),
        "format": project.get("format"),
        "status": project.get("status"),
        "season": project.get("season"),
        "year": project.get("seasonYear"),
        "episodes": project.get("episodes"),
        "score": project.get("averageScore"),
        "popularity": project.get("popularity"),
        "description": project.get("description"),
        "in_library": local_match is not None,
        "local_anime_name": local_match.get("name") if local_match else None,
        "local_detail_url": url_for("anime_detail", anime_name=local_match.get("name")) if local_match else None
    }

    if item["in_library"]:
        item["name"] = item["local_anime_name"]
        item["poster"] = url_for("poster", anime_name=item["local_anime_name"])
        item["score"] = local_match.get("score") or item["score"]
        item["year"] = local_match.get("year") or item["year"]
        item["season"] = local_match.get("season") or item["season"]
        item["episodes"] = local_match.get("episodes") or item["episodes"]
        item["status"] = local_match.get("status") or item["status"]

    return item

def build_local_studio_fallback_projects(studio_name, local_anime, allow_fetch_missing=True):
    requested_studio = normalize_studio_name(studio_name)
    library_projects = []

    for anime in local_anime:
        anime_name = anime.get("name")
        if not anime_name:
            continue

        info = get_cached_metadata_only(anime_name)
        if info is None and allow_fetch_missing:
            info = get_cached_anilist_info(anime_name)

        anime_studio = (info or {}).get("studio")
        if normalize_studio_name(anime_studio) != requested_studio:
            continue

        library_projects.append({
            "id": None,
            "name": anime_name,
            "title_romaji": None,
            "title_english": anime_name,
            "title_native": None,
            "poster": url_for("poster", anime_name=anime_name),
            "format": (info or {}).get("format"),
            "status": anime.get("status"),
            "season": anime.get("season"),
            "year": anime.get("year"),
            "episodes": anime.get("episodes"),
            "score": anime.get("score"),
            "popularity": None,
            "description": (info or {}).get("description"),
            "in_library": True,
            "local_anime_name": anime_name,
            "local_detail_url": url_for("anime_detail", anime_name=anime_name)
        })

    return library_projects

@app.route("/about")
def about():
    return render_template("project-overview.html")

@app.route("/schedule")
def schedule():
    local_tz = datetime.now().astimezone().tzinfo
    now_dt = datetime.now(local_tz)
    now_ts = int(now_dt.timestamp())
    timezone_offset_minutes = int(now_dt.utcoffset().total_seconds() // 60)
    schedule_scope = request.args.get("scope", "all").strip().lower()
    if schedule_scope not in {"all", "library"}:
        schedule_scope = "all"

    airing_list, schedule_error = get_cached_airing_schedule()
    all_processed = build_schedule_items(airing_list, local_tz, now_ts)
    library_processed = [
        item for item in all_processed
        if item.get("is_in_library")
    ]
    processed = library_processed if schedule_scope == "library" else all_processed

    current_items = [
        item for item in processed
        if item["airing_at"] <= now_ts < item["airing_at"] + 1800
    ]
    upcoming_items = [
        item for item in processed
        if item["airing_at"] > now_ts
    ]
    schedule_focus = None

    if current_items:
        schedule_focus = dict(current_items[0])
        schedule_focus["focus_mode"] = "live"
        schedule_focus["focus_badge"] = "LIVE NOW"
        schedule_focus["focus_status"] = "Airing now"
        schedule_focus["more_count"] = max(0, len(current_items) - 1)
    elif upcoming_items:
        schedule_focus = dict(upcoming_items[0])
        schedule_focus["focus_mode"] = "next"
        schedule_focus["focus_badge"] = "NEXT UP"
        schedule_focus["focus_status"] = "Upcoming"
        schedule_focus["more_count"] = 0
    
    schedule_start = now_dt.strftime("%A, %d %B %Y")
    schedule_end = (now_dt + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS - 1)).strftime("%A, %d %B %Y")

    return render_template(
        "schedule.html",
        schedule=all_processed,
        schedule_focus=schedule_focus,
        schedule_error=schedule_error,
        now_ts=now_ts,
        now_iso=now_dt.isoformat(),
        timezone_offset_minutes=timezone_offset_minutes,
        today=schedule_start,
        schedule_range=f"{schedule_start} - {schedule_end}",
        schedule_lookahead_days=SCHEDULE_LOOKAHEAD_DAYS,
        schedule_scope=schedule_scope,
        schedule_all_count=len(all_processed),
        schedule_library_count=len(library_processed),
        schedule_visible_count=len(processed)
    )

@app.route("/api/schedule-alerts")
def schedule_alerts():
    local_tz = datetime.now().astimezone().tzinfo
    now_dt = datetime.now(local_tz)
    now_ts = int(now_dt.timestamp())
    timezone_offset_minutes = int(now_dt.utcoffset().total_seconds() // 60)
    airing_list, schedule_error = get_cached_airing_schedule()

    if schedule_error:
        return jsonify({
            "ok": False,
            "error": schedule_error,
            "items": [],
            "badge_count": 0,
            "summary": "Schedule unavailable",
            "now_iso": now_dt.isoformat(),
            "timezone_offset_minutes": timezone_offset_minutes
        }), 502

    today_key = now_dt.strftime("%Y-%m-%d")
    processed = [
        item for item in build_schedule_items(airing_list, local_tz, now_ts)
        if item["date_key"] == today_key
    ]
    payload = get_schedule_alert_payload(
        processed,
        now_ts,
        now_dt.isoformat(),
        timezone_offset_minutes
    )
    payload["ok"] = True
    return jsonify(payload)

@app.route("/studio/<path:studio_name>")
def studio_page(studio_name):
    return_to = request.args.get("return_to", "").strip()
    parsed_return_to = urlparse(return_to)
    if (
        parsed_return_to.scheme
        or parsed_return_to.netloc
        or not parsed_return_to.path.startswith("/anime/")
    ):
        return_to = url_for("index")

    local_anime = get_anime()
    local_match_index = build_local_anime_match_index(local_anime)
    studio_payload, studio_error = fetch_anilist_studio_projects(studio_name)
    studio_info = None
    studio_projects = []
    fallback_used = False

    if studio_payload:
        studio_info = studio_payload.get("studio_info")

        for project in studio_payload.get("projects", []):
            local_match = None

            for title in get_project_title_options(project):
                normalized_title = normalize_anime_match_name(title)
                if normalized_title and normalized_title in local_match_index:
                    local_match = local_match_index[normalized_title]
                    break

            studio_projects.append(
                build_studio_project_item(
                    project,
                    local_match
                )
            )

    if not studio_payload:
        fallback_used = True
        studio_projects = build_local_studio_fallback_projects(
            studio_name,
            local_anime,
            allow_fetch_missing=True
        )

    library_projects = [
        project for project in studio_projects
        if project.get("in_library")
    ]

    # Compute additional profile statistics
    from collections import Counter
    years = [p.get("year") for p in studio_projects if p.get("year") and p.get("year") != 0]
    earliest_year = min(years) if years else None
    newest_year = max(years) if years else None

    formats = [p.get("format") for p in studio_projects if p.get("format")]
    format_counts = Counter(formats)
    clean_doms = []
    for fmt, count in format_counts.most_common(2):
        fmt_upper = fmt.upper()
        if fmt_upper in ("TV", "OVA", "ONA"):
            clean_doms.append(fmt_upper)
        else:
            clean_doms.append(fmt_upper.title())
    dominant_formats_str = ", ".join(clean_doms) if clean_doms else "N/A"

    # Group projects by year
    by_year = {}
    for p in studio_projects:
        year = p.get("year")
        y_key = year if (year and year != 0) else 0
        by_year.setdefault(y_key, []).append(p)

    # Sort years descending, keeping TBA (0) at the end
    sorted_years = sorted([yk for yk in by_year.keys() if yk > 0], reverse=True)
    if 0 in by_year:
        sorted_years.append(0)

    projects_by_year = [(yk, by_year[yk]) for yk in sorted_years]

    return render_template(
        "studio.html",
        studio_name=studio_name,
        studio_info=studio_info,
        studio_projects=studio_projects,
        projects_by_year=projects_by_year,
        library_projects=library_projects,
        total_projects=len(studio_projects),
        total_in_library=len(library_projects),
        studio_error=studio_error,
        fallback_used=fallback_used,
        studio_anime=library_projects,
        total_anime=len(library_projects),
        earliest_year=earliest_year,
        newest_year=newest_year,
        dominant_formats=dominant_formats_str,
        studio_return_url=return_to,
        studio_return_label="Back to Anime" if return_to != url_for("index") else "Back to Library"
    )

@app.route("/seiyuu/<int:staff_id>")
def seiyuu_page(staff_id):
    """Display seiyuu/voice actor profile and voice roles"""
    return_to = request.args.get("return_to", "").strip()
    parsed_return_to = urlparse(return_to)
    if (
        parsed_return_to.scheme
        or parsed_return_to.netloc
        or not parsed_return_to.path.startswith("/anime/")
    ):
        return_to = url_for("index")
    return_label = "Back to Anime" if return_to != url_for("index") else "Back to Library"

    local_anime = get_anime()
    local_match_index = build_local_anime_match_index(local_anime)
    fallback_name = request.args.get("name", "").strip() or None
    
    seiyuu_data, seiyuu_error = fetch_anilist_seiyuu_detail(staff_id, fallback_name=fallback_name)
    
    if not seiyuu_data:
        return render_template(
            "seiyuu.html",
            seiyuu_data=None,
            seiyuu_error=seiyuu_error or "Seiyuu not found",
            voice_roles=[],
            library_roles=[],
            seiyuu_return_url=return_to,
            seiyuu_return_label=return_label
        ), 404
    
    # Process voice roles and check if in library
    all_roles = [dict(role) for role in (seiyuu_data.get("voice_roles") or []) if isinstance(role, dict)]
    library_roles = []
    
    for role in all_roles:
        media_title = role.get("media_title", "")
        for title in [media_title, role.get("media_title_romaji", "")]:
            if title:
                normalized_title = normalize_anime_match_name(title)
                if normalized_title and normalized_title in local_match_index:
                    local_match = local_match_index[normalized_title]
                    role["in_library"] = True
                    role["local_anime_name"] = local_match.get("name")
                    role["local_detail_url"] = url_for("anime_detail", anime_name=local_match.get("name"))
                    library_roles.append(role)
                    break

    other_roles = [role for role in all_roles if not role.get("in_library")]
    bio_content = build_person_bio_content(seiyuu_data.get("description"))
    profile_url = sanitize_external_url(seiyuu_data.get("site_url"))
    social_links = list(bio_content["social_links"])
    if profile_url:
        social_links.append({"label": "AniList Profile" if seiyuu_data.get("source") == "AniList" else "Profile", "url": profile_url})

    backdrop_image = next(
        (role.get("media_poster") for role in library_roles + other_roles if role.get("media_poster")),
        None
    )
    
    return render_template(
        "seiyuu.html",
        seiyuu_data=seiyuu_data,
        seiyuu_error=seiyuu_error,
        voice_roles=all_roles,
        library_roles=library_roles,
        other_roles=other_roles,
        bio_paragraphs=bio_content["paragraphs"],
        social_links=social_links,
        backdrop_image=backdrop_image,
        seiyuu_return_url=return_to,
        seiyuu_return_label=return_label
    )

@app.route("/movies")
def movies():
    movies = get_movies()

    return render_template(
        "movies.html",
        movies=movies
    )

@app.route("/movie/<path:filename>")
def movie_detail_page(filename):

    video_path = safe_join_media_path(
        MOVIE_PATH,
        filename
    )

    if (
        not video_path
        or
        not os.path.isfile(video_path)
        or
        not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return "Movie not found", 404

    clean_title = clean_movie_title(filename)
    anime_info = get_cached_anilist_info(clean_title)

    # Ambil durasi dan resolusi
    episode_info = get_episode_cache("Movies", video_path)

    # Buat list episode buatan (hanya 1 item)
    episodes = [{
        "file": filename,
        "episode": 1,
        "thumbnail": internal_url_for("thumbnail", anime_name="Movies", episode=filename),
        "duration": episode_info["duration"],
        "resolution": episode_info["resolution"]
    }]

    history = load_history_data()
    watch_history_key = get_watch_history_key("Movies", filename)
    h_data = history.get(watch_history_key)
    if not h_data:
        legacy_data = history.get("Movies")
        if legacy_data and legacy_data.get("episode") == filename:
            h_data = legacy_data

    resume_time = 0
    if h_data and h_data.get("episode") == filename:
        resume_time = h_data.get("last_seconds", 0)

    return render_template(
        "anime.html",
        anime_name=clean_title,      # Digunakan untuk metadata/poster
        folder_name="Movies",       # Digunakan untuk mencari file di disk
        episodes=episodes,
        anime_info=anime_info,
        is_movie=True,
        resume_time=resume_time
    )

@app.route("/")
def index():

    status_filter = request.args.get(
        "status",
        "ALL"
    )

    anime_list = get_anime()
    home_movies = get_movies()

    all_count = len(anime_list) + len(home_movies)
    library_is_empty = all_count == 0

    releasing_count = len([
        anime
        for anime in anime_list
        if anime.get("status") == "RELEASING"
    ])

    finished_count = len([
        anime
        for anime in anime_list
        if anime.get("status") == "FINISHED"
    ])

    featured_slides = []
    if anime_list:
        # Ambil maksimal 5 anime acak untuk slider
        slider_candidates = random.sample(
            anime_list, 
            min(len(anime_list), 5)
        )
        for item in slider_candidates:
            info = get_cached_anilist_info(item["name"])
            featured_slides.append({
                "anime": item,
                "info": info
            })

    # Helper function to get history could be used here
    history = load_history_data()
    
    continue_watching = []
    for history_key, data in history.items():
        if not isinstance(data, dict):
            continue

        episode = data.get("episode")
        if not episode:
            continue

        is_movie = (
            history_key.startswith(MOVIE_HISTORY_PREFIX)
            or history_key == "Movies"
            or data.get("media_name") == "Movies"
        )

        media_name = "Movies" if is_movie else data.get("media_name", history_key)
        display_name = (
            data.get("display_name")
            or get_watch_history_display_name(media_name, episode)
        )

        continue_watching.append({
            "history_key": history_key,
            "name": media_name,
            "display_name": display_name,
            "is_movie": is_movie,
            "episode": episode,
            "episode_num": data.get("episode_num"),
            "time_str": data.get("time_str"), # Ambil time_str dari history
            "updated_at": data.get("updated_at", ""),
            "image_url": url_for(
                "thumbnail",
                anime_name="Movies",
                episode=episode
            ) if is_movie else url_for(
                "banner",
                anime_name=media_name
            ),
            "progress_percent": get_continue_progress_percent(data)
        })
    
    continue_watching.sort(key=lambda x: x["updated_at"], reverse=True)
    continue_watching = continue_watching[:6]

    return render_template(
        "index.html",
        anime_list=anime_list,
        home_movies=home_movies,
        library_is_empty=library_is_empty,
        featured_slides=featured_slides,
        continue_watching=continue_watching,
        status_filter=status_filter,

        all_count=all_count,
        releasing_count=releasing_count,
        finished_count=finished_count
    )


@app.route("/api/library/refresh", methods=["POST"])
@host_only
@require_action_token
def refresh_library():
    """Refresh the local index quickly, then enrich metadata in the background."""
    sync_result = sync_all_library(
        "Manual home library refresh",
        enrich_metadata=False
    )
    if sync_result.get("reason") == "sync_in_progress":
        return jsonify({
            "ok": False,
            "error": "sync_in_progress",
            "message": "A library refresh is already running. Try again shortly."
        }), 409

    if not sync_result.get("ok"):
        return json_error(
            "library_refresh_failed",
            "Library refresh could not be completed.",
            500
        )

    threading.Thread(
        target=sync_all_library,
        kwargs={"trigger_label": "Manual refresh metadata sync"},
        name="manual-metadata-sync",
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "anime_count": sync_result.get("anime_count", count_library_rows()),
        "message": "Library refreshed. Metadata will continue loading in the background."
    })


@app.route("/poster/<path:anime_name>")
def poster(anime_name):

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return send_file(
            poster_path
        )

    poster_path = get_anilist_poster(
        anime_name
    )

    if poster_path:
        return send_file(
            poster_path
        )

    return "", 404

@app.route("/banner/<anime_name>")
def banner(anime_name):

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    banner_path = os.path.join(
        BANNER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        banner_path
    ):
        return send_file(
            banner_path
        )

    get_anilist_poster(
        anime_name
    )

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    if os.path.exists(
        banner_path
    ):
        return send_file(
            banner_path
        )

    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return send_file(
            poster_path
        )

    return "", 404

@app.route("/thumbnail/<anime_name>/<path:episode>")
def thumbnail(
    anime_name,
    episode
):

    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return "", 404

    episode_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not episode_path
        or
        not os.path.isfile(episode_path)
        or
        not episode_path.lower().endswith(VIDEO_EXTENSIONS)
    ):

        return "", 404

    thumbnail_result = get_thumbnail_result(
        episode_path
    )

    if thumbnail_result.get("ok"):
        return send_file(
            thumbnail_result["path"]
        )

    return media_generation_error_response(thumbnail_result)

@app.route("/anime/<anime_name>")
def anime_detail(anime_name):

    anime_path = find_anime_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found", 404

    seasons = []

    for item in os.listdir(
        anime_path
    ):

        item_path = os.path.join(
            anime_path,
            item
        )

        if not os.path.isdir(item_path):
            continue

        try:
            has_video = any(
                filename.lower().endswith(VIDEO_EXTENSIONS)
                for filename in os.listdir(item_path)
            )
        except OSError:
            has_video = False

        if has_video:
            short_label = re.sub(r'^season[\s._-]*', '', item, flags=re.IGNORECASE).strip()
            seasons.append({
                "name": item,
                "label": short_label or item,
            })

    seasons.sort(key=lambda item: get_episode_sort_key(item["name"]))
    selected_season = None
    episode_source_path = anime_path

    if seasons:
        requested_season = request.args.get("season", "").strip()
        season_names = {item["name"] for item in seasons}
        selected_season = requested_season if requested_season in season_names else seasons[0]["name"]
        episode_source_path = safe_join_media_path(anime_path, selected_season)
        if not episode_source_path or not os.path.isdir(episode_source_path):
            abort(404)

    episodes = []

    video_files = []

    for file in os.listdir(
        episode_source_path
    ):

        if file.lower().endswith(
            VIDEO_EXTENSIONS
        ):

            video_files.append(
                file
            )

    video_files.sort(
        key=get_episode_sort_key
    )

    for index, file in enumerate(
        video_files,
        start=1
    ):

        episode_number = get_episode_number(
            file
        )

        episode_label = format_episode_number(
            episode_number
        ) or get_episode_display_label(
            file
        )

        video_path = os.path.join(
            episode_source_path,
            file
        )

        episode_info = get_episode_cache(
            anime_name,
            video_path,
            selected_season
        )

        relative_file = file
        if selected_season:
            relative_file = os.path.join(selected_season, file).replace(os.sep, "/")

        watch_status = get_episode_watch_status(
            anime_name,
            relative_file
        )

        episodes.append({

            "file": relative_file,

            "episode": episode_number,

            "episode_label": episode_label,

            "list_position": index,

            "thumbnail":
                internal_url_for("thumbnail", anime_name=anime_name, episode=relative_file),

            "duration":
                episode_info[
                    "duration"
                ],

            "resolution":
                episode_info[
                    "resolution"
                ],

            "watched":
                watch_status.get(
                    "watched",
                    False
                ),

            "progress":
                watch_status.get(
                    "progress",
                    0
                )

        })

    resume_episode = None
    resume_label = "Start Watching"
    all_episodes_watched = bool(episodes) and all(
        episode.get("watched", False)
        for episode in episodes
    )

    in_progress_episodes = [
        episode
        for episode in episodes
        if episode.get("progress", 0) > 0 and not episode.get("watched", False)
    ]

    if in_progress_episodes:
        resume_episode = max(
            in_progress_episodes,
            key=lambda episode: episode.get("episode", 0)
        )
        resume_label = "Resume Watching"
    else:
        history = load_history_data()
        history_data = history.get(get_watch_history_key(anime_name, ""))
        history_episode_file = history_data.get("episode") if isinstance(history_data, dict) else None
        history_episode = next(
            (
                episode
                for episode in episodes
                if episode.get("file") == history_episode_file
            ),
            None
        )

        if history_episode and not all_episodes_watched:
            resume_episode = history_episode
            resume_label = "Resume Watching"

        unwatched_episode = next(
            (
                episode
                for episode in episodes
                if not episode.get("watched", False)
            ),
            None
        )

        if not resume_episode and unwatched_episode:
            resume_episode = unwatched_episode
            resume_label = "Start Watching" if unwatched_episode.get("episode") == 1 else "Continue Watching"
        elif not resume_episode and episodes:
            resume_episode = episodes[0]
            resume_label = "Rewatch"

    anime_info = (
        get_season_anilist_info(anime_name, selected_season)
        if selected_season
        else get_cached_anilist_info(anime_name)
    )

    debug_log(f"Anime info loaded for {anime_name}: {anime_info}")

    return render_template(
        "anime.html",
        anime_name=anime_name,
        folder_name=anime_name,
        episodes=episodes,
        resume_episode=resume_episode,
        resume_label=resume_label,
        anime_info=anime_info,
        seasons=seasons,
        selected_season=selected_season
    )


@app.route(
    "/player/<anime_name>/<path:episode>"
)
def player(
    anime_name,
    episode
):

    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found", 404

    current_video_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not current_video_path
        or
        not os.path.isfile(current_video_path)
        or
        not current_video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):

        abort(404)

    episode_dir = os.path.dirname(
        current_video_path
    )

    try:

        season_name = os.path.relpath(
            episode_dir,
            anime_path
        )

    except ValueError:

        abort(404)

    if season_name == ".":

        season_name = None

    current_file = os.path.basename(
        current_video_path
    )

    if anime_name == "Movies":
        video_files = [
            current_file
        ]
    else:
        video_files = []

        for file in os.listdir(
            episode_dir
        ):

            if file.lower().endswith(
                VIDEO_EXTENSIONS
            ):

                video_files.append(
                    file
                )

    video_files.sort(
        key=get_episode_sort_key
    )

    episodes = []

    for index, file in enumerate(
        video_files,
        start=1
    ):

        video_path = os.path.join(
            episode_dir,
            file
        )

        relative_file = file

        if season_name:

            relative_file = os.path.join(
                season_name,
                file
            )

        relative_url = relative_file.replace(
            os.sep,
            "/"
        )

        episode_number = get_episode_number(
            file
        )

        episode_label = format_episode_number(
            episode_number
        ) or get_episode_display_label(
            file
        )

        episode_info = get_episode_cache(
            anime_name,
            video_path,
            season_name
        )

        watch_status = get_episode_watch_status(
            anime_name,
            relative_url
        )

        episodes.append({

            "file": relative_url,

            "episode": episode_number,

            "episode_label": episode_label,

            "list_position": index,

            "thumbnail":
                internal_url_for("thumbnail", anime_name=anime_name, episode=relative_url),

            "duration":
                episode_info[
                    "duration"
                ],

            "resolution":
                episode_info[
                    "resolution"
                ],

            "watched":
                watch_status.get(
                    "watched",
                    False
                ),

            "progress":
                watch_status.get(
                    "progress",
                    0
                )

        })

    current_index = 0

    for i, file in enumerate(
        video_files
    ):

        if file == current_file:

            current_index = i
            break

    previous_episode = None
    next_episode = None

    if current_index > 0:

        previous_episode = (
            video_files[
                current_index - 1
            ]
        )

        if season_name:

            previous_episode = os.path.join(
                season_name,
                previous_episode
            ).replace(
                os.sep,
                "/"
            )

    if current_index < len(video_files) - 1:

        next_episode = (
            video_files[
                current_index + 1
            ]
        )

        if season_name:

            next_episode = os.path.join(
                season_name,
                next_episode
            ).replace(
                os.sep,
                "/"
            )

    if anime_name == "Movies":
        back_url = url_for(
            "movie_detail_page",
            filename=episode
        )
    else:
        back_url = url_for(
            "anime_detail",
            anime_name=anime_name
        )

    if season_name and anime_name != "Movies":
        back_url = url_for(
            "anime_detail",
            anime_name=anime_name,
            season=season_name.replace(os.sep, "/")
        )

    watch_history_key = get_watch_history_key(
        anime_name,
        episode
    )
    watch_display_name = get_watch_history_display_name(
        anime_name,
        episode
    )

    # Ambil waktu tonton terakhir untuk fitur resume
    history = load_history_data()
    resume_time = 0
    h_data = history.get(watch_history_key)
    if not h_data and anime_name == "Movies":
        legacy_data = history.get("Movies")
        if legacy_data and legacy_data.get("episode") == episode:
            h_data = legacy_data

    if h_data:
        # Hanya resume jika episode yang dibuka sama dengan yang terakhir ditonton
        if h_data.get("episode") == episode:
            resume_time = h_data.get("last_seconds", 0)

    current_episode_number = get_episode_number(
        current_file
    )
    current_episode_label = format_episode_number(
        current_episode_number
    ) or get_episode_display_label(
        current_file
    )

    return render_template(

        "player.html",

        anime_name=anime_name,

        episode=episode,

        season_name=season_name,

        back_url=back_url,

        current_episode=
            current_episode_number,

        current_episode_label=
            current_episode_label,

        current_position=
            current_index + 1,

        total_episodes=
            len(video_files),

        previous_episode=
            previous_episode,

        next_episode=
            next_episode,
            
        resume_time=resume_time,

        watch_history_key=watch_history_key,

        watch_display_name=watch_display_name,

        is_movie=anime_name == "Movies",

        vlc_available=bool(
            VLC_PATH
            and
            os.path.isfile(VLC_PATH)
        ),
        
        episodes=episodes

    )

@app.route(
    "/stream/<anime_name>/<path:episode>"
)
def stream_video(
    anime_name,
    episode
):

    anime_path = find_media_path(
        anime_name
    )

    video_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not video_path
        or
        not os.path.isfile(video_path)
        or
        not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        app_log("Video path invalid or not found.", "WARN")
        debug_log(f"Invalid video path: {video_path}")
        abort(404)

    # Kembali menggunakan send_file untuk mendukung Range Requests (Seeking & Duration)
    # Kita set mimetype secara manual untuk mengelabui browser agar mencoba memutar .mkv sebagai mp4
    mime_type = "video/mp4"
    if not video_path.lower().endswith('.mkv'):
        mime_type = mimetypes.guess_type(video_path)[0] or "video/mp4"

    return send_file(
        video_path,
        mimetype=mime_type,
        conditional=True  # Penting: Mengaktifkan dukungan navigasi/seeking
    )

@app.route("/character_img/<anime_name>/<filename>")
def character_img(anime_name, filename):
    img_path = get_character_image_cache_path(anime_name, filename)
    if img_path and os.path.isfile(img_path):
        return send_file(img_path)
    abort(404)

@app.route("/subtitle/<anime_name>/<path:episode>")
def get_subtitle(anime_name, episode):
    anime_path = find_media_path(anime_name)
    if not anime_path:
        abort(404)
        
    video_path = safe_join_media_path(anime_path, episode)
    if not video_path or not os.path.isfile(video_path):
        abort(404)
        
    vtt_path = get_subtitle_vtt_path(anime_name, episode)
    
    # 1. Cek apakah cache VTT sudah ada
    if is_valid_cache_file(vtt_path):
        debug_log(f"Subtitle cache found: {vtt_path}")
        return send_file(vtt_path, mimetype="text/vtt")
        
    # 2. Jika tidak ada, buat menggunakan FFmpeg
    subtitle_result = generate_subtitle_vtt_result(video_path, vtt_path)
    if subtitle_result.get("ok"):
        return send_file(subtitle_result["path"], mimetype="text/vtt")
        
    # 3. Jika gagal/tidak ada subtitle, abaikan (404 tidak akan menghentikan video)
    return media_generation_error_response(subtitle_result)

@app.route("/anime/<anime_name>/seasons")
def season_list(anime_name):
    """Redirect the removed season index to the integrated anime detail page."""
    return redirect(url_for("anime_detail", anime_name=anime_name))

@app.route("/anime/<anime_name>/<season_name>")
def season_detail(anime_name, season_name):
    """Keep old season bookmarks working with the integrated season selector."""
    anime_path = find_anime_path(anime_name)
    if not anime_path:
        return "Anime not found", 404

    season_path = safe_join_media_path(anime_path, season_name)
    if not season_path or not os.path.isdir(season_path):
        return "Season not found", 404

    return redirect(
        url_for("anime_detail", anime_name=anime_name, season=season_name)
    )
@app.route("/play/<anime_name>/<path:episode>", methods=["POST"])
@host_only
@require_action_token
def play_episode(anime_name, episode):

    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return json_error(
            "anime_not_found",
            "Anime was not found.",
            404
        )

    episode_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not episode_path
        or
        not os.path.isfile(episode_path)
        or
        not episode_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return json_error(
            "episode_not_found",
            "Episode was not found.",
            404
        )

    vlc_diagnostics = diagnose_vlc_path(VLC_PATH)
    if vlc_diagnostics["status"] == "not_configured":
        return jsonify({
            "ok": False,
            "error": "vlc_not_configured",
            "status": "vlc_not_configured",
            "message": "Set player path in Settings first."
        }), 400

    if not vlc_diagnostics["available"]:
        return jsonify({
            "ok": False,
            "error": "vlc_not_found",
            "status": "vlc_not_found",
            "message": vlc_diagnostics["message"]
        }), 400

    try:

        player_process = popen_hidden_subprocess([
            VLC_PATH,
            episode_path
        ])

        # Update history for external player launch.
        episode_num = get_episode_number(os.path.basename(episode))
        update_watch_history(
            get_watch_history_key(anime_name, episode),
            episode,
            episode_num,
            media_name=anime_name,
            display_name=get_watch_history_display_name(anime_name, episode)
        )
        rpc_owner_id = f"vlc-{secrets.token_urlsafe(16)}"
        update_discord_rpc(anime_name, episode_num, owner_id=rpc_owner_id)
        threading.Thread(
            target=clear_discord_rpc_when_process_exits,
            args=(player_process, rpc_owner_id),
            daemon=True,
            name="discord-rpc-player-monitor",
        ).start()

        return jsonify({
            "ok": True,
            "status": "playing"
        })

    except PermissionError:

        return jsonify({
            "ok": False,
            "error": "player_permission_denied",
            "status": "error",
            "message": "Player executable cannot be started due to permissions."
        }), 500

    except OSError:

        return jsonify({
            "ok": False,
            "error": "player_launch_failed",
            "status": "error",
            "message": "Player executable could not be started."
        }), 500

@app.route("/screenshot", methods=["POST"])
@host_only
@require_action_token
def save_screenshot():
    data, error_response = get_json_body()
    if error_response:
        return error_response

    img_data = data.get("image")

    if not img_data or not isinstance(img_data, str):
        return json_error("missing_image_data", "No image data was provided.", 400)

    png_prefix = "data:image/png;base64,"
    if not img_data.startswith(png_prefix):
        return json_error("invalid_image_data", "Invalid image data.", 400)

    if len(img_data.encode("utf-8")) > MAX_SCREENSHOT_DATA_URL_BYTES:
        return json_error("payload_too_large", "Image data is too large.", 413)

    encoded = img_data[len(png_prefix):]
    if not encoded:
        return json_error("invalid_image_data", "Invalid image data.", 400)

    try:
        binary_data = base64.b64decode(encoded, validate=True)
    except Exception:
        return json_error("invalid_image_data", "Invalid image data.", 400)

    try:
        now = datetime.now()
        # Format milidetik 3 digit
        ms = str(now.microsecond // 1000).zfill(3)
        filename = now.strftime("vlcsnap-%Y-%m-%d-%Hh%Mm%Ss") + ms + ".png"
        
        # Menggunakan folder Pictures user yang sedang aktif agar lebih dinamis
        save_path = os.path.join(os.path.expanduser("~"), "Pictures")
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        
        full_path = os.path.join(save_path, filename)
        with open(full_path, "wb") as f:
            f.write(binary_data)
        
        return jsonify({"ok": True, "status": "success", "path": full_path})
    except Exception as e:
        app_log(f"Screenshot save failed: {e}", "ERROR")
        return json_error(
            "screenshot_save_failed",
            "Unable to save screenshot.",
            500
        )

@app.route("/update_progress", methods=["POST"])
@require_action_token
def update_progress():
    data, error_response = get_json_body()
    if error_response:
        return error_response

    anime_name = data.get("anime_name")
    episode = data.get("episode")
    time_str = data.get("time_str")

    if not anime_name or not episode:
        return json_error(
            "missing_progress_fields",
            "Missing anime_name or episode.",
            400
        )

    try:
        raw_episode_num = data.get("episode_num", 0)
        if raw_episode_num in (None, ""):
            episode_num = 0
        else:
            episode_num = normalize_episode_number(raw_episode_num)
            if episode_num is None:
                raise ValueError("invalid episode number")
        last_seconds = float(data.get("last_seconds", 0) or 0)
        duration = float(data.get("duration", 0) or 0)
    except (TypeError, ValueError):
        return json_error(
            "invalid_progress_data",
            "Invalid progress data.",
            400
        )
    
    update_watch_history(
        get_watch_history_key(anime_name, episode),
        episode,
        episode_num,
        time_str,
        last_seconds,
        duration,
        media_name=anime_name,
        display_name=get_watch_history_display_name(anime_name, episode)
    )
    rpc_owner_id = str(data.get("rpc_session_id", "")).strip() or None
    update_discord_rpc(anime_name, episode_num, time_str, owner_id=rpc_owner_id)
    return jsonify({"ok": True, "status": "success"})

def get_watch_status_payload():
    data, error_response = get_json_body()
    if error_response:
        return None, None, None, error_response

    anime_name = data.get("anime_name")
    episode = data.get("episode")

    if not anime_name or not episode:
        return None, None, data, None

    return anime_name, episode, data, None

@app.route("/api/watch-status/mark-watched", methods=["POST"])
@require_action_token
def api_mark_episode_watched():
    anime_name, episode, _, error_response = get_watch_status_payload()
    if error_response:
        return error_response

    if not anime_name or not episode:
        return json_error(
            "missing_watch_status_fields",
            "Missing anime_name or episode.",
            400
        )

    mark_episode_watched(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": True
    })

@app.route("/api/watch-status/mark-unwatched", methods=["POST"])
@require_action_token
def api_mark_episode_unwatched():
    anime_name, episode, _, error_response = get_watch_status_payload()
    if error_response:
        return error_response

    if not anime_name or not episode:
        return json_error(
            "missing_watch_status_fields",
            "Missing anime_name or episode.",
            400
        )

    mark_episode_unwatched(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": False
    })

@app.route("/api/watch-status/progress", methods=["POST"])
@require_action_token
def api_update_watch_status_progress():
    anime_name, episode, data, error_response = get_watch_status_payload()
    if error_response:
        return error_response

    if not anime_name or not episode:
        return json_error(
            "missing_watch_status_fields",
            "Missing anime_name or episode.",
            400
        )

    current_status = get_episode_watch_status(anime_name, episode)

    try:
        progress = float(data.get("progress", 0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_watch_status",
            "Invalid watch progress value.",
            400
        )

    try:
        duration = float(data.get("duration", 0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_watch_status",
            "Invalid watch duration value.",
            400
        )

    try:
        current_seconds = float(data.get("current_seconds", 0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_watch_status",
            "Invalid current playback time.",
            400
        )

    progress = max(0, min(100, progress))
    watched = True if progress >= 90 else bool(current_status.get("watched", False))

    status = update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": watched,
            "progress": progress,
            "duration": duration,
            "current_seconds": current_seconds
        }
    )

    return jsonify({
        "ok": True,
        "watched": status.get("watched", False),
        "progress": status.get("progress", 0)
    })

@app.route("/api/watch-history/remove", methods=["POST"])
@host_only
@require_action_token
def api_remove_watch_history_entry():
    payload = request.get_json(silent=True) or request.form or {}
    history_key = payload.get("history_key") if isinstance(payload, dict) else None

    if not history_key:
        return json_error(
            "missing_history_key",
            "Missing history_key.",
            400
        )

    removed = remove_watch_history_entry(str(history_key))
    return jsonify({"ok": removed, "removed": removed})

@app.route("/clear_rpc", methods=["POST"])
@host_only
@require_action_token
def clear_rpc_route():
    """Endpoint untuk menghapus status Discord secara manual."""
    owner_id = request.headers.get("X-AniBase-RPC-Session", "").strip() or None
    cleared = clear_discord_rpc(owner_id)
    return jsonify({"status": "success", "cleared": cleared})

@app.route('/favicon.ico')
def favicon():
    return send_file(
        os.path.join(app.root_path, 'static', 'arcana.jpg'),
        mimetype='image/jpeg'
    )

def load_history_data():
    return load_watch_json_file(WATCH_HISTORY_FILE, "Watch history")

def run_debounced_anime_sync(anime_name):
    if is_shutdown_requested():
        return

    try:
        sync_anime_to_db(anime_name, trigger_label=f"Watchdog sync for {anime_name}")
        debug_log(f"Watchdog sync completed: {anime_name}")
    except Exception as e:
        app_log(f"Watchdog sync failed: {anime_name}: {e}", "ERROR")
    finally:
        with LIBRARY_SYNC_DEBOUNCE_LOCK:
            LIBRARY_SYNC_TIMERS.pop(anime_name, None)

def queue_anime_sync(anime_name):
    if not anime_name or is_shutdown_requested():
        return

    with LIBRARY_SYNC_DEBOUNCE_LOCK:
        existing_timer = LIBRARY_SYNC_TIMERS.get(anime_name)
        if existing_timer is not None:
            existing_timer.cancel()
            should_log_queue = False
        else:
            should_log_queue = True

        timer = threading.Timer(
            LIBRARY_SYNC_DEBOUNCE_SECONDS,
            run_debounced_anime_sync,
            args=(anime_name,)
        )
        timer.daemon = True
        LIBRARY_SYNC_TIMERS[anime_name] = timer
        timer.start()

    if should_log_queue:
        debug_log(f"Watchdog sync queued: {anime_name}")

class LibraryHandler(FileSystemEventHandler):
    def process_event(self, event_path):
        if is_shutdown_requested():
            return

        event_path = os.path.abspath(event_path)

        movie_path = normalize_library_path(globals().get("MOVIE_PATH", ""))
        if movie_path:
            try:
                if os.path.commonpath([movie_path, event_path]) == movie_path:
                    return
            except ValueError:
                pass

        for base in get_valid_anime_paths():
            try:
                if os.path.commonpath([base, event_path]) != base:
                    continue

                relative = os.path.relpath(event_path, base)
                parts = relative.split(os.sep)
                if parts and parts[0] != "." and parts[0] != "":
                    queue_anime_sync(parts[0])
                    return # Found the anime, no need to check other base paths
            except ValueError: # path is not in base
                continue
        # If no specific anime was found, or if it was a top-level directory change/deletion
        debug_log(f"Watchdog event for {event_path} did not resolve to a specific anime. Triggering full sync.")
        sync_all_library("Watchdog full library sync")

    def on_created(self, event): self.process_event(event.src_path)
    def on_deleted(self, event): self.process_event(event.src_path)
    def on_moved(self, event):
        self.process_event(event.src_path) # Old path might be a deletion
        self.process_event(event.dest_path) # New path might be a creation/modification
    def on_modified(self, event):
        # Proses jika ada perubahan pada folder atau file video
        if event.is_directory:
            self.process_event(event.src_path)
        elif any(event.src_path.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
            self.process_event(event.src_path)

def start_scanner():
    """Menjalankan sinkronisasi awal dan memulai observer watchdog."""
    app_log("Performing initial full library sync...")
    sync_all_library("Initial library sync") # Initial full sync
    app_log("Initial full library sync complete.")

    reconfigure_library_observer()
    try:
        while not wait_for_shutdown(1):
            pass
    except KeyboardInterrupt:
        stop_library_observer()
    finally:
        stop_library_observer()

def stop_library_observer(timeout=5):
    global LIBRARY_OBSERVER

    with LIBRARY_OBSERVER_LOCK:
        if LIBRARY_OBSERVER is None:
            with LIBRARY_SYNC_DEBOUNCE_LOCK:
                for timer in LIBRARY_SYNC_TIMERS.values():
                    timer.cancel()
                LIBRARY_SYNC_TIMERS.clear()
            return

        LIBRARY_OBSERVER.stop()
        LIBRARY_OBSERVER.join(timeout=timeout)
        LIBRARY_OBSERVER = None

    with LIBRARY_SYNC_DEBOUNCE_LOCK:
        for timer in LIBRARY_SYNC_TIMERS.values():
            timer.cancel()
        LIBRARY_SYNC_TIMERS.clear()

def reconfigure_library_observer():
    global LIBRARY_OBSERVER

    if is_shutdown_requested():
        stop_library_observer()
        return

    with LIBRARY_OBSERVER_LOCK:
        if LIBRARY_OBSERVER is not None:
            LIBRARY_OBSERVER.stop()
            LIBRARY_OBSERVER.join(timeout=5)
            LIBRARY_OBSERVER = None
            with LIBRARY_SYNC_DEBOUNCE_LOCK:
                for timer in LIBRARY_SYNC_TIMERS.values():
                    timer.cancel()
                LIBRARY_SYNC_TIMERS.clear()

        valid_paths = get_valid_anime_paths()
        if not valid_paths:
            app_log("Watchdog inactive. No valid anime folders configured.", "WARN")
            return

        observer = Observer()
        handler = LibraryHandler()
        for path in valid_paths:
            observer.schedule(handler, path, recursive=True)
            debug_log(f"Monitoring folder: {path}")

        observer.start()
        LIBRARY_OBSERVER = observer

def periodic_sync_task(interval_seconds=900): # Sync every 15 minutes
    """Melakukan sinkronisasi penuh secara berkala sebagai pengaman."""
    while not is_shutdown_requested():
        if wait_for_shutdown(interval_seconds):
            break
        try:
            debug_log("Performing periodic full library sync...")
            sync_all_library("Periodic library sync")
            debug_log("Periodic full library sync complete.")
        except Exception as e:
            app_log(f"Periodic sync failed: {e}", "ERROR")

def get_auto_import_thread():
    with AUTO_IMPORT_THREAD_LOCK:
        return AUTO_IMPORT_THREAD

def join_thread_with_timeout(thread, timeout, label):
    if thread is None:
        return True

    thread.join(timeout=max(0, timeout or 0))
    if thread.is_alive():
        app_log(f"Shutdown timed out waiting for {label}.", "WARN")
        return False

    return True

def join_background_workers(threads=None, timeout=5):
    request_shutdown()
    stop_library_observer(timeout=timeout)

    all_stopped = True
    for label, thread in (threads or {}).items():
        all_stopped = join_thread_with_timeout(thread, timeout, label) and all_stopped

    auto_thread = get_auto_import_thread()
    all_stopped = join_thread_with_timeout(auto_thread, timeout, "auto-import") and all_stopped
    return all_stopped

def should_start_background_workers(debug_enabled=False):
    if debug_enabled and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False

    return True

if __name__ == "__main__":
    flask_debug = False

    # Start background tasks if main.py is run directly
    # init_db() is called when main.py is imported, so no need to call it here again.
    workers_enabled = should_start_background_workers(flask_debug)
    if workers_enabled:
        scanner_thread = threading.Thread(
            target=start_scanner,
            name="library-watchdog",
            daemon=True
        )
        scanner_thread.start()

        periodic_sync_thread = threading.Thread(
            target=periodic_sync_task,
            name="periodic-sync",
            daemon=True
        )
        periodic_sync_thread.start()

        start_auto_import_worker()

    # Suppress Flask/Werkzeug startup and request logs in production-like mode
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    log_startup_summary(
        mode="direct",
        host="0.0.0.0",
        port=5000,
        scanner_enabled=workers_enabled,
        periodic_sync_enabled=workers_enabled,
        auto_import_worker_enabled=workers_enabled
    )

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=flask_debug
    )

