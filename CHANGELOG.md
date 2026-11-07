# Changelog

## 1.3.0 — 2026-11-06

Hardening release after v1.2.0. Upgrading: run `check` once.
`check` is stricter than in 1.2.0: a duplicate bucket heading or a
second `> Last ID used:` line is now a structural problem that blocks
writes, and invalid dates and item lines outside any bucket are now
flagged, so a STATE.md that 1.2.0 accepted may need a small fix.

- Break-test round. Each of these used to crash with a raw
  traceback or misbehave, and each now has a regression test:
  - IDs accept ASCII digits only. `move ² Open`, or a
    hand-edited `> Last ID used: #²`, used to crash every
    command, because `str.isdigit()` accepts `²` but `int()`
    does not. Item lines with non-ASCII digits are no longer
    read as IDs.
  - A `STATE.md` that exists but cannot be read (for example
    a folder with that name) fails cleanly instead of with a
    traceback.
  - A file sitting where `.statecat/` or `.statecat/backups/`
    must be fails cleanly instead of with `FileExistsError`.
  - `update` and `supersede` reject empty or blank text, like
    `add` always did.
  - `compact --older-than` rejects negative days; `-5` used to
    archive every Decided item, including today's.
  - Moving an item to the bucket it is already in changes
    nothing; it used to rewrite the file and re-stamp the date.
  - Writes follow a symlinked `STATE.md` or `STATE-ARCHIVE.md`
    to its target; they used to replace the link with a
    regular file, so the linked file silently stopped updating.

- New `hook` command for Claude Code. `hook --install` adds a
  SessionStart hook to `.claude/settings.json` (in the project
  root) that runs `show` and `check` at every session start. It
  keeps every existing setting, never adds the hook twice,
  upgrades an older StateCat hook command in place, and refuses
  with no write when the file is not valid JSON or `hooks` /
  `hooks.SessionStart` has the wrong type. `hook --uninstall`
  removes only the StateCat entry; `hook` alone prints it.
- `python3` is no longer assumed. On Windows it is often a
  Microsoft Store stub that runs nothing. The hook and the
  `./statecat` launcher try `python3`, then `py`, then `python`,
  and the pointer line now names `py statecat.py check` for
  Windows. `pointer --install` upgrades the old pointer wording
  in place instead of adding a second line.
- New `pointer --uninstall` removes the pointer line (current or
  older wording) from AGENTS.md and CLAUDE.md, and deletes an
  AGENTS.md that `init` created if the pointer was all it held.

- `init` and `config set` now run under the project lock, like
  the other writers: two agents init-ing the same empty
  directory can no longer interleave and leave mismatched
  project headers, and two simultaneous `config set` calls can
  no longer lose one of the two settings.
- Integer config keys reject fractional values instead of
  truncating them: a JSON `{"stale_days": 1.9}` used to
  silently become 1; it now warns and falls back to the
  default, and `config set` rejects it. Integral values
  (1.0, "5", "5.0") are still accepted.
- The lock-held error is shell-quoted in its manual clear
  command, so a project path with spaces pastes safely, and
  on Windows it gives the PowerShell `Remove-Item -LiteralPath` command
  instead of `rm -rf`, which is not a Windows command. A
  dead owner is no longer introduced as "another process
  writing": the error says the lock was left behind and no
  process is writing. When the lock is under a second from
  clearing, the error says it should clear on the next
  attempt instead of "in about 0 seconds".
- CI gains a Windows job (`windows-2022`, Python 3.11/3.12)
  running the same suite, because the Ubuntu job cannot
  exercise real Windows filesystem behaviour. It is untested
  until GitHub Actions runs it; the Ubuntu job is unchanged
  (ubuntu-24.04, Python 3.8-3.12).
- The Windows manual clear command uses `-LiteralPath`, not
  `-Path`, so a path with wildcard characters (square
  brackets) is treated literally. A lock already past its
  stale limit now says so instead of claiming the limit is
  less than a second away.
- P7: the first Windows CI run (windows-2022, Python
  3.11/3.12) failed 10 tests and errored on 1, while all five
  Ubuntu jobs passed. All 11 were Windows-only test
  assumptions, not product bugs, and no product code changed:
  the launcher test skips on Windows (the launcher is a Unix
  shell script Windows cannot execute); the two
  world-readable tests skip on Windows (Unix mode bits do not
  exist there); the lock-held error tests now expect the
  unknown-owner branch on Windows, where liveness is always
  unknown (dead/live PIDs report after ten times the stale
  limit, with the PowerShell manual command); the
  missing-heading test no longer assumes LF fixture text (a
  Windows checkout converts it to CRLF); the PowerShell
  manual-command test builds its expectations from the real
  joined path (backslashes on Windows) and mocks the platform
  for both branches. The "could not write the lock ownership
  file" lines in the Windows log are the two deliberate
  error-path tests printing on every platform, not a Windows
  bug. A `.gitattributes` now forces LF checkouts so fixture
  line endings cannot drift by platform, and the Windows job
  sets `fail-fast: false` so a 3.12 failure no longer cancels
  the 3.11 run. Every Windows fix here is reasoned from the
  failure summary plus code inspection; the next Actions run
  is the real verification.

## 1.2.0 — 2026-11-04

Lock correctness round and review fixes, built up in stages P0–P5
after an external review of v1.1.1. The suite grew from 66 tests
to 94.

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
