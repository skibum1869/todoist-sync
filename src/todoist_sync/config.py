from pathlib import Path

from dotenv import load_dotenv
import os

# src/todoist_sync/config.py -> project root is three levels up
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / "config.env"
load_dotenv(dotenv_path=ENV_PATH)

TODOIST_API_KEY = os.environ["TODOIST_API_KEY"]
LIST_NAME = os.environ.get("SYNC_LIST_NAME", "Siri Sync")
STATE_PATH = PROJECT_ROOT / ".sync_state.json"
