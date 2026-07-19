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
- Pairings are tracked in a local state file (`var/state.json`), mapping
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
- The Reminders side talks to EventKit through a small compiled Swift
  helper (`swift/reminders-bridge`), not AppleScript. AppleScript's
  Reminders support proved unreliable under testing — direct-by-name and
  direct-by-id addressing intermittently failed or returned wrong results.
  EventKit is Apple's proper, documented framework for Reminders and has
  been reliable in comparison; the Todoist side stays in Python since the
  `todoist-api-python` SDK already covers it well.

## Known limitations

- **Un-completing doesn't propagate.** Completion only flows
  incomplete → complete, never back. If you complete something in Reminders,
  then delete it via "clean up completed" before the next sync runs, the
  completion is lost and the Todoist task stays open (there's no window
  where the deleted reminder can be read as "completed" — see the code
  comment in `sync_tasks.py` for the underlying race).
- **Genuine due-date conflicts favor Reminders.** If a due date is changed
  differently on both sides between two syncs, Reminders' value wins.
- **No true recurrence.** Todoist's API only exposes recurrence as a
  human-readable string (e.g. "Every! 1 weeks Saturday"), not a structured
  rule, so there's nothing reliable to translate into an actual EventKit
  recurrence rule — only the next due date keeps getting updated to match,
  each time the sync runs.
- **Titles/notes aren't re-synced after creation.** Only the initial copy
  at creation time; editing a title later doesn't propagate.

## Layout

```
src/todoist_sync/          Python package — Todoist API, sync logic, state, CLI entry point
swift/reminders-bridge/    Swift/EventKit helper the Python side shells out to for all Reminders access
deploy/                    launchd LaunchAgent, wrapper script, installer
var/                       generated at runtime — state.json, sync-out.log, sync-error.log
```

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install -e . --no-deps   # makes the todoist_sync package importable

cp config.env.example config.env
# edit config.env and set TODOIST_API_KEY
# (Todoist -> Settings -> Integrations -> Developer)

cd swift/reminders-bridge && swift build -c release && cd ../..
```

Building the Swift helper requires Xcode Command Line Tools
(`xcode-select --install` if `swift --version` doesn't work).

The first run will prompt macOS for Reminders access — grant it in
System Settings > Privacy & Security > Reminders.

## Running

```bash
./.venv/bin/python -m todoist_sync.sync_tasks
```

## Scheduling

Scheduled via a launchd LaunchAgent rather than cron, since both EventKit
and the Todoist client need to run in the logged-in user session. It runs
every 15 minutes and logs to `var/sync-out.log` (routine activity) /
`var/sync-error.log` (actual failures/tracebacks only — safe to share when
reporting an issue, since it won't also contain the full routine sync
history).

The agent points at `deploy/todoist-sync`, a small wrapper script, rather
than the venv's Python binary directly — otherwise macOS's Login Items list
shows the background item as "Python" instead of something recognizable.
Both that script and the compiled Swift binary are ad-hoc code-signed
(`codesign -s -`) to clear the "unidentified developer" warning macOS shows
for unsigned executables running as background items; that's a local-only
signature, not tied to an Apple Developer ID, and needs re-running after
any edit (the installer does this automatically).

```bash
./deploy/install.sh              # build, sign, install, and load
./deploy/install.sh --upgrade    # pull latest code, update deps, then build/sign/install/load
./deploy/install.sh -U           # same as --upgrade
./deploy/install.sh --uninstall  # unload and remove
```

Resolves paths automatically, so it works regardless of where the repo is
cloned or which user runs it. Requires `.venv` and `config.env` to already
be set up (see Setup, above). Safe to re-run. `--upgrade` refuses to run if
there are uncommitted local changes, so it can't clobber in-progress edits.
