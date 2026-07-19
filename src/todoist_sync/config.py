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

TODOIST_API_KEY = os.environ["TODOIST_API_KEY"]
LIST_NAME = os.environ.get("SYNC_LIST_NAME", "Siri Sync")

VAR_DIR = PROJECT_ROOT / "var"
VAR_DIR.mkdir(exist_ok=True)
VAR_DIR.chmod(0o700)
STATE_PATH = VAR_DIR / "state.json"
LOG_OUT_PATH = VAR_DIR / "sync-out.log"
LOG_ERROR_PATH = VAR_DIR / "sync-error.log"
