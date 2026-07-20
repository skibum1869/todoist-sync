from __future__ import annotations

from datetime import datetime, timedelta

from tests.conftest import FakeDue


def test_new_reminder_creates_linked_task(env):
    rid = env.store.add_reminder(name="Buy milk")

    env.run()

    pairs, archive = env.load_state()
    assert len(pairs) == 1
    assert pairs[0]["reminder_id"] == rid
    tid = pairs[0]["task_id"]
    assert env.store.tasks[tid].content == "Buy milk"


def test_multiple_new_reminders_create_distinct_linked_tasks(env):
    r1 = env.store.add_reminder(name="First")
    r2 = env.store.add_reminder(name="Second")
    r3 = env.store.add_reminder(name="Third")

    env.run()

    pairs, _ = env.load_state()
    assert len(pairs) == 3
    task_id_by_reminder = {p["reminder_id"]: p["task_id"] for p in pairs}
    assert set(task_id_by_reminder) == {r1, r2, r3}
    # Each new task must be linked to the reminder it actually came from,
    # not mixed up with a different item created in the same pass.
    assert env.store.tasks[task_id_by_reminder[r1]].content == "First"
    assert env.store.tasks[task_id_by_reminder[r2]].content == "Second"
    assert env.store.tasks[task_id_by_reminder[r3]].content == "Third"


def test_multiple_new_tasks_create_distinct_linked_reminders(env):
    t1 = env.store.add_task(content="First")
    t2 = env.store.add_task(content="Second")
    t3 = env.store.add_task(content="Third")

    env.run()

    pairs, _ = env.load_state()
    assert len(pairs) == 3
    reminder_id_by_task = {p["task_id"]: p["reminder_id"] for p in pairs}
    assert set(reminder_id_by_task) == {t1, t2, t3}
    assert env.store.reminders[reminder_id_by_task[t1]]["name"] == "First"
    assert env.store.reminders[reminder_id_by_task[t2]]["name"] == "Second"
    assert env.store.reminders[reminder_id_by_task[t3]]["name"] == "Third"


def test_new_reminder_and_new_task_in_same_run_do_not_cross_link(env):
    # A new reminder creates a task in the first discovery loop; that same
    # task must be recognized as already-linked by the second discovery
    # loop (which scans all active tasks right after), not treated as a
    # second unlinked item that spawns a duplicate reminder.
    rid = env.store.add_reminder(name="From Reminders")
    tid = env.store.add_task(content="From Todoist")

    env.run()

    pairs, _ = env.load_state()
    assert len(pairs) == 2
    assert len(env.store.reminders) == 2
    assert len(env.store.tasks) == 2
    reminder_ids = {p["reminder_id"] for p in pairs}
    task_ids = {p["task_id"] for p in pairs}
    assert reminder_ids == set(env.store.reminders)
    assert task_ids == set(env.store.tasks)
    assert rid in reminder_ids
    assert tid in task_ids


def test_pair_missing_on_both_sides_increments_missing_checks(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "PRUNE_MISSING_AFTER_CHECKS", 4)
    env.write_state([{"reminder_id": "gone-r", "task_id": "gone-t", "name": "Ghost", "body": ""}])

    env.run()

    pairs, archive = env.load_state()
    assert len(pairs) == 1
    assert pairs[0]["missing_checks"] == 1
    assert archive == []


def test_new_task_creates_linked_reminder(env):
    tid = env.store.add_task(content="Buy eggs")

    env.run()

    pairs, archive = env.load_state()
    assert len(pairs) == 1
    assert pairs[0]["task_id"] == tid
    rid = pairs[0]["reminder_id"]
    assert env.store.reminders[rid]["name"] == "Buy eggs"


def test_completing_reminder_pushes_completion_to_todoist_same_run(env):
    rid = env.store.add_reminder(name="Task", completed=True)
    tid = env.store.add_task(content="Task", completed=False)
    env.write_state([{"reminder_id": rid, "task_id": tid, "completed": False, "name": "Task", "body": ""}])

    env.run()

    # The lagging side (Todoist) must be forced to match within this same
    # run — a pair should never persist as completed=True while only one
    # side actually reflects it.
    assert env.store.tasks[tid].completed_at is not None
    pairs, _ = env.load_state()
    assert pairs[0]["completed"] is True
    assert pairs[0]["completed_at"] is not None


def test_completed_both_sides_stays_active_before_grace_period(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "ARCHIVE_AFTER_DAYS", 180)
    rid = env.store.add_reminder(name="Done", completed=True)
    tid = env.store.add_task(content="Done", completed=True)
    recent = (datetime.now() - timedelta(days=5)).isoformat()
    env.write_state(
        [{"reminder_id": rid, "task_id": tid, "completed": True, "completed_at": recent, "name": "Done", "body": ""}]
    )

    env.run()

    pairs, archive = env.load_state()
    assert len(pairs) == 1
    assert archive == []


def test_completed_both_sides_archives_after_grace_period(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "ARCHIVE_AFTER_DAYS", 5)
    rid = env.store.add_reminder(name="Done", completed=True)
    tid = env.store.add_task(content="Done", completed=True)
    old = (datetime.now() - timedelta(days=10)).isoformat()
    env.write_state(
        [{"reminder_id": rid, "task_id": tid, "completed": True, "completed_at": old, "name": "Done", "body": ""}]
    )

    env.run()

    pairs, archive = env.load_state()
    assert pairs == []
    assert len(archive) == 1
    assert archive[0]["reminder_id"] == rid


def test_recurring_task_completion_never_reconciled_or_archived(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "ARCHIVE_AFTER_DAYS", 1)
    rid = env.store.add_reminder(name="Weekly", completed=True)
    tid = env.store.add_task(content="Weekly", completed=False)
    env.store.tasks[tid].due = FakeDue(datetime(2026, 1, 8), is_recurring=True)
    old = (datetime.now() - timedelta(days=10)).isoformat()
    env.write_state(
        [{"reminder_id": rid, "task_id": tid, "completed": True, "completed_at": old, "name": "Weekly", "body": ""}]
    )

    env.run()

    # Completing the reminder side of a recurring pair must never push to
    # Todoist, and a recurring pair must never be archived.
    assert env.store.tasks[tid].completed_at is None
    pairs, archive = env.load_state()
    assert len(pairs) == 1
    assert archive == []


def test_reactivation_via_reminder_uncompleted(env):
    rid = env.store.add_reminder(id="r1", name="Redo this", completed=False)
    tid = env.store.add_task(id="t1", content="Redo this", completed=True)
    env.write_state([], [{"reminder_id": rid, "task_id": tid, "completed": True, "name": "Redo this", "body": ""}])

    env.run()

    pairs, archive = env.load_state()
    assert archive == []
    assert len(pairs) == 1
    assert pairs[0]["reminder_id"] == rid
    assert pairs[0]["task_id"] == tid
    # No duplicate should have been created for either side.
    assert len(env.store.reminders) == 1
    assert len(env.store.tasks) == 1


def test_no_reactivation_when_archived_reminder_still_shows_completed(env):
    rid = env.store.add_reminder(id="r1", name="Done", completed=True)
    tid = env.store.add_task(id="t1", content="Done", completed=True)
    env.write_state([], [{"reminder_id": rid, "task_id": tid, "completed": True, "name": "Done", "body": ""}])

    env.run()

    pairs, archive = env.load_state()
    assert pairs == []
    assert len(archive) == 1


def test_reactivation_via_task_reappearing_active(env):
    rid = env.store.add_reminder(id="r1", name="Redo", completed=True)
    tid = env.store.add_task(id="t1", content="Redo", completed=False)
    env.write_state([], [{"reminder_id": rid, "task_id": tid, "completed": True, "name": "Redo", "body": ""}])

    env.run()

    pairs, archive = env.load_state()
    assert archive == []
    assert len(pairs) == 1
    # Reconciliation should then pull the reminder side back in sync with
    # the now-reopened task, rather than leaving them mismatched.
    assert env.store.reminders[rid]["completed"] is False
    assert len(env.store.reminders) == 1
    assert len(env.store.tasks) == 1


def test_missing_pair_increments_missing_checks_without_pruning(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "PRUNE_MISSING_AFTER_CHECKS", 4)
    tid = env.store.add_task(id="t1", content="Orphan", completed=False)
    env.write_state([{"reminder_id": "gone", "task_id": tid, "name": "Orphan", "body": ""}])

    env.run()

    pairs, _ = env.load_state()
    assert len(pairs) == 1
    assert pairs[0]["missing_checks"] == 1


def test_missing_pair_pruned_after_threshold(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "PRUNE_MISSING_AFTER_CHECKS", 2)
    tid = env.store.add_task(id="t1", content="Orphan", completed=False)
    env.write_state([{"reminder_id": "gone", "task_id": tid, "name": "Orphan", "body": "", "missing_checks": 1}])

    env.run()

    pairs, archive = env.load_state()
    assert pairs == []
    assert archive == []


def test_missing_pair_resets_missing_checks_when_resolved(env):
    rid = env.store.add_reminder(id="r1", name="Back", completed=False)
    tid = env.store.add_task(id="t1", content="Back", completed=False)
    env.write_state([{"reminder_id": rid, "task_id": tid, "name": "Back", "body": "", "missing_checks": 3}])

    env.run()

    pairs, _ = env.load_state()
    assert pairs[0]["missing_checks"] == 0


def _seeded_pair(env, due=datetime(2026, 1, 10), all_day=True):
    rid = env.store.add_reminder(id="r1", name="Task", body="", completed=False, due=due, all_day=all_day)
    tid = env.store.add_task(id="t1", content="Task", description="", due=FakeDue(due.date() if all_day else due))
    env.write_state(
        [
            {
                "reminder_id": rid,
                "task_id": tid,
                "due": due.isoformat(),
                "all_day": all_day,
                "completed": False,
                "name": "Task",
                "body": "",
            }
        ]
    )
    return rid, tid


def test_due_date_changed_on_reminder_side_propagates_to_todoist(env):
    rid, tid = _seeded_pair(env)
    env.store.reminders[rid]["due"] = datetime(2026, 1, 15)

    env.run()

    assert env.store.tasks[tid].due.date == datetime(2026, 1, 15).date()
    pairs, _ = env.load_state()
    assert pairs[0]["due"] == "2026-01-15T00:00:00"


def test_due_date_changed_on_todoist_side_propagates_to_reminders(env):
    rid, tid = _seeded_pair(env)
    env.store.tasks[tid].due = FakeDue(datetime(2026, 1, 20).date())

    env.run()

    assert env.store.reminders[rid]["due"] == datetime(2026, 1, 20)
    pairs, _ = env.load_state()
    assert pairs[0]["due"] == "2026-01-20T00:00:00"


def test_due_date_conflict_reminders_wins_by_default(env):
    rid, tid = _seeded_pair(env)
    env.store.reminders[rid]["due"] = datetime(2026, 1, 15)
    env.store.tasks[tid].due = FakeDue(datetime(2026, 1, 20).date())

    env.run()

    assert env.store.tasks[tid].due.date == datetime(2026, 1, 15).date()
    assert env.store.reminders[rid]["due"] == datetime(2026, 1, 15)


def test_due_date_conflict_todoist_wins_when_configured(env, monkeypatch):
    import todoist_sync.sync_tasks as sync_tasks

    monkeypatch.setattr(sync_tasks, "CONFLICT_WINNER", "todoist")
    rid, tid = _seeded_pair(env)
    env.store.reminders[rid]["due"] = datetime(2026, 1, 15)
    env.store.tasks[tid].due = FakeDue(datetime(2026, 1, 20).date())

    env.run()

    assert env.store.reminders[rid]["due"] == datetime(2026, 1, 20)
    assert env.store.tasks[tid].due.date == datetime(2026, 1, 20).date()


def test_name_change_on_reminder_propagates_to_todoist(env):
    rid, tid = _seeded_pair(env)
    env.store.reminders[rid]["name"] = "Renamed"

    env.run()

    assert env.store.tasks[tid].content == "Renamed"


def test_name_change_on_todoist_propagates_to_reminders(env):
    rid, tid = _seeded_pair(env)
    env.store.tasks[tid].content = "Renamed"

    env.run()

    assert env.store.reminders[rid]["name"] == "Renamed"


def test_body_change_on_reminder_propagates_to_todoist(env):
    rid, tid = _seeded_pair(env)
    env.store.reminders[rid]["body"] = "New notes"

    env.run()

    assert env.store.tasks[tid].description == "New notes"


def test_body_change_on_todoist_propagates_to_reminders(env):
    rid, tid = _seeded_pair(env)
    env.store.tasks[tid].description = "New notes"

    env.run()

    assert env.store.reminders[rid]["body"] == "New notes"
