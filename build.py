import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import platform
from pathlib import Path


APP_NAME = "AniBase"
ENTRYPOINT = "tray_ui.py"
MIN_PYINSTALLER_VERSION = (6, 11, 0)
UNSUPPORTED_BUILD_PYTHON_VERSIONS = {
    (3, 10, 0): "Python 3.10.0 dapat membuat PyInstaller crash saat menganalisis bytecode.",
}
PROJECT_ROOT = Path(__file__).resolve().parent
RELEASES_DIR = PROJECT_ROOT / "releases"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
SOURCE_RELEASE_FILES = (
    "main.py",
    "tray_ui.py",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)
SOURCE_RELEASE_DIRS = ("templates", "static")


def default_version():
    today = dt.date.today()
    return f"v{today:%Y.%m.%d}"


def normalize_version(version):
    version = (version or "").strip()
    if not version:
        return default_version()
    normalized = version if version.lower().startswith("v") else f"v{version}"
    if (
        normalized in {".", ".."}
        or ".." in normalized
        or re.search(r'[<>:"/\\|?*]', normalized)
    ):
        raise ValueError("Versi release mengandung karakter path Windows yang tidak aman.")
    return normalized


def remove_path(path):
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def parse_version_tuple(version):
    match = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", version or "")
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def format_version(version_tuple):
    return ".".join(str(part) for part in version_tuple)


def ensure_supported_build_python():
    version = sys.version_info[:3]
    reason = UNSUPPORTED_BUILD_PYTHON_VERSIONS.get(version)
    if not reason:
        return

    raise RuntimeError(
        f"{reason} Gunakan Python 3.10.11+ atau Python 3.11/3.12, "
        "buat ulang .venv, lalu install ulang requirements."
    )


def ensure_pyinstaller():
    try:
        import PyInstaller
    except ImportError as error:
        raise RuntimeError(
            "PyInstaller belum terinstall. "
            "Install dulu dengan: python -m pip install -U "
            f"\"PyInstaller>={format_version(MIN_PYINSTALLER_VERSION)},<7.0\""
        ) from error

    installed_version = getattr(PyInstaller, "__version__", "")
    if parse_version_tuple(installed_version) < MIN_PYINSTALLER_VERSION:
        raise RuntimeError(
            f"PyInstaller {installed_version or 'unknown'} terlalu lama untuk build PySide6. "
            "Upgrade dulu dengan: python -m pip install -U "
            f"\"PyInstaller>={format_version(MIN_PYINSTALLER_VERSION)},<7.0\""
        )


def pyinstaller_data_arg(path):
    return f"{path}{os.pathsep}{path}"


def run_pyinstaller():
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        APP_NAME,
        "--add-data",
        pyinstaller_data_arg("templates"),
        "--add-data",
        pyinstaller_data_arg("static"),
        ENTRYPOINT,
    ]

    icon_path = PROJECT_ROOT / "static" / "favicon.ico"
    if icon_path.exists():
        command[command.index(ENTRYPOINT):command.index(ENTRYPOINT)] = [
            "--icon",
            str(icon_path),
        ]

    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def find_media_tool(name):
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(
            f"{name} tidak ditemukan di PATH mesin build. "
            "Install FFmpeg pada mesin build sebelum membuat release portable."
        )
    return Path(path).resolve()


def bundle_media_tools(release_dir):
    tools_dir = release_dir / "tools"
    tools_dir.mkdir(exist_ok=True)

    ffmpeg = find_media_tool("ffmpeg")
    ffprobe = find_media_tool("ffprobe")
    suffix = ".exe" if os.name == "nt" else ""
    shutil.copy2(ffmpeg, tools_dir / f"ffmpeg{suffix}")
    shutil.copy2(ffprobe, tools_dir / f"ffprobe{suffix}")

    license_candidates = (
        ffmpeg.parent.parent / "LICENSE",
        Path("/usr/share/doc/ffmpeg/copyright"),
    )
    license_path = next((path for path in license_candidates if path.is_file()), None)
    if license_path:
        shutil.copy2(license_path, tools_dir / "FFMPEG_LICENSE.txt")
    else:
        write_text(
            tools_dir / "FFMPEG_LICENSE.txt",
            "FFmpeg licensing information: https://ffmpeg.org/legal.html\n",
        )


def copy_exe_release_files(source_dir, release_dir):
    remove_path(release_dir)
    release_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, release_dir)

    for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "requirements.txt"):
        source = PROJECT_ROOT / filename
        if source.exists():
            shutil.copy2(source, release_dir / filename)


def write_text(path, content):
    path.write_text(content, encoding="utf-8", newline="\r\n")


def create_source_release(release_dir):
    remove_path(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    for filename in SOURCE_RELEASE_FILES:
        source = PROJECT_ROOT / filename
        if source.exists():
            shutil.copy2(source, release_dir / filename)

    for dirname in SOURCE_RELEASE_DIRS:
        source = PROJECT_ROOT / dirname
        if source.exists():
            shutil.copytree(source, release_dir / dirname)

    cache_dir = release_dir / "cache"
    cache_dir.mkdir(exist_ok=True)
    write_text(cache_dir / ".gitkeep", "")

    write_text(
        release_dir / "run_server.bat",
        "@echo off\n"
        "setlocal\n"
        "cd /d \"%~dp0\"\n"
        "if not exist .venv (\n"
        "  py -3 -m venv .venv\n"
        ")\n"
        "call .venv\\Scripts\\activate.bat\n"
        "python -m pip install -r requirements.txt\n"
        "python main.py\n",
    )

    write_text(
        release_dir / "run_tray.bat",
        "@echo off\n"
        "setlocal\n"
        "cd /d \"%~dp0\"\n"
        "if not exist .venv (\n"
        "  py -3 -m venv .venv\n"
        ")\n"
        "call .venv\\Scripts\\activate.bat\n"
        "python -m pip install -r requirements.txt\n"
        "pythonw tray_ui.py\n",
    )


def build_exe_release(release_dir):
    ensure_supported_build_python()
    ensure_pyinstaller()

    remove_path(BUILD_DIR)
    remove_path(DIST_DIR)

    run_pyinstaller()

    built_app_dir = DIST_DIR / APP_NAME
    if not built_app_dir.exists():
        raise FileNotFoundError(f"Hasil build tidak ditemukan: {built_app_dir}")

    copy_exe_release_files(built_app_dir, release_dir)
    bundle_media_tools(release_dir)
    return True


def platform_tag():
    machine = platform.machine().lower()
    arch = "x86_64" if machine in {"amd64", "x86_64"} else machine
    return "win64" if os.name == "nt" and arch == "x86_64" else (
        f"win-{arch}" if os.name == "nt" else f"linux-{arch}"
    )


def create_release_archive(release_dir, version):
    base = RELEASES_DIR / f"{APP_NAME}-{version}-{platform_tag()}"
    if os.name == "nt":
        archive_path = Path(f"{base}.zip")
        remove_path(archive_path)
        shutil.make_archive(str(base), "zip", root_dir=release_dir)
    else:
        archive_path = Path(f"{base}.tar.gz")
        remove_path(archive_path)
        shutil.make_archive(str(base), "gztar", root_dir=release_dir)
    return archive_path


def main():
    parser = argparse.ArgumentParser(
        description="Build AniBase dan simpan hasilnya ke folder releases."
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="Versi release, contoh: 1.0.0 atau v1.0.0. Default: vYYYY.MM.DD.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Jangan hapus folder build/dist sementara setelah selesai.",
    )
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Buat release source siap jalan tanpa PyInstaller.",
    )
    parser.add_argument(
        "--exe-only",
        action="store_true",
        help="Wajib build executable; jangan fallback ke source release.",
    )
    args = parser.parse_args()

    version = normalize_version(args.version)
    release_dir = RELEASES_DIR / f"{APP_NAME} {version}"

    if not (PROJECT_ROOT / ENTRYPOINT).exists():
        raise FileNotFoundError(f"Entry point tidak ditemukan: {ENTRYPOINT}")

    print(f"Building {APP_NAME} {version}...")
    built_exe = False
    if args.source_only:
        create_source_release(release_dir)
    else:
        try:
            built_exe = build_exe_release(release_dir)
        except Exception as error:
            print(f"Build executable gagal: {error}", file=sys.stderr)
            if args.exe_only:
                return 1
            print("Membuat source release sebagai fallback...")
            create_source_release(release_dir)

    if not args.keep_temp:
        remove_path(BUILD_DIR)
        remove_path(DIST_DIR)
        remove_path(PROJECT_ROOT / f"{APP_NAME}.spec")

    release_type = "executable" if built_exe else "source"
    print(f"Build {release_type} selesai: {release_dir}")
    if built_exe:
        archive_path = create_release_archive(release_dir, version)
        print(f"Arsip portable selesai: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
