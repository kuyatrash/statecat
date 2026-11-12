# StateCat — one living project state, readable by any agent in one read

Agents lose context. Sessions get compacted, memory notes drift,
and the next session wastes its first hour reconstructing what was
already decided, re-asking settled questions, and re-proposing
parked work.

StateCat fixes that with one plain, local file. A new agent reads
`STATE.md` first and instantly knows: what is decided, what is
open, what is waiting on the human, what is waiting on others,
and what is parked and must not be nagged about.

Local only. No network calls, no account, no cloud, no sync, no
installer. One Python script (`statecat.py`, Python 3.8+, no
installed packages) plus two Markdown files in your project
folder. Normal CLI output is banner-free so agents can parse it.
MIT licensed, see [LICENSE](LICENSE).

## Try it in 2 minutes

```sh
# Get the single script (or clone this repo and copy statecat.py)
curl -O https://raw.githubusercontent.com/kuyatrash/statecat/main/statecat.py

# Or install it with pip (Python 3.8+)
pip install .
# then just run: statecat init "My Project"

# In your project folder:
python3 statecat.py init "My Project"
python3 statecat.py add Decided "Use local Markdown state files" --because "plain text survives every tool"
python3 statecat.py add Open "Ship the first release"
python3 statecat.py show
```

`init` adds the pointer line below to your project's agent
instructions file (`AGENTS.md`, or `CLAUDE.md` when that is the
file you already have), and adds `.statecat/` to `.gitignore`, so
paired backups are never committed. Use `--no-pointer` or
`--no-gitignore` to skip either step. For a project that already
has a state file, `python3 statecat.py pointer --install` adds the
line (upgrading an older wording in place instead of adding a
second line), `python3 statecat.py pointer --uninstall` removes it,
and `python3 statecat.py pointer` just prints it. Only a line that
is exactly the pointer counts; the pointer quoted in a sentence or
a code block is left alone. Uninstall deletes AGENTS.md only if
StateCat created it and nothing else was added; it exits non-zero
when a file could not be read or written.

```text
Read STATE.md first, then run python3 statecat.py check (py statecat.py check on Windows if python3 is not found), before asking about or working on this project.
```

That pointer, not the skill, is the real read-first mechanism.

For Claude Code, `python3 statecat.py hook --install` also adds a
SessionStart hook to `.claude/settings.json` that runs `show` and
`check` at the start of every session (from the next session, or
after `/clear`). It keeps every existing setting, never adds the
hook twice, upgrades an older StateCat hook in place (and removes
any older one left beside the current one), and refuses to touch a
settings file that is not valid JSON, that changed while it was
being updated, or that is a link leading outside the project. Only
hook commands StateCat itself wrote are treated as StateCat's: your
own command that also runs `statecat.py show` is left alone. The
file keeps its own indentation, line endings and non-ASCII text, so
the change is a small diff. The hook uses the first of `python3`,
`py` and `python` that is Python 3.8 or newer, so it still works on
Windows where `python3` is a Microsoft Store stub. When there is no
`statecat.py` in the project root (StateCat installed with pip), it
runs the installed `statecat` command instead. It runs `show` and
`check` separately and says so in its output when either fails,
instead of failing silently. `hook --uninstall` removes only the
StateCat entry, and `hook` alone prints the entry.

## Before and after

Without StateCat, a fresh session asks: "What did we decide about
hosting? Is the pricing work still open? Should I bring up the
mobile app again?"

With StateCat, the same session reads once and knows:

```text
## Decided
- #1 [2026-10-26] Use local Markdown state files, because plain text survives every tool

## Open
- #2 [2026-10-26] Ship the first release

## Waiting on human
- #3 [2026-10-26] Pick a launch date

## Parked
- #4 [2026-10-26] Mobile app, do not raise without owner approval
```

No settled question re-asked, no parked item proposed. See
[demo/DEMO.md](demo/DEMO.md) for a full worked session, and
`example/` for a complete state file.

## Starting a new project

In your project folder, run:

```sh
python3 statecat.py init "My Project"
```

This creates `STATE.md`, `STATE-ARCHIVE.md`, and
`.statecat/backups/`, installs the read-first pointer line into
`AGENTS.md` (or `CLAUDE.md`), and adds `.statecat/` to
`.gitignore`. It refuses to overwrite existing files.

On Windows, `python3` often is not on PATH; use `py` or `python`
instead, for example `py statecat.py check`. The `./statecat`
launcher (in Git Bash, or any POSIX shell) tries `python3`, then
`py`, then `python` by itself. On Unix, if
`./statecat check` gives permission denied on a fresh clone, run
`chmod +x statecat` once, then try again.

`STATE.md` has a short About header (what the project is, its
purpose, its key paths, and a "Last ID used" line) and five
buckets: Decided, Open, Waiting on human, Waiting on others, and
Parked. `STATE-ARCHIVE.md` holds completed and superseded items
and is not part of the one-read load.

## Everyday commands

```sh
python3 statecat.py show
python3 statecat.py show Open
python3 statecat.py show --json
python3 statecat.py status
python3 statecat.py search "launch date"
python3 statecat.py add Open "Bake the test batch"
python3 statecat.py add Decided "Sell at the Saturday market" --because "foot traffic is highest there"
python3 statecat.py move 2 "Waiting on human"
python3 statecat.py add others "Flour delivery" --resolves-when "the supplier confirms a date" --review 14d
python3 statecat.py recheck 5 --review 7d --because "supplier says next week"
python3 statecat.py update 2 "Bake the test batch on Friday"
python3 statecat.py archive 2
python3 statecat.py compact --older-than 90
python3 statecat.py check
python3 statecat.py restore --list
python3 statecat.py restore
python3 statecat.py diff
```

`show --json` prints the same state as machine-readable JSON
(project, last ID, the last check receipt, and one item list per
bucket) for agents that
would rather parse JSON than Markdown. Each item has `id`, `date`,
`starred`, `text` (its first line), and `continuation` (its
hand-wrapped indented lines, stripped; empty for most items).
Waiting items also have `review` (their review date or null),
`resolves_when` (true when the text says what resolves it) and
`rechecks` (how many times `recheck` has put them off).
`status` prints a compact
summary: bucket counts, waiting items due for review, stale
waiting items, the longest current wait, Last ID used, when
`check` last ran, file size, and the check result. `search` finds items by
case-insensitive text, including wrapped continuation lines, with
`--archive` to include archived items;
when nothing matches it prints "no matches" and exits with code 1.
`compact` archives Decided items older than N days (default 90),
with `--dry-run` to preview; it never touches live buckets. A
hand-added Decided line that has no ID yet is left where it is and
reported; run `check --fix` to give it one.
`diff` shows the unified diff between a snapshot and the current
files, so you can see what changed before restoring.

Every item gets a plain-number ID, for example #12, from one
global counter stored in the "Last ID used" line. An ID never
changes and is never reused. Commands take the ID, not the text,
so editing wording does not break identity.

Shell quoting rule: in bash, an unquoted `#` starts a comment, so
`move #2 Open` would silently drop the ID. Use a bare number
(`move 2 Open`) or quote the ID (`move "#2" Open`). The code
accepts both `2` and `#2`. Item text must be a single line: text
containing a newline or carriage return is rejected with no write
and no backup taken. Empty item text is rejected the same way.

## Optional launcher (Unix)

The repo ships a small executable file named `statecat` that
forwards its arguments to `statecat.py` in the same directory, so
`./statecat check` works when both files are together. It is
optional: `python3 statecat.py <command>` always works, including
on Windows, and is the form used in this guide. The launcher must
be executable in git (mode 100755); if a fresh clone gives
permission denied, run `chmod +x statecat` once.

## Working from a subfolder

Commands other than `init` walk up parent folders to find
`STATE.md`, like git does, and operate on that project root
(including its `.statecat/backups/`). If no `STATE.md` is found
in this folder or any parent folder, the command fails with a
clear error telling you to run `init` in your project root, not
to create a second state file in the subfolder. `init` likewise
refuses if a `STATE.md` already exists in a parent folder.

To replace a decision, use `supersede`, not `update`:

```sh
python3 statecat.py supersede 3 "Use local Markdown state files"
```

The replacement gets a new ID noting "supersedes #3", and the old
item is archived under its original ID. `update` rewrites wording
in place and loses the old wording, so it must not be used to
replace a decision.

## Hand-editing safely

You can edit `STATE.md` directly in any text editor, and that edit
is the truth at the next read. A bare hand-added line with no ID
and no date is fine:

```text
- Ask supplier about pricing
```

It is a warning, not a break, and it never blocks reads or writes.
Repair it with:

```sh
python3 statecat.py check --fix
```

`--fix` assigns the next global ID and stamps an assigned date
marked with an asterisk, for example `[2026-10-26*]`. A starred
date means age unknown, not fresh: check never treats it as fresh
and never uses it to flag or clear staleness. The agent may run
`--fix`; it only adds IDs and dates and never changes wording.

If a hand edit broke the file structure, the CLI fails cleanly:
it writes nothing, names the problem line, and tells you to fix
it or run `python3 statecat.py restore`.

Wrapping an item across lines is safe only if the continuation
lines are indented and immediately follow the item line, with no
blank line in between. Those continuations move, archive, and
supersede with the item, as one block. An indented line anywhere
else is an orphan, and check reports it as a structural problem,
so it cannot silently strand text in the old bucket.

If the "Last ID used" counter falls behind the highest ID in
use, check warns about it, and `check --fix` raises the counter
to that highest ID. If the "Last ID used" line itself is missing,
`check --fix` restores it from the highest ID in use. If `STATE-ARCHIVE.md` is missing, or has no
"## Archived" heading, check reports a structural problem and
write commands refuse, instead of silently recreating a
headerless archive file. Restore a snapshot containing the
archive file, or recreate it from `templates/STATE-ARCHIVE.md`.

The CLI never reformats lines it is not changing, so your hand
edits survive. Files are opened with `newline=""`, so line
endings are preserved byte-for-byte, including on Windows.
State files are written with mode 0644 (world-readable), so
other user or agent accounts on the same machine can read them.

## Un-parking with owner approval

Parked is soft protection, admitted as such: the CLI cannot
verify who is calling it, and a hand edit bypasses it entirely.

Moving an item out of Parked, archiving a parked item, or
updating a parked item requires `--owner-approved`:

```sh
python3 statecat.py move 9 Open --owner-approved
python3 statecat.py archive 9 --owner-approved
python3 statecat.py update 9 "New wording" --owner-approved
```

Without the flag, those commands are refused and nothing changes.
`check --fix` is exempt from this guard, because it never changes
wording.

## Restoring from a backup

Every write takes a paired snapshot of both `STATE.md` and
`STATE-ARCHIVE.md` first, kept in `.statecat/backups/` (last 20
snapshots in total, safety snapshots included). Two-file writes (supersede, archive) write
the archive first, then `STATE.md`, each file atomically, so a
crash never leaves a half-written file.

To undo a bad write, restore both files together:

```sh
python3 statecat.py restore --list
python3 statecat.py restore
```

`restore --list` lists snapshots oldest first, by the timestamp
in each name. `restore` with no argument restores the most recent
pre-write snapshot. A snapshot taken to protect a restore is a safety
snapshot, listed as such, and is never the default target, so
running restore twice restores the same snapshot and does not
flip back. You can still restore a safety snapshot by naming it
explicitly. There is no single-file restore: restoring only
`STATE.md` after a supersede would recreate a duplicate ID.
`restore` validates the snapshot before overwriting anything:
both files must be present and valid UTF-8, otherwise it refuses
with a clean error and changes nothing. A bad snapshot name
prints the available snapshots.

After a restore, the CLI verifies the restored files and warns
when they have structural problems, instead of silently installing
a broken snapshot. Use `diff` first to see what a restore would
change:

If a two-file write was interrupted, `check` reports the
duplicate ID and names the `STATE-ARCHIVE.md` copy as the stray
and the `STATE.md` copy as live. The defined recovery is paired
`restore`, or removing only that stray archive copy by hand. Do
not remove the `STATE.md` copy: after an interrupted supersede
that would delete the live decision while no replacement exists.
If the two copies differ in wording, `check` says the stray
cannot be determined automatically, and paired `restore` is the
recovery.

## Waiting items and check warnings

`check` at session start reports warnings once. That report
counts as the one allowed mention of a stale Waiting on human
item for the session. An agent may mention such an item at most
once per session, and never again once you have acknowledged it.
`check` flags Waiting on human and Waiting on others items with a
real (unstarred) date older than 14 days for the owner only; it
does not chase them. It also warns when `STATE.md` grows past 150
lines or 10 KB, because the archive must not creep back into the
main file. The 14-day threshold and the size budget are tunable
in `.statecat/config` (see Configuration).

### Review dates

A waiting item can say what resolves it and when to look again:

```
- #5 [2026-10-26] Flour delivery, resolves when the supplier confirms a date [review 2026-11-09]
  - 2026-11-09 still waiting: supplier says next week
```

`add` and `move` into a waiting bucket take `--resolves-when TEXT`
and `--review DATE` (a date like 2026-11-15, or `14d` for 14 days
from today; past dates are rejected). `[review YYYY-MM-DD]` must be
the last thing on the item's first line; anywhere else it is just
text. The leading date stays the day the wait started.

When you record something late, `add ... --since 2026-11-01` dates
the item from the day it happened or the wait began, and adds
`(recorded 2026-11-12)` to the line, so a late entry does not look
younger than it is. Future dates are rejected. It works in any
bucket.

On or after its review date, `check` reports the item as `due:`
(a warning, never a structural problem, and never a prompt to
chase it). Then do one of three things: `move` it out (resolved),
raise it with the owner, or put it off with
`recheck ID --review DATE --because TEXT`. `recheck` sets the next
review date and adds a dated "still waiting" note under the item,
without touching its start date; `--because` is required, so every
deferral says why. A due item that has been put off before says
how many times. A waiting item with a review date in the future is
not flagged as stale however old it is; one without a review date
keeps the 14-day rule. `update` keeps a waiting item's review date
unless the new wording has its own, and `move` out of a waiting
bucket drops the review date (the resolves-when text and recheck
notes stay as history). StateCat only reports a due item; it never
acts on one.

### When check last ran

Every `check` (and `check --fix`, after its fix) overwrites
`.statecat/last-check.json` with the time, the result, and a
sha256 of `STATE.md` and `STATE-ARCHIVE.md`. `status` turns that
into one line, for example
`Last check: 2026-11-10 09:14, clean, 3 hours ago, files unchanged since`,
or `STATE.md changed since` when the files no longer match what
was checked. It records that the files passed the structural
check at that time, not that their entries are still true. If the
receipt cannot be written, `check` prints a note and keeps its
normal result. `status` and `show` never write it.

### Items that go missing

The same receipt keeps a list of every item ID a check has seen. It
lives outside the state files on purpose: if a line is deleted by
hand, lost in a merge, or dropped by a `restore`, the files
themselves cannot say it was ever there. `check` reports such an ID
on every run:

```
missing: #12 was in the state files at an earlier check and is now in neither STATE.md nor STATE-ARCHIVE.md; restore it, or run check --accept-missing if it was removed on purpose
```

It is a warning, not an error. `check --accept-missing` stops the
report for a line you removed on purpose. Either way the ID is never
issued again, even after `restore` rewinds `Last ID used`. Archiving,
superseding and moving never trigger it. The list is updated by
`check`, so an item added and lost before any check ran is not on
it, and a fresh clone (`.statecat/` is gitignored) starts with an
empty list. It catches accidents, not someone who also edits the
receipt.

## Configuration

`.statecat/config` is a JSON file that tunes the defaults:

```json
{
  "stale_days": 14,
  "size_lines": 150,
  "size_bytes": 10240,
  "backups_kept": 20,
  "lock_timeout": 10.0,
  "lock_stale": 60.0
}
```

`statecat.py config` prints the effective settings;
`statecat.py config set stale_days 30` changes one key. Unknown
keys are ignored and bad values fall back to their defaults, so a
hand-edited config can never break the CLI. `config set` rejects
out-of-range values with an error naming the valid range
(stale_days 0-1000000, size_lines 1-1000000, size_bytes
1-1000000000, backups_kept 0-1000, lock_timeout 0.1-3600,
lock_stale 1-86400), and rejects non-finite numbers (nan, inf)
and booleans outright: a JSON `true` is never coerced to 1,
and a NaN lock timeout can never make a writer wait forever.
Fractional values for integer keys are rejected the same way
instead of being truncated: a JSON `{"stale_days": 1.9}` warns
and falls back to the default rather than silently becoming 1,
and `config set stale_days 1.9` errors; integral values (1.0,
"5", "5.0") are still accepted.
`lock_stale` must be greater than `lock_timeout`: `config set`
rejects a pair that is not, and a JSON config stating such a
pair warns and falls back to the defaults for both lock
timings. Environment overrides (`STATECAT_LOCK_TIMEOUT`,
`STATECAT_LOCK_STALE`) get the same range bounds as `config
set`, with a warning and a fallback to the config value when
out of range, but a stale-not-greater-than-timeout env pair
only warns, so the overrides stay usable as a test escape
hatch.
A config file that is not valid JSON warns
on stderr and falls back to defaults instead of silently
ignoring the breakage. The config file is
local only: it lives under `.statecat/`, which `init` gitignores.

## One writer at a time, enforced

Two writers at once used to silently lose an item: both read the
same Last ID used, assigned the same next ID, and the last write
won. Writes now run under a cross-platform lock directory
(`.statecat/lock`), held from before the first read until after
the last write. That includes `init` and `config set`, which
used to bypass the lock: two agents init-ing the same empty
directory could interleave and leave mismatched project headers,
and two simultaneous `config set` calls could lose one setting.
A second writer waits up to 10 seconds, then fails cleanly and
changes nothing. The error names the owner PID, the lock age, the
stale limit, when the lock is expected to clear, and how to clear
it by hand (remove `.statecat/lock`, only if no writer is actually
running). The manual command is shell-quoted, so a project path
with spaces pastes safely, and on Windows it is the PowerShell
`Remove-Item -LiteralPath` command instead of `rm -rf`, which
is not a Windows command. The wording follows the ownership
state: a dead owner is not introduced as "another process
writing" (no process is writing; the lock was left behind), and
when the lock is under a second from clearing, the error says it
should clear on the next attempt instead of "in about 0 seconds".
A lock left behind by a crashed process is broken after 60 seconds.
The lock carries a random owner token. Release and stale removal
never check-then-delete in place: they rename the lock directory
aside first (atomic), verify the token or the age on the renamed
copy, and only delete it when the copy is still theirs or still
stale. A copy that belongs to someone else, or is fresh, is put
back if the lock path is free, and never deleted. A stale lock
is only broken when its directory is old *and* its owner is
proved dead: the owner PID recorded in the lock's owner file is
checked, and a live owner is never deleted, however old the
lock looks. Unknown is not dead: a lock whose owner cannot be
determined -- no owner file yet, an unreadable owner file, or
a liveness check that returns indeterminate -- is not broken
in the normal stale window at all, only after ten times the
stale limit, so a writer paused between creating the lock
directory and writing its owner file is not deleted out from
under itself. The owner file is created exclusively, so a
writer whose directory was broken and replaced can never
write its token into the new owner's lock; it waits and
retries instead. While a lock is held, a daemon heartbeat thread
touches its directory mtime every few seconds, so a
long-running but alive writer's lock stays fresh. Restoration
never renames over an existing lock directory: if a lock
already sits at the lock path, the renamed copy is left in
place for later stale cleanup instead. Residual limitation,
stated honestly: the existence check before a restore and
the rename itself are two steps, so a lock directory created
in exactly that window could still be replaced on POSIX; a
PID recorded in the owner file can be reused by an unrelated
process after the owner dies, and that strands the lock --
the liveness check reports the reused PID as alive, so the
lock is treated as live until that process exits, and the
heartbeat does not fix it, because the original owner's
heartbeat died with it. On Windows the liveness check is never
run at all: signal.CTRL_C_EVENT is literally 0 there, so
os.kill(pid, 0) would send Ctrl+C to the process group instead
of probing, so Windows liveness is always unknown and a Windows
lock is only broken after ten times the stale limit. A writer that
cannot record its owner token fails cleanly instead of
proceeding unwitnessed, and deletes nothing: the directory
at the lock path may already be a replacement writer's lock,
and nothing proves it is still ours, so it is left for stale
cleanup. A genuinely own directory left behind strands the
project until it ages past ten times the stale limit.
CI runs the suite on Ubuntu (Python 3.8-3.12) and on Windows
(windows-2022, Python 3.11/3.12). The Windows job's first run
exposed Windows-only test assumptions (the Unix launcher, Unix
file modes, unknown lock-owner liveness, CRLF fixture
checkouts); the suite now skips or expects Windows behaviour
for those, and the Windows job sets `fail-fast: false` so one
version's failure does not cancel the other.
Read-only commands (`show`, `check` without `--fix`, `search`,
`status`, `diff`) do not take the lock.

## What StateCat is not

StateCat v1 is not a task manager, a chat product, a notes app,
or a memory database. It has no due dates, reminders, priorities,
calendars, cloud sync, or graphical interface. If you need those,
they are out of scope for v1 and need a separate, explicit
approval.
