# anibase/utils.py
"""Fungsi pembantu murni (pure utility functions) untuk subprocess,
jalur berkas, normalisasi, penanganan JSON atomik, dan migrasi direktori."""

import os
import sys
import subprocess
import shutil
import json
import re
import threading
from datetime import datetime

import anibase.constants as constants
from anibase.logging import app_log, debug_log

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
    pkg_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.dirname(pkg_dir))

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
        for env_name in constants.PROJECT_RUNTIME_ENV_NAMES
    )
    if use_project_runtime and not getattr(sys, "frozen", False):
        return get_application_dir()

    return get_local_app_data_dir(constants.APP_DATA_DIR_NAME)

def get_protected_cache_files():
    return {
        os.path.abspath(constants.DB_PATH),
        os.path.abspath(constants.SETTINGS_FILE),
        os.path.abspath(constants.WATCH_HISTORY_FILE),
        os.path.abspath(constants.WATCH_STATUS_FILE),
        os.path.abspath(f"{constants.WATCH_HISTORY_FILE}.bak"),
        os.path.abspath(f"{constants.WATCH_STATUS_FILE}.bak"),
        os.path.abspath(constants.MAL_AUTH_FILE),
        os.path.abspath(constants.MAL_SCROBBLE_CACHE),
    }

def get_active_cache_dir():
    return os.path.abspath(os.path.dirname(constants.DB_PATH) or constants.CACHE_DIR)

def get_runtime_directories():
    return [
        constants.USER_DATA_DIR,
        constants.CACHE_DIR,
        constants.LOG_DIR,
        constants.RUNTIME_DIR,
        constants.TEMP_DIR,
        constants.POSTER_CACHE,
        constants.BANNER_CACHE,
        constants.THUMBNAIL_CACHE,
        constants.METADATA_CACHE,
        constants.CHARACTER_CACHE,
        constants.EPISODE_CACHE,
        constants.SUBTITLE_CACHE,
        constants.SEIYUU_CACHE,
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
    if os.path.abspath(constants.USER_DATA_DIR) == os.path.abspath(constants.APP_DIR):
        return []

    messages = []
    for legacy_name in constants.LEGACY_APP_DATA_DIR_NAMES:
        legacy_dir = get_local_app_data_dir(legacy_name)
        if os.path.abspath(legacy_dir) == os.path.abspath(constants.USER_DATA_DIR):
            continue

        message = copy_tree_missing(
            legacy_dir,
            constants.USER_DATA_DIR,
            f"legacy LocalAppData/{legacy_name}"
        )
        if message:
            messages.append(message)

    return messages

def migrate_legacy_runtime_data():
    legacy_cache_dir = os.path.join(constants.APP_DIR, "cache")
    legacy_log_dir = os.path.join(constants.APP_DIR, "logs")
    messages = []

    file_migrations = (
        (os.path.join(legacy_cache_dir, "settings.json"), constants.SETTINGS_FILE, "settings.json"),
        (os.path.join(legacy_cache_dir, "library.db"), constants.DB_PATH, "library.db"),
        (os.path.join(legacy_cache_dir, "library.db-wal"), f"{constants.DB_PATH}-wal", "library.db-wal"),
        (os.path.join(legacy_cache_dir, "library.db-shm"), f"{constants.DB_PATH}-shm", "library.db-shm"),
        (os.path.join(legacy_cache_dir, "watch_history.json"), constants.WATCH_HISTORY_FILE, "watch_history.json"),
        (os.path.join(legacy_cache_dir, "watch_history.json.bak"), f"{constants.WATCH_HISTORY_FILE}.bak", "watch_history.json.bak"),
        (os.path.join(legacy_cache_dir, "watch_status.json"), constants.WATCH_STATUS_FILE, "watch_status.json"),
        (os.path.join(legacy_cache_dir, "watch_status.json.bak"), f"{constants.WATCH_STATUS_FILE}.bak", "watch_status.json.bak"),
    )

    for source, destination, label in file_migrations:
        message = copy_file_if_missing(source, destination, label)
        if message:
            messages.append(message)

    cache_migrations = (
        ("posters", constants.POSTER_CACHE),
        ("banners", constants.BANNER_CACHE),
        ("metadata", constants.METADATA_CACHE),
        ("characters", constants.CHARACTER_CACHE),
        ("thumbnails", constants.THUMBNAIL_CACHE),
        ("subtitles", constants.SUBTITLE_CACHE),
        ("episodes", constants.EPISODE_CACHE),
        ("seiyuu", constants.SEIYUU_CACHE),
    )
    for dirname, destination in cache_migrations:
        message = copy_tree_missing(
            os.path.join(legacy_cache_dir, dirname),
            destination,
            f"cache/{dirname}"
        )
        if message:
            messages.append(message)

    message = copy_tree_missing(legacy_log_dir, constants.LOG_DIR, "logs")
    if message:
        messages.append(message)

    return messages

def request_shutdown():
    constants.SHUTDOWN_EVENT.set()

def is_shutdown_requested():
    return constants.SHUTDOWN_EVENT.is_set()

def wait_for_shutdown(timeout):
    return constants.SHUTDOWN_EVENT.wait(max(0, timeout or 0))

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

def normalize_screenshot_filename_prefix(value, default="vlcsnap"):
    prefix = str(value or "").strip()
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", prefix)
    prefix = re.sub(r"\s+", " ", prefix).strip(" .")
    return prefix[:80] or default

def normalize_library_path(path):
    if not path:
        return ""

    path = str(path).strip()
    if not path:
        return ""

    normalized = os.path.abspath(os.path.expanduser(path))

    if normalized == constants.BASE_DIR:
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

def normalize_shortcuts(raw_shortcuts):
    if not isinstance(raw_shortcuts, dict):
        return constants.DEFAULT_PLAYER_SHORTCUTS.copy()
    normalized = constants.DEFAULT_PLAYER_SHORTCUTS.copy()
    for key, default_val in constants.DEFAULT_PLAYER_SHORTCUTS.items():
        val = raw_shortcuts.get(key)
        if isinstance(val, str) and val.strip():
            normalized[key] = val.strip()
    return normalized

def normalize_episode_path_key(episode_path):
    return str(episode_path or "").replace("\\", "/").strip("/")

def normalize_episode_display_name(value):
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(value or ""))
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120]

def safe_cache_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name or "")

def clean_movie_title(filename):
    title = os.path.splitext(filename)[0]
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"\b1080p\b", "", title, flags=re.I)
    title = re.sub(r"\bBD\b", "", title, flags=re.I)
    return " ".join(title.split())

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
        if os.path.commonpath([base_path, candidate_path]) != base_path:
            return None
    except ValueError:
        return None

    return candidate_path

def is_valid_cache_file(path):
    try:
        return bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
    except OSError:
        return False

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

def preserve_corrupt_watch_file(path, label, reason):
    if not os.path.exists(path):
        return

    corrupt_path = get_corrupt_watch_file(path)

    try:
        os.replace(path, corrupt_path)
        app_log(f"{label} preserved as corrupt file: {corrupt_path} ({reason})", "WARN")
    except OSError as e:
        app_log(f"{label} could not preserve corrupt file {path}: {e}", "ERROR")

def is_resolved_path_inside(base_path, candidate_path):
    if not base_path or not candidate_path:
        return False

    base_real = os.path.normcase(os.path.realpath(os.path.abspath(base_path)))
    candidate_real = os.path.normcase(os.path.realpath(os.path.abspath(candidate_path)))

    try:
        return os.path.commonpath([base_real, candidate_real]) == base_real
    except ValueError:
        return False

def load_json_dict_if_exists(path):
    data, error = read_json_dict_file(path, os.path.basename(path))
    if error is not None:
        return {}
    return data

def remove_file_quietly(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
