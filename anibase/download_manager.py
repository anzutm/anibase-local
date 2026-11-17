# anibase/download_manager.py
"""Manajer unduhan latar belakang untuk AniBase.

Mendukung pengunduhan berkas video (seperti dari Pixeldrain API) secara asynchronous
dengan pelacakan progres (byte, persen, kecepatan bps), pembatalan, penyimpanan ke
folder anime library / auto import, dan pemindaian library otomatis saat selesai.
"""

import os
import re
import time
import secrets
import threading
from typing import Optional, Dict, Any, List
from collections import Counter

import requests
from anibase.constants import CACHE_DIR, VIDEO_EXTENSIONS
from anibase.logging import app_log, debug_log
from anibase.settings import load_settings, get_settings_library_paths
from anibase.scanner import find_media_path, clear_media_path_cache
from anibase.watcher import clean_auto_import_folder_name, unique_destination_path
from anibase.integrations.samehadaku import (
    get_pixeldrain_file_info,
    get_samehadaku_download_links,
    resolve_mediafire_direct_url,
)
from anibase.media import parse_episode_identity

DEFAULT_CHUNK_SIZE = 128 * 1024  # 128 KB
MAX_STORED_TASKS = 100

_TASKS_LOCK = threading.Lock()
_DOWNLOAD_TASKS: Dict[str, Dict[str, Any]] = {}
_CANCEL_EVENTS: Dict[str, threading.Event] = {}
_PAUSE_EVENTS: Dict[str, threading.Event] = {}
_MAX_CONCURRENT_OVERRIDE: Optional[int] = None


def get_max_concurrent_downloads(settings: Optional[Dict[str, Any]] = None) -> int:
    """Mengambil batas kuota unduhan paralel bersamaan dari pengaturan (default: 2)."""
    global _MAX_CONCURRENT_OVERRIDE
    if _MAX_CONCURRENT_OVERRIDE is not None:
        return _MAX_CONCURRENT_OVERRIDE
    settings = settings or load_settings()
    try:
        val = int(settings.get("download_max_concurrent", 2))
        return max(1, min(10, val))
    except (ValueError, TypeError):
        return 2


def set_max_concurrent_downloads(count: int, save: bool = True) -> int:
    """Mengatur kuota unduhan paralel bersamaan dan langsung memproses antrean unduhan."""
    global _MAX_CONCURRENT_OVERRIDE
    val = max(1, min(10, int(count or 2)))
    _MAX_CONCURRENT_OVERRIDE = val
    if save:
        try:
            from anibase.settings import save_settings
            st = load_settings()
            st["download_max_concurrent"] = val
            save_settings(st)
        except Exception as e:
            app_log(f"Failed to persist download_max_concurrent: {e}", "DEBUG")
    _process_download_queue()
    return val


def get_queue_overview() -> Dict[str, Any]:
    """Mengambil status ringkasan antrean unduhan."""
    with _TASKS_LOCK:
        max_concurrent = get_max_concurrent_downloads()
        active_count = sum(1 for t in _DOWNLOAD_TASKS.values() if t.get("status") == "downloading")
        queued_count = sum(1 for t in _DOWNLOAD_TASKS.values() if t.get("status") == "queued")
        paused_count = sum(1 for t in _DOWNLOAD_TASKS.values() if t.get("status") == "paused")

    return {
        "max_concurrent": max_concurrent,
        "active_count": active_count,
        "queued_count": queued_count,
        "paused_count": paused_count,
    }


def _process_download_queue():
    """Memeriksa antrean dan memulai tugas 'queued' sesuai kuota unduhan paralel."""
    with _TASKS_LOCK:
        active_count = sum(1 for t in _DOWNLOAD_TASKS.values() if t.get("status") == "downloading")
        max_allowed = get_max_concurrent_downloads()
        slots_available = max_allowed - active_count

        if slots_available <= 0:
            return

        queued_tasks = [
            t for t in _DOWNLOAD_TASKS.values()
            if t.get("status") == "queued"
        ]
        queued_tasks.sort(key=lambda x: x.get("created_at", 0))

        for task in queued_tasks[:slots_available]:
            task_id = task["task_id"]
            task["status"] = "downloading"
            task["updated_at"] = time.time()

            cancel_event = _CANCEL_EVENTS.get(task_id)
            if not cancel_event:
                cancel_event = threading.Event()
                _CANCEL_EVENTS[task_id] = cancel_event

            pause_event = _PAUSE_EVENTS.get(task_id)
            if not pause_event:
                pause_event = threading.Event()
                _PAUSE_EVENTS[task_id] = pause_event

            worker = threading.Thread(
                target=_download_worker,
                args=(
                    task_id,
                    task["url"],
                    task["save_path"],
                    task["temp_path"],
                    cancel_event,
                    pause_event,
                    task.get("anime_name", ""),
                    task.get("matched_anime")
                ),
                daemon=True,
                name=f"DownloadWorker-{task_id}"
            )
            worker.start()
            app_log(
                f"Dispatched queued download task {task_id}: {task['filename']} ({active_count + 1}/{max_allowed} active)",
                "INFO"
            )
            active_count += 1

_BATCH_LOCK = threading.Lock()
_BATCH_TASKS: Dict[str, Dict[str, Any]] = {}
_BATCH_CANCEL_EVENTS: Dict[str, threading.Event] = {}


def sanitize_filename(filename: str) -> str:
    """Membersihkan nama berkas dari karakter terlarang sistem berkas."""
    clean = re.sub(r'[<>:"/\\|?*]', '_', filename or '').strip()
    return clean or f"download_{secrets.token_hex(4)}"


def find_matching_library_anime(
    anime_name: str,
    settings: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Mencari folder anime yang cocok di library lokal pengguna berdasarkan judul.

    Mendukung:
    1. Pencocokan langsung (exact/normalized) via find_media_path.
    2. Pemetaan manual pengguna (auto_import_mappings dari pengaturan).
    3. Riwayat auto-import terakhir (auto_import_recent_imports dari pengaturan).
    4. Pencarian FTS5 SQLite pada judul resmi, Romaji, English, dan sinonim (anime_fts).
    5. Pencocokan fuzzy dan token overlap terhadap seluruh folder anime di library (get_anime_folder_index).
    """
    if not anime_name or not str(anime_name).strip():
        return None

    settings = settings or load_settings()

    raw = str(anime_name).strip()
    clean_title = re.sub(r'[\s\-–—:_]+$', '', raw).strip()
    title_no_ep = re.sub(r'(?i)\b(?:episode|episodes|eps?|e)\s*\d+.*$', '', clean_title).strip()
    title_no_ep = re.sub(r'[\s\-–—:_]+$', '', title_no_ep).strip()

    from anibase.watcher import normalize_title_for_match, get_match_key
    norm_title = normalize_title_for_match(title_no_ep or clean_title)
    key = get_match_key(norm_title) or get_match_key(clean_title)

    # 1. Cek pencarian langsung ke folder media (exact/normalized)
    for cand in (raw, clean_title, title_no_ep, norm_title):
        if cand:
            path = find_media_path(cand)
            if path and os.path.isdir(path):
                return {
                    "name": os.path.basename(path),
                    "path": path,
                    "score": 1.0,
                    "method": "direct",
                }

    # 2. Cek auto_import_mappings dari pengaturan pengguna
    mappings = settings.get("auto_import_mappings", {})
    if mappings and isinstance(mappings, dict):
        clean_key = get_match_key(clean_title)
        raw_key = get_match_key(raw)
        for map_src, map_dst in mappings.items():
            src_key = get_match_key(map_src)
            if src_key and src_key in (key, clean_key, raw_key):
                path = find_media_path(map_dst)
                if path and os.path.isdir(path):
                    return {
                        "name": map_dst,
                        "path": path,
                        "score": 1.0,
                        "method": "mapping",
                    }

    # 3. Cek riwayat auto-import terakhir
    recent_imports = settings.get("auto_import_recent_imports", [])
    if recent_imports and isinstance(recent_imports, list):
        for item in recent_imports:
            if not isinstance(item, dict):
                continue
            item_clean = item.get("clean_title") or ""
            item_anime = item.get("anime") or ""
            if item_anime and (get_match_key(item_clean) == key or get_match_key(item_clean) == get_match_key(clean_title)):
                path = find_media_path(item_anime)
                if path and os.path.isdir(path):
                    return {
                        "name": item_anime,
                        "path": path,
                        "score": 0.99,
                        "method": "recent_import",
                    }

    # 4. Cek pencarian FTS5 SQLite (mendukung judul Inggris vs Romaji dan sinonim AniList)
    try:
        from anibase.db import search_anime_fts
        for fts_query in (title_no_ep, clean_title, norm_title):
            if fts_query and len(fts_query) >= 3:
                fts_matches = search_anime_fts(fts_query, limit=3)
                for m in fts_matches:
                    m_name = m.get("name")
                    if m_name:
                        path = find_media_path(m_name)
                        if path and os.path.isdir(path):
                            return {
                                "name": m_name,
                                "path": path,
                                "score": 0.98,
                                "method": "fts",
                            }
    except Exception as fts_err:
        debug_log(f"FTS lookup during anime folder match: {fts_err}")

    # 5. Pencocokan fuzzy & similarity terhadap seluruh folder anime di library
    from anibase.scanner import get_anime_folder_index
    import difflib
    folders = get_anime_folder_index()
    best_folder = None
    best_score = 0.0
    best_method = ""

    for f in folders:
        name = f.get("name") or ""
        f_norm = normalize_title_for_match(name)
        f_key = get_match_key(f_norm) or get_match_key(name)
        if not f_key:
            continue

        # Exact key match
        if key == f_key or name.casefold() in (clean_title.casefold(), title_no_ep.casefold()):
            return {
                "name": name,
                "path": f["path"],
                "score": 1.0,
                "method": "key_exact",
            }

        # Substring containment (panjang >= 4 agar tidak false positive kata pendek)
        if len(key) >= 4 and len(f_key) >= 4:
            if key in f_key or f_key in key:
                score = 0.95
                if score > best_score:
                    best_score = score
                    best_folder = f
                    best_method = "containment"
                    continue

        # Word token overlap (Jaccard / subset score)
        q_words = set(norm_title.casefold().split())
        f_words = set(f_norm.casefold().split())
        word_score = 0.0
        if q_words and f_words:
            overlap = q_words & f_words
            if overlap:
                subset_score = len(overlap) / min(len(q_words), len(f_words))
                jaccard = len(overlap) / len(q_words | f_words)
                word_score = max(subset_score * 0.9, jaccard)

        # SequenceMatcher ratio
        ratio = difflib.SequenceMatcher(None, key, f_key).ratio()
        cand_score = max(word_score, ratio)
        if cand_score > best_score:
            best_score = cand_score
            best_folder = f
            best_method = "similarity"

    if best_folder and best_score >= 0.72:
        return {
            "name": best_folder["name"],
            "path": best_folder["path"],
            "score": best_score,
            "method": best_method,
        }

    return None


def resolve_download_destination(anime_name: str) -> Dict[str, Any]:
    """Menentukan lokasi penyimpanan unduhan dengan mencocokkan ke library anime yang ada.

    Jika ditemukan anime yang cocok di library (misal folder 'Kashita Maryoku'),
    akan mengembalikan folder anime tersebut.
    Jika belum ada anime yang cocok, akan membuat folder baru di `ongoing_path`
    atau library default dengan nama bersih (tanpa sisa karakter seperti '-' atau 'Episode X').
    """
    settings = load_settings()

    # 1. Coba cocokkan ke anime library yang sudah ada
    matched = find_matching_library_anime(anime_name, settings=settings)
    if matched and matched.get("path") and os.path.isdir(matched["path"]):
        return {
            "folder": matched["path"],
            "matched": True,
            "matched_anime": matched["name"],
            "match_method": matched.get("method", "library"),
            "score": matched.get("score", 1.0),
        }

    # 2. Tidak ditemukan yang cocok: bersihkan nama anime untuk folder baru
    raw = str(anime_name or "").strip()
    clean_raw = re.sub(r'[\s\-–—:_]+$', '', raw).strip()
    clean_no_ep = re.sub(r'(?i)\b(?:episode|episodes|eps?|e)\s*\d+.*$', '', clean_raw).strip()
    clean_no_ep = re.sub(r'[\s\-–—:_]+$', '', clean_no_ep).strip()
    folder_basename = clean_auto_import_folder_name(clean_no_ep or clean_raw) or "Unknown Anime"

    # Prioritas folder baru:
    # 1. ongoing_path
    ongoing = settings.get("ongoing_path", "").strip()
    if ongoing and os.path.isdir(ongoing):
        target = os.path.join(ongoing, folder_basename)
        os.makedirs(target, exist_ok=True)
        return {
            "folder": target,
            "matched": False,
            "matched_anime": folder_basename,
            "match_method": "new_ongoing",
            "score": 0.0,
        }

    # 2. auto_import_destination_root
    dest_root = settings.get("auto_import_destination_root", "").strip()
    if dest_root and os.path.isdir(dest_root):
        target = os.path.join(dest_root, folder_basename)
        os.makedirs(target, exist_ok=True)
        return {
            "folder": target,
            "matched": False,
            "matched_anime": folder_basename,
            "match_method": "new_auto_import",
            "score": 0.0,
        }

    # 3. Folder library pertama dari library_paths
    library_paths = get_settings_library_paths(settings)
    for lib_path in library_paths:
        if lib_path and os.path.isdir(lib_path):
            target = os.path.join(lib_path, folder_basename)
            os.makedirs(target, exist_ok=True)
            return {
                "folder": target,
                "matched": False,
                "matched_anime": folder_basename,
                "match_method": "new_library",
                "score": 0.0,
            }

    # 4. auto_import_downloads_path
    dl_path = settings.get("auto_import_downloads_path", "").strip()
    if dl_path and os.path.isdir(dl_path):
        target = os.path.join(dl_path, folder_basename)
        os.makedirs(target, exist_ok=True)
        return {
            "folder": target,
            "matched": False,
            "matched_anime": folder_basename,
            "match_method": "new_downloads_path",
            "score": 0.0,
        }

    # 5. Fallback direktori Downloads pengguna
    fallback = os.path.join(os.path.expanduser("~"), "Downloads", "AniBase", folder_basename)
    os.makedirs(fallback, exist_ok=True)
    return {
        "folder": fallback,
        "matched": False,
        "matched_anime": folder_basename,
        "match_method": "fallback_user_downloads",
        "score": 0.0,
    }


def resolve_download_folder(anime_name: str) -> str:
    """Menentukan folder penyimpanan yang paling tepat untuk anime yang diunduh.

    Prioritas:
    1. Folder anime yang sudah ada di library via pencocokan cerdas (`find_matching_library_anime`).
    2. `ongoing_path` dari pengaturan jika valid.
    3. `auto_import_destination_root` dari pengaturan jika valid.
    4. Folder library pertama dari `library_paths`.
    5. `auto_import_downloads_path` dari pengaturan jika valid.
    6. Direktori Downloads pengguna OS (~/Downloads/AniBase).
    """
    return resolve_download_destination(anime_name)["folder"]


def adapt_filename_to_library_pattern(
    folder_path: str,
    episode: Any,
    default_filename: str
) -> str:
    """Menyesuaikan nama berkas unduhan baru dengan pola penamaan berkas video yang sudah ada di folder tujuan.

    Jika folder tujuan sudah memiliki berkas episode (misal `[LENDRIVE]_Anime_-_01_[1080p].mkv`),
    fungsi ini mendeteksi pola prefix, format/padding nomor episode (misal 2 digit '07'), dan suffix/tag kualitas,
    sehingga berkas baru otomatis seragam dan rapi dengan berkas-berkas sebelumnya di folder tersebut.
    """
    if not folder_path or not os.path.isdir(folder_path):
        return default_filename

    try:
        entries = os.listdir(folder_path)
    except OSError:
        return default_filename

    video_files = [
        f for f in entries
        if os.path.isfile(os.path.join(folder_path, f))
        and os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
        and not f.endswith(".part")
    ]
    if not video_files:
        return default_filename

    target_num = None
    is_int = True
    clean_ep = str(episode or "").strip()
    if clean_ep:
        try:
            target_num = float(clean_ep)
            is_int = target_num.is_integer()
        except (ValueError, TypeError):
            m = re.search(r'\b(?:ep|episode|e)?\s*(\d+(?:\.\d+)?)\b', clean_ep, re.IGNORECASE)
            if m:
                target_num = float(m.group(1))
                is_int = target_num.is_integer()

    if target_num is None:
        ident = parse_episode_identity(default_filename)
        if ident.get("kind") == "episode" and ident.get("number") is not None:
            target_num = float(ident["number"])
            is_int = target_num.is_integer()
        else:
            m = re.search(r'\b(?:ep|episode|e)?\s*(\d+(?:\.\d+)?)\b', default_filename, re.IGNORECASE)
            if m:
                target_num = float(m.group(1))
                is_int = target_num.is_integer()

    if target_num is None:
        return default_filename

    patterns = []
    for f in video_files:
        if f == default_filename:
            continue
        ident = parse_episode_identity(f)
        if ident.get("kind") != "episode" or ident.get("number") is None:
            continue
        num = ident["number"]
        if num == target_num:
            continue

        stem, ext = os.path.splitext(f)
        regexes = [
            rf'(?i)(?<=[\s._\-\]A-Za-z])(?:S\d{{1,2}}\s*)?(?:E|EP|Episode\s*)[-_. #]*(0*{num})\b',
            rf'(?i)(?<=[\s._\-\]A-Za-z])(0*{num})(?=[\s._\-\[\]\(\)]|$)',
        ]
        for r in regexes:
            ms = list(re.finditer(r, stem))
            if ms:
                m = ms[-1]
                digits = m.group(1)
                prefix = stem[:m.start(1)]
                suffix = stem[m.end(1):] + ext
                suffix_stem, suffix_ext = os.path.splitext(suffix)
                suffix_stem = re.sub(r'(?i)v\d+$', '', suffix_stem)
                suffix_stem = re.sub(r'\[[0-9a-fA-F]{8}\]', '', suffix_stem).rstrip()
                suffix = suffix_stem + suffix_ext
                patterns.append((prefix, len(digits), suffix))
                break

    if not patterns:
        return default_filename

    best_prefix, _ = Counter(p[0] for p in patterns).most_common(1)[0]
    best_padding, _ = Counter(p[1] for p in patterns).most_common(1)[0]
    best_suffix, _ = Counter(p[2] for p in patterns).most_common(1)[0]

    if is_int:
        pad_len = max(best_padding, 1)
        formatted_ep = f"{int(target_num):0{pad_len}d}"
    else:
        formatted_ep = f"{target_num}"

    # Jika nama default mengandung resolusi tertentu (misal 720p/480p), adaptasikan pada suffix
    def_res_m = re.search(r'(?i)\b(2160|1440|1080|720|480|360)p(?=[^a-zA-Z]|$)', default_filename)
    if def_res_m:
        req_res = def_res_m.group(1) + "p"
        best_suffix = re.sub(r'(?i)\b(2160|1440|1080|720|480|360)p(?=[^a-zA-Z]|$)', req_res, best_suffix)

    # Pastikan ekstensi wadah video sesuai dengan berkas yang diunduh
    _, def_ext = os.path.splitext(default_filename)
    if def_ext:
        best_suffix = os.path.splitext(best_suffix)[0] + def_ext

    return f"{best_prefix}{formatted_ep}{best_suffix}"


def start_download(
    url: str,
    anime_name: str = "",
    episode: str = "",
    filename: str = "",
    custom_dest_dir: str = "",
    provider: str = "samehadaku"
) -> Dict[str, Any]:
    """Memulai proses pengunduhan berkas di background thread."""
    clean_url = str(url or "").strip()
    if not clean_url:
        return {"ok": False, "error": "URL unduhan tidak valid atau kosong"}

    # Resolusi nama berkas jika belum ada atau generik
    target_filename = sanitize_filename(filename)
    total_expected_bytes = 0

    # Jika URL Pixeldrain, periksa metadata resmi dari API
    pd_match = re.search(r'pixeldrain\.com/(?:api/file/|u/)([a-zA-Z0-9]+)', clean_url)
    if pd_match:
        pd_id = pd_match.group(1)
        # Pastikan URL download sesuai format API Pixeldrain
        if "api/file" not in clean_url:
            clean_url = f"https://pixeldrain.com/api/file/{pd_id}?download"

        pd_info = get_pixeldrain_file_info(pd_id)
        if pd_info:
            if not target_filename or target_filename.startswith("download_"):
                target_filename = sanitize_filename(pd_info.get("name") or "")
            total_expected_bytes = pd_info.get("size") or 0

    # Jika URL Mediafire, coba ekstraksi direct download URL
    if "mediafire.com" in clean_url.lower() and "download" not in clean_url.lower():
        try:
            from anibase.integrations.samehadaku import resolve_mediafire_direct_url
            mf_direct = resolve_mediafire_direct_url(clean_url)
            if mf_direct:
                clean_url = mf_direct
        except Exception:
            pass

    # Jika nama berkas masih kosong, buat nama berbasis anime & episode
    if not target_filename or target_filename.startswith("download_"):
        clean_anime = clean_auto_import_folder_name(anime_name) or "Anime"
        ep_label = f" - Episode {episode}" if episode else ""
        target_filename = f"{clean_anime}{ep_label}.mkv"

    # Pastikan ekstensi video terpasang
    _, ext = os.path.splitext(target_filename)
    if ext.lower() not in VIDEO_EXTENSIONS:
        target_filename += ".mkv"

    # Tentukan folder tujuan dan pencocokan anime
    if custom_dest_dir and os.path.isdir(custom_dest_dir):
        dest_folder = custom_dest_dir
        dest_info = {
            "folder": custom_dest_dir,
            "matched": False,
            "matched_anime": os.path.basename(custom_dest_dir),
            "match_method": "custom",
            "score": 1.0,
        }
    else:
        dest_info = resolve_download_destination(anime_name)
        dest_folder = dest_info["folder"]

    os.makedirs(dest_folder, exist_ok=True)

    # Sesuaikan nama berkas agar mengikuti konvensi penamaan episode yang sudah ada di folder tujuan
    target_filename = adapt_filename_to_library_pattern(dest_folder, episode, target_filename)

    save_path = unique_destination_path(dest_folder, target_filename)
    temp_path = f"{save_path}.part"
    matched_anime_name = dest_info.get("matched_anime") or anime_name

    task_id = "dl_" + secrets.token_hex(6)
    cancel_event = threading.Event()
    pause_event = threading.Event()

    task_data = {
        "task_id": task_id,
        "url": clean_url,
        "anime_name": anime_name,
        "matched_anime": matched_anime_name,
        "dest_anime_folder": os.path.basename(dest_folder),
        "dest_folder": dest_folder,
        "match_method": dest_info.get("match_method", ""),
        "episode": str(episode or ""),
        "filename": os.path.basename(save_path),
        "save_path": save_path,
        "temp_path": temp_path,
        "status": "queued",
        "downloaded_bytes": 0,
        "total_bytes": total_expected_bytes,
        "progress_percent": 0.0,
        "speed_bps": 0,
        "resumed": False,
        "provider": str(provider or "samehadaku").lower(),
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    with _TASKS_LOCK:
        _DOWNLOAD_TASKS[task_id] = task_data
        _CANCEL_EVENTS[task_id] = cancel_event
        _PAUSE_EVENTS[task_id] = pause_event
        _prune_tasks_locked()

    # Jalankan antrean unduhan (akan langsung mulai jika kuota paralel tersedia)
    _process_download_queue()

    with _TASKS_LOCK:
        current_task = dict(_DOWNLOAD_TASKS.get(task_id, task_data))

    app_log(f"Registered download task {task_id} (status: {current_task.get('status')}): {save_path}", "INFO")
    return {"ok": True, "task": current_task}


def _download_worker(
    task_id: str,
    url: str,
    save_path: str,
    temp_path: str,
    cancel_event: threading.Event,
    pause_event: Optional[threading.Event] = None,
    anime_name: str = "",
    matched_anime: Optional[str] = None
):
    """Worker fungsi pengunduhan chunk streaming dengan dukungan HTTP Range Request (Resume)."""
    if pause_event is None:
        pause_event = threading.Event()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    # Cek apakah berkas .part sudah ada untuk melanjutkan unduhan (HTTP Range)
    existing_bytes = 0
    if os.path.exists(temp_path):
        try:
            existing_bytes = os.path.getsize(temp_path)
        except OSError:
            existing_bytes = 0

    if existing_bytes > 0:
        headers["Range"] = f"bytes={existing_bytes}-"
        app_log(f"Attempting to resume download {task_id} from byte {existing_bytes} via HTTP Range", "INFO")

    downloaded = existing_bytes
    total = 0
    start_time = time.time()
    last_speed_check_time = start_time
    bytes_since_last_check = 0
    current_speed = 0
    is_partial = False

    try:
        with requests.get(url, headers=headers, stream=True, timeout=20) as resp:
            # Penanganan HTTP 416 (Range Not Satisfiable)
            if resp.status_code == 416:
                app_log(f"Task {task_id}: HTTP 416 Range Not Satisfiable. Re-verifying or restarting from 0", "WARN")
                headers.pop("Range", None)
                existing_bytes = 0
                downloaded = 0
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                with requests.get(url, headers=headers, stream=True, timeout=20) as r_fresh:
                    resp = r_fresh

            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP response error: {resp.status_code}")

            is_partial = (resp.status_code == 206)
            content_range = resp.headers.get("Content-Range", "")
            content_length = resp.headers.get("Content-Length")

            if is_partial and content_range:
                # Format: bytes 1000-4999/5000
                m = re.search(r'/(\d+)$', content_range)
                if m:
                    total = int(m.group(1))
                elif content_length and content_length.isdigit():
                    total = existing_bytes + int(content_length)
            elif content_length and content_length.isdigit():
                if is_partial:
                    total = existing_bytes + int(content_length)
                else:
                    total = int(content_length)
                    # Server tidak mendukung range / mengirim berkas dari 0
                    existing_bytes = 0
                    downloaded = 0

            with _TASKS_LOCK:
                if task_id in _DOWNLOAD_TASKS:
                    t = _DOWNLOAD_TASKS[task_id]
                    if total > 0:
                        t["total_bytes"] = total
                    t["status"] = "downloading"
                    t["resumed"] = bool(existing_bytes > 0 and is_partial)
                    t["is_resumed"] = bool(existing_bytes > 0 and is_partial)
                    t["updated_at"] = time.time()

            # Mode "ab" jika berhasil resume parsial, mode "wb" jika dari 0
            file_mode = "ab" if (existing_bytes > 0 and is_partial) else "wb"
            with open(temp_path, file_mode) as f:
                for chunk in resp.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                    if cancel_event.is_set():
                        app_log(f"Download task {task_id} cancelled by user", "INFO")
                        break

                    if pause_event.is_set():
                        app_log(f"Download task {task_id} paused by user", "INFO")
                        break

                    if not chunk:
                        continue

                    f.write(chunk)
                    chunk_len = len(chunk)
                    downloaded += chunk_len
                    bytes_since_last_check += chunk_len

                    now = time.time()
                    elapsed_check = now - last_speed_check_time
                    if elapsed_check >= 0.8:
                        current_speed = int(bytes_since_last_check / elapsed_check)
                        bytes_since_last_check = 0
                        last_speed_check_time = now

                        # Update task state
                        with _TASKS_LOCK:
                            if task_id in _DOWNLOAD_TASKS:
                                t = _DOWNLOAD_TASKS[task_id]
                                t["downloaded_bytes"] = downloaded
                                if total > 0:
                                    t["progress_percent"] = round((downloaded / total) * 100, 1)
                                t["speed_bps"] = current_speed
                                t["updated_at"] = now

        # 1. Cek jika dijeda (PAUSED)
        if pause_event.is_set():
            with _TASKS_LOCK:
                if task_id in _DOWNLOAD_TASKS:
                    t = _DOWNLOAD_TASKS[task_id]
                    t["status"] = "paused"
                    t["speed_bps"] = 0
                    t["downloaded_bytes"] = downloaded
                    if total > 0:
                        t["progress_percent"] = round((downloaded / total) * 100, 1)
                    t["updated_at"] = time.time()
            _process_download_queue()
            return

        # 2. Cek jika dibatalkan (CANCELLED)
        if cancel_event.is_set():
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            with _TASKS_LOCK:
                if task_id in _DOWNLOAD_TASKS:
                    _DOWNLOAD_TASKS[task_id]["status"] = "cancelled"
                    _DOWNLOAD_TASKS[task_id]["speed_bps"] = 0
                    _DOWNLOAD_TASKS[task_id]["updated_at"] = time.time()
            _process_download_queue()
            return

        # 3. Periksa integritas: jangan tandai selesai jika koneksi terputus prematur
        if total > 0 and downloaded < total:
            raise RuntimeError(f"Unduhan terputus sebelum selesai ({downloaded}/{total} byte)")

        # 4. Selesai unduh: ganti nama berkas dari .part ke save_path
        if os.path.exists(temp_path):
            os.replace(temp_path, save_path)

        with _TASKS_LOCK:
            if task_id in _DOWNLOAD_TASKS:
                t = _DOWNLOAD_TASKS[task_id]
                t["status"] = "completed"
                t["downloaded_bytes"] = downloaded
                t["total_bytes"] = downloaded if total <= 0 else total
                t["progress_percent"] = 100.0
                t["speed_bps"] = 0
                t["updated_at"] = time.time()

        dest_folder_name = os.path.basename(os.path.dirname(save_path))
        target_anime = matched_anime or dest_folder_name or anime_name
        app_log(
            f"Download completed successfully: {save_path} ({downloaded} bytes) -> anime folder [{target_anime}]",
            "INFO"
        )

        # Segarkan cache media dan sinkronkan library otomatis
        clear_media_path_cache()
        if target_anime:
            try:
                from anibase.scanner import sync_anime_to_db
                from anibase.watcher import queue_anime_sync
                # Segera sinkronkan anime ini ke database SQLite
                sync_anime_to_db(target_anime, trigger_label=f"Download completed: {target_anime}")
                # Jadwalkan debounced sync juga agar file watcher sinkron
                queue_anime_sync(target_anime)
            except Exception as sync_err:
                app_log(f"Failed to sync anime library after download: {sync_err}", "DEBUG")

            try:
                from anibase.watchlist_tracker import on_download_completed
                on_download_completed(target_anime, save_path)
            except Exception as track_err:
                app_log(f"Failed to notify watchlist tracker on download: {track_err}", "DEBUG")

        # Proses antrean unduhan berikutnya
        _process_download_queue()

    except Exception as error:
        app_log(f"Download task {task_id} failed: {error}", "ERROR")
        # Simpan berkas .part utuh agar pengguna bisa melanjutkan (resume) unduhan
        with _TASKS_LOCK:
            if task_id in _DOWNLOAD_TASKS:
                t = _DOWNLOAD_TASKS[task_id]
                t["status"] = "error"
                t["error"] = str(error)
                t["speed_bps"] = 0
                t["downloaded_bytes"] = downloaded
                if total > 0:
                    t["progress_percent"] = round((downloaded / total) * 100, 1)
                t["updated_at"] = time.time()
        _process_download_queue()


def pause_download(task_id: str) -> bool:
    """Menjeda tugas unduhan yang sedang berjalan atau mengantre."""
    with _TASKS_LOCK:
        task = _DOWNLOAD_TASKS.get(task_id)
        if not task:
            return False

        if task.get("status") == "downloading":
            event = _PAUSE_EVENTS.get(task_id)
            if event:
                event.set()
                return True
            task["status"] = "paused"
            task["speed_bps"] = 0
            task["updated_at"] = time.time()
            _process_download_queue()
            return True

        if task.get("status") == "queued":
            task["status"] = "paused"
            task["speed_bps"] = 0
            task["updated_at"] = time.time()
            return True

        return False


def resume_download(task_id: str) -> bool:
    """Melanjutkan tugas unduhan yang dijeda atau gagal dari posisi byte terakhir (HTTP Range)."""
    with _TASKS_LOCK:
        task = _DOWNLOAD_TASKS.get(task_id)
        if not task:
            return False

        if task.get("status") in ("paused", "error", "cancelled"):
            task["status"] = "queued"
            task["error"] = None
            task["speed_bps"] = 0
            task["updated_at"] = time.time()

            _CANCEL_EVENTS[task_id] = threading.Event()
            _PAUSE_EVENTS[task_id] = threading.Event()

    _process_download_queue()
    return True


def retry_download(task_id: str) -> bool:
    """Mencoba kembali tugas unduhan yang gagal."""
    return resume_download(task_id)


def cancel_download(task_id: str) -> bool:
    """Batalkan tugas unduhan yang sedang berjalan, mengantre, atau dijeda."""
    with _TASKS_LOCK:
        task = _DOWNLOAD_TASKS.get(task_id)
        if not task:
            return False

        if task.get("status") == "downloading":
            event = _CANCEL_EVENTS.get(task_id)
            if event:
                event.set()
            task["status"] = "cancelled"
            task["speed_bps"] = 0
            task["updated_at"] = time.time()
        else:
            temp_path = task.get("temp_path")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            task["status"] = "cancelled"
            task["speed_bps"] = 0
            task["updated_at"] = time.time()

    _process_download_queue()
    return True


def get_download_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Ambil informasi status tugas unduhan tertentu."""
    with _TASKS_LOCK:
        task = _DOWNLOAD_TASKS.get(task_id)
        return dict(task) if task else None


def get_all_download_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """Ambil daftar semua tugas unduhan terurut prioritas antrean dan waktu."""
    with _TASKS_LOCK:
        tasks = [dict(t) for t in _DOWNLOAD_TASKS.values()]

    status_priority = {
        "downloading": 0,
        "queued": 1,
        "paused": 2,
        "error": 3,
        "completed": 4,
        "cancelled": 5,
    }

    queued_tasks = [t for t in tasks if t.get("status") == "queued"]
    queued_tasks.sort(key=lambda x: x.get("created_at", 0))
    for idx, qt in enumerate(queued_tasks, start=1):
        qt["queue_position"] = idx

    tasks.sort(key=lambda x: (
        status_priority.get(x.get("status", ""), 99),
        -x.get("updated_at", 0)
    ))
    return tasks[:limit]


def clear_completed_download_tasks() -> int:
    """Hapus tugas yang sudah selesai, dibatalkan, atau gagal dari memori."""
    removed = 0
    with _TASKS_LOCK:
        to_del = [
            tid for tid, t in _DOWNLOAD_TASKS.items()
            if t.get("status") in ("completed", "cancelled", "error")
        ]
        for tid in to_del:
            _DOWNLOAD_TASKS.pop(tid, None)
            _CANCEL_EVENTS.pop(tid, None)
            _PAUSE_EVENTS.pop(tid, None)
            removed += 1
    return removed


def _prune_tasks_locked():
    """Jaga ukuran memori _DOWNLOAD_TASKS agar tidak melebihi MAX_STORED_TASKS."""
    if len(_DOWNLOAD_TASKS) <= MAX_STORED_TASKS:
        return
    # Hapus tugas tertua yang berstatus selesai/batal/error terlebih dahulu
    sorted_tasks = sorted(
        _DOWNLOAD_TASKS.items(),
        key=lambda item: item[1].get("created_at", 0)
    )
    for tid, t in sorted_tasks:
        if len(_DOWNLOAD_TASKS) <= MAX_STORED_TASKS:
            break
        if t.get("status") in ("completed", "cancelled", "error"):
            _DOWNLOAD_TASKS.pop(tid, None)
            _CANCEL_EVENTS.pop(tid, None)


def get_anime_local_episodes(anime_name: str) -> Dict[str, Any]:
    """Mencari folder library yang cocok untuk anime dan mengembalikan daftar nomor episode yang sudah ada."""
    matched = find_matching_library_anime(anime_name)
    if not matched or not matched.get("path") or not os.path.isdir(matched["path"]):
        return {
            "matched": False,
            "folder": "",
            "anime_name": anime_name,
            "existing_episodes": [],
            "count": 0,
        }

    folder = matched["path"]
    try:
        entries = os.listdir(folder)
    except OSError:
        return {
            "matched": False,
            "folder": folder,
            "anime_name": matched.get("name") or anime_name,
            "existing_episodes": [],
            "count": 0,
        }

    eps = set()
    for f in entries:
        if (
            os.path.isfile(os.path.join(folder, f))
            and os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
            and not f.endswith(".part")
        ):
            ident = parse_episode_identity(f)
            if ident.get("kind") == "episode" and ident.get("number") is not None:
                num = ident["number"]
                eps.add(int(num) if isinstance(num, (int, float)) and float(num).is_integer() else num)

    return {
        "matched": True,
        "folder": folder,
        "anime_name": matched.get("name") or anime_name,
        "existing_episodes": sorted(list(eps)),
        "count": len(eps),
    }


def resolve_best_direct_link(
    page_url: str,
    preferred_quality: str = "1080p",
    preferred_format: str = "MKV"
) -> Optional[Dict[str, Any]]:
    """Mencari tautan unduhan langsung terbaik (Pixeldrain/Mediafire) dari halaman episode Samehadaku."""
    downloads = get_samehadaku_download_links(page_url)
    if not downloads:
        return None

    qualities_order = [preferred_quality]
    for q in ["1080p", "720p", "480p", "360p"]:
        if q not in qualities_order:
            qualities_order.append(q)

    for q in qualities_order:
        matching_tiers = [
            t for t in downloads
            if q.lower() in t.get("quality", "").lower()
            and (preferred_format.lower() in t.get("format", "").lower() or not t.get("format"))
        ]
        if not matching_tiers:
            matching_tiers = [
                t for t in downloads
                if q.lower() in t.get("quality", "").lower()
            ]

        for tier in matching_tiers:
            for l in tier.get("links", []):
                if l.get("is_direct") and l.get("direct_download_url"):
                    return {
                        "direct_url": l["direct_download_url"],
                        "format": tier.get("format") or preferred_format,
                        "quality": tier.get("quality") or q,
                        "host": l.get("host") or "Pixeldrain",
                    }
                if l.get("is_pixeldrain") and l.get("pixeldrain_id"):
                    return {
                        "direct_url": f"https://pixeldrain.com/api/file/{l['pixeldrain_id']}?download",
                        "format": tier.get("format") or preferred_format,
                        "quality": tier.get("quality") or q,
                        "host": "Pixeldrain",
                    }

            for l in tier.get("links", []):
                if "mediafire.com" in l.get("url", "").lower():
                    mf_direct = resolve_mediafire_direct_url(l["url"])
                    if mf_direct:
                        return {
                            "direct_url": mf_direct,
                            "format": tier.get("format") or preferred_format,
                            "quality": tier.get("quality") or q,
                            "host": "Mediafire",
                        }

    return None


def start_batch_download(
    anime_name: str,
    episodes: List[Dict[str, Any]],
    preferred_quality: str = "1080p",
    preferred_format: str = "MKV",
    custom_dest_dir: str = ""
) -> Dict[str, Any]:
    """Memulai proses antrean unduhan batch beberapa episode secara berurutan."""
    clean_anime = str(anime_name or "").strip()
    if not clean_anime:
        return {"ok": False, "error": "Nama anime tidak boleh kosong"}
    if not episodes:
        return {"ok": False, "error": "Daftar episode batch tidak boleh kosong"}

    batch_id = "batch_" + secrets.token_hex(6)
    cancel_event = threading.Event()

    episodes_status = []
    for ep in episodes:
        episodes_status.append({
            "episode": str(ep.get("episode") or "").strip(),
            "url": str(ep.get("url") or "").strip(),
            "title": str(ep.get("title") or "").strip(),
            "status": "pending",
            "task_id": None,
            "filename": None,
            "error": None,
            "progress_percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
        })

    batch_data = {
        "batch_id": batch_id,
        "anime_name": clean_anime,
        "preferred_quality": preferred_quality or "1080p",
        "preferred_format": preferred_format or "MKV",
        "custom_dest_dir": custom_dest_dir,
        "total_episodes": len(episodes_status),
        "completed_count": 0,
        "failed_count": 0,
        "current_index": 0,
        "current_episode": episodes_status[0]["episode"] if episodes_status else "",
        "current_task_id": None,
        "status": "in_progress",
        "episodes": episodes_status,
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    with _BATCH_LOCK:
        _BATCH_TASKS[batch_id] = batch_data
        _BATCH_CANCEL_EVENTS[batch_id] = cancel_event

    worker = threading.Thread(
        target=_batch_worker,
        args=(batch_id, cancel_event),
        daemon=True,
        name=f"BatchDownloadWorker-{batch_id}"
    )
    worker.start()

    app_log(f"Started batch download {batch_id} for '{clean_anime}' ({len(episodes_status)} episodes)", "INFO")
    return {"ok": True, "batch": batch_data}


def _batch_worker(batch_id: str, cancel_event: threading.Event):
    """Worker fungsi pengunduhan batch episode secara berurutan."""
    with _BATCH_LOCK:
        batch = _BATCH_TASKS.get(batch_id)
    if not batch:
        return

    anime_name = batch["anime_name"]
    pref_quality = batch.get("preferred_quality", "1080p")
    pref_format = batch.get("preferred_format", "MKV")
    custom_dest = batch.get("custom_dest_dir", "")

    for idx, ep_item in enumerate(batch["episodes"]):
        if cancel_event.is_set():
            with _BATCH_LOCK:
                ep_item["status"] = "cancelled"
            continue

        with _BATCH_LOCK:
            batch["current_index"] = idx
            batch["current_episode"] = ep_item["episode"]
            ep_item["status"] = "resolving"
            batch["updated_at"] = time.time()

        ep_url = ep_item["url"]
        link_info = resolve_best_direct_link(ep_url, preferred_quality=pref_quality, preferred_format=pref_format)

        if not link_info or not link_info.get("direct_url"):
            with _BATCH_LOCK:
                ep_item["status"] = "error"
                ep_item["error"] = "Tautan unduhan langsung (Pixeldrain/Mediafire) tidak tersedia"
                batch["failed_count"] += 1
                batch["updated_at"] = time.time()
            time.sleep(1.0)
            continue

        if cancel_event.is_set():
            with _BATCH_LOCK:
                ep_item["status"] = "cancelled"
            break

        direct_url = link_info["direct_url"]
        quality_found = link_info.get("quality") or pref_quality
        format_found = (link_info.get("format") or pref_format).lower()

        clean_anime = clean_auto_import_folder_name(anime_name) or "Anime"
        default_filename = f"{clean_anime} - Ep {ep_item['episode']} [{quality_found}].{format_found}"

        dl_result = start_download(
            url=direct_url,
            anime_name=anime_name,
            episode=ep_item["episode"],
            filename=default_filename,
            custom_dest_dir=custom_dest
        )

        if not dl_result.get("ok"):
            with _BATCH_LOCK:
                ep_item["status"] = "error"
                ep_item["error"] = dl_result.get("error", "Gagal memulai unduhan")
                batch["failed_count"] += 1
                batch["updated_at"] = time.time()
            time.sleep(1.0)
            continue

        task_id = dl_result["task"]["task_id"]
        with _BATCH_LOCK:
            ep_item["task_id"] = task_id
            ep_item["filename"] = dl_result["task"]["filename"]
            ep_item["status"] = "downloading"
            batch["current_task_id"] = task_id
            batch["updated_at"] = time.time()

        while not cancel_event.is_set():
            t = get_download_task(task_id)
            if not t:
                break
            with _BATCH_LOCK:
                ep_item["progress_percent"] = t.get("progress_percent", 0.0)
                ep_item["downloaded_bytes"] = t.get("downloaded_bytes", 0)
                ep_item["total_bytes"] = t.get("total_bytes", 0)
                batch["updated_at"] = time.time()

            if t["status"] in ("completed", "error", "cancelled"):
                with _BATCH_LOCK:
                    if t["status"] == "completed":
                        ep_item["status"] = "completed"
                        ep_item["progress_percent"] = 100.0
                        batch["completed_count"] += 1
                    elif t["status"] == "cancelled":
                        ep_item["status"] = "cancelled"
                    else:
                        ep_item["status"] = "error"
                        ep_item["error"] = t.get("error", "Unduhan gagal")
                        batch["failed_count"] += 1
                    batch["updated_at"] = time.time()
                break
            time.sleep(0.5)

        if cancel_event.is_set():
            cancel_download(task_id)
            with _BATCH_LOCK:
                ep_item["status"] = "cancelled"
                batch["updated_at"] = time.time()
            break

        time.sleep(1.0)

    with _BATCH_LOCK:
        if cancel_event.is_set():
            batch["status"] = "cancelled"
            for rem in batch["episodes"]:
                if rem["status"] in ("pending", "resolving"):
                    rem["status"] = "cancelled"
        else:
            batch["status"] = "completed"
        batch["current_task_id"] = None
        batch["updated_at"] = time.time()

    app_log(
        f"Batch download {batch_id} finished: {batch['completed_count']} completed, {batch['failed_count']} failed",
        "INFO"
    )


def get_batch_task(batch_id: str) -> Optional[Dict[str, Any]]:
    """Mengambil status antrean batch tertentu."""
    with _BATCH_LOCK:
        batch = _BATCH_TASKS.get(batch_id)
        return dict(batch) if batch else None


def get_all_batch_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    """Mengambil daftar seluruh antrean batch unduhan terurut terbaru."""
    with _BATCH_LOCK:
        tasks = [dict(b) for b in _BATCH_TASKS.values()]
    tasks.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return tasks[:limit]


def cancel_batch_download(batch_id: str) -> bool:
    """Membatalkan seluruh antrean batch unduhan."""
    with _BATCH_LOCK:
        batch = _BATCH_TASKS.get(batch_id)
        event = _BATCH_CANCEL_EVENTS.get(batch_id)
        if not batch:
            return False
        if event:
            event.set()
        batch["status"] = "cancelled"
        curr_task = batch.get("current_task_id")
        if curr_task:
            cancel_download(curr_task)
        for ep in batch.get("episodes", []):
            if ep.get("status") in ("pending", "resolving", "downloading"):
                ep["status"] = "cancelled"
        batch["updated_at"] = time.time()
        return True


def clear_completed_batch_tasks() -> int:
    """Membersihkan batch yang sudah selesai, dibatalkan, atau gagal."""
    removed = 0
    with _BATCH_LOCK:
        to_del = [
            bid for bid, b in _BATCH_TASKS.items()
            if b.get("status") in ("completed", "cancelled", "error")
        ]
        for bid in to_del:
            _BATCH_TASKS.pop(bid, None)
            _BATCH_CANCEL_EVENTS.pop(bid, None)
            removed += 1
    return removed
