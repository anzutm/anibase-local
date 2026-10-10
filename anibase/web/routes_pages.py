# anibase/web/routes_pages.py
"""HTML view routes for AniBase."""

import os
import re
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote
from collections import Counter

from flask import render_template, redirect, url_for, request, abort, send_file

import anibase.constants as constants
from anibase.constants import (
    RESOURCE_DIR,
    TENRAI_METADATA_PROVIDER,
    ANILIST_METADATA_PROVIDER,
    SCHEDULE_LOOKAHEAD_DAYS,
    VIDEO_EXTENSIONS,
    MOVIE_HISTORY_PREFIX,
    DEFAULT_MAL_CLIENT_ID,
    THEME_PRESETS,
    SETTINGS_BACKUP_SCHEMA_VERSION,
)
from anibase.logging import app_log, debug_log
from anibase.utils import (
    safe_join_media_path,
    clean_movie_title,
    normalize_library_paths,
)
from anibase.settings import (
    load_settings,
    get_default_settings,
    save_settings,
    is_setup_complete,
    get_effective_mal_client_id,
    get_settings_library_paths,
)
from anibase.watch import (
    load_history_data,
    get_watch_history_key,
    get_watch_history_display_name,
    get_continue_progress_percent,
    get_episode_watch_status,
)
from anibase.media import (
    get_episode_sort_key,
    parse_episode_identity,
    get_episode_cache,
)
from anibase.metadata import (
    get_cached_airing_schedule,
    build_schedule_items,
    build_local_anime_match_index,
    get_studio_project_identity,
    get_project_title_options,
    normalize_anime_match_name,
    build_studio_project_item,
    build_local_studio_fallback_projects,
    build_person_bio_content,
    sanitize_external_url,
    get_cached_external_anime_info,
    get_anilist_mapping,
    get_metadata_mapping,
    get_cached_anilist_info,
    get_season_anilist_info,
    get_season_metadata_name,
    get_episode_display_override,
)
from anibase.integrations.anilist import (
    fetch_anilist_studio_projects,
    fetch_anilist_seiyuu_detail,
)
from anibase.ffmpeg import get_media_dependency_diagnostics
from anibase.integrations.tenrai import (
    get_tenrai_studio_projects,
    get_cached_tenrai_seiyuu_detail,
)
from anibase.integrations.mal import (
    load_mal_auth,
    get_mal_user_full_profile,
    get_mal_user_animelist,
    enrich_mal_list_with_local_library,
)
from anibase.scanner import (
    find_anime_path,
    find_media_path,
    get_anime_folder_index,
)
from anibase.watcher import (
    reconfigure_library_observer,
    start_auto_import_worker,
    build_auto_import_overview,
)
from anibase.web.helpers import (
    host_only,
    validate_action_token,
    json_error,
    apply_settings,
    build_settings_status_cards,
    get_anime,
    get_movies,
    internal_url_for,
    get_current_theme,
)


@host_only
def setup_page():
    settings = load_settings()
    if request.method == "GET" and is_setup_complete(settings):
        return redirect(url_for("index"))

    error = ""
    if request.method == "POST":
        if not validate_action_token():
            return json_error(
                "invalid_action_token",
                "Invalid or missing action token.",
                403
            )

        library_paths = normalize_library_paths(request.form.getlist("library_paths"))
        if not library_paths:
            error = "Add at least one anime library folder to continue."
        else:
            theme_preset = request.form.get("theme_preset", "dark-blue").strip()
            if theme_preset not in THEME_PRESETS:
                theme_preset = "dark-blue"

            existing_settings = load_settings()
            setup_settings = get_default_settings()
            setup_settings.update(existing_settings)
            setup_settings.update({
                "setup_completed": True,
                "library_paths": library_paths,
                "watchlist_path": library_paths[0] if library_paths else "",
                "ongoing_path": library_paths[1] if len(library_paths) > 1 else "",
                "lan_access_enabled": False,
                "theme_preset": theme_preset,
                "auto_import_enabled": False,
                "auto_import_downloads_path": "",
                "auto_import_destination_root": "",
                "auto_import_create_ongoing_folders": False,
                "action_token": existing_settings.get("action_token", "")
            })

            save_settings(setup_settings)
            apply_settings(setup_settings)
            reconfigure_library_observer()
            start_auto_import_worker()
            return render_template("setup_loading.html")

    return render_template(
        "setup.html",
        settings=settings,
        error=error
    )


@host_only
def settings_page():
    settings = load_settings()
    media_diagnostics = get_media_dependency_diagnostics(
        settings,
        force=request.args.get("diagnostics_refreshed") == "1"
    )
    anime_folders = get_anime_folder_index()
    auto_import_overview = build_auto_import_overview(settings, anime_folders)
    auto_import_destination_paths = get_settings_library_paths(settings)
    auto_import_mappings = settings.get("auto_import_mappings", {})
    auto_import_mapping_rows = []
    if isinstance(auto_import_mappings, dict):
        auto_import_mapping_rows = [
            {"source": str(source), "target": str(target)}
            for source, target in auto_import_mappings.items()
            if str(source).strip() and str(target).strip()
        ]

    return render_template(
        "settings.html",
        settings=settings,
        settings_status_cards=build_settings_status_cards(settings, media_diagnostics),
        media_diagnostics=media_diagnostics,
        anime_folders=anime_folders,
        auto_import_overview=auto_import_overview,
        auto_import_destination_paths=auto_import_destination_paths,
        auto_import_mapping_rows=auto_import_mapping_rows,
        saved=request.args.get("saved") == "1",
        auto_import_scanned=request.args.get("auto_import_scanned") == "1",
        auto_import_processed=request.args.get("processed", "0"),
        auto_import_moved=request.args.get("moved", "0"),
        auto_import_unmatched_count=request.args.get("unmatched", "0"),
        auto_import_errors=request.args.get("errors", "0"),
        auto_import_resolved=request.args.get("auto_import_resolved") == "1",
        cache_cleaned=request.args.get("cache_cleaned") == "1",
        cache_removed_files=request.args.get("removed_files", "0"),
        cache_removed_dirs=request.args.get("removed_dirs", "0"),
        cache_removed_watch_entries=request.args.get("removed_watch_entries", "0"),
        cache_skipped=request.args.get("skipped", "0"),
        sync_busy=request.args.get("sync_busy") == "1",
        diagnostics_refreshed=request.args.get("diagnostics_refreshed") == "1",
        mal_auth=load_mal_auth(),
        mal_connected=request.args.get("mal_connected") == "1",
        mal_disconnected=request.args.get("mal_disconnected") == "1",
        mal_error=request.args.get("mal_error"),
        has_mal_client_id=bool(get_effective_mal_client_id(settings)),
        default_mal_client_id=DEFAULT_MAL_CLIENT_ID
    )


def about():
    return render_template("project-overview.html")


def schedule():
    local_tz = datetime.now().astimezone().tzinfo
    now_dt = datetime.now(local_tz)
    now_ts = int(now_dt.timestamp())
    timezone_offset_minutes = int(now_dt.utcoffset().total_seconds() // 60)
    timezone_label = now_dt.tzname() or f"UTC{timezone_offset_minutes / 60:+g}"
    schedule_scope = request.args.get("scope", "all").strip().lower()
    if schedule_scope not in {"all", "library"}:
        schedule_scope = "all"

    airing_list, schedule_error = get_cached_airing_schedule()
    all_processed = build_schedule_items(airing_list, local_tz, now_ts)
    library_processed = [
        item for item in all_processed
        if item.get("is_in_library")
    ]
    processed = library_processed if schedule_scope == "library" else all_processed
    schedule_provider = (
        "Tenrai fallback"
        if any(item.get("provider") == TENRAI_METADATA_PROVIDER for item in airing_list)
        else "AniList"
    )

    current_items = [
        item for item in processed
        if item["airing_at"] <= now_ts < item["airing_at"] + 1800
    ]
    upcoming_items = [
        item for item in processed
        if item["airing_at"] > now_ts
    ]
    schedule_focus = None

    if current_items:
        schedule_focus = dict(current_items[0])
        schedule_focus["focus_mode"] = "live"
        schedule_focus["focus_badge"] = "LIVE NOW"
        schedule_focus["focus_status"] = "Airing now"
        schedule_focus["more_count"] = max(0, len(current_items) - 1)
    elif upcoming_items:
        schedule_focus = dict(upcoming_items[0])
        schedule_focus["focus_mode"] = "next"
        schedule_focus["focus_badge"] = "NEXT UP"
        schedule_focus["focus_status"] = "Upcoming"
        schedule_focus["more_count"] = 0

    schedule_start = now_dt.strftime("%A, %d %B %Y")
    schedule_end = (now_dt + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS - 1)).strftime("%A, %d %B %Y")

    return render_template(
        "schedule.html",
        schedule=all_processed,
        schedule_focus=schedule_focus,
        schedule_error=schedule_error,
        now_ts=now_ts,
        now_iso=now_dt.isoformat(),
        timezone_offset_minutes=timezone_offset_minutes,
        timezone_label=timezone_label,
        today=schedule_start,
        schedule_range=f"{schedule_start} - {schedule_end}",
        schedule_lookahead_days=SCHEDULE_LOOKAHEAD_DAYS,
        schedule_scope=schedule_scope,
        schedule_all_count=len(all_processed),
        schedule_library_count=len(library_processed),
        schedule_visible_count=len(processed),
        schedule_provider=schedule_provider
    )


def studio_page(studio_name):
    return_to = request.args.get("return_to", "").strip()
    parsed_return_to = urlparse(return_to)
    if (
        parsed_return_to.scheme
        or parsed_return_to.netloc
        or not parsed_return_to.path.startswith("/anime/")
    ):
        return_to = url_for("index")

    local_anime = get_anime()
    local_match_index = build_local_anime_match_index(local_anime)
    studio_payload, studio_error = fetch_anilist_studio_projects(studio_name)
    studio_info = None
    studio_projects = []
    fallback_used = bool(
        studio_payload and studio_payload.get("provider") == TENRAI_METADATA_PROVIDER
    )

    if not studio_payload:
        tenrai_payload, tenrai_error = get_tenrai_studio_projects(studio_name)
        if tenrai_payload:
            studio_payload = tenrai_payload
            studio_error = tenrai_error
            fallback_used = True
        elif tenrai_error:
            studio_error = f"{studio_error or 'AniList unavailable.'} {tenrai_error}"

    if studio_payload:
        studio_info = studio_payload.get("studio_info")
        seen_projects = set()

        for project in studio_payload.get("projects", []):
            project_identity = get_studio_project_identity(project)
            if project_identity in seen_projects:
                continue
            seen_projects.add(project_identity)

            local_match = None

            for title in get_project_title_options(project):
                normalized_title = normalize_anime_match_name(title)
                if normalized_title and normalized_title in local_match_index:
                    local_match = local_match_index[normalized_title]
                    break

            studio_projects.append(
                build_studio_project_item(
                    project,
                    local_match
                )
            )

    if not studio_payload:
        fallback_used = True
        studio_projects = build_local_studio_fallback_projects(
            studio_name,
            local_anime,
            allow_fetch_missing=True
        )

    library_projects = [
        project for project in studio_projects
        if project.get("in_library")
    ]

    # Compute additional profile statistics
    years = [p.get("year") for p in studio_projects if p.get("year") and p.get("year") != 0]
    earliest_year = min(years) if years else None
    newest_year = max(years) if years else None

    formats = [p.get("format") for p in studio_projects if p.get("format")]
    format_counts = Counter(formats)
    clean_doms = []
    for fmt, count in format_counts.most_common(2):
        fmt_upper = fmt.upper()
        if fmt_upper in ("TV", "OVA", "ONA"):
            clean_doms.append(fmt_upper)
        else:
            clean_doms.append(fmt_upper.title())
    dominant_formats_str = ", ".join(clean_doms) if clean_doms else "N/A"

    # Group projects by year
    by_year = {}
    for p in studio_projects:
        year = p.get("year")
        y_key = year if (year and year != 0) else 0
        by_year.setdefault(y_key, []).append(p)

    # Sort years descending, keeping TBA (0) at the end
    sorted_years = sorted([yk for yk in by_year.keys() if yk > 0], reverse=True)
    if 0 in by_year:
        sorted_years.append(0)

    projects_by_year = [(yk, by_year[yk]) for yk in sorted_years]

    return render_template(
        "studio.html",
        studio_name=studio_name,
        studio_info=studio_info,
        studio_projects=studio_projects,
        projects_by_year=projects_by_year,
        library_projects=library_projects,
        total_projects=len(studio_projects),
        total_in_library=len(library_projects),
        studio_error=studio_error,
        fallback_used=fallback_used,
        studio_provider=(studio_payload or {}).get("provider", ANILIST_METADATA_PROVIDER),
        studio_anime=library_projects,
        total_anime=len(library_projects),
        earliest_year=earliest_year,
        newest_year=newest_year,
        dominant_formats=dominant_formats_str,
        studio_return_url=return_to,
        studio_return_label="Back to Anime" if return_to != url_for("index") else "Back to Library"
    )


def seiyuu_page(staff_id):
    """Display seiyuu/voice actor profile and voice roles"""
    return_to = request.args.get("return_to", "").strip()
    parsed_return_to = urlparse(return_to)
    if (
        parsed_return_to.scheme
        or parsed_return_to.netloc
        or not parsed_return_to.path.startswith("/anime/")
    ):
        return_to = url_for("index")
    return_label = "Back to Anime" if return_to != url_for("index") else "Back to Library"

    local_anime = get_anime()
    local_match_index = build_local_anime_match_index(local_anime)
    fallback_name = request.args.get("name", "").strip() or None
    requested_provider = request.args.get("provider", ANILIST_METADATA_PROVIDER).strip().lower()
    if requested_provider == TENRAI_METADATA_PROVIDER:
        seiyuu_data, seiyuu_error = get_cached_tenrai_seiyuu_detail(
            staff_id,
            staff_name=fallback_name,
        )
    else:
        seiyuu_data, seiyuu_error = fetch_anilist_seiyuu_detail(
            staff_id,
            fallback_name=fallback_name,
        )

    if not seiyuu_data:
        return render_template(
            "seiyuu.html",
            seiyuu_data=None,
            seiyuu_error=seiyuu_error or "Seiyuu not found",
            voice_roles=[],
            library_roles=[],
            seiyuu_return_url=return_to,
            seiyuu_return_label=return_label
        ), 404

    # Process voice roles and check if in library
    all_roles = [dict(role) for role in (seiyuu_data.get("voice_roles") or []) if isinstance(role, dict)]
    library_roles = []

    for role in all_roles:
        media_title = role.get("media_title", "")
        for title in [media_title, role.get("media_title_romaji", "")]:
            if title:
                normalized_title = normalize_anime_match_name(title)
                if normalized_title and normalized_title in local_match_index:
                    local_match = local_match_index[normalized_title]
                    role["in_library"] = True
                    role["local_anime_name"] = local_match.get("name")
                    role["local_detail_url"] = url_for("anime_detail", anime_name=local_match.get("name"))
                    library_roles.append(role)
                    break

    other_roles = [role for role in all_roles if not role.get("in_library")]
    bio_content = build_person_bio_content(seiyuu_data.get("description"))
    profile_url = sanitize_external_url(seiyuu_data.get("site_url"))
    social_links = list(bio_content["social_links"])
    if profile_url:
        social_links.append({"label": "AniList Profile" if seiyuu_data.get("source") == "AniList" else "Profile", "url": profile_url})

    backdrop_image = next(
        (role.get("media_poster") for role in library_roles + other_roles if role.get("media_poster")),
        None
    )

    return render_template(
        "seiyuu.html",
        seiyuu_data=seiyuu_data,
        seiyuu_error=seiyuu_error,
        voice_roles=all_roles,
        library_roles=library_roles,
        other_roles=other_roles,
        bio_paragraphs=bio_content["paragraphs"],
        social_links=social_links,
        backdrop_image=backdrop_image,
        seiyuu_return_url=return_to,
        seiyuu_return_label=return_label
    )


def external_anime_detail(anilist_id=None, mal_id=None):
    """Informational detail page for anime outside user's local library."""
    return_to = request.args.get("return_to", "").strip()
    parsed_return_to = urlparse(return_to)
    if (
        parsed_return_to.scheme
        or parsed_return_to.netloc
        or not (
            parsed_return_to.path.startswith("/studio/")
            or parsed_return_to.path.startswith("/seiyuu/")
            or parsed_return_to.path.startswith("/anime/")
            or parsed_return_to.path == "/"
        )
    ):
        return_to = url_for("index")

    return_label = "Back to Library"
    if parsed_return_to.path.startswith("/studio/"):
        studio_part = unquote(parsed_return_to.path.split("/studio/")[-1])
        return_label = f"Back to {studio_part}" if studio_part else "Back to Studio"
    elif parsed_return_to.path.startswith("/seiyuu/"):
        return_label = "Back to Seiyuu"
    elif parsed_return_to.path.startswith("/anime/"):
        return_label = "Back to Anime"

    anime_info = get_cached_external_anime_info(anilist_id=anilist_id, mal_id=mal_id)
    if not anime_info:
        return render_template(
            "external_anime_error.html",
            anilist_id=anilist_id or f"MAL-{mal_id}",
            return_to=return_to,
            return_label=return_label,
            current_theme=load_settings().get("theme_preset", "dark-blue")
        ), 404

    # Bridge with local library: check if user already has this anime locally
    local_anime = get_anime()
    local_match_index = build_local_anime_match_index(local_anime)

    local_match = None
    title_candidates = [
        anime_info.get("title_english"),
        anime_info.get("title_romaji"),
        anime_info.get("name"),
    ] + (anime_info.get("synonyms") or [])

    for candidate in title_candidates:
        if not candidate:
            continue
        normalized = normalize_anime_match_name(candidate)
        if normalized and normalized in local_match_index:
            local_match = local_match_index[normalized]
            break

    # Enrich relations with local library info or external link
    enriched_relations = []
    for rel in anime_info.get("relations", []):
        r_item = dict(rel)
        rel_match = None
        for r_cand in [r_item.get("title"), r_item.get("title_romaji")]:
            if not r_cand:
                continue
            norm_rel = normalize_anime_match_name(r_cand)
            if norm_rel and norm_rel in local_match_index:
                rel_match = local_match_index[norm_rel]
                break

        r_item["in_library"] = rel_match is not None
        if rel_match:
            r_item["detail_url"] = url_for("anime_detail", anime_name=rel_match["name"])
            r_item["local_name"] = rel_match["name"]
        elif r_item.get("id"):
            r_item["detail_url"] = url_for("external_anime_detail", anilist_id=r_item["id"], return_to=request.full_path if hasattr(request, "full_path") else request.path)
            r_item["local_name"] = None
        else:
            r_item["detail_url"] = None
            r_item["local_name"] = None
        enriched_relations.append(r_item)

    # Enrich recommendations with local library info or external link
    enriched_recommendations = []
    for rec in anime_info.get("recommendations", []):
        rec_item = dict(rec)
        rec_match = None
        for rec_cand in [rec_item.get("title"), rec_item.get("title_romaji")]:
            if not rec_cand:
                continue
            norm_rec = normalize_anime_match_name(rec_cand)
            if norm_rec and norm_rec in local_match_index:
                rec_match = local_match_index[norm_rec]
                break

        rec_item["in_library"] = rec_match is not None
        if rec_match:
            rec_item["detail_url"] = url_for("anime_detail", anime_name=rec_match["name"])
            rec_item["local_name"] = rec_match["name"]
        elif rec_item.get("id"):
            rec_item["detail_url"] = url_for("external_anime_detail", anilist_id=rec_item["id"], return_to=request.full_path if hasattr(request, "full_path") else request.path)
            rec_item["local_name"] = None
        else:
            rec_item["detail_url"] = None
            rec_item["local_name"] = None
        enriched_recommendations.append(rec_item)

    return render_template(
        "external_anime.html",
        anime=anime_info,
        local_match=local_match,
        relations=enriched_relations,
        recommendations=enriched_recommendations,
        return_to=return_to,
        return_label=return_label,
        current_theme=load_settings().get("theme_preset", "dark-blue")
    )


def movies():
    movie_list = get_movies()
    featured_movies = random.sample(
        movie_list,
        k=min(5, len(movie_list)),
    ) if movie_list else []

    return render_template(
        "movies.html",
        movies=movie_list,
        featured_movies=featured_movies,
    )


def movie_detail_page(filename):
    video_path = safe_join_media_path(
        constants.MOVIE_PATH,
        filename
    )

    if (
        not video_path
        or not os.path.isfile(video_path)
        or not video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        return "Movie not found", 404

    clean_title = clean_movie_title(filename)
    anime_info = get_cached_anilist_info(clean_title)

    # Ambil durasi dan resolusi
    episode_info = get_episode_cache("Movies", video_path)

    # Buat list episode buatan (hanya 1 item)
    episodes = [{
        "file": filename,
        "episode": 1,
        "thumbnail": internal_url_for("thumbnail", anime_name="Movies", episode=filename),
        "duration": episode_info["duration"],
        "resolution": episode_info["resolution"]
    }]

    history = load_history_data()
    watch_history_key = get_watch_history_key("Movies", filename)
    h_data = history.get(watch_history_key)
    if not h_data:
        legacy_data = history.get("Movies")
        if legacy_data and legacy_data.get("episode") == filename:
            h_data = legacy_data

    resume_time = 0
    if h_data and h_data.get("episode") == filename:
        resume_time = h_data.get("last_seconds", 0)

    return render_template(
        "anime.html",
        anime_name=clean_title,      # Used for metadata/poster
        folder_name="Movies",       # Used for searching files on disk
        episodes=episodes,
        anime_info=anime_info,
        anilist_mapping=get_anilist_mapping(clean_title),
        metadata_mapping=get_metadata_mapping(clean_title),
        metadata_search_name=clean_title,
        is_movie=True,
        resume_time=resume_time
    )


def index():
    status_filter = request.args.get(
        "status",
        "ALL"
    )

    anime_list = get_anime()
    home_movies = get_movies()

    all_count = len(anime_list) + len(home_movies)
    library_is_empty = all_count == 0

    releasing_count = len([
        anime
        for anime in anime_list
        if anime.get("status") == "RELEASING"
    ])

    finished_count = len([
        anime
        for anime in anime_list
        if anime.get("status") == "FINISHED"
    ])

    featured_slides = []
    if anime_list:
        # Pick up to 5 random anime for the hero slider
        slider_candidates = random.sample(
            anime_list,
            min(len(anime_list), 5)
        )
        for item in slider_candidates:
            info = get_cached_anilist_info(item["name"])
            featured_slides.append({
                "anime": item,
                "info": info
            })

    # Helper function to get history could be used here
    history = load_history_data()

    continue_watching = []
    for history_key, data in history.items():
        if not isinstance(data, dict):
            continue

        episode = data.get("episode")
        if not episode:
            continue

        try:
            watched_seconds = float(data.get("last_seconds", 0) or 0)
        except (TypeError, ValueError):
            watched_seconds = 0
        if watched_seconds <= 0:
            continue

        is_movie = (
            history_key.startswith(MOVIE_HISTORY_PREFIX)
            or history_key == "Movies"
            or data.get("media_name") == "Movies"
        )

        media_name = "Movies" if is_movie else data.get("media_name", history_key)
        display_name = (
            data.get("display_name")
            or get_watch_history_display_name(media_name, episode)
        )

        continue_watching.append({
            "history_key": history_key,
            "name": media_name,
            "display_name": display_name,
            "is_movie": is_movie,
            "episode": episode,
            "episode_num": data.get("episode_num"),
            "episode_display_name": data.get("episode_display_name"),
            "time_str": data.get("time_str"),
            "updated_at": data.get("updated_at", ""),
            "image_url": url_for(
                "thumbnail",
                anime_name="Movies",
                episode=episode
            ) if is_movie else url_for(
                "banner",
                anime_name=media_name
            ),
            "progress_percent": get_continue_progress_percent(data)
        })

    continue_watching.sort(key=lambda x: x["updated_at"], reverse=True)
    continue_watching = continue_watching[:6]

    return render_template(
        "index.html",
        anime_list=anime_list,
        home_movies=home_movies,
        library_is_empty=library_is_empty,
        featured_slides=featured_slides,
        continue_watching=continue_watching,
        status_filter=status_filter,
        all_count=all_count,
        releasing_count=releasing_count,
        finished_count=finished_count
    )


def anime_detail(anime_name):
    anime_path = find_anime_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found", 404

    seasons = []

    for item in os.listdir(
        anime_path
    ):
        item_path = os.path.join(
            anime_path,
            item
        )

        if not os.path.isdir(item_path):
            continue

        try:
            has_video = any(
                filename.lower().endswith(VIDEO_EXTENSIONS)
                for filename in os.listdir(item_path)
            )
        except OSError:
            has_video = False

        if has_video:
            short_label = re.sub(r'^season[\s._-]*', '', item, flags=re.IGNORECASE).strip()
            seasons.append({
                "name": item,
                "label": short_label or item,
            })

    seasons.sort(key=lambda item: get_episode_sort_key(item["name"]))
    selected_season = None
    episode_source_path = anime_path

    if seasons:
        requested_season = request.args.get("season", "").strip()
        season_names = {item["name"] for item in seasons}
        selected_season = requested_season if requested_season in season_names else seasons[0]["name"]
        episode_source_path = safe_join_media_path(anime_path, selected_season)
        if not episode_source_path or not os.path.isdir(episode_source_path):
            abort(404)

    episodes = []
    video_files = []

    for file in os.listdir(
        episode_source_path
    ):
        if file.lower().endswith(
            VIDEO_EXTENSIONS
        ):
            video_files.append(
                file
            )

    video_files.sort(
        key=get_episode_sort_key
    )
    episode_name_settings = load_settings()

    for index, file in enumerate(
        video_files,
        start=1
    ):
        episode_identity = parse_episode_identity(file)
        episode_number = episode_identity["number"]
        episode_label = episode_identity["label"]

        video_path = os.path.join(
            episode_source_path,
            file
        )

        episode_info = get_episode_cache(
            anime_name,
            video_path,
            selected_season
        )

        relative_file = file
        if selected_season:
            relative_file = os.path.join(selected_season, file).replace(os.sep, "/")

        watch_status = get_episode_watch_status(
            anime_name,
            relative_file
        )
        custom_display_name = get_episode_display_override(
            anime_name,
            relative_file,
            settings=episode_name_settings,
        )

        episodes.append({
            "file": relative_file,
            "episode": episode_number,
            "episode_label": episode_label,
            "display_name": custom_display_name or episode_identity["display_name"],
            "default_display_name": episode_identity["display_name"],
            "display_name_custom": bool(custom_display_name),
            "episode_kind": episode_identity["kind"],
            "episode_end": episode_identity["end_number"],
            "list_position": index,
            "thumbnail": internal_url_for("thumbnail", anime_name=anime_name, episode=relative_file),
            "duration": episode_info["duration"],
            "resolution": episode_info["resolution"],
            "watched": watch_status.get("watched", False),
            "progress": watch_status.get("progress", 0),
            "resume_seconds": watch_status.get("current_seconds", 0)
        })

    resume_episode = None
    resume_label = "Start Watching"
    all_episodes_watched = bool(episodes) and all(
        episode.get("watched", False)
        for episode in episodes
    )

    in_progress_episodes = [
        episode
        for episode in episodes
        if episode.get("progress", 0) > 0 and not episode.get("watched", False)
    ]

    if in_progress_episodes:
        resume_episode = max(
            in_progress_episodes,
            key=lambda episode: episode.get("episode", 0)
        )
        resume_label = "Resume Watching"
    else:
        history = load_history_data()
        history_data = history.get(get_watch_history_key(anime_name, ""))
        history_episode_file = history_data.get("episode") if isinstance(history_data, dict) else None
        history_episode = next(
            (
                episode
                for episode in episodes
                if episode.get("file") == history_episode_file
            ),
            None
        )

        if history_episode and not all_episodes_watched:
            resume_episode = history_episode
            resume_label = "Resume Watching"

        unwatched_episode = next(
            (
                episode
                for episode in episodes
                if not episode.get("watched", False)
            ),
            None
        )

        if not resume_episode and unwatched_episode:
            resume_episode = unwatched_episode
            resume_label = "Start Watching" if unwatched_episode.get("episode") == 1 else "Continue Watching"
        elif not resume_episode and episodes:
            resume_episode = episodes[0]
            resume_label = "Rewatch"

    anime_info = (
        get_season_anilist_info(anime_name, selected_season)
        if selected_season
        else get_cached_anilist_info(anime_name)
    )
    metadata_name = (
        get_season_metadata_name(anime_name, selected_season)
        if selected_season
        else anime_name
    )

    debug_log(f"Anime info loaded for {anime_name}: {anime_info}")

    return render_template(
        "anime.html",
        anime_name=anime_name,
        folder_name=anime_name,
        episodes=episodes,
        resume_episode=resume_episode,
        resume_label=resume_label,
        anime_info=anime_info,
        anilist_mapping=get_anilist_mapping(metadata_name),
        metadata_mapping=get_metadata_mapping(metadata_name),
        metadata_search_name=metadata_name,
        seasons=seasons,
        selected_season=selected_season
    )


def player(anime_name, episode):
    anime_path = find_media_path(
        anime_name
    )

    if not anime_path:
        return "Anime not found", 404

    current_video_path = safe_join_media_path(
        anime_path,
        episode
    )

    if (
        not current_video_path
        or not os.path.isfile(current_video_path)
        or not current_video_path.lower().endswith(VIDEO_EXTENSIONS)
    ):
        abort(404)

    episode_dir = os.path.dirname(
        current_video_path
    )

    try:
        season_name = os.path.relpath(
            episode_dir,
            anime_path
        )
    except ValueError:
        abort(404)

    if season_name == ".":
        season_name = None

    current_file = os.path.basename(
        current_video_path
    )

    if anime_name == "Movies":
        video_files = [
            current_file
        ]
    else:
        video_files = []
        for file in os.listdir(
            episode_dir
        ):
            if file.lower().endswith(
                VIDEO_EXTENSIONS
            ):
                video_files.append(
                    file
                )

    video_files.sort(
        key=get_episode_sort_key
    )

    episodes = []
    episode_name_settings = load_settings()

    for index, file in enumerate(
        video_files,
        start=1
    ):
        video_path = os.path.join(
            episode_dir,
            file
        )

        relative_file = file
        if season_name:
            relative_file = os.path.join(
                season_name,
                file
            )

        relative_url = relative_file.replace(
            os.sep,
            "/"
        )

        episode_identity = parse_episode_identity(file)
        episode_number = episode_identity["number"]
        episode_label = episode_identity["label"]

        episode_info = get_episode_cache(
            anime_name,
            video_path,
            season_name
        )

        watch_status = get_episode_watch_status(
            anime_name,
            relative_url
        )
        custom_display_name = get_episode_display_override(
            anime_name,
            relative_url,
            settings=episode_name_settings,
        )

        episodes.append({
            "file": relative_url,
            "episode": episode_number,
            "episode_label": episode_label,
            "display_name": custom_display_name or episode_identity["display_name"],
            "default_display_name": episode_identity["display_name"],
            "display_name_custom": bool(custom_display_name),
            "episode_kind": episode_identity["kind"],
            "episode_end": episode_identity["end_number"],
            "list_position": index,
            "thumbnail": internal_url_for("thumbnail", anime_name=anime_name, episode=relative_url),
            "duration": episode_info["duration"],
            "resolution": episode_info["resolution"],
            "watched": watch_status.get("watched", False),
            "progress": watch_status.get("progress", 0),
            "resume_seconds": watch_status.get("current_seconds", 0)
        })

    current_index = 0
    for i, file in enumerate(
        video_files
    ):
        if file == current_file:
            current_index = i
            break

    previous_episode = None
    next_episode = None

    if current_index > 0:
        previous_episode = (
            video_files[
                current_index - 1
            ]
        )
        if season_name:
            previous_episode = os.path.join(
                season_name,
                previous_episode
            ).replace(
                os.sep,
                "/"
            )

    if current_index < len(video_files) - 1:
        next_episode = (
            video_files[
                current_index + 1
            ]
        )
        if season_name:
            next_episode = os.path.join(
                season_name,
                next_episode
            ).replace(
                os.sep,
                "/"
            )

    if anime_name == "Movies":
        back_url = url_for(
            "movie_detail_page",
            filename=episode
        )
    else:
        back_url = url_for(
            "anime_detail",
            anime_name=anime_name
        )

    if season_name and anime_name != "Movies":
        back_url = url_for(
            "anime_detail",
            anime_name=anime_name,
            season=season_name.replace(os.sep, "/")
        )

    watch_history_key = get_watch_history_key(
        anime_name,
        episode
    )
    watch_display_name = get_watch_history_display_name(
        anime_name,
        episode
    )

    # Get last watch time for resume feature
    history = load_history_data()
    resume_time = 0
    h_data = history.get(watch_history_key)
    if not h_data and anime_name == "Movies":
        legacy_data = history.get("Movies")
        if legacy_data and legacy_data.get("episode") == episode:
            h_data = legacy_data

    if h_data:
        if h_data.get("episode") == episode:
            resume_time = h_data.get("last_seconds", 0)

    current_episode_identity = parse_episode_identity(current_file)
    current_episode_number = current_episode_identity["number"]
    current_episode_label = current_episode_identity["label"]
    current_episode_display_name = get_episode_display_override(
        anime_name,
        episode,
        settings=episode_name_settings,
    ) or current_episode_identity["display_name"]

    return render_template(
        "player.html",
        anime_name=anime_name,
        episode=episode,
        season_name=season_name,
        back_url=back_url,
        current_episode=current_episode_number,
        current_episode_label=current_episode_label,
        current_episode_display_name=current_episode_display_name,
        current_position=current_index + 1,
        total_episodes=len(video_files),
        previous_episode=previous_episode,
        next_episode=next_episode,
        resume_time=resume_time,
        watch_history_key=watch_history_key,
        watch_display_name=watch_display_name,
        is_movie=anime_name == "Movies",
        vlc_available=bool(
            constants.VLC_PATH
            and os.path.isfile(constants.VLC_PATH)
        ),
        episodes=episodes
    )


def mal_profile_page():
    auth = load_mal_auth()
    authenticated = bool(auth.get("access_token"))
    settings = load_settings()
    theme = get_current_theme()

    if not authenticated:
        return render_template(
            "mal_profile.html",
            authenticated=False,
            client_id_configured=bool(get_effective_mal_client_id(settings)),
            current_theme=theme,
            user=None,
            animelist=[],
            status_filter="watching"
        )

    force_refresh = request.args.get("refresh") == "1"
    status_filter = request.args.get("status", "watching").strip().lower()
    valid_statuses = ["watching", "plan_to_watch", "completed", "on_hold", "dropped", "all"]
    if status_filter not in valid_statuses:
        status_filter = "watching"

    profile_data = get_mal_user_full_profile(force=force_refresh)
    user_info = profile_data.get("user") or {}
    raw_animelist = get_mal_user_animelist(status=status_filter, force=force_refresh)
    enriched_list = enrich_mal_list_with_local_library(raw_animelist)

    return render_template(
        "mal_profile.html",
        authenticated=True,
        client_id_configured=True,
        user=user_info,
        status_filter=status_filter,
        animelist=enriched_list,
        current_theme=theme,
        cached_at=profile_data.get("cached_at"),
        is_stale=profile_data.get("is_stale", False)
    )


def season_list(anime_name):
    """Redirect the removed season index to the integrated anime detail page."""
    return redirect(url_for("anime_detail", anime_name=anime_name))


def season_detail(anime_name, season_name):
    """Keep old season bookmarks working with the integrated season selector."""
    anime_path = find_anime_path(anime_name)
    if not anime_path:
        return "Anime not found", 404

    season_path = safe_join_media_path(anime_path, season_name)
    if not season_path or not os.path.isdir(season_path):
        return "Season not found", 404

    return redirect(
        url_for("anime_detail", anime_name=anime_name, season=season_name)
    )


def favicon():
    return send_file(
        os.path.join(RESOURCE_DIR, "static", "arcana.jpg"),
        mimetype="image/jpeg"
    )


def register_page_routes(app):
    """Register all HTML page routes on the Flask application."""
    app.add_url_rule("/setup", endpoint="setup_page", view_func=setup_page, methods=["GET", "POST"])
    app.add_url_rule("/settings", endpoint="settings_page", view_func=settings_page)
    app.add_url_rule("/about", endpoint="about", view_func=about)
    app.add_url_rule("/schedule", endpoint="schedule", view_func=schedule)
    app.add_url_rule("/studio/<path:studio_name>", endpoint="studio_page", view_func=studio_page)
    app.add_url_rule("/seiyuu/<int:staff_id>", endpoint="seiyuu_page", view_func=seiyuu_page)
    app.add_url_rule("/anime/external/<int:anilist_id>", endpoint="external_anime_detail", view_func=external_anime_detail)
    app.add_url_rule("/anime/external/mal/<int:mal_id>", endpoint="external_anime_detail", view_func=external_anime_detail)
    app.add_url_rule("/movies", endpoint="movies", view_func=movies)
    app.add_url_rule("/movie/<path:filename>", endpoint="movie_detail_page", view_func=movie_detail_page)
    app.add_url_rule("/", endpoint="index", view_func=index)
    app.add_url_rule("/anime/<anime_name>", endpoint="anime_detail", view_func=anime_detail)
    app.add_url_rule("/player/<anime_name>/<path:episode>", endpoint="player", view_func=player)
    app.add_url_rule("/profile", endpoint="mal_profile_page", view_func=mal_profile_page)
    app.add_url_rule("/mal/profile", endpoint="mal_profile_page_alias", view_func=mal_profile_page)
    app.add_url_rule("/anime/<anime_name>/seasons", endpoint="season_list", view_func=season_list)
    app.add_url_rule("/anime/<anime_name>/<season_name>", endpoint="season_detail", view_func=season_detail)
    app.add_url_rule("/favicon.ico", endpoint="favicon", view_func=favicon)
