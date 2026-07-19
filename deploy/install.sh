#!/bin/bash
# Installs/uninstalls the todoist-sync LaunchAgent for the current user.
# Safe to re-run — reinstalls/reloads if already installed.
#
# Usage:
#   ./deploy/install.sh              install and load
#   ./deploy/install.sh --uninstall  unload and remove
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.todoist-sync.local"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [ "${1:-}" = "--uninstall" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "Uninstalled: $LABEL"
    exit 0
fi

if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    echo "error: $PROJECT_ROOT/.venv not found." >&2
    echo "Run the Setup steps in README.md first (venv + pip install)." >&2
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/config.env" ]; then
    echo "error: $PROJECT_ROOT/config.env not found." >&2
    echo "Copy config.env.example to config.env and set TODOIST_API_KEY first." >&2
    exit 1
fi

chmod +x "$PROJECT_ROOT/deploy/todoist-sync"
codesign -s - "$PROJECT_ROOT/deploy/todoist-sync" 2>/dev/null || true

sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__LABEL__|$LABEL|g" \
    "$PROJECT_ROOT/deploy/com.todoist-sync.plist.template" > "$PLIST_PATH"

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "Installed and loaded: $LABEL (runs every 15 minutes)"
echo "Logs: $PROJECT_ROOT/sync-out.log / sync-error.log"
echo "To uninstall: ./deploy/install.sh --uninstall"
