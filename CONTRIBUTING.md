# Contributing

StateCat is one dependency-free Python script (`statecat.py`), Python
3.8+, UTF-8, no network calls. Keep it that way.

## Running the tests

```sh
python3 -m unittest discover -s tests
```

Tests are stdlib `unittest`, subprocess-driven against the real CLI
in temp folders. Add a test for every behaviour change, and keep the
suite green before pushing.

## Scope

v1 is deliberately small. The out-of-scope list in the README is
binding: no cloud, sync, accounts, or hosted service; no GUI,
mobile app, or browser dashboard; no full task management (due
dates, reminders, priorities, calendars). Parked protection is soft
by design, and skill firing is advisory. Do not present either as
hard enforcement.

## Rules for changes

- One file, one read: a fresh session must still understand a project
  from STATE.md alone.
- Untouched lines stay byte-for-byte identical. Never reformat,
  re-wrap, or reorder lines a command did not change.
- Every write takes a paired snapshot of both files first, and writes
  are atomic (temp file + rename).
- Write commands run under the project lock. Read-only commands do
  not take it.
- Keep output banner-free and plain, one result per line, so agents
  can parse it. `show --json` is the machine-readable path.
- Update README.md and SKILL.md when a command changes, and add a
  CHANGELOG.md entry.
