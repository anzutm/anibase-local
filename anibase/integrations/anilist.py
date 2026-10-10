# anibase/integrations/anilist.py
"""Integrasi AniList GraphQL API: pencarian metadata, seiyuu, studio, dan pemulihan outage."""

import os
import time
import threading
import requests
import anibase.constants as _constants
from anibase.logging import app_log
from anibase.utils import (
    safe_cache_name,
    atomic_write_json_file,
)
from anibase.metadata import (
    normalize_provider_id,
    normalize_anime_metadata_identity,
    metadata_title_parts,
    select_season_candidate,
    get_metadata_mapping,
    save_metadata_mapping,
    read_studio_project_cache,
    write_studio_project_cache,
    read_seiyuu_cache,
    write_seiyuu_cache,
    is_anilist_seiyuu_payload,
)


EXTERNAL_ANIME_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    title {
      romaji
      english
      native
    }
    synonyms
    description(asHtml: false)
    format
    status
    season
    seasonYear
    episodes
    duration
    startDate { year month day }
    endDate { year month day }
    averageScore
    meanScore
    popularity
    favourites
    genres
    tags {
      name
      rank
      isMediaSpoiler
    }
    bannerImage
    coverImage {
      extraLarge
      large
      color
    }
    trailer {
      id
      site
      thumbnail
    }
    studios(isMain: true) {
      nodes {
        id
        name
      }
    }
    nextAiringEpisode {
      episode
      airingAt
      timeUntilAiring
    }
    characters(sort: [ROLE, FAVOURITES_DESC], perPage: 18) {
      edges {
        role
        node {
          id
          name { full native }
        }
        voiceActors(language: JAPANESE) {
          id
          name { full native }
          image { large }
        }
      }
    }
    relations {
      edges {
        relationType
        node {
          id
          type
          format
          status
          title { romaji english native }
          coverImage { extraLarge large }
        }
      }
    }
    recommendations(sort: [RATING_DESC], perPage: 12) {
      nodes {
        mediaRecommendation {
          id
          type
          format
          status
          averageScore
          title { romaji english native }
          coverImage { extraLarge large }
        }
      }
    }
  }
}
"""


def mark_anilist_unavailable(now=None):
    if now is None:
        now = time.monotonic()
    with _constants.PROVIDER_REQUEST_STATE_LOCK:
        _constants.ANILIST_UNAVAILABLE_UNTIL = max(
            _constants.ANILIST_UNAVAILABLE_UNTIL,
            now + _constants.ANILIST_FAILURE_COOLDOWN_SECONDS,
        )


def can_attempt_anilist(now=None):
    if now is None:
        now = time.monotonic()
    with _constants.PROVIDER_REQUEST_STATE_LOCK:
        return now >= _constants.ANILIST_UNAVAILABLE_UNTIL


def mark_anilist_available():
    """Clear the outage cooldown after a confirmed AniList response."""
    with _constants.PROVIDER_REQUEST_STATE_LOCK:
        was_unavailable = _constants.ANILIST_UNAVAILABLE_UNTIL > time.monotonic()
        _constants.ANILIST_UNAVAILABLE_UNTIL = 0.0
    if was_unavailable:
        app_log("AniList metadata provider recovered; using it as primary again", "INFO")


def refresh_anilist_metadata(anime_name, mapped_anilist_id=None):
    """Refresh one metadata cache in a worker without replacing a good fallback."""
    try:
        original_mapping = get_metadata_mapping(anime_name)
        mal_id = original_mapping.get("mal_id") if original_mapping else None
        _constants.ANILIST_CALL_STATE.last_error = True
        metadata = normalize_anime_metadata_identity(
            get_anilist_info(anime_name, anilist_id=mapped_anilist_id)
            if mapped_anilist_id
            else get_anilist_info(anime_name, mal_id=mal_id) if mal_id
            else get_anilist_info(anime_name)
        )
        if metadata and metadata.get("metadata_provider") == _constants.ANILIST_METADATA_PROVIDER:
            mark_anilist_available()
            mapping = get_metadata_mapping(anime_name)
            if mapping != original_mapping:
                return False
            if mal_id and not mapped_anilist_id and metadata.get("mal_id") != mal_id:
                return False
            if (
                mapped_anilist_id
                and mapping
                and mapping.get("provider") == _constants.ANILIST_METADATA_PROVIDER
                and mapping.get("anilist_id") != mapped_anilist_id
            ):
                return False
            cache_file = os.path.join(_constants.METADATA_CACHE, f"{anime_name}.json")
            atomic_write_json_file(cache_file, metadata, "AniList recovery metadata", ensure_ascii=False)
            if original_mapping and original_mapping.get("provider") == _constants.TENRAI_METADATA_PROVIDER:
                save_metadata_mapping(anime_name, metadata, manual=original_mapping.get("manual") is True)
            return True
        if getattr(_constants.ANILIST_CALL_STATE, "last_error", True):
            mark_anilist_unavailable()
        return False
    except Exception as error:
        mark_anilist_unavailable()
        app_log(f"AniList background recovery failed for {anime_name}: {error}", "WARN")
        return False


def start_anilist_recovery(anime_name, mapped_anilist_id=None):
    """Start at most one recovery worker for an anime and return immediately."""
    with _constants.ANILIST_RECOVERY_LOCK:
        if anime_name in _constants.ANILIST_RECOVERY_IN_FLIGHT:
            return False
        _constants.ANILIST_RECOVERY_IN_FLIGHT.add(anime_name)

    def worker():
        try:
            refresh_anilist_metadata(anime_name, mapped_anilist_id=mapped_anilist_id)
        finally:
            with _constants.ANILIST_RECOVERY_LOCK:
                _constants.ANILIST_RECOVERY_IN_FLIGHT.discard(anime_name)

    threading.Thread(
        target=worker,
        name=f"anilist-recovery-{safe_cache_name(anime_name)[:32]}",
        daemon=True,
    ).start()
    return True


def get_anilist_info(anime_name, anilist_id=None, mal_id=None):
    # The caller uses this flag to distinguish an API outage from a valid
    # response with no matching title.  A missing title must not put the
    # primary provider into the global outage cooldown.
    _constants.ANILIST_CALL_STATE.last_error = True
    automatic_season = not anilist_id and not mal_id and metadata_title_parts(anime_name)[1]
    if automatic_season:
        base, _ = metadata_title_parts(anime_name)
        candidates, error = search_anilist_anime(base, limit=20)
        _constants.ANILIST_CALL_STATE.last_error = bool(error)
        if error:
            return None
        candidate = select_season_candidate(anime_name, candidates)
        if not candidate:
            return None
        anilist_id = candidate["anilist_id"]

    query = """
    query ($search: String, $id: Int) {
      Media(
        search: $search,
        id: $id,
        type: ANIME
      ) {
        id
        idMal
        title {
          romaji
          english
        }
        description
        episodes
        duration
        format
        season
        seasonYear
        status
        genres
        averageScore
        nextAiringEpisode {
          airingAt
          episode
          timeUntilAiring
        }
        bannerImage
        coverImage {
          extraLarge
        }
        studios(
          isMain: true
        ) {
          nodes {
            name
          }
        }
        characters(sort: [ROLE, FAVOURITES_DESC], perPage: 6) {
          edges {
            role
            node {
              name { full }
              image { large }
            }
            voiceActors(language: JAPANESE) {
              id
              name { full }
              image { large }
            }
          }
        }
        relations {
          edges {
            relationType
            node {
              title {
                romaji
                english
              }
              coverImage {
                extraLarge
              }
              type
              status
            }
          }
        }
        recommendations(sort: [RATING_DESC, ID_DESC], perPage: 10) {
          nodes {
            mediaRecommendation {
              title {
                romaji
                english
              }
              coverImage {
                extraLarge
              }
              type
              status
            }
          }
        }
      }
    }
    """

    try:
        # Explicit null filters cause AniList to return 404 for valid titles.
        if anilist_id:
            query = query.replace("$search: String, $id: Int", "$id: Int").replace("search: $search,", "")
            variables = {"id": int(anilist_id)}
        elif mal_id:
            query = query.replace("$search: String, $id: Int", "$malId: Int").replace("search: $search,", "").replace("id: $id,", "idMal: $malId,")
            variables = {"malId": int(mal_id)}
        else:
            query = query.replace("$search: String, $id: Int", "$search: String").replace("id: $id,", "")
            variables = {"search": anime_name}

        response = requests.post(
            _constants.ANILIST_API_URL,
            json={
                "query": query,
                "variables": variables
            },
            timeout=20
        )

        try:
            data = response.json()
        except ValueError:
            mark_anilist_unavailable()
            app_log(f"AniList metadata response was not valid JSON for {anime_name}", "ERROR")
            return None

        errors = data.get("errors") if isinstance(data, dict) else None
        if response.status_code == 404 and errors and all(error.get("status") == 404 for error in errors):
            _constants.ANILIST_CALL_STATE.last_error = False
            mark_anilist_available()
            return None
        if response.status_code >= 400:
            mark_anilist_unavailable()
            app_log(f"AniList metadata request failed with HTTP {response.status_code}: {data}", "ERROR")
            return None

        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            mark_anilist_unavailable()
            app_log(f"AniList metadata GraphQL errors for {anime_name}: {errors}", "WARNING")
            return None

        data_content = data.get("data") if isinstance(data, dict) else None
        media = data_content.get("Media") if isinstance(data_content, dict) else None

        if not media:
            _constants.ANILIST_CALL_STATE.last_error = False
            mark_anilist_available()
            return None

        studio = None

        # Akses nama studio secara aman
        studios = media.get("studios", {})
        nodes = studios.get("nodes", [])
        if nodes and nodes[0]:
            studio = nodes[0].get("name")

        chars_list = []
        for edge in media.get("characters", {}).get("edges", []):
            char_node = edge.get("node")
            if not char_node: continue
            va = edge.get("voiceActors", [])
            va_node = va[0] if va else None
            
            chars_list.append({
                "name": char_node["name"]["full"],
                "image_url": char_node["image"]["large"],
                "role": edge.get("role"),
                "va_name": va_node["name"]["full"] if va_node else None,
                "va_image_url": va_node["image"]["large"] if va_node else None,
                "va_staff_id": va_node.get("id") if va_node else None
            })

        relations_list = []
        for edge in media.get("relations", {}).get("edges", []):
            rel_node = edge.get("node")
            if not rel_node or rel_node.get("type") != "ANIME":
                continue
            relations_list.append({
                "title": rel_node["title"]["english"] or rel_node["title"]["romaji"],
                "poster": rel_node["coverImage"]["extraLarge"],
                "type": edge.get("relationType").replace("_", " ").title(),
                "status": rel_node.get("status")
            })

        recommendations_list = []
        for node in media.get("recommendations", {}).get("nodes", []):
            rec_media = node.get("mediaRecommendation")
            if not rec_media or rec_media.get("type") != "ANIME":
                continue
            recommendations_list.append({
                "title": rec_media["title"]["english"] or rec_media["title"]["romaji"],
                "poster": rec_media["coverImage"]["extraLarge"],
                "type": rec_media.get("type").replace("_", " ").title(),
                "status": rec_media.get("status")
            })

        metadata = normalize_anime_metadata_identity({
            "anilist_id": media.get("id"),
            "mal_id": media.get("idMal"),
            "metadata_provider": _constants.ANILIST_METADATA_PROVIDER,
            "fetched_at": datetime.now().astimezone().isoformat(),
            "title": media["title"]["english"] or media["title"]["romaji"],
            "description": media["description"],
            "episodes": media["episodes"],
            "duration": media["duration"],
            "format": media["format"],
            "season": media["season"],
            "year": media["seasonYear"],
            "status": media["status"],
            "studio": studio,
            "genres": media["genres"],
            "score": media["averageScore"],
            "next_airing": media.get("nextAiringEpisode"),
            "poster": media["coverImage"]["extraLarge"],
            "banner": media["bannerImage"],
            "characters": chars_list,
            "relations": relations_list,
            "recommendations": recommendations_list
        })
        _constants.ANILIST_CALL_STATE.last_error = False
        mark_anilist_available()
        if automatic_season:
            metadata["season_match_version"] = 1
        return metadata

    except Exception as e:
        mark_anilist_unavailable()
        app_log(f"AniList metadata error for {anime_name}: {e}", "WARN")
        return None


def search_anilist_anime(query_text, limit=12):
    query = """
    query ($search: String, $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
          id
          idMal
          title { romaji english native }
          synonyms
          relations { edges { relationType node { id } } }
          coverImage { large }
          format
          status
          season
          seasonYear
          episodes
          isAdult
        }
      }
    }
    """

    try:
        response = requests.post(
            _constants.ANILIST_API_URL,
            json={
                "query": query,
                "variables": {
                    "search": query_text,
                    "perPage": max(1, min(int(limit), 20)),
                },
            },
            timeout=15,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            mark_anilist_unavailable()
            return [], f"AniList search failed with HTTP {response.status_code}."
        if payload.get("errors"):
            mark_anilist_unavailable()
            return [], "AniList could not complete that search."

        media_items = payload.get("data", {}).get("Page", {}).get("media", [])
        results = []
        for media in media_items:
            if not isinstance(media, dict) or media.get("isAdult"):
                continue
            titles = media.get("title") or {}
            title = titles.get("english") or titles.get("romaji") or titles.get("native")
            if not title or not media.get("id"):
                continue
            results.append({
                "anilist_id": media["id"],
                "mal_id": normalize_provider_id(media.get("idMal")),
                "provider": _constants.ANILIST_METADATA_PROVIDER,
                "title": title,
                "romaji_title": titles.get("romaji"),
                "native_title": titles.get("native"),
                "synonyms": media.get("synonyms") or [],
                "metadata_relations": (media.get("relations") or {}).get("edges") or [],
                "poster": (media.get("coverImage") or {}).get("large"),
                "format": media.get("format"),
                "status": media.get("status"),
                "season": media.get("season"),
                "year": media.get("seasonYear"),
                "episodes": media.get("episodes"),
            })
        mark_anilist_available()
        return results, None
    except requests.RequestException:
        mark_anilist_unavailable()
        return [], "Unable to reach AniList. Check your internet connection."
    except Exception as error:
        mark_anilist_unavailable()
        app_log(f"AniList manual search failed: {error}", "WARN")
        return [], "AniList search is temporarily unavailable."


def fetch_anilist_studio_projects(studio_name, max_projects=_constants.STUDIO_PROJECT_LIMIT):
    cached = read_studio_project_cache(studio_name)
    if cached and cached.get("provider") != _constants.TENRAI_METADATA_PROVIDER:
        return cached, None
    if cached and not can_attempt_anilist():
        return cached, None

    query = """
    query ($search: String, $page: Int, $perPage: Int) {
      Studio(search: $search) {
        id
        name
        isAnimationStudio
        media(isMain: true, sort: POPULARITY_DESC, page: $page, perPage: $perPage) {
          pageInfo {
            total
            currentPage
            lastPage
            hasNextPage
            perPage
          }
          nodes {
            id
            title {
              romaji
              english
              native
            }
            coverImage {
              extraLarge
            }
            format
            status
            season
            seasonYear
            episodes
            averageScore
            popularity
            description
          }
        }
      }
    }
    """

    projects = []
    studio_info = None
    page = 1
    per_page = min(50, max_projects)

    try:
        while len(projects) < max_projects:
            response = requests.post(
                _constants.ANILIST_API_URL,
                json={
                    "query": query,
                    "variables": {
                        "search": studio_name,
                        "page": page,
                        "perPage": min(per_page, max_projects - len(projects))
                    }
                },
                timeout=15
            )

            try:
                data = response.json()
            except ValueError:
                mark_anilist_unavailable()
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "AniList returned an invalid studio response."

            if response.status_code >= 400:
                mark_anilist_unavailable()
                app_log(f"Studio AniList HTTP {response.status_code}: {data}", "ERROR")
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, f"AniList studio request failed with HTTP {response.status_code}."

            errors = data.get("errors")
            if errors:
                mark_anilist_unavailable()
                app_log(f"Studio AniList GraphQL errors: {errors}", "ERROR")
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "AniList returned GraphQL errors for the studio request."

            studio_data = data.get("data", {}).get("Studio")
            if not studio_data:
                mark_anilist_available()
                stale = read_studio_project_cache(studio_name, allow_stale=True)
                return stale, "Studio was not found on AniList."

            if studio_info is None:
                studio_info = {
                    "id": studio_data.get("id"),
                    "name": studio_data.get("name"),
                    "isAnimationStudio": studio_data.get("isAnimationStudio")
                }

            media_data = studio_data.get("media") or {}
            page_info = media_data.get("pageInfo") or {}
            projects.extend(media_data.get("nodes") or [])

            if not page_info.get("hasNextPage") or page >= page_info.get("lastPage", page):
                break

            page += 1

        payload = {
            "studio_info": studio_info,
            "projects": projects[:max_projects],
            "provider": _constants.ANILIST_METADATA_PROVIDER,
        }
        mark_anilist_available()
        write_studio_project_cache(studio_name, payload)
        return payload, None

    except requests.RequestException as e:
        mark_anilist_unavailable()
        app_log(f"Studio AniList request error: {e}", "ERROR")
        stale = read_studio_project_cache(studio_name, allow_stale=True)
        return stale, "Unable to reach AniList studio API."


def build_seiyuu_role_from_anilist(edge):
    media_node = edge.get("node") or {}
    if media_node.get("type") != "ANIME":
        return None

    characters = [character for character in (edge.get("characters") or []) if character]
    character_node = characters[0] if characters else {}
    character_name = (
        (character_node.get("name") or {}).get("full")
        or edge.get("characterName")
        or "Unknown"
    )
    media_title = media_node.get("title") or {}
    role_notes = edge.get("roleNotes") or edge.get("characterRole")

    return {
        "character_id": character_node.get("id"),
        "character_name": character_name,
        "character_image": (character_node.get("image") or {}).get("large"),
        "media_id": media_node.get("id"),
        "media_title": media_title.get("english") or media_title.get("romaji") or "Unknown",
        "media_title_romaji": media_title.get("romaji"),
        "media_poster": (media_node.get("coverImage") or {}).get("extraLarge"),
        "media_type": media_node.get("type"),
        "media_status": media_node.get("status"),
        "media_year": media_node.get("seasonYear"),
        "role_notes": role_notes.replace("_", " ").title() if isinstance(role_notes, str) else role_notes,
        "source": "AniList"
    }


def fetch_anilist_seiyuu_detail(staff_id, fallback_name=None):
    """Fetch seiyuu profile and voice roles from AniList"""
    from anibase.integrations.tenrai import fetch_tenrai_seiyuu_detail, cache_tenrai_seiyuu_payload

    cached = read_seiyuu_cache(staff_id)
    if cached and is_anilist_seiyuu_payload(cached):
        return cached, None

    cached = None

    def stale_anilist_cache():
        stale = read_seiyuu_cache(staff_id, allow_stale=True)
        return stale if is_anilist_seiyuu_payload(stale) else None

    def return_tenrai_fallback(message):
        payload, error = fetch_tenrai_seiyuu_detail(fallback_name, staff_id)
        if payload:
            cache_tenrai_seiyuu_payload(payload)
            return payload, message
        return None, error or message

    query = """
    query ($id: Int, $page: Int) {
      Staff(id: $id) {
        id
        name {
          full
          native
        }
        image {
          large
        }
        description
        dateOfBirth {
          year
          month
          day
        }
        age
        gender
        bloodType
        homeTown
        language
        siteUrl
        characterMedia(page: $page, perPage: 50, sort: [POPULARITY_DESC]) {
          pageInfo {
            total
            currentPage
            lastPage
          }
          edges {
            characterRole
            characterName
            roleNotes
            characters {
              id
              name {
                full
              }
              image {
                large
              }
            }
            node {
              id
              title {
                english
                romaji
              }
              coverImage {
                extraLarge
              }
              type
              status
              seasonYear
            }
          }
        }
      }
    }
    """

    try:
        staff_data = None
        voice_roles = []
        page = 1
        last_page = 1

        while page <= min(last_page, _constants.SEIYUU_ROLE_PAGE_LIMIT):
            response = requests.post(
                _constants.ANILIST_API_URL,
                json={
                    "query": query,
                    "variables": {
                        "id": staff_id,
                        "page": page
                    }
                },
                timeout=15
            )

            try:
                data = response.json()
            except ValueError:
                mark_anilist_unavailable()
                stale = stale_anilist_cache()
                if stale:
                    return stale, "AniList returned invalid seiyuu response."
                return return_tenrai_fallback("AniList returned invalid seiyuu response.")

            if response.status_code >= 400:
                mark_anilist_unavailable()
                app_log(f"Seiyuu AniList HTTP {response.status_code}: {data}", "ERROR")
                stale = stale_anilist_cache()
                if stale:
                    return stale, f"AniList request failed with HTTP {response.status_code}."
                return return_tenrai_fallback(f"AniList request failed with HTTP {response.status_code}.")

            errors = data.get("errors")
            if errors:
                mark_anilist_unavailable()
                app_log(f"Seiyuu AniList GraphQL errors: {errors}", "ERROR")
                stale = stale_anilist_cache()
                if stale:
                    return stale, "AniList returned errors for the seiyuu request."
                return return_tenrai_fallback("AniList returned errors for the seiyuu request.")

            staff = data.get("data", {}).get("Staff")
            if not staff:
                mark_anilist_available()
                return None, "Seiyuu not found on AniList."

            if staff_data is None:
                staff_data = staff

            char_media = staff.get("characterMedia") or {}
            edges = char_media.get("edges") or []
            page_info = char_media.get("pageInfo") or {}
            last_page = page_info.get("lastPage", 1)

            for edge in edges:
                role = build_seiyuu_role_from_anilist(edge)
                if role:
                    voice_roles.append(role)

            page += 1

        dob = staff_data.get("dateOfBirth") or {}
        birth_str = None
        if dob.get("year") and dob.get("month") and dob.get("day"):
            birth_str = f"{dob['year']}-{dob['month']:02d}-{dob['day']:02d}"
        elif dob.get("month") and dob.get("day"):
            birth_str = f"--{dob['month']:02d}-{dob['day']:02d}"

        payload = {
            "staff_id": staff_data.get("id"),
            "mal_id": None,
            "name_full": (staff_data.get("name") or {}).get("full"),
            "name_native": (staff_data.get("name") or {}).get("native"),
            "name_alternative": None,
            "image": (staff_data.get("image") or {}).get("large"),
            "description": staff_data.get("description"),
            "date_of_birth": birth_str,
            "age": staff_data.get("age"),
            "gender": staff_data.get("gender"),
            "blood_type": staff_data.get("bloodType"),
            "home_town": staff_data.get("homeTown"),
            "language": staff_data.get("language") or "Japanese",
            "site_url": staff_data.get("siteUrl"),
            "voice_roles": voice_roles,
            "source": "AniList"
        }

        mark_anilist_available()
        write_seiyuu_cache(staff_id, payload)
        return payload, None

    except requests.RequestException as e:
        mark_anilist_unavailable()
        app_log(f"Seiyuu AniList request error: {e}", "ERROR")
        stale = stale_anilist_cache()
        if stale:
            return stale, "Unable to reach AniList API."
        return return_tenrai_fallback("Unable to reach AniList API.")
