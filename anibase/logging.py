# anibase/logging.py
"""Sistem logging untuk AniBase. Mendukung rotasi berkas log, format terstruktur,
dan pencatatan ringkasan startup."""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from anibase.constants import (
    LOG_DIR,
    LOG_FILE,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    LOGGER_NAME,
    VERBOSE_LOGS,
)

LOGGER = logging.getLogger(LOGGER_NAME)
LOGGING_CONFIGURED = False
LOGGING_CONFIG = None
STARTUP_SUMMARY_LOGGED = False

def configure_logging(log_dir=None, max_bytes=LOG_MAX_BYTES, backup_count=LOG_BACKUP_COUNT, level=None):
    global LOGGING_CONFIGURED, LOGGING_CONFIG

    target_log_dir = os.path.abspath(log_dir or LOG_DIR)
    target_log_file = os.path.join(target_log_dir, "anibase.log")
    level_name = (level or os.environ.get("ANIBASE_LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    config = (target_log_file, int(max_bytes), int(backup_count), log_level)

    if LOGGING_CONFIGURED and LOGGING_CONFIG == config:
        return LOGGER

    os.makedirs(target_log_dir, exist_ok=True)
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
        target_log_file,
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

def get_log_file_path():
    if LOGGING_CONFIG and LOGGING_CONFIG[0]:
        return LOGGING_CONFIG[0]
    return LOG_FILE

def summarize_dependency_status(result):
    if not isinstance(result, dict):
        return "unknown"
    status = result.get("status", "unknown")
    if result.get("available"):
        return "available"
    return status

def log_startup_summary(mode, host, port, scanner_enabled, periodic_sync_enabled, auto_import_worker_enabled, load_settings_fn=None, get_media_diagnostics_fn=None):
    global STARTUP_SUMMARY_LOGGED

    if STARTUP_SUMMARY_LOGGED:
        return

    STARTUP_SUMMARY_LOGGED = True

    if load_settings_fn is None:
        try:
            from anibase.settings import load_settings as load_settings_fn
        except ImportError:
            load_settings_fn = lambda: {}

    if get_media_diagnostics_fn is None:
        try:
            import main
            get_media_diagnostics_fn = getattr(main, "get_media_dependency_diagnostics", None)
        except Exception:
            get_media_diagnostics_fn = None

    settings = load_settings_fn() if callable(load_settings_fn) else {}
    if callable(get_media_diagnostics_fn):
        diagnostics = get_media_diagnostics_fn(settings)
    else:
        diagnostics = {"ffmpeg": {}, "ffprobe": {}, "vlc": {}}

    app_log(
        "Startup summary: "
        f"mode={mode}; "
        f"bind={host}:{port}; "
        f"lan={'enabled' if settings.get('lan_access_enabled') else 'disabled'}; "
        f"scanner={'enabled' if scanner_enabled else 'disabled'}; "
        f"periodic_sync={'enabled' if periodic_sync_enabled else 'disabled'}; "
        f"auto_import_worker={'enabled' if auto_import_worker_enabled else 'disabled'}; "
        f"ffmpeg={summarize_dependency_status(diagnostics.get('ffmpeg', {}))}; "
        f"ffprobe={summarize_dependency_status(diagnostics.get('ffprobe', {}))}; "
        f"vlc={summarize_dependency_status(diagnostics.get('vlc', {}))}; "
        f"log_file={get_log_file_path()}",
        "INFO"
    )
