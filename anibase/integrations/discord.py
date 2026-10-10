# anibase/integrations/discord.py
"""Integrasi Discord Rich Presence untuk AniBase."""

import os
import re
import math
import time
import threading
from urllib.parse import urlparse
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    safe_join_media_path,
    wait_for_shutdown,
    load_json_dict_if_exists,
)
from anibase.media import (
    format_episode_number,
    get_episode_number,
    get_episode_default_display_name,
)
from anibase.watch import get_watch_history_display_name


def reset_discord_rpc():
    _constants.RPC_CONNECTION_STATUS = "idle"
    with _constants.RPC_STATE_LOCK:
        _constants.RPC_DESIRED = None
    _constants.RPC_RETRY_AT = 0.0
    _constants.RPC_RETRY_DELAY = 2.0
    _constants.RPC_LAST_PAYLOAD = None

    with _constants.RPC_LOCK:
        if _constants.rpc is not None:
            try:
                if _constants.rpc_connected:
                    _constants.rpc.clear()
                _constants.rpc.close()
            except Exception:
                pass

        _constants.rpc = None
        _constants.rpc_connected = False
        _constants.RPC_START_TIME = None
        _constants.CURRENT_RPC_ANIME = None
        _constants.CURRENT_RPC_OWNER = None


def get_discord_rpc_client():
    if _constants.Presence is None or not _constants.DISCORD_CLIENT_ID:
        return None

    if _constants.rpc is None:
        _constants.rpc = _constants.Presence(_constants.DISCORD_CLIENT_ID)

    return _constants.rpc


def update_discord_rpc(anime_name, episode_num, time_str=None, owner_id=None, payload=None):
    if not _constants.DISCORD_RPC_ENABLED or time.monotonic() < _constants.RPC_RETRY_AT:
        return

    with _constants.RPC_LOCK:
        rpc_client = None
        try:
            rpc_client = get_discord_rpc_client()
            if rpc_client is None:
                return
            if _constants.CURRENT_RPC_ANIME != anime_name or _constants.CURRENT_RPC_OWNER != owner_id:
                _constants.RPC_START_TIME = int(time.time())
            _constants.CURRENT_RPC_ANIME = anime_name
            _constants.CURRENT_RPC_OWNER = owner_id

            if not _constants.rpc_connected:
                _constants.RPC_CONNECTION_STATUS = "connecting"
                rpc_client.connect()
                _constants.rpc_connected = True

            episode_label = format_episode_number(episode_num) or str(episode_num)
            state_text = f"Episode {episode_label}"
            if time_str:
                state_text += f" ({time_str})"

            presence = dict(payload) if payload else dict(
                details=str(anime_name)[:128], state=state_text[:128],
                start=_constants.RPC_START_TIME, large_image="anibase_logo",
            )
            if _constants.ActivityType is not None:
                presence["activity_type"] = _constants.ActivityType.WATCHING
            if _constants.StatusDisplayType is not None:
                presence["status_display_type"] = _constants.StatusDisplayType.DETAILS

            if presence != _constants.RPC_LAST_PAYLOAD or time.monotonic() - _constants.RPC_LAST_SENT_AT >= 30:
                rpc_client.update(**presence)
                _constants.RPC_LAST_PAYLOAD = dict(presence)
                _constants.RPC_LAST_SENT_AT = time.monotonic()
            _constants.RPC_RETRY_AT = 0.0
            _constants.RPC_RETRY_DELAY = 2.0
            _constants.RPC_CONNECTION_STATUS = "connected"
        except Exception as e:
            _constants.RPC_CONNECTION_STATUS = (
                "unavailable" if type(e).__name__ in {"DiscordNotFound", "InvalidPipe", "FileNotFoundError"}
                else "invalid_id" if type(e).__name__ == "InvalidID" else "retrying"
            )
            app_log(f"Discord RPC error: {e}", "WARN")
            try:
                if rpc_client is not None:
                    rpc_client.close()
            except Exception:
                pass
            _constants.rpc = None
            _constants.rpc_connected = False
            _constants.RPC_LAST_PAYLOAD = None
            _constants.RPC_RETRY_AT = time.monotonic() + _constants.RPC_RETRY_DELAY
            _constants.RPC_RETRY_DELAY = min(60.0, _constants.RPC_RETRY_DELAY * 2)


def clear_discord_rpc(owner_id=None):
    """Menghapus status Discord Rich Presence."""
    if not _constants.DISCORD_RPC_ENABLED:
        return

    with _constants.RPC_LOCK:
        if owner_id is not None and owner_id != _constants.CURRENT_RPC_OWNER:
            return False

        if _constants.rpc is not None and _constants.rpc_connected:
            try:
                _constants.rpc.clear()
                debug_log("Discord RPC cleared")
            except Exception as e:
                app_log(f"Discord RPC clear error: {e}", "WARN")
                try:
                    _constants.rpc.close()
                except Exception:
                    pass
                _constants.rpc = None
                _constants.rpc_connected = False

        _constants.RPC_START_TIME = None
        _constants.CURRENT_RPC_ANIME = None
        _constants.CURRENT_RPC_OWNER = None
        _constants.RPC_LAST_PAYLOAD = None
        return True


def run_discord_rpc_worker():
    """Run Discord IPC outside request threads and keep only the latest pending task."""
    while True:
        with _constants.RPC_DISPATCH_LOCK:
            task = _constants.RPC_PENDING_TASK
            _constants.RPC_PENDING_TASK = None
            if task is None:
                _constants.RPC_WORKER_THREAD = None
                return

        action, args = task
        if action == "update":
            update_discord_rpc(*args)
        elif action == "clear":
            clear_discord_rpc(*args)
        elif action == "reconcile":
            reconcile_discord_presence()


def reconcile_discord_presence():
    with _constants.RPC_STATE_LOCK:
        desired = dict(_constants.RPC_DESIRED) if _constants.RPC_DESIRED else None
    if desired is None:
        clear_discord_rpc()
    else:
        update_discord_rpc(desired["title"], desired["episode_num"],
                           owner_id=desired["session"], payload=desired["payload"])


def discord_media_metadata(anime_name, episode):
    """Use only the selected media's cache; presence never fetches metadata."""
    from anibase.metadata import get_season_metadata_name
    movie = anime_name == "Movies"
    season = os.path.dirname(str(episode).replace("\\", "/"))
    title = get_watch_history_display_name(anime_name, episode)
    identity = title if movie else get_season_metadata_name(anime_name, season)
    path = safe_join_media_path(_constants.METADATA_CACHE, identity + ".json")
    return load_json_dict_if_exists(path) if path else {}


def discord_media_description(anime_name, episode, label=""):
    from anibase.metadata import get_episode_display_override
    movie = anime_name == "Movies"
    season = os.path.dirname(str(episode).replace("\\", "/"))
    title = get_watch_history_display_name(anime_name, episode)
    metadata = discord_media_metadata(anime_name, episode)
    title = metadata.get("title") or title
    filename = os.path.basename(str(episode).replace("\\", "/"))
    label = "Movie" if movie else (label or get_episode_default_display_name(filename))
    if not movie:
        number = format_episode_number(get_episode_number(filename))
        episode_title = get_episode_display_override(anime_name, episode) or label
        label = f"Episode {number}" if number else label
        if episode_title and episode_title != label:
            label += f" · {episode_title}"
    if season and not movie:
        match = re.fullmatch(r"(?:season\s*|s)(\d+)", os.path.basename(season), re.I)
        season_label = f"Season {int(match.group(1))}" if match else season.replace("/", " · ")
        label = f"{season_label} · {label}"
    return str(title)[:128], str(label)[:128]


def discord_media_artwork(anime_name, episode, title):
    from anibase.metadata import normalize_provider_id
    metadata = discord_media_metadata(anime_name, episode) if anime_name and episode else {}
    artwork = dict(large_image="anibase_logo", large_text=str(title)[:128])
    poster = metadata.get("poster")
    if isinstance(poster, str) and len(poster) <= 256:
        try:
            url = urlparse(poster)
            if (url.scheme == "https" and url.hostname in {
                    "s4.anilist.co", "s3.anilist.co", "cdn.myanimelist.net"}
                    and not url.username and not url.password and url.port in (None, 443)):
                artwork.update(large_image=poster, small_image="anibase_logo",
                               small_text="AniBase · Your private cinema")
        except ValueError:
            pass
    anilist_id = normalize_provider_id(metadata.get("anilist_id"))
    mal_id = normalize_provider_id(metadata.get("mal_id"))
    if anilist_id:
        artwork["buttons"] = [{"label": "View on AniList", "url": f"https://anilist.co/anime/{anilist_id}"}]
    elif mal_id:
        artwork["buttons"] = [{"label": "View on MyAnimeList", "url": f"https://myanimelist.net/anime/{mal_id}"}]
    return artwork


def start_external_discord_presence(anime_name, episode, owner):
    if not _constants.DISCORD_RPC_ENABLED:
        return
    title, label = discord_media_description(anime_name, episode)
    with _constants.RPC_STATE_LOCK:
        _constants.RPC_DESIRED = dict(session=owner, title=title, episode_num=0,
                           payload=dict(details=title, state=(label + " · External player")[:128],
                                        **discord_media_artwork(anime_name, episode, title), start=int(time.time())))
    dispatch_discord_rpc_task("reconcile")
    start_discord_presence_monitor()


def monitor_discord_presence():
    while not wait_for_shutdown(5):
        with _constants.RPC_STATE_LOCK:
            if _constants.RPC_DESIRED and _constants.RPC_DESIRED.get("expires", float("inf")) < time.monotonic():
                _constants.RPC_DESIRED = None
            needs_work = _constants.RPC_DESIRED is not None or _constants.CURRENT_RPC_OWNER is not None
        if needs_work:
            dispatch_discord_rpc_task("reconcile")
    reset_discord_rpc()


def start_discord_presence_monitor():
    with _constants.RPC_STATE_LOCK:
        if _constants.RPC_MONITOR_THREAD is None or not _constants.RPC_MONITOR_THREAD.is_alive():
            _constants.RPC_MONITOR_THREAD = threading.Thread(
                target=monitor_discord_presence,
                daemon=True,
                name="discord-presence-monitor"
            )
            _constants.RPC_MONITOR_THREAD.start()


def accept_discord_presence_event(data):
    """Ordered session events; only an explicit playback start can take ownership."""
    session = str(data.get("session", ""))[:128]
    sequence = data.get("sequence")
    event = data.get("event")
    if not session or not isinstance(sequence, int) or sequence < 1 or event not in {
        "playing", "heartbeat", "sync", "pause", "ended", "waiting", "stop"
    }:
        raise ValueError("Invalid presence event")
    position = float(data.get("position", 0))
    duration = float(data.get("duration", 0))
    speed = float(data.get("speed", 1))
    if not all(math.isfinite(x) for x in (position, duration, speed)) or speed <= 0:
        raise ValueError("Invalid playback timing")
    with _constants.RPC_STATE_LOCK:
        if sequence <= _constants.RPC_SESSION_VERSIONS.get(session, 0):
            return False
        if session not in _constants.RPC_SESSION_VERSIONS and len(_constants.RPC_SESSION_VERSIONS) >= 512:
            _constants.RPC_SESSION_VERSIONS.pop(next(iter(_constants.RPC_SESSION_VERSIONS)))
        _constants.RPC_SESSION_VERSIONS[session] = sequence
        owner = _constants.RPC_DESIRED.get("session") if _constants.RPC_DESIRED else None
        if event == "pause":
            if owner != session:
                return False
            _constants.RPC_DESIRED["paused"] = True
            presence = _constants.RPC_DESIRED["payload"]
            presence["state"] = ("Paused · " + _constants.RPC_DESIRED.get("label", presence["state"]))[:128]
            presence.pop("start", None)
            presence.pop("end", None)
            _constants.RPC_DESIRED["expires"] = time.monotonic() + 90
        elif event == "heartbeat" and owner == session and _constants.RPC_DESIRED.get("paused"):
            _constants.RPC_DESIRED["expires"] = time.monotonic() + 90
        elif event in {"ended", "waiting", "stop"}:
            if owner != session:
                return False
            _constants.RPC_DESIRED = None
        else:
            if event != "playing" and owner not in (None, session):
                return False
            title = str(data.get("title") or data.get("anime_name") or "AniBase")[:128]
            label = str(data.get("label") or "Watching anime")[:128]
            if data.get("anime_name") and data.get("episode"):
                title, label = discord_media_description(str(data["anime_name"]),
                                                         str(data["episode"]), label)
            now = time.time()
            if label == "Movie":
                label = "Movie · Watching"
            payload = dict(details=title, state=label,
                           **discord_media_artwork(data.get("anime_name"), data.get("episode"), title))
            if duration > 0:
                position = min(duration, max(0, position))
                payload["start"] = int(now - position / speed)
                if _constants.DISCORD_TIMER_MODE == "remaining":
                    payload["end"] = int(now + (duration - position) / speed)
            previous = _constants.RPC_DESIRED.get("payload", {}) if _constants.RPC_DESIRED else {}
            if event == "heartbeat" and previous.get("state") == label and previous.get("details") == title:
                if abs(previous.get("start", 0) - payload.get("start", 0)) <= 2:
                    for key in ("start", "end"):
                        if key in previous and key in payload:
                            payload[key] = previous[key]
            _constants.RPC_DESIRED = dict(session=session, title=title, episode_num=0, label=label,
                                payload=payload, expires=time.monotonic() + 90)
    dispatch_discord_rpc_task("reconcile")
    start_discord_presence_monitor()
    return True


def dispatch_discord_rpc_task(action, *args):
    """Queue Discord work without making playback/progress requests wait for IPC."""
    if not _constants.DISCORD_RPC_ENABLED:
        return False

    with _constants.RPC_DISPATCH_LOCK:
        _constants.RPC_PENDING_TASK = (action, args)
        if _constants.RPC_WORKER_THREAD is not None and _constants.RPC_WORKER_THREAD.is_alive():
            return True

        _constants.RPC_WORKER_THREAD = threading.Thread(
            target=run_discord_rpc_worker,
            daemon=True,
            name="discord-rpc-worker",
        )
        _constants.RPC_WORKER_THREAD.start()

    return True


def dispatch_discord_rpc_update(anime_name, episode_num, time_str=None, owner_id=None):
    return dispatch_discord_rpc_task(
        "update",
        anime_name,
        episode_num,
        time_str,
        owner_id,
    )


def dispatch_discord_rpc_clear(owner_id=None):
    with _constants.RPC_STATE_LOCK:
        if _constants.RPC_DESIRED and _constants.RPC_DESIRED["session"] != owner_id:
            return False
    return dispatch_discord_rpc_task("clear", owner_id)


def clear_discord_rpc_when_process_exits(process, owner_id):
    started = time.monotonic()
    try:
        process.wait()
    except Exception as e:
        app_log(f"Media player monitor error: {e}", "WARN")
    finally:
        with _constants.RPC_STATE_LOCK:
            if _constants.RPC_DESIRED and _constants.RPC_DESIRED["session"] == owner_id:
                if time.monotonic() - started < 2:
                    _constants.RPC_DESIRED["expires"] = time.monotonic() + 90
                    _constants.RPC_DESIRED["payload"]["state"] = "Opened in external player"
                else:
                    _constants.RPC_DESIRED = None
                dispatch_discord_rpc_task("reconcile")
            else:
                dispatch_discord_rpc_clear(owner_id)
