#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys

import sync_id
from config import LIST_NAME, LOOKBACK_DAYS, TODOIST_API_KEY
from reminders_bridge import RemindersBridge
from todoist_bridge import TodoistBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync_tasks")


def main() -> None:
    reminders = RemindersBridge(LIST_NAME)
    todoist = TodoistBridge(TODOIST_API_KEY)
    project_id = todoist.get_or_create_project(LIST_NAME)

    all_reminders = reminders.get_reminders()
    active_tasks = todoist.get_active_tasks(project_id)
    completed_tasks = todoist.get_recently_completed_tasks(project_id, LOOKBACK_DAYS)

    reminders_by_id = {}
    reminders_unlinked = []
    for r in all_reminders:
        sid = sync_id.extract(r["body"])
        if sid:
            reminders_by_id[sid] = r
        else:
            reminders_unlinked.append(r)

    todoist_by_id = {}
    for t in [*active_tasks, *completed_tasks]:
        sid = sync_id.extract(t.description)
        if sid:
            todoist_by_id[sid] = t

    active_sync_ids = {sync_id.extract(t.description) for t in active_tasks}
    todoist_unlinked = [t for t in active_tasks if sync_id.extract(t.description) is None]

    created_in_todoist = 0
    for r in reminders_unlinked:
        sid = sync_id.new_id()
        reminders.set_body(r["id"], sync_id.append_tag(r["body"], sid))
        task_id = todoist.create_task(project_id, r["name"], sync_id.append_tag("", sid))
        if r["completed"]:
            todoist.complete_task(task_id)
        created_in_todoist += 1

    created_in_reminders = 0
    for t in todoist_unlinked:
        sid = sync_id.new_id()
        todoist.set_task_description(t.id, sync_id.append_tag(t.description, sid))
        reminders.create_reminder(t.content, sync_id.append_tag("", sid))
        created_in_reminders += 1

    completed_synced = 0
    for sid, r in reminders_by_id.items():
        t = todoist_by_id.get(sid)
        if not t:
            continue
        todoist_done = sid not in active_sync_ids
        if r["completed"] and not todoist_done:
            todoist.complete_task(t.id)
            completed_synced += 1
        elif todoist_done and not r["completed"]:
            reminders.complete_reminder(r["id"])
            completed_synced += 1

    log.info(
        "Sync complete: %d reminder(s) -> Todoist, %d task(s) -> Reminders, %d completion(s) synced",
        created_in_todoist,
        created_in_reminders,
        completed_synced,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)
