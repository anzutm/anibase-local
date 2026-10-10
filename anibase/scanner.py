# anibase/scanner.py
"""Library scanner, path inspection, cache cleanup, and library database synchronization."""

import os
import re
import json
import shutil
import hashlib
from datetime import datetime

import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    normalize_library_path,
    is_resolved_path_inside,
    safe_cache_name,
    load_json_dict_if_exists,
    is_shutdown_requested,
    wait_for_shutdown,
    get_active_cache_dir,
    get_protected_cache_files,
)
from anibase.db import db_connection, count_library_rows
from anibase.watch import (
    load_history_data,
    load_watch_status,
    cleanup_watch_data_for_anime,
)
from anibase.metadata import (
    get_cached_anilist_info,
    get_cached_metadata_only,
)


def get_valid_anime_paths():
    valid_paths = []
    for base_path in _constants.ANIME_PATHS:
        normalized_path = normalize_library_path(base_path)
        if normalized_path and os.path.isdir(normalized_path):
            valid_paths.append(normalized_path)
    return valid_paths


def get_configured_anime_paths():
    configured_paths = []
    seen_paths = set()
    for path in _constants.ANIME_PATHS:
        normalized_path = normalize_library_path(path)
        if not normalized_path:
            continue
        path_key = os.path.normcase(normalized_path)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        configured_paths.append(normalized_path)
    return configured_paths


def collect_library_scan_roots(configured_paths):
    scan_roots = []
    failed_roots = []
    for base_path in configured_paths:
        if not os.path.isdir(base_path):
            failed_roots.append({
                "path": base_path,
                "reason": "folder is not available"
            })
            continue
        try:
            entries = os.listdir(base_path)
        except OSError as e:
            failed_roots.append({
                "path": base_path,
                "reason": str(e)
            })
            continue
        scan_roots.append({
            "path": base_path,
            "entries": entries
        })
    return scan_roots, failed_roots


def is_configured_movie_folder(path):
    movie_path = normalize_library_path(_constants.MOVIE_PATH)
    candidate = normalize_library_path(path)
    if not movie_path or not candidate:
        return False
    return os.path.normcase(candidate) == os.path.normcase(movie_path)


def is_anime_library_folder(path):
    if not os.path.isdir(path):
        return False
    folder_name = os.path.basename(os.path.normpath(path))
    if folder_name.lower() == "_unmatched downloads":
        return False
    return not is_configured_movie_folder(path)


def is_configured_movie_folder_name(folder_name):
    if not folder_name:
        return False
    for base_path in get_valid_anime_paths():
        if is_configured_movie_folder(os.path.join(base_path, folder_name)):
            return True
    return False


def get_existing_anime_names():
    existing_names = set()
    for base_path in get_valid_anime_paths():
        try:
            for name in os.listdir(base_path):
                full_path = os.path.join(base_path, name)
                if is_anime_library_folder(full_path):
                    existing_names.add(name)
        except OSError:
            continue
    return existing_names


def find_anime_path(anime_name):
    if (
        not isinstance(anime_name, str)
        or not anime_name
        or anime_name in {".", ".."}
        or "\x00" in anime_name
        or "/" in anime_name
        or "\\" in anime_name
        or os.path.isabs(anime_name)
        or re.match(r"^[a-zA-Z]:", anime_name)
    ):
        return None

    for base_path in get_valid_anime_paths():
        base_real = os.path.realpath(os.path.abspath(base_path))
        anime_path = os.path.realpath(
            os.path.abspath(
                os.path.join(base_real, anime_name)
            )
        )
        if (
            not is_resolved_path_inside(base_real, anime_path)
            or os.path.normcase(os.path.dirname(anime_path)) != os.path.normcase(base_real)
        ):
            continue
        if is_anime_library_folder(anime_path):
            return anime_path
    return None


def find_media_path(library_name):
    if library_name == "Movies":
        if _constants.MOVIE_PATH and os.path.isdir(_constants.MOVIE_PATH):
            return _constants.MOVIE_PATH
        return None
    return find_anime_path(library_name)


def get_anime_folder_index():
    folders = []
    for base_path in get_valid_anime_paths():
        try:
            for name in os.listdir(base_path):
                full_path = os.path.join(base_path, name)
                if is_anime_library_folder(full_path):
                    folders.append({
                        "name": name,
                        "path": full_path,
                        "base_path": base_path
                    })
        except OSError:
            continue
    return folders


def is_path_inside_cache(path):
    cache_root = get_active_cache_dir()
    candidate = os.path.abspath(path)
    try:
        return os.path.commonpath([cache_root, candidate]) == cache_root
    except ValueError:
        return False


def is_protected_cache_file(path):
    candidate = os.path.abspath(path)
    if candidate in get_protected_cache_files():
        return True
    cache_root = get_active_cache_dir()
    try:
        if os.path.commonpath([cache_root, candidate]) != cache_root:
            return False
    except ValueError:
        return False
    filename = os.path.basename(candidate)
    return (
        filename.startswith("watch_history.json.")
        or filename.startswith("watch_status.json.")
        or filename.startswith(".watch_history.json.tmp-")
        or filename.startswith(".watch_status.json.tmp-")
        or filename.startswith("library.db-")
    )


def make_cache_cleanup_summary():
    return {
        "removed_files": 0,
        "removed_dirs": 0,
        "removed_watch_entries": 0,
        "skipped": 0,
        "details": []
    }


def remove_cache_file_if_safe(summary, path, label):
    if is_protected_cache_file(path):
        summary["skipped"] += 1
        summary["details"].append(f"Skipped protected cache file: {label}")
        debug_log(f"Cache cleanup skipped protected file: {path}")
        return
    if not is_path_inside_cache(path) or not os.path.isfile(path):
        summary["skipped"] += 1
        summary["details"].append(f"Skipped unsafe file: {label}")
        return
    try:
        os.remove(path)
        summary["removed_files"] += 1
        summary["details"].append(f"Removed file: {label}")
    except OSError as e:
        summary["skipped"] += 1
        summary["details"].append(f"Skipped file {label}: {e}")


def remove_cache_dir_if_safe(summary, path, label):
    if not is_path_inside_cache(path) or not os.path.isdir(path):
        summary["skipped"] += 1
        summary["details"].append(f"Skipped unsafe folder: {label}")
        return
    try:
        shutil.rmtree(path)
        summary["removed_dirs"] += 1
        summary["details"].append(f"Removed folder: {label}")
    except OSError as e:
        summary["skipped"] += 1
        summary["details"].append(f"Skipped folder {label}: {e}")


def add_thumbnail_candidate_paths(candidates, anime_name, episode):
    if not anime_name or not episode:
        return
    normalized_episode = str(episode).replace("/", os.sep).replace("\\", os.sep)
    for base_path in get_valid_anime_paths():
        candidates.add(os.path.abspath(os.path.join(base_path, anime_name, normalized_episode)))


def collect_cached_episode_candidates(anime_name, safe_name):
    candidates = set()
    history = load_history_data()
    for history_key, data in history.items():
        if not isinstance(data, dict):
            continue
        media_name = data.get("media_name") or history_key
        if media_name == anime_name or safe_cache_name(media_name) == safe_name:
            add_thumbnail_candidate_paths(candidates, anime_name, data.get("episode"))

    status_data = load_watch_status()
    for status_key, episodes in status_data.items():
        if status_key != anime_name and safe_cache_name(status_key) != safe_name:
            continue
        if not isinstance(episodes, dict):
            continue
        for episode in episodes.keys():
            add_thumbnail_candidate_paths(candidates, anime_name, episode)

    if os.path.isdir(_constants.EPISODE_CACHE):
        for filename in os.listdir(_constants.EPISODE_CACHE):
            if not filename.lower().endswith(".json"):
                continue
            stem = os.path.splitext(filename)[0]
            if stem != safe_name and not stem.startswith(f"{safe_name}_"):
                continue
            season_name = None
            if stem.startswith(f"{safe_name}_"):
                season_name = stem[len(safe_name) + 1:].replace("_", os.sep)
            cache_data = load_json_dict_if_exists(os.path.join(_constants.EPISODE_CACHE, filename))
            for episode_file in cache_data.keys():
                add_thumbnail_candidate_paths(candidates, anime_name, episode_file)
                if season_name:
                    add_thumbnail_candidate_paths(
                        candidates,
                        anime_name,
                        os.path.join(season_name, episode_file)
                    )
    return candidates


def cleanup_thumbnails_for_video_paths(video_paths, summary):
    for video_path in video_paths:
        stem = hashlib.md5(video_path.encode("utf-8")).hexdigest()
        for filename in (stem + ".jpg", stem + "_v2.jpg"):
            thumbnail_path = os.path.join(_constants.THUMBNAIL_CACHE, filename)
            if os.path.isfile(thumbnail_path):
                remove_cache_file_if_safe(summary, thumbnail_path, f"thumbnails/{filename}")
        preview_path = os.path.join(_constants.THUMBNAIL_CACHE, "seek", stem)
        if os.path.isdir(preview_path):
            remove_cache_dir_if_safe(summary, preview_path, f"thumbnails/seek/{stem}")


def cleanup_anime_cache(anime_name, summary=None, include_watch_data=False):
    summary = summary or make_cache_cleanup_summary()
    safe_name = safe_cache_name(anime_name)
    if not safe_name:
        summary["skipped"] += 1
        summary["details"].append("Skipped anime cache cleanup because anime name is empty.")
        return summary

    thumbnail_candidates = collect_cached_episode_candidates(anime_name, safe_name)

    for cache_dir, label, extension in (
        (_constants.POSTER_CACHE, "posters", ".jpg"),
        (_constants.BANNER_CACHE, "banners", ".jpg"),
        (_constants.METADATA_CACHE, "metadata", ".json"),
    ):
        remove_cache_file_if_safe(
            summary,
            os.path.join(cache_dir, f"{safe_name}{extension}"),
            f"{label}/{safe_name}{extension}"
        )

    for cache_dir, label in (
        (_constants.CHARACTER_CACHE, "characters"),
        (_constants.SUBTITLE_CACHE, "subtitles"),
    ):
        remove_cache_dir_if_safe(
            summary,
            os.path.join(cache_dir, safe_name),
            f"{label}/{safe_name}"
        )

    if os.path.isdir(_constants.EPISODE_CACHE):
        for filename in os.listdir(_constants.EPISODE_CACHE):
            if not filename.lower().endswith(".json"):
                continue
            stem = os.path.splitext(filename)[0]
            if stem == safe_name or stem.startswith(f"{safe_name}_"):
                remove_cache_file_if_safe(
                    summary,
                    os.path.join(_constants.EPISODE_CACHE, filename),
                    f"episodes/{filename}"
                )

    cleanup_thumbnails_for_video_paths(thumbnail_candidates, summary)
    if include_watch_data:
        cleanup_watch_data_for_anime(anime_name, safe_name, summary)
    return summary


def cleanup_orphan_cache():
    summary = make_cache_cleanup_summary()
    existing_anime_names = get_existing_anime_names()
    existing_safe_names = {
        safe_cache_name(name)
        for name in existing_anime_names
    }
    available_base_paths = [
        path
        for path in get_valid_anime_paths()
    ]
    if not available_base_paths:
        summary["skipped"] += 1
        summary["details"].append(
            "Skipped cleanup because no configured anime folders are available."
        )
        return summary

    def clean_named_files(cache_dir, label):
        if not os.path.isdir(cache_dir):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped missing cache folder: {label}")
            return
        for filename in os.listdir(cache_dir):
            path = os.path.join(cache_dir, filename)
            if not os.path.isfile(path):
                summary["skipped"] += 1
                continue
            stem, _ = os.path.splitext(filename)
            if stem not in existing_safe_names:
                remove_cache_file_if_safe(summary, path, f"{label}/{filename}")

    def clean_named_dirs(cache_dir, label):
        if not os.path.isdir(cache_dir):
            summary["skipped"] += 1
            summary["details"].append(f"Skipped missing cache folder: {label}")
            return
        for folder_name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, folder_name)
            if not os.path.isdir(path):
                summary["skipped"] += 1
                continue
            if folder_name not in existing_safe_names:
                remove_cache_dir_if_safe(summary, path, f"{label}/{folder_name}")

    def clean_episode_files():
        if not os.path.isdir(_constants.EPISODE_CACHE):
            summary["skipped"] += 1
            summary["details"].append("Skipped missing cache folder: episodes")
            return
        for filename in os.listdir(_constants.EPISODE_CACHE):
            path = os.path.join(_constants.EPISODE_CACHE, filename)
            if not os.path.isfile(path) or not filename.lower().endswith(".json"):
                summary["skipped"] += 1
                continue
            stem = os.path.splitext(filename)[0]
            matches_existing = any(
                stem == safe_name or stem.startswith(f"{safe_name}_")
                for safe_name in existing_safe_names
            )
            if not matches_existing:
                remove_cache_file_if_safe(summary, path, f"episodes/{filename}")

    def clean_orphan_thumbnails():
        candidate_paths = set()
        history = load_history_data()
        status_data = load_watch_status()
        for history_key, data in history.items():
            if not isinstance(data, dict):
                continue
            media_name = data.get("media_name") or history_key
            if safe_cache_name(media_name) in existing_safe_names:
                continue
            add_thumbnail_candidate_paths(candidate_paths, media_name, data.get("episode"))

        for status_key, episodes in status_data.items():
            if safe_cache_name(status_key) in existing_safe_names or not isinstance(episodes, dict):
                continue
            for episode in episodes.keys():
                add_thumbnail_candidate_paths(candidate_paths, status_key, episode)

        if os.path.isdir(_constants.EPISODE_CACHE):
            for filename in os.listdir(_constants.EPISODE_CACHE):
                if not filename.lower().endswith(".json"):
                    continue
                stem = os.path.splitext(filename)[0]
                if any(stem == safe_name or stem.startswith(f"{safe_name}_") for safe_name in existing_safe_names):
                    continue
                anime_safe_name = stem.split("_", 1)[0]
                anime_name = next(
                    (name for name in existing_anime_names if safe_cache_name(name) == anime_safe_name),
                    anime_safe_name
                )
                cache_data = load_json_dict_if_exists(os.path.join(_constants.EPISODE_CACHE, filename))
                for episode_file in cache_data.keys():
                    add_thumbnail_candidate_paths(candidate_paths, anime_name, episode_file)

        cleanup_thumbnails_for_video_paths(candidate_paths, summary)
        preview_root = os.path.join(_constants.THUMBNAIL_CACHE, "seek")
        if os.path.isdir(preview_root):
            for stem in os.listdir(preview_root):
                if not re.fullmatch(r"[a-f0-9]{32}", stem):
                    continue
                folder = os.path.join(preview_root, stem)
                manifest = load_json_dict_if_exists(os.path.join(folder, "manifest.json"))
                source = manifest.get("source")
                if not isinstance(source, str) or os.path.isfile(source):
                    continue
                for root in available_base_paths:
                    try:
                        belongs = os.path.commonpath([os.path.abspath(source), os.path.abspath(root)]) == os.path.abspath(root)
                    except ValueError:
                        belongs = False
                    if belongs:
                        remove_cache_dir_if_safe(summary, folder, f"thumbnails/seek/{stem}")
                        break

    clean_named_files(_constants.POSTER_CACHE, "posters")
    clean_named_files(_constants.BANNER_CACHE, "banners")
    clean_named_files(_constants.METADATA_CACHE, "metadata")
    clean_named_dirs(_constants.CHARACTER_CACHE, "characters")
    clean_named_dirs(_constants.SUBTITLE_CACHE, "subtitles")
    clean_orphan_thumbnails()
    clean_episode_files()
    summary["skipped"] += 1
    summary["details"].append(
        "Skipped protected settings/database files, watch data, and watch data backups."
    )
    return summary


def make_library_sync_skipped_result(trigger_label):
    return {
        "ok": False,
        "skipped": True,
        "reason": "sync_in_progress",
        "message": f"{trigger_label} skipped because another library sync is already running."
    }


def acquire_library_sync(trigger_label):
    if is_shutdown_requested():
        app_log(
            f"{trigger_label} skipped because shutdown is in progress.",
            "WARN"
        )
        return False

    if _constants.LIBRARY_SYNC_LOCK.acquire(blocking=False):
        return True

    app_log(
        f"{trigger_label} skipped because another library sync is already running.",
        "WARN"
    )
    return False


def sync_anime_to_db(anime_name, trigger_label=None):
    """Memindai satu folder anime dan memperbarui database SQLite."""
    trigger_label = trigger_label or f"Anime sync for {anime_name}"
    if not acquire_library_sync(trigger_label):
        return make_library_sync_skipped_result(trigger_label)

    try:
        anime_path = find_anime_path(anime_name)
        if not anime_path:
            configured_paths = get_configured_anime_paths()
            _, failed_roots = collect_library_scan_roots(configured_paths)
            if failed_roots:
                root_list = "; ".join(
                    f"{item['path']} ({item['reason']})"
                    for item in failed_roots
                )
                app_log(
                    f"Skipped removal for missing anime {anime_name} because "
                    f"not every configured library root is safely scannable: {root_list}",
                    "WARN"
                )
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "library_roots_untrusted",
                    "anime_name": anime_name,
                    "failed_roots": failed_roots
                }

            with db_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("DELETE FROM anime_library WHERE name = ?", (anime_name,))
            summary = cleanup_anime_cache(anime_name)
            app_log(
                f"Cleaned cache for deleted anime {anime_name}: "
                f"{summary['removed_files']} files, {summary['removed_dirs']} folders, "
                "watch data preserved."
            )
            return {"ok": True, "skipped": False, "anime_name": anime_name}

        episode_count = 0
        for root, dirs, files in os.walk(anime_path):
            for file in files:
                if file.lower().endswith(_constants.VIDEO_EXTENSIONS):
                    episode_count += 1

        info = get_cached_anilist_info(anime_name)

        with db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO anime_library (name, episodes, score, genres, year, season, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                anime_name,
                episode_count,
                info.get("score") if info else None,
                json.dumps(info.get("genres")) if info else None,
                info.get("year") if info else None,
                info.get("season") if info else None,
                info.get("status") if info else None
            ))
        return {"ok": True, "skipped": False, "anime_name": anime_name}
    finally:
        _constants.LIBRARY_SYNC_LOCK.release()


def sync_all_library(trigger_label="Full library sync", enrich_metadata=True, progress_callback=None):
    """Pemindaian penuh seluruh folder anime di latar belakang."""
    if not acquire_library_sync(trigger_label):
        return make_library_sync_skipped_result(trigger_label)

    try:
        debug_log("Syncing library...")
        found_data = []
        found_names = []
        configured_paths = get_configured_anime_paths()
        previous_row_count = count_library_rows()
        scan_roots, failed_roots = collect_library_scan_roots(configured_paths)
        scan_errors = []

        total_anime = sum(
            1
            for root_info in scan_roots
            for name in root_info["entries"]
            if is_anime_library_folder(os.path.join(root_info["path"], name))
        )
        processed_anime = 0
        if progress_callback:
            progress_callback("metadata" if enrich_metadata else "scan", 0, total_anime)

        if not scan_roots:
            app_log("Sync skipped. No valid anime folders configured.", "WARN")
            return {"ok": True, "skipped": True, "reason": "no_valid_library_paths"}

        can_delete_stale = not failed_roots

        for root_info in scan_roots:
            if is_shutdown_requested():
                break
            base_path = root_info["path"]
            for name in root_info["entries"]:
                if is_shutdown_requested():
                    break
                full_path = os.path.join(base_path, name)
                if is_anime_library_folder(full_path):
                    found_names.append(name)

                    episode_count = 0
                    walk_errors = []
                    def record_walk_error(error):
                        walk_errors.append(error)

                    for root, dirs, files in os.walk(full_path, onerror=record_walk_error):
                        for file in files:
                            if file.lower().endswith(_constants.VIDEO_EXTENSIONS):
                                episode_count += 1

                    if walk_errors:
                        for error in walk_errors:
                            scan_errors.append({
                                "path": getattr(error, "filename", full_path) or full_path,
                                "reason": str(error)
                            })
                        continue

                    if enrich_metadata:
                        cache_file = os.path.join(_constants.METADATA_CACHE, f"{name}.json")
                        info = get_cached_anilist_info(name)
                        if not os.path.exists(cache_file) and wait_for_shutdown(0.7):
                            break
                    else:
                        info = get_cached_metadata_only(name)

                    found_data.append((
                        name,
                        episode_count,
                        info.get("score") if info else None,
                        json.dumps(info.get("genres")) if info else None,
                        info.get("year") if info else None,
                        info.get("season") if info else None,
                        info.get("status") if info else None
                    ))
                    processed_anime += 1
                    if progress_callback:
                        progress_callback(
                            "metadata" if enrich_metadata else "scan",
                            processed_anime,
                            total_anime
                        )

        if scan_errors:
            can_delete_stale = False

        if previous_row_count > 0 and not found_names:
            can_delete_stale = False
            app_log(
                "Full sync found zero anime while existing library data is present. "
                "Stale database/cache cleanup was skipped to protect temporary drive outages.",
                "WARN"
            )

        if failed_roots:
            root_list = "; ".join(
                f"{item['path']} ({item['reason']})"
                for item in failed_roots
            )
            app_log(
                f"Full sync could not scan every configured root. "
                f"Stale database/cache cleanup was skipped: {root_list}",
                "WARN"
            )

        if scan_errors:
            error_list = "; ".join(
                f"{item['path']} ({item['reason']})"
                for item in scan_errors
            )
            app_log(
                f"Full sync encountered filesystem errors. "
                f"Stale database/cache cleanup was skipped: {error_list}",
                "WARN"
            )

        stale_names = []

        with db_connection() as conn:
            if can_delete_stale:
                if found_names:
                    placeholders = ','.join(['?'] * len(found_names))
                    stale_rows = conn.execute(
                        f"SELECT name FROM anime_library WHERE name NOT IN ({placeholders})",
                        found_names
                    ).fetchall()
                else:
                    stale_rows = conn.execute("SELECT name FROM anime_library").fetchall()

                stale_names = [
                    row[0]
                    for row in stale_rows
                    if row and row[0]
                ]

            if found_data:
                conn.executemany("""
                    INSERT OR REPLACE INTO anime_library (name, episodes, score, genres, year, season, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, found_data)

            if can_delete_stale and found_names:
                placeholders = ','.join(['?'] * len(found_names))
                conn.execute(f"DELETE FROM anime_library WHERE name NOT IN ({placeholders})", found_names)
            elif can_delete_stale:
                conn.execute("DELETE FROM anime_library")

        if not can_delete_stale:
            stale_names = []

        for stale_name in stale_names:
            summary = cleanup_anime_cache(stale_name)
            app_log(
                f"Cleaned stale cache for {stale_name}: "
                f"{summary['removed_files']} files, {summary['removed_dirs']} folders, "
                "watch data preserved."
            )

        app_log(f"Sync complete. Detected {len(found_names)} anime.")
        return {
            "ok": True,
            "skipped": False,
            "anime_count": len(found_names),
            "stale_count": len(stale_names),
            "stale_cleanup_skipped": not can_delete_stale,
            "failed_roots": failed_roots,
            "scan_errors": scan_errors
        }
    finally:
        _constants.LIBRARY_SYNC_LOCK.release()


def update_setup_sync_state(**changes):
    with _constants.SETUP_SYNC_STATE_LOCK:
        _constants.SETUP_SYNC_STATE.update(changes)


def get_setup_sync_state():
    with _constants.SETUP_SYNC_STATE_LOCK:
        return dict(_constants.SETUP_SYNC_STATE)


def run_setup_metadata_job(anime_count):
    try:
        def report_progress(stage, current, total):
            update_setup_sync_state(
                stage=stage,
                current=current,
                total=total
            )

        sync_all_library(
            trigger_label="Setup metadata sync",
            enrich_metadata=True,
            progress_callback=report_progress
        )
        update_setup_sync_state(
            running=False,
            done=True,
            stage="complete",
            current=anime_count,
            total=anime_count,
            anime_count=anime_count
        )
    except Exception as e:
        app_log(f"Setup sync failed: {e}", "ERROR")
        update_setup_sync_state(
            running=False,
            done=True,
            stage="error",
            error="Setup sync failed. Check the server log and try again."
        )
