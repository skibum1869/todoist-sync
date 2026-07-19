from __future__ import annotations

import applescript

_ENSURE_LIST = applescript.AppleScript("""
on run {listName}
    tell application "Reminders"
        if not (exists list listName) then
            make new list with properties {name:listName}
        end if
    end tell
end run
""")

_GET_REMINDERS = applescript.AppleScript("""
on run {listName}
    tell application "Reminders"
        set theList to list listName
        set output to {}
        repeat with r in reminders of theList
            set end of output to {id of r as string, name of r, body of r, completed of r}
        end repeat
        return output
    end tell
end run
""")

_CREATE_REMINDER = applescript.AppleScript("""
on run {listName, theName, theBody}
    tell application "Reminders"
        tell list listName
            set newReminder to make new reminder with properties {name:theName, body:theBody}
        end tell
        return id of newReminder as string
    end tell
end run
""")

_SET_BODY = applescript.AppleScript("""
on run {listName, reminderId, newBody}
    tell application "Reminders"
        set r to first reminder of list listName whose id is reminderId
        set body of r to newBody
    end tell
end run
""")

_SET_COMPLETED = applescript.AppleScript("""
on run {listName, reminderId, isCompleted}
    tell application "Reminders"
        set r to first reminder of list listName whose id is reminderId
        set completed of r to isCompleted
    end tell
end run
""")


class RemindersBridge:
    """Talks to macOS Reminders.app via AppleScript for a single dedicated list."""

    def __init__(self, list_name: str):
        self.list_name = list_name
        _ENSURE_LIST.run(list_name)

    def get_reminders(self) -> list[dict]:
        rows = _GET_REMINDERS.run(self.list_name) or []
        return [
            {"id": row[0], "name": row[1], "body": row[2] or "", "completed": bool(row[3])}
            for row in rows
        ]

    def create_reminder(self, name: str, body: str) -> str:
        return _CREATE_REMINDER.run(self.list_name, name, body)

    def set_body(self, reminder_id: str, body: str) -> None:
        _SET_BODY.run(self.list_name, reminder_id, body)

    def complete_reminder(self, reminder_id: str) -> None:
        _SET_COMPLETED.run(self.list_name, reminder_id, True)
