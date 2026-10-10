# anibase/integrations/tenrai.py
"""Integrasi Tenrai API (REST client, format adapter, search, schedule, studio & seiyuu)."""

import os
import re
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import anibase.constants as _constants
from anibase.logging import app_log
from anibase.metadata import (
    normalize_provider_id,
    normalize_anime_metadata_identity,
    metadata_title_parts,
    select_season_candidate,
    normalize_studio_name,
    write_studio_project_cache,
    clean_person_description,
    get_nested_image,
    read_seiyuu_cache,
    write_seiyuu_cache,
    get_seiyuu_cache_file,
)
from anibase.integrations.jikan import build_seiyuu_role_from_jikan


def unwrap_tenrai_data(payload):
    """Return the data object from a Tenrai response envelope."""
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    return data if data is not None else payload


def tenrai_image_url(images):
    if not isinstance(images, dict):
        return None
    for image_type in ("jpg", "webp"):
        image = images.get(image_type)
        if not isinstance(image, dict):
            continue
        for key in ("large_image_url", "image_url", "small_image_url"):
            if image.get(key):
                return image[key]
    return None


def parse_tenrai_duration(duration):
    """Convert Tenrai's human-readable duration to minutes."""
    if isinstance(duration, (int, float)) and duration > 0:
        return int(duration)
    if not isinstance(duration, str):
        return None
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)", duration, re.I)
    minutes = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)", duration, re.I)
    total = (float(hours.group(1)) * 60 if hours else 0) + (float(minutes.group(1)) if minutes else 0)
    return int(total) if total > 0 else None


def normalize_tenrai_status(status):
    normalized = str(status or "").strip().lower()
    if "currently airing" in normalized or normalized in {"airing", "releasing"}:
        return "RELEASING"
    if "not yet" in normalized or normalized in {"upcoming", "planned"}:
        return "NOT_YET_RELEASED"
    if "finished" in normalized or "complete" in normalized:
        return "FINISHED"
    return str(status).upper().replace(" ", "_") if status else None


def normalize_tenrai_format(media_type):
    normalized = str(media_type or "").strip().upper().replace(" ", "_")
    return normalized or None


def tenrai_named_values(values):
    if not isinstance(values, list):
        return []
    return [item.get("name") for item in values if isinstance(item, dict) and item.get("name")]


def adapt_tenrai_characters(payload, limit=6):
    characters = unwrap_tenrai_data(payload)
    if not isinstance(characters, list):
        return []
    adapted = []
    for item in characters[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        character = item.get("character") or {}
        if not isinstance(character, dict) or not character.get("name"):
            continue
        japanese_voice = next(
            (voice for voice in (item.get("voice_actors") or [])
             if isinstance(voice, dict) and str(voice.get("language", "")).lower() == "japanese"),
            None,
        )
        person = (japanese_voice or {}).get("person") or {}
        adapted.append({
            "name": character.get("name"),
            "image_url": tenrai_image_url(character.get("images")),
            "role": item.get("role"),
            "va_name": person.get("name") or None,
            "va_image_url": tenrai_image_url(person.get("images")),
            "va_mal_id": normalize_provider_id(person.get("mal_id")),
            "va_staff_id": None,
        })
    return adapted


def adapt_tenrai_relations(payload, limit=40):
    media = unwrap_tenrai_data(payload)
    relations = media.get("relations") if isinstance(media, dict) else None
    if not isinstance(relations, list):
        return []
    adapted = []
    for item in relations:
        if len(adapted) >= max(0, limit):
            break
        if not isinstance(item, dict):
            continue
        raw_entries = item.get("entry") or []
        entries = raw_entries if isinstance(raw_entries, list) else [raw_entries]
        for entry in entries:
            if len(adapted) >= max(0, limit):
                break
            if not isinstance(entry, dict):
                continue
            entry_type = str(entry.get("type") or "").strip().lower()
            if entry_type and entry_type != "anime":
                continue
            title = entry.get("title") or entry.get("title_english") or entry.get("name")
            if not title:
                continue
            adapted.append({
                "title": title,
                "poster": tenrai_image_url(entry.get("images")),
                "type": str(entry.get("media_type") or entry.get("type") or "").upper() or None,
                "status": normalize_tenrai_status(entry.get("status")),
                "relation": item.get("relation"),
                "mal_id": normalize_provider_id(entry.get("mal_id")),
            })
    return adapted


def adapt_tenrai_recommendations(payload, limit=10):
    recommendations = unwrap_tenrai_data(payload)
    if not isinstance(recommendations, list):
        return []
    adapted = []
    for item in recommendations[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        entry = item.get("entry") or {}
        title = entry.get("title")
        if not title:
            continue
        adapted.append({
            "title": title,
            "poster": tenrai_image_url(entry.get("images")),
            "type": normalize_tenrai_format(entry.get("type")),
            "status": normalize_tenrai_status(entry.get("status")),
            "mal_id": normalize_provider_id(entry.get("mal_id")),
        })
    return adapted


def get_next_tenrai_broadcast_at(broadcast, now=None):
    """Calculate the next weekly broadcast timestamp from Tenrai metadata."""
    if not isinstance(broadcast, dict):
        return None
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    day_name = str(broadcast.get("day") or "").strip().lower().rstrip("s")
    time_text = str(broadcast.get("time") or "").strip()
    if day_name not in weekdays or not re.fullmatch(r"\d{1,2}:\d{2}", time_text):
        return None
    try:
        hour, minute = (int(part) for part in time_text.split(":", 1))
        if hour > 23 or minute > 59:
            return None
        source_tz = ZoneInfo(str(broadcast.get("timezone") or "UTC"))
    except (ValueError, ZoneInfoNotFoundError):
        return None

    now = now or datetime.now().astimezone()
    source_now = now.astimezone(source_tz)
    day_delta = (weekdays[day_name] - source_now.weekday()) % 7
    candidate = source_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate += timedelta(days=day_delta)
    if candidate <= source_now:
        candidate += timedelta(days=7)
    return int(candidate.timestamp())


def adapt_tenrai_anime_metadata(anime_payload, characters_payload=None, recommendations_payload=None):
    """Convert a Tenrai anime response into AniBase's metadata contract."""
    anime = unwrap_tenrai_data(anime_payload)
    if not isinstance(anime, dict):
        return None
    mal_id = normalize_provider_id(anime.get("mal_id"))
    title = anime.get("title_english") or anime.get("title")
    if not title or mal_id is None:
        return None
    studios = anime.get("studios") or []
    studio = studios[0].get("name") if studios and isinstance(studios[0], dict) else None
    score = anime.get("score")
    try:
        score = round(float(score) * 10, 1) if score is not None else None
    except (TypeError, ValueError):
        score = None
    broadcast = anime.get("broadcast") if isinstance(anime.get("broadcast"), dict) else None
    next_broadcast_at = get_next_tenrai_broadcast_at(broadcast)
    next_airing = None
    if normalize_tenrai_status(anime.get("status")) == "RELEASING" and next_broadcast_at:
        next_airing = {
            "airingAt": next_broadcast_at,
            "episode": None,
            "provider": _constants.TENRAI_METADATA_PROVIDER,
            "estimated": True,
        }
    return normalize_anime_metadata_identity({
        "mal_id": mal_id,
        "metadata_provider": _constants.TENRAI_METADATA_PROVIDER,
        "fetched_at": datetime.now().astimezone().isoformat(),
        "title": title,
        "description": anime.get("synopsis"),
        "episodes": anime.get("episodes"),
        "duration": parse_tenrai_duration(anime.get("duration")),
        "format": normalize_tenrai_format(anime.get("type")),
        "season": str(anime.get("season") or "").upper() or None,
        "year": anime.get("year"),
        "status": normalize_tenrai_status(anime.get("status")),
        "studio": studio,
        "genres": tenrai_named_values(anime.get("genres")),
        "score": score,
        "broadcast": broadcast,
        "next_airing": next_airing,
        "poster": tenrai_image_url(anime.get("images")),
        "banner": None,
        "characters": adapt_tenrai_characters(characters_payload),
        "relations": adapt_tenrai_relations(anime_payload),
        "recommendations": adapt_tenrai_recommendations(recommendations_payload),
    })


def tenrai_wait_for_rate_limit():
    """Reserve a public Tenrai request slot (4 requests per second)."""
    with _constants.PROVIDER_REQUEST_STATE_LOCK:
        now = time.monotonic()
        wait_seconds = max(0.0, _constants.TENRAI_NEXT_REQUEST_AT - now)
        _constants.TENRAI_NEXT_REQUEST_AT = max(now, _constants.TENRAI_NEXT_REQUEST_AT) + _constants.TENRAI_MIN_REQUEST_INTERVAL_SECONDS
    if wait_seconds:
        time.sleep(wait_seconds)


def fetch_tenrai_json(path, params=None):
    tenrai_wait_for_rate_limit()
    try:
        response = requests.get(
            f"{_constants.TENRAI_BASE_URL}/{str(path).lstrip('/')}",
            params=params or {},
            timeout=(5, 20),
        )
        try:
            payload = response.json()
        except ValueError:
            app_log(f"Tenrai returned invalid JSON for {path}", "WARN")
            return None
        if response.status_code == 429:
            app_log(f"Tenrai rate limit reached for {path}", "WARN")
            retry_after = response.headers.get("Retry-After")
            try:
                retry_delay = min(float(retry_after), 30.0)
            except (TypeError, ValueError):
                retry_delay = None
            if retry_delay:
                with _constants.PROVIDER_REQUEST_STATE_LOCK:
                    _constants.TENRAI_NEXT_REQUEST_AT = max(
                        _constants.TENRAI_NEXT_REQUEST_AT,
                        time.monotonic() + retry_delay,
                    )
            return None
        if response.status_code >= 400:
            app_log(f"Tenrai request failed with HTTP {response.status_code}: {path}", "WARN")
            return None
        return payload
    except requests.RequestException as error:
        app_log(f"Tenrai request error for {path}: {error}", "WARN")
        return None


def get_tenrai_anime_info(anime_name, mal_id=None):
    """Fetch and adapt one Tenrai/MAL anime record for the AniBase contract."""
    normalized_mal_id = normalize_provider_id(mal_id)
    if normalized_mal_id is not None:
        anime_payload = fetch_tenrai_json(f"/anime/{normalized_mal_id}/full")
    else:
        base, requested_season = metadata_title_parts(anime_name)
        search_payload = fetch_tenrai_json("/anime", {"q": base if requested_season else anime_name, "limit": 20})
        candidates = unwrap_tenrai_data(search_payload)
        if not isinstance(candidates, list) or not candidates:
            return None
        if requested_season:
            choices = [{**item, "title": item.get("title_english") or item.get("title"),
                        "romaji_title": item.get("title"), "synonyms": item.get("title_synonyms") or [],
                        "format": normalize_tenrai_format(item.get("type"))}
                       for item in candidates if isinstance(item, dict)]
            candidate = select_season_candidate(anime_name, choices)
        else:
            candidate = candidates[0]
        candidate = candidate if isinstance(candidate, dict) else None
        normalized_mal_id = normalize_provider_id((candidate or {}).get("mal_id"))
        if normalized_mal_id is None:
            return None
        anime_payload = fetch_tenrai_json(f"/anime/{normalized_mal_id}/full")

    anime = unwrap_tenrai_data(anime_payload)
    if not isinstance(anime, dict):
        return None
    characters_payload = fetch_tenrai_json(f"/anime/{normalized_mal_id}/characters")
    recommendations_payload = fetch_tenrai_json(f"/anime/{normalized_mal_id}/recommendations")
    info = adapt_tenrai_anime_metadata(anime_payload, characters_payload, recommendations_payload)
    if info and mal_id is None and metadata_title_parts(anime_name)[1]:
        info["season_match_version"] = 1
    return info


def search_tenrai_anime(query_text, limit=12):
    payload = fetch_tenrai_json("/anime", {"q": query_text, "limit": max(1, min(int(limit), 50))})
    items = unwrap_tenrai_data(payload)
    if not isinstance(items, list):
        return [], "Tenrai search is temporarily unavailable."
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mal_id = normalize_provider_id(item.get("mal_id"))
        title = item.get("title_english") or item.get("title")
        if mal_id is None or not title:
            continue
        results.append({
            "provider": _constants.TENRAI_METADATA_PROVIDER,
            "mal_id": mal_id,
            "title": title,
            "romaji_title": item.get("title"),
            "native_title": item.get("title_japanese"),
            "poster": tenrai_image_url(item.get("images")),
            "format": normalize_tenrai_format(item.get("type")),
            "status": normalize_tenrai_status(item.get("status")),
            "season": str(item.get("season") or "").upper() or None,
            "year": item.get("year"),
            "episodes": item.get("episodes"),
        })
    return results, None


def adapt_tenrai_schedule(payload, now=None, lookahead_days=None):
    """Convert Tenrai weekly broadcasts to AniBase schedule entries."""
    entries = unwrap_tenrai_data(payload)
    if not isinstance(entries, list):
        return []
    if now is None:
        now = datetime.now().astimezone()
    lookahead_days = lookahead_days or _constants.SCHEDULE_LOOKAHEAD_DAYS
    adapted = []
    for anime in entries:
        if not isinstance(anime, dict):
            continue
        broadcast = anime.get("broadcast") or {}
        next_broadcast_at = get_next_tenrai_broadcast_at(broadcast, now=now)
        if not next_broadcast_at:
            continue
        if next_broadcast_at > int((now + timedelta(days=lookahead_days)).timestamp()):
            continue
        title = anime.get("title_english") or anime.get("title")
        if not title:
            continue
        adapted.append({
            "airingAt": next_broadcast_at,
            "episode": None,
            "media": {
                "title": {"english": anime.get("title_english"), "romaji": title},
                "coverImage": {"extraLarge": tenrai_image_url(anime.get("images"))},
                "format": normalize_tenrai_format(anime.get("type")),
            },
            "provider": _constants.TENRAI_METADATA_PROVIDER,
            "mal_id": normalize_provider_id(anime.get("mal_id")),
        })
    return sorted(adapted, key=lambda item: item["airingAt"])


def get_tenrai_airing_schedule():
    payload = fetch_tenrai_json("/schedules", {"limit": 50})
    entries = adapt_tenrai_schedule(payload)
    if not entries:
        return [], "Tenrai schedule is temporarily unavailable."
    return entries, None


def adapt_tenrai_studio_projects(records, studio_name=None):
    """Adapt Tenrai/MAL anime records to the studio project contract."""
    projects = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        title = record.get("title") or record.get("name")
        if not title:
            continue
        score = record.get("score")
        try:
            score = float(score) * 10 if score is not None else None
        except (TypeError, ValueError):
            score = None
        images = record.get("images") or {}
        projects.append({
            "id": record.get("mal_id"),
            "mal_id": record.get("mal_id"),
            "title": {
                "english": record.get("title_english") or title,
                "romaji": title,
                "native": record.get("title_japanese")
            },
            "coverImage": {"extraLarge": tenrai_image_url(images)},
            "format": normalize_tenrai_format(record.get("type")),
            "status": normalize_tenrai_status(record.get("status")),
            "season": str(record.get("season") or "").upper() or None,
            "seasonYear": record.get("year"),
            "episodes": record.get("episodes"),
            "averageScore": score,
            "popularity": record.get("popularity"),
            "description": record.get("synopsis"),
            "metadata_provider": _constants.TENRAI_METADATA_PROVIDER,
        })
    return projects


def get_tenrai_studio_projects(studio_name, max_projects=_constants.STUDIO_PROJECT_LIMIT):
    """Find studio productions through Tenrai's producer and anime endpoints."""
    search = unwrap_tenrai_data(fetch_tenrai_json("/producers", {"q": studio_name, "limit": 5}))
    if not isinstance(search, list) or not search:
        return None, "Tenrai studio search returned no results."
    wanted = normalize_studio_name(studio_name)
    producer = next((item for item in search if normalize_studio_name(
        item.get("name") or (item.get("titles") or [{}])[0].get("title")
    ) == wanted), search[0])
    producer_id = producer.get("mal_id")
    if not producer_id:
        return None, "Tenrai studio search did not include a producer id."
    records = []
    page = 1
    while len(records) < max_projects:
        page_size = min(50, max_projects - len(records))
        page_payload = fetch_tenrai_json(
            "/anime", {"producers": producer_id, "limit": page_size, "page": page}
        )
        page_records = unwrap_tenrai_data(page_payload)
        if not isinstance(page_records, list) or not page_records:
            break
        records.extend(page_records)
        pagination = page_payload.get("pagination") if isinstance(page_payload, dict) else None
        if not isinstance(pagination, dict) or not pagination.get("has_next_page"):
            break
        page += 1
    projects = adapt_tenrai_studio_projects(records, studio_name)
    if not projects:
        return None, "Tenrai returned no studio productions."
    payload = {
        "studio_info": {"id": producer_id, "name": studio_name, "isAnimationStudio": True,
                         "mal_id": producer_id, "provider": _constants.TENRAI_METADATA_PROVIDER},
        "projects": projects[:max_projects],
        "provider": _constants.TENRAI_METADATA_PROVIDER,
    }
    write_studio_project_cache(studio_name, payload)
    return payload, None


def cache_tenrai_seiyuu_payload(payload):
    """Cache Tenrai seiyuu data under a provider-scoped key."""
    if not isinstance(payload, dict):
        return False
    mal_id = normalize_provider_id(payload.get("mal_id"))
    if mal_id is None:
        return False
    cache_key = f"tenrai_{mal_id}"
    write_seiyuu_cache(cache_key, payload)
    return True


def build_seiyuu_role_from_tenrai(voice):
    """Adapt a Tenrai voice credit (MAL-shaped) to the AniBase role contract."""
    role = build_seiyuu_role_from_jikan(voice)
    if not role:
        return None
    character = voice.get("character") or {}
    role["character_image"] = get_nested_image(character.get("images"), size="image_url")
    role["source"] = _constants.TENRAI_METADATA_PROVIDER
    return role


def normalize_tenrai_role_images(payload):
    """Repair cached Tenrai character image URLs from unavailable large variants."""
    if not isinstance(payload, dict):
        return payload, False
    changed = False
    for role in payload.get("voice_roles") or []:
        if not isinstance(role, dict):
            continue
        image = role.get("character_image")
        if isinstance(image, str) and "cdn.myanimelist.net/images/characters/" in image:
            repaired = re.sub(r"(characters/[^/]+/[^/?]+?)l(\.(?:jpg|jpeg|png|webp)(?:[?#].*)?)$", r"\1\2", image, flags=re.I)
            if repaired != image:
                role["character_image"] = repaired
                changed = True
    return payload, changed


def fetch_tenrai_seiyuu_detail(staff_name, anilist_staff_id=None, mal_id=None):
    """Fetch a voice actor profile from Tenrai when AniList is unavailable."""
    person = None
    normalized_mal_id = normalize_provider_id(mal_id)
    if normalized_mal_id is not None:
        person = unwrap_tenrai_data(fetch_tenrai_json(f"/people/{normalized_mal_id}/full"))
    elif staff_name:
        results = unwrap_tenrai_data(fetch_tenrai_json("/people", {"q": staff_name, "limit": 5}))
        if isinstance(results, list) and results:
            candidate = results[0] if isinstance(results[0], dict) else None
            normalized_mal_id = normalize_provider_id((candidate or {}).get("mal_id"))
            if normalized_mal_id is not None:
                person = unwrap_tenrai_data(fetch_tenrai_json(f"/people/{normalized_mal_id}/full"))
            else:
                person = candidate
    if not isinstance(person, dict):
        return None, "Seiyuu was not found on Tenrai."

    voice_roles = []
    seen_roles = set()
    for voice in person.get("voices") or []:
        role = build_seiyuu_role_from_tenrai(voice)
        if not role:
            continue
        key = (role.get("media_id"), role.get("character_id"), role.get("character_name"))
        if key in seen_roles:
            continue
        seen_roles.add(key)
        voice_roles.append(role)

    birthday = person.get("birthday")
    birth_date = birthday[:10] if isinstance(birthday, str) and len(birthday) >= 10 else None
    native_name = " ".join(part for part in [person.get("family_name"), person.get("given_name")] if part) or None
    payload = {
        "staff_id": anilist_staff_id,
        "mal_id": person.get("mal_id") or normalized_mal_id,
        "name_full": person.get("name") or staff_name or "Unknown Voice Actor",
        "name_native": native_name,
        "name_alternative": person.get("alternate_names") or None,
        "image": tenrai_image_url(person.get("images")),
        "description": clean_person_description(person.get("about")),
        "date_of_birth": birth_date,
        "age": None, "gender": None, "blood_type": None, "home_town": None,
        "language": "Japanese",
        "site_url": person.get("url"),
        "voice_roles": voice_roles,
        "source": _constants.TENRAI_METADATA_PROVIDER,
    }
    return payload, None


def get_cached_tenrai_seiyuu_detail(mal_id, staff_name=None):
    normalized_mal_id = normalize_provider_id(mal_id)
    if normalized_mal_id is None:
        return None, "Tenrai seiyuu profile needs a valid MAL person id."
    cache_key = f"tenrai_{normalized_mal_id}"
    cached = read_seiyuu_cache(cache_key)
    if cached and str(cached.get("source") or "").strip().lower() == _constants.TENRAI_METADATA_PROVIDER:
        cached, changed = normalize_tenrai_role_images(cached)
        if changed:
            write_seiyuu_cache(cache_key, cached)
        return cached, None
    payload, error = fetch_tenrai_seiyuu_detail(
        staff_name,
        anilist_staff_id=None,
        mal_id=normalized_mal_id,
    )
    if payload:
        payload["source"] = "Tenrai"
        write_seiyuu_cache(cache_key, payload)
    return payload, error
