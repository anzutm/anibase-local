from flask import (
    Flask,
    render_template,
    redirect,
    jsonify,
    send_file,
    request,
    abort,
    url_for,
    Response,
)
import flask.cli
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import types
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
from io import BytesIO
from contextlib import contextmanager
from urllib.parse import unquote, urlparse, quote_plus
from difflib import SequenceMatcher
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from werkzeug.exceptions import RequestEntityTooLarge
import time
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from pypresence import Presence
    from pypresence.types import ActivityType
    from pypresence.types import StatusDisplayType
except ImportError:
    Presence = None
    ActivityType = None
    StatusDisplayType = None

import anibase
from anibase import *
import anibase.constants as _anibase_constants
import anibase.settings as _anibase_settings
import anibase.utils as _anibase_utils
import anibase.logging as _anibase_logging
import anibase.db as _anibase_db
import anibase.watch as _anibase_watch
import anibase.ffmpeg as _anibase_ffmpeg
import anibase.player as _anibase_player
import anibase.media as _anibase_media
import anibase.thumbnails as _anibase_thumbnails
import anibase.subtitles as _anibase_subtitles
import anibase.metadata as _anibase_metadata
import anibase.integrations.aniskip as _anibase_aniskip
import anibase.integrations.mal as _anibase_mal
import anibase.integrations.discord as _anibase_discord
import anibase.integrations.tenrai as _anibase_tenrai
import anibase.integrations.jikan as _anibase_jikan
import anibase.integrations.anilist as _anibase_anilist
import anibase.scanner as _anibase_scanner
import anibase.watcher as _anibase_watcher
import anibase.web as _anibase_web
import anibase.web.helpers as _anibase_web_helpers
import anibase.web.routes_pages as _anibase_web_pages
import anibase.web.routes_api as _anibase_web_api

_MUTABLE_CONSTANTS_NAMES = {
    'ANIME_PATHS',
    'MOVIE_PATH',
    'VLC_PATH',
    'DISCORD_RPC_ENABLED',
    'DISCORD_CLIENT_ID',
    'DISCORD_TIMER_MODE',
    'RPC_CONNECTION_STATUS',
    'Presence',
    'ActivityType',
    'StatusDisplayType',
    'rpc',
    'rpc_connected',
    'RPC_PENDING_TASK',
    'RPC_WORKER_THREAD',
    'RPC_START_TIME',
    'CURRENT_RPC_ANIME',
    'CURRENT_RPC_OWNER',
    'RPC_SESSION_VERSIONS',
    'RPC_DESIRED',
    'RPC_MONITOR_THREAD',
    'RPC_RETRY_AT',
    'RPC_RETRY_DELAY',
    'RPC_LAST_PAYLOAD',
    'RPC_LAST_SENT_AT',
    'MAL_OAUTH_SESSIONS',
    'SCHEDULE_CACHE',
    'ANILIST_UNAVAILABLE_UNTIL',
    'TENRAI_NEXT_REQUEST_AT',
    'ANILIST_CALL_STATE',
    'ANILIST_RECOVERY_IN_FLIGHT',
    'LIBRARY_OBSERVER',
    'SETUP_SYNC_STATE',
    'LIBRARY_SYNC_TIMERS',
    'AUTO_IMPORT_THREAD',
    'AUTO_IMPORT_FILE_STATE',
    'AUTO_IMPORT_LOG_STATE',
}

_MODULES_TO_SYNC = (
    _anibase_constants,
    _anibase_settings,
    _anibase_utils,
    _anibase_logging,
    _anibase_db,
    _anibase_watch,
    _anibase_ffmpeg,
    _anibase_player,
    _anibase_media,
    _anibase_thumbnails,
    _anibase_subtitles,
    _anibase_metadata,
    _anibase_aniskip,
    _anibase_mal,
    _anibase_discord,
    _anibase_tenrai,
    _anibase_jikan,
    _anibase_anilist,
    _anibase_scanner,
    _anibase_watcher,
    _anibase_web,
    _anibase_web_helpers,
    _anibase_web_pages,
    _anibase_web_api,
)


class _MainModuleProxy(types.ModuleType):
    def __getattribute__(self, name):
        if name in _MUTABLE_CONSTANTS_NAMES:
            return getattr(_anibase_constants, name)
        return super().__getattribute__(name)

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _MODULES_TO_SYNC:
            if hasattr(mod, name):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _MainModuleProxy

app = Flask(
    __name__,
    template_folder=os.path.join(RESOURCE_DIR, "templates"),
    static_folder=os.path.join(RESOURCE_DIR, "static"),
)
setup_web_app(app)

ensure_runtime_directories()
RUNTIME_MIGRATION_LOG_MESSAGES = (
    migrate_legacy_user_data_dirs()
    + migrate_legacy_runtime_data()
)
configure_logging()
for migration_message in RUNTIME_MIGRATION_LOG_MESSAGES:
    app_log(migration_message)

init_db()
apply_settings(load_settings())

if __name__ == "__main__":
    flask_debug = False

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
