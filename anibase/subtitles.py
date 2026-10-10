# anibase/subtitles.py
"""Ekstraksi dan pemrosesan subtitle (WebVTT, ASS/SSA, format sidecar external),
pembersihan watermark fansub, dan deteksi track subtitle."""

import os
import re
import json
import hashlib
import subprocess
import anibase.constants as _constants
from anibase.logging import app_log, debug_log
from anibase.utils import (
    is_valid_cache_file,
    is_resolved_path_inside,
    run_hidden_subprocess,
    get_media_tool_path,
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


def get_subtitle_vtt_path(anime_name, episode_path, track_id=None):
    safe_anime = re.sub(r'[<>:"/\\|?*]', '_', anime_name)
    safe_episode = re.sub(r'[<>:"|?*]', '_', episode_path).replace('/', '_').replace('\\', '_')

    if track_id:
        safe_track = re.sub(r'[^a-zA-Z0-9_-]', '_', track_id)
        vtt_filename = f"{safe_episode}_track_{safe_track}.clean-v3.vtt"
    else:
        vtt_filename = f"{safe_episode}.clean-v3.vtt"
    return os.path.join(_constants.SUBTITLE_CACHE, safe_anime, vtt_filename)


def is_non_subtitle_text(text):
    plain_text = re.sub(r'<[^>]*>', '', text or "")
    plain_text = re.sub(r'\{.*?\}', '', plain_text).strip()
    normalized = " ".join(plain_text.casefold().split())

    if not normalized:
        return True

    if re.fullmatch(r'(?:https?://|www\.)\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+/?', normalized):
        return True

    numeric_tokens = re.findall(r'-?\d+(?:\.\d+)?', normalized)
    starts_like_ass_drawing = bool(
        re.match(r'^(?:m|n|l|b|s|p|c)\s+-?\d', normalized)
    )

    if starts_like_ass_drawing and len(numeric_tokens) >= 4:
        return True

    return False


def is_stylized_karaoke_subtitle(text):
    """Return True for ASS karaoke/effect lines unsuitable for WebVTT players."""
    raw_text = text or ""

    if re.search(r'\{[^}]*\\(?:k|K|kf|ko|kt)\d+', raw_text):
        return True
    if re.search(r'<(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}>', raw_text):
        return True

    return bool(re.search(r'\{[^}]*\\p[1-9]\d*', raw_text, flags=re.IGNORECASE))


def _vtt_timestamp_seconds(value):
    parts = value.strip().replace(',', '.').split(':')
    try:
        if len(parts) == 3:
            return (float(parts[0]) * 3600) + (float(parts[1]) * 60) + float(parts[2])
        if len(parts) == 2:
            return (float(parts[0]) * 60) + float(parts[1])
    except (TypeError, ValueError):
        pass
    return None


def _subtitle_text_key(text):
    plain_text = re.sub(r'<[^>]*>', '', text or "")
    plain_text = re.sub(r'\{.*?\}', '', plain_text)
    return " ".join(plain_text.casefold().split())


def clean_generated_subtitle_vtt(vtt_path):
    with open(vtt_path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()

    cues = []
    i = 0
    while i < len(lines):
        timestamp = lines[i].strip()
        if "-->" not in timestamp:
            i += 1
            continue

        timing = re.fullmatch(r'(\d{2,}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+(\d{2,}:\d{2}(?::\d{2})?[.,]\d{3})(?:\s+.*)?', timestamp)
        if not timing:
            i += 1
            continue
        start_raw, end_raw = timing.groups()
        start = _vtt_timestamp_seconds(start_raw)
        end = _vtt_timestamp_seconds(end_raw)
        cue_text = []
        i += 1
        while i < len(lines) and lines[i].strip() != "" and "-->" not in lines[i]:
            raw_text_line = lines[i].strip()
            text_line = re.sub(r'\{[^}]*\\[^}]*\}', '', raw_text_line)
            text_line = text_line.replace(r'\N', '\n').replace(r'\n', '\n').replace(r'\h', ' ')
            text_line = re.sub(r'<br\s*/?>', '\n', text_line, flags=re.I)
            text_line = re.sub(r'<(?!/?(?:b|i|u)>)[^>]*>', '', text_line, flags=re.I)
            if not is_stylized_karaoke_subtitle(raw_text_line) and not is_non_subtitle_text(text_line):
                cue_text.append(text_line)
            i += 1
        if start is not None and end is not None and 0 <= start < end:
            cues.append({"timestamp": f"{start_raw.replace(',', '.')} --> {end_raw.replace(',', '.')}", "start": start, "end": end, "text": cue_text})

    video_end = max((cue["end"] or 0 for cue in cues), default=0)
    occurrences = {}
    for cue in cues:
        for text_line in cue["text"]:
            key = _subtitle_text_key(text_line)
            if key:
                occurrences.setdefault(key, []).append(cue)

    watermark_keys = set()
    for key, matching_cues in occurrences.items():
        durations = sum(
            max(0, cue["end"] - cue["start"])
            for cue in matching_cues
            if cue["start"] is not None and cue["end"] is not None
        )
        first_start = min((cue["start"] for cue in matching_cues if cue["start"] is not None), default=0)
        last_end = max((cue["end"] for cue in matching_cues if cue["end"] is not None), default=0)
        spans_episode = video_end >= 60 and first_start <= 15 and last_end >= video_end * .9
        if video_end >= 60 and durations >= video_end * .8:
            watermark_keys.add(key)
        elif len(matching_cues) >= 8 and spans_episode:
            watermark_keys.add(key)

    cleaned_lines = ["WEBVTT\n"]
    seen_cues = set()
    for cue in sorted(cues, key=lambda item: (item['start'], item['end'])):
        cue_text = [line for line in cue["text"] if _subtitle_text_key(line) not in watermark_keys]
        cue_key = (cue['start'], cue['end'], tuple(_subtitle_text_key(line) for line in cue_text))
        if cue_text and cue_key not in seen_cues:
            seen_cues.add(cue_key)
            cleaned_lines.append("\n" + cue["timestamp"] + "\n")
            cleaned_lines.append("\n".join(cue_text) + "\n")

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)
    return len(seen_cues)


def find_external_subtitle(video_path):
    """Only accept same-episode sidecars, never another episode's subtitle."""
    folder = os.path.dirname(os.path.abspath(video_path))
    stem = os.path.splitext(os.path.basename(video_path))[0].casefold()
    candidates = []
    try:
        filenames = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in filenames:
        base, ext = os.path.splitext(name)
        if ext.casefold() not in {'.ass', '.ssa', '.srt', '.vtt'}:
            continue
        base = base.casefold()
        if base != stem and not base.startswith(stem + '.'):
            continue
        suffix = base[len(stem):].strip('.')
        tokens = set(suffix.split('.')) if suffix else set()
        if tokens - {'id', 'ind', 'indonesian', 'en', 'eng', 'english', 'default', 'forced', 'sdh', 'full'}:
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or not is_resolved_path_inside(os.path.realpath(folder), os.path.realpath(path)):
            continue
        rank = (bool('forced' in tokens), 0 if tokens & {'id', 'ind', 'indonesian'} else 1 if not tokens else 2, name)
        candidates.append((rank, path))
    return min(candidates)[1] if candidates else None


def find_all_external_subtitles(video_path):
    """List all valid same-episode sidecar subtitles."""
    folder = os.path.dirname(os.path.abspath(video_path))
    stem = os.path.splitext(os.path.basename(video_path))[0].casefold()
    candidates = []
    try:
        filenames = sorted(os.listdir(folder))
    except OSError:
        return []
    for name in filenames:
        base, ext = os.path.splitext(name)
        ext_lower = ext.casefold()
        if ext_lower not in {'.ass', '.ssa', '.srt', '.vtt'}:
            continue
        base_clean = base.casefold()
        if base_clean != stem and not base_clean.startswith(stem + '.'):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or not is_resolved_path_inside(os.path.realpath(folder), os.path.realpath(path)):
            continue
        suffix = base_clean[len(stem):].strip('.')
        tokens = set(suffix.split('.')) if suffix else set()
        if tokens - {'id', 'ind', 'indonesian', 'en', 'eng', 'english', 'default', 'forced', 'sdh', 'full'}:
            continue

        lang = 'und'
        if tokens & {'id', 'ind', 'indonesian'}:
            lang = 'id'
        elif tokens & {'en', 'eng', 'english'}:
            lang = 'en'

        candidates.append({
            'path': path,
            'name': name,
            'format': ext_lower.lstrip('.'),
            'tokens': tokens,
            'language': lang,
        })
    return candidates


def select_subtitle_stream(video_path, streams=None, details=False):
    """Prefer full Indonesian dialogue, then default text tracks, avoiding bitmap tracks."""
    if streams is None:
        result = run_hidden_subprocess(
            [get_media_tool_path('ffprobe'), '-v', 'error', '-select_streams', 's',
             '-show_streams', '-of', 'json', video_path],
            capture_output=True, text=True, timeout=_constants.MEDIA_PROBE_TIMEOUT_SECONDS)
        streams = json.loads(result.stdout).get('streams', [])
    supported = {'ass', 'ssa', 'subrip', 'webvtt', 'mov_text', 'text'}
    def rank(stream):
        tags = stream.get('tags') or {}
        disposition = stream.get('disposition') or {}
        title = str(tags.get('title', '')).casefold()
        partial = bool(disposition.get('forced') or re.search(r'\b(signs?|songs?|karaoke)\b', title))
        language = str(tags.get('language', '')).casefold()
        return (partial, language not in {'id', 'ind', 'indonesian'}, not disposition.get('default'), language not in {'en', 'eng'}, int(stream['index']))
    candidates = [s for s in streams if s.get('codec_name') in supported and isinstance(s.get('index'), int)]
    selected = min(candidates, key=rank) if candidates else None
    if details or selected is None:
        return selected
    return f"0:{selected['index']}"


def get_available_subtitle_tracks(video_path):
    """Enumerate external sidecars and supported embedded subtitle streams."""
    tracks = []

    # 1. External sidecars
    sidecars = find_all_external_subtitles(video_path)
    for sc in sidecars:
        name = sc['name']
        fmt = sc['format']
        lang = sc['language']
        tokens = sc['tokens']
        lang_name = 'Indonesian' if lang == 'id' else ('English' if lang == 'en' else 'External')
        title = f"{lang_name} (External {fmt.upper()})"
        if 'forced' in tokens:
            title += " [Forced]"

        tracks.append({
            'id': f"sidecar:{name}",
            'title': title,
            'language': lang,
            'format': fmt,
            'is_external': True,
            'is_default': False,
        })

    # 2. Embedded streams in video
    supported = {'ass', 'ssa', 'subrip', 'webvtt', 'mov_text', 'text'}
    streams = []
    try:
        result = run_hidden_subprocess(
            [get_media_tool_path('ffprobe'), '-v', 'error', '-select_streams', 's',
             '-show_streams', '-of', 'json', video_path],
            capture_output=True, text=True, timeout=_constants.MEDIA_PROBE_TIMEOUT_SECONDS)
        if result.returncode == 0:
            streams = json.loads(result.stdout).get('streams', [])
    except (OSError, ValueError, subprocess.SubprocessError):
        streams = []

    for s in streams:
        codec = str(s.get('codec_name', '')).lower()
        if codec not in supported:
            continue
        idx = s.get('index')
        if not isinstance(idx, int):
            continue

        tags = s.get('tags') or {}
        disposition = s.get('disposition') or {}
        lang_raw = str(tags.get('language', '')).lower().strip()
        raw_title = str(tags.get('title', '')).strip()

        if lang_raw in {'id', 'ind', 'in', 'indonesian'}:
            lang = 'id'
            lang_label = 'Indonesian'
        elif lang_raw in {'en', 'eng', 'english'}:
            lang = 'en'
            lang_label = 'English'
        elif lang_raw in {'ja', 'jpn', 'japanese', 'jp'}:
            lang = 'ja'
            lang_label = 'Japanese'
        else:
            lang = lang_raw if lang_raw else 'und'
            lang_label = lang_raw.upper() if lang_raw and lang_raw != 'und' else ''

        if raw_title:
            if lang_label and lang_label.lower() not in raw_title.lower():
                display_title = f"{lang_label} - {raw_title}"
            else:
                display_title = raw_title
        elif lang_label:
            display_title = lang_label
            if disposition.get('forced'):
                display_title += " (Forced)"
        else:
            display_title = f"Track {idx} ({codec.upper()})"

        tracks.append({
            'id': f"0:{idx}",
            'title': display_title,
            'language': lang,
            'format': codec,
            'is_external': False,
            'is_default': False,
        })

    # 3. Default track matching
    external_default = find_external_subtitle(video_path)
    default_track_id = None
    if external_default:
        default_track_id = f"sidecar:{os.path.basename(external_default)}"
    else:
        selected = select_subtitle_stream(video_path, streams=streams, details=True)
        if selected and isinstance(selected.get('index'), int):
            default_track_id = f"0:{selected['index']}"

    if not default_track_id and tracks:
        default_track_id = tracks[0]['id']

    for track in tracks:
        if track['id'] == default_track_id:
            track['is_default'] = True

    return {
        'tracks': tracks,
        'default_track_id': default_track_id
    }


def get_ass_subtitle_assets(video_path, cache_root, track_id=None):
    """Keep original typesetting and embedded fonts; never pass ASS through VTT cleanup."""
    external = None
    if track_id:
        if track_id.startswith('sidecar:'):
            sidecar_name = track_id[len('sidecar:'):]
            folder_dir = os.path.dirname(os.path.abspath(video_path))
            candidate = os.path.join(folder_dir, sidecar_name)
            if not os.path.isfile(candidate) or not is_resolved_path_inside(os.path.realpath(folder_dir), os.path.realpath(candidate)):
                raise RuntimeError(f'External subtitle {sidecar_name} not found.')
            external = candidate
    else:
        external = find_external_subtitle(video_path)

    identity = [(os.path.realpath(path), media_source_fingerprint(path))
                for path in (video_path, external) if path]
    if track_id:
        identity.append(('track', track_id))
    cache_key = hashlib.sha256(json.dumps(identity).encode('utf-8')).hexdigest()

    if track_id:
        clean_track = re.sub(r'[^a-zA-Z0-9_-]', '_', track_id)
        folder = os.path.join(cache_root, 'ass-v1', f"{cache_key}_track_{clean_track}")
    else:
        folder = os.path.join(cache_root, 'ass-v1', cache_key)
    manifest_path = os.path.join(folder, 'manifest.json')

    def cached_manifest():
        try:
            with open(manifest_path, encoding='utf-8') as handle:
                manifest = json.load(handle)
            names = ([manifest['subtitle']] + manifest['fonts']) if manifest['renderer'] == 'ass' else []
            if all(is_valid_cache_file(os.path.join(folder, name)) for name in names):
                return manifest
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    cached = cached_manifest()
    if cached is not None:
        return folder, cached
    lock_key = ('subtitle-ass', cache_key, track_id or 'default')
    lock = acquire_ffmpeg_media_lock(lock_key)
    if lock is None:
        raise RuntimeError('Subtitle extraction is busy; try again shortly.')
    temp_manifest = ''
    try:
        cached = cached_manifest()
        if cached is not None:
            return folder, cached
        streams = []
        if not external or os.path.splitext(external)[1].lower() in {'.ass', '.ssa'}:
            result = run_hidden_subprocess(
                [get_media_tool_path('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', video_path],
                capture_output=True, text=True, timeout=_constants.MEDIA_PROBE_TIMEOUT_SECONDS)
            if result.returncode:
                raise RuntimeError('Unable to inspect subtitle tracks.')
            streams = json.loads(result.stdout).get('streams', [])

        if track_id and not track_id.startswith('sidecar:'):
            target_idx = int(track_id.split(':')[-1])
            selected = next((s for s in streams if s.get('index') == target_idx), None)
            if not selected:
                raise RuntimeError(f'Subtitle track {track_id} not found.')
            is_ass = bool(selected.get('codec_name') in {'ass', 'ssa'})
            stream_map = f"0:{selected['index']}"
        elif external:
            selected = None
            is_ass = bool(os.path.splitext(external)[1].lower() in {'.ass', '.ssa'})
            stream_map = '0:s:0'
        else:
            selected = select_subtitle_stream(video_path, streams=streams, details=True)
            is_ass = bool(selected and selected.get('codec_name') in {'ass', 'ssa'})
            stream_map = f"0:{selected['index']}" if selected else '0:s:0'

        manifest = {'renderer': 'vtt', 'fonts': []}
        os.makedirs(folder, exist_ok=True)
        if is_ass:
            ass_path = os.path.join(folder, 'track.ass')
            source = external or video_path
            result = run_ffmpeg_command(
                ['ffmpeg', '-y', '-i', source, '-map', stream_map, '-c:s', 'copy', '-f', 'ass', ass_path],
                timeout=_constants.SUBTITLE_GENERATION_TIMEOUT_SECONDS)
            if not result['ok'] or not is_valid_cache_file(ass_path):
                raise RuntimeError('Unable to extract the original ASS subtitle.')
            manifest = {'renderer': 'ass', 'subtitle': 'track.ass', 'fonts': []}
            font_args = []
            font_names = []
            total_font_bytes = 0
            for stream in streams:
                tags = stream.get('tags') or {}
                ext = os.path.splitext(str(tags.get('filename', '')))[1].lower()
                if stream.get('codec_type') != 'attachment' or ext not in {'.ttf', '.otf', '.ttc', '.woff', '.woff2'}:
                    continue
                size = int(stream.get('extradata_size') or 0)
                if len(font_names) >= 32 or size > 16 * 1024 * 1024 or total_font_bytes + size > 64 * 1024 * 1024:
                    continue
                index = int(stream['index'])
                name = f'font-{index}{ext}'
                font_names.append(name)
                total_font_bytes += size
                font_args.extend([f'-dump_attachment:{index}', os.path.join(folder, name)])
            if font_args:
                result = run_ffmpeg_command(
                    ['ffmpeg', '-y', *font_args, '-i', video_path, '-map', '0:v:0?',
                     '-t', '0', '-an', '-sn', '-f', 'null', '-'],
                    timeout=_constants.SUBTITLE_GENERATION_TIMEOUT_SECONDS)
                for name in font_names:
                    path = os.path.join(folder, name)
                    if is_valid_cache_file(path) and os.path.getsize(path) <= 16 * 1024 * 1024:
                        manifest['fonts'].append(name)
        temp_manifest = temporary_media_cache_path(manifest_path, '.json')
        with open(temp_manifest, 'w', encoding='utf-8') as handle:
            json.dump(manifest, handle)
        os.replace(temp_manifest, manifest_path)
        return folder, manifest
    finally:
        remove_file_quietly(temp_manifest)
        release_ffmpeg_media_lock(lock_key, lock)


def subtitle_cache_is_current(video_path, vtt_path, track_id=None):
    if not is_valid_cache_file(vtt_path):
        return False
    try:
        if track_id and track_id.startswith('sidecar:'):
            sidecar_name = track_id[len('sidecar:'):]
            folder = os.path.dirname(os.path.abspath(video_path))
            external = os.path.join(folder, sidecar_name)
            if not os.path.isfile(external):
                return False
            sources = (video_path, external)
        elif track_id:
            sources = (video_path,)
        else:
            external = find_external_subtitle(video_path)
            sources = (video_path, external)
        newest = max(os.stat(path).st_mtime_ns for path in sources if path)
        return os.stat(vtt_path).st_mtime_ns >= newest
    except OSError:
        return False


def generate_subtitle_vtt_result(video_path, vtt_path, track_id=None):
    if subtitle_cache_is_current(video_path, vtt_path, track_id=track_id):
        return make_media_generation_result(
            True,
            vtt_path,
            "cached",
            "Subtitle cache found."
        )

    fingerprint = media_source_fingerprint(video_path)
    if fingerprint is None:
        return make_media_generation_result(
            False,
            "",
            "source_not_found",
            "Media source was not found."
        )

    failure_key = ("subtitle", vtt_path, fingerprint)
    if track_id:
        failure_key += (track_id,)
    external = None
    if track_id and track_id.startswith('sidecar:'):
        sidecar_name = track_id[len('sidecar:'):]
        folder = os.path.dirname(os.path.abspath(video_path))
        candidate = os.path.join(folder, sidecar_name)
        if os.path.isfile(candidate) and is_resolved_path_inside(os.path.realpath(folder), os.path.realpath(candidate)):
            external = candidate
    elif not track_id:
        external = find_external_subtitle(video_path)

    if external:
        failure_key += (external, media_source_fingerprint(external))
    cached_failure = get_ffmpeg_failure_cache(failure_key)
    if cached_failure:
        return cached_failure

    lock = acquire_ffmpeg_media_lock(("subtitle", vtt_path))
    if lock is None:
        return make_media_generation_result(
            False,
            "",
            "busy",
            "Subtitle generation is already running."
        )

    temp_path = ""
    last_result = None

    try:
        if subtitle_cache_is_current(video_path, vtt_path, track_id=track_id):
            return make_media_generation_result(
                True,
                vtt_path,
                "cached",
                "Subtitle cache found."
            )

        cached_failure = get_ffmpeg_failure_cache(failure_key)
        if cached_failure:
            return cached_failure

        os.makedirs(os.path.dirname(vtt_path), exist_ok=True)
        temp_path = temporary_media_cache_path(vtt_path, ".vtt")
        app_log(f"Starting subtitle generation for {os.path.basename(video_path)}" + (f" (track {track_id})" if track_id else ""), "INFO")

        if track_id:
            if track_id.startswith('sidecar:'):
                if not external:
                    return make_media_generation_result(False, '', 'not_found', 'Sidecar subtitle file not found.')
                subtitle_source = external
                stream_map = '0:s:0'
            else:
                subtitle_source = video_path
                idx = track_id.split(':')[-1]
                stream_map = f"0:{idx}"
        else:
            subtitle_source = external or video_path
            stream_map = '0:s:0'
            if subtitle_source == video_path:
                try:
                    stream_map = select_subtitle_stream(video_path)
                except (OSError, ValueError, subprocess.SubprocessError):
                    stream_map = '0:s:0'
                if stream_map is None:
                    return make_media_generation_result(False, '', 'not_found', 'No supported text subtitle found. Bitmap subtitles require an external player.')

        last_result = run_ffmpeg_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                subtitle_source,
                "-map",
                stream_map,
                '-c:s', 'webvtt',
                temp_path
            ],
            timeout=_constants.SUBTITLE_GENERATION_TIMEOUT_SECONDS
        )

        if last_result["ok"] and is_valid_cache_file(temp_path):
            cue_count = clean_generated_subtitle_vtt(temp_path)
            if cue_count and is_valid_cache_file(temp_path):
                os.replace(temp_path, vtt_path)
                clear_ffmpeg_failure_cache(failure_key)
                return make_media_generation_result(
                    True,
                    vtt_path,
                    "generated",
                    "Subtitle generated."
                )

            last_result = make_media_generation_result(
                False,
                "",
                "failed",
                "Subtitle output was empty."
            )

        remove_file_quietly(temp_path)
        if last_result:
            app_log(f"Subtitle generation failed for {os.path.basename(video_path)}: {last_result['message']}", "WARN")
        else:
            app_log(f"Subtitle generation failed for {os.path.basename(video_path)}", "WARN")

    except Exception as e:
        app_log(f"Error while creating subtitle: {e}", "ERROR")
        last_result = make_media_generation_result(
            False,
            "",
            "error",
            "Subtitle generation failed."
        )
    finally:
        remove_file_quietly(temp_path)
        release_ffmpeg_media_lock(("subtitle", vtt_path), lock)

    last_result = last_result or make_media_generation_result(
        False,
        "",
        "failed",
        "Subtitle was not generated."
    )
    if last_result["status"] != "busy":
        set_ffmpeg_failure_cache(failure_key, last_result)
    return last_result


def generate_subtitle_vtt(video_path, vtt_path, track_id=None):
    return generate_subtitle_vtt_result(video_path, vtt_path, track_id=track_id).get("ok", False)
