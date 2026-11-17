"""
anibase/watchlist_tracker.py
Ongoing Watchlist Tracker & Notifikasi Episode Baru untuk AniBase.

Memantau anime yang sedang ongoing (dari folder ongoing_path, status RELEASING
di library, maupun daftar pantau manual), mendeteksi rilis episode terbaru dari
Samehadaku, menyimpan riwayat notifikasi, dan menyediakan aksi unduh instan (1-klik).
"""

import os
import re
import time
import threading
from typing import List, Dict, Any, Optional

from anibase import constants
from anibase.settings import load_settings
from anibase.logging import app_log
from anibase.utils import is_shutdown_requested, wait_for_shutdown
from anibase.db import (
    db_connection,
    get_ongoing_watchlist,
    upsert_ongoing_watchlist,
    update_ongoing_watchlist_check,
    set_ongoing_watchlist_tracked,
    delete_ongoing_watchlist,
    save_episode_notification,
    get_episode_notifications,
    mark_notification_read,
    mark_notification_downloaded,
    get_unread_notification_count,
)
from anibase.integrations.samehadaku import (
    get_samehadaku_latest_episodes,
    get_samehadaku_anime_details,
    search_samehadaku,
)
from anibase.download_manager import (
    get_anime_local_episodes,
    find_matching_library_anime,
    resolve_best_direct_link,
    start_download,
)


def extract_episode_number(raw_value: Any) -> Optional[float]:
    """Mengekstrak nomor episode sebagai float dari string atau nilai numerik.
    
    Contoh: 'Episode 7' -> 7.0, '08' -> 8.0, '12.5' -> 12.5
    """
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    
    s = str(raw_value).strip()
    m = re.search(r'(?:episode|episodes|eps?|e)?\s*(\d+(?:\.\d+)?)', s, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            pass
    return None


def _has_video_files(dir_path: str) -> bool:
    """Memeriksa apakah sebuah direktori berisi setidaknya satu berkas video."""
    try:
        with os.scandir(dir_path) as entries:
            for entry in entries:
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in constants.VIDEO_EXTENSIONS and not entry.name.endswith(".part"):
                        return True
                elif entry.is_dir() and not entry.name.startswith(('.', '$')):
                    try:
                        with os.scandir(entry.path) as sub_entries:
                            for sub in sub_entries:
                                if sub.is_file():
                                    sub_ext = os.path.splitext(sub.name)[1].lower()
                                    if sub_ext in constants.VIDEO_EXTENSIONS and not sub.name.endswith(".part"):
                                        return True
                    except OSError:
                        pass
    except OSError:
        pass
    return False


def scan_ongoing_anime(
    settings: Optional[Dict[str, Any]] = None,
    active_only: bool = True
) -> List[Dict[str, Any]]:
    """Memindai sumber anime ongoing dan menyinkronkannya dengan tabel ongoing_watchlist.
    
    Sumber pemindaian:
    1. Subfolder di `ongoing_path` yang memiliki berkas video aktif.
    2. Anime dalam database `anime_library` yang berstatus 'RELEASING'.
    3. Anime yang ditandai secara manual di `ongoing_watchlist`.

    Fitur pembersihan & deduplikasi:
    - Menghapus entri ongoing_folder yang foldernya sudah tidak ada di disk.
    - Menghapus entri library_releasing yang statusnya sudah bukan RELEASING di library.
    - Mengabaikan anime yang sudah berstatus FINISHED di library dan koleksi lokal sudah lengkap.
    - Deduplikasi otomatis untuk alias judul yang merujuk pada folder fisik lokal yang sama.
    """
    settings = settings or load_settings()

    # 1. Pindai folder ongoing_path
    ongoing_dir = str(settings.get("ongoing_path", "")).strip()
    valid_ongoing_folders = set()
    if ongoing_dir and os.path.isdir(ongoing_dir):
        try:
            with os.scandir(ongoing_dir) as entries:
                for entry in entries:
                    if entry.is_dir() and not entry.name.startswith(('.', '$')):
                        if _has_video_files(entry.path):
                            valid_ongoing_folders.add(entry.name)
                            upsert_ongoing_watchlist(
                                anime_name=entry.name,
                                is_tracked=1,
                                source="ongoing_folder"
                            )
        except Exception as e:
            app_log(f"Error scanning ongoing_path '{ongoing_dir}': {e}", "WARN")

        # Bersihkan record usang dengan source ongoing_folder jika foldernya sudah tidak ada di disk
        try:
            with db_connection() as conn:
                cursor = conn.execute("SELECT anime_name FROM ongoing_watchlist WHERE source = 'ongoing_folder'")
                stale_folders = [r[0] for r in cursor.fetchall() if r and r[0] not in valid_ongoing_folders]
                for sf in stale_folders:
                    conn.execute("DELETE FROM ongoing_watchlist WHERE anime_name = ? AND source = 'ongoing_folder'", (sf,))
        except Exception as e:
            app_log(f"Error cleaning stale ongoing_folder records: {e}", "DEBUG")

    # 2. Pindai anime dalam library dengan status RELEASING
    releasing_names = set()
    finished_anime = {}
    try:
        with db_connection() as conn:
            cursor = conn.execute("SELECT name FROM anime_library WHERE UPPER(status) = 'RELEASING'")
            releasing_names = {r[0] for r in cursor.fetchall() if r and r[0]}
            for r_name in releasing_names:
                upsert_ongoing_watchlist(
                    anime_name=r_name,
                    is_tracked=1,
                    source="library_releasing",
                    conn=conn
                )

            # Bersihkan record usang source library_releasing yang sudah bukan RELEASING
            cursor = conn.execute("SELECT anime_name FROM ongoing_watchlist WHERE source = 'library_releasing'")
            stale_lib = [r[0] for r in cursor.fetchall() if r and r[0] not in releasing_names]
            for sl in stale_lib:
                conn.execute("DELETE FROM ongoing_watchlist WHERE anime_name = ? AND source = 'library_releasing'", (sl,))

            # Ambil anime berstatus FINISHED di library
            cursor = conn.execute("SELECT name, episodes FROM anime_library WHERE UPPER(status) = 'FINISHED'")
            finished_anime = {r[0]: (r[1] or 0) for r in cursor.fetchall() if r and r[0]}
    except Exception as e:
        app_log(f"Error querying releasing anime from library: {e}", "DEBUG")

    # 3. Bersihkan anime FINISHED dan ghost manual entries dari watchlist
    try:
        with db_connection() as conn:
            # Jika anime sudah FINISHED di library dan tidak berada di folder ongoing aktif
            for fin_name in finished_anime:
                if fin_name not in valid_ongoing_folders:
                    conn.execute("DELETE FROM ongoing_watchlist WHERE anime_name = ? AND source != 'manual'", (fin_name,))

            # Bersihkan entri ghost manual yang tidak memiliki URL, tidak ada episode, dan bukan di library
            cursor = conn.execute("SELECT anime_name, samehadaku_url, last_known_episode, latest_samehadaku_ep FROM ongoing_watchlist WHERE source = 'manual'")
            for m_row in cursor.fetchall():
                m_name, m_url, m_last, m_lat = m_row
                if not m_url and (m_last or 0) == 0 and (m_lat or 0) == 0 and m_name not in valid_ongoing_folders and m_name not in releasing_names:
                    chk = get_anime_local_episodes(m_name)
                    if not chk.get("matched") or chk.get("count", 0) == 0:
                        conn.execute("DELETE FROM ongoing_watchlist WHERE anime_name = ? AND source = 'manual'", (m_name,))
    except Exception as e:
        app_log(f"Error pruning completed/ghost watchlist items: {e}", "DEBUG")

    # 4. Ambil anime dari tabel ongoing_watchlist
    tracked_items = get_ongoing_watchlist(active_only=active_only)

    deduped_map: Dict[str, Dict[str, Any]] = {}
    duplicates_to_delete: List[str] = []

    for item in tracked_items:
        anime_name = item["anime_name"]
        local_info = get_anime_local_episodes(anime_name)
        local_eps = local_info.get("existing_episodes") or local_info.get("episodes") or []
        highest_local = max(local_eps) if local_eps else 0.0
        folder_path = local_info.get("folder") or ""

        # Tentukan kunci deduplikasi berdasarkan folder fisik lokal yang cocok
        if local_info.get("matched") and folder_path:
            canonical_key = os.path.normcase(os.path.abspath(folder_path))
            canonical_name = local_info.get("anime_name") or os.path.basename(folder_path) or anime_name
        else:
            canonical_key = "title:" + re.sub(r'[^a-zA-Z0-9]', '', anime_name.lower())
            canonical_name = anime_name

        latest_same = float(item.get("latest_samehadaku_ep") or 0.0)
        same_url = item.get("samehadaku_url") or ""

        if canonical_key in deduped_map:
            # Ditemukan duplikat (misal 'Doumo Suki' vs nama panjang Jepang untuk folder yang sama)
            existing = deduped_map[canonical_key]
            if not existing.get("samehadaku_url") and same_url:
                existing["samehadaku_url"] = same_url
            if latest_same > existing.get("latest_samehadaku_ep", 0.0):
                existing["latest_samehadaku_ep"] = latest_same
            if highest_local > existing.get("highest_local_ep", 0.0):
                existing["highest_local_ep"] = highest_local
                existing["local_episodes"] = local_eps
            if anime_name != existing.get("anime_name"):
                duplicates_to_delete.append(anime_name)
            continue

        # Sinkronkan last_known_episode di database
        if highest_local != item.get("last_known_episode"):
            update_ongoing_watchlist_check(
                anime_name=anime_name,
                last_known_episode=highest_local
            )

        deduped_map[canonical_key] = {
            "anime_name": canonical_name,
            "original_name": anime_name,
            "samehadaku_url": same_url,
            "local_episodes": local_eps,
            "highest_local_ep": highest_local,
            "latest_samehadaku_ep": latest_same,
            "is_tracked": bool(item.get("is_tracked", True)),
            "source": item.get("source", "auto"),
            "last_checked_at": item.get("last_checked_at", 0),
            "created_at": item.get("created_at", 0),
        }

    # Hapus row duplikat dari basis data agar tidak muncul lagi
    if duplicates_to_delete:
        try:
            with db_connection() as conn:
                for d_name in duplicates_to_delete:
                    conn.execute("DELETE FROM ongoing_watchlist WHERE anime_name = ?", (d_name,))
        except Exception as e:
            app_log(f"Error deleting duplicate ongoing watchlist entries: {e}", "DEBUG")

    results = []
    for entry in deduped_map.values():
        highest_local = entry["highest_local_ep"]
        latest_same = entry["latest_samehadaku_ep"]
        local_eps = entry["local_episodes"]
        has_new = bool(latest_same > highest_local and latest_same > 0)

        missing = []
        if latest_same > highest_local:
            start_check = int(highest_local) + 1 if highest_local >= 1 else 1
            for n in range(start_check, int(latest_same) + 1):
                if float(n) not in local_eps and n not in local_eps:
                    missing.append(n)

        entry["has_new_episode"] = has_new
        entry["missing_episodes"] = missing
        results.append(entry)

    return results


def check_ongoing_releases(
    deep: bool = False,
    timeout: int = 10,
    settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Memeriksa episode baru yang rilis di Samehadaku untuk anime ongoing yang dipantau.
    
    Langkah:
    1. Mengambil rilisan terbaru di halaman utama Samehadaku (cepat, 1 HTTP call).
    2. Mencocokkan rilisan dengan anime yang dipantau.
    3. Jika nomor episode rilis > nomor episode lokal tertinggi, simpan notifikasi baru!
    4. Jika `deep=True`, anime yang belum terdeteksi di rilisan terbaru akan diperiksa
       langsung ke halaman serialnya di Samehadaku.
    """
    settings = settings or load_settings()

    tracked_list = scan_ongoing_anime(settings)
    active_tracked = {item["anime_name"].lower(): item for item in tracked_list if item.get("is_tracked")}
    
    new_alerts: List[Dict[str, Any]] = []
    matched_anime_names: set = set()

    # Step A: Cek Rilisan Episode Terbaru di Beranda Samehadaku
    pages_to_check = [1, 2] if deep else [1]
    latest_releases: List[Dict[str, Any]] = []
    for page in pages_to_check:
        try:
            releases = get_samehadaku_latest_episodes(page=page, timeout=timeout)
            latest_releases.extend(releases)
        except Exception as err:
            app_log(f"Failed to fetch Samehadaku latest page {page}: {err}", "WARN")

    for release in latest_releases:
        title = release.get("title", "").strip()
        ep_raw = release.get("episode_number") or release.get("episode") or ""
        ep_num = extract_episode_number(ep_raw)
        if not title or ep_num is None:
            continue

        # Cocokkan judul anime dengan daftar pantau aktif
        matched_target_name = None
        matched_item = None

        # 1. Coba pencocokan langsung
        for t_lower, t_item in active_tracked.items():
            t_orig = t_item["anime_name"]
            if t_lower in title.lower() or title.lower() in t_lower:
                matched_target_name = t_orig
                matched_item = t_item
                break

        # 2. Coba pencocokan via find_matching_library_anime
        if not matched_target_name:
            match_res = find_matching_library_anime(title, settings=settings)
            if match_res and match_res.get("name"):
                cand_name = match_res["name"]
                if cand_name.lower() in active_tracked:
                    matched_target_name = cand_name
                    matched_item = active_tracked[cand_name.lower()]

        if not matched_target_name or not matched_item:
            continue

        matched_anime_names.add(matched_target_name.lower())

        # Bandingkan episode rilis dengan koleksi lokal
        local_info = get_anime_local_episodes(matched_target_name)
        local_eps = local_info.get("existing_episodes") or local_info.get("episodes") or []
        highest_local = max(local_eps) if local_eps else 0.0

        # Jika episode rilis lebih baru dari koleksi lokal
        if ep_num > highest_local or (ep_num not in local_eps and int(ep_num) not in local_eps):
            notif_id = save_episode_notification(
                anime_name=matched_target_name,
                episode_number=str(int(ep_num) if ep_num.is_integer() else ep_num),
                episode_title=title,
                episode_url=release.get("episode_url") or "",
                thumbnail=release.get("thumbnail") or "",
                released_on=release.get("released_on") or ""
            )

            # Perbarui informasi pelacak di database
            update_ongoing_watchlist_check(
                anime_name=matched_target_name,
                latest_samehadaku_ep=ep_num,
                samehadaku_url=release.get("anime_url") or matched_item.get("samehadaku_url") or ""
            )

            if notif_id is not None:
                alert_obj = {
                    "id": notif_id,
                    "anime_name": matched_target_name,
                    "episode_number": str(int(ep_num) if ep_num.is_integer() else ep_num),
                    "episode_title": title,
                    "episode_url": release.get("episode_url") or "",
                    "thumbnail": release.get("thumbnail") or "",
                    "released_on": release.get("released_on") or "",
                    "highest_local_ep": highest_local,
                    "is_new": True,
                }
                new_alerts.append(alert_obj)
                app_log(
                    f"New episode alert for '{matched_target_name}': Episode {ep_num} released on Samehadaku!",
                    "INFO"
                )

    # Step B: Jika Deep check aktif, periksa anime aktif yang belum ditemukan di rilisan terbaru
    if deep:
        for t_lower, t_item in active_tracked.items():
            if t_lower in matched_anime_names:
                continue

            anime_name = t_item["anime_name"]
            samehadaku_url = t_item.get("samehadaku_url")

            # Cari URL anime jika belum ada
            if not samehadaku_url:
                try:
                    search_results = search_samehadaku(anime_name, timeout=timeout)
                    if search_results:
                        samehadaku_url = search_results[0].get("url") or ""
                except Exception as e:
                    app_log(f"Deep check search failed for '{anime_name}': {e}", "DEBUG")

            if not samehadaku_url:
                continue

            try:
                details = get_samehadaku_anime_details(samehadaku_url, timeout=timeout)
                ep_list = details.get("episodes", [])
                if not ep_list:
                    continue

                local_info = get_anime_local_episodes(anime_name)
                local_eps = local_info.get("existing_episodes") or local_info.get("episodes") or []
                highest_local = max(local_eps) if local_eps else 0.0

                for ep_entry in ep_list:
                    ep_n = extract_episode_number(ep_entry.get("episode_number") or ep_entry.get("title"))
                    if ep_n and (ep_n > highest_local or (ep_n not in local_eps and int(ep_n) not in local_eps)):
                        notif_id = save_episode_notification(
                            anime_name=anime_name,
                            episode_number=str(int(ep_n) if ep_n.is_integer() else ep_n),
                            episode_title=ep_entry.get("title") or f"{anime_name} Episode {ep_n}",
                            episode_url=ep_entry.get("url") or "",
                            thumbnail=details.get("poster") or "",
                            released_on=ep_entry.get("date") or ""
                        )
                        if notif_id is not None:
                            new_alerts.append({
                                "id": notif_id,
                                "anime_name": anime_name,
                                "episode_number": str(int(ep_n) if ep_n.is_integer() else ep_n),
                                "episode_title": ep_entry.get("title") or f"{anime_name} Episode {ep_n}",
                                "episode_url": ep_entry.get("url") or "",
                                "thumbnail": details.get("poster") or "",
                                "released_on": ep_entry.get("date") or "",
                                "highest_local_ep": highest_local,
                                "is_new": True,
                            })

                # Perbarui latest_samehadaku_ep di database
                all_ep_nums = [extract_episode_number(e.get("episode_number") or e.get("title")) for e in ep_list]
                valid_ep_nums = [x for x in all_ep_nums if x is not None]
                if valid_ep_nums:
                    update_ongoing_watchlist_check(
                        anime_name=anime_name,
                        latest_samehadaku_ep=max(valid_ep_nums),
                        samehadaku_url=samehadaku_url
                    )
            except Exception as e:
                app_log(f"Deep check details failed for '{anime_name}': {e}", "DEBUG")

    # Ambil ulang data terkini
    refreshed_list = scan_ongoing_anime(settings)
    unread_count = get_unread_notification_count()

    return {
        "checked_count": len(tracked_list),
        "new_alerts_count": len(new_alerts),
        "new_alerts": new_alerts,
        "unread_notifications_count": unread_count,
        "tracked_anime": refreshed_list,
        "checked_at": time.time(),
    }


def on_download_completed(anime_name: str, file_path_or_filename: str):
    """Callback yang dipanggil ketika sebuah unduhan anime selesai.
    
    Otomatis menandai notifikasi episode terkait sebagai sudah diunduh.
    """
    if not anime_name or not file_path_or_filename:
        return
    basename = os.path.basename(file_path_or_filename)
    ep_num = extract_episode_number(basename)
    if ep_num is not None:
        try:
            affected = mark_notification_downloaded(anime_name, ep_num)
            if affected > 0:
                app_log(f"Auto-marked notification for '{anime_name}' Ep {ep_num} as downloaded", "INFO")
        except Exception as e:
            app_log(f"Failed to auto-mark notification as downloaded: {e}", "DEBUG")


def quick_download_episode_alert(
    anime_name: str,
    episode_url: str,
    preferred_quality: str = "1080p",
    preferred_format: str = "MKV"
) -> Dict[str, Any]:
    """Memulai unduhan langsung untuk notifikasi episode baru dalam satu langkah."""
    link_info = resolve_best_direct_link(episode_url, preferred_quality, preferred_format)
    if not link_info:
        return {
            "ok": False,
            "error": "Tidak dapat menemukan tautan unduhan langsung (Pixeldrain/Mediafire) untuk episode ini."
        }

    return start_download(
        download_url=link_info["url"],
        filename=link_info.get("filename") or f"{anime_name} - {preferred_quality}.mkv",
        anime_name=anime_name,
        episode_label=link_info.get("quality") or preferred_quality
    )


def watchlist_tracker_worker():
    """Worker thread yang berjalan di latar belakang secara berkala untuk memantau anime ongoing."""
    app_log("Ongoing Watchlist Tracker background worker started", "INFO")

    # Tunggu sebentar saat startup awal agar database & server siap sepenuhnya
    if wait_for_shutdown(10):
        return

    while not is_shutdown_requested():
        try:
            settings = load_settings()
            if settings.get("ongoing_tracker_enabled", True):
                check_ongoing_releases(deep=False, timeout=10, settings=settings)
        except Exception as err:
            app_log(f"Error in watchlist tracker worker loop: {err}", "ERROR")

        settings = load_settings()
        interval_mins = max(5, int(settings.get("ongoing_tracker_interval_minutes", 30)))
        interval_secs = interval_mins * 60

        if wait_for_shutdown(interval_secs):
            break

    app_log("Ongoing Watchlist Tracker background worker stopped", "INFO")


def start_watchlist_tracker_worker():
    """Memulai worker thread Ongoing Watchlist Tracker jika belum berjalan."""
    if is_shutdown_requested():
        return

    with constants.WATCHLIST_TRACKER_THREAD_LOCK:
        if constants.WATCHLIST_TRACKER_THREAD is not None and constants.WATCHLIST_TRACKER_THREAD.is_alive():
            return

        constants.WATCHLIST_TRACKER_THREAD = threading.Thread(
            target=watchlist_tracker_worker,
            name="ongoing-tracker",
            daemon=True
        )
        constants.WATCHLIST_TRACKER_THREAD.start()
