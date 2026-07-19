#!/bin/bash
# Installs/uninstalls/upgrades the todoist-sync LaunchAgent for the current user.
# Safe to re-run — reinstalls/reloads if already installed.
#
# Usage:
#   ./deploy/install.sh              install and load
#   ./deploy/install.sh --upgrade    pull latest code, update deps, then install and load
#   ./deploy/install.sh -U           same as --upgrade
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

UPGRADE=false
if [ "${1:-}" = "--upgrade" ] || [ "${1:-}" = "-U" ]; then
    UPGRADE=true
fi

if [ "$UPGRADE" = true ]; then
    if [ -n "$(cd "$PROJECT_ROOT" && git status --porcelain)" ]; then
        echo "error: uncommitted changes in $PROJECT_ROOT — commit or stash before upgrading." >&2
        exit 1
    fi
    echo "Pulling latest code..."
    (cd "$PROJECT_ROOT" && git pull)
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

if ! command -v swift >/dev/null 2>&1; then
    echo "error: swift not found — install Xcode Command Line Tools first:" >&2
    echo "  xcode-select --install" >&2
    exit 1
fi

if [ "$UPGRADE" = true ]; then
    echo "Updating Python dependencies..."
    "$PROJECT_ROOT/.venv/bin/pip" install -r "$PROJECT_ROOT/requirements.txt" --upgrade
    "$PROJECT_ROOT/.venv/bin/pip" install -e "$PROJECT_ROOT" --no-deps
fi

echo "Building reminders-bridge (Swift/EventKit helper)..."
(cd "$PROJECT_ROOT/swift/reminders-bridge" && swift build -c release)
codesign -s - "$PROJECT_ROOT/swift/reminders-bridge/.build/release/reminders-bridge" 2>/dev/null || true

chmod +x "$PROJECT_ROOT/deploy/todoist-sync"
codesign -s - "$PROJECT_ROOT/deploy/todoist-sync" 2>/dev/null || true

mkdir -p "$PROJECT_ROOT/var"

sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__LABEL__|$LABEL|g" \
    "$PROJECT_ROOT/deploy/com.todoist-sync.plist.template" > "$PLIST_PATH"

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "Installed and loaded: $LABEL (runs every 15 minutes)"
echo "Logs: $PROJECT_ROOT/var/sync-out.log / var/sync-error.log"
echo "To uninstall: ./deploy/install.sh --uninstall"
echo
echo "Note: the first sync run will prompt macOS for Reminders access —"
echo "approve it in System Settings > Privacy & Security > Reminders."
