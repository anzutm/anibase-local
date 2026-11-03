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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mal_pending_scrobbles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anime_name TEXT NOT NULL,
                episode TEXT NOT NULL,
                ep_num INTEGER NOT NULL,
                mal_id INTEGER,
                status TEXT NOT NULL DEFAULT 'watching',
                attempts INTEGER DEFAULT 0,
                last_attempt_at REAL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                UNIQUE (anime_name, episode)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mal_pending_created ON mal_pending_scrobbles(created_at ASC)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS anime_user_reviews (
                anime_name TEXT PRIMARY KEY,
                mal_id INTEGER,
                score INTEGER DEFAULT 0,
                comments TEXT,
                updated_at REAL NOT NULL
            )
        """)

        # Inisialisasi virtual table FTS5 untuk pencarian teks penuh (Full-Text Search)
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS anime_fts USING fts5(
                    name,
                    title,
                    genres,
                    synonyms,
                    description,
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
        except sqlite3.OperationalError as e:
            app_log(f"FTS5 virtual table initialization note: {e}", "INFO")


def sanitize_fts5_query(raw_query):
    """Membersihkan dan memformat query pengguna agar aman dan optimal untuk SQLite FTS5 prefix search."""
    import re
    if not raw_query or not isinstance(raw_query, str):
        return None
    words = re.findall(r'[^\W_]+', raw_query.strip(), re.UNICODE)
    if not words:
        return None
    return ' '.join(f'"{w}"*' for w in words)


def sync_anime_fts(conn, name, title=None, genres=None, synonyms=None, description=None):
    """Menyinkronkan atau memperbarui indeks FTS5 untuk satu anime."""
    if not name:
        return False
    try:
        conn.execute("DELETE FROM anime_fts WHERE name = ?", (name,))

        genres_str = ""
        if isinstance(genres, (list, tuple, set)):
            genres_str = ", ".join(str(g) for g in genres if g)
        elif isinstance(genres, str):
            genres_str = genres

        synonyms_str = ""
        if isinstance(synonyms, (list, tuple, set)):
            synonyms_str = " ".join(str(s) for s in synonyms if s)
        elif isinstance(synonyms, str):
            synonyms_str = synonyms

        clean_desc = str(description or "").strip()
        if clean_desc and ("<" in clean_desc and ">" in clean_desc):
            import re
            clean_desc = re.sub(r'<[^>]+>', ' ', clean_desc).strip()

        conn.execute("""
            INSERT INTO anime_fts (name, title, genres, synonyms, description)
            VALUES (?, ?, ?, ?, ?)
        """, (
            name,
            title or name,
            genres_str,
            synonyms_str,
            clean_desc
        ))
        return True
    except sqlite3.Error as e:
        debug_log(f"FTS sync error for {name}: {e}")
        return False


def delete_anime_fts(conn, name):
    """Menghapus entri anime dari indeks FTS5."""
    if not name:
        return False
    try:
        conn.execute("DELETE FROM anime_fts WHERE name = ?", (name,))
        return True
    except sqlite3.Error as e:
        debug_log(f"FTS delete error for {name}: {e}")
        return False


def search_anime_fts(query, limit=20, conn=None):
    """
    Melakukan pencarian Full-Text Search pada anime_fts dengan perankingan BM25.
    Bobot: Title (10.0), Name (5.0), Synonyms (3.0), Genres (2.0), Description (1.0).
    Mengembalikan daftar dict: [{"name": name, "rank": rank}, ...]
    """
    clean_query = sanitize_fts5_query(query)
    if not clean_query:
        return []

    sql = """
        SELECT name, bm25(anime_fts, 5.0, 10.0, 2.0, 3.0, 1.0) as rank
        FROM anime_fts
        WHERE anime_fts MATCH ?
        ORDER BY rank ASC
        LIMIT ?
    """
    try:
        if conn is not None:
            rows = conn.execute(sql, (clean_query, limit)).fetchall()
        else:
            with db_connection() as c:
                rows = c.execute(sql, (clean_query, limit)).fetchall()
        return [{"name": r[0], "rank": r[1]} for r in rows if r and r[0]]
    except sqlite3.Error as e:
        debug_log(f"FTS search error for '{query}': {e}")
        return []


def rebuild_anime_fts(conn=None):
    """Membangun ulang seluruh indeks FTS5 dari database dan cache metadata."""
    from anibase.metadata import get_cached_metadata_only
    def _do_rebuild(c):
        rows = c.execute("SELECT name, genres FROM anime_library").fetchall()
        c.execute("DELETE FROM anime_fts")
        count = 0
        for row in rows:
            name = row[0]
            meta = get_cached_metadata_only(name) or {}
            genres = meta.get("genres") or row[1]
            title = meta.get("title") or name
            synonyms = meta.get("synonyms")
            description = meta.get("description")
            if sync_anime_fts(c, name, title=title, genres=genres, synonyms=synonyms, description=description):
                count += 1
        return count

    try:
        if conn is not None:
            return _do_rebuild(conn)
        with db_connection() as c:
            return _do_rebuild(c)
    except sqlite3.Error as e:
        app_log(f"FTS rebuild failed: {e}", "WARN")
        return 0


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
