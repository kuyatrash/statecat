# StateCat skill

StateCat keeps one living project state in one plain, local file, so a
new agent session can start from what is already decided instead of
reconstructing it from scattered notes and old chats.

## The pointer comes first

A project using StateCat has this line in its agent instructions file
(for example AGENTS.md):

> Read STATE.md first, then run python3 statecat.py check (py statecat.py check on Windows if python3 is not found), before asking about or working on this project.

That pointer is the real read-first and session-start check mechanism.
This skill is supporting detail, not the trigger, because skills are
advisory and may not fire. `init` installs the line into AGENTS.md
(or CLAUDE.md) for new projects; `pointer --install` adds it to an
existing project, `pointer --uninstall` removes it, and `pointer`
just prints it. In Claude Code, `hook --install` adds a
SessionStart hook to `.claude/settings.json` that runs `show` and
`check` at every session start.

## Session workflow

1. Read STATE.md first, before asking the human anything about the
   project. That one read answers five questions: what is decided,
   what is open, what is waiting on the human, what is waiting on
   others, and what is parked.
2. Run `python3 statecat.py check` at session start, where the CLI is available,
   and report its warnings once in that session. That report counts
   as the one allowed mention of a stale Waiting on human item for
   the session (see "Waiting is not nagging" below). On Windows,
   `python3` often is not on PATH; use `py` or `python` instead.
   `show --json` is the machine-readable alternative to `show` when
   you would rather parse JSON than Markdown, and `status` is the
   compact one-line-per-fact summary.
3. Summarise open work and human-owned moves only when that helps
   the conversation.
4. The moment a decision changes, update the file before moving on.
   There is no separate end-of-session tidy-up step.

## The five buckets

- Decided: choices already made, each with a stable ID, a date, and
  an optional "because..." rationale. Rationale stops settled
  decisions being re-litigated, so include it when the owner gives
  one and never invent one.
- Open: work in progress or ready to start.
- Waiting on human: the specific next move that only the human can
  make. State it plainly, so the human can act without context
  hunting.
- Waiting on others: items blocked on a third party or external
  event. Name who or what it is waiting on.
- Parked: work deliberately set aside. Do not raise, resume, or
  re-propose it without the human's explicit say-so.

## IDs, dates, and moves

- Every item has a plain-number ID, for example #12, from a single
  global counter across STATE.md and STATE-ARCHIVE.md. The counter
  is stored in the "Last ID used" line in the STATE.md header.
  An ID never changes and is never reused, even after archiving,
  superseding, moving buckets, or deleting the top item by hand.
- Commands acting on an existing item take the ID, not the text.
  Shell quoting rule: in bash, an unquoted `#` starts a comment,
  so use a bare number (`move 12 Open`) or quote the ID
  (`move "#12" Open`); the code accepts both. Item text must be
  a single line; text containing a newline or carriage return is
  rejected with no write and no backup taken. Commands other
  than `init` walk up parent folders to find `STATE.md` and
  operate on that project root; `init` refuses if a `STATE.md`
  already exists in a parent folder. The optional `statecat`
  launcher in the repo root forwards to `statecat.py` in the
  same directory, so `./statecat check` works on Unix and in Git
  Bash (it tries `python3`, then `py`, then `python`); elsewhere on
  Windows, use `py statecat.py <command>`. If
  `statecat.py` lives elsewhere, use that path instead, for
  example `python3 ~/workspace/statecat/statecat.py check`:
  `move 12 Open`, `update 12 <new wording>`,
  `supersede 3 <replacement>`, `archive 12`.
- To replace a Decided item, use `supersede`, never `update`.
  Supersede archives the old item under its original ID and creates
  the replacement under a new ID noting "supersedes #N".
  `update` rewrites wording in place and loses the old wording, so
  it must not be used to replace a decision.
- To move an item, move it between buckets. Never duplicate it.
  An item lives in exactly one bucket at a time.
- A move or an update stamps today's date on the item.
- `search <text>` finds items by case-insensitive text, with
  `--archive` to include archived items. `compact` archives
  Decided items older than N days (default 90) with `--dry-run` to
  preview; it never touches live buckets, and it leaves a Decided
  line with no ID in place (run `check --fix` first). A `missing:`
  line from `check` means an ID seen at an earlier check is in
  neither file now: tell the owner, and only run
  `check --accept-missing` when they say it was removed on purpose.
  Use `add ... --since DATE` when recording something that happened
  or started waiting on an earlier day. `diff` shows the unified
  diff between a snapshot and the current files. `.statecat/config`
  (JSON) tunes stale days, size budget, backup count, and lock
  timing; `config` prints the effective settings, `config set`
  changes one key and rejects out-of-range values with an error
  naming the valid range, and rejects non-finite numbers (nan,
  inf) and booleans outright. Fractional values for integer
  keys are rejected too, not truncated: a JSON
  `{"stale_days": 1.9}` warns and falls back to the default,
  and `config set` errors on it, while integral values
  (1.0, "5", "5.0") are accepted. A config file that is not valid JSON
  warns and falls back to defaults.

## Parked: soft protection, admitted

Parked is soft protection, not a lock. The CLI cannot verify who
is calling it, and a hand edit bypasses it entirely.

- Moving an item out of Parked, archiving a parked item, or
  updating a parked item requires `--owner-approved`.
- `check --fix` is exempt from the Parked guard, because it only
  adds IDs and dates and never changes wording. Running it on a
  parked line succeeds without `--owner-approved`.
- Never un-park, hint at, or re-propose a parked item on your own
  initiative, even when open work would plainly go faster if it
  were revived.

## Waiting is not nagging

- Mention a Waiting on human item at most once per session, and
  never again in that session once the human has acknowledged it.
- The session-start `check` report counts as that one mention.
- Stale-item flags from `check` are information for the owner, not
  a prompt for the agent to chase the item.
- When you add a waiting item, add what resolves it and when to
  look again if you know them:
  `add others "Flour delivery" --resolves-when "the supplier confirms a date" --review 14d`.
  Never invent a review date the owner did not imply.
- A `due:` line from `check` means the review date arrived. The
  item gets resolved (`move`), raised with the owner once, or put
  off with `recheck ID --review DATE --because TEXT`. Use
  `recheck`, not `update`, for "still waiting": it keeps the date
  the wait started. Never take an action because a review date
  passed.
- `status` shows when `check` last ran and whether the files
  changed since. Read it as an age, not a guarantee the entries are
  still true.
- A date marked with an asterisk, for example [2026-10-26*], is an
  assigned date from `check --fix`. Its age is unknown, not fresh:
  never treat it as fresh, and never use it to flag or clear
  staleness.

## Hand edits and check --fix

- The human can edit STATE.md directly in any text editor, and
  that edit is the truth at the next read.
- A hand-added line with no ID and no date, for example
  "- Ask supplier about pricing", is accepted. Both gaps are
  warnings, not structural breaks, and neither blocks reads or
  writes.
- Run `check --fix` to repair such lines: it assigns the next
  global ID and stamps an assigned date (today, with an asterisk).
  The agent may run `--fix`; it only adds IDs and dates.
- If a hand edit broke the file structure, the CLI fails cleanly:
  it writes nothing, names the problem line, and tells the user to
  fix it or run `python3 statecat.py restore`.

## Backups, restore, and interrupted writes

- Every write takes a paired snapshot of both STATE.md and
  STATE-ARCHIVE.md first, kept in `.statecat/backups/` (last 20
  snapshots in total, safety snapshots included).
- Two-file writes (supersede, archive) write the archive first,
  then STATE.md, each file atomically. A crash between the two
  writes leaves a duplicate ID; `check` reports it, naming the
  STATE-ARCHIVE.md copy as the stray and the STATE.md copy as
  live. The defined recovery is paired `restore`, or removing
  only that stray archive copy by hand. Removing the STATE.md
  copy is not a defined recovery.
- `restore` restores both files together, never one alone. Its
  default target is the most recent pre-write snapshot. A snapshot
  taken to protect a restore is a safety snapshot, listed as
  such, and is never the default target, so restoring twice does
  not flip back. After a restore, the CLI verifies the restored
  files and warns when they have structural problems.
- One writer at a time, enforced: writes run under a
  cross-platform lock directory (`.statecat/lock`), including
  `init` and `config set`, which used to bypass it. A second
  writer waits up to 10 seconds, then fails cleanly and changes
  nothing; the error names the owner PID, the lock age, the
  stale limit, when the lock is expected to clear, and how to
  clear it by hand (remove `.statecat/lock`, only if no writer
  is actually running). The manual command is shell-quoted so
  a path with spaces pastes safely, and on Windows it is the
  PowerShell `Remove-Item -LiteralPath` command, not `rm -rf`. A dead owner
  is worded as a lock left behind, not as a process writing,
  and a sub-second countdown says the lock should clear on the
  next attempt. A lock left behind by a crashed process is broken
  after 60 seconds, but only when its owner is proved dead: the owner
  PID in the lock's owner file is checked, a live owner is
  never deleted however old the lock looks, and unknown
  ownership is not treated as dead -- a lock with no owner
  file, or an indeterminate liveness check, is only broken
  after ten times the stale limit. On Windows the liveness
  check is never run: signal.CTRL_C_EVENT is literally 0 there,
  so os.kill(pid, 0) would send Ctrl+C to the process group
  instead of probing, so Windows liveness is always unknown.
  A heartbeat
  keeps a held lock's directory fresh while its writer works,
  and the owner file is created exclusively, so a writer whose
  lock directory was replaced can never write its token into
  the new owner's lock. A PID recorded in the owner file can
  be reused by an unrelated process after the owner dies, and
  that strands the lock until that process exits; the heartbeat
  does not fix it.
  The lock carries a random owner token, and
  release and stale removal rename the lock directory aside
  first, verify the token or the age on the renamed copy, and
  only delete it when the copy is still theirs or still stale;
  a copy that belongs to someone else, or is fresh, is put back
  and never deleted, and a restore never renames over an
  existing lock directory. `lock_stale` must be greater than
  `lock_timeout` in `config set` and in JSON config; env
  overrides get the same range bounds with a warning and a
  fallback, and only warn on a stale-not-greater-than-timeout
  pair. A writer that cannot record its owner
  token fails cleanly instead of proceeding unwitnessed,
  and deletes nothing: the directory at the lock path may
  already be a replacement writer's lock, so it is left for
  stale cleanup. A genuinely own directory left behind
  strands the project until it ages past ten times the
  stale limit.
  Read-only commands do not take the lock.

## Manual rules: no CLI available

Direct editing is a supported fallback, and these rules are what
keep it from colliding with CLI use:

1. Take the next ID from the "Last ID used" line in STATE.md, and
   advance that line by one for every new ID you allocate. Also
   scan both files and never issue a number already in use.
2. Date every new line with today's date, unstarred.
3. To supersede by hand: archive the old line under its original
   ID in STATE-ARCHIVE.md first, then write the replacement in
   Decided under a new ID noting "supersedes #N".
4. To move an item by hand: move the whole line, do not copy it.
5. After a manual edit, run `python3 statecat.py check` when the CLI is
   available. It must report clean, with "Last ID used" equal to
   the highest ID issued.

## Out of scope for v1

No cloud storage, sync, accounts, hosted service, graphical
interface, mobile app, browser dashboard, automatic extraction
from chats or documents, due-date engine, reminders, priorities,
calendars, or full version history. V1 keeps paired pre-write
backups, not a complete audit log.
