# todoist-sync

Two-way sync between a dedicated Apple Reminders list and a dedicated Todoist
project on macOS. Loosely based on the approach described in
[Syncing Apple Reminders to Todoist (two-way)](https://techresolve.blog/2026/03/03/syncing-apple-reminders-to-todoist-two-way/),
reworked after that approach (tagging notes/descriptions with a `sync_id`)
turned out to clutter every synced item's notes field.

## How it works

- Both sides get a dedicated container named **Siri Sync** by default (a
  Reminders list and a Todoist project), created automatically on first
  run. Only items in these containers are ever touched. The name is set by
  `SYNC_LIST_NAME` in `config.env` — change it there if you want something
  else; the rest of this doc just uses the default.
- Pairings are tracked in a local state file (`var/state.json`), mapping
  each reminder's native id directly to its Todoist task id. Nothing is
  written into notes/descriptions to identify a pairing — those fields stay
  exactly as you typed them.
- On each run the script:
  1. Creates a linked Todoist task for any reminder in Siri Sync that isn't
     paired yet (and vice versa), copying over the title, notes/description,
     due date, and completion status.
  2. For every already-linked pair, reconciles title, notes, completion
     status, and due date in both directions — comparing each side's
     current value against what was last synced (recorded on the pair),
     not against volatile "last modified" timestamps, so the script's own
     writes can't make a stale value look "newer" and win on the next run.
     If both sides changed a field to different values between two syncs,
     `SYNC_CONFLICT_WINNER` in `config.env` (`reminders` by default, or
     `todoist`) decides which one sticks.
  3. Moves any pair that's been completed on both sides for longer than
     `SYNC_ARCHIVE_AFTER_DAYS` (180 by default) into an archive section of
     `state.json`. Archived pairs are no longer polled every run — that's
     what keeps sync fast as your history of completed tasks grows — but
     their ids are still remembered, so uncompleting one later resumes
     syncing it instead of creating a duplicate.
  4. Drops any pair whose reminder and/or task has been deleted, once
     that's been confirmed for `SYNC_PRUNE_MISSING_AFTER_CHECKS` consecutive
     runs (4 by default — about an hour at the 15-minute sync interval).
     Unlike archiving, a deleted item has nothing left to reactivate (ids
     are never reused), so it's removed from `state.json` outright rather
     than kept around.
- The Reminders side talks to EventKit through a small compiled Swift
  helper (`swift/reminders-bridge`), not AppleScript. AppleScript's
  Reminders support proved unreliable under testing — direct-by-name and
  direct-by-id addressing intermittently failed or returned wrong results.
  EventKit is Apple's proper, documented framework for Reminders and has
  been reliable in comparison; the Todoist side stays in Python since the
  `todoist-api-python` SDK already covers it well.

## Known limitations

- **A completion can be lost if you "clean up" too fast.** If you complete
  something in Reminders and then delete it via "clean up completed"
  *before* the next sync runs, the completion is lost — once the reminder
  is gone there's no window left where it can be read as "completed," so
  the Todoist task stays open. This is inherent to polling-based sync
  (nothing watches for changes in real time) and isn't fixable without a
  different architecture. Un-completing itself (without deleting) does
  propagate correctly in either direction.
- **No true recurrence.** Todoist's API only exposes recurrence as a
  human-readable string (e.g. "Every! 1 weeks Saturday"), not a structured
  rule — confirmed against the official API reference, which documents no
  RRULE/frequency/interval fields anywhere. Todoist's own completion-based
  `every!` recurrence also has no EventKit equivalent at all (EventKit
  recurrence is always fixed-schedule, never "N units after actual
  completion"). Only the next due date keeps getting updated to match,
  each time the sync runs — there's no attempt to translate recurrence
  into a real EventKit recurrence rule.
- **Completion status isn't synced for recurring Todoist tasks, on
  purpose.** Verified directly: completing a recurring Todoist task never
  sets `completed_at` at all — it just advances `due` to the next
  occurrence and stays active forever. Reconciling "completed" for these
  would record a bogus baseline (since Todoist's side can never actually
  become "completed") that flips back and silently un-completes the
  reminder on the next run. So completion sync is skipped entirely for
  any pair whose Todoist task is recurring; the due-date advancing each
  cycle is what represents progress on those instead. Completing the
  reminder side of a recurring pair just marks the reminder done locally
  — it won't push anything to Todoist.
- **Location-based reminders aren't supported.** The Swift bridge only
  ever reads/writes `EKReminder.dueDateComponents` — it has no handling
  of `EKAlarm`/`structuredLocation` at all, and the Todoist task API this
  tool uses has no location field to put it in either way. A
  "remind me when I arrive/leave" reminder still syncs over as a plain
  task (title and notes carry across), but the location trigger itself is
  silently dropped.

## Layout

```
src/todoist_sync/          Python package — Todoist API, sync logic, state, CLI entry point
swift/reminders-bridge/    Swift/EventKit helper the Python side shells out to for all Reminders access
swift/wake-watcher/        Swift/NSWorkspace helper that fires a sync shortly after the Mac wakes
deploy/                    launchd LaunchAgents, wrapper script, installer
var/                       generated at runtime — state.json, sync-out.log, sync-error.log
```

## Setup

```bash
cp config.env.example config.env
# edit config.env and set TODOIST_API_KEY
# (Todoist -> Settings -> Integrations -> Developer)

./deploy/install.sh
```

`deploy/install.sh` creates `.venv` if needed, installs the Python
dependencies, builds the Swift helpers, and loads the LaunchAgents. It
requires `config.env` to already exist (see above) and Xcode Command Line
Tools for the Swift build (`xcode-select --install` if `swift --version`
doesn't work).

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
history). Both are rotating logs (1 MB cap, 3 backups kept, ~4 MB max per
file) written directly by the script itself, not via launchd's raw
stdout/stderr redirection — so they won't grow unbounded, but running the
script manually in a terminal no longer prints anything live; `tail -f
var/sync-out.log` instead.

The agent points at `deploy/todoist-sync`, a small wrapper script, rather
than the venv's Python binary directly — otherwise macOS's Login Items list
shows the background item as "Python" instead of something recognizable.
Both that script and the compiled Swift binaries are ad-hoc code-signed
(`codesign -s -`) to clear the "unidentified developer" warning macOS shows
for unsigned executables running as background items; that's a local-only
signature, not tied to an Apple Developer ID, and needs re-running after
any edit (the installer does this automatically).

```bash
./deploy/install.sh              # build, sign, install, and load
./deploy/install.sh --upgrade    # pull latest code, update deps, then build/sign/install/load
./deploy/install.sh --uninstall  # unload and remove
```

Resolves paths automatically, so it works regardless of where the repo is
cloned or which user runs it. Requires `.venv` and `config.env` to already
be set up (see Setup, above). Safe to re-run. `--upgrade` refuses to run if
there are uncommitted local changes, so it can't clobber in-progress edits.

### Sleep and wake

launchd's `StartInterval` timer doesn't fire while the Mac is asleep, and
its wake catch-up isn't prompt — it can be several minutes after wake before
a missed run fires. To close that gap, the installer also builds and loads
a second, always-running LaunchAgent, `wake-watcher`
(`swift/wake-watcher/`), which observes `NSWorkspace.didWakeNotification`
and fires a sync ~10 seconds after every wake — a delay to give Wi-Fi a
moment to reconnect first. It runs alongside the 15-minute timer rather
than replacing it, has no external dependency (no Homebrew, no
third-party app), and is uninstalled/reinstalled together with the main
agent by `deploy/install.sh`.
