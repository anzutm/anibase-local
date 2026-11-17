# anibase/db.py
"""Manajemen koneksi basis data SQLite dan skema tabel untuk AniBase."""

import os
import time
import sqlite3
from contextlib import contextmanager
import anibase.constants as _constants
from anibase.logging import app_log, debug_log


@contextmanager
def db_connection(path=None):
    """Context manager untuk koneksi SQLite dengan auto-commit, rollback, timeout aman, dan WAL optimasi."""
    target_path = path or _constants.DB_PATH
    conn = sqlite3.connect(target_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        pass
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ongoing_watchlist (
                anime_name TEXT PRIMARY KEY,
                samehadaku_url TEXT,
                last_known_episode REAL DEFAULT 0,
                latest_samehadaku_ep REAL DEFAULT 0,
                last_checked_at REAL DEFAULT 0,
                is_tracked INTEGER DEFAULT 1,
                source TEXT DEFAULT 'auto',
                created_at REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS episode_release_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anime_name TEXT NOT NULL,
                episode_number TEXT NOT NULL,
                episode_title TEXT,
                episode_url TEXT,
                thumbnail TEXT,
                released_on TEXT,
                is_read INTEGER DEFAULT 0,
                is_downloaded INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                UNIQUE (anime_name, episode_number)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_notif_created ON episode_release_notifications(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_notif_read ON episode_release_notifications(is_read)")

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


# =========================================================================
# Ongoing Watchlist & Episode Release Notifications Database Operations
# =========================================================================

def get_ongoing_watchlist(active_only=False, conn=None):
    """Mengambil daftar anime yang masuk dalam watchlist ongoing pengguna."""
    sql = "SELECT anime_name, samehadaku_url, last_known_episode, latest_samehadaku_ep, last_checked_at, is_tracked, source, created_at FROM ongoing_watchlist"
    if active_only:
        sql += " WHERE is_tracked = 1"
    sql += " ORDER BY anime_name ASC"

    def _query(c):
        cursor = c.execute(sql)
        rows = cursor.fetchall()
        return [
            {
                "anime_name": r[0],
                "samehadaku_url": r[1] or "",
                "last_known_episode": r[2] or 0,
                "latest_samehadaku_ep": r[3] or 0,
                "last_checked_at": r[4] or 0,
                "is_tracked": bool(r[5]),
                "source": r[6] or "auto",
                "created_at": r[7] or 0,
            }
            for r in rows
        ]

    try:
        if conn is not None:
            return _query(conn)
        with db_connection() as c:
            return _query(c)
    except sqlite3.Error as e:
        app_log(f"Error fetching ongoing watchlist: {e}", "ERROR")
        return []


def upsert_ongoing_watchlist(anime_name, samehadaku_url=None, is_tracked=1, source="auto", last_known_episode=0, latest_samehadaku_ep=0, conn=None):
    """Menambahkan atau memperbarui anime dalam watchlist ongoing."""
    if not anime_name:
        return False
    now = time.time()
    sql = """
        INSERT INTO ongoing_watchlist (
            anime_name, samehadaku_url, last_known_episode, latest_samehadaku_ep, is_tracked, source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(anime_name) DO UPDATE SET
            samehadaku_url = COALESCE(excluded.samehadaku_url, ongoing_watchlist.samehadaku_url),
            last_known_episode = CASE WHEN excluded.last_known_episode > 0 THEN excluded.last_known_episode ELSE ongoing_watchlist.last_known_episode END,
            latest_samehadaku_ep = CASE WHEN excluded.latest_samehadaku_ep > 0 THEN excluded.latest_samehadaku_ep ELSE ongoing_watchlist.latest_samehadaku_ep END,
            is_tracked = CASE
                WHEN excluded.source = 'manual' THEN excluded.is_tracked
                ELSE ongoing_watchlist.is_tracked
            END
    """
    def _exec(c):
        c.execute(sql, (
            anime_name,
            samehadaku_url,
            last_known_episode,
            latest_samehadaku_ep,
            1 if is_tracked else 0,
            source,
            now
        ))
        return True

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error upserting ongoing anime '{anime_name}': {e}", "ERROR")
        return False


def update_ongoing_watchlist_check(anime_name, latest_samehadaku_ep=None, last_known_episode=None, samehadaku_url=None, conn=None):
    """Memperbarui informasi hasil pengecekan rilis episode terbaru untuk anime."""
    if not anime_name:
        return False
    now = time.time()
    updates = ["last_checked_at = ?"]
    params = [now]
    if latest_samehadaku_ep is not None:
        updates.append("latest_samehadaku_ep = ?")
        params.append(float(latest_samehadaku_ep))
    if last_known_episode is not None:
        updates.append("last_known_episode = ?")
        params.append(float(last_known_episode))
    if samehadaku_url:
        updates.append("samehadaku_url = ?")
        params.append(samehadaku_url)

    params.append(anime_name)
    sql = f"UPDATE ongoing_watchlist SET {', '.join(updates)} WHERE anime_name = ?"

    def _exec(c):
        c.execute(sql, params)
        return True

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error updating check for '{anime_name}': {e}", "ERROR")
        return False


def set_ongoing_watchlist_tracked(anime_name, is_tracked, conn=None):
    """Mengaktifkan atau menonaktifkan pemantauan ongoing untuk anime."""
    if not anime_name:
        return False
    sql = "UPDATE ongoing_watchlist SET is_tracked = ? WHERE anime_name = ?"
    def _exec(c):
        c.execute(sql, (1 if is_tracked else 0, anime_name))
        return True

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error setting tracked state for '{anime_name}': {e}", "ERROR")
        return False


def delete_ongoing_watchlist(anime_name, conn=None):
    """Menghapus anime dari daftar pantau ongoing."""
    if not anime_name:
        return False
    sql = "DELETE FROM ongoing_watchlist WHERE anime_name = ?"
    def _exec(c):
        c.execute(sql, (anime_name,))
        return True

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error deleting ongoing anime '{anime_name}': {e}", "ERROR")
        return False


def save_episode_notification(anime_name, episode_number, episode_title=None, episode_url=None, thumbnail=None, released_on=None, conn=None):
    """Menyimpan notifikasi episode baru jika belum pernah dicatat sebelumnya.
    
    Mengembalikan id notifikasi jika baru dibuat, atau None jika sudah ada (duplikat).
    """
    if not anime_name or not episode_number:
        return None
    now = time.time()
    sql = """
        INSERT OR IGNORE INTO episode_release_notifications (
            anime_name, episode_number, episode_title, episode_url, thumbnail, released_on, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    def _exec(c):
        cur = c.execute(sql, (
            anime_name,
            str(episode_number).strip(),
            episode_title or "",
            episode_url or "",
            thumbnail or "",
            released_on or "",
            now
        ))
        if cur.rowcount > 0:
            return cur.lastrowid
        return None

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error saving episode notification for {anime_name} ep {episode_number}: {e}", "ERROR")
        return None


def get_episode_notifications(unread_only=False, limit=30, conn=None):
    """Mengambil daftar notifikasi episode baru."""
    sql = """
        SELECT id, anime_name, episode_number, episode_title, episode_url, thumbnail, released_on, is_read, is_downloaded, created_at
        FROM episode_release_notifications
    """
    if unread_only:
        sql += " WHERE is_read = 0"
    sql += " ORDER BY created_at DESC LIMIT ?"

    def _query(c):
        cursor = c.execute(sql, (max(1, int(limit)),))
        rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "anime_name": r[1],
                "episode_number": r[2],
                "episode_title": r[3] or "",
                "episode_url": r[4] or "",
                "thumbnail": r[5] or "",
                "released_on": r[6] or "",
                "is_read": bool(r[7]),
                "is_downloaded": bool(r[8]),
                "created_at": r[9],
            }
            for r in rows
        ]

    try:
        if conn is not None:
            return _query(conn)
        with db_connection() as c:
            return _query(c)
    except sqlite3.Error as e:
        app_log(f"Error fetching episode notifications: {e}", "ERROR")
        return []


def mark_notification_read(notification_id=None, mark_all=False, conn=None):
    """Menandai satu atau seluruh notifikasi sebagai sudah dibaca."""
    if mark_all:
        sql = "UPDATE episode_release_notifications SET is_read = 1 WHERE is_read = 0"
        params = ()
    elif notification_id is not None:
        sql = "UPDATE episode_release_notifications SET is_read = 1 WHERE id = ?"
        params = (notification_id,)
    else:
        return 0

    def _exec(c):
        cur = c.execute(sql, params)
        return cur.rowcount

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error marking notification read: {e}", "ERROR")
        return 0


def mark_notification_downloaded(anime_name, episode_number, conn=None):
    """Menandai episode sebagai sudah diunduh (dan otomatis dibaca)."""
    if not anime_name or not episode_number:
        return 0
    ep_str = str(episode_number).strip()
    sql = """
        UPDATE episode_release_notifications
        SET is_downloaded = 1, is_read = 1
        WHERE (anime_name = ? OR anime_name LIKE ?) AND (episode_number = ? OR episode_number = ?)
    """
    clean_ep = str(int(float(ep_str))) if ep_str.replace('.', '', 1).isdigit() else ep_str

    def _exec(c):
        cur = c.execute(sql, (anime_name, f"%{anime_name}%", ep_str, clean_ep))
        return cur.rowcount

    try:
        if conn is not None:
            return _exec(conn)
        with db_connection() as c:
            return _exec(c)
    except sqlite3.Error as e:
        app_log(f"Error marking notification downloaded for {anime_name} {episode_number}: {e}", "ERROR")
        return 0


def get_unread_notification_count(conn=None):
    """Menghitung total notifikasi episode baru yang belum dibaca."""
    sql = "SELECT COUNT(*) FROM episode_release_notifications WHERE is_read = 0"
    try:
        if conn is not None:
            return conn.execute(sql).fetchone()[0]
        with db_connection() as c:
            return c.execute(sql).fetchone()[0]
    except sqlite3.Error as e:
        app_log(f"Error getting unread notification count: {e}", "ERROR")
        return 0
