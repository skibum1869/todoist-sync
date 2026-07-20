from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from todoist_sync import state as state_module
from todoist_sync import sync_tasks


@pytest.fixture(autouse=True, scope="session")
def _no_real_log_files():
    # sync_tasks._configure_logging() attaches real RotatingFileHandlers to
    # the root logger at import time (not inside main()), pointed at the
    # actual var/sync-out.log / var/sync-error.log. Without this, every
    # test that calls main() would append real log lines to those files.
    logging.getLogger().handlers.clear()


@dataclass
class FakeDue:
    date: datetime | date
    is_recurring: bool = False


@dataclass
class FakeTask:
    id: str
    content: str = "Task"
    description: str = ""
    due: FakeDue | None = None
    completed_at: datetime | None = None


@dataclass
class FakeStore:
    reminders: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    _next_reminder_id: int = 1
    _next_task_id: int = 1

    def add_reminder(self, name="Reminder", body="", completed=False, due=None, all_day=False, id=None):
        rid = id or f"r{self._next_reminder_id}"
        self._next_reminder_id += 1
        self.reminders[rid] = {
            "id": rid,
            "name": name,
            "body": body,
            "completed": completed,
            "due": due,
            "all_day": all_day,
        }
        return rid

    def add_task(self, content="Task", description="", due=None, completed=False, id=None):
        tid = id or f"t{self._next_task_id}"
        self._next_task_id += 1
        completed_at = datetime(2026, 1, 1) if completed else None
        self.tasks[tid] = FakeTask(tid, content, description, due, completed_at)
        return tid


class FakeRemindersBridge:
    def __init__(self, list_name):
        self.list_name = list_name

    def get_reminders(self):
        return [dict(r) for r in STORE.reminders.values()]

    def get_reminder(self, reminder_id):
        r = STORE.reminders.get(reminder_id)
        return dict(r) if r else None

    def create_reminder(self, name, body, due_dt=None, all_day=False):
        return STORE.add_reminder(name=name, body=body, due=due_dt, all_day=all_day)

    def set_body(self, reminder_id, body):
        STORE.reminders[reminder_id]["body"] = body
        return True

    def set_name(self, reminder_id, name):
        STORE.reminders[reminder_id]["name"] = name
        return True

    def complete_reminder(self, reminder_id):
        STORE.reminders[reminder_id]["completed"] = True
        return True

    def uncomplete_reminder(self, reminder_id):
        STORE.reminders[reminder_id]["completed"] = False
        return True

    def set_due_date(self, reminder_id, due_dt, all_day):
        STORE.reminders[reminder_id]["due"] = due_dt
        STORE.reminders[reminder_id]["all_day"] = all_day
        return True


class FakeTodoistBridge:
    def __init__(self, api_token):
        self.api_token = api_token

    def get_or_create_project(self, name):
        return "project1"

    def get_active_tasks(self, project_id):
        # Mirrors the real Todoist API: only ever returns open tasks.
        return [t for t in STORE.tasks.values() if t.completed_at is None]

    def get_task(self, task_id):
        return STORE.tasks.get(task_id)

    def create_task(self, project_id, content, description, due_dt=None, all_day=False):
        due = FakeDue(due_dt.date() if (due_dt and all_day) else due_dt) if due_dt else None
        return STORE.add_task(content=content, description=description, due=due)

    def set_task_due(self, task_id, due_dt, all_day):
        was_recurring = STORE.tasks[task_id].due.is_recurring if STORE.tasks[task_id].due else False
        STORE.tasks[task_id].due = FakeDue(due_dt.date() if all_day else due_dt, was_recurring)

    def set_task_content(self, task_id, content):
        STORE.tasks[task_id].content = content

    def set_task_description(self, task_id, description):
        STORE.tasks[task_id].description = description

    def complete_task(self, task_id):
        STORE.tasks[task_id].completed_at = datetime(2026, 1, 1)

    def uncomplete_task(self, task_id):
        STORE.tasks[task_id].completed_at = None


STORE = FakeStore()


@pytest.fixture
def env(tmp_path, monkeypatch):
    global STORE
    STORE = FakeStore()

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(sync_tasks, "RemindersBridge", FakeRemindersBridge)
    monkeypatch.setattr(sync_tasks, "TodoistBridge", FakeTodoistBridge)
    monkeypatch.setattr(sync_tasks, "STATE_PATH", state_path)
    monkeypatch.setattr(sync_tasks, "LIST_NAME", "Test List")
    monkeypatch.setattr(sync_tasks, "TODOIST_API_KEY", "fake-token")
    monkeypatch.setattr(sync_tasks, "CONFLICT_WINNER", "reminders")
    monkeypatch.setattr(sync_tasks, "ARCHIVE_AFTER_DAYS", 180)
    monkeypatch.setattr(sync_tasks, "PRUNE_MISSING_AFTER_CHECKS", 4)
    monkeypatch.setattr(sync_tasks, "_FAILURE_MARKERS", ())
    monkeypatch.setattr(sync_tasks.config, "validate", lambda: None)

    class Env:
        store = STORE

        def run(self):
            sync_tasks.main()

        def load_state(self):
            return state_module.load_state(state_path)

        def write_state(self, pairs, archive=None):
            state_module.save_state(state_path, pairs, archive or [])

    return Env()
