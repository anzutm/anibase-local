# anibase/watch.py
"""Manajemen riwayat tontonan (watch history) dan status episode (watch status)
dengan penyimpanan ganda: SQLite (utama) dan JSON (fallback/backup)."""

import os
import sys
import sqlite3
from datetime import datetime
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    read_json_dict_file,
    atomic_write_json_file,
    get_watch_backup_file,
    preserve_corrupt_watch_file,
    normalize_episode_display_name,
    clean_movie_title,
    safe_cache_name,
)
from anibase.db import db_connection


def load_watch_json_file(path, label):
    with _constants.WATCH_DATA_LOCK:
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

    with _constants.WATCH_DATA_LOCK:
        atomic_write_json_file(path, data, label)

        backup_path = get_watch_backup_file(path)
        try:
            atomic_write_json_file(backup_path, data, f"{label} backup")
        except OSError as e:
            app_log(f"{label} saved, but backup write failed: {e}", "WARN")


def load_history_data():
    """Memuat watch history. Menggunakan SQLite sebagai sumber utama, dengan fallback ke JSON jika DB kosong atau diarahkan ke mock."""
    if os.path.dirname(os.path.abspath(_constants.WATCH_HISTORY_FILE)) != os.path.dirname(os.path.abspath(_constants.DB_PATH)):
        return load_watch_json_file(_constants.WATCH_HISTORY_FILE, "Watch history")

    history = {}
    try:
        with db_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT history_key, episode, episode_num, updated_at, time_str,
                       last_seconds, duration, media_name, display_name, episode_display_name
                FROM watch_history
                ORDER BY updated_at DESC
                """
            ).fetchall()
            for row in rows:
                history[row["history_key"]] = {
                    "episode": row["episode"] or "",
                    "episode_num": row["episode_num"],
                    "updated_at": row["updated_at"] or "",
                    "time_str": row["time_str"],
                    "last_seconds": row["last_seconds"] if row["last_seconds"] is not None else 0,
                    "duration": row["duration"] if row["duration"] is not None else 0,
                    "media_name": row["media_name"] or row["history_key"],
                    "display_name": row["display_name"] or row["history_key"],
                    "episode_display_name": row["episode_display_name"],
                }
    except Exception as e:
        debug_log(f"load_history_data SQLite read error: {e}")

    if not history:
        fallback = load_watch_json_file(_constants.WATCH_HISTORY_FILE, "Watch history")
        if fallback:
            db_sync_all_watch_history(fallback)
            return fallback

    return history


def save_watch_history(history):
    save_watch_json_file(_constants.WATCH_HISTORY_FILE, history, "Watch history")
    db_sync_all_watch_history(history)


def get_watch_history_key(anime_name, episode):
    if anime_name == "Movies":
        return f"{_constants.MOVIE_HISTORY_PREFIX}{episode}"

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
    display_name=None,
    episode_display_name=None
):
    with _constants.WATCH_DATA_LOCK:
        history = load_history_data()

        entry = {
            "episode": episode,
            "episode_num": episode_num,
            "updated_at": datetime.now().isoformat(),
            "time_str": time_str,
            "last_seconds": last_seconds,
            "duration": duration,
            "media_name": media_name or history_key,
            "display_name": display_name or history_key,
            "episode_display_name": normalize_episode_display_name(episode_display_name),
        }
        history[history_key] = entry

        save_watch_history(history)
        db_save_watch_history_entry(history_key, entry)


def remove_watch_history_entry(history_key):
    with _constants.WATCH_DATA_LOCK:
        history = load_history_data()

        if history_key not in history:
            return False

        history.pop(history_key, None)
        save_watch_history(history)
        db_remove_watch_history_entry(history_key)
        return True


def load_watch_status():
    """Memuat episode watch status. Menggunakan SQLite sebagai sumber utama, dengan fallback ke JSON jika DB kosong atau diarahkan ke mock."""
    if os.path.dirname(os.path.abspath(_constants.WATCH_STATUS_FILE)) != os.path.dirname(os.path.abspath(_constants.DB_PATH)):
        return load_watch_json_file(_constants.WATCH_STATUS_FILE, "Watch status")

    status_data = {}
    try:
        with db_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT anime_name, episode, watched, progress, duration, current_seconds, updated_at
                FROM episode_watch_status
                """
            ).fetchall()
            for row in rows:
                anime = row["anime_name"]
                ep = row["episode"]
                if anime not in status_data:
                    status_data[anime] = {}
                status_data[anime][ep] = {
                    "watched": bool(row["watched"]),
                    "progress": row["progress"] if row["progress"] is not None else 0,
                    "duration": row["duration"] if row["duration"] is not None else 0,
                    "current_seconds": row["current_seconds"] if row["current_seconds"] is not None else 0,
                    "updated_at": row["updated_at"] or ""
                }
    except Exception as e:
        debug_log(f"load_watch_status SQLite read error: {e}")

    if not status_data:
        fallback = load_watch_json_file(_constants.WATCH_STATUS_FILE, "Watch status")
        if fallback:
            db_sync_all_watch_status(fallback)
            return fallback

    return status_data


def save_watch_status(status_data):
    save_watch_json_file(_constants.WATCH_STATUS_FILE, status_data, "Watch status")
    db_sync_all_watch_status(status_data)


def get_episode_watch_status(anime_name, episode):
    """Mengambil status nonton satu episode secara cepat dari SQLite via indexed primary key."""
    if not anime_name or not episode:
        return {}

    if os.path.dirname(os.path.abspath(_constants.WATCH_STATUS_FILE)) != os.path.dirname(os.path.abspath(_constants.DB_PATH)):
        return load_watch_status().get(anime_name, {}).get(episode, {})

    try:
        with db_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT watched, progress, duration, current_seconds, updated_at
                FROM episode_watch_status
                WHERE anime_name = ? AND episode = ?
                """,
                (str(anime_name), str(episode))
            ).fetchone()
            if row is not None:
                return {
                    "watched": bool(row["watched"]),
                    "progress": row["progress"] if row["progress"] is not None else 0,
                    "duration": row["duration"] if row["duration"] is not None else 0,
                    "current_seconds": row["current_seconds"] if row["current_seconds"] is not None else 0,
                    "updated_at": row["updated_at"] or ""
                }
    except Exception as e:
        debug_log(f"get_episode_watch_status SQLite read error: {e}")

    return load_watch_status().get(anime_name, {}).get(episode, {})


def update_episode_watch_status(anime_name, episode, data):
    with _constants.WATCH_DATA_LOCK:
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
        db_save_episode_watch_status(anime_name, episode, episode_status)
        return episode_status


def mark_episode_watched(anime_name, episode, scrobble_callback=None):
    result = update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": True,
            "progress": 100
        }
    )
    if scrobble_callback:
        scrobble_callback(anime_name, episode)
    else:
        main_mod = sys.modules.get("main")
        if main_mod and hasattr(main_mod, "trigger_mal_scrobble_if_enabled"):
            try:
                main_mod.trigger_mal_scrobble_if_enabled(anime_name, episode)
            except Exception:
                pass
    return result


def mark_episode_unwatched(anime_name, episode, clear_scrobble_callback=None):
    if clear_scrobble_callback:
        clear_scrobble_callback(anime_name, episode)
    else:
        main_mod = sys.modules.get("main")
        if main_mod and hasattr(main_mod, "clear_mal_scrobble_record"):
            try:
                main_mod.clear_mal_scrobble_record(anime_name, episode)
            except Exception:
                pass
    return update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": False,
            "progress": 0
        }
    )


def db_save_watch_history_entry(history_key, entry, conn=None):
    if not history_key or not isinstance(entry, dict):
        return
    query = """
        INSERT OR REPLACE INTO watch_history (
            history_key, episode, episode_num, updated_at, time_str,
            last_seconds, duration, media_name, display_name, episode_display_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        str(history_key),
        str(entry.get("episode") or ""),
        entry.get("episode_num"),
        str(entry.get("updated_at") or datetime.now().isoformat()),
        entry.get("time_str"),
        float(entry.get("last_seconds") or 0.0),
        float(entry.get("duration") or 0.0),
        str(entry.get("media_name") or history_key),
        str(entry.get("display_name") or history_key),
        entry.get("episode_display_name"),
    )
    if conn is not None:
        conn.execute(query, params)
    else:
        try:
            with db_connection() as c:
                c.execute(query, params)
        except Exception as e:
            debug_log(f"db_save_watch_history_entry error: {e}")


def db_remove_watch_history_entry(history_key, conn=None):
    if not history_key:
        return
    query = "DELETE FROM watch_history WHERE history_key = ?"
    if conn is not None:
        conn.execute(query, (str(history_key),))
    else:
        try:
            with db_connection() as c:
                c.execute(query, (str(history_key),))
        except Exception as e:
            debug_log(f"db_remove_watch_history_entry error: {e}")


def db_save_episode_watch_status(anime_name, episode, entry, conn=None):
    if not anime_name or not episode or not isinstance(entry, dict):
        return
    query = """
        INSERT OR REPLACE INTO episode_watch_status (
            anime_name, episode, watched, progress, duration, current_seconds, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        str(anime_name),
        str(episode),
        1 if entry.get("watched") else 0,
        float(entry.get("progress") or 0.0),
        float(entry.get("duration") or 0.0),
        float(entry.get("current_seconds") or 0.0),
        str(entry.get("updated_at") or datetime.now().isoformat()),
    )
    if conn is not None:
        conn.execute(query, params)
    else:
        try:
            with db_connection() as c:
                c.execute(query, params)
        except Exception as e:
            debug_log(f"db_save_episode_watch_status error: {e}")


def db_sync_all_watch_history(history_dict, conn=None):
    if not isinstance(history_dict, dict):
        return
    def _do_sync(c):
        for k, v in history_dict.items():
            if isinstance(v, dict):
                db_save_watch_history_entry(k, v, conn=c)
    if conn is not None:
        _do_sync(conn)
    else:
        try:
            with db_connection() as c:
                _do_sync(c)
        except Exception as e:
            debug_log(f"db_sync_all_watch_history error: {e}")


def db_sync_all_watch_status(status_dict, conn=None):
    if not isinstance(status_dict, dict):
        return
    def _do_sync(c):
        for anime, eps in status_dict.items():
            if isinstance(eps, dict):
                for ep, data in eps.items():
                    if isinstance(data, dict):
                        db_save_episode_watch_status(anime, ep, data, conn=c)
    if conn is not None:
        _do_sync(conn)
    else:
        try:
            with db_connection() as c:
                _do_sync(c)
        except Exception as e:
            debug_log(f"db_sync_all_watch_status error: {e}")


def db_remove_episode_watch_status(anime_name, episode=None, conn=None):
    if not anime_name:
        return
    if episode:
        query = "DELETE FROM episode_watch_status WHERE anime_name = ? AND episode = ?"
        params = (str(anime_name), str(episode))
    else:
        query = "DELETE FROM episode_watch_status WHERE anime_name = ?"
        params = (str(anime_name),)

    if conn is not None:
        conn.execute(query, params)
    else:
        try:
            with db_connection() as c:
                c.execute(query, params)
        except Exception as e:
            debug_log(f"db_remove_episode_watch_status error: {e}")


def db_cleanup_watch_data_for_anime(anime_name, safe_name=None, conn=None):
    if not anime_name:
        return
    safe_name = safe_name or safe_cache_name(anime_name)
    def _do_cleanup(c):
        c.execute(
            "DELETE FROM episode_watch_status WHERE anime_name = ? OR anime_name = ?",
            (str(anime_name), str(safe_name))
        )
        c.execute(
            """
            DELETE FROM watch_history
            WHERE history_key = ? OR history_key = ?
               OR media_name = ? OR media_name = ?
            """,
            (str(anime_name), str(safe_name), str(anime_name), str(safe_name))
        )
    if conn is not None:
        _do_cleanup(conn)
    else:
        try:
            with db_connection() as c:
                _do_cleanup(c)
        except Exception as e:
            debug_log(f"db_cleanup_watch_data_for_anime error: {e}")


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

    try:
        with db_connection() as conn:
            conn.execute(
                "DELETE FROM episode_watch_status WHERE anime_name = ? OR anime_name = ?",
                (str(anime_name), str(safe_name))
            )
            conn.execute(
                """
                DELETE FROM watch_history
                WHERE history_key = ? OR history_key = ?
                   OR media_name = ? OR media_name = ?
                """,
                (str(anime_name), str(safe_name), str(anime_name), str(safe_name))
            )
    except Exception as e:
        debug_log(f"cleanup_watch_data_for_anime SQLite cleanup error: {e}")


def migrate_watch_data_from_json_to_db(conn=None):
    """Migrasi data satu kali dari file JSON ke tabel SQLite jika tabel kosong."""
    def _migrate(c):
        try:
            count_history = c.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
        except sqlite3.OperationalError:
            return
        if count_history == 0 and os.path.exists(_constants.WATCH_HISTORY_FILE):
            data, err = read_json_dict_file(_constants.WATCH_HISTORY_FILE, "Watch history migration")
            if not err and isinstance(data, dict) and data:
                db_sync_all_watch_history(data, conn=c)
                app_log(f"Migrated {len(data)} watch history entries from JSON to SQLite.", "INFO")

        try:
            count_status = c.execute("SELECT COUNT(*) FROM episode_watch_status").fetchone()[0]
        except sqlite3.OperationalError:
            return
        if count_status == 0 and os.path.exists(_constants.WATCH_STATUS_FILE):
            data, err = read_json_dict_file(_constants.WATCH_STATUS_FILE, "Watch status migration")
            if not err and isinstance(data, dict) and data:
                total_episodes = sum(len(eps) for eps in data.values() if isinstance(eps, dict))
                db_sync_all_watch_status(data, conn=c)
                app_log(f"Migrated {total_episodes} episode status records from JSON to SQLite.", "INFO")

    if conn is not None:
        _migrate(conn)
    else:
        try:
            with db_connection() as c:
                _migrate(c)
        except Exception as e:
            app_log(f"Watch data migration to SQLite failed: {e}", "WARN")
