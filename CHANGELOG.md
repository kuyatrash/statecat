# Changelog

## 1.2.0 — 2026-11-04

Lock correctness round and review fixes, built up in stages P0–P5
after an external review of v1.1.1. The suite grew from 66 tests
to 90.

Fixed:

- P0: lock ownership token (PID + uuid, release only if the token
  matches); config validation with clear errors and a warning on
  malformed JSON; invalid UTF-8 fails cleanly instead of a
  traceback; `check --fix` restores a deleted Last ID line;
  `restore` validates both snapshot files before overwriting and
  stages the pair; `add` rejects empty text; unknown buckets, IDs
  and snapshot names now list the valid values.
- P1: lock release and stale removal rename the lock directory
  aside first, then re-verify the token or age on the renamed copy,
  so a writer can no longer delete a new owner's lock; an
  owner-write failure fails cleanly instead of proceeding;
  config rejects non-finite numbers (nan, inf) and booleans for
  numeric keys.
- P2: stale locks prove abandonment, not just age -- the owner PID
  is checked for liveness (`os.kill(pid, 0)`) and a daemon
  heartbeat keeps a held lock's directory fresh; env overrides
  get the full config range validation; `lock_stale` must be
  greater than `lock_timeout` in `config set` and JSON config.
- P3: unknown ownership is not dead -- a lock with no owner file,
  or an indeterminate liveness check, is only broken after ten
  times the stale limit; the owner file is created exclusively
  (mode "x"), so a resumed writer can never overwrite a
  replacement owner's token.
- P4: a writer that fails to write its owner file deletes
  nothing, instead of renaming and deleting whatever directory
  sits at the lock path; the other rename/delete sites were
  audited and clear (stale-break and release decide on isolated
  renamed copies).
- P5: the lock-held error now names the owner PID, the lock age,
  the stale limit, when the lock is expected to clear, and how to
  clear it by hand; the PID liveness probe is never run on
  Windows, where `os.kill(pid, 0)` would send Ctrl+C to the
  process group instead of probing, so Windows liveness is
  always unknown.

Behaviour changes:

- `config set` now rejects values it used to accept: out-of-range
  numbers, non-finite numbers (nan, inf, -inf) and booleans for
  numeric keys, and a `lock_stale` that is not greater than
  `lock_timeout` (env overrides only warn on that pair).
- `add` now rejects empty or whitespace-only text.

Known limitations, stated honestly:

- A PID recorded in the owner file can be reused by an unrelated
  process after the owner dies, and that strands the lock until
  that process exits; the heartbeat does not fix it.
- The ten-times stale limit for unknown ownership is a
  heuristic, not a proof: a writer suspended longer than that
  between creating the lock directory and writing its owner
  file could still lose its lock.
- The restore check (lexists) and rename are two steps, so a
  lock directory created in exactly that window could still be
  replaced on POSIX; exclusive owner-file creation arbitrates
  that case, so only one writer can claim the resulting
  directory.
- The heartbeat checks the owner token and then touches the
  lock directory in two steps, so it can refresh a successor's
  directory once; it never deletes anything.
- A failed owner-file write leaves an ownerless directory
  behind, which strands the project until it ages past ten
  times the stale limit (600 seconds at defaults).

## 1.1.1 — 2026-11-04

Fixed:

- `STATE.md` and `STATE-ARCHIVE.md` were written with mode 0600,
  because the temp files from `mkstemp` keep that mode. Another
  user, container, or agent account could not read them, which
  cuts against "readable by any agent." Atomic writes now chmod
  the temp file to 0644 before the rename, for both the text and
  byte writers (so `restore` is covered too). Two tests cover it.

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
