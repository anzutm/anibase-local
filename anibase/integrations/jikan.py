# anibase/integrations/jikan.py
"""Integrasi Jikan API (MyAnimeList v4) untuk data seiyuu dan voice roles."""

import requests
import anibase.constants as _constants
from anibase.logging import app_log
from anibase.metadata import (
    clean_person_description,
    get_nested_image,
)


def build_seiyuu_role_from_jikan(voice):
    anime = voice.get("anime") or {}
    character = voice.get("character") or {}
    if not anime:
        return None

    return {
        "character_id": character.get("mal_id"),
        "character_name": character.get("name") or "Unknown",
        "character_image": get_nested_image(character.get("images")),
        "media_id": anime.get("mal_id"),
        "media_title": anime.get("title") or "Unknown",
        "media_title_romaji": anime.get("title"),
        "media_poster": get_nested_image(anime.get("images")),
        "media_type": "ANIME",
        "media_status": None,
        "media_year": None,
        "role_notes": voice.get("role"),
        "source": "MyAnimeList"
    }


def fetch_jikan_seiyuu_detail(staff_name, anilist_staff_id=None):
    if not staff_name:
        return None, "Tenrai fallback needs a seiyuu name."

    try:
        search_response = requests.get(
            f"{_constants.JIKAN_BASE_URL}/people",
            params={"q": staff_name, "limit": 1},
            timeout=15
        )

        try:
            search_data = search_response.json()
        except ValueError:
            return None, "MyAnimeList returned invalid seiyuu search response."

        if search_response.status_code >= 400:
            return None, f"MyAnimeList seiyuu search failed with HTTP {search_response.status_code}."

        results = search_data.get("data") or []
        if not results:
            return None, "Seiyuu was not found on MyAnimeList."

        person_id = results[0].get("mal_id")
        if not person_id:
            return None, "MyAnimeList seiyuu search did not include a person id."

        detail_response = requests.get(
            f"{_constants.JIKAN_BASE_URL}/people/{person_id}/full",
            timeout=15
        )

        try:
            detail_data = detail_response.json()
        except ValueError:
            return None, "MyAnimeList returned invalid seiyuu detail response."

        if detail_response.status_code >= 400:
            return None, f"MyAnimeList seiyuu detail failed with HTTP {detail_response.status_code}."

        person = detail_data.get("data") or results[0]
        voice_roles = []
        seen_roles = set()

        for voice in person.get("voices") or []:
            role = build_seiyuu_role_from_jikan(voice)
            if not role:
                continue
            role_key = (role.get("media_id"), role.get("character_id"), role.get("character_name"))
            if role_key in seen_roles:
                continue
            seen_roles.add(role_key)
            voice_roles.append(role)

        birthday = person.get("birthday")
        birth_date = birthday[:10] if isinstance(birthday, str) and len(birthday) >= 10 else None
        native_name = " ".join(
            part for part in [person.get("family_name"), person.get("given_name")] if part
        ) or None

        payload = {
            "staff_id": anilist_staff_id,
            "mal_id": person.get("mal_id"),
            "name_full": person.get("name") or staff_name,
            "name_native": native_name,
            "name_alternative": person.get("alternate_names") or None,
            "image": get_nested_image(person.get("images"), size="image_url"),
            "description": clean_person_description(person.get("about")),
            "date_of_birth": birth_date,
            "age": None,
            "gender": None,
            "blood_type": None,
            "home_town": None,
            "language": "Japanese",
            "site_url": person.get("url"),
            "voice_roles": voice_roles,
            "source": "MyAnimeList"
        }

        return payload, None

    except requests.RequestException as e:
        app_log(f"Seiyuu MyAnimeList request error for {staff_name}: {e}", "ERROR")
        return None, "Unable to reach MyAnimeList seiyuu API."
