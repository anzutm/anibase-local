#!/usr/bin/env bash
set -e
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f ".venv/bin/python" ]; then
    echo "Virtual environment not detected. Running ./setup_dev.sh..."
    chmod +x setup_dev.sh
    ./setup_dev.sh
fi

source .venv/bin/activate
export WEBKIT_DISABLE_DMABUF_RENDERER=1
export WEBKIT_GST_DMABUF_SINK_DISABLED=1
export PATH="$PROJECT_ROOT/tools:$PATH"
export GDK_BACKEND=wayland,x11
python tray_ui.py "$@"
