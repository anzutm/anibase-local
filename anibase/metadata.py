# anibase/metadata.py
"""Manajemen metadata anime, caching, sinkronisasi provider eksternal, jadwal tayang, studio, dan seiyuu."""

import os
import re
import json
import time
import html
import shutil
import requests
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import url_for
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    safe_cache_name,
    remove_file_quietly,
    atomic_write_json_file,
    normalize_episode_path_key,
    normalize_episode_display_name,
)
from anibase.settings import load_settings, save_settings


SEASON_TITLE_PATTERN = re.compile(
    r"\b(?:season\s*|s)(\d+)\b|\b(\d+)(?:st|nd|rd|th)\s+season\b", re.I
)


def normalize_provider_id(value):
    try:
        provider_id = int(value)
    except (TypeError, ValueError):
        return None
    return provider_id if provider_id > 0 else None


def normalize_anime_metadata_identity(metadata):
    """Normalize provider identity without treating a MAL id as an AniList id."""
    if not isinstance(metadata, dict):
        return metadata

    normalized = dict(metadata)
    anilist_id = normalize_provider_id(normalized.get("anilist_id"))
    mal_id = normalize_provider_id(normalized.get("mal_id"))

    if anilist_id is None:
        normalized.pop("anilist_id", None)
    else:
        normalized["anilist_id"] = anilist_id

    if mal_id is None:
        normalized.pop("mal_id", None)
    else:
        normalized["mal_id"] = mal_id

    provider = str(normalized.get("metadata_provider") or "").strip().lower()
    if not provider and anilist_id is not None:
        provider = _constants.ANILIST_METADATA_PROVIDER
    if provider:
        normalized["metadata_provider"] = provider
        if provider == _constants.TENRAI_METADATA_PROVIDER:
            normalized.pop("anilist_id", None)

    normalized["metadata_schema_version"] = _constants.ANIME_METADATA_SCHEMA_VERSION
    return normalized


def metadata_title_parts(title):
    title = str(title or "")
    match = SEASON_TITLE_PATTERN.search(title)
    number = int(next(value for value in match.groups() if value)) if match else 0
    base = SEASON_TITLE_PATTERN.sub(" ", title)
    return " ".join(re.findall(r"[^\W_]+", base.casefold())), number


def select_season_candidate(query, candidates):
    """Require title identity before considering season numbers; fail closed."""
    base, season = metadata_title_parts(query)
    if not base or not season:
        return None
    related = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        titles = [item.get(key) for key in ("title", "romaji_title", "native_title")]
        titles += item.get("synonyms") or []
        matches = []
        for title in titles:
            name, number = metadata_title_parts(title)
            if name and (name == base or name.startswith(base + " ")
                         or SequenceMatcher(None, name, base).ratio() >= .9):
                matches.append((name, number))
        if matches:
            related.append((item, matches))
    explicit = [item for item, matches in related
                if any(number == season for _, number in matches)]
    if len(explicit) == 1:
        return explicit[0]
    if explicit:
        return None
    originals = [item for item, matches in related
                 if any(name == base and number == 0 for name, number in matches)
                 and not any(number > 1 for _, number in matches)
                 and item.get("format") in ("TV", "TV_SHORT")]
    if season == 1:
        return originals[0] if len(originals) == 1 else None
    if season == 2 and len(originals) == 1:
        original = originals[0]
        linked = []
        for item, matches in related:
            if item is original or item.get("format") != "TV" or any(n for _, n in matches):
                continue
            if not item.get("year") or not original.get("year") or item["year"] <= original["year"]:
                continue
            edges = original.get("metadata_relations") or []
            if any(edge.get("relationType") in ("SEQUEL", "SIDE_STORY")
                   and (edge.get("node") or {}).get("id") == item.get("anilist_id") for edge in edges):
                linked.append(item)
        return linked[0] if len(linked) == 1 else None
    return None


def get_metadata_mapping(anime_name, settings=None):
    settings = settings if isinstance(settings, dict) else load_settings()
    mappings = settings.get("anilist_mappings", {})
    mapping = mappings.get(anime_name) if isinstance(mappings, dict) else None
    if not isinstance(mapping, dict):
        return None

    provider = str(mapping.get("provider") or _constants.ANILIST_METADATA_PROVIDER).strip().lower()
    if provider not in {_constants.ANILIST_METADATA_PROVIDER, _constants.TENRAI_METADATA_PROVIDER}:
        return None

    anilist_id = normalize_provider_id(mapping.get("anilist_id"))
    mal_id = normalize_provider_id(mapping.get("mal_id"))
    if provider == _constants.ANILIST_METADATA_PROVIDER and anilist_id is None:
        return None
    if provider == _constants.TENRAI_METADATA_PROVIDER and mal_id is None:
        return None

    normalized = dict(mapping)
    normalized["provider"] = provider
    if anilist_id is not None:
        normalized["anilist_id"] = anilist_id
    else:
        normalized.pop("anilist_id", None)
    if mal_id is None:
        normalized.pop("mal_id", None)
    else:
        normalized["mal_id"] = mal_id
    if provider == _constants.TENRAI_METADATA_PROVIDER:
        normalized.pop("anilist_id", None)
    return normalized


def get_anilist_mapping(anime_name, settings=None):
    mapping = get_metadata_mapping(anime_name, settings=settings)
    return mapping if mapping and mapping.get("provider") == _constants.ANILIST_METADATA_PROVIDER else None


def save_metadata_mapping(anime_name, metadata, manual=False):
    metadata = normalize_anime_metadata_identity(metadata)
    provider = str(metadata.get("metadata_provider") or _constants.ANILIST_METADATA_PROVIDER).strip().lower()
    if provider not in {_constants.ANILIST_METADATA_PROVIDER, _constants.TENRAI_METADATA_PROVIDER}:
        raise ValueError("Unsupported metadata provider")
    anilist_id = normalize_provider_id(metadata.get("anilist_id"))
    mal_id = normalize_provider_id(metadata.get("mal_id"))
    if provider == _constants.ANILIST_METADATA_PROVIDER and anilist_id is None:
        raise ValueError("AniList mapping requires a valid AniList id")
    if provider == _constants.TENRAI_METADATA_PROVIDER and mal_id is None:
        raise ValueError("Tenrai mapping requires a valid MAL id")

    settings = load_settings()
    mappings = settings.get("anilist_mappings", {})
    if not isinstance(mappings, dict):
        mappings = {}
    record = {
        "provider": provider,
        "title": metadata.get("title") or anime_name,
        "updated_at": datetime.now().isoformat(),
    }
    if manual:
        record["manual"] = True
    if anilist_id is not None:
        record["anilist_id"] = anilist_id
    if mal_id is not None:
        record["mal_id"] = mal_id
    if provider == _constants.TENRAI_METADATA_PROVIDER:
        record.pop("anilist_id", None)
    mappings[anime_name] = record
    settings["anilist_mappings"] = mappings
    save_settings(settings)
    return record


def save_anilist_mapping(anime_name, metadata):
    normalized = dict(metadata or {})
    normalized["metadata_provider"] = _constants.ANILIST_METADATA_PROVIDER
    return save_metadata_mapping(anime_name, normalized)


def remove_anilist_mapping(anime_name):
    settings = load_settings()
    mappings = settings.get("anilist_mappings", {})
    if not isinstance(mappings, dict) or anime_name not in mappings:
        return False

    mappings.pop(anime_name, None)
    settings["anilist_mappings"] = mappings
    save_settings(settings)
    return True


def invalidate_anilist_cache(anime_name):
    safe_name = safe_cache_name(anime_name)
    removed = False
    for path in (
        os.path.join(_constants.METADATA_CACHE, f"{anime_name}.json"),
        os.path.join(_constants.POSTER_CACHE, f"{safe_name}.jpg"),
        os.path.join(_constants.BANNER_CACHE, f"{safe_name}.jpg"),
    ):
        if os.path.isfile(path):
            remove_file_quietly(path)
            removed = True

    character_dir = os.path.join(_constants.CHARACTER_CACHE, safe_name)
    if os.path.isdir(character_dir):
        shutil.rmtree(character_dir)
        removed = True
    return removed


def is_next_airing_expired(next_airing, now_ts=None):
    if not isinstance(next_airing, dict):
        return False

    try:
        airing_at = int(next_airing.get("airingAt") or 0)
    except (TypeError, ValueError):
        return False

    if airing_at <= 0:
        return False

    if now_ts is None:
        now_ts = int(time.time())

    return now_ts >= airing_at + _constants.AIRING_NOW_GRACE_SECONDS


def remove_expired_next_airing(info, now_ts=None):
    if not isinstance(info, dict):
        return info, False

    if is_next_airing_expired(info.get("next_airing"), now_ts=now_ts):
        cleaned = dict(info)
        cleaned.pop("next_airing", None)
        return cleaned, True

    return info, False


def format_time_until_airing(seconds):
    if not seconds or seconds <= 0:
        return None
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"


def format_schedule_alert_countdown(seconds):
    total_minutes = max(0, int((seconds + 59) // 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours and minutes:
        return f"Starts in {hours}h {minutes}m"
    if hours:
        return f"Starts in {hours}h"
    return f"Starts in {minutes}m"


def get_season_metadata_name(anime_name, season_name):
    anime_name = str(anime_name or "").strip()
    season_name = str(season_name or "").strip()
    if not season_name:
        return anime_name
    if anime_name.lower() in season_name.lower():
        return season_name
    return f"{anime_name} {season_name}"


def get_season_anilist_info(anime_name, season_name):
    metadata_name = get_season_metadata_name(anime_name, season_name)
    info = get_cached_anilist_info(metadata_name)
    mapping = get_metadata_mapping(metadata_name)
    if mapping and mapping.get("manual") is True:
        if info:
            info["character_cache_name"] = metadata_name
        return info
    season_match = re.search(
        r"(?:season|s)\s*([0-9]+)|([0-9]+)(?:st|nd|rd|th)\s*season",
        season_name,
        flags=re.IGNORECASE,
    )
    requested_season = int(next((group for group in (season_match.groups() if season_match else ()) if group), 0) or 0)
    title = str((info or {}).get("title") or "")
    title_match = re.search(
        r"(?:season|s)\s*([0-9]+)|([0-9]+)(?:st|nd|rd|th)\s*season",
        title,
        flags=re.IGNORECASE,
    )
    title_season = int(next((group for group in (title_match.groups() if title_match else ()) if group), 0) or 0)
    if info and requested_season and (
        (requested_season == 1 and title_season > 1)
        or (requested_season > 1 and title_season not in (0, requested_season))
    ):
        invalidate_anilist_cache(metadata_name)
        remove_anilist_mapping(metadata_name)
        info = get_cached_anilist_info(metadata_name)
    if info:
        info["character_cache_name"] = metadata_name
    return info


def get_episode_display_override(anime_name, episode_path, settings=None):
    settings = settings if isinstance(settings, dict) else load_settings()
    all_names = settings.get("episode_display_names", {})
    anime_names = all_names.get(anime_name) if isinstance(all_names, dict) else None
    if not isinstance(anime_names, dict):
        return ""
    return normalize_episode_display_name(
        anime_names.get(normalize_episode_path_key(episode_path), "")
    )


def save_episode_display_override(anime_name, episode_path, display_name):
    settings = load_settings()
    all_names = settings.get("episode_display_names", {})
    if not isinstance(all_names, dict):
        all_names = {}
    anime_names = all_names.get(anime_name, {})
    if not isinstance(anime_names, dict):
        anime_names = {}

    episode_key = normalize_episode_path_key(episode_path)
    normalized_name = normalize_episode_display_name(display_name)
    if normalized_name:
        anime_names[episode_key] = normalized_name
    else:
        anime_names.pop(episode_key, None)

    if anime_names:
        all_names[anime_name] = anime_names
    else:
        all_names.pop(anime_name, None)
    settings["episode_display_names"] = all_names
    save_settings(settings)
    return normalized_name


def remove_episode_display_overrides(anime_name):
    settings = load_settings()
    all_names = settings.get("episode_display_names", {})
    if not isinstance(all_names, dict) or anime_name not in all_names:
        return False
    all_names.pop(anime_name, None)
    settings["episode_display_names"] = all_names
    save_settings(settings)
    return True


def normalize_studio_name(studio_name):
    normalized = re.sub(r"[^a-zA-Z0-9\s]", " ", studio_name or "")
    return " ".join(normalized.casefold().split())


def normalize_anime_match_name(value):
    normalized = re.sub(r"[^a-zA-Z0-9\s]", " ", (value or "").casefold())
    return " ".join(normalized.split())


def get_studio_project_cache_file(studio_name):
    safe_name = safe_cache_name(normalize_studio_name(studio_name) or studio_name or "unknown")
    os.makedirs(os.path.join(_constants.CACHE_DIR, "studios"), exist_ok=True)
    return os.path.join(_constants.CACHE_DIR, "studios", f"{safe_name}.json")


def read_studio_project_cache(studio_name, allow_stale=False):
    cache_file = get_studio_project_cache_file(studio_name)
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    cached_at = payload.get("cached_at")
    if not allow_stale:
        if not cached_at or (time.time() - cached_at) > _constants.STUDIO_PROJECT_CACHE_TTL_SECONDS:
            return None

    return payload.get("data")


def write_studio_project_cache(studio_name, data):
    cache_file = get_studio_project_cache_file(studio_name)
    try:
        atomic_write_json_file(
            cache_file,
            {
                "cached_at": time.time(),
                "data": data
            },
            f"Studio project cache for {studio_name}",
            ensure_ascii=False
        )
    except Exception as e:
        app_log(f"Studio project cache write failed for {studio_name}: {e}", "WARN")


def build_local_anime_match_index(local_anime):
    match_index = {}
    for anime in local_anime:
        anime_name = anime.get("name")
        if not anime_name:
            continue
        names = {anime_name}
        info = get_cached_metadata_only(anime_name) or {}
        cached_title = info.get("title")
        if cached_title:
            names.add(cached_title)
        for name in names:
            normalized = normalize_anime_match_name(name)
            if normalized:
                match_index.setdefault(normalized, anime)
    return match_index


def get_project_title_options(project):
    title = project.get("title") or {}
    return [
        title.get("english"),
        title.get("romaji"),
        title.get("native")
    ]


def get_studio_project_identity(project):
    project_id = project.get("id") or project.get("mal_id")
    if project_id not in (None, ""):
        return ("provider-id", str(project_id))

    normalized_titles = tuple(
        sorted({
            normalized
            for title in get_project_title_options(project)
            if (normalized := normalize_anime_match_name(title))
        })
    )
    return (
        "title",
        normalized_titles,
        project.get("seasonYear") or 0,
        str(project.get("format") or "").upper(),
    )


def build_studio_project_item(project, local_match=None):
    title_options = get_project_title_options(project)
    display_title = next((title for title in title_options if title), "Untitled")
    title = project.get("title") or {}

    item = {
        "id": project.get("id"),
        "name": display_title,
        "title_romaji": title.get("romaji"),
        "title_english": title.get("english"),
        "title_native": title.get("native"),
        "poster": (project.get("coverImage") or {}).get("extraLarge") or url_for("static", filename="arcana.jpg"),
        "format": project.get("format"),
        "status": project.get("status"),
        "season": project.get("season"),
        "year": project.get("seasonYear"),
        "episodes": project.get("episodes"),
        "score": project.get("averageScore"),
        "popularity": project.get("popularity"),
        "description": project.get("description"),
        "in_library": local_match is not None,
        "local_anime_name": local_match.get("name") if local_match else None,
        "local_detail_url": url_for("anime_detail", anime_name=local_match.get("name")) if local_match else None
    }

    if item["in_library"]:
        item["name"] = item["local_anime_name"]
        item["poster"] = url_for("poster", anime_name=item["local_anime_name"])
        item["score"] = local_match.get("score") or item["score"]
        item["year"] = local_match.get("year") or item["year"]
        item["season"] = local_match.get("season") or item["season"]
        item["episodes"] = local_match.get("episodes") or item["episodes"]
        item["status"] = local_match.get("status") or item["status"]

    return item


def build_local_studio_fallback_projects(studio_name, local_anime, allow_fetch_missing=True):
    requested_studio = normalize_studio_name(studio_name)
    library_projects = []

    for anime in local_anime:
        anime_name = anime.get("name")
        if not anime_name:
            continue

        info = get_cached_metadata_only(anime_name)
        if info is None and allow_fetch_missing:
            info = get_cached_anilist_info(anime_name)

        anime_studio = (info or {}).get("studio")
        if normalize_studio_name(anime_studio) != requested_studio:
            continue

        library_projects.append({
            "id": None,
            "name": anime_name,
            "title_romaji": None,
            "title_english": anime_name,
            "title_native": None,
            "poster": url_for("poster", anime_name=anime_name),
            "format": (info or {}).get("format"),
            "status": anime.get("status"),
            "season": anime.get("season"),
            "year": anime.get("year"),
            "episodes": anime.get("episodes"),
            "score": anime.get("score"),
            "popularity": None,
            "description": (info or {}).get("description"),
            "in_library": True,
            "local_anime_name": anime_name,
            "local_detail_url": url_for("anime_detail", anime_name=anime_name)
        })

    return library_projects


def get_seiyuu_cache_file(staff_id):
    return os.path.join(_constants.SEIYUU_CACHE, f"staff_{staff_id}.json")


def read_seiyuu_cache(staff_id, allow_stale=False):
    cache_file = get_seiyuu_cache_file(staff_id)
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    fetched_at = payload.get("fetched_at")
    if not allow_stale:
        if not fetched_at or (time.time() - fetched_at) > _constants.SEIYUU_CACHE_TTL_SECONDS:
            return None

    return payload.get("data")


def write_seiyuu_cache(staff_id, data):
    os.makedirs(_constants.SEIYUU_CACHE, exist_ok=True)
    cache_file = get_seiyuu_cache_file(staff_id)
    try:
        atomic_write_json_file(
            cache_file,
            {
                "fetched_at": time.time(),
                "data": data
            },
            f"Seiyuu cache for {staff_id}",
            ensure_ascii=False
        )
    except Exception as e:
        app_log(f"Seiyuu cache write failed for staff {staff_id}: {e}", "WARN")


def clean_person_description(text):
    if not text:
        return None
    text = html.unescape(str(text))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitize_external_url(value):
    url = str(value or "").strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def build_person_bio_content(text):
    cleaned = clean_person_description(text)
    if not cleaned:
        return {"paragraphs": [], "social_links": []}

    cleaned = cleaned.replace("~!", "").replace("!~", "")
    social_links = []
    social_keys = set()

    def collect_social(match):
        label = match.group(1).strip()
        url = sanitize_external_url(match.group(2))
        key = label.casefold()
        host = (urlparse(url).netloc.casefold() if url else "")
        social_name = None
        if "twitter" in key or host.endswith("twitter.com") or host.endswith("x.com"):
            social_name = "Twitter"
        elif "instagram" in key or host.endswith("instagram.com"):
            social_name = "Instagram"
        if social_name and url:
            social_key = (social_name, url)
            if social_key not in social_keys:
                social_links.append({"label": social_name, "url": url})
                social_keys.add(social_key)
            return ""
        return match.group(0)

    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", collect_social, cleaned)
    token_pattern = re.compile(
        r"\[([^\]]+)\]\(([^)]+)\)|__(.+?)__|\*\*(.+?)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)"
    )
    paragraphs = []
    for block in re.split(r"\n\s*\n|\n", cleaned):
        block = block.strip()
        if not block:
            continue
        segments = []
        cursor = 0
        for match in token_pattern.finditer(block):
            if match.start() > cursor:
                segments.append({"kind": "text", "text": block[cursor:match.start()]})
            if match.group(1) is not None:
                url = sanitize_external_url(match.group(2))
                if url:
                    segments.append({"kind": "link", "text": match.group(1), "url": url})
                else:
                    segments.append({"kind": "text", "text": match.group(1)})
            elif match.group(3) is not None or match.group(4) is not None:
                segments.append({"kind": "strong", "text": match.group(3) or match.group(4)})
            else:
                segments.append({"kind": "em", "text": match.group(5)})
            cursor = match.end()
        if cursor < len(block):
            segments.append({"kind": "text", "text": block[cursor:]})
        if any(segment.get("text", "").strip() for segment in segments):
            paragraphs.append(segments)

    return {"paragraphs": paragraphs, "social_links": social_links}


def get_nested_image(images, size="large_image_url"):
    if not isinstance(images, dict):
        return None

    for image_type in ("jpg", "webp"):
        image_data = images.get(image_type) or {}
        if image_data.get(size):
            return image_data.get(size)
    return None


def is_anilist_seiyuu_payload(payload):
    if not isinstance(payload, dict):
        return False
    source = str(payload.get("source") or "").strip().lower()
    return source in {"", _constants.ANILIST_METADATA_PROVIDER}


def get_cached_metadata_only(anime_name):
    cache_file = os.path.join(_constants.METADATA_CACHE, f"{anime_name}.json")
    if not os.path.exists(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    data, expired_next_airing = remove_expired_next_airing(data)
    if expired_next_airing:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except OSError as e:
            app_log(f"Unable to prune expired cached metadata for {anime_name}: {e}", "WARN")

    return data


def get_cached_anilist_info(anime_name):
    from anibase.integrations.anilist import (
        get_anilist_info,
        can_attempt_anilist,
        mark_anilist_available,
        mark_anilist_unavailable,
        start_anilist_recovery,
    )
    from anibase.integrations.tenrai import get_tenrai_anime_info

    cache_file = os.path.join(_constants.METADATA_CACHE, f"{anime_name}.json")
    info = None
    stale_fallback_info = None
    manual_mapping = get_metadata_mapping(anime_name)
    mapped_anilist_id = manual_mapping.get("anilist_id") if manual_mapping else None
    mapped_mal_id = manual_mapping.get("mal_id") if manual_mapping else None
    automatic_season = bool(metadata_title_parts(anime_name)[1]
                            and not (manual_mapping and manual_mapping.get("manual") is True))
    if automatic_season:
        mapped_anilist_id = None
        mapped_mal_id = None
    manual_tenrai_override = bool(
        manual_mapping
        and manual_mapping.get("provider") == _constants.TENRAI_METADATA_PROVIDER
        and manual_mapping.get("manual") is True
    )

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                info = json.load(f)
                info = normalize_anime_metadata_identity(info)
                info, expired_next_airing = remove_expired_next_airing(info)
                pruned_info = info
                if automatic_season and info.get("season_match_version") != 1:
                    info = None
                    pruned_info = None
                    expired_next_airing = False
                if mapped_anilist_id and info and info.get("anilist_id") != mapped_anilist_id:
                    info = None
                if manual_tenrai_override and info and info.get("metadata_provider") != _constants.TENRAI_METADATA_PROVIDER:
                    info = None
                if info and ("characters" not in info or "relations" not in info or "recommendations" not in info):
                    info = None
                elif info and "characters" in info and any(char.get("va_name") and "va_staff_id" not in char for char in info.get("characters", [])):
                    info = None
                elif info and (info.get("status") or "").upper() == "RELEASING" and not info.get("next_airing"):
                    info = None
                if (
                    expired_next_airing
                    and isinstance(pruned_info, dict)
                    and (
                        not mapped_anilist_id
                        or pruned_info.get("anilist_id") == mapped_anilist_id
                    )
                ):
                    stale_fallback_info = pruned_info
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(pruned_info, f, ensure_ascii=False, indent=4)
                    except OSError as e:
                        app_log(f"Unable to prune expired airing metadata for {anime_name}: {e}", "WARN")

                if (
                    info
                    and info.get("metadata_provider") == _constants.TENRAI_METADATA_PROVIDER
                    and can_attempt_anilist()
                ):
                    start_anilist_recovery(anime_name, mapped_anilist_id=mapped_anilist_id)
        except Exception:
            info = None

    if not info:
        if can_attempt_anilist():
            try:
                _constants.ANILIST_CALL_STATE.last_error = True
            except Exception:
                pass
            info = (
                get_anilist_info(anime_name, anilist_id=mapped_anilist_id)
                if mapped_anilist_id
                else get_anilist_info(anime_name, mal_id=mapped_mal_id) if mapped_mal_id
                else get_anilist_info(anime_name)
            )
            info = normalize_anime_metadata_identity(info)
            if info and info.get("metadata_provider") == _constants.ANILIST_METADATA_PROVIDER:
                mark_anilist_available()
                if manual_mapping and manual_mapping.get("provider") == _constants.TENRAI_METADATA_PROVIDER:
                    save_metadata_mapping(anime_name, info, manual=manual_mapping.get("manual") is True)
            elif getattr(_constants.ANILIST_CALL_STATE, "last_error", True):
                mark_anilist_unavailable()

        if not info and not stale_fallback_info:
            info = get_tenrai_anime_info(anime_name, mal_id=mapped_mal_id)
            info = normalize_anime_metadata_identity(info)
            if (
                info
                and info.get("metadata_provider") == _constants.TENRAI_METADATA_PROVIDER
                and not manual_mapping
            ):
                try:
                    save_metadata_mapping(anime_name, info)
                except (OSError, ValueError) as error:
                    app_log(f"Unable to persist Tenrai mapping for {anime_name}: {error}", "WARN")
        if not info and stale_fallback_info:
            info = stale_fallback_info
        if info:
            atomic_write_json_file(cache_file, info, "Anime metadata", ensure_ascii=False)

    if info:
        if "characters" in info:
            safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
            anime_char_dir = os.path.join(_constants.CHARACTER_CACHE, safe_anime)
            os.makedirs(anime_char_dir, exist_ok=True)
            for char in info["characters"]:
                if char.get("image_url"):
                    clean_name = re.sub(r'[<>:"/\\|?*]', '_', char['name'])
                    fname = f"{clean_name}_char.jpg"
                    fpath = os.path.join(anime_char_dir, fname)
                    if not os.path.exists(fpath):
                        try:
                            r = requests.get(char["image_url"], timeout=15)
                            if r.status_code == 200:
                                with open(fpath, "wb") as f_img:
                                    f_img.write(r.content)
                        except Exception:
                            pass
                    char["image_local"] = fname

                if char.get("va_image_url") and char.get("va_name"):
                    clean_va = re.sub(r'[<>:"/\\|?*]', '_', char['va_name'])
                    fname = f"{clean_va}_va.jpg"
                    fpath = os.path.join(anime_char_dir, fname)
                    if not os.path.exists(fpath):
                        try:
                            r = requests.get(char["va_image_url"], timeout=15)
                            if r.status_code == 200:
                                with open(fpath, "wb") as f_img:
                                    f_img.write(r.content)
                        except Exception:
                            pass
                    char["va_image_local"] = fname

        if "relations" in info:
            for rel in info["relations"]:
                rel_title = rel.get("title")
                rel_poster_url = rel.get("poster")
                if rel_title and rel_poster_url and rel_poster_url.startswith("http"):
                    safe_rel_title = re.sub(r'[<>:"/\\|?*]', '_', rel_title)
                    rel_poster_path = os.path.join(_constants.POSTER_CACHE, f"{safe_rel_title}.jpg")
                    if not os.path.exists(rel_poster_path):
                        try:
                            r = requests.get(rel_poster_url, timeout=15)
                            if r.status_code == 200:
                                with open(rel_poster_path, "wb") as f:
                                    f.write(r.content)
                        except Exception:
                            pass

        if "recommendations" in info:
            for rec in info["recommendations"]:
                rec_title = rec.get("title")
                rec_poster_url = rec.get("poster")
                if rec_title and rec_poster_url and rec_poster_url.startswith("http"):
                    safe_rec_title = re.sub(r'[<>:"/\\|?*]', '_', rec_title)
                    rec_poster_path = os.path.join(_constants.POSTER_CACHE, f"{safe_rec_title}.jpg")
                    if not os.path.exists(rec_poster_path):
                        try:
                            r = requests.get(rec_poster_url, timeout=15)
                            if r.status_code == 200:
                                with open(rec_poster_path, "wb") as f:
                                    f.write(r.content)
                        except Exception:
                            pass

    return info


def get_anilist_poster(anime_name):
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    poster_path = os.path.join(_constants.POSTER_CACHE, f"{safe_name}.jpg")
    banner_path = os.path.join(_constants.BANNER_CACHE, f"{safe_name}.jpg")

    if os.path.exists(poster_path):
        return poster_path

    info = get_cached_anilist_info(anime_name)
    if not info:
        return None

    try:
        poster_url = info.get("poster")
        banner_url = info.get("banner")

        if not poster_url:
            return None

        poster_image = requests.get(poster_url, timeout=15)
        with open(poster_path, "wb") as f:
            f.write(poster_image.content)

        if banner_url:
            banner_image = requests.get(banner_url, timeout=15)
            with open(banner_path, "wb") as f:
                f.write(banner_image.content)

        return poster_path
    except Exception as e:
        app_log(f"AniList poster error for {anime_name}: {e}", "WARN")
        return None


def fetch_external_anime_metadata(anilist_id=None, mal_id=None):
    from anibase.integrations.anilist import (
        EXTERNAL_ANIME_QUERY,
        can_attempt_anilist,
        mark_anilist_available,
        mark_anilist_unavailable,
    )
    if not can_attempt_anilist():
        return None

    try:
        if anilist_id:
            variables = {"id": int(anilist_id)}
            query_str = EXTERNAL_ANIME_QUERY
        elif mal_id:
            variables = {"idMal": int(mal_id)}
            query_str = EXTERNAL_ANIME_QUERY.replace("query ($id: Int)", "query ($idMal: Int)").replace("Media(id: $id,", "Media(idMal: $idMal,")
        else:
            return None

        response = requests.post(
            _constants.ANILIST_API_URL,
            json={
                "query": query_str,
                "variables": variables
            },
            timeout=15
        )

        if response.status_code >= 400:
            mark_anilist_unavailable()
            app_log(f"External anime AniList HTTP {response.status_code} for id {anilist_id}", "WARN")
            return None

        data = response.json()
        errors = data.get("errors")
        if errors:
            mark_anilist_unavailable()
            app_log(f"External anime AniList GraphQL error for id {anilist_id}: {errors}", "WARN")
            return None

        media = (data.get("data") or {}).get("Media")
        if not media or not isinstance(media, dict):
            return None

        title = media.get("title") or {}
        romaji_title = title.get("romaji")
        english_title = title.get("english")
        native_title = title.get("native")
        display_name = english_title or romaji_title or native_title or f"Anime #{anilist_id}"

        cover = media.get("coverImage") or {}
        poster_url = cover.get("extraLarge") or cover.get("large") or url_for("static", filename="arcana.jpg")
        banner_url = media.get("bannerImage")

        studios = (media.get("studios") or {}).get("nodes") or []
        main_studio = studios[0].get("name") if studios and studios[0] else None

        trailer_raw = media.get("trailer") or {}
        trailer = None
        if trailer_raw and trailer_raw.get("id"):
            site = (trailer_raw.get("site") or "").lower()
            tid = trailer_raw.get("id")
            if site == "youtube":
                trailer = {
                    "site": "youtube",
                    "id": tid,
                    "watch_url": f"https://www.youtube.com/watch?v={tid}",
                    "embed_url": f"https://www.youtube-nocookie.com/embed/{tid}",
                    "thumbnail": trailer_raw.get("thumbnail") or f"https://img.youtube.com/vi/{tid}/hqdefault.jpg"
                }

        next_airing_raw = media.get("nextAiringEpisode")
        next_airing = None
        if next_airing_raw and isinstance(next_airing_raw, dict):
            next_airing = {
                "episode": next_airing_raw.get("episode"),
                "airingAt": next_airing_raw.get("airingAt"),
                "timeUntilAiring": next_airing_raw.get("timeUntilAiring"),
                "formatted_countdown": format_time_until_airing(next_airing_raw.get("timeUntilAiring"))
            }

        characters = []
        for edge in (media.get("characters") or {}).get("edges") or []:
            c_node = edge.get("node") or {}
            c_name = c_node.get("name") or {}
            c_image = (c_node.get("image") or {}).get("large")
            role = edge.get("role")
            vas = edge.get("voiceActors") or []
            va = vas[0] if vas else {}
            va_name = (va.get("name") or {}).get("full")
            va_native = (va.get("name") or {}).get("native")
            va_image = (va.get("image") or {}).get("large")
            va_id = va.get("id")

            characters.append({
                "character_id": c_node.get("id"),
                "character_name": c_name.get("full") or "Unknown",
                "character_native": c_name.get("native"),
                "character_image": c_image,
                "role": role,
                "va_id": va_id,
                "va_name": va_name,
                "va_native": va_native,
                "va_image": va_image
            })

        relations = []
        for edge in (media.get("relations") or {}).get("edges") or []:
            r_node = edge.get("node") or {}
            r_title = r_node.get("title") or {}
            r_cover = r_node.get("coverImage") or {}
            relations.append({
                "id": r_node.get("id"),
                "relation_type": edge.get("relationType"),
                "format": r_node.get("format"),
                "status": r_node.get("status"),
                "title": r_title.get("english") or r_title.get("romaji") or r_title.get("native"),
                "title_romaji": r_title.get("romaji"),
                "poster": r_cover.get("extraLarge") or r_cover.get("large")
            })

        recommendations = []
        for node in (media.get("recommendations") or {}).get("nodes") or []:
            rec_media = node.get("mediaRecommendation") or {}
            if not rec_media or not rec_media.get("id"):
                continue
            rec_title = rec_media.get("title") or {}
            rec_cover = rec_media.get("coverImage") or {}
            recommendations.append({
                "id": rec_media.get("id"),
                "format": rec_media.get("format"),
                "status": rec_media.get("status"),
                "score": rec_media.get("averageScore"),
                "title": rec_title.get("english") or rec_title.get("romaji") or rec_title.get("native"),
                "title_romaji": rec_title.get("romaji"),
                "poster": rec_cover.get("extraLarge") or rec_cover.get("large")
            })

        tags = []
        for tag in media.get("tags") or []:
            if tag and not tag.get("isMediaSpoiler"):
                tags.append({
                    "name": tag.get("name"),
                    "rank": tag.get("rank")
                })
        tags = sorted(tags, key=lambda t: t.get("rank", 0), reverse=True)[:8]

        start_date = media.get("startDate") or {}
        end_date = media.get("endDate") or {}
        start_str = f"{start_date.get('year', '')}-{start_date.get('month', ''):02d}-{start_date.get('day', ''):02d}" if start_date.get('year') and start_date.get('month') and start_date.get('day') else str(start_date.get('year', '')) if start_date.get('year') else None
        end_str = f"{end_date.get('year', '')}-{end_date.get('month', ''):02d}-{end_date.get('day', ''):02d}" if end_date.get('year') and end_date.get('month') and end_date.get('day') else str(end_date.get('year', '')) if end_date.get('year') else None

        result = {
            "id": media.get("id"),
            "id_mal": media.get("idMal"),
            "name": display_name,
            "title_english": english_title,
            "title_romaji": romaji_title,
            "title_native": native_title,
            "synonyms": media.get("synonyms") or [],
            "description": media.get("description"),
            "format": media.get("format"),
            "status": media.get("status"),
            "season": media.get("season"),
            "year": media.get("seasonYear"),
            "episodes": media.get("episodes"),
            "duration": media.get("duration"),
            "start_date": start_str,
            "end_date": end_str,
            "score": media.get("averageScore"),
            "mean_score": media.get("meanScore"),
            "popularity": media.get("popularity"),
            "favourites": media.get("favourites"),
            "genres": media.get("genres") or [],
            "tags": [t["name"] for t in tags],
            "poster": poster_url,
            "banner": banner_url,
            "cover_color": cover.get("color") or "#3b82f6",
            "studio": main_studio,
            "trailer": trailer,
            "next_airing": next_airing,
            "characters": characters,
            "relations": relations,
            "recommendations": recommendations,
            "anilist_url": f"https://anilist.co/anime/{media.get('id')}",
            "mal_url": f"https://myanimelist.net/anime/{media.get('idMal')}" if media.get("idMal") else None,
            "provider": "AniList"
        }

        mark_anilist_available()
        return result

    except Exception as e:
        mark_anilist_unavailable()
        app_log(f"Error fetching external anime metadata for id {anilist_id}: {e}", "ERROR")
        return None


def get_cached_external_anime_info(anilist_id=None, mal_id=None, force=False):
    if anilist_id:
        cache_key = f"external_{int(anilist_id)}.json"
    elif mal_id:
        cache_key = f"external_mal_{int(mal_id)}.json"
    else:
        return None

    cache_file = os.path.join(_constants.METADATA_CACHE, cache_key)

    if not force and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                if isinstance(cached_data, dict):
                    return cached_data
        except Exception as e:
            app_log(f"Failed reading external anime cache {cache_key}: {e}", "WARN")

    data = fetch_external_anime_metadata(anilist_id=anilist_id, mal_id=mal_id)
    if data:
        try:
            atomic_write_json_file(cache_file, data, f"External anime metadata {cache_key}")
            if anilist_id and data.get("id_mal"):
                alt_cache = os.path.join(_constants.METADATA_CACHE, f"external_mal_{data['id_mal']}.json")
                atomic_write_json_file(alt_cache, data, f"External anime metadata mal_{data['id_mal']}")
            elif mal_id and data.get("id"):
                alt_cache = os.path.join(_constants.METADATA_CACHE, f"external_{data['id']}.json")
                atomic_write_json_file(alt_cache, data, f"External anime metadata anilist_{data['id']}")
        except Exception as e:
            app_log(f"Failed writing external anime cache {cache_key}: {e}", "WARN")
    elif os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return data


def get_airing_schedule():
    from anibase.integrations.anilist import (
        mark_anilist_available,
        mark_anilist_unavailable,
    )
    from anibase.integrations.tenrai import get_tenrai_airing_schedule

    local_tz = datetime.now().astimezone().tzinfo
    now = datetime.now(local_tz)
    start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=_constants.SCHEDULE_LOOKAHEAD_DAYS)
    start_of_day = int(start_dt.timestamp())
    end_of_day = int(end_dt.timestamp())

    query = """
    query ($start: Int, $end: Int, $page: Int) {
      Page(page: $page, perPage: 50) {
        pageInfo {
          total
          currentPage
          lastPage
          hasNextPage
          perPage
        }
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
          airingAt
          episode
          media {
            title {
              romaji
              english
            }
            coverImage {
              extraLarge
            }
            format
          }
        }
      }
    }
    """

    schedules = []
    page = 1

    try:
        while True:
            response = requests.post(
                _constants.ANILIST_API_URL,
                json={
                    "query": query,
                    "variables": {
                        "start": start_of_day,
                        "end": end_of_day,
                        "page": page
                    }
                },
                timeout=10
            )

            try:
                data = response.json()
            except ValueError as e:
                mark_anilist_unavailable()
                app_log(f"Schedule API JSON error: {e}", "ERROR")
                return get_tenrai_airing_schedule()

            if response.status_code >= 400:
                mark_anilist_unavailable()
                app_log(f"Schedule API HTTP {response.status_code}: {data}", "ERROR")
                return get_tenrai_airing_schedule()

            errors = data.get("errors")
            if errors:
                mark_anilist_unavailable()
                app_log(f"Schedule API GraphQL errors: {errors}", "ERROR")
                return get_tenrai_airing_schedule()

            page_data = data.get("data", {}).get("Page")
            if not page_data:
                mark_anilist_unavailable()
                app_log(f"Schedule API missing page data: {data}", "ERROR")
                return get_tenrai_airing_schedule()

            page_info = page_data.get("pageInfo") or {}
            page_schedules = page_data.get("airingSchedules") or []
            schedules.extend(page_schedules)

            if not page_info.get("hasNextPage"):
                break

            page += 1

        mark_anilist_available()
        return schedules, None

    except requests.RequestException as e:
        mark_anilist_unavailable()
        app_log(f"Schedule API request error: {e}", "ERROR")
        return get_tenrai_airing_schedule()


def get_cached_airing_schedule():
    now_monotonic = time.time()
    with _constants.SCHEDULE_CACHE_LOCK:
        if _constants.SCHEDULE_CACHE["expires_at"] > now_monotonic:
            return list(_constants.SCHEDULE_CACHE["airing_list"]), _constants.SCHEDULE_CACHE["error"]

    airing_list, schedule_error = get_airing_schedule()

    with _constants.SCHEDULE_CACHE_LOCK:
        _constants.SCHEDULE_CACHE["airing_list"] = airing_list
        _constants.SCHEDULE_CACHE["error"] = schedule_error
        _constants.SCHEDULE_CACHE["expires_at"] = time.time() + _constants.SCHEDULE_CACHE_TTL_SECONDS

    return list(airing_list), schedule_error


def find_schedule_library_match(title_candidates, existing_anime_names):
    import main
    best_name = None
    best_score = 0

    for candidate in title_candidates:
        if not candidate:
            continue

        if candidate in existing_anime_names:
            return candidate, 1

        for anime_name in existing_anime_names:
            score = main.get_title_similarity(candidate, anime_name)
            if score > best_score:
                best_score = score
                best_name = anime_name

    if best_name and best_score >= 0.72:
        return best_name, best_score

    return None, best_score


def build_schedule_items(airing_list, local_tz, now_ts):
    import main
    existing_anime_names = main.get_existing_anime_names()
    today_dt = datetime.fromtimestamp(now_ts, local_tz).date()
    processed = []

    for item in airing_list:
        media = item.get("media") or {}
        title_data = media.get("title") or {}
        title = title_data.get("english") or title_data.get("romaji")
        title_candidates = [
            title_data.get("english"),
            title_data.get("romaji")
        ]

        if not title or not item.get("airingAt"):
            continue

        airing_dt = datetime.fromtimestamp(item["airingAt"], local_tz)
        airing_time = airing_dt.strftime("%H:%M")
        airing_date = airing_dt.date()
        day_delta = (airing_date - today_dt).days

        if day_delta == 0:
            date_label = "Today"
        elif day_delta == 1:
            date_label = "Tomorrow"
        elif day_delta == 2:
            date_label = "Day After Tomorrow"
        else:
            date_label = airing_dt.strftime("%A")

        if item["airingAt"] <= now_ts < item["airingAt"] + 1800:
            airing_status = "Airing now"
        elif item["airingAt"] > now_ts:
            airing_status = "Upcoming"
        else:
            airing_status = "Aired"

        cover_image = media.get("coverImage") or {}
        local_anime_name, local_match_score = find_schedule_library_match(
            title_candidates,
            existing_anime_names
        )

        processed.append({
            "title": title,
            "poster": cover_image.get("extraLarge") or url_for("static", filename="arcana.jpg"),
            "episode": item.get("episode"),
            "time": airing_time,
            "date_key": airing_dt.strftime("%Y-%m-%d"),
            "date_label": date_label,
            "date_display": airing_dt.strftime("%A, %d %B %Y"),
            "airing_at": item["airingAt"],
            "airing_iso": airing_dt.isoformat(),
            "format": media.get("format"),
            "status": airing_status,
            "is_in_library": bool(local_anime_name),
            "local_anime_name": local_anime_name,
            "local_match_score": round(local_match_score, 3) if local_match_score else 0,
            "detail_url": url_for("anime_detail", anime_name=local_anime_name) if local_anime_name else None
        })

    return processed


def get_schedule_alert_payload(processed, now_ts, now_iso, timezone_offset_minutes):
    current_items = [
        item for item in processed
        if item["airing_at"] <= now_ts < item["airing_at"] + 1800
    ]
    upcoming_items = [
        item for item in processed
        if item["airing_at"] > now_ts
    ]
    alert_items = current_items + upcoming_items
    badge_count = len(current_items) + len([
        item for item in upcoming_items
        if item["airing_at"] <= now_ts + 7200
    ])

    payload_items = []
    for item in alert_items:
        if item in current_items:
            status_mode = "live"
            status_label = "LIVE NOW"
        else:
            status_mode = "upcoming"
            status_label = format_schedule_alert_countdown(item["airing_at"] - now_ts)

        payload_items.append({
            "title": item["title"],
            "poster": item["poster"],
            "episode": item["episode"],
            "time": item["time"],
            "airing_at": item["airing_at"],
            "airing_iso": item["airing_iso"],
            "format": item["format"],
            "detail_url": item["detail_url"],
            "status_mode": status_mode,
            "status_label": status_label
        })

    return {
        "items": payload_items,
        "badge_count": badge_count,
        "summary": f"{len(current_items)} airing now \u00b7 {len(upcoming_items)} upcoming",
        "now_iso": now_iso,
        "timezone_offset_minutes": timezone_offset_minutes
    }
