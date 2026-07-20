from __future__ import annotations

from todoist_sync import sync_tasks


def _recording_setters():
    calls = {"todoist": [], "reminders": []}
    return calls, (lambda v: calls["todoist"].append(v)), (lambda v: calls["reminders"].append(v))


def test_no_change_returns_last_value_unchanged():
    calls, set_todoist, set_reminders = _recording_setters()

    value, changed = sync_tasks._reconcile_scalar("same", "same", "same", set_todoist, set_reminders)

    assert (value, changed) == ("same", False)
    assert calls == {"todoist": [], "reminders": []}


def test_only_reminder_changed_pushes_to_todoist():
    calls, set_todoist, set_reminders = _recording_setters()

    value, changed = sync_tasks._reconcile_scalar("old", "new", "old", set_todoist, set_reminders)

    assert (value, changed) == ("new", True)
    assert calls == {"todoist": ["new"], "reminders": []}


def test_only_todoist_changed_pushes_to_reminders():
    calls, set_todoist, set_reminders = _recording_setters()

    value, changed = sync_tasks._reconcile_scalar("old", "old", "new", set_todoist, set_reminders)

    assert (value, changed) == ("new", True)
    assert calls == {"todoist": [], "reminders": ["new"]}


def test_both_changed_to_same_value_needs_no_push():
    calls, set_todoist, set_reminders = _recording_setters()

    value, changed = sync_tasks._reconcile_scalar("old", "new", "new", set_todoist, set_reminders)

    assert (value, changed) == ("new", True)
    assert calls == {"todoist": [], "reminders": []}


def test_genuine_conflict_reminders_wins(monkeypatch):
    monkeypatch.setattr(sync_tasks, "CONFLICT_WINNER", "reminders")
    calls, set_todoist, set_reminders = _recording_setters()

    value, changed = sync_tasks._reconcile_scalar(
        "old", "from-reminders", "from-todoist", set_todoist, set_reminders
    )

    assert (value, changed) == ("from-reminders", True)
    assert calls == {"todoist": ["from-reminders"], "reminders": []}


def test_genuine_conflict_todoist_wins(monkeypatch):
    monkeypatch.setattr(sync_tasks, "CONFLICT_WINNER", "todoist")
    calls, set_todoist, set_reminders = _recording_setters()

    value, changed = sync_tasks._reconcile_scalar(
        "old", "from-reminders", "from-todoist", set_todoist, set_reminders
    )

    assert (value, changed) == ("from-todoist", True)
    assert calls == {"todoist": [], "reminders": ["from-todoist"]}
