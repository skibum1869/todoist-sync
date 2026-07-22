#!/bin/bash
# Installs/uninstalls/updates the todoist-sync LaunchAgent for the current user.
# Safe to re-run — reinstalls/reloads if already installed.
#
# Usage:
#   ./deploy/install.sh              install and load (also the rebuild/reload step below)
#   ./deploy/install.sh --update     pull latest code from git; run again afterward to rebuild/reload
#   ./deploy/install.sh --upgrade    explicit alias for the default install/reload path
#   ./deploy/install.sh --uninstall  unload and remove
#
# --update and --upgrade are deliberately separate: --update only ever pulls
# code, it never touches requirements.txt's pinned dependency versions —
# those are pinned on purpose and only change when someone edits the file
# by hand.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.todoist-sync.local"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
WAKE_LABEL="com.todoist-sync.wake-watcher.local"
WAKE_PLIST_PATH="$HOME/Library/LaunchAgents/${WAKE_LABEL}.plist"

case "${1:-}" in
    --uninstall)
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
        launchctl unload "$WAKE_PLIST_PATH" 2>/dev/null || true
        rm -f "$WAKE_PLIST_PATH"
        echo "Uninstalled: $LABEL, $WAKE_LABEL"
        exit 0
        ;;
    --update)
        if [ -n "$(cd "$PROJECT_ROOT" && git status --porcelain)" ]; then
            echo "error: uncommitted changes in $PROJECT_ROOT — commit or stash before updating." >&2
            exit 1
        fi
        echo "Pulling latest code..."
        (cd "$PROJECT_ROOT" && git pull)
        echo "Code updated. Run ./deploy/install.sh (or --upgrade) to rebuild and reload with it."
        exit 0
        ;;
    "" | --upgrade)
        ;;
    *)
        echo "error: unknown argument '${1}'" >&2
        echo "Usage: $0 [--update|--upgrade|--uninstall]" >&2
        exit 1
        ;;
esac

if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    echo "Creating virtualenv..."
    python3 -m venv "$PROJECT_ROOT/.venv"
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

echo "Installing Python dependencies..."
"$PROJECT_ROOT/.venv/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"
"$PROJECT_ROOT/.venv/bin/pip" install -e "$PROJECT_ROOT" --no-deps

echo "Building reminders-bridge (Swift/EventKit helper)..."
(cd "$PROJECT_ROOT/swift/reminders-bridge" && swift build -c release)
codesign -s - "$PROJECT_ROOT/swift/reminders-bridge/.build/release/reminders-bridge" 2>/dev/null || true

echo "Building wake-watcher (Swift/NSWorkspace helper)..."
(cd "$PROJECT_ROOT/swift/wake-watcher" && swift build -c release)
codesign -s - "$PROJECT_ROOT/swift/wake-watcher/.build/release/wake-watcher" 2>/dev/null || true

chmod +x "$PROJECT_ROOT/deploy/todoist-sync"
codesign -s - "$PROJECT_ROOT/deploy/todoist-sync" 2>/dev/null || true

mkdir -p "$PROJECT_ROOT/var"

sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__LABEL__|$LABEL|g" \
    "$PROJECT_ROOT/deploy/com.todoist-sync.plist.template" > "$PLIST_PATH"

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

# Wake trigger: StartInterval alone only catches up some time after the Mac
# wakes, not immediately. wake-watcher is a small always-running LaunchAgent
# that observes NSWorkspace.didWakeNotification and fires a sync ~10s after
# wake, so pair it with the timer above rather than replacing it.
sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__LABEL__|$WAKE_LABEL|g" \
    "$PROJECT_ROOT/deploy/com.todoist-sync.wake-watcher.plist.template" > "$WAKE_PLIST_PATH"

launchctl unload "$WAKE_PLIST_PATH" 2>/dev/null || true
launchctl load "$WAKE_PLIST_PATH"

echo "Installed and loaded: $LABEL (runs every 15 minutes)"
echo "Installed and loaded: $WAKE_LABEL (syncs ~10s after wake from sleep)"
echo "Logs: $PROJECT_ROOT/var/sync.log"
echo "To uninstall: ./deploy/install.sh --uninstall"
echo
echo "Note: the first sync run will prompt macOS for Reminders access —"
echo "approve it in System Settings > Privacy & Security > Reminders."
