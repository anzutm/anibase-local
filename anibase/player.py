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
