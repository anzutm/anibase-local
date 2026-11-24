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
    get_desktop_gui_engine,
    get_desktop_storage_path,
    is_system_tray_available,
    set_system_tray_available,
    # Windows-specific (kept for backward compatibility)
    is_windows_startup_enabled,
    set_windows_startup,
    sync_windows_startup_from_settings,
    # Linux-specific
    is_linux_startup_enabled,
    set_linux_startup,
    DesktopApi,
)
from anibase.desktop.updater import (
    send_desktop_notification,
    check_post_update_notification,
    check_github_release,
    start_update_checker_service,
    is_version_newer,
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
    "get_desktop_gui_engine",
    "get_desktop_storage_path",
    "is_system_tray_available",
    "set_system_tray_available",
    # Windows-specific (backward compat)
    "is_windows_startup_enabled",
    "set_windows_startup",
    "sync_windows_startup_from_settings",
    # Linux-specific
    "is_linux_startup_enabled",
    "set_linux_startup",
    "DesktopApi",
    # Updater & Notifications
    "send_desktop_notification",
    "check_post_update_notification",
    "check_github_release",
    "start_update_checker_service",
    "is_version_newer",
]

