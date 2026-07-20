# Versioning

The current version lives in the `VERSION` file at the repo root (a single
line, e.g. `1.2.3`). `pyproject.toml` reads its `[project] version` from
that file, and `todoist_sync.__version__` reads it at import time. It's
logged once per run (`todoist-sync vX.Y.Z starting`) so any log line can be
traced back to the exact code that produced it.

**Rule: any commit that changes the app's runtime behavior must bump
`VERSION` as part of that same commit**, following semantic versioning
(MAJOR.MINOR.PATCH):

- **MAJOR** — incompatible/breaking change (input file format, API contract,
  CLI flags, config schema).
- **MINOR** — new backwards-compatible feature or UI capability.
- **PATCH** — backwards-compatible bug fix, or a small behavior/UI tweak that
  isn't a new capability.

Commits that don't touch runtime behavior (docs, tests, CI, refactors with
no observable difference) don't need a `VERSION` bump.

When a `VERSION` bump lands, tag the commit (`git tag vX.Y.Z`) and cut a
GitHub release from it so the log-visible version always resolves back to a
concrete, inspectable set of changes.

# Secrets

`config.env` holds a live `TODOIST_API_KEY` and is gitignored on purpose —
never commit it. Beyond git, never print or `cat` `config.env` in full
either (terminal output, logs, tool calls, screen shares all count as
exposure just as much as a commit does). To inspect its non-secret
settings, use `grep -v TODOIST_API_KEY config.env` or read it and redact
the key's value before displaying it.
