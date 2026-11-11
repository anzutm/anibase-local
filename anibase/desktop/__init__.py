# anibase/desktop/__init__.py
"""Desktop application shell and window controllers for AniBase."""

from anibase.desktop.shell import (
    create_desktop_window,
    restore_desktop_window,
    close_desktop_window_permanent,
    DesktopApi,
)

__all__ = [
    "create_desktop_window",
    "restore_desktop_window",
    "close_desktop_window_permanent",
    "DesktopApi",
]

