# anibase/db.py
"""Manajemen koneksi basis data SQLite dan skema tabel untuk AniBase."""

import os
import sqlite3
from contextlib import contextmanager
import anibase.constants as _constants
from anibase.logging import app_log, debug_log


@contextmanager
def db_connection(path=None):
    """Context manager untuk koneksi SQLite dengan auto-commit dan rollback."""
    target_path = path or _constants.DB_PATH
    conn = sqlite3.connect(target_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    """Menginisialisasi direktori cache dan skema tabel SQLite (anime_library, watch_history, episode_watch_status)."""
    for folder in (
        _constants.POSTER_CACHE,
        _constants.THUMBNAIL_CACHE,
        _constants.METADATA_CACHE,
        _constants.EPISODE_CACHE,
        _constants.BANNER_CACHE,
        _constants.SUBTITLE_CACHE,
        _constants.CHARACTER_CACHE,
        _constants.SEIYUU_CACHE,
    ):
        os.makedirs(folder, exist_ok=True)

    target_path = db_path or _constants.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

    with db_connection(target_path) as conn:
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_history (
                history_key TEXT PRIMARY KEY,
                episode TEXT NOT NULL,
                episode_num REAL,
                updated_at TEXT NOT NULL,
                time_str TEXT,
                last_seconds REAL DEFAULT 0,
                duration REAL DEFAULT 0,
                media_name TEXT,
                display_name TEXT,
                episode_display_name TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_history_updated ON watch_history(updated_at DESC)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS episode_watch_status (
                anime_name TEXT NOT NULL,
                episode TEXT NOT NULL,
                watched INTEGER DEFAULT 0,
                progress REAL DEFAULT 0,
                duration REAL DEFAULT 0,
                current_seconds REAL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (anime_name, episode)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_episode_status_anime ON episode_watch_status(anime_name)")


def count_library_rows(conn=None):
    """Menghitung total baris di tabel anime_library."""
    try:
        if conn is not None:
            return conn.execute("SELECT COUNT(*) FROM anime_library").fetchone()[0]
        with db_connection() as c:
            return c.execute("SELECT COUNT(*) FROM anime_library").fetchone()[0]
    except sqlite3.Error as e:
        app_log(f"Unable to count library rows before sync: {e}", "WARN")
        return 0
