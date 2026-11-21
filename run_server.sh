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
python main.py "$@"
