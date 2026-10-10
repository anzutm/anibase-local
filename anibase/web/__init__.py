# anibase/web/__init__.py
"""Flask web application, route handlers, and UI infrastructure for AniBase."""

import os
from flask import Flask
from werkzeug.exceptions import RequestEntityTooLarge

from anibase.constants import (
    RESOURCE_DIR,
    MAX_REQUEST_BODY_BYTES,
)

from anibase.web.helpers import (
    set_app,
    json_error,
    handle_request_entity_too_large,
    get_json_body,
    validate_action_token,
    require_action_token,
    inject_action_token,
    internal_url_for,
    is_local_client_address,
    is_local_request,
    host_only,
    is_lan_client_address,
    reject_new_work_during_shutdown,
    block_lan_access_when_disabled,
    redirect_to_setup_when_needed,
    apply_settings,
    get_current_theme,
    inject_theme,
    inject_shortcuts,
    inject_mal_user,
    safe_cache_name,
    get_character_image_cache_path,
    dependency_card_state,
    dependency_card_text,
    build_settings_status_cards,
    is_localhost_request,
    pick_windows_path,
    get_anime_watch_summary,
    get_anime,
    get_movies,
    get_watch_status_payload,
)

from anibase.web.routes_pages import (
    setup_page,
    settings_page,
    about,
    schedule,
    studio_page,
    seiyuu_page,
    external_anime_detail,
    movies,
    movie_detail_page,
    index,
    anime_detail,
    player,
    mal_profile_page,
    season_list,
    season_detail,
    favicon,
    register_page_routes,
)

from anibase.web.routes_api import (
    setup_sync,
    export_settings_backup,
    import_settings_backup,
    refresh_media_diagnostics_settings,
    update_settings,
    scan_auto_import_settings,
    resolve_auto_import_settings,
    dismiss_auto_import_unmatched,
    dismiss_all_auto_import_unmatched,
    cleanup_cache_settings,
    pick_settings_folder,
    pick_settings_file,
    schedule_alerts,
    api_anilist_search,
    api_update_anime_metadata_match,
    api_update_episode_display_name,
    refresh_library,
    poster,
    banner,
    seek_preview,
    thumbnail,
    stream_video,
    character_img,
    image_proxy,
    get_subtitle,
    get_media_subtitles,
    get_episode_skip_times,
    mal_oauth_login,
    mal_oauth_callback,
    mal_disconnect,
    mal_status,
    mal_refresh_profile,
    mal_update_progress,
    play_episode,
    save_screenshot,
    update_progress,
    discord_presence_event,
    api_mark_episode_watched,
    api_mark_episode_unwatched,
    api_update_watch_status_progress,
    api_remove_watch_history_entry,
    api_delete_anime,
    clear_rpc_route,
    register_api_routes,
)


def setup_web_app(app):
    """Configure errorhandlers, hooks, context processors, and routes on the Flask app."""
    set_app(app)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BODY_BYTES
    app.errorhandler(RequestEntityTooLarge)(handle_request_entity_too_large)
    app.before_request(reject_new_work_during_shutdown)
    app.before_request(block_lan_access_when_disabled)
    app.before_request(redirect_to_setup_when_needed)
    app.context_processor(inject_action_token)
    app.context_processor(inject_theme)
    app.context_processor(inject_shortcuts)
    app.context_processor(inject_mal_user)
    register_page_routes(app)
    register_api_routes(app)
    return app


def create_app(resource_dir=None):
    """Factory to create and initialize the Flask application."""
    res_dir = resource_dir or RESOURCE_DIR
    app = Flask(
        "main",
        template_folder=os.path.join(res_dir, "templates"),
        static_folder=os.path.join(res_dir, "static"),
    )
    return setup_web_app(app)
