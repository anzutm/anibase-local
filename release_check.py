import py_compile
import subprocess
import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
RELEASES_DIR = PROJECT_ROOT / "releases"
APP_NAME = "AniBase"
EXE_NAME = "AniBase.exe" if os.name == "nt" else "AniBase"
REQUIRED_PROJECT_FILES = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")
TOOL_SUFFIX = ".exe" if os.name == "nt" else ""
REQUIRED_PORTABLE_TOOLS = (
    f"tools/ffmpeg{TOOL_SUFFIX}",
    f"tools/ffprobe{TOOL_SUFFIX}",
    "tools/FFMPEG_LICENSE.txt",
)
COMPILE_TARGETS = ("main.py", "tray_ui.py", "build.py")
PRIVATE_RUNTIME_FILES = {
    "settings.json",
    "library.db",
    "watch_history.json",
    "watch_status.json",
    "anibase.log",
}


def print_result(ok, label, detail=""):
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def run_step(label, func):
    try:
        detail = func()
    except Exception as error:
        print_result(False, label, str(error))
        return False

    print_result(True, label, detail)
    return True


def compile_targets():
    for filename in COMPILE_TARGETS:
        path = PROJECT_ROOT / filename
        if not path.exists():
            raise FileNotFoundError(f"{filename} tidak ditemukan")
        py_compile.compile(str(path), doraise=True)
    return ", ".join(COMPILE_TARGETS)


def run_unit_tests():
    tests_dir = PROJECT_ROOT / "tests"
    if not tests_dir.is_dir():
        raise FileNotFoundError("folder tests tidak ditemukan")

    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        output = (result.stdout or "").strip()
        if output:
            print(output)
        raise RuntimeError(f"unit test gagal dengan exit code {result.returncode}")
    return "unittest discover lulus"


def release_dirs():
    if not RELEASES_DIR.exists():
        return []
    return [
        path
        for path in RELEASES_DIR.iterdir()
        if path.is_dir() and path.name.startswith(f"{APP_NAME} v")
    ]


def latest_release_dir():
    candidates = release_dirs()
    if not candidates:
        raise FileNotFoundError(f"folder release tidak ditemukan di {RELEASES_DIR}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def check_latest_release_folder():
    release_dir = latest_release_dir()
    return str(release_dir.relative_to(PROJECT_ROOT))


def check_executable():
    release_dir = latest_release_dir()
    exe_path = release_dir / EXE_NAME
    if not exe_path.is_file():
        raise FileNotFoundError(f"{exe_path.relative_to(PROJECT_ROOT)} tidak ditemukan")
    return str(exe_path.relative_to(PROJECT_ROOT))


def check_no_private_runtime_files():
    release_dir = latest_release_dir()
    found = []
    for path in release_dir.rglob("*"):
        if path.is_file() and path.name in PRIVATE_RUNTIME_FILES:
            found.append(str(path.relative_to(PROJECT_ROOT)))
    if found:
        raise RuntimeError("file runtime pribadi ikut release: " + ", ".join(found))
    return "tidak ada settings/db/history/status/log runtime"


def check_required_release_files():
    release_dir = latest_release_dir()
    missing = [
        filename
        for filename in REQUIRED_PROJECT_FILES
        if not (release_dir / filename).is_file()
    ]
    if missing:
        raise FileNotFoundError("file release wajib hilang: " + ", ".join(missing))
    return ", ".join(REQUIRED_PROJECT_FILES)


def check_portable_media_tools():
    release_dir = latest_release_dir()
    missing = [
        filename for filename in REQUIRED_PORTABLE_TOOLS
        if not (release_dir / filename).is_file()
    ]
    if missing:
        raise FileNotFoundError("portable tool hilang: " + ", ".join(missing))
    return ", ".join(REQUIRED_PORTABLE_TOOLS)


def main():
    print("AniBase release check")
    print("=" * 22)

    checks = [
        ("py_compile", compile_targets),
        ("unit tests", run_unit_tests),
        ("latest release folder", check_latest_release_folder),
        (f"{EXE_NAME} exists", check_executable),
        ("portable FFmpeg/FFprobe exist", check_portable_media_tools),
        ("no private runtime files bundled", check_no_private_runtime_files),
        ("release docs/licenses exist", check_required_release_files),
    ]

    results = [run_step(label, func) for label, func in checks]
    passed = sum(1 for ok in results if ok)
    total = len(results)

    print("=" * 22)
    if all(results):
        print(f"SUMMARY: PASS ({passed}/{total}) - release aman untuk dicek sebelum GitHub Release.")
        return 0

    print(f"SUMMARY: FAIL ({passed}/{total}) - perbaiki item FAIL sebelum membuat GitHub Release.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
