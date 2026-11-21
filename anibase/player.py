# anibase/player.py
"""Manajemen eksternal player (VLC dsb) dan diagnostik path eksekutabel."""

import os
import shutil
from anibase.utils import normalize_library_path
from anibase.ffmpeg import make_dependency_result


def diagnose_vlc_path(vlc_path):
    path = normalize_library_path(vlc_path)
    if not path:
        return make_dependency_result(
            False,
            "",
            "",
            "not_configured",
            "Media Player path is not configured."
        )

    if os.path.isdir(path):
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player path points to a folder, not an executable file."
        )

    if not os.path.exists(path):
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player executable was not found."
        )

    if not os.path.isfile(path):
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player path is not a regular file."
        )

    if os.name == "nt" and os.path.splitext(path)[1].lower() not in {".exe", ".bat", ".cmd"}:
        return make_dependency_result(
            False,
            path,
            "",
            "path_invalid",
            "Media Player path should point to a Windows executable."
        )

    return make_dependency_result(
        True,
        path,
        "",
        "available",
        "Media Player executable path is valid."
    )


def _linux_xdg_data_dirs():
    """Returns a list of XDG data dirs to search for applications."""
    xdg_data_dirs = os.environ.get("XDG_DATA_DIRS", "").strip()
    dirs = [d for d in xdg_data_dirs.split(":") if d] if xdg_data_dirs else []
    if not dirs:
        dirs = ["/usr/local/share", "/usr/share"]
    # Also add flatpak user & system dirs
    home = os.path.expanduser("~")
    dirs += [
        os.path.join(home, ".local", "share"),
        "/var/lib/flatpak/exports/share",
        os.path.join(home, ".local", "share", "flatpak", "exports", "share"),
        "/snap",
    ]
    return dirs


def detect_installed_media_players():
    """Memindai lokasi instalasi standar media player di Windows/Linux/macOS untuk deteksi otomatis."""
    detected = []

    # --- Common Linux binary paths for VLC and MPV ---
    _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _bundled_vlc = [
        os.path.join(_app_root, "tools", "vlc.exe" if os.name == "nt" else "vlc"),
    ]
    _vlc_linux = [
        "/usr/bin/vlc",
        "/usr/local/bin/vlc",
        "/snap/bin/vlc",
        "/var/lib/flatpak/exports/bin/org.videolan.VLC",
        os.path.join(os.path.expanduser("~"), ".local", "share", "flatpak", "exports", "bin", "org.videolan.VLC"),
    ]
    _mpv_linux = [
        "/usr/bin/mpv",
        "/usr/local/bin/mpv",
        "/snap/bin/mpv",
        "/var/lib/flatpak/exports/bin/io.mpv.Mpv",
        os.path.join(os.path.expanduser("~"), ".local", "share", "flatpak", "exports", "bin", "io.mpv.Mpv"),
    ]

    candidates = [
        ("AniBase Built-in Player (VLC Compatible)", _bundled_vlc),
        ("VLC Media Player", [
            # Windows
            os.path.expandvars(r"%ProgramFiles%\VideoLAN\VLC\vlc.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\VideoLAN\VLC\vlc.exe"),
            # macOS
            "/Applications/VLC.app/Contents/MacOS/VLC",
        ] + _vlc_linux),
        ("MPV Player", [
            # Windows
            os.path.expandvars(r"%ProgramFiles%\mpv\mpv.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\mpv\mpv.exe"),
            r"C:\tools\mpv\mpv.exe",
            r"C:\mpv\mpv.exe",
            # macOS
            "/usr/local/bin/mpv",
            "/opt/homebrew/bin/mpv",
            "/Applications/mpv.app/Contents/MacOS/mpv",
        ] + _mpv_linux),
        ("MPC-HC / MPC-BE", [
            os.path.expandvars(r"%ProgramFiles%\MPC-HC\mpc-hc64.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\MPC-HC\mpc-hc.exe"),
            os.path.expandvars(r"%ProgramFiles%\MPC-BE x64\mpc-be64.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\MPC-BE\mpc-be.exe"),
        ]),
        ("PotPlayer", [
            os.path.expandvars(r"%ProgramFiles%\DAUM\PotPlayer\PotPlayer64.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\DAUM\PotPlayer\PotPlayer.exe"),
        ]),
        ("Celluloid (GNOME MPV)", [
            "/usr/bin/celluloid",
            "/usr/local/bin/celluloid",
            "/snap/bin/celluloid",
        ]),
        ("Haruna (KDE)", [
            "/usr/bin/haruna",
            "/usr/local/bin/haruna",
        ]),
        ("SMPlayer", [
            "/usr/bin/smplayer",
            "/usr/local/bin/smplayer",
            os.path.expandvars(r"%ProgramFiles%\SMPlayer\smplayer.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\SMPlayer\smplayer.exe"),
        ]),
    ]

    # Also try PATH lookup for the main players on Linux/macOS
    if os.name != "nt":
        for player_name, cmd in [("VLC Media Player", "vlc"), ("MPV Player", "mpv")]:
            which_path = shutil.which(cmd)
            if which_path:
                # Prepend to the right candidate list
                for name, paths in candidates:
                    if name == player_name and which_path not in paths:
                        paths.insert(0, which_path)

    seen = set()
    for name, paths in candidates:
        for p in paths:
            norm = os.path.normpath(p)
            if norm.lower() in seen:
                continue
            if os.path.isfile(p):
                seen.add(norm.lower())
                detected.append({
                    "name": name,
                    "path": norm,
                    "exists": True
                })
                break

    return detected
