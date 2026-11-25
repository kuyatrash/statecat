# Changelog

## Unreleased

Bug fixes from a read-through and a fuzz of hand-edited state files.
Upgrading needs no action.

- `restore` of the oldest snapshot no longer crashes or loses that
  snapshot. At the `backups_kept` limit (20 by default), the safety
  snapshot taken before the restore pruned the oldest snapshot,
  which was the one being restored, so restore failed with a
  FileNotFoundError traceback and the snapshot was gone. The target
  is now read before the safety snapshot is taken.
- A "Last ID used" line whose value is not a number (for example
  `> Last ID used: none`) is now repaired in place. `add`,
  `supersede` and `check --fix` used to leave it and insert a second
  "Last ID used" line below it, and `check` said "line is missing".
  `check` now names the line and says it is not a number.
- Docs: README, SKILL.md and CONTRIBUTING.md said read-only commands
  do not take the lock; `show`, `status`, `search`, `check` and
  `diff` do take it. README said state files are always
  written with mode 0644; a rewrite keeps the file's own bits and a
  new file follows the umask. README's lock section described the
  old rename-first release and said a reused PID always strands the
  lock; it now describes the check-in-place order and the Linux
  start-time check.

## 1.5.5 — 2026-11-12

Two additions from the Moltbook feedback of Nov 10 and 11 (sections
10 to 16); the plan is in docs/plans/id-ledger-and-since-dates.md.
Upgrading needs no action: the first `check` after upgrading starts
the ID list from what is in the files, and reports nothing.

- `check` now notices an item that vanished. The check receipt
  (`.statecat/last-check.json`) keeps `seen_ids`, every item ID any
  check has seen. An ID on that list that is in neither STATE.md nor
  STATE-ARCHIVE.md is reported as `missing: #N ...` on every check, a
  warning with exit 0, until it comes back or
  `check --accept-missing` drops it. The list is outside the state
  files because a file cannot audit itself: a line deleted by hand
  or lost in a merge leaves no trace in the file it was in. `status`
  prints `Missing item IDs: N`, `show --json` adds
  `last_check.missing_ids`, and `restore` says when it leaves IDs
  missing.
- A lost ID is never issued again. `restore` rewinds `Last ID used`,
  so the next `add` could reuse an ID that had already named another
  item. `add`, `supersede` and `check --fix` now skip every ID on the
  list.
- `add --since YYYY-MM-DD` dates an item from the day it happened or
  the wait began, and adds `(recorded DATE)` to the line when that
  differs from today. A wait recorded late used to look younger than
  it was and go stale late. Future and impossible dates are refused.
- Limits, as built: the list is updated by `check`, not by every
  write, so an item added and lost before any check ran is not on
  it; `.statecat/` is gitignored, so a fresh clone starts with an
  empty list; and it catches accidents, not someone who also edits
  the receipt.

## 1.5.1 — 2026-11-11

Bug-fix release from a read-through and a fuzz of hand-edited state
files. Upgrading needs no action. `restore --list` now prints
snapshots in true time order, so a script that relied on safety
snapshots being listed last will see them in between.

- A hand-added line with no ID no longer crashes anything. A waiting
  line with a date but no ID crashed `check`, `status` and the
  SessionStart hook with a traceback once it was older than
  `stale_days`, and so did a line with a starred date and no ID.
  That also blocked `check --fix`, the command that repairs such a
  line. Both now report the item as `#?`, like the other warnings.
- `compact` no longer crashes on an old Decided line with no ID. The
  dry run failed on its first line; the real run had already moved
  the line into the archive, where nothing can find it by ID, and
  then failed before printing what it archived. Such a line now
  stays in STATE.md and compact says how many it skipped and to run
  `check --fix`.
- `init` no longer crashes on a `.gitignore` that is not valid UTF-8.
  It used to fail with UnicodeDecodeError after STATE.md and
  STATE-ARCHIVE.md were already created. The file is now read and
  appended to as bytes, so its existing bytes are never rewritten,
  and the added line uses the file's own line endings (CRLF stays
  CRLF). `/.statecat/` and `/.statecat` count as already ignored.
- A refused `init` leaves nothing behind. Running `init` in a
  subfolder of an existing project was refused, but only after the
  lock had created an empty `.statecat/` there.
- A project folder that cannot be written (no permission, read-only
  filesystem) now gives a clean error from every command instead of
  a PermissionError traceback, and says that even read-only commands
  need write access to `.statecat/` for the lock.
- A lock owner file that is not valid UTF-8 is read as "no token"
  instead of raising.
- Snapshots are ordered by the timestamp in their name, not by
  folder mtime. Pruning used folder mtimes, which copying or
  unpacking a project resets, so it could delete a recent snapshot
  and keep an old one. `restore --list` used a plain name sort,
  which put every safety snapshot after every pre-write one.
- `restore NAME` and `diff NAME` accept an exact snapshot name even
  when it is also the prefix of another.
- Faster start. Measured on Windows with Python 3.12, from roughly
  85-95 ms per command: about 13 ms less for `show`, `search`,
  `pointer` and `restore --list`, 10 ms for writes and `diff`, 8 ms
  for `status` and 5 ms for `check`. `difflib`, `hashlib`, `shlex`
  and `tempfile` are imported only by the functions that use them,
  and lock tokens come from `os.urandom` instead of `uuid`. What is
  left is almost all Python starting up, importing `argparse` and
  `re`, and compiling the script; StateCat's own work is about
  10 ms of it.
- `search` builds each item's text once instead of twice.

## 1.5.0 — 2026-11-10

Two additions from the Moltbook feedback review: a record of when
`check` last ran, and review dates for waiting items. Upgrading
needs no action: old files parse unchanged, and a waiting item with
no review date keeps the 14-day stale rule. `.statecat/` gains
`last-check.json`, covered by the existing gitignore line.

- `check` (and `check --fix`, after its fix) overwrites
  `.statecat/last-check.json` with the time, result, and a sha256
  of both state files. A clean check used to leave no trace, so a
  file checked an hour ago looked the same as one never checked.
  `status` prints `Last check: <time>, <result>, <age>, files
  unchanged since` (or which file changed since), and
  `show --json` has a `last_check` object. It records a structural
  check, not that the entries are still true. A receipt that cannot
  be written is a note on stderr, never a failure. The receipt
  stays out of STATE.md so `check` does not rewrite the file, and
  churn its history and backups, every session.
- Waiting items can carry `, resolves when ...` and a trailing
  `[review YYYY-MM-DD]`. `add` and `move` take `--resolves-when`
  and `--review` (a date or `Nd`) for waiting buckets only.
  `check` reports an item as `due:` from its review date on, and
  does not flag it as stale before then. It warns on an invalid
  review date, one before the item's own date, or a review date
  on a non-waiting item.
- New `recheck ID --review DATE --because TEXT` records "still
  waiting": it moves the review date and adds a dated note under
  the item, keeping the date the wait started (`update` re-stamps
  it). `--because` is required. A due item that has been rechecked
  says how many times it was deferred.
- `update` keeps a waiting item's review date unless the new
  wording has its own; `move` out of a waiting bucket drops it and
  says so.
- `status` adds `Waiting items due for review` and `Longest wait`.
  `show --json` waiting items add `review`, `resolves_when` and
  `rechecks`.

## 1.4.0 — 2026-11-09

Safety release after v1.3.0: the stale-lock takeover race (SC-17) and
the hook and pointer findings from the same review (SC-08 to SC-20),
plus the new `hook` and `pointer --uninstall` commands. Upgrading:

- Run `hook --install` once if you use the Claude Code hook. It
  upgrades the 1.3.0 hook (and the hand-written one from the setup
  instructions) to the new command, which reports failures and only
  uses Python 3.8 or newer.
- `pointer --uninstall` now deletes AGENTS.md only if StateCat
  recorded creating it, and that record starts with 1.4.0. An
  AGENTS.md made by an older `init` is kept, holding just its header,
  so delete it by hand if you want it gone.
- `hook` and `pointer` install/uninstall take the project lock and
  exit 1 when a file cannot be read or written, so scripts that
  relied on exit 0 there may now see a failure they did not see
  before.
- `.statecat/` gains `lock.break` (the lock-breaking guard) and
  `pointer-created`; both are covered by the existing `.statecat/`
  gitignore line.

- Breaking a stale lock can no longer create two writers (SC-17). A
  waiter used to rename an old-looking lock aside *before* checking
  whether its owner was alive, then put it back; while it was aside
  the lock path was empty, and a third process could take it next to
  the still-running owner. The abandonment check now runs with the
  lock still in place, so a live owner's lock is never moved, and
  the renamed copy is only deleted if it is the very directory that
  was checked. Stale breaking and release are serialised by an OS
  lock on `.statecat/lock.break`, which the kernel drops if its
  holder dies, so it can never go stale itself. Release checks its
  token in place too, and never moves a lock that is no longer its
  own.
- `hook --install` and `hook --uninstall` recognise a StateCat hook
  only by an exact command StateCat has shipped (SC-09). A user's own
  command or wrapper that merely contains `statecat.py show` and
  `statecat.py check` used to be replaced by install or deleted by
  uninstall; it is now left alone.
- The SessionStart hook no longer hides failures (SC-10). It ran
  `show && check; exit 0`, so a failed `show` skipped `check` and
  nothing said so. Both now run, and a failure (including a missing
  `statecat` command or an unreachable project folder) is reported
  in the hook's output, which is what the agent sees. It still exits
  0, because Claude Code only adds a SessionStart hook's output to
  the session on exit 0. `hook --install` upgrades the older hook.
- The hook picks the first of `python3`, `py`, `python` that is
  Python 3.8 or newer (SC-16). It only checked that an interpreter
  ran an empty program, which Python 2 or 3.7 does too.
- `hook --install`/`--uninstall` and `pointer --install`/`--uninstall`
  take the project lock, and refuse to write a file that changed
  while they were updating it, so two runs, or a run and an editor,
  can no longer silently lose one change (SC-08, SC-14).
- Hook, pointer and `.gitignore` writes refuse a symlink that leads
  outside the project, instead of rewriting a file elsewhere (SC-11).
  A link inside the project (`CLAUDE.md` -> `AGENTS.md`) still works.
- `pointer --uninstall` deletes AGENTS.md only when StateCat
  recorded creating it (in `.statecat/pointer-created`); a file you
  made that holds only the same header is kept (SC-12). It exits 1
  when a file could not be read or written, instead of 0 (SC-19);
  `pointer --install` does too.
- Only a whole line that is exactly a pointer wording, outside fenced
  code, counts as the pointer, so the pointer quoted in your docs no
  longer makes install report "already installed" (SC-13).
- `hook --install` with the current hook and an older StateCat hook
  both present removes the older one, and duplicates of the current
  one, instead of leaving both to run (SC-15).
- `hook --install`/`--uninstall` keep `settings.json`'s own
  indentation (spaces, tab, or one line), line endings, final
  newline, BOM and non-ASCII text, instead of rewriting the file
  with two-space indentation and escaped characters (SC-20). Layout
  JSON cannot express, like a short array kept on one line, is
  still normalised.
- Fenced code in STATE.md follows CommonMark (SC-18): `~~~` fences
  are recognised, and a fence closes only on the same character, at
  least as long, with nothing else on the line, so a ```` block can
  show a ``` line without its example headings and items being
  parsed as structure.
- `init` with a blank project name uses the folder name, as when the
  name is omitted, instead of writing an empty `# Project:` header;
  surrounding spaces in a name are trimmed.
- The SessionStart hook runs the installed `statecat` command when
  there is no `statecat.py` in the project root, so it works for a
  pip install too; it used to print nothing there. `hook --install`
  upgrades an existing StateCat hook to this command.
- `hook --install` says "could not read" when `.claude/settings.json`
  cannot be opened (a folder in its place, no permission), instead
  of wrongly calling the file invalid JSON.
- The "no item #N" hint names an interpreter that runs on the
  platform: `py statecat.py show` on Windows, where `python3` is
  often a Microsoft Store stub, and `python3 statecat.py show`
  elsewhere.
- `search` matches words on an item's hand-wrapped continuation
  lines, not just its first line, and prints the whole item.
  Matching uses `casefold()`, so e.g. German `ß` matches `SS`.
- `show --json` items gain a `continuation` list with their wrapped
  lines (stripped), so JSON readers no longer lose that text. `text`
  is unchanged, so existing readers keep working.
- A bucket heading with trailing whitespace (`## Open  `, often left
  by an editor) no longer blocks every write as a "missing bucket
  heading": the parser already read it as the bucket, but the
  section lookup used by writes and by check compared it exactly.
- `check --fix` with nothing to fix no longer takes a snapshot. Each
  no-op run used to add an identical snapshot, pushing real ones
  out of `backups_kept` and making the no-op copy the default
  `restore` target.
- `config set` keeps keys it does not know (from a newer StateCat,
  say) instead of dropping them from `.statecat/config`.
- Output is always UTF-8. On Windows a piped stdout (an agent, the
  SessionStart hook, `| cat`) defaults to the ANSI code page, so
  `show`, `search`, and `diff` crashed with `UnicodeEncodeError` on
  any item containing a character outside it, such as a check mark;
  the hook then silently printed nothing.
- `compact` archives items in their file order; it used to write
  them to STATE-ARCHIVE.md in reverse.
- `diff` reads snapshots BOM-tolerant, like the current files, so a
  BOM alone is no longer reported as a changed first line (and no
  longer crashed the Windows output).
- `pointer --install` keeps a UTF-8 BOM when it upgrades the old
  wording, and `pointer --uninstall` removes a pointer on a
  BOM-prefixed first line and keeps the BOM.
- Bucket spacing fix: `add`, `move`, and `supersede` used to insert
  after the blank line that separates a bucket from the next
  heading, so consecutive items were split by blank lines, and each
  `archive` or `move` left its blank line behind. Repeated cycles
  piled up empty lines. New items now join the end of the bucket's
  item list, and removing an item collapses the doubled blank line
  it leaves. Existing stray blank lines are left as they are.
- A file saved without a final newline no longer gets an inserted
  line glued onto its last line. Archiving into a STATE-ARCHIVE.md
  without a final newline used to merge the item into the
  `## Archived` heading, which broke every later write. Moving the
  last item of such a STATE.md now ends the moved line properly.
- `pointer --install` and `--uninstall` act on the project root when
  run from a subfolder, like `hook` and the other commands, instead
  of creating an AGENTS.md in the subfolder. `hook --uninstall` no
  longer deletes SessionStart groups the user had left empty.
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
