# anibase/watcher.py
"""Watchdog filesystem observer, debounced library sync, auto-import worker and background tasks."""

import os
import re
import time
import shutil
import threading
from datetime import datetime
from difflib import SequenceMatcher
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.settings import (
    load_settings,
    save_settings,
    get_configured_auto_import_destination,
)
from anibase.utils import (
    normalize_library_path,
    wait_for_shutdown,
    is_shutdown_requested,
    request_shutdown,
)
from anibase.scanner import (
    get_anime_folder_index,
    get_valid_anime_paths,
    sync_anime_to_db,
    sync_all_library,
)


def normalize_title_for_match(value):
    value = os.path.splitext(os.path.basename(value or ""))[0]
    value = re.sub(r'[\[\(【].*?[\]\)】]', ' ', value)
    value = re.sub(r'(?i)\b(?:lendrive|kusonime|samehadaku|dualsubs?|multi subs?|hevc|x264|x265|h264|h265|aac|flac|web-dl|webdl|bd|bluray|1080p|720p|480p|2160p|4k)\b', ' ', value)
    value = re.sub(r'(?i)\b(?:episode|episodes|eps?|e)\s*\d+\b', ' ', value)
    value = re.sub(r'(?i)(?:^|[\s._-])(?:s\d{1,2})?\d{1,3}(?:v\d+)?(?:$|[\s._-])', ' ', value)
    value = re.sub(r'[_\-.]+', ' ', value)
    value = re.sub(r'[^a-zA-Z0-9\s]', ' ', value)
    return ' '.join(value.split())


def get_match_key(value):
    return re.sub(r'[^a-z0-9]', '', (value or '').casefold())


def clean_auto_import_folder_name(clean_title):
    cleaned = re.sub(r'[<>:"/\\|?*]', ' ', clean_title or '')
    return ' '.join(cleaned.split())


def get_title_similarity(short_title, folder_title):
    short_key = get_match_key(short_title)
    folder_key = get_match_key(folder_title)
    if not short_key or not folder_key:
        return 0

    if short_key == folder_key:
        return 1

    if short_key in folder_key:
        return 0.94

    short_words = set(normalize_title_for_match(short_title).casefold().split())
    folder_words = set(normalize_title_for_match(folder_title).casefold().split())
    word_score = 0
    if short_words and folder_words:
        word_score = len(short_words & folder_words) / len(short_words)

    ratio = SequenceMatcher(None, short_key, folder_key).ratio()
    return max(ratio, word_score)


def get_auto_import_target_root(settings=None):
    settings = settings if isinstance(settings, dict) else load_settings()
    destination_root = get_configured_auto_import_destination(settings)
    if destination_root and os.path.isdir(destination_root):
        return destination_root
    return ""


def get_auto_import_candidate_folders(settings):
    target_root = get_auto_import_target_root(settings)
    if not target_root:
        return []

    target_key = os.path.normcase(os.path.realpath(os.path.abspath(target_root)))
    return [
        folder
        for folder in get_anime_folder_index()
        if os.path.normcase(
            os.path.realpath(os.path.abspath(folder.get("base_path", "")))
        ) == target_key
    ]


def resolve_auto_import_target(clean_title, settings):
    mappings = settings.get("auto_import_mappings", {})
    mapped_title = mappings.get(clean_title)
    if not mapped_title:
        clean_key = get_match_key(clean_title)
        for mapping_key, mapping_value in mappings.items():
            if get_match_key(mapping_key) == clean_key:
                mapped_title = mapping_value
                break

    folders = get_auto_import_candidate_folders(settings)
    if mapped_title:
        for folder in folders:
            if folder["name"] == mapped_title:
                return folder, 1, "manual_mapping"

    best_folder = None
    best_score = 0
    for folder in folders:
        score = get_title_similarity(clean_title, folder["name"])
        if score > best_score:
            best_score = score
            best_folder = folder

    if best_folder and best_score >= 0.72:
        return best_folder, best_score, "similarity"

    return None, best_score, "unmatched"


def create_auto_import_ongoing_target(clean_title, settings):
    if not settings.get("auto_import_create_ongoing_folders"):
        return None

    target_root = get_auto_import_target_root(settings)
    if not target_root:
        return None

    folder_name = clean_auto_import_folder_name(clean_title)
    if len(get_match_key(folder_name)) < 3:
        return None

    target_path = os.path.join(target_root, folder_name)
    os.makedirs(target_path, exist_ok=True)
    return {
        "name": folder_name,
        "path": target_path,
        "base_path": target_root
    }


def unique_destination_path(folder_path, filename):
    stem, ext = os.path.splitext(filename)
    destination = os.path.join(folder_path, filename)
    index = 2

    while os.path.exists(destination):
        destination = os.path.join(folder_path, f"{stem} ({index}){ext}")
        index += 1

    return destination


def update_auto_import_settings_state(recent=None, unmatched=None):
    with _constants.AUTO_IMPORT_STATE_LOCK:
        settings = load_settings()

        if recent:
            existing_recent = settings.get("auto_import_recent_imports", [])
            settings["auto_import_recent_imports"] = (recent + existing_recent)[:25]

        if unmatched:
            existing_unmatched = settings.get("auto_import_unmatched", [])
            unmatched_by_path = {
                item.get("source_path"): item
                for item in existing_unmatched
                if item.get("source_path")
            }
            for item in unmatched:
                source_path = item.get("source_path")
                if source_path:
                    unmatched_by_path[source_path] = item
            settings["auto_import_unmatched"] = list(unmatched_by_path.values())[-50:]

        save_settings(settings)


def remove_auto_import_unmatched(source_path):
    with _constants.AUTO_IMPORT_STATE_LOCK:
        settings = load_settings()
        settings["auto_import_unmatched"] = [
            item
            for item in settings.get("auto_import_unmatched", [])
            if item.get("source_path") != source_path
        ]
        save_settings(settings)


def clear_auto_import_unmatched():
    """Clear pending unmatched records without deleting their source files."""
    with _constants.AUTO_IMPORT_STATE_LOCK:
        settings = load_settings()
        unmatched = settings.get("auto_import_unmatched", [])
        removed_count = len(unmatched) if isinstance(unmatched, list) else 0
        settings["auto_import_unmatched"] = []
        save_settings(settings)
    return removed_count


def is_download_temp_file(path):
    lowered = path.lower()
    return lowered.endswith(_constants.DOWNLOAD_TEMP_EXTENSIONS)


def should_log_auto_import(path, event_key, fingerprint=None):
    now = time.time()
    state_key = (path, event_key)
    state = _constants.AUTO_IMPORT_LOG_STATE.get(state_key)

    if state and state.get("fingerprint") == fingerprint:
        return False

    _constants.AUTO_IMPORT_LOG_STATE[state_key] = {
        "fingerprint": fingerprint,
        "logged_at": now
    }

    for key, value in list(_constants.AUTO_IMPORT_LOG_STATE.items()):
        if now - value.get("logged_at", now) > _constants.AUTO_IMPORT_LOG_TTL_SECONDS:
            _constants.AUTO_IMPORT_LOG_STATE.pop(key, None)

    return True


def has_related_temp_download(path):
    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    stem, _ = os.path.splitext(filename)

    try:
        for item in os.listdir(directory):
            item_lower = item.lower()
            if item.startswith(stem) and item_lower.endswith(_constants.DOWNLOAD_TEMP_EXTENSIONS):
                return True
    except OSError:
        return True

    return False


def is_file_stable_for_import(path, stable_seconds):
    try:
        size = os.path.getsize(path)
        modified_at = os.path.getmtime(path)
    except OSError:
        return False, "file is not readable yet"

    now = time.time()
    state = _constants.AUTO_IMPORT_FILE_STATE.get(path)
    if not state or state.get("size") != size:
        _constants.AUTO_IMPORT_FILE_STATE[path] = {
            "size": size,
            "modified_at": modified_at,
            "last_change": now,
        }
        return False, "waiting for file size to become stable"

    stable_for = now - state.get("last_change", now)
    if stable_for < stable_seconds:
        if should_log_auto_import(
            path,
            "stable_wait",
            (size, state.get("modified_at"), int(state.get("last_change", now)))
        ):
            debug_log(f"Auto import waiting for stable file: {os.path.basename(path)}")
        return False, f"file stable for {int(stable_for)}s"

    try:
        with open(path, "rb"):
            pass
    except OSError as e:
        return False, f"file is locked: {e}"

    return True, "ready"


def record_auto_import_unmatched(path, clean_title, reason):
    item = {
        "filename": os.path.basename(path),
        "source_path": path,
        "clean_title": clean_title,
        "reason": reason,
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    update_auto_import_settings_state(unmatched=[item])


def move_auto_import_file(source_path, target_folder, clean_title, match_score=None):
    os.makedirs(target_folder, exist_ok=True)
    destination = unique_destination_path(target_folder, os.path.basename(source_path))
    shutil.move(source_path, destination)
    _constants.AUTO_IMPORT_FILE_STATE.pop(source_path, None)
    for key in list(_constants.AUTO_IMPORT_LOG_STATE.keys()):
        if key[0] == source_path:
            _constants.AUTO_IMPORT_LOG_STATE.pop(key, None)

    anime_name = os.path.basename(target_folder)
    recent_item = {
        "filename": os.path.basename(destination),
        "from": source_path,
        "to": destination,
        "anime": anime_name,
        "clean_title": clean_title,
        "match_score": match_score,
        "imported_at": datetime.now().isoformat(timespec="seconds")
    }
    update_auto_import_settings_state(recent=[recent_item])
    remove_auto_import_unmatched(source_path)
    sync_anime_to_db(anime_name, trigger_label=f"Auto import sync for {anime_name}")
    app_log(f"Auto import moved: {os.path.basename(destination)} -> {target_folder}")
    return destination


def move_auto_import_to_unmatched(source_path, clean_title, reason):
    record_auto_import_unmatched(source_path, clean_title, reason)
    _constants.AUTO_IMPORT_FILE_STATE.pop(source_path, None)
    app_log(f"Auto import left unmatched file in place: {os.path.basename(source_path)} ({reason})", "WARN")
    return source_path


def auto_import_candidate_files(downloads_path):
    try:
        names = os.listdir(downloads_path)
    except OSError as e:
        app_log(f"Auto import cannot read downloads folder: {e}", "ERROR")
        return []

    candidates = []
    for name in names:
        path = os.path.join(downloads_path, name)
        if not os.path.isfile(path):
            continue
        lowered = name.lower()
        if is_download_temp_file(path):
            continue
        if not lowered.endswith(_constants.VIDEO_EXTENSIONS):
            continue
        candidates.append(path)

    return candidates


def is_real_path_inside(base_path, candidate_path):
    if not base_path or not candidate_path:
        return False

    base_real = os.path.normcase(os.path.realpath(os.path.abspath(base_path)))
    candidate_real = os.path.normcase(os.path.realpath(os.path.abspath(candidate_path)))

    try:
        return os.path.commonpath([base_real, candidate_real]) == base_real
    except ValueError:
        return False


def is_valid_auto_import_resolve_source(source_path, settings):
    if not source_path:
        return False

    source_real = os.path.realpath(os.path.abspath(source_path))

    if not os.path.isfile(source_real):
        return False

    if not source_real.lower().endswith(_constants.VIDEO_EXTENSIONS):
        return False

    downloads_path = normalize_library_path(settings.get("auto_import_downloads_path"))
    if downloads_path and os.path.isdir(downloads_path):
        if is_real_path_inside(downloads_path, source_real):
            return True

    for item in settings.get("auto_import_unmatched", []):
        if not isinstance(item, dict):
            continue

        unmatched_path = item.get("source_path")
        if not unmatched_path:
            continue

        unmatched_real = os.path.realpath(os.path.abspath(unmatched_path))
        if os.path.normcase(unmatched_real) == os.path.normcase(source_real):
            return os.path.isfile(unmatched_real)

    return False


def auto_import_scan_once(force_enabled=False):
    settings = load_settings()

    if not force_enabled and not settings.get("auto_import_enabled"):
        return {"processed": 0, "moved": 0, "unmatched": 0, "errors": 0}

    downloads_path = normalize_library_path(settings.get("auto_import_downloads_path"))
    if not downloads_path or not os.path.isdir(downloads_path):
        if should_log_auto_import(
            "auto_import_settings",
            "invalid_downloads_path",
            downloads_path
        ):
            app_log("Auto import inactive. Downloads folder is invalid.", "WARN")
            debug_log(f"Invalid auto-import downloads folder: {downloads_path}")
        return {"processed": 0, "moved": 0, "unmatched": 0, "errors": 1}

    stable_seconds = settings.get("auto_import_stable_seconds", 60)
    summary = {"processed": 0, "moved": 0, "unmatched": 0, "errors": 0}

    for path in auto_import_candidate_files(downloads_path):
        summary["processed"] += 1
        filename = os.path.basename(path)
        try:
            file_fingerprint = (os.path.getsize(path), os.path.getmtime(path))
        except OSError:
            file_fingerprint = None

        if should_log_auto_import(path, "detected", file_fingerprint):
            debug_log(f"Auto import detected: {filename}")

        if has_related_temp_download(path):
            if should_log_auto_import(path, "temp_wait", file_fingerprint):
                debug_log(f"Auto import waiting for temp download to finish: {filename}")
            continue

        is_stable, stable_reason = is_file_stable_for_import(path, stable_seconds)
        if not is_stable:
            continue

        clean_title = normalize_title_for_match(filename)
        target, score, match_method = resolve_auto_import_target(clean_title, settings)
        if not target:
            target = create_auto_import_ongoing_target(clean_title, settings)
            if target:
                score = 1
                match_method = "created_ongoing_folder"

        try:
            if target:
                move_auto_import_file(path, target["path"], clean_title, round(score, 3))
                summary["moved"] += 1
            else:
                reason = f"No confident match for '{clean_title}' (best score {score:.2f})."
                move_auto_import_to_unmatched(path, clean_title, reason)
                summary["unmatched"] += 1
        except OSError as e:
            summary["errors"] += 1
            record_auto_import_unmatched(path, clean_title, str(e))
            app_log(f"Auto import move error for {filename}: {e}", "ERROR")

    return summary


def auto_import_worker():
    last_enabled_state = None

    while not is_shutdown_requested():
        settings = load_settings()
        enabled = settings.get("auto_import_enabled", False)

        if enabled != last_enabled_state:
            state_label = "enabled" if enabled else "disabled"
            app_log(f"Auto import {state_label}.")
            last_enabled_state = enabled

        if enabled:
            auto_import_scan_once()
            sleep_seconds = settings.get("auto_import_interval_seconds", 15)
        else:
            sleep_seconds = 5

        if wait_for_shutdown(max(5, sleep_seconds)):
            break


def start_auto_import_worker():
    if is_shutdown_requested():
        return

    with _constants.AUTO_IMPORT_THREAD_LOCK:
        if _constants.AUTO_IMPORT_THREAD is not None and _constants.AUTO_IMPORT_THREAD.is_alive():
            return

        _constants.AUTO_IMPORT_THREAD = threading.Thread(
            target=auto_import_worker,
            name="auto-import",
            daemon=True
        )
        _constants.AUTO_IMPORT_THREAD.start()


def build_auto_import_overview(settings, anime_folders):
    downloads_path = normalize_library_path(
        settings.get("auto_import_downloads_path", "")
    )
    recent_imports = settings.get("auto_import_recent_imports", [])
    unmatched_items = settings.get("auto_import_unmatched", [])

    overview = {
        "enabled": bool(settings.get("auto_import_enabled")),
        "downloads_path": downloads_path,
        "downloads_exists": bool(downloads_path and os.path.isdir(downloads_path)),
        "destination_root": get_auto_import_target_root(settings) if downloads_path else "",
        "interval_seconds": settings.get("auto_import_interval_seconds", 15),
        "stable_seconds": settings.get("auto_import_stable_seconds", 60),
        "recent_imports": [],
        "unmatched": []
    }

    if isinstance(recent_imports, list):
        for item in recent_imports[:2]:
            if not isinstance(item, dict):
                continue
            overview["recent_imports"].append({
                "filename": item.get("filename") or os.path.basename(item.get("to", "")),
                "source": item.get("from", ""),
                "target": item.get("to", ""),
                "anime": item.get("anime", ""),
                "imported_at": item.get("imported_at", ""),
                "match_score": item.get("match_score")
            })

    if isinstance(unmatched_items, list):
        for index, item in enumerate(unmatched_items[:8]):
            if not isinstance(item, dict):
                continue

            clean_title = item.get("clean_title", "")
            suggested_target, score, match_method = resolve_auto_import_target(clean_title, settings)
            source_path = item.get("source_path", "")
            overview["unmatched"].append({
                "index": index,
                "filename": item.get("filename") or os.path.basename(source_path),
                "source_path": source_path,
                "clean_title": clean_title,
                "reason": item.get("reason", "No confident match."),
                "created_at": item.get("created_at", ""),
                "exists": bool(source_path and os.path.isfile(source_path)),
                "suggested_anime": suggested_target["name"] if suggested_target else "",
                "suggested_path": suggested_target["path"] if suggested_target else "",
                "suggested_score": round(score * 100) if score else 0,
                "match_method": match_method
            })

    return overview


def build_auto_import_mappings_from_pairs(sources, targets):
    return {
        str(source).strip(): str(target).strip()
        for source, target in zip(sources or [], targets or [])
        if str(source).strip() and str(target).strip()
    }


def run_debounced_anime_sync(anime_name):
    if is_shutdown_requested():
        return

    try:
        sync_anime_to_db(anime_name, trigger_label=f"Watchdog sync for {anime_name}")
        debug_log(f"Watchdog sync completed: {anime_name}")
    except Exception as e:
        app_log(f"Watchdog sync failed: {anime_name}: {e}", "ERROR")
    finally:
        with _constants.LIBRARY_SYNC_DEBOUNCE_LOCK:
            _constants.LIBRARY_SYNC_TIMERS.pop(anime_name, None)


def queue_anime_sync(anime_name):
    if not anime_name or is_shutdown_requested():
        return

    with _constants.LIBRARY_SYNC_DEBOUNCE_LOCK:
        existing_timer = _constants.LIBRARY_SYNC_TIMERS.get(anime_name)
        if existing_timer is not None:
            existing_timer.cancel()
            should_log_queue = False
        else:
            should_log_queue = True

        timer = threading.Timer(
            _constants.LIBRARY_SYNC_DEBOUNCE_SECONDS,
            run_debounced_anime_sync,
            args=(anime_name,)
        )
        timer.daemon = True
        _constants.LIBRARY_SYNC_TIMERS[anime_name] = timer
        timer.start()

    if should_log_queue:
        debug_log(f"Watchdog sync queued: {anime_name}")


class LibraryHandler(FileSystemEventHandler):
    def process_event(self, event_path):
        if is_shutdown_requested():
            return

        event_path = os.path.abspath(event_path)

        movie_path = normalize_library_path(_constants.MOVIE_PATH)
        if movie_path:
            try:
                if os.path.commonpath([movie_path, event_path]) == movie_path:
                    return
            except ValueError:
                pass

        for base in get_valid_anime_paths():
            try:
                if os.path.commonpath([base, event_path]) != base:
                    continue

                relative = os.path.relpath(event_path, base)
                parts = relative.split(os.sep)
                if parts and parts[0] != "." and parts[0] != "":
                    queue_anime_sync(parts[0])
                    return
            except ValueError:
                continue

        debug_log(f"Watchdog event for {event_path} did not resolve to a specific anime. Triggering full sync.")
        sync_all_library("Watchdog full library sync")

    def on_created(self, event):
        self.process_event(event.src_path)

    def on_deleted(self, event):
        self.process_event(event.src_path)

    def on_moved(self, event):
        self.process_event(event.src_path)
        self.process_event(event.dest_path)

    def on_modified(self, event):
        if event.is_directory:
            self.process_event(event.src_path)
        elif any(event.src_path.lower().endswith(ext) for ext in _constants.VIDEO_EXTENSIONS):
            self.process_event(event.src_path)


def start_scanner():
    """Run initial sync and start the watchdog observer."""
    app_log("Performing initial full library sync...")
    sync_all_library("Initial library sync")
    app_log("Initial full library sync complete.")

    reconfigure_library_observer()
    try:
        while not wait_for_shutdown(1):
            pass
    except KeyboardInterrupt:
        stop_library_observer()
    finally:
        stop_library_observer()


def stop_library_observer(timeout=5):
    with _constants.LIBRARY_OBSERVER_LOCK:
        if _constants.LIBRARY_OBSERVER is None:
            with _constants.LIBRARY_SYNC_DEBOUNCE_LOCK:
                for timer in _constants.LIBRARY_SYNC_TIMERS.values():
                    timer.cancel()
                _constants.LIBRARY_SYNC_TIMERS.clear()
            return

        _constants.LIBRARY_OBSERVER.stop()
        _constants.LIBRARY_OBSERVER.join(timeout=timeout)
        _constants.LIBRARY_OBSERVER = None

    with _constants.LIBRARY_SYNC_DEBOUNCE_LOCK:
        for timer in _constants.LIBRARY_SYNC_TIMERS.values():
            timer.cancel()
        _constants.LIBRARY_SYNC_TIMERS.clear()


def reconfigure_library_observer():
    if is_shutdown_requested():
        stop_library_observer()
        return

    with _constants.LIBRARY_OBSERVER_LOCK:
        if _constants.LIBRARY_OBSERVER is not None:
            _constants.LIBRARY_OBSERVER.stop()
            _constants.LIBRARY_OBSERVER.join(timeout=5)
            _constants.LIBRARY_OBSERVER = None
            with _constants.LIBRARY_SYNC_DEBOUNCE_LOCK:
                for timer in _constants.LIBRARY_SYNC_TIMERS.values():
                    timer.cancel()
                _constants.LIBRARY_SYNC_TIMERS.clear()

        valid_paths = get_valid_anime_paths()
        if not valid_paths:
            app_log("Watchdog inactive. No valid anime folders configured.", "WARN")
            return

        observer = Observer()
        handler = LibraryHandler()
        for path in valid_paths:
            observer.schedule(handler, path, recursive=True)
            debug_log(f"Monitoring folder: {path}")

        observer.start()
        _constants.LIBRARY_OBSERVER = observer


def periodic_sync_task(interval_seconds=900):
    """Melakukan sinkronisasi penuh secara berkala sebagai pengaman."""
    while not is_shutdown_requested():
        if wait_for_shutdown(interval_seconds):
            break
        try:
            debug_log("Performing periodic full library sync...")
            sync_all_library("Periodic library sync")
            debug_log("Periodic full library sync complete.")
        except Exception as e:
            app_log(f"Periodic sync failed: {e}", "ERROR")


def get_auto_import_thread():
    with _constants.AUTO_IMPORT_THREAD_LOCK:
        return _constants.AUTO_IMPORT_THREAD


def join_thread_with_timeout(thread, timeout, label):
    if thread is None:
        return True

    thread.join(timeout=max(0, timeout or 0))
    if thread.is_alive():
        app_log(f"Shutdown timed out waiting for {label}.", "WARN")
        return False

    return True


def join_background_workers(threads=None, timeout=5):
    request_shutdown()
    stop_library_observer(timeout=timeout)

    all_stopped = True
    for label, thread in (threads or {}).items():
        all_stopped = join_thread_with_timeout(thread, timeout, label) and all_stopped

    auto_thread = get_auto_import_thread()
    all_stopped = join_thread_with_timeout(auto_thread, timeout, "auto-import") and all_stopped
    all_stopped = join_thread_with_timeout(_constants.RPC_MONITOR_THREAD, timeout, "discord-presence") and all_stopped
    return all_stopped


def should_start_background_workers(debug_enabled=False):
    if debug_enabled and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    return True
