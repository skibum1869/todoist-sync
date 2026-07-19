from pathlib import Path

from dotenv import load_dotenv
import os

ENV_PATH = Path(__file__).resolve().parent / "config.env"
load_dotenv(dotenv_path=ENV_PATH)

TODOIST_API_KEY = os.environ["TODOIST_API_KEY"]
LIST_NAME = os.environ.get("SYNC_LIST_NAME", "Siri Sync")
LOOKBACK_DAYS = int(os.environ.get("SYNC_LOOKBACK_DAYS", "30"))
