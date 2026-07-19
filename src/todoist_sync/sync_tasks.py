#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import logging
import logging.handlers
import subprocess
import sys
from datetime import datetime, time, timedelta

import httpx

from . import config, state
from . import __version__
from .config import (
    CONFLICT_WINNER,
    LIST_NAME,
    LOCK_PATH,
    LOG_ERROR_PATH,
    LOG_OUT_PATH,
    NETWORK_DOWN_MARKER,
    STATE_PATH,
    TODOIST_API_KEY,
)
from .reminders_bridge import RemindersBridge
from .todoist_bridge import TodoistBridge

_NETWORK_NOTIFY_THROTTLE = timedelta(hours=1)

_MAX_LOG_BYTES = 1_000_000  # 1 MB per file
_LOG_BACKUP_COUNT = 3


class _MaxLevelFilter(logging.Filter):
    """Excludes records at or above the given level, for the routine-log
    handler so it doesn't duplicate what the error log already has."""

    def __init__(self, below_level: int):
        super().__init__()
        self.below_level = below_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.below_level


def _configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # Write directly to the log files (rotating, capped) rather than through
    # stdout/stderr + launchd's StandardOutPath/StandardErrorPath, which had
    # no size limit and would grow forever.
    info_handler = logging.handlers.RotatingFileHandler(
        LOG_OUT_PATH, maxBytes=_MAX_LOG_BYTES, backupCount=_LOG_BACKUP_COUNT
    )
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(_MaxLevelFilter(logging.ERROR))
    info_handler.setFormatter(formatter)

    error_handler = logging.handlers.RotatingFileHandler(
        LOG_ERROR_PATH, maxBytes=_MAX_LOG_BYTES, backupCount=_LOG_BACKUP_COUNT
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(info_handler)
    root.addHandler(error_handler)


_configure_logging()
log = logging.getLogger("sync_tasks")


def _notify_macos(title: str, message: str) -> None:
    # AppleScript string literals: backslash and double-quote need escaping.
    def _escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    subprocess.run(["osascript", "-e", script], check=False)


def _notify_network_down() -> None:
    now = datetime.now()
    last = None
    if NETWORK_DOWN_MARKER.exists():
        try:
            last = datetime.fromisoformat(NETWORK_DOWN_MARKER.read_text().strip())
        except ValueError:
            last = None
    if last is None or now - last > _NETWORK_NOTIFY_THROTTLE:
        _notify_macos("Todoist Sync", "No internet connection — sync skipped.")
    NETWORK_DOWN_MARKER.write_text(now.isoformat())


def _clear_network_down_marker() -> None:
    if NETWORK_DOWN_MARKER.exists():
        NETWORK_DOWN_MARKER.unlink()


def _acquire_lock():
    """Prevents overlapping runs: the 15-minute timer and the wake-watcher
    are separate launchd jobs that can both invoke this script around the
    same time (e.g. the Mac wakes right as the timer also fires). Without
    this, two processes could read the same state.json, both create a task
    for the same new reminder, and race to write state.json back — silently
    duplicating items or losing pairs. flock is tied to the open fd, so it's
    released automatically even if the process crashes."""
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def _due_to_datetime(due) -> tuple[datetime, bool] | tuple[None, None]:
    if due is None:
        return None, None
    if isinstance(due.date, datetime):
        return due.date, False
    return datetime.combine(due.date, time.min), True


def _due_equal(a_due: datetime | None, a_all_day: bool, b_due: datetime | None, b_all_day: bool) -> bool:
    if a_due is None and b_due is None:
        return True
    if a_due is None or b_due is None:
        return False
    if a_all_day != b_all_day:
        return False
    if a_all_day:
        return a_due.date() == b_due.date()
    return a_due == b_due


def _serialize_due(due_dt: datetime | None) -> str | None:
    return due_dt.isoformat() if due_dt else None


def _deserialize_due(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _reconcile_scalar(last_value, r_value, t_value, set_todoist, set_reminders):
    """3-way merge for a simple equality-comparable field: propagate
    whichever side changed since last_value, or resolve a genuine conflict
    (both changed, to different values) per CONFLICT_WINNER. Returns
    (new_value, changed)."""
    r_changed = r_value != last_value
    t_changed = t_value != last_value

    if not r_changed and not t_changed:
        return last_value, False

    if r_changed and t_changed and r_value != t_value:
        winner = r_value if CONFLICT_WINNER == "reminders" else t_value
    elif r_changed:
        winner = r_value
    else:
        winner = t_value

    if winner != t_value:
        set_todoist(winner)
    if winner != r_value:
        set_reminders(winner)
    return winner, True


def main() -> None:
    log.info("todoist-sync v%s starting", __version__)
    config.validate()
    reminders = RemindersBridge(LIST_NAME)
    todoist = TodoistBridge(TODOIST_API_KEY)
    project_id = todoist.get_or_create_project(LIST_NAME)

    pairs = state.load_state(STATE_PATH)
    linked_reminder_ids = {p["reminder_id"] for p in pairs}
    linked_task_ids = {p["task_id"] for p in pairs}

    # 1. Propagate brand-new items sitting in the dedicated containers,
    #    including whatever due date/time each one already has. The pair's
    #    fields record what we just synced, so reconciliation below has a
    #    correct baseline from the start.
    created_in_todoist = 0
    for r in reminders.get_reminders():
        if r["id"] in linked_reminder_ids:
            continue
        task_id = todoist.create_task(project_id, r["name"], r["body"], r["due"], r["all_day"])
        if r["completed"]:
            todoist.complete_task(task_id)
        pairs.append(
            {
                "reminder_id": r["id"],
                "task_id": task_id,
                "due": _serialize_due(r["due"]),
                "all_day": r["all_day"],
                "completed": r["completed"],
                "name": r["name"],
                "body": r["body"],
            }
        )
        linked_task_ids.add(task_id)
        created_in_todoist += 1
        # Persist immediately: create_task() is not idempotent, so if a
        # later item in this loop fails (e.g. connectivity drops mid-run),
        # an un-persisted pair here would make this reminder look unlinked
        # again next run and get a duplicate task created for it.
        state.save_state(STATE_PATH, pairs)

    created_in_reminders = 0
    for t in todoist.get_active_tasks(project_id):
        if t.id in linked_task_ids:
            continue
        due_dt, all_day = _due_to_datetime(t.due)
        body = t.description or ""
        reminder_id = reminders.create_reminder(t.content, body, due_dt, all_day)
        pairs.append(
            {
                "reminder_id": reminder_id,
                "task_id": t.id,
                "due": _serialize_due(due_dt),
                "all_day": all_day,
                "completed": False,
                "name": t.content,
                "body": body,
            }
        )
        linked_reminder_ids.add(reminder_id)
        created_in_reminders += 1
        # Same rationale as above: create_reminder() is not idempotent.
        state.save_state(STATE_PATH, pairs)

    # 2. Reconcile already-linked pairs by id directly. Reminder lookups are
    #    scoped to LIST_NAME on the Swift side (not just addressed by global
    #    id) so a tampered or stale state.json entry can't read/mutate a
    #    reminder outside the intended list. Every field is resolved against
    #    the last-synced value recorded on the pair (not wall-clock "last
    #    modified" timestamps) — otherwise our own writes to one side make
    #    that side look "newer" on the next run and its value keeps winning
    #    even when it's the stale one. Genuine conflicts (both sides changed
    #    to different values since the last sync) resolve per
    #    CONFLICT_WINNER.
    completed_synced = 0
    due_synced = 0
    name_synced = 0
    notes_synced = 0
    for pair in pairs:
        r = reminders.get_reminder(pair["reminder_id"])
        t = todoist.get_task(pair["task_id"])
        if r is None or t is None:
            continue

        # Recurring Todoist tasks never actually get completed_at set —
        # "completing" one just advances its due date to the next
        # occurrence and leaves it structurally incomplete. Treating that
        # as a real completion signal would record a bogus "completed"
        # baseline that the next run reads as Todoist having reverted,
        # silently un-completing the reminder. Due-date reconciliation
        # below is what actually represents progress on these.
        if t.due is None or not t.due.is_recurring:
            new_completed, completed_changed = _reconcile_scalar(
                pair.get("completed", False),
                r["completed"],
                t.completed_at is not None,
                set_todoist=lambda v: (todoist.complete_task(t.id) if v else todoist.uncomplete_task(t.id)),
                set_reminders=lambda v: (
                    reminders.complete_reminder(r["id"]) if v else reminders.uncomplete_reminder(r["id"])
                ),
            )
            pair["completed"] = new_completed
            if completed_changed:
                completed_synced += 1

        new_name, name_changed = _reconcile_scalar(
            pair.get("name", r["name"]),
            r["name"],
            t.content,
            set_todoist=lambda v: todoist.set_task_content(t.id, v),
            set_reminders=lambda v: reminders.set_name(r["id"], v),
        )
        pair["name"] = new_name
        if name_changed:
            name_synced += 1

        new_body, body_changed = _reconcile_scalar(
            pair.get("body", r["body"]),
            r["body"],
            t.description or "",
            set_todoist=lambda v: todoist.set_task_description(t.id, v),
            set_reminders=lambda v: reminders.set_body(r["id"], v),
        )
        pair["body"] = new_body
        if body_changed:
            notes_synced += 1

        last_due = _deserialize_due(pair.get("due"))
        last_all_day = pair.get("all_day", False)
        r_due, r_all_day = r["due"], r["all_day"]
        t_due, t_all_day = _due_to_datetime(t.due)

        r_changed = not _due_equal(r_due, r_all_day, last_due, last_all_day)
        t_changed = not _due_equal(t_due, t_all_day, last_due, last_all_day)

        if r_changed and t_changed and not _due_equal(r_due, r_all_day, t_due, t_all_day):
            # Both sides changed to different values since the last sync —
            # genuine conflict, resolved per CONFLICT_WINNER.
            if CONFLICT_WINNER == "todoist":
                winning_due, winning_all_day = t_due, t_all_day
                if t_due is not None:
                    reminders.set_due_date(r["id"], t_due, t_all_day)
                    due_synced += 1
            else:
                winning_due, winning_all_day = r_due, r_all_day
                if r_due is not None:
                    todoist.set_task_due(t.id, r_due, r_all_day)
                    due_synced += 1
        elif r_changed and r_due is not None:
            todoist.set_task_due(t.id, r_due, r_all_day)
            due_synced += 1
            winning_due, winning_all_day = r_due, r_all_day
        elif t_changed and t_due is not None:
            reminders.set_due_date(r["id"], t_due, t_all_day)
            due_synced += 1
            winning_due, winning_all_day = t_due, t_all_day
        else:
            winning_due, winning_all_day = last_due, last_all_day

        pair["due"] = _serialize_due(winning_due)
        pair["all_day"] = winning_all_day

    state.save_state(STATE_PATH, pairs)
    _clear_network_down_marker()

    log.info(
        "Sync complete: %d reminder(s) -> Todoist, %d task(s) -> Reminders, "
        "%d completion(s) synced, %d due date(s) synced, %d title(s) synced, "
        "%d note(s) synced",
        created_in_todoist,
        created_in_reminders,
        completed_synced,
        due_synced,
        name_synced,
        notes_synced,
    )


if __name__ == "__main__":
    lock_file = _acquire_lock()
    if lock_file is None:
        log.info("Another sync is already running — skipping this run")
        sys.exit(0)
    try:
        main()
    except httpx.TransportError:
        # No response reached us at all (DNS failure, connection refused,
        # timed out, etc.) — treat this as "internet unavailable" rather
        # than a real sync bug, and surface it since this runs headless via
        # launchd with no other visible output.
        log.warning("Sync failed: no internet connection")
        _notify_network_down()
        sys.exit(1)
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)
    finally:
        lock_file.close()
