# anibase/desktop/shell.py
"""Desktop application shell hosting Microsoft Edge WebView2 for AniBase."""

import ctypes
import os
import sys
import threading
import time
import webbrowser
import webview

from anibase.settings import load_settings, save_settings

desktop_window = None
desktop_api = None
_window_state = {
    "x": None,
    "y": None,
    "width": 1280,
    "height": 800,
    "maximized": False,
}
_is_shutting_down = False


class DesktopApi:
    """Python API exposed to JavaScript via window.pywebview.api."""

    def __init__(self, window):
        self.window = window

    def minimize(self):
        if self.window:
            self.window.minimize()

    def toggle_maximize(self):
        if not self.window:
            return
        if _window_state.get("maximized"):
            self.window.restore()
        else:
            self.window.maximize()

    def close(self):
        """User clicked close in titlebar: minimize to system tray."""
        if self.window:
            save_current_window_state()
            self.window.hide()

    def open_external(self, url):
        """Open external URL in the user's default browser."""
        try:
            webbrowser.open(str(url))
        except Exception:
            pass

    def get_state(self):
        return {
            "maximized": bool(_window_state.get("maximized")),
            "width": _window_state.get("width", 1280),
            "height": _window_state.get("height", 800),
        }


def get_screen_bounds():
    """Returns (width, height) of primary screen on Windows."""
    if os.name == "nt":
        try:
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    return 1920, 1080


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
    except Exception:
        pass


def save_current_window_state():
    global _window_state
    if not desktop_window:
        return
    try:
        if not _window_state.get("maximized"):
            if desktop_window.width and desktop_window.height:
                _window_state["width"] = int(desktop_window.width)
                _window_state["height"] = int(desktop_window.height)
            if desktop_window.x is not None and desktop_window.y is not None:
                _window_state["x"] = int(desktop_window.x)
                _window_state["y"] = int(desktop_window.y)

        settings = load_settings()
        settings["desktop_window"] = {
            "x": _window_state["x"],
            "y": _window_state["y"],
            "width": _window_state["width"],
            "height": _window_state["height"],
            "maximized": bool(_window_state["maximized"]),
        }
        save_settings(settings)
    except Exception:
        pass


def on_window_closing():
    """Intercept window close to hide to System Tray instead of terminating."""
    global _is_shutting_down
    if _is_shutting_down:
        return True

    save_current_window_state()
    if desktop_window:
        desktop_window.hide()
    return False


def on_window_maximized():
    _window_state["maximized"] = True
    if desktop_window:
        try:
            desktop_window.evaluate_js(
                "window.__anibase_set_maximized && window.__anibase_set_maximized(true)"
            )
        except Exception:
            pass


def on_window_restored():
    _window_state["maximized"] = False
    if desktop_window:
        try:
            desktop_window.evaluate_js(
                "window.__anibase_set_maximized && window.__anibase_set_maximized(false)"
            )
        except Exception:
            pass


def on_window_resized(width, height):
    if not _window_state.get("maximized"):
        _window_state["width"] = int(width)
        _window_state["height"] = int(height)


def on_window_moved(x, y):
    if not _window_state.get("maximized"):
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
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, "AniBase")
                if hwnd:
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        return True
    except Exception:
        return False


def create_desktop_window(url):
    """Initializes and returns the pywebview desktop window."""
    global desktop_window, desktop_api

    load_saved_window_state()

    desktop_window = webview.create_window(
        title="AniBase",
        url=url,
        frameless=True,
        easy_drag=False,
        width=_window_state["width"],
        height=_window_state["height"],
        x=_window_state["x"],
        y=_window_state["y"],
        min_size=(960, 600),
        background_color="#080b12",
    )

    desktop_api = DesktopApi(desktop_window)
    desktop_window.js_api = desktop_api

    desktop_window.events.closing += on_window_closing
    desktop_window.events.maximized += on_maximized
    desktop_window.events.restored += on_window_restored
    desktop_window.events.resized += on_window_resized
    desktop_window.events.moved += on_window_moved

    if _window_state.get("maximized"):
        def auto_max():
            time.sleep(0.5)
            if desktop_window:
                desktop_window.maximize()
        threading.Thread(target=auto_max, daemon=True).start()

    return desktop_window


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

