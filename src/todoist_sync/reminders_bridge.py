from __future__ import annotations

from datetime import datetime

import applescript

# Reminders.app's AppleScript support is unreliable for direct addressing
# (`list "Name"`, `list id "..."`, `exists list ...`, and `whose` clauses have
# all been observed to fail or mismatch in practice). Enumerating `lists` and
# `reminders of` a matched list, then comparing properties inside the loop,
# has been the only pattern that works consistently — every script below
# sticks to that shape even though it's more verbose.

_GET_REMINDERS = applescript.AppleScript("""
on run {listName}
    tell application "Reminders"
        repeat with l in lists
            if name of l is listName then
                set output to {}
                repeat with r in reminders of l
                    set end of output to {id of r as string, name of r, body of r, completed of r, due date of r, allday due date of r}
                end repeat
                return output
            end if
        end repeat
    end tell
    return {}
end run
""")

_GET_REMINDER = applescript.AppleScript("""
on run {listName, reminderId}
    tell application "Reminders"
        repeat with l in lists
            if name of l is listName then
                repeat with r in reminders of l
                    if (id of r as string) is reminderId then
                        return {name of r, body of r, completed of r, due date of r, allday due date of r}
                    end if
                end repeat
            end if
        end repeat
    end tell
    return {}
end run
""")

_CREATE_REMINDER = applescript.AppleScript("""
on run {listName, theName, theBody, hasDue, dueDate, isAllDay}
    tell application "Reminders"
        set targetList to missing value
        repeat with l in lists
            if name of l is listName then
                set targetList to l
                exit repeat
            end if
        end repeat
        if targetList is missing value then
            set targetList to make new list with properties {name:listName}
        end if
        tell targetList
            set newReminder to make new reminder with properties {name:theName, body:theBody}
        end tell
        if hasDue then
            set due date of newReminder to dueDate
            if isAllDay then
                set allday due date of newReminder to dueDate
            end if
        end if
        return id of newReminder as string
    end tell
end run
""")

_SET_BODY = applescript.AppleScript("""
on run {listName, reminderId, newBody}
    tell application "Reminders"
        repeat with l in lists
            if name of l is listName then
                repeat with r in reminders of l
                    if (id of r as string) is reminderId then
                        set body of r to newBody
                        return true
                    end if
                end repeat
            end if
        end repeat
    end tell
    return false
end run
""")

_SET_COMPLETED = applescript.AppleScript("""
on run {listName, reminderId, isCompleted}
    tell application "Reminders"
        repeat with l in lists
            if name of l is listName then
                repeat with r in reminders of l
                    if (id of r as string) is reminderId then
                        set completed of r to isCompleted
                        return true
                    end if
                end repeat
            end if
        end repeat
    end tell
    return false
end run
""")

_SET_DUE_DATE = applescript.AppleScript("""
on run {listName, reminderId, dueDate, isAllDay}
    tell application "Reminders"
        repeat with l in lists
            if name of l is listName then
                repeat with r in reminders of l
                    if (id of r as string) is reminderId then
                        set due date of r to dueDate
                        if isAllDay then
                            set allday due date of r to dueDate
                        end if
                        return true
                    end if
                end repeat
            end if
        end repeat
    end tell
    return false
end run
""")


def _row_to_reminder(reminder_id: str, name: str, body, completed: bool, due, all_day_due) -> dict:
    # Reminders.app returns AppleScript "missing value" (an AEType sentinel,
    # not falsy in Python) for an empty body/due date, not None or "".
    due_dt = due if isinstance(due, datetime) else None
    all_day_dt = all_day_due if isinstance(all_day_due, datetime) else None
    # Reminders.app populates "allday due date" (as midnight) even for
    # reminders that have a specific time — it's only genuinely all-day when
    # that value matches "due date" itself (also midnight).
    all_day = due_dt is not None and all_day_dt is not None and due_dt == all_day_dt
    return {
        "id": reminder_id,
        "name": name,
        "body": body if isinstance(body, str) else "",
        "completed": bool(completed),
        "due": due_dt,
        "all_day": all_day,
    }


class RemindersBridge:
    """Talks to macOS Reminders.app via AppleScript for a single dedicated list."""

    def __init__(self, list_name: str):
        self.list_name = list_name
        # Touch the list once so it's created if missing; also serves as the
        # per-process warm-up Reminders.app seems to need before later calls
        # resolve correctly.
        _GET_REMINDERS.run(list_name)

    def get_reminders(self) -> list[dict]:
        rows = _GET_REMINDERS.run(self.list_name) or []
        return [_row_to_reminder(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]

    def get_reminder(self, reminder_id: str) -> dict | None:
        row = _GET_REMINDER.run(self.list_name, reminder_id)
        if not row:
            return None
        return _row_to_reminder(reminder_id, row[0], row[1], row[2], row[3], row[4])

    def create_reminder(self, name: str, body: str, due_dt: datetime | None = None, all_day: bool = False) -> str:
        return _CREATE_REMINDER.run(self.list_name, name, body, due_dt is not None, due_dt or datetime.now(), all_day)

    def set_body(self, reminder_id: str, body: str) -> bool:
        return _SET_BODY.run(self.list_name, reminder_id, body)

    def complete_reminder(self, reminder_id: str) -> bool:
        return _SET_COMPLETED.run(self.list_name, reminder_id, True)

    def set_due_date(self, reminder_id: str, due_dt: datetime, all_day: bool) -> bool:
        return _SET_DUE_DATE.run(self.list_name, reminder_id, due_dt, all_day)
