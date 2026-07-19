#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
from datetime import datetime, time

from . import state
from .config import LIST_NAME, STATE_PATH, TODOIST_API_KEY
from .reminders_bridge import RemindersBridge
from .todoist_bridge import TodoistBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync_tasks")


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


def main() -> None:
    reminders = RemindersBridge(LIST_NAME)
    todoist = TodoistBridge(TODOIST_API_KEY)
    project_id = todoist.get_or_create_project(LIST_NAME)

    pairs = state.load_state(STATE_PATH)
    linked_reminder_ids = {p["reminder_id"] for p in pairs}
    linked_task_ids = {p["task_id"] for p in pairs}

    # 1. Propagate brand-new items sitting in the dedicated containers,
    #    including whatever due date/time each one already has. The pair's
    #    "due"/"all_day" fields record what we just synced, so reconciliation
    #    below has a correct baseline from the start.
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
            }
        )
        linked_task_ids.add(task_id)
        created_in_todoist += 1

    created_in_reminders = 0
    for t in todoist.get_active_tasks(project_id):
        if t.id in linked_task_ids:
            continue
        due_dt, all_day = _due_to_datetime(t.due)
        reminder_id = reminders.create_reminder(t.content, t.description or "", due_dt, all_day)
        pairs.append(
            {
                "reminder_id": reminder_id,
                "task_id": t.id,
                "due": _serialize_due(due_dt),
                "all_day": all_day,
            }
        )
        linked_reminder_ids.add(reminder_id)
        created_in_reminders += 1

    # 2. Reconcile already-linked pairs by id directly, regardless of which
    #    list/project they currently live in. Due-date conflicts are resolved
    #    against the last-synced value recorded on the pair (not wall-clock
    #    "last modified" timestamps) — otherwise our own writes to one side
    #    make that side look "newer" on the next run and its value keeps
    #    winning even when it's the stale one.
    completed_synced = 0
    due_synced = 0
    for pair in pairs:
        r = reminders.get_reminder(pair["reminder_id"])
        t = todoist.get_task(pair["task_id"])
        if r is None or t is None:
            continue

        todoist_done = t.completed_at is not None
        if r["completed"] and not todoist_done:
            todoist.complete_task(t.id)
            completed_synced += 1
        elif todoist_done and not r["completed"]:
            reminders.complete_reminder(r["id"])
            completed_synced += 1

        last_due = _deserialize_due(pair.get("due"))
        last_all_day = pair.get("all_day", False)
        r_due, r_all_day = r["due"], r["all_day"]
        t_due, t_all_day = _due_to_datetime(t.due)

        r_changed = not _due_equal(r_due, r_all_day, last_due, last_all_day)
        t_changed = not _due_equal(t_due, t_all_day, last_due, last_all_day)

        if r_changed and t_changed and not _due_equal(r_due, r_all_day, t_due, t_all_day):
            # Both sides changed to different values since the last sync —
            # genuine conflict. Reminders wins (simple, deterministic).
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

    log.info(
        "Sync complete: %d reminder(s) -> Todoist, %d task(s) -> Reminders, "
        "%d completion(s) synced, %d due date(s) synced",
        created_in_todoist,
        created_in_reminders,
        completed_synced,
        due_synced,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)
