# anibase/utils.py
"""Fungsi pembantu murni (pure utility functions) untuk subprocess,
jalur berkas, normalisasi, penanganan JSON atomik, dan migrasi direktori."""

import os
import sys
import subprocess
import shutil
import json
import re
import socket
import ipaddress
import threading
import functools
from datetime import datetime
from urllib.parse import urlparse

import anibase.constants as constants
from anibase.logging import app_log, debug_log

def hidden_subprocess_kwargs():
    if os.name != "nt":
        return {}

    kwargs = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window

    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo

    return kwargs

def with_hidden_subprocess_window(kwargs):
    merged = dict(kwargs)
    low_priority = merged.pop("low_priority", False)
    for key, value in hidden_subprocess_kwargs().items():
        if key == "creationflags":
            merged[key] = merged.get(key, 0) | value
        else:
            merged.setdefault(key, value)
    if os.name == "nt" and low_priority:
        # IDLE_PRIORITY_CLASS (0x00000040) ensures background tasks (FFmpeg thumbnails/previews)
        # only run when no other process/thread needs CPU or disk I/O, preventing media stream lag.
        merged["creationflags"] = merged.get("creationflags", 0) | 0x00000040
    elif os.name != "nt":
        # When running frozen under PyInstaller on Linux, LD_LIBRARY_PATH contains _internal.
        # This breaks external binaries like system ffmpeg/ffprobe/vlc due to library version mismatch.
        # Restore the original LD_LIBRARY_PATH from LD_LIBRARY_PATH_ORIG or strip out _internal paths.
        env = dict(merged.get("env") or os.environ)
        if "LD_LIBRARY_PATH_ORIG" in os.environ:
            orig = os.environ["LD_LIBRARY_PATH_ORIG"]
            if orig:
                env["LD_LIBRARY_PATH"] = orig
            else:
                env.pop("LD_LIBRARY_PATH", None)
        elif "LD_LIBRARY_PATH" in env:
            cleaned = [p for p in env["LD_LIBRARY_PATH"].split(":") if p and "_internal" not in p]
            if cleaned:
                env["LD_LIBRARY_PATH"] = ":".join(cleaned)
            else:
                env.pop("LD_LIBRARY_PATH", None)
        merged["env"] = env
    return merged

def run_hidden_subprocess(args, **kwargs):
    return subprocess.run(args, **with_hidden_subprocess_window(kwargs))

def popen_hidden_subprocess(args, **kwargs):
    return subprocess.Popen(args, **with_hidden_subprocess_window(kwargs))

def get_application_dir():
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    pkg_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.dirname(pkg_dir))

def get_resource_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.abspath(sys._MEIPASS)
    return get_application_dir()

def get_media_tool_path(name):
    executable = f"{name}.exe" if os.name == "nt" else name
    bundled_path = os.path.join(get_application_dir(), "tools", executable)
    if getattr(sys, "frozen", False) and os.path.isfile(bundled_path):
        return bundled_path
    return shutil.which(name) or name

def get_local_app_data_dir(app_data_dir_name):
    if os.name != "nt":
        xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
        if not xdg_data_home:
            xdg_data_home = os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.abspath(os.path.join(xdg_data_home, app_data_dir_name))

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return os.path.abspath(os.path.join(local_app_data, app_data_dir_name))

    return os.path.abspath(
        os.path.join(os.path.expanduser("~"), "AppData", "Local", app_data_dir_name)
    )

def get_user_data_dir():
    enabled_values = {"1", "true", "yes", "on"}
    use_project_runtime = any(
        os.environ.get(env_name, "").strip().lower() in enabled_values
        for env_name in constants.PROJECT_RUNTIME_ENV_NAMES
    )
    if use_project_runtime and not getattr(sys, "frozen", False):
        return get_application_dir()

    return get_local_app_data_dir(constants.APP_DATA_DIR_NAME)

def get_protected_cache_files():
    return {
        os.path.abspath(constants.DB_PATH),
        os.path.abspath(constants.SETTINGS_FILE),
        os.path.abspath(constants.WATCH_HISTORY_FILE),
        os.path.abspath(constants.WATCH_STATUS_FILE),
        os.path.abspath(f"{constants.WATCH_HISTORY_FILE}.bak"),
        os.path.abspath(f"{constants.WATCH_STATUS_FILE}.bak"),
        os.path.abspath(constants.MAL_AUTH_FILE),
        os.path.abspath(constants.MAL_SCROBBLE_CACHE),
    }

def get_active_cache_dir():
    return os.path.abspath(os.path.dirname(constants.DB_PATH) or constants.CACHE_DIR)

def get_runtime_directories():
    return [
        constants.USER_DATA_DIR,
        constants.CACHE_DIR,
        constants.LOG_DIR,
        constants.RUNTIME_DIR,
        constants.TEMP_DIR,
        constants.POSTER_CACHE,
        constants.BANNER_CACHE,
        constants.THUMBNAIL_CACHE,
        constants.METADATA_CACHE,
        constants.CHARACTER_CACHE,
        constants.EPISODE_CACHE,
        constants.SUBTITLE_CACHE,
        constants.SEIYUU_CACHE,
    ]

def ensure_runtime_directories():
    for directory in get_runtime_directories():
        os.makedirs(directory, exist_ok=True)

def copy_file_if_missing(source, destination, label):
    if not os.path.isfile(source):
        return None
    if os.path.exists(destination):
        return None

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(source, destination)
    return f"Migration copied {label}."

def copy_tree_missing(source_dir, destination_dir, label):
    if not os.path.isdir(source_dir):
        return None

    copied = 0
    skipped = 0
    for root, _dirs, files in os.walk(source_dir):
        relative_root = os.path.relpath(root, source_dir)
        target_root = (
            destination_dir
            if relative_root == "."
            else os.path.join(destination_dir, relative_root)
        )
        os.makedirs(target_root, exist_ok=True)

        for filename in files:
            source = os.path.join(root, filename)
            destination = os.path.join(target_root, filename)
            if os.path.exists(destination):
                skipped += 1
                continue
            shutil.copy2(source, destination)
            copied += 1

    if copied:
        return f"Migration processed {label}: copied={copied}; skipped_existing={skipped}."

    return None

def migrate_legacy_user_data_dirs():
    if os.path.abspath(constants.USER_DATA_DIR) == os.path.abspath(constants.APP_DIR):
        return []

    messages = []
    for legacy_name in constants.LEGACY_APP_DATA_DIR_NAMES:
        legacy_dir = get_local_app_data_dir(legacy_name)
        if os.path.abspath(legacy_dir) == os.path.abspath(constants.USER_DATA_DIR):
            continue

        message = copy_tree_missing(
            legacy_dir,
            constants.USER_DATA_DIR,
            f"legacy LocalAppData/{legacy_name}"
        )
        if message:
            messages.append(message)

    return messages

def migrate_legacy_runtime_data():
    legacy_cache_dir = os.path.join(constants.APP_DIR, "cache")
    legacy_log_dir = os.path.join(constants.APP_DIR, "logs")
    messages = []

    file_migrations = (
        (os.path.join(legacy_cache_dir, "settings.json"), constants.SETTINGS_FILE, "settings.json"),
        (os.path.join(legacy_cache_dir, "library.db"), constants.DB_PATH, "library.db"),
        (os.path.join(legacy_cache_dir, "library.db-wal"), f"{constants.DB_PATH}-wal", "library.db-wal"),
        (os.path.join(legacy_cache_dir, "library.db-shm"), f"{constants.DB_PATH}-shm", "library.db-shm"),
        (os.path.join(legacy_cache_dir, "watch_history.json"), constants.WATCH_HISTORY_FILE, "watch_history.json"),
        (os.path.join(legacy_cache_dir, "watch_history.json.bak"), f"{constants.WATCH_HISTORY_FILE}.bak", "watch_history.json.bak"),
        (os.path.join(legacy_cache_dir, "watch_status.json"), constants.WATCH_STATUS_FILE, "watch_status.json"),
        (os.path.join(legacy_cache_dir, "watch_status.json.bak"), f"{constants.WATCH_STATUS_FILE}.bak", "watch_status.json.bak"),
    )

    for source, destination, label in file_migrations:
        message = copy_file_if_missing(source, destination, label)
        if message:
            messages.append(message)

    cache_migrations = (
        ("posters", constants.POSTER_CACHE),
        ("banners", constants.BANNER_CACHE),
        ("metadata", constants.METADATA_CACHE),
        ("characters", constants.CHARACTER_CACHE),
        ("thumbnails", constants.THUMBNAIL_CACHE),
        ("subtitles", constants.SUBTITLE_CACHE),
        ("episodes", constants.EPISODE_CACHE),
        ("seiyuu", constants.SEIYUU_CACHE),
    )
    for dirname, destination in cache_migrations:
        message = copy_tree_missing(
            os.path.join(legacy_cache_dir, dirname),
            destination,
            f"cache/{dirname}"
        )
        if message:
            messages.append(message)

    message = copy_tree_missing(legacy_log_dir, constants.LOG_DIR, "logs")
    if message:
        messages.append(message)

    return messages

def request_shutdown():
    constants.SHUTDOWN_EVENT.set()

def is_shutdown_requested():
    return constants.SHUTDOWN_EVENT.is_set()

def wait_for_shutdown(timeout):
    return constants.SHUTDOWN_EVENT.wait(max(0, timeout or 0))

def normalize_bool_setting(value, default=False):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False

    if value is None:
        return default

    return default

def normalize_int_setting(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, number))

def normalize_screenshot_filename_prefix(value, default="vlcsnap"):
    prefix = str(value or "").strip()
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", prefix)
    prefix = re.sub(r"\s+", " ", prefix).strip(" .")
    return prefix[:80] or default

def ensure_linux_path_mounted(path):
    """If a Linux path is on an unmounted removable/secondary partition under /run/media/ or /media/,
    attempt to auto-mount its backing device via udisksctl."""
    if os.name == "nt" or not path or os.path.isdir(path):
        return

    try:
        from pathlib import Path
        parts = Path(path).parts
        uuid_or_label = None
        if len(parts) >= 5 and parts[1] == "run" and parts[2] == "media":
            uuid_or_label = parts[4]
        elif len(parts) >= 4 and parts[1] == "media":
            uuid_or_label = parts[3]

        if not uuid_or_label:
            return

        for by_dir in ("/dev/disk/by-uuid", "/dev/disk/by-label"):
            dev_link = os.path.join(by_dir, uuid_or_label)
            if os.path.exists(dev_link):
                run_hidden_subprocess(
                    ["udisksctl", "mount", "-b", dev_link, "--no-user-interaction"],
                    capture_output=True,
                    timeout=5,
                )
                break
    except Exception:
        pass


def normalize_library_path(path):
    if not path:
        return ""

    path = str(path).strip()
    if not path:
        return ""

    normalized = os.path.abspath(os.path.expanduser(path))

    if normalized == constants.BASE_DIR:
        return ""

    ensure_linux_path_mounted(normalized)

    return normalized

def normalize_library_paths(paths):
    if isinstance(paths, str):
        raw_paths = [paths]
    elif isinstance(paths, (list, tuple)):
        raw_paths = paths
    else:
        raw_paths = []

    normalized_paths = []
    seen_paths = set()
    for path in raw_paths:
        normalized_path = normalize_library_path(path)
        if not normalized_path:
            continue

        path_key = os.path.normcase(normalized_path)
        if path_key in seen_paths:
            continue

        seen_paths.add(path_key)
        normalized_paths.append(normalized_path)

    return normalized_paths

def normalize_shortcuts(raw_shortcuts):
    if not isinstance(raw_shortcuts, dict):
        return constants.DEFAULT_PLAYER_SHORTCUTS.copy()
    normalized = constants.DEFAULT_PLAYER_SHORTCUTS.copy()
    for key, default_val in constants.DEFAULT_PLAYER_SHORTCUTS.items():
        val = raw_shortcuts.get(key)
        if isinstance(val, str) and val.strip():
            normalized[key] = val.strip()
    return normalized

def normalize_episode_path_key(episode_path):
    return str(episode_path or "").replace("\\", "/").strip("/")

def normalize_episode_display_name(value):
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(value or ""))
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120]

def safe_cache_name(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name or "")

def clean_movie_title(filename):
    title = os.path.splitext(filename)[0]
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"\b1080p\b", "", title, flags=re.I)
    title = re.sub(r"\bBD\b", "", title, flags=re.I)
    return " ".join(title.split())

@functools.lru_cache(maxsize=512)
def safe_join_media_path(base_path, relative_path):
    if not base_path:
        return None

    base_path = os.path.abspath(base_path)
    if os.name != "nt" and not os.path.isdir(base_path):
        ensure_linux_path_mounted(base_path)

    candidate_path = os.path.abspath(
        os.path.normpath(
            os.path.join(
                base_path,
                relative_path
            )
        )
    )

    try:
        if os.path.commonpath([base_path, candidate_path]) != base_path:
            return None
    except ValueError:
        return None

    return candidate_path

def is_valid_cache_file(path):
    try:
        return bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)
    except OSError:
        return False

def read_json_dict_file(path, label):
    if not os.path.exists(path):
        return None, "missing"

    try:
        if os.path.getsize(path) == 0:
            return None, "empty"

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    except OSError as e:
        return None, f"OS error: {e}"

    if not isinstance(data, dict):
        return None, "JSON root is not an object"

    return data, None

def atomic_write_json_file(path, data, label, ensure_ascii=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    tmp_path = os.path.join(
        os.path.dirname(path),
        f".{os.path.basename(path)}.tmp-{os.getpid()}-{threading.get_ident()}"
    )

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=ensure_ascii)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        debug_log(f"{label} saved atomically: {path}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def get_watch_backup_file(path):
    return f"{path}.bak"

def get_corrupt_watch_file(path):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    corrupt_path = f"{path}.corrupt-{timestamp}"
    index = 1

    while os.path.exists(corrupt_path):
        corrupt_path = f"{path}.corrupt-{timestamp}-{index}"
        index += 1

    return corrupt_path

def preserve_corrupt_watch_file(path, label, reason):
    if not os.path.exists(path):
        return

    corrupt_path = get_corrupt_watch_file(path)

    try:
        os.replace(path, corrupt_path)
        app_log(f"{label} preserved as corrupt file: {corrupt_path} ({reason})", "WARN")
    except OSError as e:
        app_log(f"{label} could not preserve corrupt file {path}: {e}", "ERROR")

def is_resolved_path_inside(base_path, candidate_path):
    if not base_path or not candidate_path:
        return False

    base_real = os.path.normcase(os.path.realpath(os.path.abspath(base_path)))
    candidate_real = os.path.normcase(os.path.realpath(os.path.abspath(candidate_path)))

    try:
        return os.path.commonpath([base_real, candidate_real]) == base_real
    except ValueError:
        return False

def load_json_dict_if_exists(path):
    data, error = read_json_dict_file(path, os.path.basename(path))
    if error is not None:
        return {}
    return data

def remove_file_quietly(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def get_local_lan_ip():
    """Mengembalikan alamat IP lokal perangkat di jaringan LAN."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def generate_qr_svg(data: str) -> str:
    """Menghasilkan representasi string SVG dari QR code untuk teks/URL data."""
    if not data:
        return ""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
        import io

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
            image_factory=SvgPathImage
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(attrib={"class": "lan-qr-svg", "xmlns": "http://www.w3.org/2000/svg"})
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception as e:
        app_log(f"Gagal membuat QR SVG: {e}", "DEBUG")
        return ""


_DISALLOWED_HOSTNAMES = {"localhost", "localhost.localdomain"}
_DISALLOWED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".arpa")
SAMEHADAKU_ALLOWED_HOSTS = ("samehadaku.how", "samehadaku.email")
LENDRIVE_DEFAULT_HOST = "lendrive.web.id"


def _normalize_hostname(hostname: str) -> str:
    return str(hostname or "").strip().lower().rstrip(".").strip("[]")


def is_public_network_host(hostname: str) -> bool:
    """Reject loopback, link-local, multicast, reserved, or private hosts/IPs."""
    clean_host = _normalize_hostname(hostname)
    if not clean_host or any(ch.isspace() or ord(ch) < 32 for ch in clean_host):
        return False

    if clean_host in _DISALLOWED_HOSTNAMES or any(
        clean_host.endswith(suffix) for suffix in _DISALLOWED_HOST_SUFFIXES
    ):
        return False

    try:
        ip = ipaddress.ip_address(clean_host)
        return bool(ip.is_global)
    except ValueError:
        pass

    try:
        addr_info = socket.getaddrinfo(clean_host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return True
    except OSError:
        return False

    resolved_ips = []
    for entry in addr_info:
        sockaddr = entry[4]
        if not sockaddr:
            continue
        raw_ip = str(sockaddr[0]).split("%", 1)[0]
        try:
            resolved_ips.append(ipaddress.ip_address(raw_ip))
        except ValueError:
            continue

    return bool(resolved_ips) and all(ip.is_global for ip in resolved_ips)


def host_matches_allowlist(hostname: str, allowed_hosts) -> bool:
    """Return True if hostname equals or is a subdomain of any allowed host."""
    clean_host = _normalize_hostname(hostname)
    if not clean_host:
        return False
    for entry in allowed_hosts or ():
        allowed = _normalize_hostname(entry)
        if allowed and (clean_host == allowed or clean_host.endswith(f".{allowed}")):
            return True
    return False


def validate_external_provider_url(url: str, allowed_hosts=None):
    """Validate an external HTTP(S) provider URL against SSRF and optional host allowlists."""
    clean_url = str(url or "").strip()
    if not clean_url or any(ord(ch) < 32 for ch in clean_url):
        return False, "", "URL kosong atau mengandung karakter tidak valid."

    try:
        parsed = urlparse(clean_url)
    except ValueError:
        return False, "", "Format URL tidak valid."

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False, "", "URL harus menggunakan protokol http:// atau https:// yang valid."

    if parsed.username is not None or parsed.password is not None:
        return False, "", "URL dengan kredensial tersemat tidak diizinkan."

    hostname = _normalize_hostname(parsed.hostname or "")
    if not is_public_network_host(hostname):
        return False, "", "Host jaringan lokal/privat tidak diizinkan."

    if allowed_hosts is not None and not host_matches_allowlist(hostname, allowed_hosts):
        return False, "", "Domain URL tidak termasuk dalam daftar sumber yang diizinkan."

    return True, clean_url, ""


def get_samehadaku_allowed_hosts():
    """Return allowed host domains for Samehadaku scraping."""
    return SAMEHADAKU_ALLOWED_HOSTS


def get_lendrive_allowed_hosts(settings=None):
    """Return allowed host domains for Lendrive, including user-configured mirror."""
    hosts = [LENDRIVE_DEFAULT_HOST]
    try:
        cfg = settings if isinstance(settings, dict) else None
        if cfg is None:
            from anibase.settings import load_settings
            cfg = load_settings()
        custom_url = str((cfg or {}).get("lendrive_base_url") or "").strip()
        if custom_url:
            parsed = urlparse(custom_url)
            if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
                custom_host = _normalize_hostname(parsed.hostname)
                if custom_host and custom_host not in hosts and is_public_network_host(custom_host):
                    hosts.append(custom_host)
    except Exception:
        pass
    return tuple(hosts)


def is_lendrive_provider_url(url: str, settings=None) -> bool:
    """Return True if URL belongs to Lendrive default or configured custom mirror."""
    clean_url = str(url or "").strip()
    if not clean_url:
        return False
    try:
        hostname = urlparse(clean_url).hostname or ""
    except ValueError:
        return False
    return host_matches_allowlist(hostname, get_lendrive_allowed_hosts(settings))


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    """Build a single PNG chunk with CRC-32."""
    import struct
    import zlib

    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _ico_dib_to_png_bytes(entry_bytes: bytes, fallback_width: int, fallback_height: int) -> bytes | None:
    """Convert an uncompressed 32-bit BGRA ICO DIB entry to PNG bytes without external deps."""
    import struct
    import zlib

    if len(entry_bytes) < 40:
        return None
    header_size, raw_w, raw_h, _planes, bpp, compression = struct.unpack_from("<IiiHHI", entry_bytes, 0)
    if header_size < 40 or bpp != 32 or compression not in (0, 3):
        return None

    width = abs(raw_w) or fallback_width
    # ICO DIB height stores combined XOR + AND mask height (2x visual height)
    height = (abs(raw_h) // 2) if abs(raw_h) == fallback_height * 2 else (abs(raw_h) or fallback_height)
    if width <= 0 or height <= 0:
        return None

    pixel_offset = header_size + (12 if compression == 3 and header_size == 40 else 0)
    row_bytes = width * 4
    expected_end = pixel_offset + (row_bytes * height)
    if len(entry_bytes) < expected_end:
        return None

    bottom_up = raw_h > 0
    rows = []
    for y in range(height):
        src_y = (height - 1 - y) if bottom_up else y
        start = pixel_offset + src_y * row_bytes
        row = bytearray(entry_bytes[start:start + row_bytes])
        # BGRA -> RGBA in place
        row[0::4], row[2::4] = row[2::4], row[0::4]
        rows.append(b"\x00" + bytes(row))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"".join(rows), level=6)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def ico_to_png_bytes(ico_path) -> bytes | None:
    """Render the highest-resolution frame of an .ico file to PNG bytes."""
    import io
    import struct

    try:
        from PIL import Image

        with Image.open(ico_path) as im:
            sizes = getattr(im, "ico", None) and im.ico.sizes()
            if sizes:
                best = max(sizes, key=lambda s: s[0] * s[1])
                frame = im.ico.getimage(best).convert("RGBA")
            else:
                frame = im.convert("RGBA")
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        pass

    try:
        with open(ico_path, "rb") as handle:
            data = handle.read()
        if len(data) < 6:
            return None
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        if reserved != 0 or kind != 1 or count <= 0:
            return None

        best_entry = None
        best_area = -1
        for idx in range(count):
            pos = 6 + idx * 16
            if pos + 16 > len(data):
                break
            w, h, _colors, _res, _planes, _bpp, size, offset = struct.unpack_from("<BBBBHHII", data, pos)
            width = 256 if w == 0 else w
            height = 256 if h == 0 else h
            if offset + size > len(data):
                continue
            area = width * height
            if area > best_area:
                best_area = area
                best_entry = (width, height, data[offset:offset + size])

        if not best_entry:
            return None
        width, height, payload = best_entry
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return payload
        return _ico_dib_to_png_bytes(payload, width, height)
    except OSError:
        return None


def export_ico_to_png(ico_path, png_path) -> bool:
    """Export an .ico file to a .png file only when missing or content differs."""
    png_bytes = ico_to_png_bytes(ico_path)
    if not png_bytes:
        return False

    target = str(png_path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        if os.path.isfile(target):
            with open(target, "rb") as existing:
                if existing.read() == png_bytes:
                    return True
    except OSError:
        pass

    with open(target, "wb") as out:
        out.write(png_bytes)
    return True



