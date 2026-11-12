# Plan: an ID ledger outside the state files, and `add --since`

Status: shipped in v1.5.5 (see CHANGELOG).
Source: Moltbook feedback, Nov 10 and 11 watch runs, sections 10 to 16,
worked from the owner's summary of those sections (the sections
themselves were not available when this was written, so the "not doing"
list below is the place to check for anything the summary flattened).

---

## Part 1: an ID ledger that the state files cannot edit

### Problem

Everything `check` knows, it reads from the two files it is checking.
If a line is deleted by hand, lost in a bad merge, or dropped by a
`restore`, nothing in those files says it was ever there. hermeswgls
put it as "a file cannot audit itself" and proposed a superset ID check:
an ID list kept outside the file and diffed against it (section 11).
Section 14 added that the witness has to live outside the archive too.

There is a second, older hole on the same seam. `restore` puts
`Last ID used` back to the snapshot's value, so the next `add` could
issue an ID that had already named a different item. The rule "an ID
is never reused" did not survive a restore.

### Design

The check receipt, `.statecat/last-check.json`, gains one key:

```json
"seen_ids": [1, 2, 3, 5, 8]
```

It is every item ID any `check` has seen in either file: the previous
list plus what is in the files now. It only grows.

- **Report.** An ID on the list that is in neither `STATE.md` nor
  `STATE-ARCHIVE.md` is reported by `check` as
  `missing: #N was in the state files at an earlier check ...`. It is a
  warning (exit 0), and it repeats on every check until the ID comes
  back or is accepted. A one-time notice would be missed by the first
  session that did not read it.
- **Accept.** `check --accept-missing` drops the missing IDs from the
  list, for a line removed on purpose.
- **Never reissue.** `add`, `supersede` and `check --fix` skip every
  ID on the list when choosing the next one, so a lost ID stays
  retired even after `restore` rewinds the counter.
- **Where it shows.** `status` prints `Missing item IDs: N`.
  `show --json` adds `last_check.missing_ids` and leaves the full list
  out. `restore` prints a note when it leaves IDs missing.

Archiving, superseding and moving never trip it: the ID is still in
one of the two files.

### Limits, stated plainly

- The list is updated by `check`, not by every write. An item added
  and then lost before any check ran is not on it. The SessionStart
  hook and the pointer line both run `check`, so in practice the gap
  is one session.
- It lives in `.statecat/`, which is gitignored. A fresh clone starts
  with an empty list and learns the IDs at its first check. No list
  means nothing is known, never that something is missing.
- It records IDs, not wording. An item reworded by hand is not caught
  (hermesagentj's locator row with a digest, section 12, would do
  that; see "not doing").
- Anyone who can edit `STATE.md` can also edit the receipt. This
  catches accidents, not adversaries.

### Tests

- A hand-deleted line is reported on two checks in a row, counted in
  `status`, listed in `show --json`, and cleared by `--accept-missing`.
- Archive, supersede and move report nothing.
- After `restore`, the next `add` does not reuse the lost ID.
- No receipt, or a receipt with a damaged list, reports nothing and
  does not crash.

---

## Part 2: `add --since`, two dates kept apart

### Problem

yuigui asked which date gets stamped when something is found late:
the day it happened or the day it was learned (section 13). StateCat
had one date per item, always today. A wait recorded a week late
looked a week younger than it was, so it went stale a week late.

### Design

`add BUCKET TEXT --since YYYY-MM-DD` dates the item from that day and
says when it was written down:

```
- #7 [2026-11-01] Wait for the vendor, resolves when they reply (recorded 2026-11-12) [review 2026-11-15]
```

- The leading date stays what it has always meant for a waiting item:
  when the wait began. Staleness and `Longest wait` use it.
- `(recorded DATE)` is plain text, added only when the two dates
  differ. It sits before the review marker, which stays last.
- Future dates and impossible dates are refused before any write.
- Any bucket takes it: a decision made last week can be dated then.

### Not doing

- A second machine-read date field. One parsed date per item keeps
  hand-editing simple; the recorded date is text.
- `--since` on `move` or `update`. Those stamp today on purpose.

---

## Not doing from sections 10 to 16

- **"Probably answered, confirm" as a state** (cooperemail, section
  10) and re-arming on a non-answer (canochief). `recheck` already
  re-arms. A sixth state for half-closed items is a bigger change than
  a point release, and StateCat cannot see replies arrive.
- **Tombstones** (sections 12 to 16). StateCat has none: an archived
  item keeps its full text. luna_nixie's count (64 of 68 tombstones
  were failed verification rows) is a reason to keep it that way.
- **Locator rows with a digest** (hermesagentj, section 12) and
  **provenance, observed against asserted** (section 15). Both add a
  field to every line. Worth a plan of their own.
- **Canary rows** (section 14). The ledger is the cheaper outside
  witness for what StateCat stores.
- **Scrub of history** (fieldnotes_ai, yuigui). That is about the
  repository, not the tool.

## Decisions for Adrian

1. Should every write update the ledger, not only `check`? It would
   close the one-session gap, at the cost of writing the receipt on
   every command.
2. Should `restore` accept the IDs it removes on its own? The plan
   says no: a restore that drops items is exactly what should stay
   visible until someone looks.
