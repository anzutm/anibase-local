import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import platform
import hashlib
import tempfile
from pathlib import Path


APP_NAME = "AniBase"
ENTRYPOINT = "tray_ui.py"
MIN_PYINSTALLER_VERSION = (6, 11, 0)
REQUIRED_BUILD_PYTHON_SERIES = (3, 12)
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
REQUIRED_ASSETS = (
    'templates/index.html', 'templates/anime.html', 'templates/player.html',
    'templates/setup.html', 'templates/setup_loading.html',
    'static/home-motion.js', 'static/home.css', 'static/player.js',
    'static/subtitles.js', 'static/subtitles.css',
    'static/plyr.js', 'static/plyr.css', 'static/plyr.svg',
    'static/vendor/subtitles-octopus/subtitles-octopus.js',
    'static/vendor/subtitles-octopus/subtitles-octopus-worker.js',
    'static/vendor/subtitles-octopus/subtitles-octopus-worker.wasm',
    'static/vendor/subtitles-octopus/InterVariable.woff2',
    'static/vendor/subtitles-octopus/default.woff2',
    'static/vendor/subtitles-octopus/LICENSE',
    'static/vendor/subtitles-octopus/COPYRIGHT',
    'static/vendor/subtitles-octopus/INTER-OFL.txt',
)


def validate_assets(resource_dir):
    missing = [name for name in REQUIRED_ASSETS
               if not (resource_dir / name).is_file() or (resource_dir / name).stat().st_size == 0]
    if missing:
        raise FileNotFoundError('Aset aplikasi hilang/kosong: ' + ', '.join(missing))
    # Catch failed downloads (HTML error pages saved with a binary extension).
    for name, signature in (
        ('static/vendor/subtitles-octopus/subtitles-octopus-worker.wasm', b'\x00asm'),
        ('static/vendor/subtitles-octopus/InterVariable.woff2', b'wOF2'),
        ('static/vendor/subtitles-octopus/default.woff2', b'wOF2'),
    ):
        with (resource_dir / name).open('rb') as handle:
            if handle.read(4) != signature:
                raise ValueError(f'Format aset tidak valid: {name}')


def validate_packaged_assets(release_dir, executable=True):
    resource_dir = release_dir / '_internal' if executable else release_dir
    validate_assets(resource_dir)
    for dirname in SOURCE_RELEASE_DIRS:
        for source in (PROJECT_ROOT / dirname).rglob('*'):
            if not source.is_file():
                continue
            target = resource_dir / source.relative_to(PROJECT_ROOT)
            if not target.is_file():
                raise FileNotFoundError(f'Aset tidak ikut paket: {source.relative_to(PROJECT_ROOT)}')
            with source.open('rb') as original, target.open('rb') as bundled:
                if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(bundled, 'sha256').digest():
                    raise ValueError(f'Aset paket berbeda dari source: {source.relative_to(PROJECT_ROOT)}')


def preflight(source_only=False):
    ensure_supported_build_python()
    for filename in SOURCE_RELEASE_FILES:
        if not (PROJECT_ROOT / filename).is_file():
            raise FileNotFoundError(f'File project wajib hilang: {filename}')
    validate_assets(PROJECT_ROOT)
    if not source_only:
        ensure_pyinstaller()
        find_media_tool('ffmpeg')
        find_media_tool('ffprobe')


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
        or any(ord(char) < 32 for char in normalized)
        or normalized.endswith('.')
    ):
        raise ValueError("Versi release mengandung karakter path Windows yang tidak aman.")
    return normalized


def remove_path(path):
    path = Path(path)
    resolved = path.resolve()
    roots = [root.resolve() for root in (BUILD_DIR, DIST_DIR, RELEASES_DIR)]
    if resolved in roots or not any(resolved.is_relative_to(root) for root in roots):
        raise ValueError(f'Menolak menghapus path di luar output AniBase: {path}')
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


def ensure_supported_build_python(version_info=None):
    version = tuple(version_info or sys.version_info[:3])
    if version[:2] == REQUIRED_BUILD_PYTHON_SERIES:
        return

    raise RuntimeError(
        f"Build AniBase memerlukan Python 3.12.x; versi aktif adalah "
        f"{format_version(version)}. Buat ulang .venv dengan Python 3.12, "
        "lalu install ulang requirements."
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


def run_pyinstaller():
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--contents-directory", "_internal",
        "--workpath", str(BUILD_DIR / APP_NAME),
        "--specpath", str(BUILD_DIR / APP_NAME),
        "--distpath", str(DIST_DIR),
        "--name",
        APP_NAME,
        "--add-data",
        f"{PROJECT_ROOT / 'templates'}{os.pathsep}templates",
        "--add-data",
        f"{PROJECT_ROOT / 'static'}{os.pathsep}static",
        str(PROJECT_ROOT / ENTRYPOINT),
    ]

    icon_path = PROJECT_ROOT / "static" / "favicon.ico"
    if icon_path.exists():
        command[-1:-1] = [
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
    # Shared Windows FFmpeg builds need the DLLs shipped alongside their executables.
    if os.name == 'nt':
        for directory in {ffmpeg.parent, ffprobe.parent}:
            for library in directory.glob('*.dll'):
                target = tools_dir / library.name
                if target.exists() and target.read_bytes() != library.read_bytes():
                    raise RuntimeError(f'FFmpeg/FFprobe memakai DLL yang berbeda: {library.name}')
                shutil.copy2(library, target)

    license_candidates = (
        ffmpeg.parent.parent / "LICENSE",
        ffmpeg.parent / "LICENSE",
        ffmpeg.parent.parent / "LICENSE.txt",
        ffmpeg.parent / "LICENSE.txt",
        ffmpeg.parent.parent / "COPYING.GPLv3",
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
        "  py -3.12 -m venv .venv\n"
        "  if errorlevel 1 exit /b 1\n"
        ")\n"
        "call .venv\\Scripts\\activate.bat\n"
        "if errorlevel 1 exit /b 1\n"
        "python -m pip install -r requirements.txt\n"
        "if errorlevel 1 exit /b 1\n"
        "python main.py\n",
    )

    write_text(
        release_dir / "run_tray.bat",
        "@echo off\n"
        "setlocal\n"
        "cd /d \"%~dp0\"\n"
        "if not exist .venv (\n"
        "  py -3.12 -m venv .venv\n"
        "  if errorlevel 1 exit /b 1\n"
        ")\n"
        "call .venv\\Scripts\\activate.bat\n"
        "if errorlevel 1 exit /b 1\n"
        "python -m pip install -r requirements.txt\n"
        "if errorlevel 1 exit /b 1\n"
        "pythonw tray_ui.py\n",
    )


def build_exe_release(release_dir):
    preflight()

    remove_path(BUILD_DIR / APP_NAME)
    remove_path(DIST_DIR / APP_NAME)

    run_pyinstaller()

    built_app_dir = DIST_DIR / APP_NAME
    if not built_app_dir.exists():
        raise FileNotFoundError(f"Hasil build tidak ditemukan: {built_app_dir}")

    copy_exe_release_files(built_app_dir, release_dir)
    bundle_media_tools(release_dir)
    validate_packaged_assets(release_dir)
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--source-only",
        action="store_true",
        help="Buat release source siap jalan tanpa PyInstaller.",
    )
    mode.add_argument(
        "--exe-only",
        action="store_true",
        help="Wajib build executable; jangan fallback ke source release.",
    )
    parser.add_argument('--check', action='store_true',
                        help='Periksa syarat build dan aset saja; tidak membuat atau menghapus output.')
    args = parser.parse_args()

    ensure_supported_build_python()
    version = normalize_version(args.version)
    release_dir = RELEASES_DIR / f"{APP_NAME} {version}"

    if args.check:
        preflight(source_only=args.source_only)
        print('Pemeriksaan berhasil. Tidak ada build atau perubahan output.')
        return 0

    # Validate shared inputs before starting a build or replacing an existing release.
    preflight(source_only=True)

    if not (PROJECT_ROOT / ENTRYPOINT).exists():
        raise FileNotFoundError(f"Entry point tidak ditemukan: {ENTRYPOINT}")

    print(f"Building {APP_NAME} {version}...")
    built_exe = False
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.anibase-staging-', dir=RELEASES_DIR) as staging:
        staged_release = Path(staging) / APP_NAME
        if args.source_only:
            create_source_release(staged_release)
        else:
            try:
                built_exe = build_exe_release(staged_release)
            except Exception as error:
                print(f"Build executable gagal: {error}", file=sys.stderr)
                if args.exe_only:
                    return 1
                print("Membuat source release sebagai fallback...")
                create_source_release(staged_release)
        validate_packaged_assets(staged_release, executable=built_exe)
        # Keep the previous release until compilation, copying and validation succeed.
        remove_path(release_dir)
        staged_release.replace(release_dir)

    if not args.keep_temp:
        remove_path(BUILD_DIR / APP_NAME)
        remove_path(DIST_DIR / APP_NAME)

    release_type = "executable" if built_exe else "source"
    print(f"Build {release_type} selesai: {release_dir}")
    if built_exe:
        archive_path = create_release_archive(release_dir, version)
        print(f"Arsip portable selesai: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
