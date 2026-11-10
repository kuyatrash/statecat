# Plan: last-check record and review dates for waiting items

Status: shipped in v1.5.0 (see CHANGELOG). Decisions taken, and where the
build differs from the plan below:
- Receipt stays in `.statecat/`, not in STATE.md.
- `[review YYYY-MM-DD]` kept as the marker syntax.
- `recheck` requires `--because`.
- `waiting_needs_review` was not built (YAGNI; config has no booleans).
- `check` does not print a "changed since" note: it would fire after
  every normal `add` and add noise to every session-start hook. The
  information is in `status` and `show --json`.
- `status` keeps its `Stale waiting items` line and adds
  `Waiting items due for review` and `Longest wait` beside it.
Source: Moltbook feedback, sections 6b, 7a-7c (part 1) and 1, 2, 8c, 9c, 9g
(part 2).

---

## Part 1: record when `check` last ran

### Problem

`check` is read-only. A clean check leaves no trace, so a file checked an
hour ago and a file nobody has checked for a month look the same. Five
separate Moltbook posts made this point: a store that writes only on
change cannot tell "nothing happened" from "nobody looked."

### What "checked" means here

The record says the file passed StateCat's **structural** check at a
given time. It does **not** say the entries are still true. The docs and
the output must keep calling it "last check," never "last verified."
Checking entries against the world (7c) is out of scope.

### Design

**Where it lives:** `.statecat/last-check.json`, overwritten on every
run. It is not stored in STATE.md (see "Decision for Adrian" below).
`.statecat/` is already gitignored.

```json
{
  "time": "2026-11-10T09:14:03-05:00",
  "result": "clean",            // "clean" | "warnings" | "problems"
  "problems": 0,
  "warnings": 2,
  "state_sha256": "…",
  "archive_sha256": "…",
  "last_id": 41,
  "version": "1.5.0"
}
```

The two hashes do the real work. They let a later reader ask whether this
exact content was checked, rather than just when a check last ran. If the
hashes don't match the current files, the files changed after the last
check, either by hand or through a write that `check` never saw.

The file holds one receipt, overwritten each time, because one check
covers the whole file (the 7a retention question). There is no history
log; the snapshots already serve as one.

**Who writes it:**
- `check`: always, on a plain run, including when it finds problems.
- `check --fix`: after its write, so the hashes describe the fixed file.
- `status`, `show` and the write commands never write it. That keeps
  one writer for the record, and `status` stays a pure reader.

The SessionStart hook already runs `check`, so every session start
leaves a receipt. The hook runs outside the agent, which partly answers
7b's "separate the witness from the worked-on." The agent can't skip
writing the receipt, though it could still delete it.

**Failure handling:** if the receipt can't be written (read-only folder,
full disk), `check` prints one line to stderr
(`note: could not record this check: …`) and keeps its normal exit code.
A missing receipt must never turn a clean check into a failure.

**Concurrency:** `check` already runs under the project lock
(`main()`, statecat.py:3516), so the receipt write is serialised with
every other writer. Use the existing `atomic_write_bytes`
(statecat.py:796).

### Where it shows up

`status` gets one new line, read before it does its own scan:

```
Last check: 2026-11-10 09:14, clean, 3 hours ago, files unchanged since
Last check: 2026-11-02 18:40, 2 warnings, 8 days ago, STATE.md changed since
Last check: never recorded
```

`check` gets one line, printed before it overwrites the receipt, and only
when the previous receipt's hashes don't match the current files:

```
note: STATE.md changed since the last check (2026-11-09 22:10)
```

This is information, not a warning. It does not count toward the warning
total and does not change the exit code. Most changes come from normal
StateCat writes, and a warning would fire after every `add`. Add a
warning tier later only if this turns out to be useful.

`show` doesn't change. It prints the file verbatim, and agents parse that
output.

`show --json` gets a top-level `"last_check"` object (the receipt, plus
`"files_changed_since": true|false`), or `null`.

### Not doing

- Putting the receipt in STATE.md.
- A per-item last-verified date (7c). That is a different feature with
  its own open questions.
- An age threshold that warns when a check is old. `status` shows the
  age; that's enough until real use shows otherwise.

### Decision for Adrian

**Should the receipt be visible in the one read?** Putting a
`> Last check: …` header line in STATE.md would make it visible to anyone
who just reads the file (the point of 7b). The cost: `check` would write
STATE.md on every run, which means a git diff every session, a pre-write
snapshot every session (pushing real snapshots out of the 20 kept), and
`check` would stop being read-only. The plan says no. The receipt goes
in `.statecat/`, and the hook's `check` output already reaches the agent
at session start.

### Tests (part 1)

- A clean `check` writes the receipt with the correct hashes and result.
- `check` with warnings, and with problems, still writes the receipt.
  The exit codes don't change.
- `check --fix` writes the receipt after its fix, with hashes matching
  the fixed file.
- An unwritable `.statecat/` prints the note to stderr, and the exit
  code doesn't change.
- `status` shows "never recorded," then "unchanged since," then
  "changed since" after an `add`.
- `check` prints the changed-since note only on a hash mismatch, and the
  note doesn't count as a warning.
- `show --json` includes `last_check`.
- `status` and `show` never create the receipt.
- A corrupt or non-JSON receipt is treated as "never recorded," with no
  crash.

---

## Part 2: review dates and "resolves when" for waiting items

### Problem

A waiting item looks the same whether it is two days old or two months
dead. The current rule (statecat.py:687-705) flags an item once it is
older than `stale_days` (default 14). That rule can't tell "expected to
take a while" from "should have resolved last week," and it gives no way
to record "still waiting, checked today" without `update`. `update`
re-stamps the item's date, which erases how long it has been waiting.
drifts' point (6f) is that the length of the wait is exactly what
matters.

canochief's rule (9g) is the target. A pending item names:
1. what resolves it,
2. when to look again.

When that date passes, the item has to be resolved, escalated, or put
off again with a reason. StateCat only **reports** this. It never acts
on a due date, and silence never triggers anything (cooperemail, 9g).

Owner is already implied by the bucket (human vs. others), so it isn't a
separate field.

### Line format

Plain text, readable by a human and parsed by the CLI, following the
existing `, because …` convention:

```
- #4 [2026-10-26] Wait for the flour supplier delivery, resolves when the supplier confirms a date [review 2026-11-15]
```

- `, resolves when …` is free text. The CLI writes it and `check` only
  looks for whether it is there.
- `[review YYYY-MM-DD]` must be the last thing on the item's first line.
  The parser matches `\[review (\d{4}-\d{2}-\d{2})\]\s*$` against the
  item text. If it's anywhere else, it's just text.
- The leading `[date]` stays the date the wait started. The review flow
  never changes it.

Rechecks add indented, dated continuation lines. StateCat already parses
these as part of the item (`continuation_indices`), so `show`, `search`,
`move` and `archive` carry them along unchanged:

```
- #4 [2026-10-26] Wait for the flour supplier delivery, resolves when the supplier confirms a date [review 2026-11-29]
  - 2026-11-15 still waiting: supplier says next week
```

This is the dated "still waiting" line from 8c. The quiet itself becomes
a record. The deferral count is the number of these lines, so it needs
no extra field.

**Parser change:** `Item` gets read-only attributes `review_date`,
`review_invalid` and `has_resolves_when`, plus a `recheck_count` derived
from the continuation lines. `Item.text` and `render()` don't change, so
untouched lines stay byte-for-byte identical, as the module docstring
promises.

### CLI changes

```
add waiting-others "Wait for the supplier" --resolves-when "they confirm a date" --review 2026-11-15
add waiting-human  "Pick the stall sign"  --review 7d
move 12 waiting-others --review 14d [--resolves-when "…"]
recheck 4 --review 2026-11-29 --because "supplier says next week"
```

- `--review` takes `YYYY-MM-DD` or `Nd`, counted from today. Past dates
  are rejected, and so is any value that isn't a real calendar date.
- `--review` and `--resolves-when` are allowed only when the target
  bucket is a Waiting bucket. Otherwise they are an error.
- **New `recheck` command.** It runs only on Waiting items and requires
  both `--review` and `--because`. It takes a pre-write snapshot,
  replaces the `[review …]` marker, appends the dated continuation line,
  and keeps the item's leading date. Output: `rechecked #4, next review
  2026-11-29 (deferred 2 times)`. It is a writer, so add it to the
  locked command list in `main()`.
- **`update`** keeps an existing `[review …]` marker when the new
  wording doesn't include one. Otherwise rewording a waiting item would
  silently drop its review date.
- **`move` out of a Waiting bucket** removes the `[review …]` marker and
  says so (`moved #4 to Decided; dropped review date 2026-11-29`). The
  `resolves when` text and the recheck lines stay, because they're the
  item's history. `move` already rewrites the item's first line
  (`_move_impl`), so this doesn't break the "never reformat" rule any
  more than `move` does today.

### What `check` reports

| Item | Output |
|---|---|
| Waiting, review date passed | `due: #4 in Waiting on others passed its review date 2026-11-15 (resolve, move, or recheck)` |
| Waiting, review date in the future | nothing, even if older than `stale_days` |
| Waiting, no review date | the current `stale:` rule, unchanged |
| Waiting, 2+ rechecks and due again | `due` line adds `, deferred 2 times` |
| `[review …]` not a real date, or before the item's date | `warning: #4 has an invalid review date …` |
| `[review …]` on a non-Waiting item | `warning: #4 in Open has a review date; only waiting items use one` |

`due:` lines are counted like `stale:` lines: they're warnings, never
problems, and never a reason to block a write. Use the existing wording
style. For Waiting on human, keep the "(owner only, not a prompt to
chase)" note.

A missing `resolves when` or review date is **not** a warning by
default, so existing files stay clean. A new config key,
`waiting_needs_review` (bool, default `false`), turns on
`warning: #4 in Waiting on others has no review date` for teams that
want canochief's stricter rule. This is a warning, not a write-time
refusal, matching the docstring rule: "never a reason to block a write."

`status` changes from `Stale waiting items: N` to
`Waiting: N due for review, M stale`, and adds the longest current wait
(`longest wait: #4, 15 days`). This keeps waiting visible without
counting it as work, which was botsmatter's idle-tag point (section 2).

`show --json` items in Waiting buckets get `review`, `resolves_when`
(bool) and `rechecks` (int).

### Not doing

- Closing an item automatically when a named message arrives
  (cooperemail, 9g). StateCat makes no network calls.
- Any default action when a review date passes.
- Refusing writes that lack a review date.
- Applying review dates to Open or Parked items.

### Decisions for Adrian

1. **Marker syntax.** Is `[review 2026-11-15]` at the end of the line
   right, or does it read too much like the start date? An alternative
   is `(review by 2026-11-15)`, but parentheses are common in normal
   text and would collide more often.
2. **Should `recheck` require `--because`?** The plan says yes. A
   deferral with no reason is the ghost pending state that 9g warns
   about.
3. **Should `waiting_needs_review` default on for new `init` projects
   only?** The plan says off everywhere for 1.5.0. Revisit after use.

### Tests (part 2)

- Parsing: a marker at the end is recognised, the same text mid-line is
  not, an invalid date is flagged, and an untouched line renders
  byte-for-byte the same.
- `add`/`move` with `--review` (absolute and `Nd`), a past date, an
  invalid date, and a non-Waiting target bucket.
- `recheck`: replaces the marker, appends a dated continuation, keeps
  the leading date, counts deferrals, rejects non-Waiting items, and
  rejects a missing `--because` or `--review`.
- `update` keeps the marker. `move` out of Waiting drops the marker and
  keeps the continuations. `archive` carries everything.
- `check`: due, future, no-review fallback to `stale:`, a deferral count
  in the due line, review on a non-Waiting item,
  `waiting_needs_review` on and off.
- `status` and `show --json` fields.
- Windows: newline-exact fixtures for the continuation-line writes, as
  in the existing pass-2 fixtures.

---

## Docs and release

- README: a short "Last check" section and a "Waiting items" section
  covering the format, `recheck`, and what `due` means.
- SKILL.md: tell agents to add `--review` when adding a waiting item,
  use `recheck` instead of `update` for "still waiting," and to read the
  `Last check` line in `status` as an age, not a guarantee.
- Module docstring: add the review-marker rule next to the starred-date
  rule.
- `example/STATE.md`: give #4 a `resolves when` and a review date.
- CHANGELOG 1.5.0. Upgrading needs no action: old files parse
  unchanged, and the `stale:` behaviour stays the same for items with
  no review date.

## Rough size

- Part 1: ~80 lines of code and ~10 tests.
- Part 2: ~250 lines of code (parser, three commands, check/status/json)
  and ~25 tests.
