# anibase/integrations/aniskip.py
"""Integrasi AniSkip API untuk deteksi skip opening dan ending."""

import os
import json
import time
import requests
import anibase.constants as _constants
from anibase.logging import app_log


def get_aniskip_times(mal_id, episode_num, episode_length=0):
    """Retrieve opening and ending skip times from AniSkip with disk caching."""
    if not mal_id or episode_num is None or episode_num <= 0:
        return {"found": False, "results": [], "reason": "invalid_parameters"}

    os.makedirs(_constants.ANISKIP_CACHE, exist_ok=True)
    cache_file = os.path.join(_constants.ANISKIP_CACHE, f"{mal_id}_{int(episode_num)}.json")

    # 1. Check local cache
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            cached_at = cached.get("cached_at", 0)
            if cached.get("found") or (time.time() - cached_at < 7 * 86400):
                return cached
        except (OSError, ValueError, KeyError):
            pass

    # 2. Query AniSkip API
    url = f"{_constants.ANISKIP_BASE_URL}/skip-times/{int(mal_id)}/{int(episode_num)}"
    params = [
        ("types[]", "op"),
        ("types[]", "ed"),
        ("types[]", "mixed-op"),
        ("types[]", "mixed-ed"),
        ("types[]", "recap")
    ]
    if episode_length and episode_length > 0:
        params.append(("episodeLength", str(int(episode_length))))

    try:
        response = requests.get(url, params=params, timeout=_constants.ANISKIP_TIMEOUT_SECONDS)
        if response.status_code == 200:
            data = response.json()
            raw_results = data.get("results") or []
            results = []
            for item in raw_results:
                interval = item.get("interval") or {}
                start = float(interval.get("startTime", 0))
                end = float(interval.get("endTime", 0))
                skip_type = str(item.get("skipType", "")).lower()
                name_map = {
                    "op": "Opening",
                    "ed": "Ending",
                    "mixed-op": "Opening",
                    "mixed-ed": "Ending",
                    "recap": "Recap"
                }
                name = name_map.get(skip_type, "Segment")
                if end > start:
                    results.append({
                        "type": skip_type,
                        "name": name,
                        "start": start,
                        "end": end
                    })

            payload = {
                "found": bool(results),
                "results": results,
                "cached_at": time.time()
            }
            try:
                with open(cache_file, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
            except OSError:
                pass
            return payload

        elif response.status_code == 404:
            payload = {
                "found": False,
                "results": [],
                "cached_at": time.time()
            }
            try:
                with open(cache_file, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
            except OSError:
                pass
            return payload

    except (requests.RequestException, ValueError) as error:
        app_log(f"AniSkip request failed for mal_id={mal_id}, ep={episode_num}: {error}", "WARN")

    return {"found": False, "results": [], "reason": "network_unavailable"}
