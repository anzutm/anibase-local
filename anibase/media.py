# anibase/media.py
"""Manajemen media video: probing durasi, resolusi, identifikasi episode,
dan caching metadata episode."""

import os
import re
import json
import subprocess
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    run_hidden_subprocess,
    get_media_tool_path,
    atomic_write_json_file,
)


def get_video_duration_seconds(video_path):
    try:
        result = run_hidden_subprocess(
            [
                get_media_tool_path("ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=_constants.MEDIA_PROBE_TIMEOUT_SECONDS
        )

        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]))
    except subprocess.TimeoutExpired:
        app_log(f"Duration detection timed out for {video_path}", "WARN")
        return 0
    except Exception:
        return 0


def format_timestamp(seconds):
    seconds = max(0, int(seconds or 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_video_resolution(video_path):
    try:
        result = run_hidden_subprocess(
            [
                get_media_tool_path("ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=_constants.MEDIA_PROBE_TIMEOUT_SECONDS
        )

        debug_log(f"ffprobe streams for {video_path}: {result.stdout}")

        data = json.loads(result.stdout)

        for stream in data["streams"]:
            if stream["codec_type"] == "video":
                debug_log(f"Video height for {video_path}: {stream['height']}")
                return f'{stream["height"]}p'

    except subprocess.TimeoutExpired:
        app_log(f"Resolution detection timed out for {video_path}", "WARN")
    except Exception as e:
        app_log(f"Resolution detection failed for {video_path}: {e}", "WARN")

    return ""


def get_video_duration(video_path):
    try:
        result = run_hidden_subprocess(
            [
                get_media_tool_path("ffprobe"),
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=_constants.MEDIA_PROBE_TIMEOUT_SECONDS
        )

        data = json.loads(result.stdout)
        seconds = int(float(data["format"]["duration"]))
        minutes = seconds // 60
        return f"{minutes} min"

    except subprocess.TimeoutExpired:
        app_log(f"Duration detection timed out for {video_path}", "WARN")
        return ""
    except Exception:
        return ""


def normalize_episode_number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    if number <= 0:
        return None

    if number.is_integer():
        return int(number)

    return number


def format_episode_number(value):
    number = normalize_episode_number(value)
    if number is None:
        return ""

    if isinstance(number, int):
        return str(number)

    return f"{number:g}"


_EPISODE_METADATA_TRAILER_RE = re.compile(
    r"(?i)[\s._-]+("
    r"(?:mkv|mp4|avi|webm)?(?:2160|1440|1080|720|576|480|360|240)p|"
    r"(?:1920x1080|1280x720|848x480|640x360)|"
    r"mkv|mp4|avi|webm|"
    r"end|tamat|final|"
    r"x264|x265|h264|h265|hevc|avc|av1|10bit|8bit|hi10p?|"
    r"aac|flac|mp3|opus|"
    r"sub[\s._-]?indo|subtitle[\s._-]?indonesia|dual[\s._-]?audio|raw|softsub|hardsub|"
    r"bd|bdrip|bluray|blu-ray|dvd|dvdrip|hdtv|web-?dl|webrip|"
    r"\[[0-9a-f]{8}\]|\([0-9a-f]{8}\)"
    r")$"
)


def _is_bracket_metadata(bracket_inner):
    bracket_inner = bracket_inner.strip()
    if re.match(r"^\d+(?:\.\d+)?\s*[-~]\s*\d+(?:\.\d+)?$", bracket_inner):
        return False
    num_match = re.match(r"^(\d+(?:\.\d+)?)$", bracket_inner)
    if num_match:
        val = normalize_episode_number(num_match.group(1))
        if isinstance(val, int) and 1900 <= val <= 2099:
            return True
        return False
    return True


def _strip_trailing_episode_metadata(s):
    s = s.strip()
    while True:
        prev = s
        m = re.search(r"\s*([\[\(])([^\]\)]*)([\]\)])\s*$", s)
        if m and _is_bracket_metadata(m.group(2)):
            s = s[:m.start()].strip()
            continue
        s = _EPISODE_METADATA_TRAILER_RE.sub("", s).strip()
        if s == prev:
            break
    return s


def parse_episode_identity(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    normalized = re.sub(r"[_]+", " ", stem)
    normalized = re.sub(r"[-]{2,}", " - ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    special_patterns = (
        ("ncop", "NCOP", r"(?i)(?:^|[\s.\[\(\-])(NCOP)\s*[-_. #]*(\d+(?:\.\d+)?(?![\d.]|p\b))?\b"),
        ("nced", "NCED", r"(?i)(?:^|[\s.\[\(\-])(NCED)\s*[-_. #]*(\d+(?:\.\d+)?(?![\d.]|p\b))?\b"),
        ("ova", "OVA", r"(?i)(?:^|[\s.\[\(\-])(OVA)\s*[-_. #]*(\d+(?:\.\d+)?(?![\d.]|p\b))?\b"),
        ("oad", "OAD", r"(?i)(?:^|[\s.\[\(\-])(OAD)\s*[-_. #]*(\d+(?:\.\d+)?(?![\d.]|p\b))?\b"),
        ("ona", "ONA", r"(?i)(?:^|[\s.\[\(\-])(ONA)\s*[-_. #]*(\d+(?:\.\d+)?(?![\d.]|p\b))?\b"),
        ("special", "Special", r"(?i)(?:^|[\s.\[\(\-])(?:SPECIAL|SP)\s*[-_. #]*(\d+(?:\.\d+)?(?![\d.]|p\b))?\b"),
    )
    for kind, label, pattern in special_patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        number_group = match.lastindex and match.group(match.lastindex)
        number = normalize_episode_number(number_group) if number_group else None
        display_name = label
        if number is not None:
            display_name += f" {format_episode_number(number)}"
        return {
            "kind": kind,
            "number": number or 0,
            "end_number": None,
            "label": display_name,
            "display_name": display_name,
        }

    range_patterns = (
        r"(?i)\bS\d{1,2}\s*E\s*(\d+(?:\.\d+)?)\s*[-~]\s*(?:E\s*)?(\d+(?:\.\d+)?)\b",
        r"(?i)\b(?:EPISODES?|EPS?|E)\s*[-_. #]*(\d+(?:\.\d+)?)\s*[-~]\s*(?:EPISODES?|EPS?|E)?\s*(\d+(?:\.\d+)?)\b",
        r"(?i)[\[\(]\s*(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*[\]\)]",
        r"(?i)\b(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\b(?=.*\bBATCH\b)",
    )
    for pattern in range_patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        start = normalize_episode_number(match.group(1))
        end = normalize_episode_number(match.group(2))
        if start is None or end is None or float(end) < float(start):
            continue
        start_label = format_episode_number(start)
        end_label = format_episode_number(end)
        return {
            "kind": "batch",
            "number": start,
            "end_number": end,
            "label": f"{start_label}-{end_label}",
            "display_name": f"Episodes {start_label}-{end_label} \u00b7 Batch",
        }

    cleaned_stem = _strip_trailing_episode_metadata(stem)
    cleaned_normalized = re.sub(r"[_]+", " ", cleaned_stem)
    cleaned_normalized = re.sub(r"[-]{2,}", " - ", cleaned_normalized)
    cleaned_normalized = re.sub(r"\s+", " ", cleaned_normalized).strip()

    patterns = [
        r"(?i)\bS\d{1,2}\s*E\s*(\d+(?:\.\d+)?)\b",
        r"(?i)\b(?:EP|EPS|Episode)\s*[-_. #]*(\d+(?:\.\d+)?)\b",
        r"(?i)(?:^|[\s._\-\]A-Za-z])S\d{1,2}[\s._-]+(\d+(?:\.\d+)?)\b",
        r"(?i)(?:^|[-\s._])(\d+(?:\.\d+)?)(?=[-_.\s]+(?:(?:mkv|mp4|avi|webm)[-_.\s]*)?(?:2160|1440|1080|720|576|480|360|240)p(?:[-_.\s]|$))",
        r"(?i)(?:^|[\s.\[\(])-\s*(\d+(?:\.\d+)?)(?=\s*(?:$|[\]\)\[\(]|[A-Za-z]))",
        r"(?i)[\[\(]\s*(\d+(?:\.\d+)?)\s*[\]\)]",
        r"(?i)(?:^|[\s._-])(\d+(?:\.\d+)?)(?=\s*(?:$|[\]\)\[\(]))",
    ]

    for text_to_search in [cleaned_normalized, normalized]:
        for pattern_index, pattern in enumerate(patterns):
            matches = list(re.finditer(pattern, text_to_search))
            for match in reversed(matches):
                number = normalize_episode_number(match.group(1))
                if pattern_index >= 2 and isinstance(number, int) and 1900 <= number <= 2099:
                    continue
                if number is not None:
                    label = format_episode_number(number)
                    return {
                        "kind": "episode",
                        "number": number,
                        "end_number": None,
                        "label": label,
                        "display_name": f"Episode {label}",
                    }

    if re.search(r"(?i)\bBATCH\b", normalized):
        return {
            "kind": "batch",
            "number": 0,
            "end_number": None,
            "label": "Batch",
            "display_name": "Batch",
        }

    return {
        "kind": "unknown",
        "number": 0,
        "end_number": None,
        "label": stem,
        "display_name": stem,
    }


def get_episode_number(filename):
    identity = parse_episode_identity(filename)
    if identity["number"]:
        return identity["number"]
    if identity["kind"] == "unknown":
        app_log(f"Could not parse episode number from filename: {filename}", "WARN")
    return 0


def get_episode_display_label(filename):
    return parse_episode_identity(filename)["label"]


def get_episode_default_display_name(filename):
    return parse_episode_identity(filename)["display_name"]


def get_episode_sort_key(filename):
    identity = parse_episode_identity(filename)
    kind_order = {
        "episode": 0,
        "batch": 1,
        "special": 2,
        "ova": 3,
        "oad": 4,
        "ona": 5,
        "ncop": 6,
        "nced": 7,
        "unknown": 8,
    }
    number = identity["number"]
    return (
        kind_order.get(identity["kind"], 8),
        float(number) if number else float("inf"),
        os.path.basename(filename).lower(),
    )


def load_episode_cache_file(cache_file):
    if not os.path.exists(cache_file):
        return {}

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def save_episode_cache_file(cache_file, cache_data):
    atomic_write_json_file(
        cache_file,
        cache_data,
        "Episode cache",
        ensure_ascii=False
    )


def get_episode_cache(anime_name, video_path, season_name=None):
    cache_name = anime_name

    if season_name:
        cache_name += "_" + season_name

    cache_name = (
        cache_name
        .replace("\\", "_")
        .replace("/", "_")
        .replace(":", "")
    )

    cache_file = os.path.join(
        _constants.EPISODE_CACHE,
        f"{cache_name}.json"
    )

    filename = os.path.basename(video_path)

    with _constants.EPISODE_CACHE_LOCK:
        cache_data = load_episode_cache_file(cache_file)

    if filename in cache_data:
        return cache_data[filename]

    duration = get_video_duration(video_path)
    resolution = get_video_resolution(video_path)

    episode_cache_entry = {
        "duration": duration,
        "resolution": resolution
    }

    with _constants.EPISODE_CACHE_LOCK:
        cache_data = load_episode_cache_file(cache_file)

        if filename in cache_data:
            return cache_data[filename]

        cache_data[filename] = episode_cache_entry
        save_episode_cache_file(cache_file, cache_data)

    return cache_data[filename]
