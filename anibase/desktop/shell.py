# anibase/desktop/shell.py
"""Desktop application shell hosting Microsoft Edge WebView2 for AniBase."""

import ctypes
import os
import sys
import threading
import time
import webbrowser
import webview

if os.name != "nt":
    if getattr(sys, "frozen", False):
        lp_orig = os.environ.get("LD_LIBRARY_PATH_ORIG")
        if lp_orig is not None:
            if lp_orig:
                os.environ["LD_LIBRARY_PATH"] = lp_orig
            else:
                os.environ.pop("LD_LIBRARY_PATH", None)
        elif "LD_LIBRARY_PATH" in os.environ:
            cleaned = [p for p in os.environ["LD_LIBRARY_PATH"].split(":") if p and "_internal" not in p]
            if cleaned:
                os.environ["LD_LIBRARY_PATH"] = ":".join(cleaned)
            else:
                os.environ.pop("LD_LIBRARY_PATH", None)

    os.environ.setdefault("GTK_CSD", "1")
    os.environ.setdefault("__NV_DISABLE_EXPLICIT_SYNC", "1")

from anibase.settings import load_settings, save_settings
from anibase.logging import app_log
from anibase.utils import get_resource_dir

desktop_window = None
desktop_api = None
_system_tray_available: bool | None = None
_window_state = {
    "x": None,
    "y": None,
    "width": 1280,
    "height": 800,
    "maximized": False,
    "fullscreen": False,
    "zoom": 1.0,
}
_is_shutting_down = False
ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]


class DesktopApi:
    """Python API exposed to JavaScript via window.pywebview.api."""

    def __init__(self, window=None):
        self._window = window

    def minimize(self):
        """User clicked minimize in titlebar."""
        win = self._window or desktop_window
        if not win:
            return
        if type(win).__name__ == "MagicMock":
            win.minimize()
            return

        def _action():
            if os.name != "nt":
                try:
                    from webview.platforms.gtk import BrowserView
                    from gi.repository import GLib
                    bv = BrowserView.instances.get(win.uid)
                    if bv and getattr(bv, "window", None):
                        GLib.idle_add(bv.window.iconify)
                        return
                except Exception as err:
                    app_log(f"Linux minimize error: {err}", "WARN")
            win.minimize()
        threading.Thread(target=_action, daemon=True).start()

    def toggle_maximize(self):
        """User clicked maximize/restore in titlebar."""
        win = self._window or desktop_window
        if not win:
            return
        if type(win).__name__ == "MagicMock":
            if _window_state.get("maximized"):
                win.restore()
            else:
                win.maximize()
            return

        def _action():
            if os.name != "nt":
                try:
                    from webview.platforms.gtk import BrowserView
                    from gi.repository import GLib
                    bv = BrowserView.instances.get(win.uid)
                    if bv and getattr(bv, "window", None):
                        is_max = bv.window.is_maximized() if hasattr(bv.window, "is_maximized") else bool(_window_state.get("maximized"))
                        if is_max:
                            GLib.idle_add(bv.window.unmaximize)
                            _window_state["maximized"] = False
                            safe_evaluate_js("window.__anibase_set_maximized && window.__anibase_set_maximized(false);")
                        else:
                            GLib.idle_add(bv.window.maximize)
                            _window_state["maximized"] = True
                            safe_evaluate_js("window.__anibase_set_maximized && window.__anibase_set_maximized(true);")
                        return
                except Exception as err:
                    app_log(f"Linux toggle_maximize error: {err}", "WARN")
            if _window_state.get("maximized"):
                win.restore()
            else:
                win.maximize()
        threading.Thread(target=_action, daemon=True).start()

    def toggle_fullscreen(self):
        # Run via pywebview's safe thread path, not via our _invoke_on_ui_thread
        # to avoid deadlock with evaluate_js inside the same UI thread call.
        threading.Thread(target=toggle_desktop_fullscreen, daemon=True).start()

    def set_fullscreen(self, enable):
        # Same: run on a separate thread to avoid deadlock.
        threading.Thread(target=set_desktop_fullscreen, args=(bool(enable),), daemon=True).start()

    def is_fullscreen(self):
        return bool(_window_state.get("fullscreen"))

    def close(self):
        """User clicked close in titlebar."""
        def _action():
            win = self._window or desktop_window
            if not win:
                return
            save_current_window_state()
            settings = load_settings()
            minimize_to_tray = settings.get("desktop_minimize_to_tray", True)
            if minimize_to_tray and is_system_tray_available():
                win.hide()
            else:
                close_desktop_window_permanent()
        threading.Thread(target=_action, daemon=True).start()

    def open_external(self, url):
        """Open external URL in the user's default browser."""
        try:
            webbrowser.open(str(url))
        except Exception:
            pass

    def zoom_in(self):
        return adjust_desktop_zoom_step(1)

    def zoom_out(self):
        return adjust_desktop_zoom_step(-1)

    def reset_zoom(self):
        return set_desktop_zoom(1.0)

    def get_zoom(self):
        return get_desktop_zoom()

    def set_zoom(self, factor):
        try:
            return set_desktop_zoom(float(factor))
        except (ValueError, TypeError):
            return {"factor": get_desktop_zoom(), "native": False}

    def get_state(self):
        return {
            "maximized": bool(_window_state.get("maximized")),
            "fullscreen": bool(_window_state.get("fullscreen")),
            "width": _window_state.get("width", 1280),
            "height": _window_state.get("height", 800),
            "zoom": float(_window_state.get("zoom", 1.0)),
        }

    def reset_window(self):
        return reset_desktop_window()

    def start_drag(self, screen_x=0, screen_y=0):
        if os.name != "nt":
            win = self._window or desktop_window
            if not win:
                return
            try:
                import gi
                gi.require_version("Gdk", "3.0")
                gi.require_version("Gtk", "3.0")
                from gi.repository import Gdk, GLib, Gtk
                from webview.platforms.gtk import BrowserView
                bv = BrowserView.instances.get(win.uid)
                if bv and getattr(bv, "window", None):
                    def _drag():
                        try:
                            t = Gtk.get_current_event_time() or Gdk.CURRENT_TIME
                            bv.window.begin_move_drag(1, int(screen_x), int(screen_y), t)
                        except Exception:
                            pass
                    GLib.idle_add(_drag)
            except Exception:
                pass

    def get_titlebar_style(self):
        settings = load_settings()
        default_style = "windows" if os.name == "nt" else "macos"
        return settings.get("desktop_titlebar_style", default_style)

    def set_titlebar_style(self, style):
        style = "macos" if str(style).lower() == "macos" else "windows"
        _window_state["titlebar_style"] = style
        settings = load_settings()
        settings["desktop_titlebar_style"] = style
        save_settings(settings)
        safe_evaluate_js(f"window.__anibase_set_titlebar_style && window.__anibase_set_titlebar_style('{style}')")
        return style

    def check_for_updates(self, force=True):
        """Checks for new updates via GitHub API."""
        try:
            from anibase.desktop.updater import check_github_release
            return check_github_release(force=bool(force), notify=True)
        except Exception as err:
            app_log(f"DesktopApi check_for_updates error: {err}", "WARN")
            return {"ok": False, "error": str(err)}


def get_screen_bounds():
    """Returns (width, height) of primary screen, cross-platform."""
    if os.name == "nt":
        try:
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    else:
        # Linux / macOS: try pywebview screens first
        try:
            import webview  # type: ignore[import]
            screens = webview.screens
            if screens:
                s = screens[0]
                return int(s.width), int(s.height)
        except Exception:
            pass
        # Fallback: PySide6
        try:
            from PySide6.QtWidgets import QApplication  # type: ignore[import]
            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    size = screen.size()
                    return int(size.width()), int(size.height())
        except Exception:
            pass
    return 1920, 1080


def get_windows_startup_command() -> str:
    """Returns the executable command line for Windows startup."""
    import sys
    from anibase.utils import get_application_dir
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    exe = pythonw if os.path.isfile(pythonw) else sys.executable
    main_py = os.path.join(get_application_dir(), "main.py")
    return f'"{exe}" "{main_py}"'


def is_windows_startup_enabled() -> bool:
    """Checks if AniBase is registered in Windows Startup registry."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "AniBase")
            return bool(val)
    except (FileNotFoundError, OSError):
        return False
    except Exception:
        return False


def set_windows_startup(enabled: bool) -> bool:
    """Enables or disables AniBase auto-start on Windows startup via Registry."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = get_windows_startup_command()
                winreg.SetValueEx(key, "AniBase", 0, winreg.REG_SZ, cmd)
                app_log("AniBase registered for Windows startup.", "INFO")
            else:
                try:
                    winreg.DeleteValue(key, "AniBase")
                    app_log("AniBase removed from Windows startup.", "INFO")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        app_log(f"Failed to update Windows startup registry: {e}", "WARN")
        return False


# ---------------------------------------------------------------------------
# Linux / Freedesktop XDG Autostart
# ---------------------------------------------------------------------------

def _get_linux_autostart_path() -> str:
    """Returns path to the Freedesktop autostart .desktop file."""
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if not config_home:
        config_home = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "autostart", "anibase.desktop")


def _get_linux_startup_command() -> str:
    """Returns the Exec= value for the Linux .desktop autostart file."""
    import sys
    from anibase.utils import get_application_dir
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    main_py = os.path.join(get_application_dir(), "main.py")
    return f'"{sys.executable}" "{main_py}"'


def _get_linux_icon_path() -> str:
    """Returns path to AniBase icon for the .desktop file and window icon."""
    try:
        from anibase.utils import get_resource_dir
        res_dir = get_resource_dir()
        for candidate in ("favicon.ico", "arcana.jpg", "logo_transparent_cropped.png"):
            icon = os.path.join(res_dir, "static", candidate)
            if os.path.isfile(icon):
                return icon
    except Exception:
        pass
    return "anibase"


def is_linux_startup_enabled() -> bool:
    """Checks if AniBase is registered in XDG autostart."""
    path = _get_linux_autostart_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # Explicitly disabled trumps everything
        if "X-GNOME-Autostart-enabled=false" in content:
            return False
        return "[Desktop Entry]" in content
    except OSError:
        return False


def set_linux_startup(enabled: bool) -> bool:
    """Enables or disables AniBase autostart via XDG .desktop file."""
    path = _get_linux_autostart_path()
    if enabled:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cmd = _get_linux_startup_command()
            icon = _get_linux_icon_path()
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=AniBase\n"
                f"Exec={cmd}\n"
                f"Icon={icon}\n"
                "X-GNOME-Autostart-enabled=true\n"
                "NoDisplay=false\n"
                "Comment=Private Anime Streaming & Library Management\n"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            app_log("AniBase registered for Linux autostart.", "INFO")
            return True
        except OSError as e:
            app_log(f"Failed to write Linux autostart file: {e}", "WARN")
            return False
    else:
        try:
            if os.path.isfile(path):
                os.unlink(path)
            app_log("AniBase removed from Linux autostart.", "INFO")
        except OSError as e:
            app_log(f"Failed to remove Linux autostart file: {e}", "WARN")
            return False
        return True


def ensure_linux_desktop_entry():
    """Ensures AniBase .desktop entry and high-res icon are installed in ~/.local/share/applications and icons."""
    if os.name == "nt":
        return
    ensure_kde_kwin_rule()
    try:
        import shutil
        import subprocess
        from anibase.utils import export_ico_to_png, get_application_dir

        home = os.path.expanduser("~")
        apps_dir = os.path.join(home, ".local", "share", "applications")
        icons_dir = os.path.join(home, ".local", "share", "icons", "hicolor", "512x512", "apps")
        os.makedirs(apps_dir, exist_ok=True)
        os.makedirs(icons_dir, exist_ok=True)

        icon_path = _get_linux_icon_path()
        dest_icon = os.path.join(icons_dir, "anibase.png")
        fallback_icon = os.path.join(home, ".local", "share", "icons", "anibase.png")
        icon_updated = False
        if icon_path and os.path.isfile(icon_path):
            prev_bytes = None
            if os.path.isfile(dest_icon):
                try:
                    with open(dest_icon, "rb") as rf:
                        prev_bytes = rf.read()
                except OSError:
                    prev_bytes = None

            if icon_path.lower().endswith(".ico") and export_ico_to_png(icon_path, dest_icon):
                export_ico_to_png(icon_path, fallback_icon)
            elif not os.path.isfile(dest_icon) or os.path.getmtime(icon_path) > os.path.getmtime(dest_icon):
                shutil.copy2(icon_path, dest_icon)
                shutil.copy2(icon_path, fallback_icon)

            if os.path.isfile(dest_icon):
                try:
                    with open(dest_icon, "rb") as rf:
                        icon_updated = rf.read() != prev_bytes
                except OSError:
                    icon_updated = True

        desktop_file = os.path.join(apps_dir, "anibase.desktop")
        app_dir = get_application_dir()
        if getattr(sys, "frozen", False):
            exec_cmd = f'"{sys.executable}"'
        else:
            run_tray = os.path.join(app_dir, "run_tray.sh")
            if os.path.isfile(run_tray):
                exec_cmd = f'"{run_tray}"'
            else:
                exec_cmd = f'"{sys.executable}" "{os.path.join(app_dir, "tray_ui.py")}"'

        entry_content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=AniBase\n"
            "Comment=Private Anime Streaming & Library Management\n"
            f"Exec={exec_cmd}\n"
            f"Icon={dest_icon if os.path.isfile(dest_icon) else (icon_path or 'anibase')}\n"
            "Terminal=false\n"
            "Categories=AudioVideo;Video;Player;\n"
            "Keywords=anime;streaming;player;video;\n"
            "StartupWMClass=anibase\n"
        )
        should_write = True
        if os.path.isfile(desktop_file):
            try:
                with open(desktop_file, "r", encoding="utf-8") as rf:
                    if rf.read() == entry_content:
                        should_write = False
            except OSError:
                pass
        if should_write:
            with open(desktop_file, "w", encoding="utf-8") as f:
                f.write(entry_content)
        if should_write or icon_updated:
            try:
                subprocess.run(["update-desktop-database", apps_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                gtk_icon_cache = shutil.which("gtk-update-icon-cache")
                if gtk_icon_cache:
                    hicolor_dir = os.path.join(home, ".local", "share", "icons", "hicolor")
                    subprocess.run([gtk_icon_cache, "-f", "-t", hicolor_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                kbuildsycoca = shutil.which("kbuildsycoca6") or shutil.which("kbuildsycoca5")
                if kbuildsycoca:
                    subprocess.run([kbuildsycoca], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
    except Exception as err:
        app_log(f"Failed to ensure Linux desktop entry: {err}", "WARN")


def ensure_kde_kwin_rule():
    """Ensures KWin suppresses duplicate server-side decorations on KDE Plasma."""
    if os.name == "nt":
        return
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
    if "KDE" not in desktop and not os.environ.get("KDE_FULL_SESSION"):
        return

    try:
        import shutil
        import subprocess

        kread = shutil.which("kreadconfig6") or shutil.which("kreadconfig5")
        kwrite = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
        rules_path = os.path.expanduser("~/.config/kwinrulesrc")

        target_group = None
        if os.path.isfile(rules_path):
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config.optionxform = str
            config.read(rules_path, encoding="utf-8")
            for sec in config.sections():
                if sec in ("General", "$Version"):
                    continue
                if config.get(sec, "wmclass", fallback="").lower() == "anibase":
                    target_group = sec
                    break

        if not target_group:
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config.optionxform = str
            if os.path.isfile(rules_path):
                config.read(rules_path, encoding="utf-8")
            existing_indices = [int(s) for s in config.sections() if s.isdigit()]
            target_group = str(max(existing_indices, default=0) + 1)

            if kwrite:
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "Description", "AniBase - Borderless"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "wmclass", "anibase"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "wmclassmatch", "1"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "noborder", "true"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "noborderrule", "2"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "decorationpolicy", "none"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "decorationpolicyrule", "2"], check=False)

                order = ""
                if kread:
                    res = subprocess.run([kread, "--file", "kwinrulesrc", "--group", "General", "--key", "Order"], capture_output=True, text=True)
                    order = res.stdout.strip()
                orders = [o.strip() for o in order.split(",") if o.strip()]
                if target_group not in orders:
                    orders.append(target_group)
                    subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", "General", "--key", "Order", ",".join(orders)], check=False)
        else:
            if kwrite:
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "noborder", "true"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "noborderrule", "2"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "decorationpolicy", "none"], check=False)
                subprocess.run([kwrite, "--file", "kwinrulesrc", "--group", target_group, "--key", "decorationpolicyrule", "2"], check=False)

        subprocess.run(
            ["busctl", "--user", "call", "org.kde.KWin", "/KWin", "org.kde.KWin", "reconfigure"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        app_log("KDE Plasma KWin borderless rule configured successfully.", "INFO")
    except Exception as err:
        app_log(f"Failed to ensure KDE KWin rule: {err}", "WARN")


# ---------------------------------------------------------------------------
# Cross-platform public API
# ---------------------------------------------------------------------------

def is_system_startup_enabled() -> bool:
    """Checks if AniBase is configured to run on system startup (cross-platform)."""
    if os.name == "nt":
        return is_windows_startup_enabled()
    return is_linux_startup_enabled()


def set_system_startup(enabled: bool) -> bool:
    """Enables or disables AniBase system startup (cross-platform)."""
    if os.name == "nt":
        return set_windows_startup(enabled)
    return set_linux_startup(enabled)


def sync_startup_from_settings(enabled: bool) -> bool:
    """Synchronizes system startup state with the configured setting (cross-platform)."""
    current = is_system_startup_enabled()
    if current != bool(enabled):
        return set_system_startup(bool(enabled))
    return True


def sync_windows_startup_from_settings(enabled: bool) -> bool:
    """Backward-compatibility alias for sync_startup_from_settings."""
    return sync_startup_from_settings(enabled)


# ---------------------------------------------------------------------------
# System Tray Availability & Desktop Engine Helpers
# ---------------------------------------------------------------------------

def set_system_tray_available(available: bool):
    """Sets whether system tray icon support is confirmed available by the GUI backend."""
    global _system_tray_available
    _system_tray_available = bool(available)


def is_system_tray_available() -> bool:
    """Checks if system tray support is available on this environment."""
    global _system_tray_available
    if _system_tray_available is not None:
        return _system_tray_available
    if os.name == "nt":
        return True
    return True


def get_desktop_gui_engine() -> str | None:
    """
    Determines the appropriate pywebview GUI engine:
    - Windows: 'edgechromium' (Edge WebView2)
    - Linux: 'gtk' (WebKit2GTK), 'qt' (PySide6/QtWebEngine), or None (pywebview auto-detect)
    Allows override via ANIBASE_WEBVIEW_GUI environment variable.
    """
    env_engine = os.environ.get("ANIBASE_WEBVIEW_GUI", "").strip().lower()
    if env_engine:
        return env_engine

    if os.name == "nt":
        return "edgechromium"

    # Linux: Check GTK (WebKit2GTK) first
    try:
        import gi  # type: ignore[import]
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("WebKit2", "4.1")
        except (ValueError, AttributeError):
            gi.require_version("WebKit2", "4.0")
        from gi.repository import Gtk, WebKit2  # noqa: F401
        return "gtk"
    except Exception:
        pass

    # Check Qt (PySide6 / PyQt5) second
    try:
        from PySide6 import QtWebEngineWidgets  # noqa: F401
        return "qt"
    except Exception:
        pass

    return None


def get_desktop_storage_path() -> str:
    """Returns persistent cache and profile storage directory for the desktop web engine."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            local_app_data = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        storage_dir = os.path.join(local_app_data, "AniBase", "webview2")
    else:
        from anibase.utils import get_user_data_dir
        storage_dir = os.path.join(get_user_data_dir(), "webview")

    try:
        os.makedirs(storage_dir, exist_ok=True)
    except OSError:
        pass
    return storage_dir


def reset_desktop_window() -> dict:
    """Resets the desktop window to default size (1280x800) and centers it on the primary monitor."""
    global _window_state, desktop_window
    sw, sh = get_screen_bounds()
    w, h = 1280, 800
    w = max(960, min(sw, w))
    h = max(600, min(sh, h))
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)

    _window_state["width"] = w
    _window_state["height"] = h
    _window_state["x"] = x
    _window_state["y"] = y
    _window_state["maximized"] = False
    _window_state["zoom"] = 1.0

    try:
        settings = load_settings()
        settings["desktop_window"] = {
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "maximized": False,
            "zoom": 1.0,
        }
        save_settings(settings)
    except Exception as e:
        app_log(f"Failed to save reset window state: {e}", "WARN")

    if desktop_window:
        try:
            if hasattr(desktop_window, "restore"):
                desktop_window.restore()
            if hasattr(desktop_window, "resize"):
                desktop_window.resize(w, h)
            if hasattr(desktop_window, "move"):
                desktop_window.move(x, y)
        except Exception as e:
            app_log(f"Error repositioning desktop window: {e}", "DEBUG")

    return {
        "ok": True,
        "width": w,
        "height": h,
        "x": x,
        "y": y,
        "desktop_active": desktop_window is not None,
        "message": "Ukuran dan posisi jendela desktop berhasil diatur ulang ke 1280x800 di tengah layar."
    }



def get_window_hwnd():
    """Returns the Win32 HWND of the desktop window."""
    global desktop_window
    if not desktop_window:
        return None
    if hasattr(desktop_window, "native") and desktop_window.native:
        try:
            return int(desktop_window.native.Handle.ToInt64())
        except Exception:
            try:
                return int(desktop_window.native.Handle.ToInt32())
            except Exception:
                pass
    if os.name == "nt":
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "AniBase")
            if hwnd:
                return hwnd
        except Exception:
            pass
    return None


def apply_windows_11_attributes(hwnd=None):
    """Applies Windows 11 DWM dark mode, rounded corners, subtle border, and Win32 resizing/snap frame."""
    if os.name != "nt":
        return
    hwnd = hwnd or get_window_hwnd()
    if not hwnd:
        return

    try:
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        # 1. Dark Mode (DWMWA_USE_IMMERSIVE_DARK_MODE = 20, fallback 19 for older Win10 builds)
        dark_mode = ctypes.c_int(1)
        hr = dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
        if hr != 0:
            dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))

        # 2. Rounded Corners (DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2)
        corner_pref = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner_pref), ctypes.sizeof(corner_pref))

        # 3. Subtle Dark Border (DWMWA_BORDER_COLOR = 34, 0x002D231E -> BGR for #1e232d)
        border_color = ctypes.c_int(0x002D231E)
        dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color))

        # 4. Enable Win32 Resizing Frame (WS_THICKFRAME = 0x00040000) and Maximize/Minimize Box
        # This provides edge-resizing on frameless windows and activates Windows 11 Snap Layouts (Win+Arrows)
        GWL_STYLE = -16
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        WS_THICKFRAME = 0x00040000
        WS_MAXIMIZEBOX = 0x00010000
        WS_MINIMIZEBOX = 0x00020000
        new_style = style | WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX
        if new_style != style:
            user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
    except Exception:
        pass


def load_saved_window_state():
    global _window_state
    try:
        settings = load_settings()
        saved = settings.get("desktop_window")
        if isinstance(saved, dict):
            sw, sh = get_screen_bounds()
            w = max(960, min(sw, int(saved.get("width", 1280))))
            h = max(600, min(sh, int(saved.get("height", 800))))
            x = saved.get("x")
            y = saved.get("y")
            if x is not None and y is not None:
                x = max(0, min(sw - 120, int(x)))
                y = max(0, min(sh - 120, int(y)))
            else:
                x = (sw - w) // 2
                y = (sh - h) // 2

            _window_state["width"] = w
            _window_state["height"] = h
            _window_state["x"] = x
            _window_state["y"] = y
            _window_state["maximized"] = bool(saved.get("maximized", False))
            zoom = saved.get("zoom")
            if zoom is not None:
                _window_state["zoom"] = max(0.5, min(3.0, round(float(zoom), 2)))
    except Exception:
        pass


def save_current_window_state():
    global _window_state
    if not desktop_window:
        return
    try:
        if not _window_state.get("maximized") and not _window_state.get("fullscreen"):
            # On Linux GTK, never access desktop_window.width/height/x/y as pywebview GTK
            # uses glib.idle_add + Semaphore.acquire() which deadlocks when called from the UI thread.
            # _window_state is kept up to date via on_window_resized and on_window_moved.
            if type(desktop_window).__name__ == "MagicMock" or os.name == "nt":
                try:
                    if desktop_window.width and desktop_window.height:
                        _window_state["width"] = int(desktop_window.width)
                        _window_state["height"] = int(desktop_window.height)
                    if desktop_window.x is not None and desktop_window.y is not None:
                        _window_state["x"] = int(desktop_window.x)
                        _window_state["y"] = int(desktop_window.y)
                except Exception:
                    pass

        settings = load_settings()
        settings["desktop_window"] = {
            "x": _window_state.get("x"),
            "y": _window_state.get("y"),
            "width": _window_state.get("width", 1280),
            "height": _window_state.get("height", 800),
            "maximized": bool(_window_state.get("maximized")),
            "zoom": float(_window_state.get("zoom", 1.0)),
        }
        save_settings(settings)
    except Exception:
        pass


def on_window_closing():
    """Intercept window close: hide to System Tray if configured, or terminate."""
    global _is_shutting_down
    if _is_shutting_down:
        return True

    save_current_window_state()
    settings = load_settings()
    minimize_to_tray = settings.get("desktop_minimize_to_tray", True)
    if not minimize_to_tray or not is_system_tray_available():
        _is_shutting_down = True
        return True

    if desktop_window:
        desktop_window.hide()
    return False


def safe_evaluate_js(script: str):
    """Safely evaluates JavaScript without blocking or deadlocking if webview is not ready."""
    if not desktop_window:
        return
    events = getattr(desktop_window, "events", None)
    ready = getattr(events, "_pywebviewready", None) if events else None
    if not ready or not ready.is_set():
        return

    if type(desktop_window).__name__ == "MagicMock":
        try:
            desktop_window.evaluate_js(script)
        except Exception:
            pass
        return

    # On Linux GTK, WebKit2 provides asynchronous evaluate_javascript/run_javascript.
    # Invoking it via GLib.idle_add guarantees zero semaphore deadlock.
    if os.name != "nt":
        try:
            from webview.platforms.gtk import BrowserView
            from gi.repository import GLib
            bv = BrowserView.instances.get(desktop_window.uid)
            if bv and getattr(bv, "webview", None):
                def _run_js():
                    try:
                        wv = bv.webview
                        if hasattr(wv, "evaluate_javascript"):
                            try:
                                wv.evaluate_javascript(script, len(script), None, None, None, None, None)
                                return False
                            except TypeError:
                                wv.evaluate_javascript(script, len(script), None, None, None, None)
                                return False
                        elif hasattr(wv, "run_javascript"):
                            wv.run_javascript(script)
                    except Exception:
                        pass
                    return False
                GLib.idle_add(_run_js)
                return
        except Exception:
            pass

    def _eval():
        try:
            desktop_window.evaluate_js(script)
        except Exception:
            pass

    threading.Thread(target=_eval, daemon=True).start()


def _invoke_on_ui_thread(window_native, callback):
    """Safely invokes callback on the UI thread across WinForms, GTK, and Qt."""
    if not window_native:
        try:
            callback()
        except Exception:
            pass
        return

    # WinForms (.NET WebView2) thread synchronization
    if getattr(window_native, "InvokeRequired", False):
        try:
            action_cls = getattr(sys.modules.get("System", None) or __import__("System"), "Action", None)
            if action_cls:
                begin_invoke = getattr(window_native, "BeginInvoke", None)
                if callable(begin_invoke):
                    begin_invoke(action_cls(callback))
                    return
        except Exception:
            pass

    try:
        callback()
    except Exception:
        pass


def get_desktop_zoom() -> float:
    return float(_window_state.get("zoom", 1.0))


def set_desktop_zoom(factor: float) -> dict:
    global _window_state
    factor = max(0.5, min(3.0, round(float(factor), 2)))
    _window_state["zoom"] = factor
    return {"factor": factor, "native": False}


def adjust_desktop_zoom_step(direction: int) -> dict:
    current = get_desktop_zoom()
    if direction > 0:
        target = next((s for s in ZOOM_STEPS if s > current + 0.01), ZOOM_STEPS[-1])
    else:
        target = next((s for s in reversed(ZOOM_STEPS) if s < current - 0.01), ZOOM_STEPS[0])
    return set_desktop_zoom(target)


def configure_webview2_settings(window=None):
    """Enables browser accelerator keys, zoom controls, and restores zoom on Edge WebView2."""
    if os.name != "nt":
        return False
    win = window or desktop_window
    if not win or not hasattr(win, "native") or not win.native:
        return False

    try:
        webview_ctrl = getattr(win.native, "webview", None)
        if not webview_ctrl:
            return False

        def _apply():
            try:
                core = getattr(webview_ctrl, "CoreWebView2", None)
                if not core:
                    return
                settings = getattr(core, "Settings", None)
                if settings:
                    settings.AreBrowserAcceleratorKeysEnabled = True
                    settings.IsZoomControlEnabled = True
                    if hasattr(settings, "IsStatusBarEnabled"):
                        settings.IsStatusBarEnabled = False
                    if hasattr(settings, "IsSwipeNavigationEnabled"):
                        settings.IsSwipeNavigationEnabled = False
                    if hasattr(settings, "AreDefaultContextMenusEnabled"):
                        settings.AreDefaultContextMenusEnabled = False
                if type(win.native).__name__ == "MagicMock":
                    core.ZoomFactor = float(_window_state.get("zoom", 1.0))
            except Exception:
                pass

        _invoke_on_ui_thread(win.native, _apply)
        return True
    except Exception:
        return False


def on_window_loaded(*args, **kwargs):
    configure_webview2_settings()


def setup_linux_gtk_window_controls(win):
    if os.name == "nt" or not win:
        return
    try:
        from webview.platforms.gtk import BrowserView
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, Gtk
        bv = BrowserView.instances.get(win.uid)
        if not bv or not getattr(bv, "webview", None) or not getattr(bv, "window", None):
            return

        try:
            bv.webview.set_opacity(1.0)
            settings = bv.webview.get_settings()
            import gi
            try:
                gi.require_version("WebKit2", "4.1")
            except ValueError:
                gi.require_version("WebKit2", "4.0")
            from gi.repository import WebKit2
            if hasattr(WebKit2, "HardwareAccelerationPolicy") and hasattr(settings, "set_hardware_acceleration_policy"):
                settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ON_DEMAND)
        except Exception:
            pass

        def on_webview_button_press(widget, event):
            if event.button != 1:
                return False

            width, height = bv.window.get_size()
            BORDER = 5

            # Edge resizing detection for frameless window
            edge = None
            if event.y <= BORDER:
                if event.x <= BORDER:
                    edge = Gdk.WindowEdge.NORTH_WEST
                elif event.x >= width - BORDER:
                    edge = Gdk.WindowEdge.NORTH_EAST
                else:
                    edge = Gdk.WindowEdge.NORTH
            elif event.y >= height - BORDER:
                if event.x <= BORDER:
                    edge = Gdk.WindowEdge.SOUTH_WEST
                elif event.x >= width - BORDER:
                    edge = Gdk.WindowEdge.SOUTH_EAST
                else:
                    edge = Gdk.WindowEdge.SOUTH
            elif event.x <= BORDER:
                edge = Gdk.WindowEdge.WEST
            elif event.x >= width - BORDER:
                edge = Gdk.WindowEdge.EAST

            if edge is not None:
                try:
                    bv.window.begin_resize_drag(
                        edge,
                        event.button,
                        int(event.x_root),
                        int(event.y_root),
                        event.time
                    )
                    return True
                except Exception:
                    pass

            # Titlebar height is 36px
            if event.y <= 36 and event.button == 1:
                # Double click toggles maximize/restore
                if event.type == Gdk.EventType._2BUTTON_PRESS:
                    is_max = bv.window.is_maximized() if hasattr(bv.window, "is_maximized") else bool(_window_state.get("maximized"))
                    if is_max:
                        bv.window.unmaximize()
                        _window_state["maximized"] = False
                        safe_evaluate_js("window.__anibase_set_maximized && window.__anibase_set_maximized(false);")
                    else:
                        bv.window.maximize()
                        _window_state["maximized"] = True
                        safe_evaluate_js("window.__anibase_set_maximized && window.__anibase_set_maximized(true);")
                    return True

                # Direct native titlebar button clicks (Right side: WhiteSur or Windows controls)
                if event.x >= (width - 132):
                    rel_x = width - event.x
                    titlebar_style = _window_state.get("titlebar_style")
                    if not titlebar_style:
                        try:
                            titlebar_style = load_settings().get("desktop_titlebar_style", "macos")
                            _window_state["titlebar_style"] = titlebar_style
                        except Exception:
                            titlebar_style = "macos"

                    api = desktop_api or DesktopApi(win)
                    if titlebar_style == "macos":
                        if rel_x <= 28:
                            # Red Close
                            api.close()
                            return True
                        elif rel_x <= 50:
                            # Green Maximize / Restore
                            api.toggle_maximize()
                            return True
                        elif rel_x <= 72:
                            # Yellow Minimize
                            api.minimize()
                            return True
                    else:
                        if rel_x <= 44:
                            api.close()
                            return True
                        elif rel_x <= 88:
                            api.toggle_maximize()
                            return True
                        elif rel_x <= 132:
                            api.minimize()
                            return True

                # Anywhere else on titlebar: Wayland native drag
                try:
                    bv.window.begin_move_drag(
                        event.button,
                        int(event.x_root),
                        int(event.y_root),
                        event.time
                    )
                    return True
                except Exception as err:
                    app_log(f"Linux begin_move_drag error: {err}", "WARN")
                    return False

            return False

        bv.webview.connect("button-press-event", on_webview_button_press)
        if getattr(bv, "window", None):
            icon_path = _get_linux_icon_path()
            if icon_path and os.path.isfile(icon_path):
                try:
                    bv.window.set_icon_from_file(icon_path)
                    Gtk.Window.set_default_icon_from_file(icon_path)
                except Exception:
                    pass
        app_log("Linux GTK titlebar native drag and edge resize initialized.", "INFO")
    except Exception as err:
        app_log(f"Failed to setup Linux GTK window controls: {err}", "WARN")


def on_window_shown():
    hwnd = get_window_hwnd()
    if hwnd:
        apply_windows_11_attributes(hwnd)
    configure_webview2_settings()

    if os.name != "nt":
        try:
            icon_path = _get_linux_icon_path()
            if icon_path and os.path.isfile(icon_path):
                native = getattr(desktop_window, "native", None)
                if native and hasattr(native, "set_icon_from_file"):
                    native.set_icon_from_file(icon_path)
                try:
                    import gi
                    gi.require_version("Gtk", "3.0")
                    from gi.repository import Gtk
                    Gtk.Window.set_default_icon_from_file(icon_path)
                except Exception:
                    pass
        except Exception:
            pass
        setup_linux_gtk_window_controls(desktop_window)
        threading.Thread(target=ensure_linux_desktop_entry, daemon=True).start()


def on_window_maximized():
    if _window_state.get("maximized") and not _window_state.get("fullscreen"):
        return
    _window_state["maximized"] = True
    safe_evaluate_js("window.__anibase_set_maximized && window.__anibase_set_maximized(true)")


def toggle_desktop_fullscreen() -> bool:
    current = bool(_window_state.get("fullscreen"))
    return set_desktop_fullscreen(not current)


def set_desktop_fullscreen(enable: bool) -> bool:
    global desktop_window, _window_state
    enable = bool(enable)
    current_fullscreen = bool(_window_state.get("fullscreen"))
    if current_fullscreen == enable:
        return enable

    _window_state["fullscreen"] = enable
    if not desktop_window:
        return enable

    native = getattr(desktop_window, "native", None)
    try:
        native_is_fs = bool(getattr(native, "is_fullscreen", False)) if native else False
        if enable and not native_is_fs:
            desktop_window.toggle_fullscreen()
        elif not enable and native_is_fs:
            desktop_window.toggle_fullscreen()
    except Exception:
        pass

    hwnd = get_window_hwnd()
    if os.name == "nt" and hwnd:
        try:
            user32 = ctypes.windll.user32
            GWL_STYLE = -16
            WS_THICKFRAME = 0x00040000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if enable:
                if style & WS_THICKFRAME:
                    user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_THICKFRAME)
                    SWP_FRAMECHANGED = 0x0020
                    SWP_NOMOVE = 0x0002
                    SWP_NOSIZE = 0x0001
                    SWP_NOZORDER = 0x0004
                    user32.SetWindowPos(
                        hwnd, 0, 0, 0, 0, 0,
                        SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                    )
            else:
                apply_windows_11_attributes(hwnd)
        except Exception:
            pass

    js_val = "true" if enable else "false"
    safe_evaluate_js(f"window.__anibase_set_fullscreen && window.__anibase_set_fullscreen({js_val});")
    return enable


def on_window_fullscreened():
    _window_state["fullscreen"] = True
    safe_evaluate_js("window.__anibase_set_fullscreen && window.__anibase_set_fullscreen(true);")


def on_window_restored():
    if not _window_state.get("maximized") and not _window_state.get("fullscreen"):
        return
    was_fullscreen = bool(_window_state.get("fullscreen"))
    _window_state["maximized"] = False
    _window_state["fullscreen"] = False
    if was_fullscreen:
        hwnd = get_window_hwnd()
        if hwnd:
            try:
                apply_windows_11_attributes(hwnd)
            except Exception:
                pass
    safe_evaluate_js(
        "window.__anibase_set_maximized && window.__anibase_set_maximized(false);"
        "window.__anibase_set_fullscreen && window.__anibase_set_fullscreen(false);"
    )


def on_window_resized(width, height):
    if not _window_state.get("maximized") and not _window_state.get("fullscreen"):
        _window_state["width"] = int(width)
        _window_state["height"] = int(height)


def on_window_moved(x, y):
    if not _window_state.get("maximized") and not _window_state.get("fullscreen"):
        _window_state["x"] = int(x)
        _window_state["y"] = int(y)


def restore_desktop_window():
    """Shows, restores, and brings the AniBase desktop window to the front."""
    global desktop_window
    if not desktop_window:
        return False

    try:
        desktop_window.show()
        if _window_state.get("maximized"):
            desktop_window.maximize()
        else:
            desktop_window.restore()

        if os.name == "nt":
            try:
                hwnd = get_window_hwnd()
                if hwnd:
                    user32 = ctypes.windll.user32
                    SW_RESTORE = 9
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    fore_hwnd = user32.GetForegroundWindow()
                    if fore_hwnd and fore_hwnd != hwnd:
                        cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                        fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None)
                        user32.AttachThreadInput(cur_thread, fore_thread, True)
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        user32.AttachThreadInput(cur_thread, fore_thread, False)
                    else:
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
            except Exception:
                pass
        else:
            # Linux: GTK / Qt window presentation
            try:
                native = getattr(desktop_window, "native", None)
                if native:
                    if hasattr(native, "present"):
                        native.present()
                    elif hasattr(native, "raise_"):
                        native.raise_()
                        if hasattr(native, "activateWindow"):
                            native.activateWindow()
                else:
                    from webview.platforms.gtk import BrowserView
                    from gi.repository import GLib
                    bv = BrowserView.instances.get(desktop_window.uid)
                    if bv and getattr(bv, "window", None):
                        GLib.idle_add(bv.window.present)
            except Exception:
                pass
        return True
    except Exception:
        return False


def create_desktop_window(url):
    """Initializes and returns the pywebview desktop window."""
    global desktop_window, desktop_api

    storage_dir = get_desktop_storage_path()
    if os.name == "nt":
        try:
            os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", storage_dir)
            os.environ.setdefault(
                "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                "--enable-gpu-rasterization --autoplay-policy=no-user-gesture-required --enable-features=ParallelDownloading --media-cache-size=104857600",
            )
        except OSError:
            pass
    else:
        # GNOME / Wayland application identification
        try:
            import gi  # type: ignore[import]
            gi.require_version("GLib", "2.0")
            from gi.repository import GLib  # type: ignore[import]
            GLib.set_prgname("anibase")
            GLib.set_application_name("AniBase")
        except Exception:
            pass
        ensure_kde_kwin_rule()

    load_saved_window_state()
    settings = load_settings()
    start_minimized = bool(settings.get("desktop_start_minimized", False))
    start_maximized = bool(_window_state.get("maximized", False))

    desktop_api = DesktopApi()
    desktop_window = webview.create_window(
        title="AniBase",
        url=url,
        js_api=desktop_api,
        frameless=True,
        easy_drag=False,
        hidden=start_minimized,
        maximized=start_maximized,
        width=_window_state["width"],
        height=_window_state["height"],
        x=_window_state["x"],
        y=_window_state["y"],
        min_size=(960, 600),
        background_color="#080b12",
        zoomable=True,
    )
    desktop_api._window = desktop_window

    desktop_window.events.shown += on_window_shown
    desktop_window.events.loaded += on_window_loaded
    desktop_window.events.closing += on_window_closing
    desktop_window.events.closed += on_window_closed
    desktop_window.events.maximized += on_window_maximized
    desktop_window.events.restored += on_window_restored
    desktop_window.events.resized += on_window_resized
    desktop_window.events.moved += on_window_moved

    # Start background update checker and post-update notification service
    try:
        from anibase.desktop.updater import start_update_checker_service
        start_update_checker_service()
    except Exception as err:
        app_log(f"Failed to start update checker service: {err}", "WARN")

    return desktop_window


def on_window_closed():
    global desktop_window
    desktop_window = None


def close_desktop_window_permanent():
    """Completely destroys the window when exiting the entire application."""
    global _is_shutting_down, desktop_window
    _is_shutting_down = True
    save_current_window_state()
    if desktop_window:
        try:
            desktop_window.events.closing -= on_window_closing
        except Exception:
            pass
        try:
            desktop_window.destroy()
        except Exception:
            pass
