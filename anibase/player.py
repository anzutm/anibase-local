# anibase/player.py
"""Manajemen eksternal player (VLC dsb) dan diagnostik path eksekutabel."""

import os
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


def detect_installed_media_players():
    """Memindai lokasi instalasi standar media player di Windows/OS untuk deteksi otomatis."""
    detected = []

    candidates = [
        ("VLC Media Player", [
            os.path.expandvars(r"%ProgramFiles%\VideoLAN\VLC\vlc.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\VideoLAN\VLC\vlc.exe"),
            "/usr/bin/vlc",
            "/Applications/VLC.app/Contents/MacOS/VLC",
        ]),
        ("MPV Player", [
            os.path.expandvars(r"%ProgramFiles%\mpv\mpv.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\mpv\mpv.exe"),
            r"C:\tools\mpv\mpv.exe",
            r"C:\mpv\mpv.exe",
            "/usr/bin/mpv",
            "/usr/local/bin/mpv",
        ]),
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
    ]

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
