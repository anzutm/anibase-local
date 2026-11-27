"""
anibase.desktop.updater
Cross-platform update notifier and GitHub release checker for AniBase.
Specialized for Linux (freedesktop notifications, notify-send, KDE Plasma).
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from anibase.constants import APP_NAME, APP_VERSION
from anibase.utils import app_log, get_user_data_dir

GITHUB_REPO = "anzutm/anibase-local"
CHECK_INTERVAL_SECONDS = 4 * 3600  # 4 hours


def parse_version_tuple(version_str: str) -> Tuple[int, int, int]:
    """Parses a version string like 'v1.5.5' or '1.5.5-beta' into a comparable tuple of ints."""
    if not version_str or not isinstance(version_str, str):
        return (0, 0, 0)
    clean = version_str.strip().lstrip("vV")
    main_part = clean.split("-")[0]
    parts = []
    for chunk in main_part.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_version_newer(candidate_ver: str, current_ver: str = APP_VERSION) -> bool:
    """Returns True if candidate_ver is strictly newer than current_ver."""
    return parse_version_tuple(candidate_ver) > parse_version_tuple(current_ver)


def get_version_state_path() -> str:
    """Returns absolute path to version_state.json in user data directory."""
    return os.path.join(get_user_data_dir(), "version_state.json")


def load_version_state() -> Dict[str, Any]:
    """Loads version state file or returns defaults."""
    path = get_version_state_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            app_log(f"Failed to read version_state.json: {e}", "WARN")
    return {}


def save_version_state(state: Dict[str, Any]) -> bool:
    """Saves version state file safely."""
    path = get_version_state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(temp_path, path)
        return True
    except Exception as e:
        app_log(f"Failed to write version_state.json: {e}", "WARN")
        return False


def get_default_icon_path() -> str:
    """Finds best available application icon path for notifications."""
    try:
        from anibase.desktop.shell import _get_linux_icon_path
        icon = _get_linux_icon_path()
        if icon and os.path.isfile(icon):
            return icon
    except Exception:
        pass
    home = os.path.expanduser("~")
    std_icon = os.path.join(home, ".local", "share", "icons", "hicolor", "512x512", "apps", "anibase.png")
    if os.path.isfile(std_icon):
        return std_icon
    return "anibase"


_DESKTOP_NOTIFICATION_HANDLER_LOCK = threading.Lock()
_desktop_notification_handler = None


def register_desktop_notification_handler(handler):
    """Registers (or clears with None) an in-process UI notification handler (e.g. Qt tray)."""
    global _desktop_notification_handler
    with _DESKTOP_NOTIFICATION_HANDLER_LOCK:
        _desktop_notification_handler = handler


def send_desktop_notification(
    title: str,
    message: str,
    icon_path: Optional[str] = None,
    urgency: str = "normal",
    expire_time_ms: int = 7000,
) -> bool:
    """
    Sends a native desktop notification on Linux or via the registered tray handler on Windows.
    Non-blocking, silent on failure.
    """
    with _DESKTOP_NOTIFICATION_HANDLER_LOCK:
        tray_handler = _desktop_notification_handler

    if os.name == "nt" and callable(tray_handler):
        try:
            accepted = tray_handler(title, message, int(expire_time_ms))
            if accepted is not False:
                return True
        except Exception as e:
            app_log(f"Windows tray notification handler failed: {e}", "DEBUG")

    def _deliver():
        icon = icon_path or get_default_icon_path()
        if os.name != "nt":
            # 1. Try notify-send
            notify_send = shutil.which("notify-send")
            if notify_send:
                cmd = [
                    notify_send,
                    f"--app-name={APP_NAME}",
                    f"--urgency={urgency}",
                    f"--expire-time={expire_time_ms}",
                    "--hint=string:desktop-entry:anibase",
                ]
                if icon and (os.path.isfile(icon) or icon.isalnum()):
                    cmd.append(f"--icon={icon}")
                cmd.extend([title, message])
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=5.0,
                    )
                    return True
                except Exception as e:
                    app_log(f"notify-send failed: {e}", "DEBUG")

            # 2. Try DBus org.freedesktop.Notifications via busctl
            busctl = shutil.which("busctl")
            if busctl:
                try:
                    subprocess.run(
                        [
                            busctl,
                            "--user",
                            "call",
                            "org.freedesktop.Notifications",
                            "/org/freedesktop/Notifications",
                            "org.freedesktop.Notifications",
                            "Notify",
                            "susssasa{sv}i",
                            APP_NAME,
                            "0",
                            icon if (icon and os.path.isfile(icon)) else "anibase",
                            title,
                            message,
                            "0",
                            "0",
                            str(expire_time_ms),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=5.0,
                    )
                    return True
                except Exception as e:
                    app_log(f"busctl notification failed: {e}", "DEBUG")
        elif callable(tray_handler):
            try:
                if tray_handler(title, message, int(expire_time_ms)) is not False:
                    return True
            except Exception as e:
                app_log(f"Windows tray notification failed: {e}", "DEBUG")

        app_log(f"Notification: [{title}] {message}", "INFO")
        return False

    threading.Thread(target=_deliver, daemon=True, name="desktop-notif").start()
    return True


def check_post_update_notification(notify_on_upgrade: bool = True) -> Dict[str, Any]:
    """
    Checks if AniBase was upgraded since the last launch.
    If upgraded, fires a desktop notification confirming the new version.
    """
    state = load_version_state()
    last_version = state.get("last_run_version")
    current_version = APP_VERSION

    result = {
        "is_upgrade": False,
        "previous_version": last_version,
        "current_version": current_version,
        "notified": False,
    }

    if last_version is None:
        state["last_run_version"] = current_version
        state["first_installed_at"] = int(time.time())
        save_version_state(state)
        app_log(f"Version tracker initialized with AniBase v{current_version}", "INFO")
        if notify_on_upgrade and os.name != "nt":
            send_desktop_notification(
                f"{APP_NAME} v{current_version}",
                f"AniBase v{current_version} aktif dan siap digunakan di sistem Anda.",
            )
            result["notified"] = True
        return result

    if last_version != current_version:
        result["is_upgrade"] = True
        app_log(f"AniBase update detected: v{last_version} -> v{current_version}", "INFO")
        if notify_on_upgrade:
            send_desktop_notification(
                f"{APP_NAME} Telah Diperbarui",
                f"AniBase berhasil diperbarui ke versi v{current_version}.",
            )
            result["notified"] = True

        state["last_run_version"] = current_version
        state["last_updated_at"] = int(time.time())
        save_version_state(state)
        return result

    return result


def check_github_release(
    force: bool = False,
    repo: str = GITHUB_REPO,
    notify: bool = True,
) -> Dict[str, Any]:
    """
    Checks GitHub Releases API for a newer version.
    Returns release information and triggers desktop notification if a newer version is found.
    """
    state = load_version_state()
    now = time.time()
    last_check = float(state.get("last_check_timestamp", 0))

    if not force and (now - last_check < CHECK_INTERVAL_SECONDS):
        latest_known = state.get("latest_known_release", APP_VERSION)
        update_available = is_version_newer(latest_known, APP_VERSION)
        return {
            "ok": True,
            "cached": True,
            "current_version": APP_VERSION,
            "latest_version": latest_known,
            "update_available": update_available,
            "release_url": state.get("latest_release_url") or f"https://github.com/{repo}/releases",
            "release_notes": state.get("latest_release_notes", ""),
        }

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"AniBase/{APP_VERSION}",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                raw_data = resp.read().decode("utf-8")
                release = json.loads(raw_data)
                tag_name = release.get("tag_name", "").strip()
                html_url = release.get("html_url", f"https://github.com/{repo}/releases")
                body = release.get("body", "")
                name = release.get("name", tag_name)

                clean_ver = tag_name.lstrip("vV").strip()
                update_available = is_version_newer(clean_ver, APP_VERSION)

                state["last_check_timestamp"] = now
                state["latest_known_release"] = clean_ver
                state["latest_release_url"] = html_url
                state["latest_release_notes"] = body[:2000] if body else ""

                notified = False
                if update_available and notify:
                    last_notified = state.get("last_notified_release")
                    if last_notified != clean_ver:
                        send_desktop_notification(
                            f"Pembaruan AniBase Tersedia (v{clean_ver})",
                            f"Versi v{clean_ver} telah rilis di GitHub. Buka browser atau rilis untuk mengunduh.",
                        )
                        state["last_notified_release"] = clean_ver
                        notified = True

                save_version_state(state)
                return {
                    "ok": True,
                    "cached": False,
                    "current_version": APP_VERSION,
                    "latest_version": clean_ver,
                    "release_name": name,
                    "update_available": update_available,
                    "release_url": html_url,
                    "release_notes": body,
                    "notified": notified,
                }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        app_log(f"GitHub release check failed: {e}", "DEBUG")
        return {
            "ok": False,
            "error": str(e),
            "current_version": APP_VERSION,
            "update_available": False,
        }
    except Exception as e:
        app_log(f"Unexpected error in GitHub release check: {e}", "WARN")
        return {
            "ok": False,
            "error": str(e),
            "current_version": APP_VERSION,
            "update_available": False,
        }


def start_update_checker_service(initial_delay_sec: float = 3.0, poll_interval_sec: float = 21600.0):
    """
    Starts a background daemon thread that:
    1. Delivers post-update notification if the app was just upgraded.
    2. Checks GitHub releases for new updates and notifies if available.
    3. Runs periodically in the background.
    """
    def _worker():
        time.sleep(initial_delay_sec)
        try:
            check_post_update_notification()
        except Exception as e:
            app_log(f"Error in post-update notification check: {e}", "WARN")

        while True:
            try:
                check_github_release(force=False, notify=True)
            except Exception as e:
                app_log(f"Error checking GitHub release in background: {e}", "DEBUG")
            time.sleep(poll_interval_sec)

    thread = threading.Thread(target=_worker, name="update-checker-service", daemon=True)
    thread.start()
    return thread
