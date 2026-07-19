# todoist-sync

Two-way sync between a dedicated Apple Reminders list and a dedicated Todoist
project on macOS, based on the approach described in
[Syncing Apple Reminders to Todoist (two-way)](https://techresolve.blog/2026/03/03/syncing-apple-reminders-to-todoist-two-way/).

## How it works

- Both sides get a dedicated container named **Siri Sync** (a Reminders list
  and a Todoist project), created automatically on first run.
- Every synced item carries a `sync_id:<uuid>` tag hidden in its
  notes/description. That tag is the source of truth linking a reminder to
  its Todoist task, and it's what prevents the sync from re-creating items
  it already synced (no infinite loops).
- On each run the script:
  1. Fetches reminders from the Siri Sync list and tasks from the Siri Sync
     project (active + recently completed, see `SYNC_LOOKBACK_DAYS`).
  2. Any reminder or task without a `sync_id` is new — it gets a fresh id,
     the id is written back onto the original item, and a linked copy is
     created on the other side.
  3. For already-linked pairs, completing an item on either side marks its
     counterpart complete too.

**Known limitation:** only the title and completion status are kept in
sync. Editing a title after the initial sync, or un-completing a finished
item, won't propagate — re-run with a fresh item if you need to change one.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp config.env.example config.env
# edit config.env and set TODOIST_API_KEY
# (Todoist -> Settings -> Integrations -> Developer)
```

The first run will prompt macOS for permission to control Reminders — grant
it in System Settings > Privacy & Security > Automation.

## Running

```bash
./.venv/bin/python sync_tasks.py
```

## Scheduling

Run every 15–30 minutes via cron:

```
*/15 * * * * cd /path/to/todoist-sync && ./.venv/bin/python sync_tasks.py >> sync.log 2>&1
```
