# anibase/thumbnails.py
"""Pembuatan thumbnail video, cache gambar pratinjau, dan sprite seek preview."""

import os
import re
import json
import math
import time
import secrets
import hashlib
import threading
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    is_valid_cache_file,
    is_shutdown_requested,
)
from anibase.ffmpeg import (
    media_source_fingerprint,
    make_media_generation_result,
    acquire_ffmpeg_media_lock,
    release_ffmpeg_media_lock,
    get_ffmpeg_failure_cache,
    set_ffmpeg_failure_cache,
    clear_ffmpeg_failure_cache,
    run_ffmpeg_command,
    temporary_media_cache_path,
    remove_file_quietly,
)
from anibase.media import (
    get_video_duration_seconds,
    format_timestamp,
)


def get_thumbnail_seek_points(video_path):
    duration_seconds = get_video_duration_seconds(video_path)

    if duration_seconds <= 0:
        return [60, 30, 10, 3, 1, 0]

    candidates = [
        int(duration_seconds * 0.20),
        int(duration_seconds * 0.35),
        min(300, int(duration_seconds * 0.12)),
        60,
        30,
        10,
        3,
        1,
        0
    ]

    seek_points = []
    for point in candidates:
        if 0 <= point < max(1, duration_seconds - 1) and point not in seek_points:
            seek_points.append(point)

    return seek_points or [0]


def get_thumbnail_cache_path(video_path):
    filename = hashlib.md5(
        video_path.encode("utf-8")
    ).hexdigest() + "_v2"

    return os.path.join(
        _constants.THUMBNAIL_CACHE,
        filename + ".jpg"
    )


def get_thumbnail_result(video_path):
    thumbnail_path = get_thumbnail_cache_path(video_path)

    if is_valid_cache_file(thumbnail_path):
        return make_media_generation_result(
            True,
            thumbnail_path,
            "cached",
            "Thumbnail cache found."
        )

    fingerprint = media_source_fingerprint(video_path)
    if fingerprint is None:
        return make_media_generation_result(
            False,
            "",
            "source_not_found",
            "Media source was not found."
        )

    failure_key = ("thumbnail", thumbnail_path, fingerprint)
    cached_failure = get_ffmpeg_failure_cache(failure_key)
    if cached_failure:
        return cached_failure

    lock = acquire_ffmpeg_media_lock(("thumbnail", thumbnail_path))
    if lock is None:
        result = make_media_generation_result(
            False,
            "",
            "busy",
            "Thumbnail generation is already running."
        )
        return result

    last_error = None
    last_status = "failed"
    temp_path = ""

    try:
        if is_valid_cache_file(thumbnail_path):
            return make_media_generation_result(
                True,
                thumbnail_path,
                "cached",
                "Thumbnail cache found."
            )

        cached_failure = get_ffmpeg_failure_cache(failure_key)
        if cached_failure:
            return cached_failure

        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        seek_points = get_thumbnail_seek_points(video_path)
        app_log(f"Starting thumbnail generation for {os.path.basename(video_path)}", "INFO")

        for seek_point in seek_points:
            temp_path = temporary_media_cache_path(thumbnail_path, ".jpg")
            result = run_ffmpeg_command(
                [
                    "ffmpeg",
                    "-y",
                    "-threads",
                    "1",
                    "-ss",
                    format_timestamp(seek_point),
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=640:-2:force_original_aspect_ratio=decrease",
                    "-filter_threads",
                    "1",
                    "-threads",
                    "1",
                    "-q:v",
                    "2",
                    temp_path
                ],
                timeout=_constants.THUMBNAIL_GENERATION_TIMEOUT_SECONDS
            )

            if result["ok"] and is_valid_cache_file(temp_path):
                os.replace(temp_path, thumbnail_path)
                clear_ffmpeg_failure_cache(failure_key)
                return make_media_generation_result(
                    True,
                    thumbnail_path,
                    "generated",
                    "Thumbnail generated."
                )

            last_error = result["message"]
            last_status = result["status"]
            remove_file_quietly(temp_path)

            if result["status"] in {"busy", "ffmpeg_unavailable", "timeout", "error"}:
                break

        if last_error:
            app_log(f"Thumbnail generation failed for {os.path.basename(video_path)}: {last_error}", "WARN")
        else:
            app_log(f"Thumbnail generation failed for {os.path.basename(video_path)}", "WARN")

    except Exception as e:
        app_log(f"Thumbnail generation error: {e}", "ERROR")
        last_error = "Thumbnail generation failed."
        last_status = "error"
    finally:
        remove_file_quietly(temp_path)
        release_ffmpeg_media_lock(("thumbnail", thumbnail_path), lock)

    result = make_media_generation_result(
        False,
        "",
        last_status,
        last_error or "Thumbnail was not generated."
    )
    if result["status"] != "busy":
        set_ffmpeg_failure_cache(failure_key, result)
    return result


def get_thumbnail(video_path):
    result = get_thumbnail_result(video_path)
    if result.get("ok"):
        return result.get("path")

    return None


def seek_preview_identity(video_path):
    fingerprint = media_source_fingerprint(video_path)
    if fingerprint is None:
        return None
    stem = hashlib.md5(video_path.encode("utf-8")).hexdigest()
    version = hashlib.sha256(repr((fingerprint, 3)).encode()).hexdigest()[:24]
    return os.path.join(_constants.THUMBNAIL_CACHE, "seek", stem), version


def seek_preview_frames(duration):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid media duration")
    interval = max(10, math.ceil(duration / 180))
    frames = []
    for index in range(math.ceil(duration / interval)):
        frames.append({
            "startTime": index * interval,
            "endTime": min(duration, (index + 1) * interval),
            "text": f"sprite-{index // 100 + 1:02d}.jpg",
            "x": (index % 10) * 384, "y": ((index % 100) // 10) * 216,
            "w": 384, "h": 216,
        })
    return interval, frames


def read_seek_preview(folder, version):
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("version") != version or not data.get("frames"):
            return None
        if not isinstance(data["frames"], list) or len(data["frames"]) > 180:
            return None
        if any(not isinstance(frame, dict)
               or not re.fullmatch(r"sprite-0[12]\.jpg", str(frame.get("text", "")))
               for frame in data["frames"]):
            return None
        if all(is_valid_cache_file(os.path.join(folder, name))
               for name in {frame["text"] for frame in data["frames"]}):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def generate_seek_preview(video_path, folder, version):
    temp_files = []
    succeeded = False
    try:
        duration = get_video_duration_seconds(video_path)
        interval, frames = seek_preview_frames(duration)
        os.makedirs(folder, exist_ok=True)
        prefix = ".preview-" + secrets.token_hex(8)
        sprite_names = sorted({frame["text"] for frame in frames})
        temp_files = [os.path.join(folder, prefix + name) for name in sprite_names]
        result = run_ffmpeg_command([
            "ffmpeg", "-y", "-threads", "1", "-skip_frame", "nokey", "-i", video_path,
            "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", f"fps=1/{interval}:start_time=0:round=up,"
            f"tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration},"
            "scale=384:216:force_original_aspect_ratio=decrease:flags=lanczos,"
            "pad=384:216:(ow-iw)/2:(oh-ih)/2,setsar=1,tile=10x10:nb_frames=100",
            "-frames:v", str(len(sprite_names)), "-threads", "1", "-q:v", "2",
            os.path.join(folder, prefix + "sprite-%02d.jpg"),
        ], timeout=180)
        if not result["ok"] or not all(is_valid_cache_file(path) for path in temp_files):
            app_log(f'Seek preview generation failed: {result.get("status", "incomplete")} '
                    f'for {os.path.basename(video_path)}', "WARN")
            return
        if seek_preview_identity(video_path) != (folder, version):
            return
        for temp_path, name in zip(temp_files, sprite_names):
            os.replace(temp_path, os.path.join(folder, name))
        manifest_path = os.path.join(folder, "manifest.json")
        temporary = temporary_media_cache_path(manifest_path, ".json")
        temp_files.append(temporary)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"version": version, "frames": frames, "source": video_path}, handle)
        os.replace(temporary, manifest_path)
        app_log(f"Seek preview ready: {os.path.basename(video_path)} ({len(frames)} frames)", "INFO")
        succeeded = True
    except (OSError, ValueError) as error:
        app_log(f"Seek preview unavailable: {type(error).__name__}", "WARN")
    finally:
        for path in temp_files:
            remove_file_quietly(path)
        with _constants.SEEK_PREVIEW_LOCK:
            _constants.SEEK_PREVIEW_ACTIVE.discard(folder)
            if not succeeded:
                _constants.SEEK_PREVIEW_FAILURES[(folder, version)] = time.monotonic() + 60


def request_seek_preview(video_path, folder, version):
    with _constants.SEEK_PREVIEW_LOCK:
        now = time.monotonic()
        for key in list(_constants.SEEK_PREVIEW_FAILURES):
            if _constants.SEEK_PREVIEW_FAILURES[key] <= now:
                _constants.SEEK_PREVIEW_FAILURES.pop(key, None)
        if (folder, version) in _constants.SEEK_PREVIEW_FAILURES:
            return "unavailable"
        if _constants.SEEK_PREVIEW_ACTIVE:
            return "pending"
        if is_shutdown_requested():
            return "unavailable"
        _constants.SEEK_PREVIEW_ACTIVE.add(folder)
        try:
            threading.Thread(target=generate_seek_preview,
                             args=(video_path, folder, version), daemon=True,
                             name="seek-preview").start()
        except RuntimeError:
            _constants.SEEK_PREVIEW_ACTIVE.discard(folder)
            return "unavailable"
    return "pending"
