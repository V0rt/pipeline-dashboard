#!/bin/bash
set -e

# Pipeline Dashboard — run script
# Usage: ./run.sh              # start on :8800
#        ./run.sh --port 8888  # custom port

PORT="${1:-8800}"
DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$DIR"

# Ensure deps
if ! python3 -c "import fastapi" 2>/dev/null; then
    pip install -r requirements.txt
fi

exec python3 server.py --port "$PORT"