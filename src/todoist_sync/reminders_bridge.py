from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_BINARY = (
    Path(__file__).resolve().parent.parent.parent
    / "swift"
    / "reminders-bridge"
    / ".build"
    / "release"
    / "reminders-bridge"
)


class RemindersUnavailableError(RuntimeError):
    """Raised when the Reminders bridge can't run at all: EventKit access
    not granted, or the compiled helper binary is missing. Distinct from
    RuntimeError so callers can offer a specific, actionable notice instead
    of treating it as an unclassified sync failure."""


def _redact_args_for_log(args: tuple[str, ...]) -> str:
    # --body carries notes content — the most likely place for genuinely
    # sensitive text, and rarely needed to diagnose a bridge-command
    # failure — so log its size instead of the raw text. --name (title) is
    # left as-is: short, and needed to tell items apart in a trace.
    out = []
    redact_next = False
    for a in args:
        if redact_next:
            out.append(f"<{len(a)} chars>")
            redact_next = False
            continue
        out.append(a)
        redact_next = a == "--body"
    return " ".join(out)


def _run(*args: str) -> str:
    log.debug("reminders-bridge: %s", _redact_args_for_log(args))
    if not _BINARY.exists():
        raise RemindersUnavailableError(
            f"{_BINARY} not found — build it first: "
            f"(cd swift/reminders-bridge && swift build -c release)"
        )
    try:
        result = subprocess.run(
            [str(_BINARY), *args], capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        if "Reminders access not granted" in (e.stderr or ""):
            raise RemindersUnavailableError(
                "Reminders access not granted — check System Settings > "
                "Privacy & Security > Reminders."
            ) from e
        # CalledProcessError's own str() is just "Command '...' returned
        # non-zero exit status N" — the helper's actual error message is in
        # stderr and would otherwise never reach the log.
        log.error("reminders-bridge command failed: %s", (e.stderr or "").strip())
        raise
    return result.stdout.strip()


def _serialize_due(due_dt: datetime | None) -> str | None:
    return due_dt.replace(microsecond=0).isoformat() if due_dt else None


def _row_to_reminder(row: dict) -> dict:
    due = row.get("due")
    return {
        "id": row["id"],
        "name": row["name"],
        "body": row.get("body") or "",
        "completed": bool(row.get("completed")),
        "due": datetime.fromisoformat(due) if due else None,
        "all_day": bool(row.get("allDay")),
    }


class RemindersBridge:
    """Talks to macOS Reminders.app via a compiled Swift/EventKit helper.

    EventKit provides direct, reliable id-based lookups and unambiguous
    all-day/timed due-date semantics — unlike Reminders.app's AppleScript
    dictionary, which proved unreliable for direct addressing across
    repeated testing (see swift/reminders-bridge and git history).
    """

    def __init__(self, list_name: str):
        self.list_name = list_name
        # Touch the list once so it's created if missing.
        _run("get-reminders", "--list", list_name)

    def get_reminders(self) -> list[dict]:
        rows = json.loads(_run("get-reminders", "--list", self.list_name))
        return [_row_to_reminder(row) for row in rows]

    def get_reminder(self, reminder_id: str) -> dict | None:
        row = json.loads(_run("get-reminder", "--list", self.list_name, "--id", reminder_id))
        return _row_to_reminder(row) if row else None

    def create_reminder(self, name: str, body: str, due_dt: datetime | None = None, all_day: bool = False) -> str:
        args = ["create-reminder", "--list", self.list_name, "--name", name, "--body", body]
        due = _serialize_due(due_dt)
        if due is not None:
            args += ["--due", due]
            if all_day:
                args.append("--all-day")
        return json.loads(_run(*args))["id"]

    def set_body(self, reminder_id: str, body: str) -> bool:
        return json.loads(_run("set-body", "--list", self.list_name, "--id", reminder_id, "--body", body))["ok"]

    def set_name(self, reminder_id: str, name: str) -> bool:
        return json.loads(_run("set-name", "--list", self.list_name, "--id", reminder_id, "--name", name))["ok"]

    def complete_reminder(self, reminder_id: str) -> bool:
        return json.loads(_run("complete", "--list", self.list_name, "--id", reminder_id))["ok"]

    def uncomplete_reminder(self, reminder_id: str) -> bool:
        return json.loads(_run("uncomplete", "--list", self.list_name, "--id", reminder_id))["ok"]

    def set_due_date(self, reminder_id: str, due_dt: datetime, all_day: bool) -> bool:
        args = ["set-due", "--list", self.list_name, "--id", reminder_id, "--due", _serialize_due(due_dt)]
        if all_day:
            args.append("--all-day")
        return json.loads(_run(*args))["ok"]
