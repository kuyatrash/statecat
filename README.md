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
line, and `python3 statecat.py pointer` just prints it.

```text
Read STATE.md first, then run python3 statecat.py check, before asking about or working on this project.
```

That pointer, not the skill, is the real read-first mechanism.

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
instead, for example `py statecat.py check`. On Unix, if
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
python3 statecat.py update 2 "Bake the test batch on Friday"
python3 statecat.py archive 2
python3 statecat.py compact --older-than 90
python3 statecat.py check
python3 statecat.py restore --list
python3 statecat.py restore
python3 statecat.py diff
```

`show --json` prints the same state as machine-readable JSON
(project, last ID, and one item list per bucket) for agents that
would rather parse JSON than Markdown. `status` prints a compact
summary: bucket counts, stale waiting items, Last ID used, file
size, and the check result. `search` finds items by
case-insensitive text, with `--archive` to include archived items.
`compact` archives Decided items older than N days (default 90),
with `--dry-run` to preview; it never touches live buckets.
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
and no backup taken.

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
to that highest ID. If `STATE-ARCHIVE.md` is missing, or has no
"## Archived" heading, check reports a structural problem and
write commands refuse, instead of silently recreating a
headerless archive file. Restore a snapshot containing the
archive file, or recreate it from `templates/STATE-ARCHIVE.md`.

The CLI never reformats lines it is not changing, so your hand
edits survive. Files are opened with `newline=""`, so line
endings are preserved byte-for-byte, including on Windows.

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

`restore` with no argument restores the most recent pre-write
snapshot. A snapshot taken to protect a restore is a safety
snapshot, listed as such, and is never the default target, so
running restore twice restores the same snapshot and does not
flip back. You can still restore a safety snapshot by naming it
explicitly. There is no single-file restore: restoring only
`STATE.md` after a supersede would recreate a duplicate ID.

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
hand-edited config can never break the CLI. The config file is
local only: it lives under `.statecat/`, which `init` gitignores.

## One writer at a time, enforced

Two writers at once used to silently lose an item: both read the
same Last ID used, assigned the same next ID, and the last write
won. Writes now run under a cross-platform lock directory
(`.statecat/lock`), held from before the first read until after
the last write. A second writer waits up to 10 seconds, then
fails cleanly with "the lock is held" and changes nothing. A lock
left behind by a crashed process is broken after 60 seconds.
Read-only commands (`show`, `check` without `--fix`, `search`,
`status`, `diff`) do not take the lock.

## What StateCat is not

StateCat v1 is not a task manager, a chat product, a notes app,
or a memory database. It has no due dates, reminders, priorities,
calendars, cloud sync, or graphical interface. If you need those,
they are out of scope for v1 and need a separate, explicit
approval.
