# todoist-sync

Two-way sync between a dedicated Apple Reminders list and a dedicated Todoist
project on macOS. Loosely based on the approach described in
[Syncing Apple Reminders to Todoist (two-way)](https://techresolve.blog/2026/03/03/syncing-apple-reminders-to-todoist-two-way/),
reworked after that approach (tagging notes/descriptions with a `sync_id`)
turned out to clutter every synced item's notes field.

## How it works

- Both sides get a dedicated container named **Siri Sync** (a Reminders list
  and a Todoist project), created automatically on first run. Only items in
  these containers are ever touched.
- Pairings are tracked in a local state file (`.sync_state.json`), mapping
  each reminder's native id directly to its Todoist task id. Nothing is
  written into notes/descriptions to identify a pairing — those fields stay
  exactly as you typed them.
- On each run the script:
  1. Creates a linked Todoist task for any reminder in Siri Sync that isn't
     paired yet (and vice versa), copying over the title, notes/description,
     due date, and completion status.
  2. For every already-linked pair, reconciles completion status and due
     date in both directions — comparing each side's current value against
     what was last synced (recorded on the pair), not against volatile
     "last modified" timestamps, so the script's own writes can't make a
     stale value look "newer" and win on the next run.

## Known limitations

- **Un-completing doesn't propagate.** Completion only flows
  incomplete → complete, never back. If you complete something in Reminders,
  then delete it via "clean up completed" before the next sync runs, the
  completion is lost and the Todoist task stays open (there's no window
  where the deleted reminder can be read as "completed" — see the code
  comment in `sync_tasks.py` for the underlying race).
- **Genuine due-date conflicts favor Reminders.** If a due date is changed
  differently on both sides between two syncs, Reminders' value wins.
- **No true recurrence in Reminders.** Reminders.app's AppleScript
  dictionary has no recurrence-rule property at all, so a recurring
  Todoist task can't become a genuinely recurring reminder — only its next
  due date keeps getting updated to match, each time the sync runs.
- **Titles/notes aren't re-synced after creation.** Only the initial copy
  at creation time; editing a title later doesn't propagate.

## Layout

```
src/todoist_sync/   sync package (config, bridges, entry point)
deploy/             launchd LaunchAgent + wrapper script
```

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install -e . --no-deps   # makes the todoist_sync package importable

cp config.env.example config.env
# edit config.env and set TODOIST_API_KEY
# (Todoist -> Settings -> Integrations -> Developer)
```

The first run will prompt macOS for permission to control Reminders — grant
it in System Settings > Privacy & Security > Automation.

## Running

```bash
./.venv/bin/python -m todoist_sync.sync_tasks
```

## Scheduling

Scheduled via a launchd LaunchAgent (`deploy/com.maxharris.todoist-sync.plist`)
rather than cron, since AppleScript needs to run in the logged-in GUI
session to control Reminders.app reliably. It runs every 15 minutes
(`StartInterval`) and logs to `sync-out.log` / `sync-error.log` in the
project root.

The plist points at `deploy/todoist-sync`, a small wrapper script, rather
than the venv's Python binary directly — otherwise macOS's Login Items list
shows the background item as "Python" instead of something recognizable.

```bash
cp deploy/com.maxharris.todoist-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.maxharris.todoist-sync.plist

# to stop:
launchctl unload ~/Library/LaunchAgents/com.maxharris.todoist-sync.plist
```

## Reminders.app AppleScript quirks

Reminders.app's AppleScript support is unreliable for direct addressing —
`list "Name"`, `list id "..."`, `exists list ...`, and `whose` clauses have
all been observed to fail, return stale/wrong results, or in one case
resolve to the wrong list entirely. Every script in `reminders_bridge.py`
instead enumerates `lists` and `reminders of` a matched list, comparing
properties inside the loop — the only pattern that's held up reliably.
