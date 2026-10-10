# anibase/constants.py
"""Definisi konstanta, path direktori, nama berkas, batas waktu (TTL),
pola regex, dan variabel state global untuk AniBase."""

import os
import sys
import threading

APP_NAME = "AniBase"
APP_DATA_DIR_NAME = "AniBase"
LEGACY_APP_DATA_DIR_NAMES = ("".join(("Anzu", "Anime Server")),)
PROJECT_RUNTIME_ENV_NAMES = (
    "ANIBASE_USE_PROJECT_RUNTIME",
)

def _get_initial_application_dir():
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    # When in anibase/ package, application root is parent directory
    pkg_dir = os.path.abspath(os.path.dirname(__file__))
    parent_dir = os.path.abspath(os.path.dirname(pkg_dir))
    return parent_dir

def _get_initial_resource_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.abspath(sys._MEIPASS)
    return _get_initial_application_dir()

def _get_initial_local_app_data_dir(app_data_dir_name):
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

def _get_initial_user_data_dir():
    enabled_values = {"1", "true", "yes", "on"}
    use_project_runtime = any(
        os.environ.get(env_name, "").strip().lower() in enabled_values
        for env_name in PROJECT_RUNTIME_ENV_NAMES
    )
    if use_project_runtime and not getattr(sys, "frozen", False):
        return _get_initial_application_dir()

    return _get_initial_local_app_data_dir(APP_DATA_DIR_NAME)

APP_DIR = _get_initial_application_dir()
RESOURCE_DIR = _get_initial_resource_dir()
USER_DATA_DIR = _get_initial_user_data_dir()
BASE_DIR = APP_DIR

MAX_REQUEST_BODY_BYTES = 32 * 1024 * 1024

# Logging Paths & Configuration
LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "anibase.log")
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s:%(threadName)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 3 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOGGER_NAME = "anibase"
VERBOSE_LOGS = os.environ.get("ANIBASE_VERBOSE_LOGS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on"
}

# Runtime & Cache Directories
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
IMAGE_PROXY_CACHE = os.path.join(CACHE_DIR, "image_proxy")
ANISKIP_CACHE = os.path.join(CACHE_DIR, "aniskip")

# Core Database & State Files
DB_PATH = os.path.join(CACHE_DIR, "library.db")
WATCH_HISTORY_FILE = os.path.join(CACHE_DIR, "watch_history.json")
WATCH_STATUS_FILE = os.path.join(CACHE_DIR, "watch_status.json")
SETTINGS_FILE = os.path.join(CACHE_DIR, "settings.json")
MAL_AUTH_FILE = os.path.join(CACHE_DIR, "mal_auth.json")
MAL_SCROBBLE_CACHE = os.path.join(CACHE_DIR, "mal_scrobble_cache.json")
MAL_PROFILE_CACHE = os.path.join(CACHE_DIR, "mal_profile_cache.json")

# External APIs & Defaults
ANISKIP_BASE_URL = "https://api.aniskip.com/v2"
ANISKIP_TIMEOUT_SECONDS = 5
MAL_API_BASE_URL = "https://api.myanimelist.net/v2"
MAL_OAUTH_AUTH_URL = "https://myanimelist.net/v1/oauth2/authorize"
MAL_OAUTH_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
DEFAULT_MAL_CLIENT_ID = "cc3c671a6202a3ef9c6fd9383c6f3160"
JIKAN_BASE_URL = "https://api.jikan.moe/v4"
TENRAI_BASE_URL = "https://api.tenrai.org/v1"
ANILIST_API_URL = "https://graphql.anilist.co"

# Schema Versions & TTLs
SETTINGS_BACKUP_SCHEMA_VERSION = 1
ANIME_METADATA_SCHEMA_VERSION = 2
MAL_PROFILE_CACHE_TTL = 1800       # 30 menit
MAL_ANIMELIST_CACHE_TTL = 900      # 15 menit
SCHEDULE_CACHE_TTL_SECONDS = 300    # 5 menit
SCHEDULE_LOOKAHEAD_DAYS = 3
AIRING_NOW_GRACE_SECONDS = 30 * 60  # 30 menit
STUDIO_PROJECT_CACHE_TTL_SECONDS = 86400  # 24 jam
STUDIO_PROJECT_LIMIT = 80
SEIYUU_CACHE_TTL_SECONDS = 86400    # 24 jam
SEIYUU_ROLE_PAGE_LIMIT = 3
ANILIST_METADATA_PROVIDER = "anilist"
TENRAI_METADATA_PROVIDER = "tenrai"
ANILIST_FAILURE_COOLDOWN_SECONDS = 60
TENRAI_MIN_REQUEST_INTERVAL_SECONDS = 0.25
LIBRARY_SYNC_DEBOUNCE_SECONDS = 3
AUTO_IMPORT_LOG_TTL_SECONDS = 300

# Supported Video Extensions
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
    ".!ut",
    ".aria2"
)

THEME_PRESETS = {
    "dark-blue",
    "dark-orange",
    "dark-green"
}

# Shortcut Definitions
SHORTCUT_DEFINITIONS = [
    {
        "category": "Playback",
        "category_id": "playback",
        "icon": "&#9654;",
        "description": "Basic player playback and display controls.",
        "actions": [
            {
                "id": "toggle_play",
                "label": "Play / Pause",
                "description": "Start or pause video playback",
                "default": "Space",
            },
            {
                "id": "toggle_fullscreen",
                "label": "Fullscreen",
                "description": "Enter or exit fullscreen cinema mode",
                "default": "f",
            },
            {
                "id": "toggle_mute",
                "label": "Mute / Unmute",
                "description": "Silence audio or restore previous volume",
                "default": "m",
            },
            {
                "id": "toggle_subtitles",
                "label": "Toggle Subtitles",
                "description": "Turn soft subtitles on or off",
                "default": "c",
            },
        ],
    },
    {
        "category": "Seeking & Navigation",
        "category_id": "seeking",
        "icon": "&#9197;",
        "description": "Jump forward, backward, or switch between episodes.",
        "actions": [
            {
                "id": "seek_backward",
                "label": "Seek Backward (5s)",
                "description": "Rewind playback by 5 seconds",
                "default": "ArrowLeft",
            },
            {
                "id": "seek_forward",
                "label": "Seek Forward (5s)",
                "description": "Fast-forward playback by 5 seconds",
                "default": "ArrowRight",
            },
            {
                "id": "seek_backward_large",
                "label": "Seek Backward (30s)",
                "description": "Rewind playback by 30 seconds",
                "default": "Ctrl+ArrowLeft",
            },
            {
                "id": "seek_forward_large",
                "label": "Seek Forward (30s)",
                "description": "Fast-forward playback by 30 seconds",
                "default": "Ctrl+ArrowRight",
            },
            {
                "id": "previous_episode",
                "label": "Previous Episode",
                "description": "Switch immediately to previous episode",
                "default": "Shift+P",
            },
            {
                "id": "next_episode",
                "label": "Next Episode",
                "description": "Switch immediately to next episode",
                "default": "Shift+N",
            },
        ],
    },
    {
        "category": "Volume & Speed",
        "category_id": "audio_speed",
        "icon": "&#128266;",
        "description": "Control audio loudness and playback rate.",
        "actions": [
            {
                "id": "volume_up",
                "label": "Volume Up (+5%)",
                "description": "Increase sound volume by 5%",
                "default": "ArrowUp",
            },
            {
                "id": "volume_down",
                "label": "Volume Down (-5%)",
                "description": "Decrease sound volume by 5%",
                "default": "ArrowDown",
            },
            {
                "id": "speed_up",
                "label": "Speed Up (+0.25x)",
                "description": "Increase playback speed up to 2x",
                "default": "+",
            },
            {
                "id": "speed_down",
                "label": "Speed Down (-0.25x)",
                "description": "Decrease playback speed down to 0.25x",
                "default": "-",
            },
        ],
    },
    {
        "category": "Special Features",
        "category_id": "special",
        "icon": "&#10024;",
        "description": "AniSkip markers, instant screenshots, and help overlay.",
        "actions": [
            {
                "id": "aniskip",
                "label": "Skip Intro / Outro",
                "description": "Skip anime opening or ending when detected",
                "default": "s",
            },
            {
                "id": "screenshot",
                "label": "Capture Screenshot",
                "description": "Save crisp frame to configured folder",
                "default": "s",
            },
            {
                "id": "shortcut_help",
                "label": "Shortcuts Overlay",
                "description": "Display keyboard shortcuts cheat sheet",
                "default": "?",
            },
        ],
    },
]

DEFAULT_PLAYER_SHORTCUTS = {
    action["id"]: action["default"]
    for cat in SHORTCUT_DEFINITIONS
    for action in cat["actions"]
}

# Synchronization Locks & Events
SHUTDOWN_EVENT = threading.Event()
WATCH_DATA_LOCK = threading.RLock()
SETTINGS_LOCK = threading.RLock()
EPISODE_CACHE_LOCK = threading.RLock()
SCHEDULE_CACHE_LOCK = threading.RLock()
MAL_AUTH_LOCK = threading.RLock()
MAL_SCROBBLE_LOCK = threading.RLock()
MAL_PROFILE_LOCK = threading.RLock()
MAL_ANIMELIST_LOCK = threading.RLock()
PROVIDER_REQUEST_STATE_LOCK = threading.RLock()
ANILIST_RECOVERY_LOCK = threading.RLock()
LIBRARY_OBSERVER_LOCK = threading.RLock()
LIBRARY_SYNC_LOCK = threading.Lock()
SETUP_SYNC_STATE_LOCK = threading.Lock()
LIBRARY_SYNC_DEBOUNCE_LOCK = threading.RLock()
AUTO_IMPORT_THREAD_LOCK = threading.RLock()
AUTO_IMPORT_STATE_LOCK = threading.RLock()
RPC_LOCK = threading.RLock()
RPC_DISPATCH_LOCK = threading.Lock()
RPC_STATE_LOCK = threading.RLock()

try:
    from pypresence import Presence
    from pypresence.types import ActivityType
    from pypresence.types import StatusDisplayType
except ImportError:
    Presence = None
    ActivityType = None
    StatusDisplayType = None

# Runtime Global States (Mutable)
ANIME_PATHS = []
MOVIE_PATH = ""
VLC_PATH = ""
DISCORD_RPC_ENABLED = True
DISCORD_CLIENT_ID = ""
DISCORD_TIMER_MODE = "remaining"
RPC_CONNECTION_STATUS = "idle"
rpc = None
rpc_connected = False
RPC_PENDING_TASK = None
RPC_WORKER_THREAD = None
RPC_START_TIME = None
CURRENT_RPC_ANIME = None
CURRENT_RPC_OWNER = None
RPC_SESSION_VERSIONS = {}
RPC_DESIRED = None
RPC_MONITOR_THREAD = None
RPC_RETRY_AT = 0.0
RPC_RETRY_DELAY = 2.0
RPC_LAST_PAYLOAD = None
RPC_LAST_SENT_AT = 0.0

MAL_OAUTH_SESSIONS = {}
SCHEDULE_CACHE = {
    "expires_at": 0,
    "airing_list": [],
    "error": None
}
ANILIST_UNAVAILABLE_UNTIL = 0.0
TENRAI_NEXT_REQUEST_AT = 0.0
ANILIST_CALL_STATE = threading.local()
ANILIST_RECOVERY_IN_FLIGHT = set()
LIBRARY_OBSERVER = None
SETUP_SYNC_STATE = {
    "running": False,
    "done": False,
    "error": "",
    "stage": "idle",
    "current": 0,
    "total": 0,
    "anime_count": 0,
}
LIBRARY_SYNC_TIMERS = {}
AUTO_IMPORT_THREAD = None
AUTO_IMPORT_FILE_STATE = {}
AUTO_IMPORT_LOG_STATE = {}

MOVIE_HISTORY_PREFIX = "movie::"

# Media & FFmpeg Limits, Locks & Caches
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
SEEK_PREVIEW_LOCK = threading.Lock()
SEEK_PREVIEW_ACTIVE = set()
SEEK_PREVIEW_FAILURES = {}
