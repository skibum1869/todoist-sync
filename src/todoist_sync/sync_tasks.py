#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import logging
import logging.handlers
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import httpx

from . import config, state
from . import __version__
from .config import (
    ARCHIVE_AFTER_DAYS,
    AUTH_FAILURE_MARKER,
    CONFLICT_WINNER,
    LIST_NAME,
    LOCK_PATH,
    LOG_LEVEL,
    LOG_PATH,
    NETWORK_DOWN_MARKER,
    PRUNE_MISSING_AFTER_CHECKS,
    REMINDERS_ACCESS_MARKER,
    STATE_PATH,
    TODOIST_API_KEY,
)
from .reminders_bridge import RemindersBridge, RemindersUnavailableError
from .todoist_bridge import TodoistBridge

_NOTIFY_THROTTLE = timedelta(hours=1)
_FAILURE_MARKERS = (NETWORK_DOWN_MARKER, AUTH_FAILURE_MARKER, REMINDERS_ACCESS_MARKER)

_MAX_LOG_BYTES = 1_000_000  # 1 MB per file
_LOG_BACKUP_COUNT = 3


def _configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    # Write directly to the log file (rotating, capped) rather than through
    # stdout/stderr + launchd's StandardOutPath/StandardErrorPath, which had
    # no size limit and would grow forever.
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=_MAX_LOG_BYTES, backupCount=_LOG_BACKUP_COUNT
    )
    handler.setFormatter(formatter)

    effective_level = LOG_LEVEL or logging.INFO
    root = logging.getLogger()
    root.setLevel(effective_level)
    root.addHandler(handler)

    # launchd runs this headless with nothing capturing stdout/stderr (the
    # plist sets no StandardOutPath/StandardErrorPath), so this only ever
    # shows up for someone running the script manually in a terminal —
    # otherwise a run's outcome is invisible unless you go tail the file.
    if sys.stderr.isatty():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(console_handler)

    # httpx/httpcore log every request at INFO with no handler of their own,
    # so without this they'd propagate straight into root and flood the log
    # with one line per Todoist API call. Only let that through in DEBUG,
    # where it doubles as the per-query trace for the Todoist side.
    third_party_level = logging.DEBUG if effective_level <= logging.DEBUG else logging.WARNING
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(third_party_level)


_configure_logging()
log = logging.getLogger("sync_tasks")


def _notify_macos(title: str, message: str) -> None:
    # AppleScript string literals: backslash and double-quote need escaping.
    def _escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    subprocess.run(["osascript", "-e", script], check=False)


def _notify_once(marker: Path, title: str, message: str) -> None:
    """Notifies via macOS banner, throttled per-marker to once per
    _NOTIFY_THROTTLE so a persistent failure (offline for hours, an
    expired token nobody's noticed yet) doesn't spam a banner every 15
    minutes. The marker also records that this failure category is
    currently active, so a subsequent success can clear it."""
    now = datetime.now()
    last = None
    if marker.exists():
        try:
            last = datetime.fromisoformat(marker.read_text().strip())
        except ValueError:
            last = None
    if last is None or now - last > _NOTIFY_THROTTLE:
        _notify_macos(title, message)
    marker.write_text(now.isoformat())


def _clear_failure_markers() -> None:
    for marker in _FAILURE_MARKERS:
        if marker.exists():
            marker.unlink()


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


def _redact_for_log(field_name: str, value):
    # Notes/description content is the most likely place for genuinely
    # sensitive text — unlike a title (short, needed to tell items apart in
    # a trace), it's rarely needed to diagnose a sync bug, so DEBUG logs a
    # size instead of the raw text.
    if field_name == "body" and value:
        return f"<{len(value)} chars>"
    return value


def _reconcile_scalar(
    last_value, r_value, t_value, set_todoist, set_reminders, *, field_name="value", pair_label="?"
):
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
        # Both sides changed to different values: whichever wasn't chosen
        # gets silently overwritten. Worth a WARNING (not just DEBUG) since
        # that's a real edit being discarded, not routine propagation — but
        # the values themselves are personal content (titles/notes), so
        # keep those out of the default-visible line and behind DEBUG.
        log.warning(
            "Conflict on %s for pair %s: %s wins (SYNC_CONFLICT_WINNER)",
            field_name,
            pair_label,
            CONFLICT_WINNER,
        )
        log.debug(
            "Conflict detail for pair %s: reminders=%r, todoist=%r",
            pair_label,
            _redact_for_log(field_name, r_value),
            _redact_for_log(field_name, t_value),
        )
    elif r_changed:
        winner = r_value
        log.debug(
            "Pair %s: %s changed in Reminders -> %r, propagating to Todoist",
            pair_label,
            field_name,
            _redact_for_log(field_name, winner),
        )
    else:
        winner = t_value
        log.debug(
            "Pair %s: %s changed in Todoist -> %r, propagating to Reminders",
            pair_label,
            field_name,
            _redact_for_log(field_name, winner),
        )

    if winner != t_value:
        set_todoist(winner)
    if winner != r_value:
        set_reminders(winner)
    return winner, True


def _pair_label(pair: dict) -> str:
    return f"r={pair['reminder_id']}/t={pair['task_id']}"


def main() -> None:
    log.info("todoist-sync v%s starting", __version__)
    config.validate()
    log.debug(
        "config: list=%r conflict_winner=%r archive_after_days=%d prune_after_checks=%d",
        LIST_NAME,
        CONFLICT_WINNER,
        ARCHIVE_AFTER_DAYS,
        PRUNE_MISSING_AFTER_CHECKS,
    )
    reminders = RemindersBridge(LIST_NAME)
    todoist = TodoistBridge(TODOIST_API_KEY)
    project_id = todoist.get_or_create_project(LIST_NAME)

    pairs, archive = state.load_state(STATE_PATH)
    linked_reminder_ids = {p["reminder_id"] for p in pairs}
    linked_task_ids = {p["task_id"] for p in pairs}
    # Archived pairs (completed on both sides for SYNC_ARCHIVE_AFTER_DAYS)
    # are excluded from the per-pair reconciliation loop below, but their
    # ids are kept here so a reminder/task that gets reactivated is matched
    # back to its existing pair instead of spawning a duplicate.
    archived_by_reminder = {p["reminder_id"]: p for p in archive}
    archived_by_task = {p["task_id"]: p for p in archive}

    def _unarchive(pair: dict) -> None:
        log.debug("Pair %s: reactivated from archive", _pair_label(pair))
        archive.remove(pair)
        del archived_by_reminder[pair["reminder_id"]]
        del archived_by_task[pair["task_id"]]
        pairs.append(pair)
        linked_reminder_ids.add(pair["reminder_id"])
        linked_task_ids.add(pair["task_id"])

    # 1. Propagate brand-new items sitting in the dedicated containers,
    #    including whatever due date/time each one already has. The pair's
    #    fields record what we just synced, so reconciliation below has a
    #    correct baseline from the start.
    created_in_todoist = 0
    reactivated = 0
    for r in reminders.get_reminders():
        if r["id"] in linked_reminder_ids:
            continue
        archived_pair = archived_by_reminder.get(r["id"])
        if archived_pair is not None:
            # get_reminders() returns every reminder regardless of
            # completion, so an archived id showing up here isn't
            # necessarily a reactivation — only act if it's been unchecked.
            if not r["completed"]:
                _unarchive(archived_pair)
                reactivated += 1
                state.save_state(STATE_PATH, pairs, archive)
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
        state.save_state(STATE_PATH, pairs, archive)

    created_in_reminders = 0
    for t in todoist.get_active_tasks(project_id):
        if t.id in linked_task_ids:
            continue
        archived_pair = archived_by_task.get(t.id)
        if archived_pair is not None:
            # get_active_tasks() only ever returns open tasks, so a match
            # here is always a genuine reactivation — no completed check
            # needed, unlike the reminders side above.
            _unarchive(archived_pair)
            reactivated += 1
            state.save_state(STATE_PATH, pairs, archive)
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
        state.save_state(STATE_PATH, pairs, archive)

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
    now = datetime.now()
    to_archive = []
    to_prune = []
    for pair in pairs:
        r = reminders.get_reminder(pair["reminder_id"])
        t = todoist.get_task(pair["task_id"], project_id)
        if r is None or t is None:
            # get_reminder/get_task only return None on a confirmed 404, not
            # on a transient error (those raise instead) — so this is a real
            # deletion signal, not a flaky one. Still require a few
            # consecutive misses rather than pruning on the first sighting,
            # in case the other side is mid-edit (e.g. an EventKit/iCloud
            # propagation lag) rather than genuinely gone. A check count
            # (not elapsed time) is what actually matters here — it demands
            # that many independent confirmations regardless of how much
            # wall-clock time passed between them (e.g. the Mac sleeping).
            # Once confirmed gone, prune outright rather than archive:
            # unlike a completed pair, a deleted one has no id worth
            # matching a reactivation against.
            pair["missing_checks"] = pair.get("missing_checks", 0) + 1
            log.debug(
                "Pair %s: reminder or task missing (check %d/%d)",
                _pair_label(pair),
                pair["missing_checks"],
                PRUNE_MISSING_AFTER_CHECKS,
            )
            if pair["missing_checks"] >= PRUNE_MISSING_AFTER_CHECKS:
                to_prune.append(pair)
            continue
        if pair.get("missing_checks"):
            pair["missing_checks"] = 0

        # Recurring Todoist tasks never actually get completed_at set —
        # "completing" one just advances its due date to the next
        # occurrence and leaves it structurally incomplete. Treating that
        # as a real completion signal would record a bogus "completed"
        # baseline that the next run reads as Todoist having reverted,
        # silently un-completing the reminder. Due-date reconciliation
        # below is what actually represents progress on these. They're
        # also never eligible for archiving, since they never reach a
        # real "done" state.
        is_recurring = t.due is not None and t.due.is_recurring
        if not is_recurring:
            new_completed, completed_changed = _reconcile_scalar(
                pair.get("completed", False),
                r["completed"],
                t.completed_at is not None,
                set_todoist=lambda v: (todoist.complete_task(t.id) if v else todoist.uncomplete_task(t.id)),
                set_reminders=lambda v: (
                    reminders.complete_reminder(r["id"]) if v else reminders.uncomplete_reminder(r["id"])
                ),
                field_name="completed",
                pair_label=_pair_label(pair),
            )
            pair["completed"] = new_completed
            if completed_changed:
                completed_synced += 1
            # completed_at anchors the SYNC_ARCHIVE_AFTER_DAYS grace period.
            # Stamp it on every fresh transition to completed, and backfill
            # it for pairs that were already completed before this field
            # existed, so they don't sit un-archived forever.
            if new_completed:
                if completed_changed or not pair.get("completed_at"):
                    pair["completed_at"] = now.isoformat()
            else:
                pair["completed_at"] = None

        new_name, name_changed = _reconcile_scalar(
            pair.get("name", r["name"]),
            r["name"],
            t.content,
            set_todoist=lambda v: todoist.set_task_content(t.id, v),
            set_reminders=lambda v: reminders.set_name(r["id"], v),
            field_name="name",
            pair_label=_pair_label(pair),
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
            field_name="body",
            pair_label=_pair_label(pair),
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
            log.warning(
                "Conflict on due date for pair %s: %s wins (SYNC_CONFLICT_WINNER)",
                _pair_label(pair),
                CONFLICT_WINNER,
            )
            log.debug(
                "Conflict detail for pair %s: reminders=%r, todoist=%r", _pair_label(pair), r_due, t_due
            )
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
            log.debug("Pair %s: due date changed in Reminders -> %r, propagating to Todoist", _pair_label(pair), r_due)
            todoist.set_task_due(t.id, r_due, r_all_day)
            due_synced += 1
            winning_due, winning_all_day = r_due, r_all_day
        elif t_changed and t_due is not None:
            log.debug("Pair %s: due date changed in Todoist -> %r, propagating to Reminders", _pair_label(pair), t_due)
            reminders.set_due_date(r["id"], t_due, t_all_day)
            due_synced += 1
            winning_due, winning_all_day = t_due, t_all_day
        else:
            winning_due, winning_all_day = last_due, last_all_day

        pair["due"] = _serialize_due(winning_due)
        pair["all_day"] = winning_all_day

        if (
            not is_recurring
            and pair.get("completed")
            and pair.get("completed_at")
            and now - datetime.fromisoformat(pair["completed_at"]) >= timedelta(days=ARCHIVE_AFTER_DAYS)
        ):
            to_archive.append(pair)

    # 3. Move pairs that have sat completed on both sides past the grace
    #    period into the archive. Archived pairs are excluded from the
    #    per-pair API reconciliation above on future runs — the ids are
    #    kept only so a later reactivation (see step 1) is recognized
    #    instead of spawning a duplicate.
    for pair in to_archive:
        log.debug("Pair %s: archived (completed on both sides past SYNC_ARCHIVE_AFTER_DAYS)", _pair_label(pair))
        pairs.remove(pair)
        archive.append(pair)

    # 4. Drop pairs whose reminder and/or task has been confirmed gone for
    #    longer than SYNC_PRUNE_MISSING_AFTER_DAYS. These are removed
    #    outright, not archived — there's nothing left to reactivate.
    for pair in to_prune:
        log.debug("Pair %s: pruned (missing for %d consecutive checks)", _pair_label(pair), pair["missing_checks"])
        pairs.remove(pair)

    state.save_state(STATE_PATH, pairs, archive)
    _clear_failure_markers()

    if not any(
        (
            created_in_todoist,
            created_in_reminders,
            completed_synced,
            due_synced,
            name_synced,
            notes_synced,
            reactivated,
            to_archive,
            to_prune,
        )
    ):
        log.info("Sync complete: no changes")
    else:
        log.info(
            "Sync complete: %d reminder(s) -> Todoist, %d task(s) -> Reminders, "
            "%d completion(s) synced, %d due date(s) synced, %d title(s) synced, "
            "%d note(s) synced, %d reactivated from archive, %d archived, %d pruned",
            created_in_todoist,
            created_in_reminders,
            completed_synced,
            due_synced,
            name_synced,
            notes_synced,
            reactivated,
            len(to_archive),
            len(to_prune),
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
        _notify_once(NETWORK_DOWN_MARKER, "Todoist Sync", "No internet connection — sync skipped.")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            # An expired/revoked token would otherwise retry silently
            # forever, logged but never actually noticed.
            log.warning("Sync failed: Todoist API token invalid or expired")
            _notify_once(
                AUTH_FAILURE_MARKER,
                "Todoist Sync",
                "Todoist API token invalid or expired — check config.env.",
            )
        else:
            # HTTPStatusError's own message is just "<status> for url:
            # <url>" — the actual reason (validation error, rate limit
            # detail, etc.) is in the response body and would otherwise
            # never reach the log.
            log.exception("Sync failed: %s", e.response.text)
        sys.exit(1)
    except RemindersUnavailableError as e:
        log.warning("Sync failed: %s", e)
        _notify_once(REMINDERS_ACCESS_MARKER, "Todoist Sync", str(e))
        sys.exit(1)
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)
    finally:
        lock_file.close()
