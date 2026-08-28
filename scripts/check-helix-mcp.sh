#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "Helix MCP virtual environment is not available." >&2
    exit 2
fi

cd "$PROJECT_DIR"
exec "$PYTHON" -m helix_mcp.operations.cli "$@"
