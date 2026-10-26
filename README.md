# StateCat v1 — user guide

StateCat keeps one living project state in one plain, local file,
so a new agent session can start from what is already decided
instead of reconstructing it from scattered notes and old chats.

Mascot: Tsuki, an all-black cat with glowing yellow eyes. Tsuki is
the mascot only; no feature is modelled on cat behaviour. Normal
CLI output is banner-free so agents can parse it; Tsuki appears
only in `--help` and documentation.

StateCat is local only. It makes no network calls, has no account,
no cloud, no sync, and no installer. It is one Python script
(`statecat.py`, Python 3.8+, no installed packages) plus two
Markdown files in your project folder.

## Starting a new project

In your project folder, run:

```sh
python3 statecat.py init "My Project"
```

This creates `STATE.md`, `STATE-ARCHIVE.md`, and
`.statecat/backups/`. It refuses to overwrite existing files.

`STATE.md` has a short About header (what the project is, its
purpose, its key paths, and a "Last ID used" line) and five
buckets: Decided, Open, Waiting on human, Waiting on others, and
Parked. `STATE-ARCHIVE.md` holds completed and superseded items
and is not part of the one-read load.

Add the pointer line to your project's agent instructions file
(see `AGENTS-pointer.md`):

```text
Read STATE.md first, then run statecat check, before asking about or working on this project.
```

## Everyday commands

```sh
python3 statecat.py show
python3 statecat.py show Open
python3 statecat.py add Open "Bake the test batch"
python3 statecat.py add Decided "Sell at the Saturday market" --because "foot traffic is highest there"
python3 statecat.py move #2 "Waiting on human"
python3 statecat.py update #2 "Bake the test batch on Friday"
python3 statecat.py archive #2
python3 statecat.py check
python3 statecat.py restore --list
python3 statecat.py restore
```

Every item gets a plain-number ID, for example #12, from one
global counter stored in the "Last ID used" line. An ID never
changes and is never reused. Commands take the ID, not the text,
so editing wording does not break identity.

To replace a decision, use `supersede`, not `update`:

```sh
python3 statecat.py supersede #3 "Use local Markdown state files"
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
it or run `statecat restore`.

The CLI never reformats lines it is not changing, so your hand
edits survive. Files are opened with `newline=""`, so line
endings are preserved byte-for-byte, including on Windows.

## Un-parking with owner approval

Parked is soft protection, admitted as such: the CLI cannot
verify who is calling it, and a hand edit bypasses it entirely.

Moving an item out of Parked, archiving a parked item, or
updating a parked item requires `--owner-approved`:

```sh
python3 statecat.py move #9 Open --owner-approved
python3 statecat.py archive #9 --owner-approved
python3 statecat.py update #9 "New wording" --owner-approved
```

Without the flag, those commands are refused and nothing changes.
`check --fix` is exempt from this guard, because it never changes
wording.

## Restoring from a backup

Every write takes a paired snapshot of both `STATE.md` and
`STATE-ARCHIVE.md` first, kept in `.statecat/backups/` (last 20
pre-write snapshots). Two-file writes (supersede, archive) write
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
`check` flags Waiting on human items with a real (unstarred) date
older than 14 days for the owner only; it does not chase them.
It also warns when `STATE.md` grows past 150 lines or 10 KB,
because the archive must not creep back into the main file.

## What StateCat is not

StateCat v1 is not a task manager, a chat product, a notes app,
or a memory database. It has no due dates, reminders, priorities,
calendars, cloud sync, or graphical interface. One writer at a
time. If you need those, they are out of scope for v1 and need a
separate, explicit approval.
