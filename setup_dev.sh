#!/usr/bin/env bash
# ==============================================================================
# AniBase Developer Setup Script for Linux (CachyOS / Arch / Ubuntu / Fedora)
# ==============================================================================
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "=================================================="
echo "          AniBase Linux Developer Setup           "
echo "=================================================="

# Helper: check command availability
cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Ensure project bundled tools are executable and accessible on PATH
if [ -d "$PROJECT_ROOT/tools" ]; then
    chmod +x "$PROJECT_ROOT/tools/zenity" 2>/dev/null || true
    chmod +x "$PROJECT_ROOT/tools/vlc" 2>/dev/null || true
    export PATH="$PROJECT_ROOT/tools:$PATH"
fi

# ------------------------------------------------------------------------------
# [1/5] Detect Linux Distribution & System Dependencies
# ------------------------------------------------------------------------------
echo ""
echo "[1/5] Checking system dependencies..."

MISSING_DEPS=()
if ! cmd_exists ffmpeg; then MISSING_DEPS+=("ffmpeg"); fi
if ! cmd_exists ffprobe; then MISSING_DEPS+=("ffprobe"); fi
if ! cmd_exists zenity && ! cmd_exists kdialog; then MISSING_DEPS+=("zenity"); fi

# Check WebKitGTK & PyGObject for GTK desktop engine
HAS_GTK_WEBKIT=false
if python3 -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')" >/dev/null 2>&1; then
    HAS_GTK_WEBKIT=true
elif python3 -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.0')" >/dev/null 2>&1; then
    HAS_GTK_WEBKIT=true
fi

if [ "$HAS_GTK_WEBKIT" = false ]; then
    MISSING_DEPS+=("python-gobject" "webkit2gtk-4.1")
fi

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo "  Notice: The following system packages are recommended/missing:"
    echo "          ${MISSING_DEPS[*]}"
    echo ""
    if cmd_exists pacman; then
        echo "  [CachyOS / Arch Linux detected]"
        echo "  Install missing packages with:"
        echo "    sudo pacman -S --needed ffmpeg zenity python-gobject webkit2gtk-4.1 libnotify gnome-shell-extension-appindicator"
        echo ""
        if [ -t 0 ]; then
            read -r -p "  Would you like to install them now with sudo pacman? [Y/n] " response
            response=${response:-Y}
            if [[ "$response" =~ ^[Yy]$ ]]; then
                sudo pacman -S --needed ffmpeg zenity python-gobject webkit2gtk-4.1 libnotify
            fi
        fi
    elif cmd_exists apt-get; then
        echo "  [Debian / Ubuntu detected]"
        echo "  Install with: sudo apt-get install ffmpeg zenity python3-gi python3-gi-cairo gir1.2-webkit2-4.1 libnotify-bin"
    elif cmd_exists dnf; then
        echo "  [Fedora detected]"
        echo "  Install with: sudo dnf install ffmpeg zenity python3-gobject webkit2gtk4.1 libnotify"
    fi
else
    echo "  System dependencies (ffmpeg, zenity, WebKitGTK) are satisfied."
fi

# ------------------------------------------------------------------------------
# [2/5] Checking Python Version
# ------------------------------------------------------------------------------
echo ""
echo "[2/5] Checking Python interpreter..."

PYTHON_BIN=""
for candidate in python3.12 python3 python; do
    if cmd_exists "$candidate"; then
        VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        if [ "$VER" = "3.12" ]; then
            PYTHON_BIN="$candidate"
            break
        elif [ -z "$PYTHON_BIN" ] && "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "Error: Python 3.12.x was not found. Please install Python 3.12." >&2
    exit 1
fi

PY_FULL_VER=$("$PYTHON_BIN" -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
echo "  Found Python $PY_FULL_VER ($PYTHON_BIN)"

# ------------------------------------------------------------------------------
# [3/5] Setting up Virtual Environment (.venv)
# ------------------------------------------------------------------------------
echo ""
echo "[3/5] Setting up virtual environment..."

# Use --system-site-packages so system-installed PyGObject & WebKitGTK are accessible
if [ ! -d ".venv" ]; then
    echo "  Creating .venv with --system-site-packages..."
    "$PYTHON_BIN" -m venv --system-site-packages .venv
else
    echo "  Existing .venv found."
fi

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
if [ ! -f "$VENV_PY" ]; then
    echo "Error: Virtual environment python executable not found at $VENV_PY" >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# [4/5] Installing Python Dependencies
# ------------------------------------------------------------------------------
echo ""
echo "[4/5] Installing Python dependencies into .venv..."
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements.txt

# Ensure helper scripts are executable
chmod +x setup_dev.sh run_tray.sh run_server.sh 2>/dev/null || true

# ------------------------------------------------------------------------------
# [5/5] Validating Setup & Assets
# ------------------------------------------------------------------------------
echo ""
echo "[5/5] Validating AniBase prerequisites & assets..."
"$VENV_PY" -m pip check
if "$VENV_PY" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>/dev/null; then
    "$VENV_PY" build.py --check
else
    echo "  Notice: Python $PY_FULL_VER detected (standalone release packaging requires Python 3.12.x)."
    "$VENV_PY" -c "from build import validate_assets, PROJECT_ROOT; validate_assets(PROJECT_ROOT)"
    echo "  Checks passed. Static and template assets validated."
fi

echo ""
echo "=================================================="
echo "    AniBase Linux environment is ready!           "
echo "=================================================="
echo ""
echo "To start AniBase on CachyOS / Linux:"
echo "  1. Tray + Desktop UI (Recommended):"
echo "     ./run_tray.sh"
echo ""
echo "  2. Web Server only (Headless / Browser):"
echo "     ./run_server.sh"
echo ""
echo "  3. Manual activation:"
echo "     source .venv/bin/activate"
echo "     python tray_ui.py"
echo ""
echo "GNOME Note:"
echo "  On GNOME Shell, install and enable AppIndicator extension if"
echo "  you wish to see the tray icon in the top panel:"
echo "    sudo pacman -S gnome-shell-extension-appindicator"
echo "=================================================="
