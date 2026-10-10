# anibase/ffmpeg.py
"""Manajemen eksekusi subprocess FFmpeg/FFprobe, pool concurrency lock,
cache kegagalan, dan diagnostik dependensi eksternal."""

import os
import shutil
import subprocess
import secrets
import time
import threading
from flask import Response
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    run_hidden_subprocess,
    get_media_tool_path,
    is_shutdown_requested,
    normalize_library_path,
)
from anibase.settings import load_settings


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


def acquire_ffmpeg_media_lock(key, timeout=None):
    if timeout is None:
        timeout = _constants.FFMPEG_MEDIA_LOCK_TIMEOUT_SECONDS
    with _constants.FFMPEG_MEDIA_LOCKS_GUARD:
        entry = _constants.FFMPEG_MEDIA_LOCKS.get(key)
        if entry is None:
            entry = {
                "lock": threading.Lock(),
                "users": 0
            }
            _constants.FFMPEG_MEDIA_LOCKS[key] = entry
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

    with _constants.FFMPEG_MEDIA_LOCKS_GUARD:
        entry = _constants.FFMPEG_MEDIA_LOCKS.get(key)
        if not entry:
            return

        entry["users"] = max(0, entry.get("users", 1) - 1)
        if entry["users"] == 0 and not entry["lock"].locked():
            _constants.FFMPEG_MEDIA_LOCKS.pop(key, None)


def get_ffmpeg_failure_cache(key):
    now = time.monotonic()
    with _constants.FFMPEG_FAILURE_CACHE_LOCK:
        expired = [
            cache_key
            for cache_key, value in _constants.FFMPEG_FAILURE_CACHE.items()
            if value.get("expires_at", 0) <= now
        ]
        for cache_key in expired:
            _constants.FFMPEG_FAILURE_CACHE.pop(cache_key, None)

        value = _constants.FFMPEG_FAILURE_CACHE.get(key)
        if value and value.get("expires_at", 0) > now:
            return value.get("result")

    return None


def set_ffmpeg_failure_cache(key, result):
    with _constants.FFMPEG_FAILURE_CACHE_LOCK:
        _constants.FFMPEG_FAILURE_CACHE[key] = {
            "expires_at": time.monotonic() + _constants.FFMPEG_FAILURE_CACHE_TTL_SECONDS,
            "result": result
        }


def clear_ffmpeg_failure_cache(key):
    with _constants.FFMPEG_FAILURE_CACHE_LOCK:
        _constants.FFMPEG_FAILURE_CACHE.pop(key, None)


def run_ffmpeg_command(args, timeout):
    if is_shutdown_requested():
        return make_media_generation_result(
            False,
            "",
            "shutting_down",
            "Media generation skipped because the server is shutting down."
        )

    acquired = _constants.FFMPEG_SEMAPHORE.acquire(timeout=_constants.FFMPEG_SEMAPHORE_TIMEOUT_SECONDS)
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
        _constants.FFMPEG_SEMAPHORE.release()

    if result.returncode != 0:
        return make_media_generation_result(
            False,
            "",
            "failed",
            result.stderr.strip() or f"FFmpeg return code: {result.returncode}"
        )

    return make_media_generation_result(True, "", "generated", "")


def media_generation_error_response(result):
    status = result.get("status") or "failed"
    status_code = 503 if status in {
        "busy",
        "ffmpeg_unavailable",
        "timeout",
        "error",
        "shutting_down"
    } else 404
    response = Response("", status=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-AniBase-Media-Status"] = status
    if status_code == 503:
        response.headers["Retry-After"] = "2" if status == "busy" else "10"
    return response


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
            timeout=_constants.MEDIA_DIAGNOSTIC_TIMEOUT_SECONDS
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


def get_media_dependency_diagnostics(settings=None, force=False):
    from anibase.player import diagnose_vlc_path
    settings = settings if isinstance(settings, dict) else load_settings()
    vlc_path = normalize_library_path(settings.get("vlc_path", ""))
    cache_key = (
        "media-dependencies",
        vlc_path
    )
    now = time.monotonic()

    with _constants.MEDIA_DIAGNOSTIC_CACHE_LOCK:
        cached = _constants.MEDIA_DIAGNOSTIC_CACHE.get(cache_key)
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

    with _constants.MEDIA_DIAGNOSTIC_CACHE_LOCK:
        _constants.MEDIA_DIAGNOSTIC_CACHE.clear()
        _constants.MEDIA_DIAGNOSTIC_CACHE[cache_key] = {
            "expires_at": now + _constants.MEDIA_DIAGNOSTIC_CACHE_TTL_SECONDS,
            "value": diagnostics
        }

    return diagnostics
