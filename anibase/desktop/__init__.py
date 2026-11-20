# anibase/desktop/__init__.py
"""Desktop application shell and window controllers for AniBase."""

from anibase.desktop.shell import (
    create_desktop_window,
    restore_desktop_window,
    close_desktop_window_permanent,
    reset_desktop_window,
    # Cross-platform (preferred)
    is_system_startup_enabled,
    set_system_startup,
    sync_startup_from_settings,
    # Windows-specific (kept for backward compatibility)
    is_windows_startup_enabled,
    set_windows_startup,
    sync_windows_startup_from_settings,
    # Linux-specific
    is_linux_startup_enabled,
    set_linux_startup,
    DesktopApi,
)

__all__ = [
    "create_desktop_window",
    "restore_desktop_window",
    "close_desktop_window_permanent",
    "reset_desktop_window",
    # Cross-platform (preferred)
    "is_system_startup_enabled",
    "set_system_startup",
    "sync_startup_from_settings",
    # Windows-specific (backward compat)
    "is_windows_startup_enabled",
    "set_windows_startup",
    "sync_windows_startup_from_settings",
    # Linux-specific
    "is_linux_startup_enabled",
    "set_linux_startup",
    "DesktopApi",
]

