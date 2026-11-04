# Changelog

## 1.1.0 — 2026-11-04

Fixed:

- Two writers at once could silently lose an item: both read the same
  Last ID used, assigned the same next ID, and the last write won,
  while `check` reported clean. Writes now run under a cross-platform
  lock directory (`.statecat/lock`); a lock left behind by a crashed
  process is broken after 60 seconds.

Added:

- `init` installs the read-first pointer line into AGENTS.md (or
  CLAUDE.md) and adds `.statecat/` to .gitignore. `--no-pointer` and
  `--no-gitignore` skip either step. New `pointer` command prints the
  line, or installs it with `--install`.
- `show --json` prints machine-readable JSON.
- `status` prints a compact summary: bucket counts, stale waiting
  items, Last ID used, file size, and the check result.
- `search` finds items by case-insensitive text, with `--archive` to
  include STATE-ARCHIVE.md.
- `compact` archives Decided items older than N days (default 90),
  with `--dry-run` to preview.
- `.statecat/config` (JSON) tunes stale days, size budget, backup
  count, and lock timing. `config` prints the effective settings;
  `config set <key> <value>` changes one.
- Stale flags now cover Waiting on others as well as Waiting on
  human.
- `diff` shows the unified diff between a snapshot and the current
  files.
- `restore` verifies the restored files and warns when they have
  structural problems.
- `--version` flag; installable with pip (`pip install .`);
  GitHub Actions CI runs the test suite on Python 3.8–3.12.
- The item parser tolerates a missing space after the date bracket;
  archive items now get missing-ID and missing-date warnings.

## 1.0.0 — 2026-10-26

- First release: one living project state in STATE.md, readable by
  any agent in one read; Decided / Open / Waiting on human /
  Waiting on others / Parked buckets; paired backups and restore;
  `check` with `--fix`; supersede; archive; size budget; staleness;
  Parked protection is soft, by design.
