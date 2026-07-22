import logging
from pathlib import Path

from dotenv import load_dotenv
import os

# src/todoist_sync/config.py -> project root is three levels up
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / "config.env"
load_dotenv(dotenv_path=ENV_PATH)

# config.env holds a live API key — re-assert owner-only permissions on
# every run in case it was ever created/restored with a looser default.
if ENV_PATH.exists():
    ENV_PATH.chmod(0o600)

LIST_NAME = os.environ.get("SYNC_LIST_NAME", "Siri Sync")

VAR_DIR = PROJECT_ROOT / "var"
VAR_DIR.mkdir(exist_ok=True)
VAR_DIR.chmod(0o700)
STATE_PATH = VAR_DIR / "state.json"
LOG_PATH = VAR_DIR / "sync.log"
NETWORK_DOWN_MARKER = VAR_DIR / "network-down-since"
AUTH_FAILURE_MARKER = VAR_DIR / "auth-failure-since"
REMINDERS_ACCESS_MARKER = VAR_DIR / "reminders-access-down-since"
LOCK_PATH = VAR_DIR / "sync.lock"

# TODOIST_API_KEY/CONFLICT_WINNER are read leniently here (no raise) and
# validated explicitly via validate(), which sync_tasks.py calls only after
# logging is configured. Raising here at import time — before any log
# handler exists — would send a bad config.env's traceback straight to
# stderr, which launchd doesn't capture, so it would vanish with no trace.
TODOIST_API_KEY = os.environ.get("TODOIST_API_KEY")
CONFLICT_WINNER = os.environ.get("SYNC_CONFLICT_WINNER", "reminders").strip().lower()

_ARCHIVE_AFTER_DAYS_RAW = os.environ.get("SYNC_ARCHIVE_AFTER_DAYS", "180")
try:
    ARCHIVE_AFTER_DAYS = int(_ARCHIVE_AFTER_DAYS_RAW)
except ValueError:
    ARCHIVE_AFTER_DAYS = None

_PRUNE_MISSING_AFTER_CHECKS_RAW = os.environ.get("SYNC_PRUNE_MISSING_AFTER_CHECKS", "4")
try:
    PRUNE_MISSING_AFTER_CHECKS = int(_PRUNE_MISSING_AFTER_CHECKS_RAW)
except ValueError:
    PRUNE_MISSING_AFTER_CHECKS = None

# INFO (default) is the terse "starting" + one-line summary. DEBUG adds
# per-query/per-pair detail (bridge calls, conflict resolutions, individual
# archive/prune actions) plus httpx's own request logging — off by default
# since it's meant for a temporary troubleshooting window, not routine use.
_LOG_LEVEL_RAW = os.environ.get("SYNC_LOG_LEVEL", "INFO").strip().upper()
LOG_LEVEL = getattr(logging, _LOG_LEVEL_RAW, None) if _LOG_LEVEL_RAW in ("DEBUG", "INFO", "WARNING", "ERROR") else None


def validate() -> None:
    if not TODOIST_API_KEY:
        raise RuntimeError("TODOIST_API_KEY is not set — check config.env")
    if CONFLICT_WINNER not in ("reminders", "todoist"):
        raise ValueError(
            f"SYNC_CONFLICT_WINNER must be 'reminders' or 'todoist', got {CONFLICT_WINNER!r}"
        )
    if ARCHIVE_AFTER_DAYS is None or ARCHIVE_AFTER_DAYS <= 0:
        raise ValueError(
            f"SYNC_ARCHIVE_AFTER_DAYS must be a positive integer, got {_ARCHIVE_AFTER_DAYS_RAW!r}"
        )
    if PRUNE_MISSING_AFTER_CHECKS is None or PRUNE_MISSING_AFTER_CHECKS <= 0:
        raise ValueError(
            f"SYNC_PRUNE_MISSING_AFTER_CHECKS must be a positive integer, got "
            f"{_PRUNE_MISSING_AFTER_CHECKS_RAW!r}"
        )
    if LOG_LEVEL is None:
        raise ValueError(
            f"SYNC_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, got {_LOG_LEVEL_RAW!r}"
        )
