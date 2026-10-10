# anibase/web/routes_api.py
"""API and streaming routes for AniBase."""

import os
import sys
import re
import json
import sqlite3
import time
import secrets
import base64
import shutil
import hashlib
import mimetypes
import subprocess
import threading
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse, quote_plus

import requests
from flask import (
    request,
    jsonify,
    redirect,
    url_for,
    abort,
    send_file,
    Response,
)

import anibase.constants as constants
from anibase.constants import (
    CACHE_DIR,
    SETTINGS_BACKUP_SCHEMA_VERSION,
    DEFAULT_PLAYER_SHORTCUTS,
    THEME_PRESETS,
    AUTO_IMPORT_STATE_LOCK,
    LIBRARY_SYNC_LOCK,
    ANILIST_METADATA_PROVIDER,
    TENRAI_METADATA_PROVIDER,
    METADATA_CACHE,
    POSTER_CACHE,
    BANNER_CACHE,
    VIDEO_EXTENSIONS,
    IMAGE_PROXY_CACHE,
    THUMBNAIL_CACHE,
    DEFAULT_MAL_CLIENT_ID,
    MAL_OAUTH_SESSIONS,
    MAL_OAUTH_AUTH_URL,
    MAL_OAUTH_TOKEN_URL,
    MAX_SCREENSHOT_DATA_URL_BYTES,
    RPC_STATE_LOCK,
)
from anibase.logging import app_log
from anibase.utils import (
    atomic_write_json_file,
    normalize_library_paths,
    normalize_shortcuts,
    normalize_screenshot_filename_prefix,
    normalize_int_setting,
    normalize_library_path,
    normalize_episode_path_key,
    clean_movie_title,
    safe_join_media_path,
    popen_hidden_subprocess,
)
from anibase.settings import (
    load_settings,
    get_default_settings,
    save_settings,
    get_effective_mal_client_id,
)
from anibase.db import db_connection, count_library_rows
from anibase.watch import (
    mark_episode_watched,
    mark_episode_unwatched,
    update_episode_watch_status,
    remove_watch_history_entry,
    load_watch_status,
    save_watch_status,
    db_remove_episode_watch_status,
    update_watch_history,
    get_watch_history_key,
    get_watch_history_display_name,
    get_episode_watch_status,
)
from anibase.ffmpeg import (
    get_media_dependency_diagnostics,
    media_generation_error_response,
)
from anibase.player import diagnose_vlc_path
from anibase.media import (
    get_episode_number,
    normalize_episode_number,
    get_episode_default_display_name,
    format_timestamp,
)
from anibase.thumbnails import (
    seek_preview_identity,
    read_seek_preview,
    request_seek_preview,
    get_thumbnail_result,
)
from anibase.subtitles import (
    get_subtitle_vtt_path,
    get_ass_subtitle_assets,
    subtitle_cache_is_current,
    generate_subtitle_vtt_result,
    get_available_subtitle_tracks,
)
from anibase.metadata import (
    get_cached_airing_schedule,
    build_schedule_items,
    get_schedule_alert_payload,
    normalize_provider_id,
    normalize_anime_metadata_identity,
    save_metadata_mapping,
    invalidate_anilist_cache,
    remove_anilist_mapping,
    get_metadata_mapping,
    get_season_metadata_name,
    save_episode_display_override,
    remove_episode_display_overrides,
    get_anilist_poster,
    sanitize_external_url,
    get_cached_anilist_info,
    get_episode_display_override,
)
from anibase.integrations.anilist import (
    search_anilist_anime,
    get_anilist_info,
)
from anibase.integrations.tenrai import (
    search_tenrai_anime,
    get_tenrai_anime_info,
)
from anibase.integrations.aniskip import get_aniskip_times
from anibase.integrations.mal import (
    cleanup_mal_oauth_sessions,
    fetch_mal_user_profile,
    save_mal_auth,
    clear_mal_auth,
    load_mal_auth,
    is_mal_authenticated,
    clear_mal_profile_cache,
    get_mal_user_full_profile,
    update_mal_user_anime_status,
    trigger_mal_scrobble_if_enabled,
)
from anibase.integrations.discord import (
    start_external_discord_presence,
    clear_discord_rpc_when_process_exits,
    accept_discord_presence_event,
    dispatch_discord_rpc_task,
    dispatch_discord_rpc_clear,
)
from anibase.scanner import (
    get_setup_sync_state,
    update_setup_sync_state,
    sync_all_library,
    run_setup_metadata_job,
    find_anime_path,
    find_media_path,
    cleanup_orphan_cache,
    cleanup_anime_cache,
)
from anibase.watcher import (
    build_auto_import_mappings_from_pairs,
    reconfigure_library_observer,
    start_auto_import_worker,
    auto_import_scan_once,
    is_valid_auto_import_resolve_source,
    normalize_title_for_match,
    move_auto_import_file,
    record_auto_import_unmatched,
    clear_auto_import_unmatched,
)
from anibase.web.helpers import (
    host_only,
    require_action_token,
    json_error,
    get_json_body,
    apply_settings,
    is_local_request,
    get_watch_status_payload,
    pick_windows_path,
    get_character_image_cache_path,
)


@host_only
@require_action_token
def setup_sync():
    if request.method == "POST":
        state = get_setup_sync_state()
        if not state["running"]:
            update_setup_sync_state(
                running=True,
                done=False,
                error="",
                stage="scan",
                current=0,
                total=0,
                anime_count=0
            )
            try:
                sync_result = sync_all_library(
                    "Setup local library sync",
                    enrich_metadata=False,
                    progress_callback=lambda stage, current, total: update_setup_sync_state(
                        stage=stage,
                        current=current,
                        total=total
                    )
                )
                if sync_result.get("reason") == "sync_in_progress":
                    update_setup_sync_state(running=False)
                    return jsonify({
                        "ok": False,
                        "error": "sync_in_progress",
                        "message": sync_result.get("message")
                    }), 409

                with db_connection() as conn:
                    anime_count = conn.execute("SELECT COUNT(*) FROM anime_library").fetchone()[0]
                reconfigure_library_observer()
                update_setup_sync_state(
                    stage="metadata",
                    current=0,
                    total=anime_count,
                    anime_count=anime_count
                )
                threading.Thread(
                    target=run_setup_metadata_job,
                    args=(anime_count,),
                    name="setup-metadata-sync",
                    daemon=True
                ).start()
            except Exception as e:
                app_log(f"Setup sync failed: {e}", "ERROR")
                update_setup_sync_state(running=False, done=True, stage="error")
                return json_error(
                    "setup_sync_failed",
                    "Setup sync failed. Check the server log and try again.",
                    500
                )

    state = get_setup_sync_state()
    return jsonify({"ok": not bool(state["error"]), **state})


@host_only
def export_settings_backup():
    """Download portable personal settings and mappings as a JSON backup."""
    settings = load_settings()
    backup_settings = dict(settings)
    backup_settings.pop("action_token", None)
    payload = {
        "format": "anibase-settings-backup",
        "version": SETTINGS_BACKUP_SCHEMA_VERSION,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "settings": backup_settings,
    }
    backup_path = os.path.join(CACHE_DIR, "anibase-settings-backup.json")
    atomic_write_json_file(backup_path, payload, "Settings backup")
    return send_file(
        backup_path,
        as_attachment=True,
        download_name="anibase-settings-backup.json",
        mimetype="application/json",
    )


@host_only
@require_action_token
def import_settings_backup():
    upload = request.files.get("backup_file")
    if not upload or not upload.filename:
        return redirect("/settings?backup_error=No+backup+file+selected")
    try:
        payload = json.load(upload.stream)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return redirect("/settings?backup_error=Backup+file+is+not+valid+JSON")
    if not isinstance(payload, dict) or payload.get("format") != "anibase-settings-backup":
        return redirect("/settings?backup_error=Invalid+AniBase+backup+file")
    imported = payload.get("settings")
    if not isinstance(imported, dict):
        return redirect("/settings?backup_error=Backup+does+not+contain+settings")

    current = load_settings()
    imported.pop("action_token", None)
    merged = get_default_settings()
    merged.update(current)
    merged.update(imported)
    merged["action_token"] = current.get("action_token", "")
    save_settings(merged)
    apply_settings(load_settings())
    reconfigure_library_observer()
    start_auto_import_worker()
    return redirect("/settings?backup_imported=1")


@host_only
@require_action_token
def refresh_media_diagnostics_settings():
    get_media_dependency_diagnostics(load_settings(), force=True)
    return redirect("/settings?diagnostics_refreshed=1")


@host_only
@require_action_token
def update_settings():
    existing_settings = load_settings()
    mapping_sources = request.form.getlist("auto_import_mapping_source")
    mapping_targets = request.form.getlist("auto_import_mapping_target")
    auto_import_mappings = build_auto_import_mappings_from_pairs(
        mapping_sources,
        mapping_targets
    )

    library_paths = normalize_library_paths(request.form.getlist("library_paths"))

    submitted_shortcuts = {}
    has_shortcut_fields = False
    for action_id in DEFAULT_PLAYER_SHORTCUTS:
        field_name = f"shortcut_{action_id}"
        if field_name in request.form:
            has_shortcut_fields = True
            val = request.form.get(field_name, "").strip()
            if val:
                submitted_shortcuts[action_id] = val
    if has_shortcut_fields:
        effective_shortcuts = normalize_shortcuts(submitted_shortcuts)
    else:
        effective_shortcuts = normalize_shortcuts(existing_settings.get("shortcuts"))

    settings = {
        "setup_completed": True,
        "library_paths": library_paths,
        "watchlist_path": library_paths[0] if library_paths else "",
        "ongoing_path": library_paths[1] if len(library_paths) > 1 else "",
        "movie_path": request.form.get("movie_path", "").strip(),
        "vlc_path": request.form.get("vlc_path", "").strip(),
        "screenshot_folder": request.form.get("screenshot_folder", "").strip(),
        "screenshot_filename_prefix": normalize_screenshot_filename_prefix(
            request.form.get("screenshot_filename_prefix"),
            "vlcsnap"
        ),
        "discord_rpc_enabled": "discord_rpc_enabled" in request.form,
        "discord_client_id": request.form.get("discord_client_id", "").strip(),
        "discord_timer_mode": "elapsed" if request.form.get("discord_timer_mode", existing_settings.get("discord_timer_mode")) == "elapsed" else "remaining",
        "lan_access_enabled": "lan_access_enabled" in request.form,
        "theme_preset": request.form.get("theme_preset", "dark-blue").strip(),
        "auto_import_enabled": "auto_import_enabled" in request.form,
        "auto_import_downloads_path": request.form.get("auto_import_downloads_path", "").strip(),
        "auto_import_destination_root": request.form.get("auto_import_destination_root", "").strip(),
        "auto_import_interval_seconds": normalize_int_setting(
            request.form.get("auto_import_interval_seconds"),
            existing_settings.get("auto_import_interval_seconds", 15),
            5,
            3600
        ),
        "auto_import_stable_seconds": normalize_int_setting(
            request.form.get("auto_import_stable_seconds"),
            existing_settings.get("auto_import_stable_seconds", 60),
            10,
            86400
        ),
        "auto_import_create_ongoing_folders": "auto_import_create_ongoing_folders" in request.form,
        "auto_import_mappings": auto_import_mappings,
        "auto_import_recent_imports": existing_settings.get("auto_import_recent_imports", []),
        "auto_import_unmatched": existing_settings.get("auto_import_unmatched", []),
        "anilist_mappings": existing_settings.get("anilist_mappings", {}),
        "episode_display_names": existing_settings.get("episode_display_names", {}),
        "action_token": existing_settings.get("action_token", ""),
        "mal_scrobble_enabled": "mal_scrobble_enabled" in request.form,
        "mal_client_id": request.form.get("mal_client_id", "").strip(),
        "mal_scrobble_threshold": normalize_int_setting(
            request.form.get("mal_scrobble_threshold"),
            existing_settings.get("mal_scrobble_threshold", 90),
            50,
            100
        ),
        "shortcuts": effective_shortcuts
    }

    if settings["theme_preset"] not in THEME_PRESETS:
        settings["theme_preset"] = "dark-blue"

    save_settings(settings)
    apply_settings(settings)
    reconfigure_library_observer()
    start_auto_import_worker()
    sync_busy = LIBRARY_SYNC_LOCK.locked()
    if sync_busy:
        app_log("Settings save sync skipped because another library sync is already running.", "WARN")
    else:
        threading.Thread(
            target=sync_all_library,
            kwargs={"trigger_label": "Settings save sync"},
            name="settings-sync",
            daemon=True
        ).start()

    if sync_busy:
        return redirect("/settings?saved=1&sync_busy=1")

    return redirect("/settings?saved=1")


@host_only
@require_action_token
def scan_auto_import_settings():
    summary = auto_import_scan_once(force_enabled=True)

    return redirect(
        "/settings?auto_import_scanned=1"
        f"&processed={summary['processed']}"
        f"&moved={summary['moved']}"
        f"&unmatched={summary['unmatched']}"
        f"&errors={summary['errors']}"
    )


@host_only
@require_action_token
def resolve_auto_import_settings():
    source_path = request.form.get("source_path", "").strip()
    target_anime = request.form.get("target_anime", "").strip()
    add_mapping = "add_mapping" in request.form
    settings = load_settings()

    target_path = find_anime_path(target_anime)
    if (
        not target_path
        or not is_valid_auto_import_resolve_source(source_path, settings)
    ):
        app_log(f"Auto import manual resolve rejected unsafe source: {source_path}", "WARN")
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return json_error(
                "invalid_auto_import_source",
                "Auto import source or target is not valid.",
                422
            )
        return redirect("/settings?auto_import_scanned=1&processed=0&moved=0&unmatched=0&errors=1")

    source_path = os.path.realpath(os.path.abspath(source_path))
    clean_title = normalize_title_for_match(os.path.basename(source_path))

    try:
        move_auto_import_file(source_path, target_path, clean_title, 1)
        if add_mapping and clean_title:
            with AUTO_IMPORT_STATE_LOCK:
                settings = load_settings()
                mappings = settings.setdefault("auto_import_mappings", {})
                mappings[clean_title] = target_anime
                save_settings(settings)
        return redirect("/settings?auto_import_resolved=1")
    except OSError as e:
        record_auto_import_unmatched(source_path, clean_title, str(e))
        app_log(f"Auto import manual resolve failed: {e}", "ERROR")
        return redirect("/settings?auto_import_scanned=1&processed=0&moved=0&unmatched=0&errors=1")


@host_only
@require_action_token
def dismiss_auto_import_unmatched():
    """Remove an unmatched entry from the pending list (without moving the file)."""
    source_path = request.form.get("source_path", "").strip()

    if not source_path:
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return json_error("missing_source_path", "source_path is required.", 400)
        return redirect("/settings")

    want_json = (
        request.accept_mimetypes.accept_json
        and not request.accept_mimetypes.accept_html
    )

    with AUTO_IMPORT_STATE_LOCK:
        settings = load_settings()
        unmatched = settings.get("auto_import_unmatched", [])
        source_norm = os.path.normcase(os.path.realpath(os.path.abspath(source_path)))

        new_list = [
            item for item in unmatched
            if isinstance(item, dict) and os.path.normcase(
                os.path.realpath(os.path.abspath(item.get("source_path", "")))
            ) != source_norm
        ]

        if len(new_list) == len(unmatched):
            if want_json:
                return jsonify({"ok": True, "removed": False})
            return redirect("/settings?auto_import_dismissed=1")

        settings["auto_import_unmatched"] = new_list
        save_settings(settings)

    app_log(f"Auto import unmatched dismissed: {source_path}", "INFO")

    if want_json:
        return jsonify({"ok": True, "removed": True})
    return redirect("/settings?auto_import_dismissed=1")


@host_only
@require_action_token
def dismiss_all_auto_import_unmatched():
    """Clear all unmatched entries without deleting any source files."""
    removed_count = clear_auto_import_unmatched()
    app_log(f"Auto import unmatched list cleared: {removed_count} entries", "INFO")

    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"ok": True, "removed_count": removed_count})
    return redirect("/settings?auto_import_dismissed_all=1")


@host_only
@require_action_token
def cleanup_cache_settings():
    summary = cleanup_orphan_cache()

    return redirect(
        "/settings?cache_cleaned=1"
        f"&removed_files={summary['removed_files']}"
        f"&removed_dirs={summary['removed_dirs']}"
        f"&removed_watch_entries={summary['removed_watch_entries']}"
        f"&skipped={summary['skipped']}"
    )


@host_only
def pick_settings_folder():
    return pick_windows_path("folder")


@host_only
def pick_settings_file():
    return pick_windows_path("file")


def schedule_alerts():
    local_tz = datetime.now().astimezone().tzinfo
    now_dt = datetime.now(local_tz)
    now_ts = int(now_dt.timestamp())
    timezone_offset_minutes = int(now_dt.utcoffset().total_seconds() // 60)
    airing_list, schedule_error = get_cached_airing_schedule()

    if schedule_error:
        return jsonify({
            "ok": False,
            "error": schedule_error,
            "items": [],
            "badge_count": 0,
            "summary": "Schedule unavailable",
            "now_iso": now_dt.isoformat(),
            "timezone_offset_minutes": timezone_offset_minutes
        }), 502

    today_key = now_dt.strftime("%Y-%m-%d")
    processed = [
        item for item in build_schedule_items(airing_list, local_tz, now_ts)
        if item["date_key"] == today_key
    ]
    payload = get_schedule_alert_payload(
        processed,
        now_ts,
        now_dt.isoformat(),
        timezone_offset_minutes
    )
    payload["ok"] = True
    return jsonify(payload)


@host_only
def api_anilist_search():
    query_text = request.args.get("q", "").strip()
    media_type = request.args.get("media_type", "").strip().lower()
    if len(query_text) < 2:
        return json_error(
            "invalid_search_query",
            "Enter at least two characters to search AniList.",
            400,
        )
    if len(query_text) > 120:
        return json_error(
            "invalid_search_query",
            "Search query is too long.",
            400,
        )

    results, error = search_anilist_anime(query_text)
    provider = ANILIST_METADATA_PROVIDER
    if error or not results:
        fallback_results, fallback_error = search_tenrai_anime(query_text)
        if fallback_results:
            results = fallback_results
            provider = TENRAI_METADATA_PROVIDER
            error = None
        elif error:
            return json_error("metadata_search_failed", fallback_error or error, 502)
    if media_type == "movie":
        results = [item for item in results if str(item.get("format") or "").upper() == "MOVIE"]
    return jsonify({"ok": True, "provider": provider, "results": results})


@host_only
@require_action_token
def api_update_anime_metadata_match():
    data, error_response = get_json_body()
    if error_response:
        return error_response

    anime_name = str(data.get("anime_name") or "").strip()
    is_movie = str(data.get("media_type") or "").strip().lower() == "movie"
    movie_filename = str(data.get("movie_filename") or "").strip()
    if is_movie:
        movie_path = safe_join_media_path(constants.MOVIE_PATH, movie_filename) if anime_name and movie_filename else None
        if (
            not movie_path
            or not os.path.isfile(movie_path)
            or not movie_path.lower().endswith(VIDEO_EXTENSIONS)
            or clean_movie_title(movie_filename) != anime_name
        ):
            return json_error("movie_not_found", "Movie file was not found.", 404)
        anime_path = None
    else:
        anime_path = find_anime_path(anime_name) if anime_name else None
        if not anime_path:
            return json_error("anime_not_found", "Anime folder was not found.", 404)

    season_name = str(data.get("season_name") or "").strip()
    metadata_name = anime_name
    if season_name and not is_movie:
        season_path = safe_join_media_path(anime_path, season_name)
        if (
            not season_path
            or not os.path.isdir(season_path)
            or os.path.normcase(os.path.dirname(season_path)) != os.path.normcase(anime_path)
        ):
            return json_error("season_not_found", "Season folder was not found.", 404)
        metadata_name = get_season_metadata_name(anime_name, season_name)

    if data.get("automatic") is True:
        removed = remove_anilist_mapping(metadata_name)
        invalidate_anilist_cache(metadata_name)
        return jsonify({
            "ok": True,
            "automatic": True,
            "mapping_removed": removed,
        })

    provider = str(data.get("provider") or ANILIST_METADATA_PROVIDER).strip().lower()
    if provider == TENRAI_METADATA_PROVIDER:
        mal_id = normalize_provider_id(data.get("mal_id"))
        if mal_id is None:
            return json_error("invalid_mal_id", "Select a valid Tenrai title.", 400)
        metadata = normalize_anime_metadata_identity(
            get_tenrai_anime_info(metadata_name, mal_id=mal_id)
        )
        valid_match = metadata and metadata.get("metadata_provider") == TENRAI_METADATA_PROVIDER \
            and metadata.get("mal_id") == mal_id
    else:
        provider = ANILIST_METADATA_PROVIDER
        try:
            anilist_id = int(data.get("anilist_id"))
        except (TypeError, ValueError):
            return json_error("invalid_anilist_id", "Select a valid AniList title.", 400)
        if anilist_id <= 0:
            return json_error("invalid_anilist_id", "Select a valid AniList title.", 400)
        metadata = normalize_anime_metadata_identity(
            get_anilist_info(metadata_name, anilist_id=anilist_id)
        )
        valid_match = metadata and metadata.get("anilist_id") == anilist_id
    if not valid_match:
        return json_error(
            "metadata_fetch_failed",
            f"{provider.title()} metadata could not be loaded for that title.",
            502,
        )

    save_metadata_mapping(metadata_name, metadata, manual=True)
    invalidate_anilist_cache(metadata_name)
    cache_file = os.path.join(METADATA_CACHE, f"{metadata_name}.json")
    atomic_write_json_file(cache_file, metadata, f"{provider.title()} metadata", ensure_ascii=False)

    try:
        with db_connection() as conn:
            if not is_movie:
                conn.execute(
                    """
                    UPDATE anime_library
                    SET score = ?, genres = ?, year = ?, season = ?, status = ?
                    WHERE name = ?
                    """,
                    (
                        metadata.get("score"),
                        json.dumps(metadata.get("genres")),
                        metadata.get("year"),
                        metadata.get("season"),
                        metadata.get("status"),
                        anime_name,
                    ),
                )
    except sqlite3.Error as error:
        app_log(f"Unable to update matched metadata row for {anime_name}: {error}", "WARN")

    return jsonify({
        "ok": True,
        "mapping": get_metadata_mapping(metadata_name),
        "metadata": {
            "anilist_id": metadata.get("anilist_id"),
            "mal_id": metadata.get("mal_id"),
            "provider": metadata.get("metadata_provider"),
            "title": metadata.get("title"),
        },
    })


@host_only
@require_action_token
def api_update_episode_display_name():
    data, error_response = get_json_body()
    if error_response:
        return error_response

    anime_name = str(data.get("anime_name") or "").strip()
    episode_path = normalize_episode_path_key(data.get("episode"))
    anime_path = find_anime_path(anime_name)
    video_path = safe_join_media_path(anime_path, episode_path) if anime_path else None
    if (
        not anime_path
        or not episode_path
        or not video_path
        or not os.path.isfile(video_path)
        or not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return json_error("episode_not_found", "Episode file was not found.", 404)

    requested_name = data.get("display_name", "")
    if not isinstance(requested_name, str):
        return json_error("invalid_display_name", "Display name must be text.", 400)
    if len(requested_name) > 500:
        return json_error("invalid_display_name", "Display name is too long.", 400)

    saved_name = save_episode_display_override(
        anime_name,
        episode_path,
        requested_name,
    )
    default_name = get_episode_default_display_name(os.path.basename(episode_path))
    return jsonify({
        "ok": True,
        "display_name": saved_name or default_name,
        "custom": bool(saved_name),
        "default_name": default_name,
    })


@host_only
@require_action_token
def refresh_library():
    """Refresh the local index quickly, then enrich metadata in the background."""
    sync_result = sync_all_library(
        "Manual home library refresh",
        enrich_metadata=False
    )
    if sync_result.get("reason") == "sync_in_progress":
        return jsonify({
            "ok": False,
            "error": "sync_in_progress",
            "message": "A library refresh is already running. Try again shortly."
        }), 409

    if not sync_result.get("ok"):
        return json_error(
            "library_refresh_failed",
            "Library refresh could not be completed.",
            500
        )

    threading.Thread(
        target=sync_all_library,
        kwargs={"trigger_label": "Manual refresh metadata sync"},
        name="manual-metadata-sync",
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "anime_count": sync_result.get("anime_count", count_library_rows()),
        "message": "Library refreshed. Metadata will continue loading in the background."
    })


def poster(anime_name):
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return send_file(
            poster_path
        )

    poster_path = get_anilist_poster(
        anime_name
    )

    if poster_path:
        return send_file(
            poster_path
        )

    return "", 404


def banner(anime_name):
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    banner_path = os.path.join(
        BANNER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        banner_path
    ):
        return send_file(
            banner_path
        )

    get_anilist_poster(
        anime_name
    )

    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    if os.path.exists(
        banner_path
    ):
        return send_file(
            banner_path
        )

    poster_path = os.path.join(
        POSTER_CACHE,
        f"{safe_name}.jpg"
    )

    if os.path.exists(
        poster_path
    ):
        return send_file(
            poster_path
        )

    return "", 404


def seek_preview(anime_name, episode):
    video_path = safe_join_media_path(find_media_path(anime_name), episode)
    if (not video_path or not os.path.isfile(video_path)
            or not video_path.lower().endswith(VIDEO_EXTENSIONS)):
        abort(404)
    identity = seek_preview_identity(video_path)
    if identity is None:
        abort(404)
    folder, version = identity
    data = read_seek_preview(folder, version)
    asset = request.args.get("asset")
    if asset:
        if request.args.get("v") != version or data is None:
            abort(404)
        allowed = {frame["text"] for frame in data["frames"]}
        if asset not in allowed or not re.fullmatch(r"sprite-\d{2}\.jpg", asset):
            abort(404)
        response = send_file(os.path.join(folder, asset), mimetype="image/jpeg",
                             conditional=True, max_age=86400)
        response.cache_control.public = False
        response.cache_control.private = True
        return response
    if data is None:
        status = request_seek_preview(video_path, folder, version)
        response = jsonify(status=status)
        response.status_code = 202 if status == "pending" else 503
        response.headers["Retry-After"] = "3"
    else:
        frames = [{**frame, "text": url_for("seek_preview", anime_name=anime_name,
                   episode=episode, asset=frame["text"], v=version)}
                  for frame in data["frames"]]
        if request.args.get("format") == "vtt":
            lines = ["WEBVTT", ""]
            for frame in frames:
                lines.extend([
                    f'{format_timestamp(frame["startTime"])}.000 --> '
                    f'{format_timestamp(frame["endTime"])}.000',
                    f'{frame["text"]}#xywh={frame["x"]},{frame["y"]},{frame["w"]},{frame["h"]}', "",
                ])
            response = Response("\n".join(lines), mimetype="text/vtt")
        else:
            response = jsonify(status="ready", frames=frames)
    response.headers["Cache-Control"] = "no-store"
    return response


def thumbnail(anime_name, episode):
    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return "", 404

    episode_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not episode_path
        or not os.path.isfile(episode_path)
        or not episode_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return "", 404

    thumbnail_result = get_thumbnail_result(
        episode_path
    )

    if thumbnail_result.get("ok"):
        return send_file(
            thumbnail_result["path"]
        )

    return media_generation_error_response(thumbnail_result)


def stream_video(anime_name, episode):
    anime_path = find_media_path(
        anime_name
    )

    video_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not video_path
        or not os.path.isfile(video_path)
        or not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        app_log("Video path invalid or not found.", "WARN")
        abort(404)

    mime_type = "video/mp4"
    if not video_path.lower().endswith('.mkv'):
        mime_type = mimetypes.guess_type(video_path)[0] or "video/mp4"

    response = send_file(
        video_path,
        mimetype=mime_type,
        conditional=True,
        etag=True,
        max_age=3600,
    )
    response.cache_control.public = False
    response.cache_control.private = True
    return response


def character_img(anime_name, filename):
    img_path = get_character_image_cache_path(anime_name, filename)
    if img_path and os.path.isfile(img_path):
        return send_file(img_path)
    abort(404)


def image_proxy():
    """Serve provider images locally so browser CDN/hotlink issues do not break cards."""
    image_url = sanitize_external_url(request.args.get("url"))
    if not image_url:
        abort(400)
    parsed = urlparse(image_url)
    allowed_hosts = {"cdn.myanimelist.net", "s4.anilist.co", "img.anili.st"}
    host = (parsed.hostname or "").casefold()
    if host not in allowed_hosts:
        abort(403)

    cache_key = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
    extension = os.path.splitext(parsed.path)[1].lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        extension = ".img"
    cache_path = os.path.join(IMAGE_PROXY_CACHE, cache_key + extension)
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
        return send_file(cache_path, max_age=86400)

    try:
        upstream = requests.get(
            image_url,
            timeout=12,
            headers={"User-Agent": "AniBase/1.0 image proxy"},
        )
        if upstream.status_code != 200 or not upstream.content:
            abort(404)
        content_type = (upstream.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            abort(404)
        if len(upstream.content) > 8 * 1024 * 1024:
            abort(413)
        os.makedirs(IMAGE_PROXY_CACHE, exist_ok=True)
        with open(cache_path, "wb") as handle:
            handle.write(upstream.content)
        return send_file(BytesIO(upstream.content), mimetype=content_type, max_age=86400)
    except requests.RequestException:
        abort(404)


def get_subtitle(anime_name, episode):
    anime_path = find_media_path(anime_name)
    if not anime_path:
        abort(404)

    video_path = safe_join_media_path(anime_path, episode)
    if not video_path or not os.path.isfile(video_path):
        abort(404)

    track_id = request.args.get('track')
    if track_id:
        clean_track = track_id.strip()
        is_sidecar = clean_track.startswith('sidecar:') and not any(c in clean_track for c in ('/', '\\', '..'))
        is_stream = bool(re.match(r'^(?:0:)?\d+$', clean_track))
        if not (is_sidecar or is_stream):
            abort(400)
        track_id = clean_track

    vtt_path = get_subtitle_vtt_path(anime_name, episode, track_id=track_id)

    subtitle_format = request.args.get('format', 'vtt')
    if subtitle_format in {'manifest', 'asset'}:
        try:
            if track_id:
                folder, manifest = get_ass_subtitle_assets(video_path, os.path.dirname(vtt_path), track_id=track_id)
            else:
                folder, manifest = get_ass_subtitle_assets(video_path, os.path.dirname(vtt_path))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            app_log(f'ASS subtitle preparation failed: {error}', 'WARN')
            return json_error('subtitle_unavailable', 'Original subtitle is unavailable.', 503)
        if subtitle_format == 'manifest':
            payload = {'renderer': manifest['renderer'], 'fonts': []}
            if manifest['renderer'] == 'ass':
                def asset_url(name):
                    extra = {'track': track_id} if track_id else {}
                    return url_for('get_subtitle', anime_name=anime_name, episode=episode,
                                   format='asset', asset=name, version=os.path.basename(folder),
                                   **extra)
                payload['subtitle'] = asset_url(manifest['subtitle'])
                payload['fonts'] = [asset_url(name) for name in manifest['fonts']]
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'no-store'
            return response
        name = request.args.get('asset', '')
        allowed = [manifest.get('subtitle')] + manifest['fonts']
        if not name or name not in allowed:
            abort(404)
        return send_file(os.path.join(folder, name), mimetype='text/plain' if name == 'track.ass' else 'application/octet-stream')

    if subtitle_cache_is_current(video_path, vtt_path, track_id=track_id):
        return send_file(vtt_path, mimetype="text/vtt")

    if track_id:
        subtitle_result = generate_subtitle_vtt_result(video_path, vtt_path, track_id=track_id)
    else:
        subtitle_result = generate_subtitle_vtt_result(video_path, vtt_path)
    if subtitle_result.get("ok"):
        return send_file(subtitle_result["path"], mimetype="text/vtt")

    return media_generation_error_response(subtitle_result)


def get_media_subtitles(anime_name, episode):
    anime_path = find_media_path(anime_name)
    if not anime_path:
        return json_error("anime_not_found", "Anime was not found.", 404)

    video_path = safe_join_media_path(anime_path, episode)
    if not video_path or not os.path.isfile(video_path):
        return json_error("episode_not_found", "Episode was not found.", 404)

    try:
        track_info = get_available_subtitle_tracks(video_path)
        response = jsonify({
            "status": "success",
            "tracks": track_info.get("tracks", []),
            "default_track_id": track_info.get("default_track_id")
        })
        response.headers['Cache-Control'] = 'no-cache'
        return response
    except Exception as error:
        app_log(f"Failed to probe subtitle tracks for {anime_name}/{episode}: {error}", "WARN")
        return json_error("probe_failed", "Failed to probe subtitle tracks.", 500)


def get_episode_skip_times(anime_name, episode):
    anime_path = find_media_path(anime_name)
    if not anime_path:
        return json_error("anime_not_found", "Anime was not found.", 404)

    video_path = safe_join_media_path(anime_path, episode)
    if not video_path or not os.path.isfile(video_path):
        return json_error("episode_not_found", "Episode was not found.", 404)

    info = get_cached_anilist_info(anime_name) or {}
    mal_id = normalize_provider_id(info.get("mal_id"))
    if not mal_id:
        manual_mapping = get_metadata_mapping(anime_name)
        if manual_mapping:
            mal_id = normalize_provider_id(manual_mapping.get("mal_id"))

    if not mal_id:
        return jsonify({
            "status": "success",
            "found": False,
            "results": [],
            "reason": "no_mal_id"
        })

    ep_num = get_episode_number(os.path.basename(video_path))
    if not ep_num or ep_num <= 0:
        return jsonify({
            "status": "success",
            "found": False,
            "results": [],
            "reason": "no_episode_number"
        })

    episode_length = request.args.get("episodeLength", type=float) or 0
    skip_data = get_aniskip_times(mal_id, ep_num, episode_length=episode_length)
    response = jsonify({
        "status": "success",
        "found": skip_data.get("found", False),
        "results": skip_data.get("results", [])
    })
    response.headers["Cache-Control"] = "no-cache"
    return response


@host_only
def mal_oauth_login():
    settings = load_settings()
    if request.method == "POST":
        submitted_id = str(request.form.get("mal_client_id", "")).strip()
        if submitted_id:
            settings["mal_client_id"] = submitted_id
            save_settings(settings)

    client_id = get_effective_mal_client_id(settings)
    if not client_id:
        return redirect("/settings?mal_error=Please+configure+MyAnimeList+Client+ID+first")

    verifier = secrets.token_urlsafe(64)[:128]
    state = secrets.token_urlsafe(32)

    if client_id == DEFAULT_MAL_CLIENT_ID:
        redirect_uri = "http://localhost:5000/api/mal/callback"
    else:
        redirect_uri = url_for("mal_oauth_callback", _external=True)

    cleanup_mal_oauth_sessions()
    MAL_OAUTH_SESSIONS[state] = {
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "timestamp": time.time()
    }

    auth_url = (
        f"{MAL_OAUTH_AUTH_URL}?"
        f"response_type=code&"
        f"client_id={quote_plus(client_id)}&"
        f"code_challenge={quote_plus(verifier)}&"
        f"code_challenge_method=plain&"
        f"state={quote_plus(state)}&"
        f"redirect_uri={quote_plus(redirect_uri)}"
    )
    return redirect(auth_url)


@host_only
def mal_oauth_callback():
    error = request.args.get("error")
    if error:
        return redirect(f"/settings?mal_error={quote_plus(error)}")

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return redirect("/settings?mal_error=Missing+authorization+code+or+state")

    cleanup_mal_oauth_sessions()
    session = MAL_OAUTH_SESSIONS.pop(state, None)
    if not session:
        return redirect("/settings?mal_error=Invalid+or+expired+OAuth+session")

    verifier = session.get("verifier")
    settings = load_settings()
    client_id = get_effective_mal_client_id(settings)
    if not client_id:
        return redirect("/settings?mal_error=MAL+Client+ID+not+configured")

    redirect_uri = session.get("redirect_uri") or (
        "http://localhost:5000/api/mal/callback"
        if client_id == DEFAULT_MAL_CLIENT_ID
        else url_for("mal_oauth_callback", _external=True)
    )

    try:
        resp = requests.post(
            MAL_OAUTH_TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15
        )
        if resp.status_code != 200:
            app_log(f"MAL token exchange error {resp.status_code}: {resp.text}", "WARN")
            return redirect(f"/settings?mal_error=Token+exchange+failed+{resp.status_code}")

        token_data = resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 2592000)

        profile = fetch_mal_user_profile(access_token) or {}
        username = profile.get("name") or "MAL User"
        picture = profile.get("picture") or ""
        user_id = profile.get("id")

        auth_payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + float(expires_in),
            "user_id": user_id,
            "username": username,
            "picture": picture
        }
        save_mal_auth(auth_payload)

        if not settings.get("mal_scrobble_enabled"):
            settings["mal_scrobble_enabled"] = True
            save_settings(settings)

        return redirect("/settings?mal_connected=1")
    except Exception as e:
        app_log(f"MAL callback error: {e}", "ERROR")
        return redirect("/settings?mal_error=OAuth+exchange+exception")


@host_only
@require_action_token
def mal_disconnect():
    clear_mal_auth()
    return redirect("/settings?mal_disconnected=1")


@host_only
def mal_status():
    auth = load_mal_auth()
    settings = load_settings()
    is_authed = bool(auth.get("access_token"))
    return jsonify({
        "status": "success",
        "connected": is_authed,
        "username": auth.get("username", "") if is_authed else "",
        "picture": auth.get("picture", "") if is_authed else "",
        "scrobble_enabled": settings.get("mal_scrobble_enabled", False),
        "threshold": settings.get("mal_scrobble_threshold", 90),
        "client_id_configured": bool(get_effective_mal_client_id(settings))
    })


@host_only
@require_action_token
def mal_refresh_profile():
    if not is_mal_authenticated():
        return jsonify({"ok": False, "error": "Not authenticated with MyAnimeList"}), 401

    clear_mal_profile_cache()
    profile = get_mal_user_full_profile(force=True)
    return jsonify({
        "ok": True,
        "message": "Profile refreshed successfully",
        "user": profile.get("user")
    })


@host_only
@require_action_token
def mal_update_progress():
    if not is_mal_authenticated():
        return jsonify({"ok": False, "error": "Not authenticated with MyAnimeList"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict()

    mal_id = payload.get("mal_id")
    num_watched = payload.get("num_watched")
    score = payload.get("score")
    status = payload.get("status")

    res = update_mal_user_anime_status(mal_id, num_watched=num_watched, score=score, status=status)
    if res.get("ok"):
        return jsonify({"ok": True, "result": res.get("data")})
    return jsonify({"ok": False, "error": res.get("reason", "Update failed")}), 400


@host_only
@require_action_token
def play_episode(anime_name, episode):
    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return json_error(
            "anime_not_found",
            "Anime was not found.",
            404
        )

    episode_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not episode_path
        or not os.path.isfile(episode_path)
        or not episode_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return json_error(
            "episode_not_found",
            "Episode was not found.",
            404
        )

    vlc_diagnostics = diagnose_vlc_path(constants.VLC_PATH)
    if vlc_diagnostics["status"] == "not_configured":
        return jsonify({
            "ok": False,
            "error": "vlc_not_configured",
            "status": "vlc_not_configured",
            "message": "Set player path in Settings first."
        }), 400

    if not vlc_diagnostics["available"]:
        return jsonify({
            "ok": False,
            "error": "vlc_not_found",
            "status": "vlc_not_found",
            "message": vlc_diagnostics["message"]
        }), 400

    try:
        player_process = popen_hidden_subprocess([
            constants.VLC_PATH,
            episode_path
        ])

        episode_num = get_episode_number(os.path.basename(episode))
        update_watch_history(
            get_watch_history_key(anime_name, episode),
            episode,
            episode_num,
            media_name=anime_name,
            display_name=get_watch_history_display_name(anime_name, episode),
            episode_display_name=get_episode_display_override(
                anime_name,
                episode,
            ) or get_episode_default_display_name(os.path.basename(episode)),
        )
        rpc_owner_id = f"vlc-{secrets.token_urlsafe(16)}"
        start_external_discord_presence(anime_name, episode, rpc_owner_id)
        threading.Thread(
            target=clear_discord_rpc_when_process_exits,
            args=(player_process, rpc_owner_id),
            daemon=True,
            name="discord-rpc-player-monitor",
        ).start()

        return jsonify({
            "ok": True,
            "status": "playing"
        })

    except PermissionError:
        return jsonify({
            "ok": False,
            "error": "player_permission_denied",
            "status": "error",
            "message": "Player executable cannot be started due to permissions."
        }), 500

    except OSError:
        return jsonify({
            "ok": False,
            "error": "player_launch_failed",
            "status": "error",
            "message": "Player executable could not be started."
        }), 500


@host_only
@require_action_token
def save_screenshot():
    data, error_response = get_json_body()
    if error_response:
        return error_response

    img_data = data.get("image")

    if not img_data or not isinstance(img_data, str):
        return json_error("missing_image_data", "No image data was provided.", 400)

    png_prefix = "data:image/png;base64,"
    if not img_data.startswith(png_prefix):
        return json_error("invalid_image_data", "Invalid image data.", 400)

    if len(img_data.encode("utf-8")) > MAX_SCREENSHOT_DATA_URL_BYTES:
        return json_error("payload_too_large", "Image data is too large.", 413)

    encoded = img_data[len(png_prefix):]
    if not encoded:
        return json_error("invalid_image_data", "Invalid image data.", 400)

    try:
        binary_data = base64.b64decode(encoded, validate=True)
    except Exception:
        return json_error("invalid_image_data", "Invalid image data.", 400)

    try:
        settings = load_settings()
        filename_prefix = settings.get("screenshot_filename_prefix") or "vlcsnap"
        save_path = normalize_library_path(settings.get("screenshot_folder"))
        if not save_path:
            save_path = os.path.join(os.path.expanduser("~"), "Pictures")

        now = datetime.now()
        ms = str(now.microsecond // 1000).zfill(3)
        filename = f"{filename_prefix}-" + now.strftime("%Y-%m-%d-%Hh%Mm%Ss") + ms + ".png"

        if not os.path.exists(save_path):
            os.makedirs(save_path)

        full_path = os.path.join(save_path, filename)
        with open(full_path, "wb") as f:
            f.write(binary_data)

        return jsonify({"ok": True, "status": "success", "path": full_path})
    except Exception as e:
        app_log(f"Screenshot save failed: {e}", "ERROR")
        return json_error(
            "screenshot_save_failed",
            "Unable to save screenshot.",
            500
        )


@require_action_token
def update_progress():
    data, error_response = get_json_body()
    if error_response:
        return error_response

    anime_name = data.get("anime_name")
    episode = data.get("episode")
    time_str = data.get("time_str")

    if not anime_name or not episode:
        return json_error(
            "missing_progress_fields",
            "Missing anime_name or episode.",
            400
        )

    try:
        raw_episode_num = data.get("episode_num", 0)
        if raw_episode_num in (None, ""):
            episode_num = 0
        else:
            episode_num = normalize_episode_number(raw_episode_num)
            if episode_num is None:
                raise ValueError("invalid episode number")
        last_seconds = float(data.get("last_seconds", 0) or 0)
        duration = float(data.get("duration", 0) or 0)
    except (TypeError, ValueError):
        return json_error(
            "invalid_progress_data",
            "Invalid progress data.",
            400
        )

    update_watch_history(
        get_watch_history_key(anime_name, episode),
        episode,
        episode_num,
        time_str,
        last_seconds,
        duration,
        media_name=anime_name,
        display_name=get_watch_history_display_name(anime_name, episode),
        episode_display_name=data.get("episode_display_name"),
    )
    return jsonify({"ok": True, "status": "success"})


@require_action_token
def discord_presence_event():
    if not is_local_request() or not constants.DISCORD_RPC_ENABLED:
        return jsonify(ok=True, ignored=True)
    data, error = get_json_body()
    if error:
        return error
    try:
        accepted = accept_discord_presence_event(data)
    except (ValueError, TypeError, OverflowError):
        return json_error("invalid_presence_event", "Invalid playback state.", 400)
    return jsonify(ok=True, accepted=accepted)


@require_action_token
def api_mark_episode_watched():
    anime_name, episode, _, error_response = get_watch_status_payload()
    if error_response:
        return error_response

    if not anime_name or not episode:
        return json_error(
            "missing_watch_status_fields",
            "Missing anime_name or episode.",
            400
        )

    mark_episode_watched(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": True
    })


@require_action_token
def api_mark_episode_unwatched():
    anime_name, episode, _, error_response = get_watch_status_payload()
    if error_response:
        return error_response

    if not anime_name or not episode:
        return json_error(
            "missing_watch_status_fields",
            "Missing anime_name or episode.",
            400
        )

    mark_episode_unwatched(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": False
    })


@require_action_token
def api_update_watch_status_progress():
    anime_name, episode, data, error_response = get_watch_status_payload()
    if error_response:
        return error_response

    if not anime_name or not episode:
        return json_error(
            "missing_watch_status_fields",
            "Missing anime_name or episode.",
            400
        )

    current_status = get_episode_watch_status(anime_name, episode)

    try:
        progress = float(data.get("progress", 0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_watch_status",
            "Invalid watch progress value.",
            400
        )

    try:
        duration = float(data.get("duration", 0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_watch_status",
            "Invalid watch duration value.",
            400
        )

    try:
        current_seconds = float(data.get("current_seconds", 0))
    except (TypeError, ValueError):
        return json_error(
            "invalid_watch_status",
            "Invalid current playback time.",
            400
        )

    progress = max(0, min(100, progress))
    settings = load_settings()
    threshold = float(settings.get("mal_scrobble_threshold") or 90)
    was_watched = bool(current_status.get("watched", False))
    watched = True if progress >= threshold else was_watched

    status = update_episode_watch_status(
        anime_name,
        episode,
        {
            "watched": watched,
            "progress": progress,
            "duration": duration,
            "current_seconds": current_seconds
        }
    )

    scrobbled = False
    if watched and not was_watched:
        scrobbled = trigger_mal_scrobble_if_enabled(anime_name, episode)

    return jsonify({
        "ok": True,
        "watched": status.get("watched", False),
        "progress": status.get("progress", 0),
        "scrobbled": scrobbled,
        "anime_name": anime_name,
        "episode": episode
    })


@host_only
@require_action_token
def api_remove_watch_history_entry():
    payload = request.get_json(silent=True) or request.form or {}
    history_key = payload.get("history_key") if isinstance(payload, dict) else None

    if not history_key:
        return json_error(
            "missing_history_key",
            "Missing history_key.",
            400
        )

    removed = remove_watch_history_entry(str(history_key))
    return jsonify({"ok": removed, "removed": removed})


@host_only
@require_action_token
def api_delete_anime():
    """Endpoint to delete an anime folder or movie file and all associated caches."""
    payload = request.get_json(silent=True) or request.form or {}
    anime_name = payload.get("anime_name") if isinstance(payload, dict) else None
    is_movie = payload.get("is_movie", False)
    filename = payload.get("filename")

    if not anime_name:
        return json_error("missing_anime_name", "Missing anime_name.", 400)

    if is_movie:
        if not filename:
            return json_error("missing_filename", "Missing filename for movie.", 400)

        movies_dir = find_media_path("Movies")
        if not movies_dir:
            return json_error("not_found", "Movies directory not found.", 404)

        target_path = safe_join_media_path(movies_dir, filename)
        if not target_path or not os.path.isfile(target_path):
            return json_error("not_found", "Movie file not found on disk.", 404)

        try:
            os.remove(target_path)
        except Exception as e:
            app_log(f"Failed to delete movie file {target_path}: {e}", "ERROR")
            return json_error("delete_failed", f"Failed to delete movie: {e}", 500)

        try:
            movies_cache_dir = os.path.join(CACHE_DIR, "Movies")
            if os.path.isdir(movies_cache_dir):
                for ext in (".jpg", ".vtt"):
                    cache_file = safe_join_media_path(movies_cache_dir, f"{filename}{ext}")
                    if cache_file and os.path.isfile(cache_file):
                        os.remove(cache_file)

            video_rel_path = os.path.join("Movies", filename)
            thumb_hash = hashlib.md5(video_rel_path.encode("utf-8")).hexdigest() + ".jpg"
            thumb_file = os.path.join(THUMBNAIL_CACHE, thumb_hash)
            if os.path.isfile(thumb_file):
                os.remove(thumb_file)

            remove_watch_history_entry(get_watch_history_key("Movies", filename))

            status_data = load_watch_status()
            movies_status = status_data.get("Movies")
            if isinstance(movies_status, dict) and filename in movies_status:
                movies_status.pop(filename, None)
                if not movies_status:
                    status_data.pop("Movies", None)
                save_watch_status(status_data)
                db_remove_episode_watch_status("Movies", filename)

            clean_title = clean_movie_title(filename)
            if clean_title:
                cleanup_anime_cache(clean_title, include_watch_data=True)
        except Exception as e:
            app_log(f"Failed to clean cache for movie {filename}: {e}", "WARN")

        app_log(f"Deleted movie file and caches for: {filename}", "INFO")
        return jsonify({"status": "success", "ok": True, "deleted": True})

    else:
        target_path = find_anime_path(anime_name)
        if not target_path or not os.path.isdir(target_path):
            return json_error("not_found", "Anime folder not found on disk.", 404)

        try:
            import stat

            def handle_remove_readonly(func, path, exc_info):
                os.chmod(path, stat.S_IWRITE)
                func(path)

            if sys.version_info >= (3, 12):
                shutil.rmtree(target_path, onexc=handle_remove_readonly)
            else:
                shutil.rmtree(target_path, onerror=handle_remove_readonly)

            if os.path.lexists(target_path):
                raise OSError("Anime folder still exists after deletion.")
        except Exception as e:
            app_log(f"Failed to delete anime folder {target_path}: {e}", "ERROR")
            return json_error(
                "delete_failed",
                "Failed to delete the anime folder. Database and cache were preserved.",
                500
            )

        cleanup_anime_cache(anime_name, include_watch_data=True)
        remove_anilist_mapping(anime_name)
        remove_episode_display_overrides(anime_name)

        try:
            with db_connection() as conn:
                conn.execute("DELETE FROM anime_library WHERE name = ?", (anime_name,))
                conn.execute("DELETE FROM episode_watch_status WHERE anime_name = ?", (anime_name,))
                conn.execute("DELETE FROM watch_history WHERE history_key = ? OR media_name = ?", (anime_name, anime_name))
        except Exception as e:
            app_log(f"Failed to remove {anime_name} from database: {e}", "WARN")

        app_log(f"Deleted anime folder and caches for: {anime_name}", "INFO")
        return jsonify({"status": "success", "ok": True, "deleted": True})


@host_only
@require_action_token
def clear_rpc_route():
    """Endpoint for clearing Discord status manually."""
    owner_id = request.headers.get("X-AniBase-RPC-Session", "").strip() or None
    with RPC_STATE_LOCK:
        if constants.RPC_DESIRED and owner_id == constants.RPC_DESIRED["session"]:
            constants.RPC_DESIRED = None
            queued = dispatch_discord_rpc_task("reconcile")
        elif constants.RPC_DESIRED:
            queued = False
        else:
            queued = dispatch_discord_rpc_clear(owner_id)
    return jsonify({"status": "success", "queued": queued})


def register_api_routes(app):
    """Register all API, streaming, and RPC endpoints on the Flask application."""
    app.add_url_rule("/setup/sync", endpoint="setup_sync", view_func=setup_sync, methods=["GET", "POST"])
    app.add_url_rule("/settings/backup/export", endpoint="export_settings_backup", view_func=export_settings_backup)
    app.add_url_rule("/settings/backup/import", endpoint="import_settings_backup", view_func=import_settings_backup, methods=["POST"])
    app.add_url_rule("/settings/media-diagnostics/check", endpoint="refresh_media_diagnostics_settings", view_func=refresh_media_diagnostics_settings, methods=["POST"])
    app.add_url_rule("/settings", endpoint="update_settings", view_func=update_settings, methods=["POST"])
    app.add_url_rule("/settings/auto-import/scan", endpoint="scan_auto_import_settings", view_func=scan_auto_import_settings, methods=["POST"])
    app.add_url_rule("/settings/auto-import/resolve", endpoint="resolve_auto_import_settings", view_func=resolve_auto_import_settings, methods=["POST"])
    app.add_url_rule("/settings/auto-import/dismiss", endpoint="dismiss_auto_import_unmatched", view_func=dismiss_auto_import_unmatched, methods=["POST"])
    app.add_url_rule("/settings/auto-import/dismiss-all", endpoint="dismiss_all_auto_import_unmatched", view_func=dismiss_all_auto_import_unmatched, methods=["POST"])
    app.add_url_rule("/settings/cleanup-cache", endpoint="cleanup_cache_settings", view_func=cleanup_cache_settings, methods=["POST"])
    app.add_url_rule("/settings/pick-folder", endpoint="pick_settings_folder", view_func=pick_settings_folder)
    app.add_url_rule("/settings/pick-file", endpoint="pick_settings_file", view_func=pick_settings_file)
    app.add_url_rule("/api/schedule-alerts", endpoint="schedule_alerts", view_func=schedule_alerts)
    app.add_url_rule("/api/anilist/search", endpoint="api_anilist_search", view_func=api_anilist_search)
    app.add_url_rule("/api/anime/metadata-match", endpoint="api_update_anime_metadata_match", view_func=api_update_anime_metadata_match, methods=["POST"])
    app.add_url_rule("/api/episode/display-name", endpoint="api_update_episode_display_name", view_func=api_update_episode_display_name, methods=["POST"])
    app.add_url_rule("/api/library/refresh", endpoint="refresh_library", view_func=refresh_library, methods=["POST"])
    app.add_url_rule("/poster/<path:anime_name>", endpoint="poster", view_func=poster)
    app.add_url_rule("/banner/<anime_name>", endpoint="banner", view_func=banner)
    app.add_url_rule("/seek-preview/<anime_name>/<path:episode>", endpoint="seek_preview", view_func=seek_preview)
    app.add_url_rule("/thumbnail/<anime_name>/<path:episode>", endpoint="thumbnail", view_func=thumbnail)
    app.add_url_rule("/stream/<anime_name>/<path:episode>", endpoint="stream_video", view_func=stream_video)
    app.add_url_rule("/character_img/<anime_name>/<filename>", endpoint="character_img", view_func=character_img)
    app.add_url_rule("/image-proxy", endpoint="image_proxy", view_func=image_proxy)
    app.add_url_rule("/subtitle/<anime_name>/<path:episode>", endpoint="get_subtitle", view_func=get_subtitle)
    app.add_url_rule("/api/media/<anime_name>/<path:episode>/subtitles", endpoint="get_available_subtitles", view_func=get_media_subtitles)
    app.add_url_rule("/api/media/<anime_name>/<path:episode>/skip-times", endpoint="get_episode_skip_times", view_func=get_episode_skip_times)
    app.add_url_rule("/api/mal/login", endpoint="mal_oauth_login", view_func=mal_oauth_login, methods=["GET", "POST"])
    app.add_url_rule("/api/mal/callback", endpoint="mal_oauth_callback", view_func=mal_oauth_callback)
    app.add_url_rule("/api/mal/disconnect", endpoint="mal_disconnect", view_func=mal_disconnect, methods=["POST"])
    app.add_url_rule("/api/mal/status", endpoint="mal_status", view_func=mal_status)
    app.add_url_rule("/api/mal/refresh-profile", endpoint="mal_refresh_profile", view_func=mal_refresh_profile, methods=["POST"])
    app.add_url_rule("/api/mal/update-progress", endpoint="mal_update_progress", view_func=mal_update_progress, methods=["POST"])
    app.add_url_rule("/play/<anime_name>/<path:episode>", endpoint="play_episode", view_func=play_episode, methods=["POST"])
    app.add_url_rule("/screenshot", endpoint="save_screenshot", view_func=save_screenshot, methods=["POST"])
    app.add_url_rule("/update_progress", endpoint="update_progress", view_func=update_progress, methods=["POST"])
    app.add_url_rule("/api/discord/presence", endpoint="discord_presence_event", view_func=discord_presence_event, methods=["POST"])
    app.add_url_rule("/api/watch-status/mark-watched", endpoint="api_mark_episode_watched", view_func=api_mark_episode_watched, methods=["POST"])
    app.add_url_rule("/api/watch-status/mark-unwatched", endpoint="api_mark_episode_unwatched", view_func=api_mark_episode_unwatched, methods=["POST"])
    app.add_url_rule("/api/watch-status/progress", endpoint="api_update_watch_status_progress", view_func=api_update_watch_status_progress, methods=["POST"])
    app.add_url_rule("/api/watch-history/remove", endpoint="api_remove_watch_history_entry", view_func=api_remove_watch_history_entry, methods=["POST"])
    app.add_url_rule("/api/anime/delete", endpoint="api_delete_anime", view_func=api_delete_anime, methods=["POST"])
    app.add_url_rule("/clear_rpc", endpoint="clear_rpc_route", view_func=clear_rpc_route, methods=["POST"])
