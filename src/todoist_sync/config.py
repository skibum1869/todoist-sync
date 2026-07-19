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
LOG_OUT_PATH = VAR_DIR / "sync-out.log"
LOG_ERROR_PATH = VAR_DIR / "sync-error.log"
NETWORK_DOWN_MARKER = VAR_DIR / "network-down-since"
LOCK_PATH = VAR_DIR / "sync.lock"

# TODOIST_API_KEY/CONFLICT_WINNER are read leniently here (no raise) and
# validated explicitly via validate(), which sync_tasks.py calls only after
# logging is configured. Raising here at import time — before any log
# handler exists — would send a bad config.env's traceback straight to
# stderr, which launchd doesn't capture, so it would vanish with no trace.
TODOIST_API_KEY = os.environ.get("TODOIST_API_KEY")
CONFLICT_WINNER = os.environ.get("SYNC_CONFLICT_WINNER", "reminders").strip().lower()


def validate() -> None:
    if not TODOIST_API_KEY:
        raise RuntimeError("TODOIST_API_KEY is not set — check config.env")
    if CONFLICT_WINNER not in ("reminders", "todoist"):
        raise ValueError(
            f"SYNC_CONFLICT_WINNER must be 'reminders' or 'todoist', got {CONFLICT_WINNER!r}"
        )
