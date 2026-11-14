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

import requests
from anibase.constants import CACHE_DIR, VIDEO_EXTENSIONS
from anibase.logging import app_log
from anibase.settings import load_settings, get_settings_library_paths
from anibase.scanner import find_media_path, clear_media_path_cache
from anibase.watcher import clean_auto_import_folder_name, unique_destination_path
from anibase.integrations.samehadaku import get_pixeldrain_file_info

DEFAULT_CHUNK_SIZE = 128 * 1024  # 128 KB
MAX_STORED_TASKS = 100

_TASKS_LOCK = threading.Lock()
_DOWNLOAD_TASKS: Dict[str, Dict[str, Any]] = {}
_CANCEL_EVENTS: Dict[str, threading.Event] = {}


def sanitize_filename(filename: str) -> str:
    """Membersihkan nama berkas dari karakter terlarang sistem berkas."""
    clean = re.sub(r'[<>:"/\\|?*]', '_', filename or '').strip()
    return clean or f"download_{secrets.token_hex(4)}"


def resolve_download_folder(anime_name: str) -> str:
    """Menentukan folder penyimpanan yang paling tepat untuk anime yang diunduh.

    Prioritas:
    1. Folder anime yang sudah ada di library (`find_media_path`).
    2. `ongoing_path` dari pengaturan jika valid.
    3. `auto_import_destination_root` dari pengaturan jika valid.
    4. `auto_import_downloads_path` dari pengaturan jika valid.
    5. Folder library pertama dari `library_paths`.
    6. Direktori Downloads pengguna OS (~/Downloads/AniBase).
    """
    clean_name = clean_auto_import_folder_name(anime_name) or "Unknown Anime"

    # 1. Cek library lokal jika anime sudah pernah ada
    if anime_name:
        existing_path = find_media_path(anime_name)
        if existing_path and os.path.isdir(existing_path):
            return existing_path

    settings = load_settings()

    # 2. Ongoing folder
    ongoing = settings.get("ongoing_path", "").strip()
    if ongoing and os.path.isdir(ongoing):
        target = os.path.join(ongoing, clean_name)
        os.makedirs(target, exist_ok=True)
        return target

    # 3. Auto import destination root
    dest_root = settings.get("auto_import_destination_root", "").strip()
    if dest_root and os.path.isdir(dest_root):
        target = os.path.join(dest_root, clean_name)
        os.makedirs(target, exist_ok=True)
        return target

    # 4. Library paths pertama
    library_paths = get_settings_library_paths(settings)
    for lib_path in library_paths:
        if lib_path and os.path.isdir(lib_path):
            target = os.path.join(lib_path, clean_name)
            os.makedirs(target, exist_ok=True)
            return target

    # 5. Auto import downloads folder
    dl_path = settings.get("auto_import_downloads_path", "").strip()
    if dl_path and os.path.isdir(dl_path):
        target = os.path.join(dl_path, clean_name)
        os.makedirs(target, exist_ok=True)
        return target

    # 6. Fallback Downloads pengguna
    fallback = os.path.join(os.path.expanduser("~"), "Downloads", "AniBase", clean_name)
    os.makedirs(fallback, exist_ok=True)
    return fallback


def start_download(
    url: str,
    anime_name: str = "",
    episode: str = "",
    filename: str = "",
    custom_dest_dir: str = ""
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

    # Jika nama berkas masih kosong, buat nama berbasis anime & episode
    if not target_filename or target_filename.startswith("download_"):
        clean_anime = clean_auto_import_folder_name(anime_name) or "Anime"
        ep_label = f" - Episode {episode}" if episode else ""
        target_filename = f"{clean_anime}{ep_label}.mkv"

    # Pastikan ekstensi video terpasang
    _, ext = os.path.splitext(target_filename)
    if ext.lower() not in VIDEO_EXTENSIONS:
        target_filename += ".mkv"

    # Tentukan folder tujuan
    if custom_dest_dir and os.path.isdir(custom_dest_dir):
        dest_folder = custom_dest_dir
    else:
        dest_folder = resolve_download_folder(anime_name)

    os.makedirs(dest_folder, exist_ok=True)
    save_path = unique_destination_path(dest_folder, target_filename)
    temp_path = f"{save_path}.part"

    task_id = "dl_" + secrets.token_hex(6)
    cancel_event = threading.Event()

    task_data = {
        "task_id": task_id,
        "url": clean_url,
        "anime_name": anime_name,
        "episode": str(episode or ""),
        "filename": os.path.basename(save_path),
        "save_path": save_path,
        "temp_path": temp_path,
        "status": "downloading",
        "downloaded_bytes": 0,
        "total_bytes": total_expected_bytes,
        "progress_percent": 0.0,
        "speed_bps": 0,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    with _TASKS_LOCK:
        _DOWNLOAD_TASKS[task_id] = task_data
        _CANCEL_EVENTS[task_id] = cancel_event
        _prune_tasks_locked()

    worker = threading.Thread(
        target=_download_worker,
        args=(task_id, clean_url, save_path, temp_path, cancel_event, anime_name),
        daemon=True,
        name=f"DownloadWorker-{task_id}"
    )
    worker.start()

    app_log(f"Started download task {task_id}: {save_path} from {clean_url}", "INFO")
    return {"ok": True, "task": task_data}


def _download_worker(
    task_id: str,
    url: str,
    save_path: str,
    temp_path: str,
    cancel_event: threading.Event,
    anime_name: str
):
    """Worker fungsi pengunduhan chunk streaming."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    downloaded = 0
    total = 0
    start_time = time.time()
    last_speed_check_time = start_time
    bytes_since_last_check = 0
    current_speed = 0

    try:
        with requests.get(url, headers=headers, stream=True, timeout=20) as resp:
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP response error: {resp.status_code}")

            content_length = resp.headers.get("Content-Length")
            if content_length and content_length.isdigit():
                total = int(content_length)

            with _TASKS_LOCK:
                if task_id in _DOWNLOAD_TASKS:
                    if total > 0:
                        _DOWNLOAD_TASKS[task_id]["total_bytes"] = total
                    _DOWNLOAD_TASKS[task_id]["status"] = "downloading"
                    _DOWNLOAD_TASKS[task_id]["updated_at"] = time.time()

            # Buka berkas .part untuk penulisan
            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                    if cancel_event.is_set():
                        app_log(f"Download task {task_id} cancelled by user", "INFO")
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

        # Periksa apakah dibatalkan
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
            return

        # Selesai unduh: ganti nama berkas dari .part ke save_path
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

        app_log(f"Download completed successfully: {save_path} ({downloaded} bytes)", "INFO")

        # Segarkan cache media dan antrikan sinkronisasi library jika ada anime_name
        clear_media_path_cache()
        if anime_name:
            try:
                from anibase.watcher import queue_anime_sync
                queue_anime_sync(anime_name)
            except Exception as sync_err:
                app_log(f"Failed to queue anime sync after download: {sync_err}", "DEBUG")

    except Exception as error:
        app_log(f"Download task {task_id} failed: {error}", "ERROR")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        with _TASKS_LOCK:
            if task_id in _DOWNLOAD_TASKS:
                t = _DOWNLOAD_TASKS[task_id]
                t["status"] = "error"
                t["error"] = str(error)
                t["speed_bps"] = 0
                t["updated_at"] = time.time()


def cancel_download(task_id: str) -> bool:
    """Batalkan tugas unduhan yang sedang berjalan."""
    with _TASKS_LOCK:
        event = _CANCEL_EVENTS.get(task_id)
        task = _DOWNLOAD_TASKS.get(task_id)
        if not task:
            return False
        if event:
            event.set()
        task["status"] = "cancelled"
        task["updated_at"] = time.time()
        return True


def get_download_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Ambil informasi status tugas unduhan tertentu."""
    with _TASKS_LOCK:
        task = _DOWNLOAD_TASKS.get(task_id)
        return dict(task) if task else None


def get_all_download_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """Ambil daftar semua tugas unduhan terurut terbaru."""
    with _TASKS_LOCK:
        tasks = [dict(t) for t in _DOWNLOAD_TASKS.values()]
    tasks.sort(key=lambda x: x.get("created_at", 0), reverse=True)
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
